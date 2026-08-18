"""
fetch_map_layers.py
-------------------
Builds the two toggleable overlay layers for the web map -- cycling lanes and
public transport routes -- both read from the local .osm.pbf via osmium.

Target-area polygons come from geo_utils.get_target_polygons(), shared with
fetch_towns.py and memoised, so a run touching both modules makes one set of
Nominatim calls rather than two. That helper raises on an unsupported
TARGET_LEVEL rather than returning an empty list -- otherwise an unrecognised
target area produces valid-but-empty GeoJSON and exits successfully, a failure
you would only notice as a blank map.
"""

import json

import osmium
from shapely.geometry import LineString
from shapely.ops import unary_union, linemerge

from . import config
from .geo_utils import get_target_polygons

ROUTE_TYPES = {"bus", "tram", "trolleybus"}


def _round_coords(coords):
    """Trims coordinate precision to config.MAP_COORD_PRECISION decimals."""
    n = config.MAP_COORD_PRECISION
    return [[round(x, n), round(y, n)] for x, y in coords]


def _simplify(line):
    """
    Douglas-Peucker simplification at config.MAP_SIMPLIFY_TOLERANCE_DEG.

    preserve_topology=False is deliberate and safe here: these are independent
    display-only line segments, not a topological network, so there are no
    shared boundaries to keep valid. The faster non-topological algorithm is
    the right choice.
    """
    tolerance = config.MAP_SIMPLIFY_TOLERANCE_DEG
    if not tolerance:
        return line
    return line.simplify(tolerance, preserve_topology=False)


def _clip_to_polygon(coords, polygon):
    """
    Clips a line to a polygon, returning a list of coordinate lists. A single
    input line may be split into several pieces where it crosses the border.
    """
    clipped = LineString(coords).intersection(polygon)
    if clipped.is_empty:
        return []
    if clipped.geom_type == "LineString":
        return [list(clipped.coords)]
    if clipped.geom_type == "MultiLineString":
        return [list(part.coords) for part in clipped.geoms]
    return []


def _write_geojson(features, path, label):
    """
    Writes a FeatureCollection minified. These files are consumed by a
    browser, not read by hand, and the cycling layer in particular is large
    enough that pretty-printing measurably slows the hosted map's first load.
    """
    geojson = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, separators=(",", ":"))
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"[INFO] {label} GeoJSON -> {path} "
          f"({len(features)} segments, {size_mb:.2f} MB)")


class _CyclewayCollector(osmium.SimpleHandler):
    """Collects every way tagged as, or carrying, a cycleway."""

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
    """Reads cycling lane ways directly from the local .osm.pbf."""
    polygons = get_target_polygons()

    print(f"[INFO] Reading cycling lanes from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    collector = _CyclewayCollector()
    collector.apply_file(str(config.OSM_PBF_PATH), locations=True)
    print(f"[INFO] Found {len(collector.ways)} cycleway-tagged ways in the local file.")

    features = []
    for label, boundary in polygons:
        count_before = len(features)
        for _name, coords in collector.ways:
            for clipped_coords in _clip_to_polygon(list(coords), boundary):
                if len(clipped_coords) < 2:
                    continue
                simplified = _simplify(LineString(clipped_coords))
                if simplified.is_empty or len(simplified.coords) < 2:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": _round_coords(simplified.coords),
                    },
                    # No properties: the map draws these as anonymous lines, and
                    # street names across 50,000 segments would be a meaningful
                    # share of a file every visitor downloads.
                    "properties": {},
                })
        print(f"[INFO] '{label}': {len(features) - count_before} cycling segments.")

    _write_geojson(features, config.GEOJSON_CYCLING_PATH, "Cycling lanes")


class _RouteRelationCollector(osmium.SimpleHandler):
    """
    Pass 1: collects every relation tagged route=bus|tram|trolleybus and the
    IDs of every WAY member it references. Only IDs are needed here --
    resolving coordinates requires a second pass, the same two-pass pattern
    used for admin_centre resolution in fetch_towns.py.
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
    Reads bus/tram/trolleybus route relations from the local .osm.pbf.

    Each member road segment is drawn individually rather than assembling
    full continuous routes -- unnecessary for a purely visual layer.
    Overlapping segments shared by several routes are merged via
    unary_union/linemerge, so a road served by five bus lines renders once
    instead of five times.
    """
    polygons = get_target_polygons()

    print(f"[INFO] Reading public transport routes from {config.OSM_PBF_PATH} "
          f"(no internet/Overpass involved)...")
    rel_collector = _RouteRelationCollector()
    rel_collector.apply_file(str(config.OSM_PBF_PATH))
    print(f"[INFO] Found {rel_collector.route_count} bus/tram/trolleybus route relations, "
          f"referencing {len(rel_collector.wanted_way_ids)} unique road segments.")

    way_resolver = _RouteWayResolver(rel_collector.wanted_way_ids)
    way_resolver.apply_file(str(config.OSM_PBF_PATH), locations=True)
    print(f"[INFO] Resolved {len(way_resolver.lines)} road segment geometries.")

    all_lines = []
    for label, boundary in polygons:
        count_before = len(all_lines)
        for line in way_resolver.lines:
            for clipped_coords in _clip_to_polygon(list(line.coords), boundary):
                if len(clipped_coords) >= 2:
                    all_lines.append(LineString(clipped_coords))
        print(f"[INFO] '{label}': {len(all_lines) - count_before} route segments.")

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

    features = []
    for piece in pieces:
        simplified = _simplify(piece)
        if simplified.is_empty or len(simplified.coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": _round_coords(simplified.coords),
            },
            "properties": {},
        })

    _write_geojson(features, config.GEOJSON_TRANSIT_PATH, "Public transport")
