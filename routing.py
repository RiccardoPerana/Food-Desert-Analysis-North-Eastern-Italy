"""
Handles calls to OSRM for:
  1. Real routed walking distance from town center -> nearest supermarket
     (used against the 3km threshold).
  2. Full route geometry, later cross-referenced against OSM way tags to
     check for sidewalks/cycling lanes.

IMPORTANT: Requires a self-hosted OSRM server running and listening on config.OSRM_BASE_URL (see README.md for setup). 
If you get "connection actively refused" errors, it means the OSRM server isn't running 
start it in its own terminal tab and leave it running.
"""

import time
import requests
from shapely.geometry import Point, LineString
import polyline

import config


def find_nearest_supermarket_straightline(town_point, supermarkets_gdf):
    """
    Cheap first-pass filter: returns the nearest supermarket by straight-line distance, and that distance in km. 
    Used only to pick which supermarket to route to.
    NOT used as the final distance figure.
    """
    supermarkets_gdf = supermarkets_gdf.copy()
    supermarkets_gdf["_dist_deg"] = supermarkets_gdf.geometry.distance(town_point)
    nearest = supermarkets_gdf.loc[supermarkets_gdf["_dist_deg"].idxmin()]
    dist_km = nearest["_dist_deg"] * 111.0
    return nearest, dist_km


def get_routed_distance_and_geometry(origin_point: Point, dest_point: Point, profile: str):
    """
    Queries OSRM for the route between two points.
    Returns (distance_km, route_linestring) or (None, None) on failure.
    """
    url = (
        f"{config.OSRM_BASE_URL}/route/v1/{profile}/"
        f"{origin_point.x},{origin_point.y};{dest_point.x},{dest_point.y}"
        f"?overview=full&geometries=polyline"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "Ok" or not data.get("routes"):
            print(f"[WARN] No route found between {origin_point} and {dest_point}")
            return None, None

        route = data["routes"][0]
        distance_km = route["distance"] / 1000.0
        coords = polyline.decode(route["geometry"])  # list of (lat, lon)
        line = LineString([(lon, lat) for lat, lon in coords])
        return distance_km, line

    except requests.RequestException as e:
        print(f"[ERROR] OSRM request failed: {e}")
        return None, None
    finally:
        time.sleep(config.REQUEST_PAUSE_SEC)


def get_walking_route(town_point, supermarket_point):
    #Convenience wrapper for the pedestrian route (used for the 3km rule).
    return get_routed_distance_and_geometry(
        town_point, supermarket_point, config.OSRM_PROFILE_WALK
    )