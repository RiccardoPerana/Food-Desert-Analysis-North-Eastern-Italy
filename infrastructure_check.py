"""
infrastructure_check.py
------------------------
Given a route's geometry (a LineString from OSRM), determines whether
sidewalks and/or cycling lanes exist along that specific path.

Method: buffer the route line slightly (to tolerate GPS/routing snap
imprecision), then query Overpass for all 'highway' ways intersecting
that buffer, and inspect their tags.

NOTE: This module is only used once config.SKIP_INFRASTRUCTURE_CHECK is
set to False in config.py (i.e. once the cycling-profile OSRM server is
also running -- see README.md Phase 2 setup).
"""

import overpy
from shapely.geometry import LineString

import config
from overpass_utils import query_with_retry

SIDEWALK_POSITIVE_VALUES = {"yes", "left", "right", "both", "separate"}
CYCLEWAY_POSITIVE_VALUES = {"lane", "track", "shared_lane", "share_busway", "opposite_lane", "opposite_track"}

ROUTE_BUFFER_DEG = 0.0005  # ~50m, tolerant of routing/snap imprecision


def check_route_infrastructure(route_line: LineString):
    """
    Returns a dict: {"has_sidewalk": bool, "has_cycling": bool}
    based on OSM way tags along the given route.
    """
    minx, miny, maxx, maxy = route_line.buffer(ROUTE_BUFFER_DEG).bounds

    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    way["highway"]({miny},{minx},{maxy},{maxx});
    out tags geom;
    """

    try:
        result = query_with_retry(query)
    except Exception as e:
        print(f"[ERROR] Overpass infrastructure query failed: {e}")
        # Fail safe: don't silently mark the town as "matching" on bad data.
        return {"has_sidewalk": None, "has_cycling": None, "needs_manual_review": True}

    route_buffer = route_line.buffer(ROUTE_BUFFER_DEG)

    has_sidewalk = False
    has_cycling = False

    for way in result.ways:
        try:
            coords = [(float(n.lon), float(n.lat)) for n in way.get_nodes(resolve_missing=True)]
        except Exception:
            continue
        if len(coords) < 2:
            continue

        way_line = LineString(coords)
        if not way_line.intersects(route_buffer):
            continue  # this way isn't actually on our route corridor

        tags = way.tags

        if tags.get("highway") == "cycleway":
            has_cycling = True
        if tags.get("highway") == "footway" and tags.get("footway") != "crossing":
            has_sidewalk = True

        sidewalk_val = tags.get("sidewalk")
        if sidewalk_val in SIDEWALK_POSITIVE_VALUES:
            has_sidewalk = True

        for key in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both"):
            if tags.get(key) in CYCLEWAY_POSITIVE_VALUES:
                has_cycling = True

        if has_sidewalk and has_cycling:
            break

    return {
        "has_sidewalk": has_sidewalk,
        "has_cycling": has_cycling,
        "needs_manual_review": False,
    }


def fails_infrastructure_criteria(route_line: LineString) -> bool:
    """
    Returns True if the route has NEITHER sidewalks NOR cycling lanes,
    i.e. the town fails our "safe access" test and should be flagged.
    """
    result = check_route_infrastructure(route_line)
    if result["needs_manual_review"]:
        return False  # don't auto-include on inconclusive data
    return not (result["has_sidewalk"] or result["has_cycling"])
