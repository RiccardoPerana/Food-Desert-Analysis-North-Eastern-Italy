"""
pipeline.py
-----------
Main orchestrator. Runs the full food-desert analysis:

  1. Fetch town boundaries and name-label centre points
  2. Attach official ISTAT population figures
  3. Fetch supermarkets/minimarkets from the local OSM extract
  4. For each town:
       a. Exclude it if it already has a supermarket within its own
          boundary (or within a short radius of its centre point -- a
          safety net for towns whose boundary polygon came back
          inaccurate from Nominatim)
       b. Spatial-index nearest-supermarket lookup, then a real routed
          walking distance to that supermarket
       c. Exclude it if the routed distance is within
          config.DISTANCE_THRESHOLD_KM
  5. Export results to spreadsheet + GeoJSON for the web map

There is deliberately no automated cycling-lane/sidewalk check. Instead the
web map renders the cycling-lane layer on top of each selected town's route,
so infrastructure can be assessed by eye against the actual path.

Run with:  python pipeline.py
"""

import json

import pandas as pd
import geopandas as gpd
from tqdm import tqdm

from . import config
from .fetch_towns import build_towns_dataset
from .fetch_supermarkets import fetch_supermarkets
from .population_istat import attach_population
from .routing import (
    project_points_to_metric,
    find_supermarket_candidates,
    get_walking_route,
)
from .export_spreadsheet import export_to_spreadsheet
from .geo_utils import METRIC_CRS
from . import paths


def _safe_value(value, fallback=None):
    """
    Returns `value` unless it is None or NaN, in which case returns `fallback`.

    A plain `value or fallback` is NOT safe here: float('nan') is truthy in
    Python, so an `or` chain would happily keep a stray NaN instead of
    falling through.

    This matters more than it looks. json.dump() writes NaN as a bare `NaN`
    token, which is not valid JSON, and JavaScript's JSON.parse() rejects the
    entire file when it hits one. A single NaN anywhere in towns.geojson
    would make EVERY town vanish from the map -- not just the bad row.
    """
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except (TypeError, ValueError):
        pass  # not NaN-checkable (e.g. it's a string) -- that's fine
    return value


def town_has_own_supermarket(boundary_metric, center_metric, supermarkets_metric):
    """
    True if a supermarket falls inside (or just outside) the town's own
    boundary, OR within a short straight-line radius of its centre point.

    The centre-point check is a safety net. Some towns -- especially larger
    urbanised ones bordering a bigger city -- came back from Nominatim with a
    boundary polygon that was too small, offset, or matched to a different
    entity of the same name. A boundary-only test would then wrongly report
    "no supermarket" for towns that visibly have one. Checking proximity to
    the centre point catches those cases without having to hand-verify a
    thousand polygons.

    All geometry here is in METRIC_CRS, so the buffers below are in real
    metres rather than the previous rough degrees-to-metres approximation.
    """
    zone = boundary_metric.buffer(config.OWN_SUPERMARKET_BUFFER_M).union(
        center_metric.buffer(config.OWN_SUPERMARKET_CENTER_RADIUS_M)
    )

    # Cheap bounding-box prefilter via the R-tree index, then an exact test on
    # the handful of survivors -- rather than comparing all ~20,000 supermarkets
    # against all ~1,000 towns.
    candidate_positions = supermarkets_metric.sindex.query(zone)
    if len(candidate_positions) == 0:
        return False

    candidates = supermarkets_metric.geometry.iloc[candidate_positions]
    return candidates.intersects(zone).any()


def run_pipeline():
    paths.ensure_directories()

    print("[STEP 1] Fetching towns boundaries and label points...")
    towns = build_towns_dataset()

    print("[STEP 2] Attaching ISTAT population data...")
    towns = attach_population(towns)

    print("[STEP 3] Fetching supermarkets (study area + border buffer)...")
    supermarkets = fetch_supermarkets(towns)

    # --- Project everything once, up front ---------------------------------
    # Every distance and buffer operation below happens in metres. Doing the
    # projection here, vectorised, is dramatically cheaper than converting
    # geometry inside the per-town loop.
    print("[STEP 3b] Projecting geometry to a metric CRS and building spatial index...")
    supermarkets_metric = supermarkets.to_crs(METRIC_CRS)
    supermarkets_metric.sindex  # force the R-tree to build now, not lazily mid-loop

    town_boundaries_metric = towns.to_crs(METRIC_CRS).geometry
    town_centers_metric = project_points_to_metric(towns["center_point"])

    results = []
    route_geoms_for_map = []
    unroutable = []          # towns OSRM could not find a path for
    excluded_has_own = 0
    excluded_close_enough = 0

    print("[STEP 4] Evaluating each town against the criteria...")
    for pos in tqdm(range(len(towns))):
        town = towns.iloc[pos]
        boundary_metric = town_boundaries_metric.iloc[pos]
        center_metric = town_centers_metric.iloc[pos]

        # --- Criterion 1: no supermarket/minimarket of its own -------------
        if town_has_own_supermarket(boundary_metric, center_metric, supermarkets_metric):
            excluded_has_own += 1
            continue

        town_point = town["center_point"]

        # --- Criterion 2: shortest ACTUAL walk to any nearby supermarket ---
        # Several candidates are routed rather than just the straight-line
        # nearest. A shop that looks closest on a map may be across a river,
        # lake or motorway with no crossing -- see ROUTING_CANDIDATE_COUNT in
        # config.py for the Papozze case that motivated this.
        candidate_positions = find_supermarket_candidates(
            center_metric, supermarkets_metric
        )

        walk_km, walk_route, nearest_market = None, None, None
        for position in candidate_positions:
            candidate = supermarkets.iloc[position]
            km, route = get_walking_route(town_point, candidate.geometry)
            if km is None:
                continue
            if walk_km is None or km < walk_km:
                walk_km, walk_route, nearest_market = km, route, candidate
            if walk_km <= config.DISTANCE_THRESHOLD_KM:
                # Already inside the threshold -- no closer candidate can
                # change the outcome, so stop routing.
                break

        if walk_km is None:
            # Not one candidate could be reached on foot. Recorded rather than
            # dropped: no walkable route to ANY nearby shop is arguably the most
            # severe finding available, and silently discarding it would remove
            # the worst cases from a report about access.
            #
            # nearest_market is None here (every candidate failed), so the list
            # of shops tried is reported instead of a single name.
            unroutable.append({
                "name": town["name"],
                "province": _safe_value(town.get("province"), "Unknown"),
                "town_lat": town_point.y,
                "town_lon": town_point.x,
                "candidates_tried": [
                    str(supermarkets.iloc[position]["name"])
                    for position in candidate_positions
                ],
            })
            continue

        if walk_km <= config.DISTANCE_THRESHOLD_KM:
            excluded_close_enough += 1
            continue

        # --- Passed both criteria: record it -------------------------------
        raw_population = town.get("population")
        population = int(raw_population) if pd.notna(raw_population) else None

        raw_65 = town.get(config.VULNERABILITY_AGE_FIELD)
        population_65plus = int(raw_65) if pd.notna(raw_65) else None

        pct_65plus = _safe_value(town.get("pct_65plus"))
        aging_index = _safe_value(town.get("aging_index"))

        # How far past the acceptable threshold this town sits, and how many
        # people that actually burdens. See VULNERABILITY SCORING in config.py.
        excess_km = round(walk_km - config.DISTANCE_THRESHOLD_KM, 2)
        vulnerability = (
            round(population_65plus * excess_km) if population_65plus is not None else None
        )

        province = (
            _safe_value(town.get("province"))
            or _safe_value(town.get("province_name"))
            or "Unknown"
        )

        results.append({
            "name": town["name"],
            "province": province,
            "population": population,
            "distance_km": round(walk_km, 2),
            "flagged_for_review": walk_km >= config.DISTANCE_REVIEW_THRESHOLD_KM,
            "population_65plus": population_65plus,
            "pct_65plus": round(float(pct_65plus), 1) if pct_65plus is not None else None,
            "aging_index": round(float(aging_index), 1) if aging_index is not None else None,
            "excess_km": excess_km,
            "vulnerability": vulnerability,
            "nearest_supermarket": nearest_market["name"],
            "town_lat": town_point.y,
            "town_lon": town_point.x,
            "supermarket_lat": nearest_market.geometry.y,
            "supermarket_lon": nearest_market.geometry.x,
        })

        route_geoms_for_map.append({
            "town_name": town["name"],
            "geometry": walk_route,
        })

    # --- Run summary: a full accounting of all N towns --------------------
    print("\n" + "=" * 62)
    print("RUN SUMMARY")
    print("=" * 62)
    print(f"  Towns evaluated                  : {len(towns)}")
    print(f"  Excluded -- has its own shop      : {excluded_has_own}")
    print(f"  Excluded -- within {config.DISTANCE_THRESHOLD_KM}km          : {excluded_close_enough}")
    print(f"  Unroutable (no walking path)      : {len(unroutable)}")
    print(f"  MATCHED as underserved            : {len(results)}")

    # The headline figure. "111 towns" is a count; "111 towns, home to N
    # thousand residents aged 65+" is a finding -- and it is the second one
    # that answers "why should I care about this?"
    # Headline figures are computed from CONFIRMED results only -- rows flagged
    # for review are excluded here.
    #
    # This matters more than it looks. Flagged rows are, by definition, the
    # ones with implausibly large distances, which makes them exactly the rows
    # most likely to top a distance-weighted ranking. Reporting an unverified
    # outlier as the project's headline finding is how a single routing quirk
    # ends up quoted as a result. Flagged rows remain in the spreadsheet, which
    # is the full audit trail; they just do not drive the summary.
    confirmed = [r for r in results if not r.get("flagged_for_review", False)]
    flagged = len(results) - len(confirmed)

    affected_total = sum(r["population"] or 0 for r in confirmed)
    affected_65 = sum(r["population_65plus"] or 0 for r in confirmed)

    if affected_total:
        print("  " + "-" * 58)
        print(f"  Confirmed (unflagged) towns       : {len(confirmed)}")
        print(f"  Residents affected                : {affected_total:,}")
        print(f"    of whom aged 65+                : {affected_65:,} "
              f"({100 * affected_65 / affected_total:.1f}%)")

        ranked = sorted(confirmed, key=lambda r: r.get("vulnerability") or 0, reverse=True)
        print("\n  Most affected towns (confirmed):")
        for r in ranked[:3]:
            print(f"    {r['name'][:28]:30} {r['population_65plus'] or 0:>6,} aged 65+ "
                  f"@ {r['distance_km']:>5.1f}km  (score {r['vulnerability'] or 0:,})")

        if flagged:
            worst_flagged = max(results, key=lambda r: r["distance_km"])
            print(f"\n  {flagged} town(s) flagged for review and EXCLUDED from the "
                  f"figures above.")
            print(f"    Furthest: {worst_flagged['name']} at "
                  f"{worst_flagged['distance_km']}km -- verify this route by hand "
                  f"before quoting it.")
    print("=" * 62 + "\n")

    if unroutable:
        print(f"[WARN] {len(unroutable)} towns had no routable walking path to their "
              f"nearest supermarket. These are NOT in the results and are worth a "
              f"manual look -- see {config.UNROUTABLE_REPORT_PATH}")
        with open(config.UNROUTABLE_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(unroutable, f, ensure_ascii=False, indent=2)

    _write_outputs(results, route_geoms_for_map)
    return results


def _write_outputs(results, route_geoms_for_map):
    export_to_spreadsheet(results, config.SPREADSHEET_PATH)

    # The spreadsheet intentionally keeps EVERY result, flagged or not -- it
    # is the full audit trail. The web map, by contrast, should only show
    # towns whose figures have been accepted, so flagged-for-review towns
    # (and their routes) are excluded here.
    unflagged_results = [r for r in results if not r.get("flagged_for_review", False)]
    unflagged_names = {r["name"] for r in unflagged_results}
    print(f"[INFO] Map will show {len(unflagged_results)}/{len(results)} towns "
          f"({len(results) - len(unflagged_results)} flagged towns excluded from the map).")

    towns_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["town_lon"], r["town_lat"]]},
                "properties": {
                    "name": r["name"],
                    "province": r["province"],
                    "population": r["population"],
                    "population_65plus": r["population_65plus"],
                    "pct_65plus": r["pct_65plus"],
                    "aging_index": r["aging_index"],
                    "vulnerability": r["vulnerability"],
                    "distance_km": r["distance_km"],
                    "flagged_for_review": r["flagged_for_review"],
                    "nearest_supermarket": r["nearest_supermarket"],
                    "supermarket_lat": r["supermarket_lat"],
                    "supermarket_lon": r["supermarket_lon"],
                },
            }
            for r in unflagged_results
        ],
    }
    _write_geojson(towns_geojson, config.GEOJSON_TOWNS_PATH)

    routes_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(x, 6), round(y, 6)] for x, y in rg["geometry"].coords
                    ],
                },
                "properties": {"town_name": rg["town_name"]},
            }
            for rg in route_geoms_for_map
            if rg["town_name"] in unflagged_names
        ],
    }
    _write_geojson(routes_geojson, config.GEOJSON_ROUTES_PATH)

    print(f"[OUTPUT] Spreadsheet   -> {config.SPREADSHEET_PATH}")
    print(f"[OUTPUT] Towns GeoJSON -> {config.GEOJSON_TOWNS_PATH}")
    print(f"[OUTPUT] Routes GeoJSON-> {config.GEOJSON_ROUTES_PATH}")


def _write_geojson(payload, path):
    """
    Writes GeoJSON without pretty-print indentation.

    These files are served to a browser, not read by hand. routes.geojson in
    particular holds full road polylines for every matched town; indenting it
    roughly triples the file size, which directly slows down the hosted web
    map's initial load. Coordinates are also rounded to 6 decimal places
    (~11cm precision -- far finer than the underlying OSM data warrants).
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
