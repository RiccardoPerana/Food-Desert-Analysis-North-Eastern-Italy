"""
DIAGNOSTIC SCRIPT -- spot-checks a specific town's supermarket data.

For each town in TOWN_NAMES, this prints:
  1. The town's center point and boundary size (sanity check on location data)
  2. Whether it's flagged as underserved in your final spreadsheet output
     (if pipeline.py has been run)
  3. A TIGHT live query (3km) showing raw node/way details -- useful for
     checking whether nearby supermarkets are being read correctly
  4. What's in the CACHED supermarket dataset within ~10km
  5. A wider LIVE query (10km) for comparison against the cache

Edit TOWN_NAMES below to check different towns.
Run with:  python diagnose_supermarkets.py
"""

import math
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
import overpy

import config
from overpass_utils import query_with_retry

TOWN_NAMES = ["Torri di Quartesolo"]  # edit this list to check other towns
CHECK_RADIUS_KM = 10


def _km_to_deg(km, lat):
    deg_lat = km / 111.0
    deg_lon = km / (111.320 * math.cos(math.radians(lat)))
    return deg_lat, deg_lon


def check_town(name, comuni_gdf, supermarkets_cached, tight_radius_km=3):
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

    # --- Check against final spreadsheet results, if they exist ---
    try:
        df = pd.read_excel(config.SPREADSHEET_PATH)
        match = df[df["Town"] == name]
        if not match.empty:
            r = match.iloc[0]
            print(f"\n[FINAL RESULTS] '{name}' IS flagged as underserved:")
            print(f"   Distance to nearest supermarket: {r['Distance to Nearest Supermarket (km)']} km")
            print(f"   Nearest supermarket: {r['Nearest Supermarket']}")
            print(f"   Population: {r['Population']}")
        else:
            print(f"\n[FINAL RESULTS] '{name}' is NOT in the underserved-towns spreadsheet "
                  f"(either it has its own supermarket, or is within the distance threshold).")
    except FileNotFoundError:
        print(f"\n[FINAL RESULTS] Could not find {config.SPREADSHEET_PATH} -- "
              f"run pipeline.py first if you want this cross-check.")

    # --- Tight-radius live check: raw node/way details ---
    t_deg_lat, t_deg_lon = _km_to_deg(tight_radius_km, center.y)
    t_south, t_north = center.y - t_deg_lat, center.y + t_deg_lat
    t_west, t_east = center.x - t_deg_lon, center.x + t_deg_lon
    print(f"\n[TIGHT LIVE CHECK] Fetching raw OSM elements within {tight_radius_km}km, "
          f"showing node/way type and center-computation status...")
    tight_query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      node["shop"~"supermarket|convenience"]({t_south},{t_west},{t_north},{t_east});
      way["shop"~"supermarket|convenience"]({t_south},{t_west},{t_north},{t_east});
    );
    out center;
    """
    try:
        tight_result = query_with_retry(tight_query)
        for node in tight_result.nodes:
            name_tag = node.tags.get("name", "Unnamed")
            print(f"   [NODE] {name_tag} -- lon={node.lon}, lat={node.lat} (nodes always have coords)")
        for way in tight_result.ways:
            name_tag = way.tags.get("name", "Unnamed")
            has_center = way.center_lon is not None and way.center_lat is not None
            if has_center:
                print(f"   [WAY]  {name_tag} -- center OK ({way.center_lon}, {way.center_lat})")
            else:
                print(f"   [WAY]  {name_tag} -- *** NO CENTER COMPUTED -- "
                      f"WOULD BE SILENTLY DROPPED BY THE OLD OVERPASS-BASED CODE ***")
    except Exception as e:
        print(f"[ERROR] Tight live check failed: {e}")

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
    print(f"\n[LIVE QUERY] Fetching fresh data for the same {CHECK_RADIUS_KM}km area from Overpass...")
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