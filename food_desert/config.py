"""
config.py
---------
Central configuration for the Food Desert Analysis pipeline.

Change the AREA OF INTEREST block to scale from a single province up to
several regions, and eventually to a whole country.

--- A NOTE ON "SCALE HOOK" MARKERS -------------------------------------------
Several settings below are tagged:

    # SCALE HOOK (national): ...

These are DELIBERATE extension points for running this pipeline at national
scale. They are not yet read by any code path, which makes them look
identical to accidental dead code in a diff or a static analysis pass.

They are not dead code. Do not remove them during cleanup. Each tag states
what the setting is for and what work is required to activate it.

Anything NOT carrying that tag has no such protection.
"""

import socket
import urllib.request

from . import paths

# ---------------------------------------------------------------------------
# GLOBAL NETWORK DEFAULTS
# ---------------------------------------------------------------------------
# Prevents indefinite hangs on an unresponsive Overpass mirror. Applied at
# import time because overpy uses urllib internally and offers no per-request
# timeout hook of its own.
socket.setdefaulttimeout(60)

# Public Overpass servers reject requests carrying a generic or missing
# User-Agent header with HTTP 406. Installing a global opener with a proper
# identifying agent fixes every overpy query in the project at once.
_opener = urllib.request.build_opener()
_opener.addheaders = [
    ("User-Agent", "FoodDesertAnalysisTool/1.0 (personal research project)")
]
urllib.request.install_opener(_opener)

# ---------------------------------------------------------------------------
# AREA OF INTEREST
# ---------------------------------------------------------------------------
# Supported today: "multi_region" | "region" | "province"
#
# SCALE HOOK (national): "country" is a recognised, reserved value. It is
# accepted by the config but raises NotImplementedError in
# geo_utils.get_target_polygons(), with a description of the remaining work.
# It raises rather than returning an empty polygon list, which would produce
# valid-but-empty overlay files and exit successfully.
# Required to activate:
#   1. A country-level polygon source. Nominatim can geocode "Italy", but the
#      returned multipolygon includes every offshore island and is expensive
#      to intersect against -- a simplified boundary performs far better.
#   2. A national .osm.pbf, or the multi-extract path via OSM_PBF_PATHS.
#   3. A disk-backed osmium node index -- see the note on OSM_PBF_PATHS.
TARGET_LEVEL = "multi_region"

TARGET_NAME = "Padova"          # used when TARGET_LEVEL is "region" or "province"
TARGET_REGIONS = ["Veneto", "Friuli-Venezia Giulia", "Trentino-Alto Adige"]

# SCALE HOOK (national): ISO 3166-1 alpha-2 code of the country under study.
# Not read today because the target area is always resolved from explicit
# region names. Required at country level to disambiguate geocoding (towns
# share names with places in other countries) and to select the correct
# Geofabrik extract path.
COUNTRY_ISO = "IT"

# How far beyond the study area's edge to search for supermarkets, in km.
#
# This is NOT a future feature -- it fixes a limitation that exists right
# now: a town on the outer boundary whose nearest shop sits just across that
# boundary would otherwise never see it, and would be wrongly reported as
# underserved. Now applied in fetch_supermarkets.py.
#
# NOTE: changing this value invalidates the supermarket cache. Delete
# data/cache/supermarkets_cache.gpkg after adjusting it, or the cached clip
# radius silently persists and the new value has no effect.
BORDER_BUFFER_KM = 12

# ---------------------------------------------------------------------------
# LOCAL OSM EXTRACT
# ---------------------------------------------------------------------------
# One file serves three purposes: supermarket POIs, town admin-centre
# lookups, and the cycling-lane / public-transport map overlays. It is also
# the same file OSRM is built from.
OSM_PBF_PATH = paths.OSM_DIR / "nord-est-latest.osm.pbf"

# SCALE HOOK (national): the list of extracts to read POIs from. Kept as a
# LIST, not a single path, because the practical route to national coverage
# is several regional Geofabrik extracts rather than one enormous file --
# and because neighbouring-country extracts can be appended here to close the
# cross-border gap in Known Limitations without changing any code.
#
# Deduplication by (osm_id, osm_type) already handles the overlap where two
# adjacent extracts cover the same ground.
#
# MEMORY WARNING: osmium's apply_file(..., locations=True) builds an in-memory
# index of node coordinates. For the ~500MB nord-est extract that is roughly
# 1.5-3GB of RAM. A full Italy extract (~2GB) needs well beyond what an 8GB
# machine can provide, and requires switching to a disk-backed index:
#     handler.apply_file(path, locations=True,
#                        idx="sparse_file_array,nodes.cache")
# This is the single hardest constraint on running nationally -- harder than
# runtime, and harder than anything in the Python code itself.
OSM_PBF_PATHS = [OSM_PBF_PATH]

# ---------------------------------------------------------------------------
# CRITERIA THRESHOLDS
# ---------------------------------------------------------------------------
# Routed walking distance from the town centre to the nearest supermarket.
# Anything above this counts as underserved.
DISTANCE_THRESHOLD_KM = 3.0

# A supermarket within this many METRES of the town's boundary counts as
# the town "having one of its own".
OWN_SUPERMARKET_BUFFER_M = 500

# Safety net for towns whose Nominatim boundary polygon came back too small
# or offset: a supermarket within this radius of the town CENTRE also counts
# as the town having one.
OWN_SUPERMARKET_CENTER_RADIUS_M = 1500

# Results at or beyond this distance are highlighted in the spreadsheet as
# worth a manual look rather than being silently trusted. Genuinely remote
# mountain towns really can be this far from a shop by road, so this is a
# review flag, not a rejection.
DISTANCE_REVIEW_THRESHOLD_KM = 10.0

# OSM shop tags that count as "supermarket or minimarket".
SUPERMARKET_TAGS = {
    "shop": ["supermarket", "convenience"]
}

# ---------------------------------------------------------------------------
# CACHING
# ---------------------------------------------------------------------------
TOWNS_CACHE_PATH = paths.CACHE_DIR / "towns_cache.gpkg"
SUPERMARKETS_CACHE_PATH = paths.CACHE_DIR / "supermarkets_cache.gpkg"
FORCE_REFRESH_CACHE = False   # True = ignore caches and rebuild everything

# ---------------------------------------------------------------------------
# ISTAT DEMOGRAPHIC DATA
# ---------------------------------------------------------------------------
# Official ISTAT workbooks, used EXACTLY AS DOWNLOADED -- no manual editing.
# Source: "Censimento della popolazione: dati regionali, anno 2024"
# https://www.istat.it/comunicato-territoriale/censimento-della-popolazione-dati-regionali-anno-2024/
#
# Reading the published .xlsx directly (rather than hand-converted CSVs) is
# what makes the demographic half of this analysis reproducible: anyone who
# clones the repository can download the same four files and regenerate the
# same numbers.
#
# NOTE: Trentino-Alto Adige ships as TWO workbooks, one per autonomous
# province (Trento, and Bolzano/Alto Adige). Both are listed.
def _discover_istat_workbooks():
    """
    Every .xlsx in data/istat/ is treated as an ISTAT regional workbook.

    Filenames are NOT hardcoded, deliberately. ISTAT's published names vary in
    punctuation between downloads ("Allegato-statistico" vs
    "Allegato_statistico", "04_1" vs "04.1"), browsers append " (1)" to
    duplicates, and Windows hides extensions -- so a hardcoded list produces a
    MISSING file for something that is visibly sitting in the folder.

    Discovery removes that entire class of problem: drop a workbook in, and it
    is picked up. Adding a region needs no code change at all, which is also
    what the national-scale path requires.
    """
    if not paths.ISTAT_DIR.exists():
        return []
    return sorted(paths.ISTAT_DIR.glob("*.xlsx"))


ISTAT_POPULATION_XLSX = _discover_istat_workbooks()

# Drop towns with no ISTAT match from the analysis entirely.
#
# ISTAT covers every municipality in the three target regions, so a town that
# fails to match is almost always one the OSM extract picked up from OUTSIDE
# the study area -- Austrian, Slovenian, San Marino, or a neighbouring Italian
# region. Leaving them in means analysing towns that are not in the stated
# study area, and reporting them without population figures.
#
# Set to False to keep them and inspect the list first. Turning this on is
# the recommended default once you have reviewed the unmatched names once.
EXCLUDE_UNMATCHED_TOWNS = True

# ---------------------------------------------------------------------------
# VULNERABILITY SCORING
# ---------------------------------------------------------------------------
# Distance alone treats a commuter town of 4,000 with a median age of 38 and a
# mountain village of 400 where a third of residents are over 75 as equivalent
# findings. They are not. The score below weights the distance burden by the
# number of people most affected by it.
#
#     vulnerability = residents_65plus x (routed_km - DISTANCE_THRESHOLD_KM)
#
# The unit is "elderly-kilometres": how many people are affected, multiplied by
# how far past the acceptable threshold they are. It has no meaning on its own,
# only as a ranking -- which is exactly what it is for. Sorting by it surfaces
# where the problem is LARGEST, whereas sorting by distance alone surfaces only
# where it is most extreme, which tends to be tiny hamlets.
#
# 65 is the standard pensionable-age cutoff and matches ISTAT's own "indice di
# vecchiaia" numerator, so the figure is directly comparable to published
# statistics rather than being a threshold invented here.
VULNERABILITY_AGE_FIELD = "population_65plus"

# ---------------------------------------------------------------------------
# WEB MAP PAYLOAD SIZE
# ---------------------------------------------------------------------------
# The cycling and public-transport overlays are by far the largest published
# files -- roughly 15MB each before optimisation, and BOTH are fetched when
# the page opens, not when the user toggles them on. On a phone that is a slow,
# expensive blank screen before anything renders.
#
# Two cheap reductions, neither of which is visible at map zoom levels:
#
# 1. COORDINATE PRECISION. Raw OSM coordinates carry 13+ decimal places
#    (11.876843210987...). Five decimals is about 1.1 metres -- far finer than
#    the underlying survey accuracy. Each number shrinks from ~17 characters to
#    ~8, which roughly halves the file on its own.
#
# 2. GEOMETRY SIMPLIFICATION. Douglas-Peucker removes points that sit
#    essentially on a straight line between their neighbours. At a ~2 metre
#    tolerance a cycle path keeps its shape but sheds redundant vertices.
#
# Set SIMPLIFY_TOLERANCE_DEG to 0 to disable simplification entirely.
MAP_COORD_PRECISION = 5           # decimal places; 5 ~= 1.1m
MAP_SIMPLIFY_TOLERANCE_DEG = 0.00002   # ~2m at this latitude

# ---------------------------------------------------------------------------
# OUTPUT PATHS
# ---------------------------------------------------------------------------
# All resolved from paths.py, which anchors them to the repository root --
# so the pipeline behaves identically regardless of your working directory.
OUTPUT_DIR = paths.OUTPUT_DIR
SPREADSHEET_PATH = OUTPUT_DIR / "food_desert_towns.xlsx"
GEOJSON_TOWNS_PATH = OUTPUT_DIR / "towns.geojson"
GEOJSON_ROUTES_PATH = OUTPUT_DIR / "routes.geojson"
GEOJSON_CYCLING_PATH = OUTPUT_DIR / "cycling_lanes.geojson"
GEOJSON_TRANSIT_PATH = OUTPUT_DIR / "public_transport.geojson"

# The four GeoJSON files the published web map loads. `run.py publish` copies
# them here from OUTPUT_DIR as an explicit, deliberate step -- so an
# experimental run can never silently become your live demo.
PUBLISHED_DATA_DIR = paths.DOCS_DATA_DIR
PUBLISHABLE_FILES = [
    GEOJSON_TOWNS_PATH,
    GEOJSON_ROUTES_PATH,
    GEOJSON_CYCLING_PATH,
    GEOJSON_TRANSIT_PATH,
]

# Towns for which OSRM could find no walking route to any candidate shop.
# Written out for manual inspection rather than dropped.
UNROUTABLE_REPORT_PATH = OUTPUT_DIR / "unroutable_towns.json"

# ---------------------------------------------------------------------------
# ROUTING (OSRM, self-hosted -- see README)
# ---------------------------------------------------------------------------
# How many nearby supermarkets to route to before choosing the closest.
#
# WHY THIS IS NOT 1. The obvious approach -- find the straight-line nearest
# shop, route to it, report that distance -- is wrong wherever geography gets
# in the way. Papozze, on the Po delta, has an unnamed shop 2.8km away in a
# straight line, but it sits on the far bank with no bridge nearby: the real
# walking route is 28.7km. A Coop 7.1km away straight-line is reachable in
# roughly 8km on foot. Routing only to the straight-line nearest reported
# Papozze as a 28.7km food desert when it is an 8km one.
#
# The same failure occurs anywhere a river, lake, motorway or ridge separates
# a town from a shop that looks close on a map. Routing to several candidates
# and keeping the shortest ACTUAL walk removes it.
#
# Cost is roughly linear in this number, but OSRM is local and answers in
# milliseconds, so 5 candidates is cheap insurance.
ROUTING_CANDIDATE_COUNT = 5

# Radius searched for those candidates. Generous on purpose: a town whose
# nearest shops are all across a river needs candidates well beyond the
# straight-line nearest to find a reachable one.
ROUTING_CANDIDATE_SEARCH_M = 25_000

OSRM_BASE_URL = "http://localhost:5000"
OSRM_PROFILE_WALK = "foot"
OSRM_TIMEOUT_SEC = 15

# NOTE: there is deliberately NO pause between OSRM requests. OSRM runs in a
# local Docker container -- there is no third-party server to be polite to,
# and a 2-second courtesy delay across ~1,000 towns was costing over half an
# hour per run for no benefit whatsoever.

# ---------------------------------------------------------------------------
# NOMINATIM / OVERPASS
# ---------------------------------------------------------------------------
# Nominatim's usage policy DOES require max 1 request/second. This applies to
# boundary-polygon geocoding in fetch_towns.py.
#
# SCALE HOOK (national): this rate limit is the wall for a national run.
# ~7,900 towns at 1 request/second is over two hours of pure waiting, and
# Nominatim's policy discourages bulk use at that volume regardless.
# Required to activate: source boundary polygons from the local .osm.pbf
# instead (osmium can assemble admin_level=8 relations into areas via
# MultipolygonCollector), or self-host a Nominatim instance. The former also
# removes the wrong-entity matching that OWN_SUPERMARKET_CENTER_RADIUS_M
# currently exists to paper over.
NOMINATIM_PAUSE_SEC = 1.0

# Overpass is used only by diagnostics.py, never by the analysis itself.
# Public mirrors go through real periods of instability, so query_with_retry()
# rotates through this list on each retry rather than hammering one broken
# mirror.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_TIMEOUT = 180
