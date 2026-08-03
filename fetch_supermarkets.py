"""
Reads  from the regional OSM extract already downloaded for OSRM (nord-est-latest.osm.pbf), 
using the `osmium` library.

WHY THIS CHANGE: 
Free Public Overpass mirrors often returned technically-successful but SILENTLY INCOMPLETE responses under heavy load.
(existing supermarkets were missing from fetched data with no indication anything had gone wrong). 
Reading directly from the local .osm.pbf file removes the network/server from this step entirely.

REQUIRES: pip install osmium

KNOWN LIMITATION: this reads ONLY the Nord-Est regional extract (Veneto + Trentino-Alto Adige + Friuli-Venezia Giulia). 
Supermarkets in NEIGHBORING regions/countries are not included. 
This only affects towns right on the outer edge of the three-region area. 
To close that gap too, download neighboring regions' .osm.pbf extracts and add them to OSM_PBF_PATHS.
"""

import os
import osmium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

import config

OSM_PBF_PATHS = [config.OSM_PBF_PATH]
SHOP_TAGS = set(config.SUPERMARKET_TAGS["shop"])


class _SupermarketHandler(osmium.SimpleHandler):
    """
    Collects every node/way tagged shop=supermarket or shop=convenience found thorughout the local file(s).
    Ways (buildings mapped as outlines rather than single points) get their centroid computed from their resolved node coordinates
     -- `locations=True` in apply_file() is what makes way.nodes[i].location available below.
    """
    def __init__(self):
        super().__init__()
        self.records = []
        self.geoms = []

    def node(self, n):
        shop = n.tags.get("shop")
        if shop in SHOP_TAGS:
            self.records.append({
                "name": n.tags.get("name", "Unnamed supermarket"),
                "shop_type": shop,
                "osm_id": n.id,
                "osm_type": "node",
            })
            self.geoms.append(Point(n.location.lon, n.location.lat))

    def way(self, w):
        shop = w.tags.get("shop")
        if shop not in SHOP_TAGS:
            return
        coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(coords) < 1:
            return
        # Simple average of the outline's points as an approximate
        # center -- matches what Overpass's "out center;" gave us before.
        avg_lon = sum(c[0] for c in coords) / len(coords)
        avg_lat = sum(c[1] for c in coords) / len(coords)
        self.records.append({
            "name": w.tags.get("name", "Unnamed supermarket"),
            "shop_type": shop,
            "osm_id": w.id,
            "osm_type": "way",
        })
        self.geoms.append(Point(avg_lon, avg_lat))


def _extract_from_file(pbf_path):
    """Extracts supermarket/minimarket POIs from a single local .osm.pbf file."""
    if not os.path.exists(pbf_path):
        raise FileNotFoundError(
            f"Could not find {pbf_path} -- this should be the same file you "
            f"downloaded earlier for OSRM. Make sure it's in the project "
            f"root folder (same folder as pipeline.py)."
        )
    print(f"[INFO] Reading {pbf_path} (no internet/Overpass involved)...")
    handler = _SupermarketHandler()
    handler.apply_file(pbf_path, locations=True)
    print(f"[INFO] Found {len(handler.records)} supermarket/minimarket entries in {pbf_path}.")
    return gpd.GeoDataFrame(handler.records, geometry=handler.geoms, crs="EPSG:4326")


def fetch_supermarkets(comuni_gdf=None):
    """
    Extracts every supermarket/minimarket from the local .osm.pbf file(s) in OSM_PBF_PATHS.
    """
    if not config.FORCE_REFRESH_CACHE and os.path.exists(config.SUPERMARKETS_CACHE_PATH):
        print(f"[INFO] Loading supermarkets from cache: {config.SUPERMARKETS_CACHE_PATH}")
        return gpd.read_file(config.SUPERMARKETS_CACHE_PATH)

    all_gdfs = [_extract_from_file(p) for p in OSM_PBF_PATHS]
    combined = gpd.GeoDataFrame(
        pd.concat(all_gdfs, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    combined = combined.drop_duplicates(subset=["osm_id", "osm_type"])

    print(f"[INFO] Extracted {len(combined)} unique supermarkets/minimarkets total "
          f"from {len(OSM_PBF_PATHS)} local file(s) -- fully complete, no network gaps possible.")

    os.makedirs(os.path.dirname(config.SUPERMARKETS_CACHE_PATH), exist_ok=True)
    combined.to_file(config.SUPERMARKETS_CACHE_PATH, driver="GPKG")
    print(f"[INFO] Cached supermarkets to {config.SUPERMARKETS_CACHE_PATH}")

    return combined


if __name__ == "__main__":
    fetch_supermarkets()