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
# "province" | "region" | "multi_region" | "country"
TARGET_LEVEL = "multi_region"
TARGET_NAME = "Padova"          # only used when TARGET_LEVEL == "province"
TARGET_REGIONS = ["Veneto", "Friuli-Venezia Giulia", "Trentino-Alto Adige"]
COUNTRY_ISO = "IT"

# Buffer (in km) added around the target area boundary when searching for
# supermarkets. In case the supermarket is across the border.
BORDER_BUFFER_KM = 12

# ---------------------------------------------------------------------------
# LOCAL OSM FILE (used for supermarkets, comuni admin-center lookups, and cycling lanes)
# ---------------------------------------------------------------------------
OSM_PBF_PATH = "./nord-est-latest.osm.pbf"

# ---------------------------------------------------------------------------
# CRITERIA THRESHOLDS
# ---------------------------------------------------------------------------
DISTANCE_THRESHOLD_KM = 3.0      # routed distance between town center to the nearest supermarket
OWN_SUPERMARKET_BUFFER_M = 500   # if a supermarket is within this radius of the town's admin boundary, we treat the town as "having one"

# Any result at or beyond this distance gets flagged in the spreadsheet as "worth a manual look". 
# Remote mountain towns can really be this far from a  supermarket by real road distance.
DISTANCE_REVIEW_THRESHOLD_KM = 10.0

# OSM shop tags that count as "supermarket or minimarket"
SUPERMARKET_TAGS = {
    "shop": ["supermarket", "convenience"]
}

# ---------------------------------------------------------------------------
# CACHING (important for processing 1000+ towns)
# ---------------------------------------------------------------------------
COMUNI_CACHE_PATH = "./data/comuni_cache.gpkg"
SUPERMARKETS_CACHE_PATH = "./data/supermarkets_cache.gpkg"
FORCE_REFRESH_CACHE = False  # set True to ignore caches and re-fetch everything

# ---------------------------------------------------------------------------
# ISTAT POPULATION DATA
# ---------------------------------------------------------------------------
# Prepare the "Popolazione residente comunale" CSV from: https://www.istat.it/comunicato-territoriale/censimento-della-popolazione-dati-regionali-anno-2024/
# One file per region is fine -- all listed files get combined together before matching.
# Expected columns per file Provincia, Codice Comune, Denominazione/Nome, Comune, Popolazione Totale.
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
# ROUTING (OSRM  used for walking-distance calculations)
# ---------------------------------------------------------------------------
OSRM_BASE_URL = "http://localhost:5000"   # point this at your own OSRM instance
OSRM_PROFILE_WALK = "foot"               

# ---------------------------------------------------------------------------
# NETWORK / RATE LIMITING
# ---------------------------------------------------------------------------
# Still used by fetch_map_layers.py's public transport fetching, and as a fallback anywhere Overpass is still involved. 
# Public Overpass mirrors go through  outages/instability, 
# query_with_retry() rotates through this list on each retry attempt, so a
# temporarily broken mirror gets skipped in favor of a working one.
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_TIMEOUT = 180
REQUEST_PAUSE_SEC = 2.0   # be polite between requests