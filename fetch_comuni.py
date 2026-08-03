"""
Fetches all towns boundaries within the target area, and for each one, the exact point where its name label is rendered on the map.

Admin-center and place-node lookups read directly from the local.osm.pbf file (via `osmium`) 
These previously had a confirmed silent-failure mode.

Only the boundary POLYGON geometry per comune still uses osmnx/Nominatim (a separate, non-Overpass, non-local-file service)
"""

import time
import os
import osmium
import geopandas as gpd
from shapely.geometry import Point
import osmnx as ox

import config


class _AdminRelationCollector(osmium.SimpleHandler):
    """
    Pass 1: collects every admin_level=8 boundary relation's name and, if tagged, the node ID of its admin_centre member.
    We only need the ID here, not its location yet becasue resolving locations requires a (see _NodeLocationResolver below), 
    since relations are stored after nodes in a normal .osm.pbf file, 
    so we can't know which node IDs we'll need until this first pass has finished.
    """
    def __init__(self):
        super().__init__()
        self.records = []
        self.wanted_node_ids = set()

    def relation(self, r):
        if r.tags.get("boundary") != "administrative" or r.tags.get("admin_level") != "8":
            return
        name = r.tags.get("name")
        if not name:
            return

        admin_centre_ref = None
        for m in r.members:
            if m.role == "admin_centre" and m.type == "n":
                admin_centre_ref = m.ref
                self.wanted_node_ids.add(m.ref)
                break

        self.records.append({
            "name": name,
            "osm_id": r.id,
            "province_name": config.TARGET_NAME if config.TARGET_LEVEL == "province" else None,
            "admin_centre_ref": admin_centre_ref,
        })


class _NodeLocationResolver(osmium.SimpleHandler):
    """Pass 2: resolves a specific, known set of node IDs to their lon/lat."""
    def __init__(self, wanted_ids):
        super().__init__()
        self.wanted_ids = wanted_ids
        self.locations = {}

    def node(self, n):
        if n.id in self.wanted_ids:
            self.locations[n.id] = (n.location.lon, n.location.lat)


def fetch_comuni_relations_with_centers():
    """
    Reads all admin_level=8 boundary relations from the local .osm.pbf file 
    and resolves each one's admin_centre point.
    """
    print(f"[INFO] Reading comune boundary relations from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    collector = _AdminRelationCollector()
    collector.apply_file(config.OSM_PBF_PATH)

    node_locations = {}
    if collector.wanted_node_ids:
        resolver = _NodeLocationResolver(collector.wanted_node_ids)
        resolver.apply_file(config.OSM_PBF_PATH)
        node_locations = resolver.locations

    records = []
    for rec in collector.records:
        center_point = None
        ref = rec["admin_centre_ref"]
        if ref is not None and ref in node_locations:
            lon, lat = node_locations[ref]
            center_point = Point(lon, lat)
        records.append({
            "name": rec["name"],
            "osm_id": rec["osm_id"],
            "province_name": rec["province_name"],
            "center_point": center_point,
        })

    found = sum(1 for r in records if r["center_point"] is not None)
    print(f"[INFO] {found}/{len(records)} comuni had a tagged admin_centre "
          f"(resolved locally from file, no network).")
    return records


class _PlaceNodeCollector(osmium.SimpleHandler):
    """Collects every place=city|town|village node, for the label-point fallback."""
    def __init__(self):
        super().__init__()
        self.lookup = {}

    def node(self, n):
        place = n.tags.get("place")
        if place in ("city", "town", "village"):
            name = n.tags.get("name")
            if name:
                self.lookup[name.strip().lower()] = Point(n.location.lon, n.location.lat)


def fetch_all_place_nodes_bulk():
    """
    Reads every place=city|town|village node from the local .osm.pbf file at once. 
    Used as a fallback name-lookup for towns that don't have a properly tagged admin_centre.
    """
    collector = _PlaceNodeCollector()
    collector.apply_file(config.OSM_PBF_PATH)
    print(f"[INFO] Found {len(collector.lookup)} place nodes locally for fallback matching.")
    return collector.lookup


def fetch_comuni_boundary_polygons(names):
    """
    Fetches boundary POLYGON geometry per comune name via osmnx (Nominatim).
    This still runs one request per comune and is the single longest-running step in the pipeline (likely 30-60+ minutes)
    Progress is logged periodically so it's clear the process hasn't hung.
    """
    geoms = {}
    total = len(names)
    for i, name in enumerate(names, start=1):
        try:
            gdf = ox.geocode_to_gdf(f'{name}, Italy', which_result=1)
            geoms[name] = gdf.geometry.iloc[0]
        except Exception as e:
            print(f"[WARN] Could not resolve boundary polygon for {name}: {e}")
        if i % 25 == 0 or i == total:
            print(f"[INFO] Boundary polygon progress: {i}/{total} comuni processed.")
        time.sleep(config.REQUEST_PAUSE_SEC)
    return geoms


def _filter_to_target_regions(gdf):
    """
    The local .osm.pbf file (Nord-Est extract) includes some bordering areas beyond Veneto/Friuli-Venezia Giulia/Trentino-Alto Adige;
    This filters comuni down to just those whose center point actually falls within the target regions' real boundaries.
    """
    if config.TARGET_LEVEL == "multi_region":
        region_names = config.TARGET_REGIONS
    elif config.TARGET_LEVEL == "region":
        region_names = [config.TARGET_NAME]
    else:
        return gdf  # single-province mode doesn't need this filter

    print(f"[INFO] Filtering comuni to target regions {region_names} "
          f"(the local file includes some bordering area beyond these)...")
    region_polygons = []
    for region_name in region_names:
        try:
            region_gdf = ox.geocode_to_gdf(f"{region_name}, Italy")
            region_polygons.append(region_gdf.geometry.iloc[0])
        except Exception as e:
            print(f"[WARN] Could not geocode region '{region_name}' for filtering: {e}")

    if not region_polygons:
        print("[WARN] No region polygons resolved -- skipping filter (risk of extra comuni).")
        return gdf

    combined_region = region_polygons[0]
    for p in region_polygons[1:]:
        combined_region = combined_region.union(p)

    before_count = len(gdf)
    mask = gdf["center_point"].apply(lambda pt: combined_region.contains(pt))
    filtered = gdf[mask].copy()
    removed = before_count - len(filtered)
    if removed:
        print(f"[INFO] Removed {removed} comuni outside the target regions "
              f"(e.g. San Marino, Marche, Emilia-Romagna picked up by the local file).")
    return filtered


def build_comuni_dataset():
    """
    Full assembly: boundaries + label points.
    Columns: name, osm_id, province_name, boundary (polygon), center_point (Point)

    Caches the final result to disk (config.COMUNI_CACHE_PATH).
    Just in case a LATER pipeline step crashes, you do not have to redo this. 
    Set config.FORCE_REFRESH_CACHE = True to ignore the cache.
    """
    if not config.FORCE_REFRESH_CACHE and os.path.exists(config.COMUNI_CACHE_PATH):
        print(f"[INFO] Loading comuni from cache: {config.COMUNI_CACHE_PATH}")
        gdf = gpd.read_file(config.COMUNI_CACHE_PATH)
        if gdf.geometry.name != "boundary":
            print(f"[INFO] Renaming geometry column '{gdf.geometry.name}' -> 'boundary'.")
            gdf = gdf.rename(columns={gdf.geometry.name: "boundary"})
            gdf = gdf.set_geometry("boundary")
        gdf["center_point"] = gdf.apply(
            lambda r: Point(r["center_lon"], r["center_lat"]), axis=1
        )
        gdf = gdf.drop(columns=["center_lon", "center_lat"])
        return _filter_to_target_regions(gdf)

    records = fetch_comuni_relations_with_centers()

    missing = [r for r in records if r["center_point"] is None]
    if missing:
        place_lookup = fetch_all_place_nodes_bulk()
        for r in missing:
            key = r["name"].strip().lower()
            if key in place_lookup:
                r["center_point"] = place_lookup[key]

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

    gdf = gpd.GeoDataFrame(valid_records, geometry=geoms, crs="EPSG:4326")
    gdf = gdf.rename(columns={"geometry": "boundary"})
    gdf = gdf.set_geometry("boundary")
    gdf = _filter_to_target_regions(gdf)

    os.makedirs(os.path.dirname(config.COMUNI_CACHE_PATH), exist_ok=True)
    cache_gdf = gdf.copy()
    cache_gdf["center_lon"] = cache_gdf["center_point"].apply(lambda p: p.x)
    cache_gdf["center_lat"] = cache_gdf["center_point"].apply(lambda p: p.y)
    cache_gdf = cache_gdf.drop(columns=["center_point"])
    cache_gdf.to_file(config.COMUNI_CACHE_PATH, driver="GPKG")
    print(f"[INFO] Cached comuni dataset to {config.COMUNI_CACHE_PATH}")

    return gdf

if __name__ == "__main__":
    comuni = build_comuni_dataset()
    print(comuni[["name", "center_point"]].head(20))