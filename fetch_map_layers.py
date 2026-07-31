"""
fetch_map_layers.py
--------------------
Fetches the two toggleable overlay layers for the web map:
  1. Cycling lanes (area-wide, not just on matched-town routes)
  2. Public transport routes (bus/tram relations)

Every query is scoped to ONE sub-area at a time (one province, or one
region when running multi_region), never the whole combined target area
in a single request. This matters a lot at multi-region scale: a single
query covering Veneto + Friuli-Venezia Giulia + Trentino-Alto Adige at
once would almost certainly time out, the same way a single all-route-
types query timed out even for Padua province alone. Splitting by area
AND by route type keeps every individual request small enough to reliably
finish within a public mirror's timeout window.
"""

import json
from shapely.geometry import LineString
from shapely.ops import unary_union, linemerge
import osmnx as ox

import config
from overpass_utils import query_with_retry


def _get_target_polygons():
    """
    Returns a list of (label, polygon) pairs to process SEPARATELY.
    - province/region/other: a single-item list.
    - multi_region: one item per region in config.TARGET_REGIONS.
    """
    if config.TARGET_LEVEL == "province":
        gdf = ox.geocode_to_gdf(f"Provincia di {config.TARGET_NAME}, Italy")
        return [(config.TARGET_NAME, gdf.geometry.iloc[0])]
    elif config.TARGET_LEVEL == "region":
        gdf = ox.geocode_to_gdf(f"{config.TARGET_NAME}, Italy")
        return [(config.TARGET_NAME, gdf.geometry.iloc[0])]
    elif config.TARGET_LEVEL == "multi_region":
        result = []
        for region_name in config.TARGET_REGIONS:
            gdf = ox.geocode_to_gdf(f"{region_name}, Italy")
            result.append((region_name, gdf.geometry.iloc[0]))
        return result
    else:
        gdf = ox.geocode_to_gdf(config.TARGET_NAME)
        return [(config.TARGET_NAME, gdf.geometry.iloc[0])]


def _poly_filter(boundary):
    """
    Builds an Overpass "poly:" filter string from a boundary polygon,
    instead of using area["name"=...]->.searchArea;

    WHY: some public Overpass mirrors have incomplete or out-of-sync
    named-area indexes -- a query using area["name"="Padova"] can silently
    return ZERO results on one mirror while working fine on another. A
    "poly:" filter is purely geometric and doesn't depend on any mirror's
    area-name index, so it's far more consistent across mirrors.

    Some regions (e.g. Friuli-Venezia Giulia) come back from Nominatim as
    a MultiPolygon rather than a single Polygon (small exclaves/islands
    split into separate pieces). Overpass's "poly:" filter only accepts a
    single ring, so we use the largest sub-polygon as a representative
    shape for this broad query -- exact clipping against the FULL
    multi-part boundary still happens afterward in _clip_to_polygon, so
    this only affects which candidates get initially queried, not the
    final accuracy of what's drawn.

    The polygon is simplified first -- a full-precision border can have
    thousands of vertices, making for a very long query string. A ~100m
    tolerance is precise enough here.
    """
    if boundary.geom_type == "MultiPolygon":
        boundary = max(boundary.geoms, key=lambda p: p.area)

    simplified = boundary.simplify(0.001, preserve_topology=True)
    coords = list(simplified.exterior.coords)
    # Overpass "poly:" filter expects "lat lon lat lon ..." (reversed from
    # the usual lon,lat GeoJSON order).
    return " ".join(f"{lat} {lon}" for lon, lat in coords)


def _query_with_empty_recheck(query, label, max_empty_retries=2):
    """
    Wraps query_with_retry with an additional safety net: some mirrors
    occasionally return a technically-successful but implausibly EMPTY
    result (e.g. Veneto returning 0 bus routes -- impossible for a region
    containing Venice, Padova, and Verona) instead of a proper error, so
    our normal retry-on-failure logic never kicks in. This retries the
    whole query with a fresh mirror rotation before accepting an empty
    result as final. Genuinely empty categories (e.g. trolleybus routes,
    which legitimately don't exist in most of this area) just cost a
    couple of harmless extra retries and still correctly come back empty.
    """
    result = None
    for attempt in range(max_empty_retries + 1):
        result = query_with_retry(query)
        has_data = bool(result.relations) or bool(result.ways)
        if has_data or attempt == max_empty_retries:
            return result
        print(f"[WARN] '{label}' returned an empty result -- retrying with a "
              f"fresh mirror rotation ({attempt + 1}/{max_empty_retries})...")
    return result


def _clip_to_polygon(coords, polygon):
    """
    Clips a line (list of [lon, lat]) to the given polygon. Returns a list
    of coordinate-lists (a line crossing the border more than once splits
    into multiple pieces).
    """
    line = LineString(coords)
    clipped = line.intersection(polygon)
    if clipped.is_empty:
        return []
    if clipped.geom_type == "LineString":
        return [list(clipped.coords)]
    elif clipped.geom_type == "MultiLineString":
        return [list(part.coords) for part in clipped.geoms]
    return []


def fetch_cycling_lanes_geojson():
    polygons = _get_target_polygons()
    features = []
    skipped_total = 0

    for label, boundary in polygons:
        try:
            poly = _poly_filter(boundary)
            query = f"""
            [out:json][timeout:{config.OVERPASS_TIMEOUT}];
            (
              way["highway"="cycleway"](poly:"{poly}");
              way["cycleway"](poly:"{poly}");
              way["cycleway:left"](poly:"{poly}");
              way["cycleway:right"](poly:"{poly}");
            );
            out geom;
            """
            result = _query_with_empty_recheck(query, label)
        except Exception as e:
            print(f"[WARN] Cycling lanes fetch failed for '{label}', "
                  f"skipping this region: {e}")
            continue

        count_before = len(features)
        for way in result.ways:
            # "out geom;" embeds full coordinate geometry directly on the way
            # object -- way.nodes would try (and fail) to resolve separate
            # node elements, which aren't present in this response format.
            geom = way.attributes.get("geometry")
            if not geom:
                skipped_total += 1
                continue
            coords = [[float(pt["lon"]), float(pt["lat"])] for pt in geom if pt is not None]
            if len(coords) < 2:
                continue
            for clipped_coords in _clip_to_polygon(coords, boundary):
                if len(clipped_coords) < 2:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": clipped_coords},
                    "properties": {"name": way.tags.get("name", ""), "type": "cycleway"},
                })
        print(f"[INFO] '{label}': {len(features) - count_before} cycling segments fetched.")

    if skipped_total:
        print(f"[INFO] Skipped {skipped_total} way(s) with incomplete geometry.")

    geojson = {"type": "FeatureCollection", "features": features}
    with open(config.GEOJSON_CYCLING_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"[INFO] Cycling lanes GeoJSON -> {config.GEOJSON_CYCLING_PATH} "
          f"({len(features)} segments, clipped to real borders)")


def fetch_public_transport_geojson():
    polygons = _get_target_polygons()
    route_types = ["bus", "tram", "trolleybus"]
    all_lines = []

    for label, boundary in polygons:
        try:
            poly = _poly_filter(boundary)
        except Exception as e:
            print(f"[WARN] Could not build polygon filter for '{label}', "
                  f"skipping this region entirely: {e}")
            continue

        for route_type in route_types:
            query = f"""
            [out:json][timeout:{config.OVERPASS_TIMEOUT}];
            relation["route"="{route_type}"](poly:"{poly}");
            out geom;
            """
            try:
                result = _query_with_empty_recheck(query, f"{label} / {route_type}")
            except Exception as e:
                print(f"[WARN] '{label}' / '{route_type}' query failed after all retries: {e}")
                continue

            count_before = len(all_lines)
            for rel in result.relations:
                for member in rel.members:
                    if hasattr(member, "geometry") and member.geometry:
                        coords = [[float(pt.lon), float(pt.lat)] for pt in member.geometry]
                        if len(coords) < 2:
                            continue
                        for clipped_coords in _clip_to_polygon(coords, boundary):
                            if len(clipped_coords) >= 2:
                                all_lines.append(LineString(clipped_coords))
            print(f"[INFO] '{label}' / '{route_type}': "
                  f"{len(all_lines) - count_before} line segments fetched.")

    # Merge all overlapping/touching lines into a single dissolved network,
    # so each physical road segment appears exactly once in the output,
    # no matter how many routes (or how many regions' worth of routes)
    # run along it.
    merged = unary_union(all_lines) if all_lines else None
    if merged is not None and not merged.is_empty:
        merged = linemerge(merged)
        if merged.geom_type == "LineString":
            pieces = [merged]
        elif merged.geom_type == "MultiLineString":
            pieces = list(merged.geoms)
        else:
            pieces = []
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
          f"({len(features)} segments, clipped to real borders)")


if __name__ == "__main__":
    fetch_cycling_lanes_geojson()
    fetch_public_transport_geojson()