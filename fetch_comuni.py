"""
fetch_comuni.py
---------------
Fetches all "comune" (municipality) boundaries within the target area,
and for each one, the exact point where its name label is rendered on the map.

This fetches almost everything in bulk, in 2 Overpass queries total for
the whole target area (regardless of how many comuni it contains):
  1. One query gets ALL admin_level=8 boundary relations AND all their
     member nodes (including admin_centre) in a single request.
  2. One fallback query gets ALL place=city|town|village nodes in the
     whole area at once, for comuni whose admin_centre wasn't tagged --
     matched locally by name instead of queried individually.

Only the boundary POLYGON geometry still requires one call per comune
(via osmnx/Nominatim, a separate, non-Overpass service).
"""

import time
import os
import overpy
import geopandas as gpd
from shapely.geometry import Point
import osmnx as ox

import config
from overpass_utils import query_with_retry


def _build_area_query_fragment():
    """Builds the Overpass 'area' selector for the configured target area.

    Uses regex ('~') matching instead of exact ('=') string matching for
    region/province names. This matters because OSM sometimes tags region
    names with quirks that break exact matching -- e.g. Trentino-Alto
    Adige is actually tagged "Trentino – Alto Adige/Südtirol" (en-dash,
    plus a German name appended), which silently matched ZERO relations
    under exact matching, with no error at all. A substring match still
    reliably finds the right admin_level=4 area without needing to know
    every possible naming variant in advance.
    """
    if config.TARGET_LEVEL == "province":
        return f'area["name"~"{config.TARGET_NAME}"]["admin_level"="6"]->.searchArea;'
    elif config.TARGET_LEVEL == "region":
        return f'area["name"~"{config.TARGET_NAME}"]["admin_level"="4"]->.searchArea;'
    elif config.TARGET_LEVEL == "country":
        return f'area["ISO3166-1"="{config.COUNTRY_ISO}"]["admin_level"="2"]->.searchArea;'
    elif config.TARGET_LEVEL == "multi_region":
        # Combine multiple named regions into one search area via Overpass's
        # union operator. Each region gets its own temporary area variable
        # (.r0, .r1, ...), then they're unioned together into .searchArea.
        lines = []
        var_names = []
        for i, region_name in enumerate(config.TARGET_REGIONS):
            var = f".r{i}"
            lines.append(f'area["name"~"{region_name}"]["admin_level"="4"]->{var};')
            var_names.append(var)
        union_expr = "; ".join(var_names) + ";"
        lines.append(f'({union_expr})->.searchArea;')
        return "\n    ".join(lines)
    else:
        raise ValueError(f"Unknown TARGET_LEVEL: {config.TARGET_LEVEL}")


def fetch_comuni_relations_with_centers():
    """
    TWO lightweight Overpass queries (instead of one heavy one):

      Query A: relation tags + member list only, NO geometry recursion.
               This used to also recurse (">") into every node making up
               every boundary polygon -- for ~100 comuni with detailed
               borders, that produced a multi-megabyte response that kept
               failing mid-transfer (IncompleteRead / RemoteDisconnected)
               on busy public mirrors. We don't need that geometry here,
               only the admin_centre reference, so we skip it entirely.

      Query B: a small, targeted lookup for ONLY the specific admin_centre
               node IDs found in Query A (typically <=~100 nodes).
    """
    area_fragment = _build_area_query_fragment()

    # --- Query A: relations only, no geometry recursion ---
    query_relations = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    {area_fragment}
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
            "province_name": config.TARGET_NAME if config.TARGET_LEVEL == "province" else None,
            "center_point": None,  # resolved below (Query B) or via bulk fallback
        })
        rel_to_admin_centre_ref[rel.id] = admin_centre_ref

    # --- Query B: targeted lookup of only the admin_centre node IDs ---
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
    print(f"[INFO] {found}/{len(records)} comuni had a tagged admin_centre "
          f"(resolved via 2 lightweight queries, no bulk geometry download).")
    return records


def fetch_all_place_nodes_bulk():
    """
    ONE Overpass query: fetches every place=city|town|village node in the
    whole target area at once. Used as a fallback name-lookup for comuni
    that don't have a properly tagged admin_centre.
    """
    area_fragment = _build_area_query_fragment()

    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    {area_fragment}
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


def fetch_comuni_boundary_polygons(names):
    """
    Fetches boundary POLYGON geometry per comune name via osmnx (Nominatim).
    This still runs one request per comune -- Nominatim is a separate,
    lighter-weight service from Overpass and is not the bottleneck we've
    been hitting. At 1000+ comuni this is the single longest-running step
    in the whole pipeline (likely 30-60+ minutes) -- progress is logged
    periodically so it's clear the process hasn't hung.
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


def build_comuni_dataset():
    """
    Full assembly: boundaries + label points, using the minimum number of
    Overpass queries possible (2 total, regardless of area size).
    Columns: name, osm_id, province_name, boundary (polygon), center_point (Point)

    Caches the final result to disk (config.COMUNI_CACHE_PATH). At
    multi-region scale this step involves 1000+ individual Nominatim
    calls (one per comune, for boundary polygons) and can take well over
    an hour -- if a LATER pipeline step crashes, you do not want to redo
    this. Set config.FORCE_REFRESH_CACHE = True to ignore the cache.
    """
    if not config.FORCE_REFRESH_CACHE and os.path.exists(config.COMUNI_CACHE_PATH):
        print(f"[INFO] Loading comuni from cache: {config.COMUNI_CACHE_PATH}")
        gdf = gpd.read_file(config.COMUNI_CACHE_PATH)
        # GeoPackage read/write round-trips sometimes silently rename the
        # geometry column to "geometry" regardless of what it was called
        # when written (this is what caused the "stray geometry column"
        # warning during the Trentino merge too) -- normalize it back to
        # "boundary" so every downstream access of comune_row["boundary"]
        # works reliably regardless of what pyogrio decided to call it.
        if gdf.geometry.name != "boundary":
            print(f"[INFO] Renaming geometry column '{gdf.geometry.name}' -> 'boundary'.")
            gdf = gdf.rename(columns={gdf.geometry.name: "boundary"})
            gdf = gdf.set_geometry("boundary")
        gdf["center_point"] = gdf.apply(
            lambda r: Point(r["center_lon"], r["center_lat"]), axis=1
        )
        gdf = gdf.drop(columns=["center_lon", "center_lat"])
        return gdf

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

    # Cache to disk. GeoPackage only supports one active geometry column,
    # so center_point gets flattened to plain lon/lat floats for storage
    # and reconstructed as a Point on load (see the cache-read branch above).
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
    comuni.to_file("./data/comuni_padova.gpkg", driver="GPKG")