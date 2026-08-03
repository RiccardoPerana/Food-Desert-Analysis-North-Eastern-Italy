"""
Fetches the two toggleable overlay layers for the web map, BOTH from the local .osm.pbf file (via osmium)

Cycling lanes were converted to local reading earlier this session.
Public transport routes (bus/tram/trolleybus) are now converted too
"""

import json
import osmium
from shapely.geometry import LineString
from shapely.ops import unary_union, linemerge
import osmnx as ox

import config

ROUTE_TYPES = {"bus", "tram", "trolleybus"}


def _get_target_polygons():
    """Returns [(label, polygon), ...] for each target region/province, via Nominatim."""
    if config.TARGET_LEVEL == "multi_region":
        names = config.TARGET_REGIONS
    elif config.TARGET_LEVEL in ("region", "province"):
        names = [config.TARGET_NAME]
    else:
        names = []

    polygons = []
    for name in names:
        try:
            gdf = ox.geocode_to_gdf(f"{name}, Italy")
            polygons.append((name, gdf.geometry.iloc[0]))
        except Exception as e:
            print(f"[WARN] Could not geocode '{name}' for clipping: {e}")
    return polygons


def _clip_to_polygon(coords, polygon):
    """Clips a line to a polygon, returning a list of coordinate-lists (may split at borders)."""
    line = LineString(coords)
    clipped = line.intersection(polygon)
    if clipped.is_empty:
        return []
    if clipped.geom_type == "LineString":
        return [list(clipped.coords)]
    elif clipped.geom_type == "MultiLineString":
        return [list(part.coords) for part in clipped.geoms]
    return []


class _CyclewayCollector(osmium.SimpleHandler):
    """Collects every way tagged as a cycleway."""
    def __init__(self):
        super().__init__()
        self.ways = []

    def way(self, w):
        tags = w.tags
        is_cycleway = (
            tags.get("highway") == "cycleway"
            or "cycleway" in tags
            or "cycleway:left" in tags
            or "cycleway:right" in tags
        )
        if not is_cycleway:
            return
        coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(coords) >= 2:
            self.ways.append((tags.get("name", ""), coords))


def fetch_cycling_lanes_geojson():
    """Reads cycling lane ways directly from the local .osm.pbf file."""
    polygons = _get_target_polygons()

    print(f"[INFO] Reading cycling lanes from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    collector = _CyclewayCollector()
    collector.apply_file(config.OSM_PBF_PATH, locations=True)
    print(f"[INFO] Found {len(collector.ways)} cycleway-tagged ways in the local file.")

    features = []
    for label, boundary in polygons:
        count_before = len(features)
        for name, coords in collector.ways:
            for clipped_coords in _clip_to_polygon(list(coords), boundary):
                if len(clipped_coords) < 2:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": clipped_coords},
                    "properties": {"name": name, "type": "cycleway"},
                })
        print(f"[INFO] '{label}': {len(features) - count_before} cycling segments (clipped locally).")

    geojson = {"type": "FeatureCollection", "features": features}
    with open(config.GEOJSON_CYCLING_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"[INFO] Cycling lanes GeoJSON -> {config.GEOJSON_CYCLING_PATH} "
          f"({len(features)} segments, clipped to real borders, fully local)")


class _RouteRelationCollector(osmium.SimpleHandler):
    """
    Pass 1: collects every relation tagged route=bus|tram|trolleybus, and
    the IDs of every WAY member it references. Only the IDs are needed
    here -- resolving their actual coordinates requires a second pass
    (see _RouteWayResolver below), the same two-pass pattern used
    elsewhere in this project (e.g. admin_centre resolution).
    """
    def __init__(self):
        super().__init__()
        self.wanted_way_ids = set()
        self.route_count = 0

    def relation(self, r):
        if r.tags.get("route") not in ROUTE_TYPES:
            return
        self.route_count += 1
        for m in r.members:
            if m.type == "w":
                self.wanted_way_ids.add(m.ref)


class _RouteWayResolver(osmium.SimpleHandler):
    """Pass 2: resolves the geometry of every way ID collected in pass 1."""
    def __init__(self, wanted_way_ids):
        super().__init__()
        self.wanted_way_ids = wanted_way_ids
        self.lines = []

    def way(self, w):
        if w.id not in self.wanted_way_ids:
            return
        coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(coords) >= 2:
            self.lines.append(LineString(coords))


def fetch_public_transport_geojson():
    """
    Reads bus/tram/trolleybus route relations directly from the local.osm.pbf file. 
    Draws each individual member road segment rather than assembling full continuous routes since it is unnecessary for a purely visual map layer. 
    Overlapping segments shared by multiple routes are merged into one via unary_union/linemerge, 
    so a road used by several bus lines only renders once, avoiding visually thicker lines where routesoverlap.
    """
    polygons = _get_target_polygons()

    print(f"[INFO] Reading public transport routes from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    rel_collector = _RouteRelationCollector()
    rel_collector.apply_file(config.OSM_PBF_PATH)
    print(f"[INFO] Found {rel_collector.route_count} bus/tram/trolleybus route relations, "
          f"referencing {len(rel_collector.wanted_way_ids)} unique road segments.")

    way_resolver = _RouteWayResolver(rel_collector.wanted_way_ids)
    way_resolver.apply_file(config.OSM_PBF_PATH, locations=True)
    print(f"[INFO] Resolved {len(way_resolver.lines)} road segment geometries.")

    all_lines = []
    for label, boundary in polygons:
        count_before = len(all_lines)
        for line in way_resolver.lines:
            for clipped_coords in _clip_to_polygon(list(line.coords), boundary):
                if len(clipped_coords) >= 2:
                    all_lines.append(LineString(clipped_coords))
        print(f"[INFO] '{label}': {len(all_lines) - count_before} route segments (clipped locally).")

    merged = unary_union(all_lines) if all_lines else None
    if merged is not None and not merged.is_empty:
        merged = linemerge(merged)

    if merged is None or merged.is_empty:
        pieces = []
    elif merged.geom_type == "LineString":
        pieces = [merged]
    elif merged.geom_type == "MultiLineString":
        pieces = list(merged.geoms)
    else:
        pieces = []

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": list(piece.coords)},
            "properties": {},
        }
        for piece in pieces
    ]

    geojson = {"type": "FeatureCollection", "features": features}
    with open(config.GEOJSON_TRANSIT_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"[INFO] Public transport GeoJSON -> {config.GEOJSON_TRANSIT_PATH} "
          f"({len(features)} segments, clipped to real borders, fully local)")


if __name__ == "__main__":
    fetch_cycling_lanes_geojson()
    fetch_public_transport_geojson()