"""
config.py
---------
Central configuration for the Food Desert Analysis pipeline.
Change TARGET_AREA to scale from a single province up to a whole region or country.
"""

import socket
import urllib.request

# ---------------------------------------------------------------------------
# CRITICAL FIX: prevent indefinite hangs on unresponsive mirrors.
# Our retry logic only catches mirrors that respond with an ERROR -- but a
# mirror that accepts the connection and then simply never responds at all
# (no error, no disconnect, just silence) causes Python's default networking
# behavior to wait indefinitely, potentially for hours, with zero visible
# progress and no exception our retry logic can catch. This sets a hard
# global socket timeout so ANY stalled network call anywhere in this
# project raises socket.timeout after 60 seconds instead of hanging
# forever -- query_with_retry's broad exception handler then catches it
# and moves on to the next mirror, same as any other failure.
# ---------------------------------------------------------------------------
socket.setdefaulttimeout(60)

# ---------------------------------------------------------------------------
# OVERPASS API FIX: public Overpass servers reject requests with generic/
# missing User-Agent headers (HTTP 406). This installs a global opener with
# a proper identifying User-Agent so every overpy query in this project
# works correctly without needing to touch each file individually.
# ---------------------------------------------------------------------------
_opener = urllib.request.build_opener()
_opener.addheaders = [('User-Agent', 'FoodDesertAnalysisTool/1.0 (personal research project)')]
urllib.request.install_opener(_opener)

# ---------------------------------------------------------------------------
# AREA OF INTEREST
# ---------------------------------------------------------------------------
# "province" | "region" | "country" | "multi_region"
TARGET_LEVEL = "multi_region"

# Used when TARGET_LEVEL is "province" or "region" (single named area).
TARGET_NAME = "Padova"

# Used when TARGET_LEVEL is "multi_region" -- a list of region names,
# combined together in the Overpass query. Official OSM names for these
# three (note the hyphens):
TARGET_REGIONS = ["Veneto", "Friuli-Venezia Giulia", "Trentino-Alto Adige"]

COUNTRY_ISO = "IT"

# Buffer (in km) added around the target area boundary when searching for
# supermarkets. This is what solves the "closest supermarket is in the next
# province over" edge case -- we never clip supermarket search to the
# administrative boundary of the town/province being analyzed.
BORDER_BUFFER_KM = 12

# ---------------------------------------------------------------------------
# CACHING (important once you're processing 1000+ comuni)
# ---------------------------------------------------------------------------
# Fetching comuni boundaries takes one Nominatim call PER comune -- across
# three full regions that's over 1000 network calls, likely well over an
# hour. If the pipeline crashes partway through a LATER step (supermarkets,
# routing, etc.), you do not want to repeat all of that. These cache files
# let a re-run skip straight past anything already fetched successfully.
COMUNI_CACHE_PATH = "./data/comuni_cache.gpkg"
SUPERMARKETS_CACHE_PATH = "./data/supermarkets_cache.gpkg"
FORCE_REFRESH_CACHE = False  # set True to ignore caches and re-fetch everything

# ---------------------------------------------------------------------------
# MAP BACKGROUND LAYER BOUNDING BOX
# ---------------------------------------------------------------------------
# Used only by fetch_map_layers.py to fetch cycling-lane and public-transport
# overlays for the web map. A fixed, generous box for Padua province --
# doesn't need to be pixel-perfect, just needs to comfortably cover the area.
# Format: (south, west, north, east). Widen this if you scale to Veneto/Italy.
MAP_BBOX = (44.95, 11.25, 45.65, 12.15)

# ---------------------------------------------------------------------------
# CRITERIA THRESHOLDS
# ---------------------------------------------------------------------------
DISTANCE_THRESHOLD_KM = 3.0      # town center -> nearest supermarket, routed distance
DISTANCE_THRESHOLD_KM = 3.0      # town center -> nearest supermarket, routed distance
OWN_SUPERMARKET_BUFFER_M = 500   # if a supermarket is within this radius of the town's
                                  # admin boundary, we treat the town as "having one"

# Any result at or beyond this distance gets flagged in the spreadsheet as
# "worth a manual look" -- NOT automatically discarded. Remote mountain
# comuni (Belluno, Trentino, Alto Adige) can genuinely be this far from a
# supermarket by real road distance, and that's exactly the kind of case
# this project exists to surface -- so flagging, not deleting, is the
# right call here.
DISTANCE_REVIEW_THRESHOLD_KM = 8.0
OWN_SUPERMARKET_BUFFER_M = 500   # if a supermarket is within this radius of the town's
                                  # admin boundary, we treat the town as "having one"

# OSM shop tags that count as "supermarket or minimarket"
SUPERMARKET_TAGS = {
    "shop": ["supermarket", "convenience"]
}

# ---------------------------------------------------------------------------
# TESTING TOGGLE
# ---------------------------------------------------------------------------
# Set to False once you have a bike-profile OSRM server running.
# While True, the pipeline stops after the distance check (criteria 1 & 2)
# and skips the cycling-infrastructure check (criterion 3), so you can
# validate the pipeline end-to-end using only the foot-profile OSRM server.
SKIP_INFRASTRUCTURE_CHECK = True

# ---------------------------------------------------------------------------
# ROUTING
# ---------------------------------------------------------------------------
# Self-hosted OSRM is required at this scale -- the public demo server
# rate-limits aggressively and is NOT meant for batch/production use.
OSRM_BASE_URL = "http://localhost:5000"   # point this at your own OSRM instance
OSRM_PROFILE_WALK = "foot"                # matches "elderly resident on foot" use case
OSRM_PROFILE_BIKE = "bike"                # used for the cycling-lane check route (Phase 2)

# ---------------------------------------------------------------------------
# ISTAT POPULATION DATA
# ---------------------------------------------------------------------------
# Download the "Popolazione residente comunale" CSV from:
# https://www.istat.it/it/archivio/156224
# Expected columns (this ISTAT export): PROVINCIA, CODICE COMUNE,
# DENOMINAZIONE COMUNE, Popolazione Totale
#
# For multi-region runs, provide ONE FILE PER REGION here (each with its
# own PROVINCIA column) rather than a single whole-of-Italy file -- the
# national download does not include a province column, which we need
# for the spreadsheet/map output. The loader below reads and combines
# every file in this list automatically.
ISTAT_POPULATION_CSVS = [
    "./data/comuni veneto.csv",
    "./data/comuni friuli.csv",
    "./data/comuni trentino.csv",
]

# ---------------------------------------------------------------------------
# OUTPUT PATHS
# ---------------------------------------------------------------------------
OUTPUT_DIR = "./output"
SPREADSHEET_PATH = f"{OUTPUT_DIR}/food_desert_towns.xlsx"
GEOJSON_TOWNS_PATH = f"{OUTPUT_DIR}/towns.geojson"
GEOJSON_ROUTES_PATH = f"{OUTPUT_DIR}/routes.geojson"
GEOJSON_CYCLING_PATH = f"{OUTPUT_DIR}/cycling_lanes.geojson"
GEOJSON_TRANSIT_PATH = f"{OUTPUT_DIR}/public_transport.geojson"

# ---------------------------------------------------------------------------
# NETWORK / RATE LIMITING
# ---------------------------------------------------------------------------
# Public Overpass mirrors periodically have real outages/instability (not
# just heavy load) -- 500s, 404s, dropped connections. Rather than betting
# on one server, query_with_retry() rotates through this list on each
# retry attempt, so a temporarily broken mirror gets skipped automatically.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_TIMEOUT = 180
REQUEST_PAUSE_SEC = 2.0   # be polite between requests