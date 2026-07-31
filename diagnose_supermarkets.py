"""
diagnose_supermarkets.py
--------------------------
ONE-OFF DIAGNOSTIC SCRIPT.

Investigates suspected false positives (towns flagged as "far from any
supermarket" when a supermarket actually exists nearby or even inside
town). Compares:
  1. What's in the CACHED supermarkets dataset near a given town
  2. What a FRESH, live Overpass query finds near that same town

If live data finds supermarkets the cache doesn't have, that confirms
the cached supermarket dataset has real gaps -- most likely from grid
cells that silently failed during the original fetch (we've seen this
happen repeatedly with flaky public Overpass mirrors all session).

Run with:  python diagnose_supermarkets.py
Edit TOWN_NAMES below to check other towns.
"""

import geopandas as gpd
from shapely.geometry import Point
import overpy

import config
from overpass_utils import query_with_retry

TOWN_NAMES = ["San Giovanni Lupatoto", "Nogara"]
CHECK_RADIUS_KM = 10  # how far around the town center to check


def _km_to_deg(km, lat):
    import math
    deg_lat = km / 111.0
    deg_lon = km / (111.320 * math.cos(math.radians(lat)))
    return deg_lat, deg_lon


def check_town(name, comuni_gdf, supermarkets_cached):
    print(f"\n{'='*60}")
    print(f"TOWN: {name}")
    print(f"{'='*60}")

    row = comuni_gdf[comuni_gdf["name"] == name]
    if row.empty:
        print(f"[ERROR] '{name}' not found in comuni cache -- check exact spelling/accents.")
        return

    row = row.iloc[0]
    center = row["center_point"]
    boundary = row["boundary"]
    print(f"Center point: lon={center.x:.5f}, lat={center.y:.5f}")
    print(f"Boundary polygon area (deg^2, rough size indicator): {boundary.area:.6f}")
    print(f"Boundary bounding box: {boundary.bounds}")

    # --- Check 1: what's in the CACHED supermarket dataset nearby? ---
    deg_lat, deg_lon = _km_to_deg(CHECK_RADIUS_KM, center.y)
    check_box = Point(center.x, center.y).buffer(max(deg_lat, deg_lon))
    nearby_cached = supermarkets_cached[supermarkets_cached.geometry.intersects(check_box)]
    print(f"\n[CACHED DATA] Supermarkets within ~{CHECK_RADIUS_KM}km in cached dataset: "
          f"{len(nearby_cached)}")
    for _, sm in nearby_cached.iterrows():
        dist_deg = center.distance(sm.geometry)
        dist_km_approx = dist_deg * 111.0
        print(f"   - {sm['name']} (~{dist_km_approx:.1f}km straight-line)")

    within_boundary = supermarkets_cached[supermarkets_cached.geometry.intersects(boundary)]
    print(f"[CACHED DATA] Supermarkets INSIDE this comune's boundary polygon: {len(within_boundary)}")
    for _, sm in within_boundary.iterrows():
        print(f"   - {sm['name']}")

    # --- Check 2: what does a FRESH, live Overpass query find nearby? ---
    south = center.y - deg_lat
    north = center.y + deg_lat
    west = center.x - deg_lon
    east = center.x + deg_lon
    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      node["shop"~"supermarket|convenience"]({south},{west},{north},{east});
      way["shop"~"supermarket|convenience"]({south},{west},{north},{east});
    );
    out center;
    """
    print(f"\n[LIVE QUERY] Fetching fresh data for the same area from Overpass...")
    try:
        result = query_with_retry(query)
        live_names = []
        for node in result.nodes:
            live_names.append(node.tags.get("name", "Unnamed"))
        for way in result.ways:
            live_names.append(way.tags.get("name", "Unnamed"))
        print(f"[LIVE QUERY] Supermarkets found in fresh query: {len(live_names)}")
        for n in live_names:
            print(f"   - {n}")

        cached_names = set(nearby_cached["name"].tolist())
        live_names_set = set(live_names)
        missing_from_cache = live_names_set - cached_names
        if missing_from_cache:
            print(f"\n[CONFIRMED GAP] These supermarkets exist in a LIVE query but are "
                  f"MISSING from your cached dataset: {missing_from_cache}")
        else:
            print(f"\n[NO GAP FOUND] Live query and cached data agree for this area.")
    except Exception as e:
        print(f"[ERROR] Live query failed: {e}")


def main():
    print("Loading cached comuni and supermarkets data...")
    comuni_gdf = gpd.read_file(config.COMUNI_CACHE_PATH)
    if comuni_gdf.geometry.name != "boundary":
        comuni_gdf = comuni_gdf.rename(columns={comuni_gdf.geometry.name: "boundary"})
        comuni_gdf = comuni_gdf.set_geometry("boundary")
    comuni_gdf["center_point"] = comuni_gdf.apply(
        lambda r: Point(r["center_lon"], r["center_lat"]), axis=1
    )

    supermarkets_gdf = gpd.read_file(config.SUPERMARKETS_CACHE_PATH)

    for name in TOWN_NAMES:
        check_town(name, comuni_gdf, supermarkets_gdf)


if __name__ == "__main__":
    main()