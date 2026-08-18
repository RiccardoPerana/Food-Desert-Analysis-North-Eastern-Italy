"""
geo_utils.py
------------
Shared geographic helpers.

Three things live here because they must be identical everywhere they are
used, and are easy to get subtly wrong in isolation.

1. THE METRIC PROJECTION (METRIC_CRS).
   Every distance and buffer in this project is computed in metres, in one
   shared CRS. Two modules using different projections would produce distances
   that are not comparable to each other -- a discrepancy that produces
   plausible numbers rather than an error.

2. DEGREES-TO-KILOMETRES CONVERSION (km_to_degrees).
   The familiar "divide by 111" is correct for LATITUDE only. At roughly 46
   degrees north -- the entire study area -- one degree of LONGITUDE spans
   about 77 km, so a flat 111 stretches east-west distances by about 1.44x.
   That is enough to pick the wrong nearest supermarket. This helper accounts
   for latitude, and is used only for rough bounding boxes; anything affecting
   results projects to METRIC_CRS instead.

3. TARGET-AREA POLYGON LOOKUP (get_target_polygons).
   Resolved from Nominatim once and memoised, so the several modules needing
   the study-area boundary share one set of network calls per run.
"""

import math

import osmnx as ox

from . import config


# ---------------------------------------------------------------------------
# PROJECTION
# ---------------------------------------------------------------------------
# EPSG:3035 is ETRS89 / LAEA Europe: an equal-area projection expressed in
# METRES, designed by the EU for exactly this kind of pan-European analysis.
#
# Working in this CRS means .distance(), .buffer() and spatial-index
# nearest-neighbour queries all speak real metres directly. No hand-rolled
# conversion factors, no latitude-dependent error, no anisotropy.
METRIC_CRS = "EPSG:3035"

WGS84 = "EPSG:4326"

# Kilometres per degree of latitude. Near-constant everywhere on Earth.
KM_PER_DEG_LAT = 111.32


def km_to_degrees(km, latitude):
    """
    Converts a distance in km into (degrees_latitude, degrees_longitude) at
    the given latitude.

    The two return values differ because meridians converge towards the
    poles: a degree of longitude is a full 111 km at the equator but shrinks
    by cos(latitude) as you move away from it.

    NOTE: this is only appropriate for drawing rough bounding boxes, which
    is why the only remaining caller is the diagnostic script. Anything that
    affects the actual results should project to METRIC_CRS instead.
    """
    deg_lat = km / KM_PER_DEG_LAT
    deg_lon = km / (KM_PER_DEG_LAT * math.cos(math.radians(latitude)))
    return deg_lat, deg_lon


# ---------------------------------------------------------------------------
# TARGET AREA RESOLUTION
# ---------------------------------------------------------------------------
_TARGET_POLYGON_CACHE = None


def get_target_polygons():
    """
    Returns [(label, polygon), ...] for the configured target area, geocoded
    via Nominatim.

    The result is memoised, so the several modules that need it share a
    single set of network calls per run rather than each paying for its own.

    Raises ValueError on an unsupported TARGET_LEVEL rather than returning an
    empty list, which would let fetch_map_layers.py write an empty GeoJSON and
    exit successfully.
    """
    global _TARGET_POLYGON_CACHE
    if _TARGET_POLYGON_CACHE is not None:
        return _TARGET_POLYGON_CACHE

    if config.TARGET_LEVEL == "multi_region":
        names = config.TARGET_REGIONS
    elif config.TARGET_LEVEL in ("region", "province"):
        names = [config.TARGET_NAME]
    elif config.TARGET_LEVEL == "country":
        # SCALE HOOK (national): reserved and recognised, but not yet built.
        # This deliberately raises rather than returning an empty list. The
        # previous implementation returned [], which caused fetch_map_layers
        # to write out empty overlay files with no error at all -- a failure
        # you would only discover by noticing a blank map.
        raise NotImplementedError(
            "TARGET_LEVEL = 'country' is a reserved scale hook, not yet "
            "implemented. Three pieces of work are required:\n"
            "  1. A country-level boundary source. Nominatim can geocode "
            "'Italy', but the multipolygon it returns includes every offshore "
            "island and is slow to intersect -- a simplified boundary is "
            "strongly preferred.\n"
            "  2. National OSM coverage, via config.OSM_PBF_PATHS.\n"
            "  3. A disk-backed osmium node index -- the in-memory default "
            "will not fit a national extract on a typical machine.\n"
            "See the SCALE HOOK notes in config.py for the full picture."
        )
    else:
        raise ValueError(
            f"Unsupported config.TARGET_LEVEL: {config.TARGET_LEVEL!r}. "
            f"Expected one of: 'multi_region', 'region', 'province', 'country'."
        )

    print(f"[INFO] Resolving target area polygons via Nominatim: {names}")
    polygons = []
    for name in names:
        try:
            gdf = ox.geocode_to_gdf(f"{name}, Italy")
            polygons.append((name, gdf.geometry.iloc[0]))
        except Exception as e:
            print(f"[WARN] Could not geocode '{name}': {e}")

    if not polygons:
        raise RuntimeError(
            "Could not resolve ANY target area polygon. Check your internet "
            "connection and the region names in config.TARGET_REGIONS."
        )

    _TARGET_POLYGON_CACHE = polygons
    return polygons


def get_combined_target_polygon():
    """
    Returns a single polygon covering the whole target area, for
    point-in-area filtering.
    """
    polygons = [poly for _, poly in get_target_polygons()]
    combined = polygons[0]
    for p in polygons[1:]:
        combined = combined.union(p)
    return combined
