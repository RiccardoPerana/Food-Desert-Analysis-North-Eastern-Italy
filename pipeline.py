"""
Main orchestrator. Runs the full urban-desert analysis:
  1. Fetch towns boundaries + name-label center points
  2. Attach official ISTAT population figures
  3. Fetch supermarkets/minimarkets (target area + cross-border buffer)
  4. For each town:
       a. Exclude if it already has a supermarket within its own boundary
          (OR within center_check_km of its center point: a safety net
          for towns whose boundary polygon turned out to be inaccurate)
       b. Straight-line prefilter -> routed walking distance
       c. Exclude if routed distance <= 3km
  5. Export results to spreadsheet + GeoJSON for the web map

Note: there is no automated cycling-lane/sidewalk check. 
Instead, the web map renders the cycling-lane layer visually on top of each selected town's route, 
so this can be checked by eye directly on the map --

Run with:  python pipeline.py
"""

import os
import json
from tqdm import tqdm
import pandas as pd

import config
from fetch_comuni import build_comuni_dataset
from fetch_supermarkets import fetch_supermarkets
from population_istat import attach_population
from routing import find_nearest_supermarket_straightline, get_walking_route


def _safe_value(value, fallback=None):
    """
    Returns `value` unless it's None or NaN, in which case returns `fallback`.
    Needed because `value or fallback` is NOT safe for this -- float('nan')
    is truthy in Python, so a plain `or` chain would keep a stray NaN instead of falling through to the fallback. 
    This matters because Python's json.dump() writes NaN as a literal (invalid) `NaN` token in JSON output, 
    which JavaScript's JSON.parse() rejects outright -- andsince JSON.parse fails on the whole file,
    a single NaN anywhere in towns.geojson would make EVERY town disappear from the map, not just the one with the bad value.
    """
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except (TypeError, ValueError):
        pass  # value isn't NaN-checkable (e.g. it's a string) -- that's fine
    return value


def town_has_own_supermarket(comune_row, supermarkets_gdf, center_check_km=1.5):
    """
    True if a supermarket falls within (or just outside) the comune's own boundary, OR within a straight-line radius of the town's center point.
    The center-point check is a safety net: some towns (especially large, urbanized ones bordering a bigger city) 
    got a boundary polygon from Nominatim that's too small, offset, or matched to the wrong entity of the same name 
    which made the boundary-only check wrongly report.
    NO supermarket for towns that obviously have one nearby. 
    Checking proximity to the center point directly catches these cases without needing to re-fetch or re-verify every boundary polygon.
    """
    boundary_buffered = comune_row["boundary"].buffer(
        config.OWN_SUPERMARKET_BUFFER_M / 111_000  # meters -> degrees, rough
    )
    if supermarkets_gdf.geometry.intersects(boundary_buffered).any():
        return True

    center = comune_row["center_point"]
    center_buffer_deg = center_check_km / 111.0
    center_zone = center.buffer(center_buffer_deg)
    return supermarkets_gdf.geometry.intersects(center_zone).any()


def run_pipeline():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs("./data", exist_ok=True)

    print("[STEP 1] Fetching comuni boundaries and label points...")
    comuni = build_comuni_dataset()

    print("[STEP 2] Attaching ISTAT population data...")
    comuni = attach_population(comuni)

    print("[STEP 3] Fetching supermarkets (target area + border buffer)...")
    supermarkets = fetch_supermarkets(comuni)

    results = []
    route_geoms_for_map = []

    print("[STEP 4] Evaluating each comune against the criteria...")
    for _, comune in tqdm(comuni.iterrows(), total=len(comuni)):

        # --- Criterion 1: no supermarket/minimarket of its own ---
        if town_has_own_supermarket(comune, supermarkets):
            continue

        town_point = comune["center_point"]

        nearest_market, straight_km = find_nearest_supermarket_straightline(
            town_point, supermarkets
        )

        # --- Criterion 2: routed distance > 3km ---
        walk_km, walk_route = get_walking_route(town_point, nearest_market.geometry)
        if walk_km is None:
            print(f"[SKIP] No routable path found for {comune['name']}, flagging for manual check.")
            continue
        if walk_km <= config.DISTANCE_THRESHOLD_KM:
            continue  # close enough by real-world walking distance

        # --- Passed both evaluated criteria: record it ---
        raw_population = comune.get("population")
        population = int(raw_population) if pd.notna(raw_population) else None

        province = (
            _safe_value(comune.get("province"))
            or _safe_value(comune.get("province_name"))
            or "Unknown"
        )

        results.append({
            "name": comune["name"],
            "province": province,
            "population": population,
            "distance_km": round(walk_km, 2),
            "flagged_for_review": walk_km >= config.DISTANCE_REVIEW_THRESHOLD_KM,
            "nearest_supermarket": nearest_market["name"],
            "town_lat": town_point.y,
            "town_lon": town_point.x,
            "supermarket_lat": nearest_market.geometry.y,
            "supermarket_lon": nearest_market.geometry.x,
        })

        route_geoms_for_map.append({
            "town_name": comune["name"],
            "geometry": walk_route,
        })

    print(f"[DONE] {len(results)} towns matched the evaluated criteria.")

    _write_outputs(results, route_geoms_for_map)
    return results


def _write_outputs(results, route_geoms_for_map):
    from export_spreadsheet import export_to_spreadsheet
    export_to_spreadsheet(results, config.SPREADSHEET_PATH)

    # The spreadsheet (already written above) intentionally keeps EVERY result, flagged or not
    # it's your full audit trail. 
    # The web map, by contrast, should only show towns you've decided to trust,
    # so flagged-for-review towns (and their routes) are excluded here.
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
    with open(config.GEOJSON_TOWNS_PATH, "w", encoding="utf-8") as f:
        json.dump(towns_geojson, f, ensure_ascii=False, indent=2)

    routes_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": list(rg["geometry"].coords),
                },
                "properties": {"town_name": rg["town_name"]},
            }
            for rg in route_geoms_for_map
            if rg["town_name"] in unflagged_names
        ],
    }
    with open(config.GEOJSON_ROUTES_PATH, "w", encoding="utf-8") as f:
        json.dump(routes_geojson, f, ensure_ascii=False, indent=2)

    print(f"[OUTPUT] Spreadsheet -> {config.SPREADSHEET_PATH}")
    print(f"[OUTPUT] Towns GeoJSON -> {config.GEOJSON_TOWNS_PATH}")
    print(f"[OUTPUT] Routes GeoJSON -> {config.GEOJSON_ROUTES_PATH}")


if __name__ == "__main__":
    run_pipeline()