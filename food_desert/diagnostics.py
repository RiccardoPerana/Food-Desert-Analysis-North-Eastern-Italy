"""
diagnostics.py
--------------
DIAGNOSTIC MODULE -- spot-checks a specific town's supermarket data.

For each town supplied this reports:
  1. Its centre point and boundary size (a sanity check on the location data)
  2. Whether it appears in the final spreadsheet, if the analysis has run
  3. What the CACHED supermarket dataset contains nearby
  4. What a LIVE Overpass query finds over the same area, as a second opinion

Pass one or more town names as command-line arguments.
Run with:  python run.py diagnose "Town Name"

Overpass is used HERE and nowhere else in the project. The main pipeline reads
everything from the local extract; this script deliberately queries the live API
so its answer is independent of the cache it is checking. Disagreement between
the two is the signal worth having.
"""

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from . import config
from .overpass_utils import query_with_retry
from .geo_utils import km_to_degrees

# Town names are now passed on the command line rather than edited into this
# file:   python run.py diagnose "Torri di Quartesolo" "Arsie"
CHECK_RADIUS_KM = 10        # radius for the cached-vs-live comparison
TIGHT_RADIUS_KM = 3         # radius for the close-in raw element listing


def _bbox_around(center, radius_km):
    """Returns (south, west, north, east) for an Overpass bounding box."""
    deg_lat, deg_lon = km_to_degrees(radius_km, center.y)
    return (
        center.y - deg_lat,
        center.x - deg_lon,
        center.y + deg_lat,
        center.x + deg_lon,
    )


def _live_supermarket_query(center, radius_km):
    """
    Runs one live Overpass query for supermarkets within radius_km of a point.

    Returns a list of (kind, name, has_coords) tuples, where kind is "NODE" or
    "WAY". Ways without a computed centre are reported rather than dropped --
    silently discarding them was the exact failure mode that motivated moving
    the main pipeline off Overpass and onto the local extract.
    """
    south, west, north, east = _bbox_around(center, radius_km)
    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      node["shop"~"supermarket|convenience"]({south},{west},{north},{east});
      way["shop"~"supermarket|convenience"]({south},{west},{north},{east});
    );
    out center;
    """

    result = query_with_retry(query)
    found = []
    for node in result.nodes:
        found.append(("NODE", node.tags.get("name", "Unnamed"), True))
    for way in result.ways:
        has_coords = way.center_lon is not None and way.center_lat is not None
        found.append(("WAY", way.tags.get("name", "Unnamed"), has_coords))
    return found


def check_town(name, towns_gdf, supermarkets_cached):
    print(f"\n{'=' * 62}")
    print(f"TOWN: {name}")
    print(f"{'=' * 62}")

    row = towns_gdf[towns_gdf["name"] == name]
    if row.empty:
        print(f"[ERROR] '{name}' not found in the towns cache -- "
              f"check exact spelling and accents.")
        return

    row = row.iloc[0]
    center = row["center_point"]
    boundary = row["boundary"]
    print(f"Centre point : lon={center.x:.5f}, lat={center.y:.5f}")
    print(f"Boundary area: {boundary.area:.6f} deg^2 (rough size indicator)")
    print(f"Bounding box : {boundary.bounds}")

    # --- Cross-check against the final spreadsheet, if it exists ------------
    try:
        df = pd.read_excel(config.SPREADSHEET_PATH)
        match = df[df["Town"] == name]
        if not match.empty:
            r = match.iloc[0]
            print(f"\n[FINAL RESULTS] '{name}' IS flagged as underserved:")
            print(f"   Distance to nearest supermarket: "
                  f"{r['Distance to Nearest Supermarket (km)']} km")
            print(f"   Nearest supermarket            : {r['Nearest Supermarket']}")
            print(f"   Population                     : {r['Population']}")
        else:
            print(f"\n[FINAL RESULTS] '{name}' is NOT in the spreadsheet -- it either "
                  f"has its own supermarket, or sits within the distance threshold.")
    except FileNotFoundError:
        print(f"\n[FINAL RESULTS] {config.SPREADSHEET_PATH} not found -- "
              f"run pipeline.py first if you want this cross-check.")

    # --- Close-in raw element listing --------------------------------------
    print(f"\n[TIGHT LIVE CHECK] Raw OSM elements within {TIGHT_RADIUS_KM}km...")
    try:
        for kind, shop_name, has_coords in _live_supermarket_query(center, TIGHT_RADIUS_KM):
            status = "coords OK" if has_coords else "*** NO CENTRE COMPUTED ***"
            print(f"   [{kind:4}] {shop_name} -- {status}")
    except Exception as e:
        print(f"[ERROR] Tight live check failed: {e}")

    # --- What the cached dataset holds nearby ------------------------------
    deg_lat, deg_lon = km_to_degrees(CHECK_RADIUS_KM, center.y)
    check_zone = Point(center.x, center.y).buffer(max(deg_lat, deg_lon))
    nearby_cached = supermarkets_cached[supermarkets_cached.geometry.intersects(check_zone)]

    print(f"\n[CACHED DATA] Supermarkets within ~{CHECK_RADIUS_KM}km: {len(nearby_cached)}")
    for _, sm in nearby_cached.iterrows():
        dist_km_approx = center.distance(sm.geometry) * 111.0
        print(f"   - {sm['name']} (~{dist_km_approx:.1f}km straight-line, approx)")

    within_boundary = supermarkets_cached[supermarkets_cached.geometry.intersects(boundary)]
    print(f"[CACHED DATA] Supermarkets INSIDE this town's boundary: {len(within_boundary)}")
    for _, sm in within_boundary.iterrows():
        print(f"   - {sm['name']}")

    # --- Same area, live, for comparison -----------------------------------
    print(f"\n[LIVE QUERY] Fetching fresh data for the same {CHECK_RADIUS_KM}km area...")
    try:
        live = _live_supermarket_query(center, CHECK_RADIUS_KM)
        live_names = {shop_name for _, shop_name, _ in live}
        print(f"[LIVE QUERY] Supermarkets found: {len(live)}")
        for _, shop_name, _ in live:
            print(f"   - {shop_name}")

        cached_names = set(nearby_cached["name"].tolist())
        missing_from_cache = live_names - cached_names
        if missing_from_cache:
            print(f"\n[GAP] Present in the LIVE query but MISSING from the cached "
                  f"dataset: {missing_from_cache}")
            print("      Note: the cache is clipped to the study area plus "
                  "BORDER_BUFFER_KM, so shops well outside it are expected to be absent.")
        else:
            print("\n[NO GAP] Live query and cached data agree for this area.")
    except Exception as e:
        print(f"[ERROR] Live query failed: {e}")


def run_diagnostics(town_names):
    print("Loading cached towns and supermarkets data...")
    towns_gdf = gpd.read_file(config.TOWNS_CACHE_PATH)
    if towns_gdf.geometry.name != "boundary":
        towns_gdf = towns_gdf.rename(columns={towns_gdf.geometry.name: "boundary"})
        towns_gdf = towns_gdf.set_geometry("boundary")
    towns_gdf["center_point"] = towns_gdf.apply(
        lambda r: Point(r["center_lon"], r["center_lat"]), axis=1
    )

    supermarkets_gdf = gpd.read_file(config.SUPERMARKETS_CACHE_PATH)

    for name in town_names:
        check_town(name, towns_gdf, supermarkets_gdf)
