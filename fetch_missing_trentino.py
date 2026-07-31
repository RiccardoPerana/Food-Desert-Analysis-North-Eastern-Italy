"""
fetch_missing_trentino.py
--------------------------
ONE-OFF RECOVERY SCRIPT.

The original multi-region run silently fetched ZERO comuni from
Trentino-Alto Adige, because OSM tags that region's name as
"Trentino – Alto Adige/Südtirol" (en-dash + German name appended) rather
than the plain "Trentino-Alto Adige" the query used -- so an exact-string
match found nothing for that region specifically, while Veneto and
Friuli-Venezia Giulia matched fine and were fetched correctly.

This script fetches ONLY the missing Trentino-Alto Adige comuni, using
the region's stable OSM relation ID (45757) directly -- bypassing name
matching entirely, so there's no ambiguity -- and merges the result into
your EXISTING comuni cache. This avoids re-fetching the ~774 comuni that
already completed successfully in the first run.

Run this once:  python fetch_missing_trentino.py
Then continue normally:  python pipeline.py
(pipeline.py will now load the complete, merged cache automatically)
"""

import os
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

import config
from overpass_utils import query_with_retry
from fetch_comuni import fetch_comuni_boundary_polygons

TRENTINO_ALTO_ADIGE_RELATION_ID = 45757  # verified via OpenStreetMap wiki/relation page
TEMP_TRENTINO_ONLY_PATH = "./data/trentino_fetched_only.gpkg"


def fetch_trentino_comuni_relations():
    """
    Same approach as fetch_comuni_relations_with_centers() in fetch_comuni.py,
    but scoped directly to the known relation ID instead of a name match --
    completely sidesteps the naming-mismatch bug.
    """
    query_relations = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    rel({TRENTINO_ALTO_ADIGE_RELATION_ID});
    map_to_area->.searchArea;
    relation["admin_level"="8"]["boundary"="administrative"](area.searchArea);
    out body;
    """
    result = query_with_retry(query_relations)

    records = []
    rel_to_admin_centre_ref = {}
    admin_centre_refs = set()

    for rel in result.relations:
        name = rel.tags.get("name")
        if not name:
            continue
        admin_centre_ref = None
        for member in rel.members:
            if member.role == "admin_centre":
                admin_centre_ref = member.ref
                admin_centre_refs.add(member.ref)
                break
        records.append({
            "name": name,
            "osm_id": rel.id,
            "province_name": None,
            "center_point": None,
        })
        rel_to_admin_centre_ref[rel.id] = admin_centre_ref

    if admin_centre_refs:
        ids_str = ",".join(str(i) for i in admin_centre_refs)
        query_nodes = f"""
        [out:json][timeout:{config.OVERPASS_TIMEOUT}];
        node(id:{ids_str});
        out body;
        """
        node_result = query_with_retry(query_nodes)
        node_lookup = {n.id: (float(n.lon), float(n.lat)) for n in node_result.nodes}
        for r in records:
            ref = rel_to_admin_centre_ref.get(r["osm_id"])
            if ref is not None and ref in node_lookup:
                lon, lat = node_lookup[ref]
                r["center_point"] = Point(lon, lat)

    found = sum(1 for r in records if r["center_point"] is not None)
    print(f"[INFO] Trentino-Alto Adige: {found}/{len(records)} comuni had a tagged admin_centre.")
    return records


def fetch_trentino_place_nodes_bulk():
    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    rel({TRENTINO_ALTO_ADIGE_RELATION_ID});
    map_to_area->.searchArea;
    node["place"~"city|town|village"](area.searchArea);
    out body;
    """
    result = query_with_retry(query)
    lookup = {}
    for node in result.nodes:
        name = node.tags.get("name")
        if name:
            lookup[name.strip().lower()] = Point(float(node.lon), float(node.lat))
    print(f"[INFO] Bulk-fetched {len(lookup)} place nodes for fallback matching.")
    return lookup


def main():
    if os.path.exists(TEMP_TRENTINO_ONLY_PATH):
        print(f"[INFO] Found previously-fetched Trentino data at "
              f"{TEMP_TRENTINO_ONLY_PATH} -- skipping re-fetch entirely.")
        new_gdf = gpd.read_file(TEMP_TRENTINO_ONLY_PATH)
    else:
        print("[STEP 1] Fetching Trentino-Alto Adige comuni relations + admin_centre points...")
        records = fetch_trentino_comuni_relations()

        missing = [r for r in records if r["center_point"] is None]
        if missing:
            place_lookup = fetch_trentino_place_nodes_bulk()
            for r in missing:
                key = r["name"].strip().lower()
                if key in place_lookup:
                    r["center_point"] = place_lookup[key]

        print(f"[STEP 2] Fetching boundary polygons for {len(records)} comuni "
              f"(one Nominatim call per comune, expect ~10-15 minutes)...")
        names = [r["name"] for r in records]
        polygons = fetch_comuni_boundary_polygons(names)

        valid_records = []
        geoms = []
        for r in records:
            poly = polygons.get(r["name"])
            if poly is None:
                print(f"[WARN] Skipping {r['name']} -- no boundary polygon resolved.")
                continue
            if r["center_point"] is None:
                print(f"[FALLBACK] Using polygon centroid for {r['name']} -- verify manually.")
                r["center_point"] = poly.centroid
            valid_records.append(r)
            geoms.append(poly)

        new_gdf = gpd.GeoDataFrame(valid_records, geometry=geoms, crs="EPSG:4326")
        new_gdf = new_gdf.rename(columns={"geometry": "boundary"})
        new_gdf = new_gdf.set_geometry("boundary")
        print(f"[INFO] Successfully fetched {len(new_gdf)} Trentino-Alto Adige comuni.")

        # Flatten center_point to plain lon/lat floats IMMEDIATELY.
        new_gdf["center_lon"] = new_gdf["center_point"].apply(lambda p: p.x)
        new_gdf["center_lat"] = new_gdf["center_point"].apply(lambda p: p.y)
        new_gdf = new_gdf.drop(columns=["center_point"])

        # Save this immediately, BEFORE attempting the merge below -- if the
        # merge step fails again for any reason, this ~15 minutes of fetching
        # work is preserved and won't need to be redone a third time.
        os.makedirs(os.path.dirname(TEMP_TRENTINO_ONLY_PATH), exist_ok=True)
        new_gdf.to_file(TEMP_TRENTINO_ONLY_PATH, driver="GPKG")
        print(f"[INFO] Saved freshly-fetched Trentino data to {TEMP_TRENTINO_ONLY_PATH} "
              f"as a safety checkpoint.")

    print("[STEP 3] Merging with existing comuni cache...")
    if os.path.exists(config.COMUNI_CACHE_PATH):
        # Already stored as boundary(geometry) + center_lon/center_lat floats
        # -- no Point reconstruction needed here at all.
        existing = gpd.read_file(config.COMUNI_CACHE_PATH)
        print(f"[INFO] Existing cache had {len(existing)} comuni.")
    else:
        existing = gpd.GeoDataFrame(
            columns=list(new_gdf.columns), geometry="boundary", crs="EPSG:4326"
        )
        print("[INFO] No existing cache found -- creating a new one.")

    combined = pd.concat([existing, new_gdf], ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="boundary", crs="EPSG:4326")

    # Defensive safety net: directly inspect every column's actual dtype and
    # forcibly drop anything geometry-typed other than "boundary" itself.
    # We've hit this "multiple geometry columns" error twice now despite
    # believing center_point was fully flattened beforehand -- rather than
    # keep guessing at geopandas' exact auto-detection behavior, this
    # guarantees a clean single-geometry frame right before saving,
    # regardless of how a stray geometry column snuck back in.
    stray_geom_cols = [
        c for c in combined.columns
        if c != "boundary" and str(combined[c].dtype) == "geometry"
    ]
    if stray_geom_cols:
        print(f"[WARN] Found unexpected extra geometry-typed column(s), "
              f"dropping before save: {stray_geom_cols}")
        combined = combined.drop(columns=stray_geom_cols)

    os.makedirs(os.path.dirname(config.COMUNI_CACHE_PATH), exist_ok=True)
    combined.to_file(config.COMUNI_CACHE_PATH, driver="GPKG")

    print(f"[DONE] Combined cache now has {len(combined)} comuni total "
          f"-> {config.COMUNI_CACHE_PATH}")


if __name__ == "__main__":
    main()