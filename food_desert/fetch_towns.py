"""
fetch_towns.py
---------------
Builds the towns dataset: one row per municipality, with a boundary polygon
and the point where its name label sits on the map.

Admin-centre and place-node lookups read directly from the local .osm.pbf via
`osmium`, with no network involved -- public Overpass mirrors have a confirmed
silent failure mode under load, returning successful responses that are quietly
missing entries.

Boundary POLYGON geometry is the one exception: it comes from osmnx/Nominatim,
one request per town. See the SCALE HOOK note on
fetch_town_boundary_polygons() -- that call is simultaneously the slowest step
in the pipeline and the source of its least reliable data.
"""

import time

import osmium
import geopandas as gpd
from shapely.geometry import Point
import osmnx as ox

from . import config
from .geo_utils import get_combined_target_polygon, WGS84


class _AdminRelationCollector(osmium.SimpleHandler):
    """
    Pass 1: collects every admin_level=8 boundary relation's name and, where
    tagged, the node ID of its admin_centre member.

    Only the ID is captured here, not the location. Relations are stored
    after nodes in a normal .osm.pbf, so which node IDs matter cannot be
    known until this first pass has completed -- hence the second pass in
    _NodeLocationResolver below.
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


def fetch_town_relations_with_centers():
    """
    Reads all admin_level=8 boundary relations from the local .osm.pbf and
    resolves each one's admin_centre point.
    """
    print(f"[INFO] Reading town boundary relations from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    collector = _AdminRelationCollector()
    collector.apply_file(str(config.OSM_PBF_PATH))

    node_locations = {}
    if collector.wanted_node_ids:
        resolver = _NodeLocationResolver(collector.wanted_node_ids)
        resolver.apply_file(str(config.OSM_PBF_PATH))
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
    print(f"[INFO] {found}/{len(records)} towns had a tagged admin_centre "
          f"(resolved locally from file, no network).")
    return records


class _PlaceNodeCollector(osmium.SimpleHandler):
    """Collects every place=city|town|village node, for the label-point fallback."""

    def __init__(self):
        super().__init__()
        self.lookup = {}

    def node(self, n):
        if n.tags.get("place") in ("city", "town", "village"):
            name = n.tags.get("name")
            if name:
                self.lookup[name.strip().lower()] = Point(n.location.lon, n.location.lat)


def fetch_all_place_nodes_bulk():
    """
    Reads every place=city|town|village node from the local .osm.pbf in one
    pass. Used as a fallback name lookup for towns without a properly tagged
    admin_centre.
    """
    collector = _PlaceNodeCollector()
    collector.apply_file(str(config.OSM_PBF_PATH))
    print(f"[INFO] Found {len(collector.lookup)} place nodes locally for fallback matching.")
    return collector.lookup


def fetch_town_boundary_polygons(names):
    """
    Fetches a boundary polygon per town name via osmnx/Nominatim.

    This is one network request per town and is by a wide margin the
    longest-running step in the pipeline (30-60+ minutes for three regions).
    Progress is logged periodically so it is obvious the process has not hung.

    SCALE HOOK (national): this function is the pipeline's hard ceiling.
    ~7,900 towns at Nominatim's mandatory 1 request/second is over two hours
    of pure waiting, and their usage policy discourages bulk querying at that
    volume regardless.

    It is also the source of the pipeline's least reliable data. `which_result=1`
    accepts whatever Nominatim ranks first, which occasionally returns a
    different entity sharing the town's name, or a polygon that is offset or
    too small. config.OWN_SUPERMARKET_CENTER_RADIUS_M exists purely to paper
    over those cases.

    Required to activate national scale: read the boundary polygons from the
    local .osm.pbf instead. osmium can assemble admin_level=8 relations into
    closed areas via osmium.area.MultipolygonCollector, using the SAME
    relations already being read in pass 1 above. That removes every network
    call in this module AND removes the wrong-entity problem at its source --
    two of this project's three biggest weaknesses in one change.
    """
    geoms = {}
    total = len(names)
    for i, name in enumerate(names, start=1):
        try:
            gdf = ox.geocode_to_gdf(f"{name}, Italy", which_result=1)
            geoms[name] = gdf.geometry.iloc[0]
        except Exception as e:
            print(f"[WARN] Could not resolve boundary polygon for {name}: {e}")
        if i % 25 == 0 or i == total:
            print(f"[INFO] Boundary polygon progress: {i}/{total} towns processed.")

        # Nominatim's usage policy requires a maximum of 1 request per second.
        # This is a genuine third-party rate limit and must stay. (Local OSRM
        # calls deliberately have no such delay -- see routing.py.)
        time.sleep(config.NOMINATIM_PAUSE_SEC)
    return geoms


def _filter_to_target_regions(gdf):
    """
    The Nord-Est extract includes some bordering territory beyond the three
    target regions. This trims the towns down to those whose centre point
    actually falls inside the target area.

    Called ONCE, before the result is cached. Running it again on cache load
    would re-geocode the region polygons against Nominatim on every run, to
    re-filter data that was already filtered before it was saved.
    """
    if config.TARGET_LEVEL == "province":
        return gdf  # single-province mode does not need this filter

    print("[INFO] Filtering towns to the target area "
          "(the local extract includes some bordering territory)...")
    combined_region = get_combined_target_polygon()

    before_count = len(gdf)
    mask = gdf["center_point"].apply(combined_region.contains)
    filtered = gdf[mask].copy()
    removed = before_count - len(filtered)
    if removed:
        print(f"[INFO] Removed {removed} towns outside the target area "
              f"(e.g. neighbouring regions picked up by the extract).")
    return filtered


def build_towns_dataset():
    """
    Full assembly: boundaries plus label points.
    Columns: name, osm_id, province_name, boundary (polygon), center_point (Point)

    The result is cached to config.TOWNS_CACHE_PATH so that a crash in a
    LATER pipeline step does not cost another full Nominatim run. Set
    config.FORCE_REFRESH_CACHE = True to rebuild.

    WARNING: FORCE_REFRESH_CACHE also invalidates the supermarket cache. If
    you only need supermarkets rebuilt, delete data/cache/supermarkets_cache.gpkg
    directly rather than setting this flag -- it is the difference between a
    90-second restart and a 45-minute one.
    """
    if not config.FORCE_REFRESH_CACHE and config.TOWNS_CACHE_PATH.exists():
        print(f"[INFO] Loading towns from cache: {config.TOWNS_CACHE_PATH}")
        gdf = gpd.read_file(config.TOWNS_CACHE_PATH)

        if gdf.geometry.name != "boundary":
            print(f"[INFO] Renaming geometry column '{gdf.geometry.name}' -> 'boundary'.")
            gdf = gdf.rename(columns={gdf.geometry.name: "boundary"})
            gdf = gdf.set_geometry("boundary")

        gdf["center_point"] = gdf.apply(
            lambda r: Point(r["center_lon"], r["center_lat"]), axis=1
        )
        gdf = gdf.drop(columns=["center_lon", "center_lat"])

        # NOTE: no re-filter here. The cache was written post-filter, so
        # re-running it would only cost three redundant Nominatim calls.
        print(f"[INFO] Loaded {len(gdf)} towns from cache (already filtered to target area).")
        return gdf

    records = fetch_town_relations_with_centers()

    missing = [r for r in records if r["center_point"] is None]
    if missing:
        place_lookup = fetch_all_place_nodes_bulk()
        for r in missing:
            key = r["name"].strip().lower()
            if key in place_lookup:
                r["center_point"] = place_lookup[key]

    names = [r["name"] for r in records]
    print(f"[INFO] Fetching boundary polygons for {len(names)} towns via Nominatim. "
          f"At {config.NOMINATIM_PAUSE_SEC}s/request this will take roughly "
          f"{len(names) * config.NOMINATIM_PAUSE_SEC / 60:.0f} minutes -- "
          f"it is cached afterwards, so this cost is paid once.")
    polygons = fetch_town_boundary_polygons(names)

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

    gdf = gpd.GeoDataFrame(valid_records, geometry=geoms, crs=WGS84)
    gdf = gdf.rename(columns={"geometry": "boundary"}).set_geometry("boundary")
    gdf = _filter_to_target_regions(gdf)

    config.TOWNS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_gdf = gdf.copy()
    cache_gdf["center_lon"] = cache_gdf["center_point"].apply(lambda p: p.x)
    cache_gdf["center_lat"] = cache_gdf["center_point"].apply(lambda p: p.y)
    cache_gdf = cache_gdf.drop(columns=["center_point"])
    cache_gdf.to_file(config.TOWNS_CACHE_PATH, driver="GPKG")
    print(f"[INFO] Cached towns dataset to {config.TOWNS_CACHE_PATH}")

    return gdf
