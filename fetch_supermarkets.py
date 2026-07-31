"""
fetch_supermarkets.py
----------------------
Fetches all supermarket/minimarket POIs within the target area PLUS a buffer
zone extending into neighboring provinces/regions.

This is where the "cross-border nearest supermarket" edge case gets solved:
we deliberately do NOT clip this query to the exact admin boundary. We
expand the search bounding box by config.BORDER_BUFFER_KM in every direction.

At multi-region scale, a single query across the whole bounding box is too
heavy and risks timing out -- so this splits the area into a grid of
smaller cells, queries each one separately, and merges + deduplicates
the results.

IMPORTANT LESSON LEARNED: an earlier version of this used 0.4-degree
(~40km) grid cells. When a cell failed after all retries, it was simply
skipped and never retried -- silently wiping out ALL supermarkets in a
huge area (confirmed via diagnose_supermarkets.py: Longarone was missing
12 of 13 real nearby supermarkets because its grid cell had failed this
way). This version uses smaller cells (much smaller blast radius per
failure) AND does a final retry pass over any cells that failed the
first time, with a clear report of anything that still couldn't be
fetched even after that.

Includes disk caching: once fetched successfully, results are saved to
config.SUPERMARKETS_CACHE_PATH so a later pipeline crash (e.g. during
routing) doesn't require re-fetching thousands of supermarkets again.
"""

import math
import os
import geopandas as gpd
from shapely.geometry import Point

import config
from overpass_utils import query_with_retry

KM_TO_DEG_LAT = 1 / 111.0
GRID_CELL_DEG = 0.15  # ~15km per cell -- much smaller blast radius if a
                       # cell fails than the previous 0.4 (~40km) setting

# Checkpointing: this fetch can take hours across three regions. If the
# terminal closes, the machine sleeps, or anything else interrupts it
# partway through, these files let a re-run resume from where it left
# off instead of starting over from zero.
CHECKPOINT_RECORDS_PATH = "./data/supermarkets_checkpoint.gpkg"
CHECKPOINT_PROGRESS_PATH = "./data/supermarkets_checkpoint_progress.txt"
CHECKPOINT_SAVE_EVERY = 20  # save progress every N cells


def _buffered_bbox(gdf, buffer_km):
    """Returns (south, west, north, east) bbox expanded by buffer_km."""
    minx, miny, maxx, maxy = gdf.total_bounds
    lat_mid = (miny + maxy) / 2
    km_to_deg_lon = 1 / (111.320 * math.cos(math.radians(lat_mid)))

    buf_lat = buffer_km * KM_TO_DEG_LAT
    buf_lon = buffer_km * km_to_deg_lon

    return (miny - buf_lat, minx - buf_lon, maxy + buf_lat, maxx + buf_lon)


def _split_into_grid(bbox, cell_deg=GRID_CELL_DEG):
    """Splits a (south, west, north, east) bbox into a grid of smaller cells."""
    south, west, north, east = bbox
    cells = []
    lat = south
    while lat < north:
        lat_end = min(lat + cell_deg, north)
        lon = west
        while lon < east:
            lon_end = min(lon + cell_deg, east)
            cells.append((lat, lon, lat_end, lon_end))
            lon = lon_end
        lat = lat_end
    return cells


def _build_query(cell, shop_filter):
    south, west, north, east = cell
    return f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      node["shop"~"{shop_filter}"]({south},{west},{north},{east});
      way["shop"~"{shop_filter}"]({south},{west},{north},{east});
    );
    out center;
    """


def _extract_records(result, seen_ids, records, geoms):
    """Extracts supermarket records from an Overpass result, deduplicating by osm_id."""
    added = 0
    for node in result.nodes:
        key = ("node", node.id)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        records.append({
            "name": node.tags.get("name", "Unnamed supermarket"),
            "shop_type": node.tags.get("shop"),
            "osm_id": node.id,
            "osm_type": "node",
        })
        geoms.append(Point(float(node.lon), float(node.lat)))
        added += 1

    for way in result.ways:
        if way.center_lon is None or way.center_lat is None:
            continue
        key = ("way", way.id)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        records.append({
            "name": way.tags.get("name", "Unnamed supermarket"),
            "shop_type": way.tags.get("shop"),
            "osm_id": way.id,
            "osm_type": "way",
        })
        geoms.append(Point(float(way.center_lon), float(way.center_lat)))
        added += 1

    return added


def _save_checkpoint(records, geoms, cells_done, total_cells):
    """Saves current progress to disk so a re-run can resume from here."""
    if records:
        checkpoint_gdf = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
        os.makedirs(os.path.dirname(CHECKPOINT_RECORDS_PATH), exist_ok=True)
        checkpoint_gdf.to_file(CHECKPOINT_RECORDS_PATH, driver="GPKG")
    # Store total_cells alongside cells_done -- this is what lets us detect
    # a stale checkpoint from a DIFFERENT grid configuration (e.g. if
    # GRID_CELL_DEG or the target area changed between runs) and reject it
    # instead of silently resuming against the wrong cell boundaries.
    with open(CHECKPOINT_PROGRESS_PATH, "w") as f:
        f.write(f"{cells_done},{total_cells}")


def _load_checkpoint(expected_total_cells):
    """
    Loads a previous checkpoint if one exists AND matches the current
    grid configuration. Returns (cells_already_done, records, geoms,
    seen_ids) -- all empty/zero if no valid checkpoint is found.

    CRITICAL: a checkpoint built under a different total cell count (e.g.
    from an earlier run with a different GRID_CELL_DEG or a different
    target area) is REJECTED rather than resumed from -- resuming by
    "skip the first N cells" only makes sense if cell #i means the same
    geographic area in both runs. Using a mismatched checkpoint would
    silently skip the wrong cells and blend in data from unrelated
    areas, corrupting the result with no visible error (this happened
    once already this session).
    """
    if not (os.path.exists(CHECKPOINT_PROGRESS_PATH) and os.path.exists(CHECKPOINT_RECORDS_PATH)):
        return 0, [], [], set()

    with open(CHECKPOINT_PROGRESS_PATH) as f:
        contents = f.read().strip()

    parts = contents.split(",")
    if len(parts) != 2:
        print("[WARN] Checkpoint file format not recognized (from an older "
              "script version) -- ignoring it and starting fresh.")
        return 0, [], [], set()

    cells_done, saved_total_cells = int(parts[0]), int(parts[1])
    if saved_total_cells != expected_total_cells:
        print(f"[WARN] Found a checkpoint, but it was built for a DIFFERENT "
              f"grid ({saved_total_cells} total cells vs {expected_total_cells} "
              f"expected now) -- likely left over from an earlier run with "
              f"different settings. Ignoring it and starting completely fresh "
              f"to avoid silently corrupting results.")
        return 0, [], [], set()

    checkpoint_gdf = gpd.read_file(CHECKPOINT_RECORDS_PATH)
    records = []
    geoms = []
    seen_ids = set()
    for _, row in checkpoint_gdf.iterrows():
        seen_ids.add((row["osm_type"], row["osm_id"]))
        records.append({
            "name": row["name"],
            "shop_type": row["shop_type"],
            "osm_id": row["osm_id"],
            "osm_type": row["osm_type"],
        })
        geoms.append(row.geometry)

    return cells_done, records, geoms, seen_ids


def _clear_checkpoint():
    """Removes checkpoint files once the fetch has completed successfully."""
    for path in (CHECKPOINT_RECORDS_PATH, CHECKPOINT_PROGRESS_PATH):
        if os.path.exists(path):
            os.remove(path)


def fetch_supermarkets(comuni_gdf):
    """
    Returns a GeoDataFrame of supermarkets/minimarkets with columns:
        name, shop_type, geometry (Point)
    Covers the target area + BORDER_BUFFER_KM buffer, fetched in a grid
    of smaller queries to stay within mirror timeout limits, with a
    retry pass for any cells that failed the first time around.
    Automatically resumes from a checkpoint if a previous run was
    interrupted partway through.
    """
    if not config.FORCE_REFRESH_CACHE and os.path.exists(config.SUPERMARKETS_CACHE_PATH):
        print(f"[INFO] Loading supermarkets from cache: {config.SUPERMARKETS_CACHE_PATH}")
        return gpd.read_file(config.SUPERMARKETS_CACHE_PATH)

    bbox = _buffered_bbox(comuni_gdf, config.BORDER_BUFFER_KM)
    cells = _split_into_grid(bbox)

    cells_done, records, geoms, seen_ids = _load_checkpoint(len(cells))
    if cells_done > 0:
        print(f"[INFO] Resuming from checkpoint: {cells_done}/{len(cells)} cells "
              f"already done, {len(records)} supermarkets already fetched.")
    else:
        print(f"[INFO] Fetching supermarkets across {len(cells)} grid cells "
              f"(~{GRID_CELL_DEG*111:.0f}km per cell, target area + "
              f"{config.BORDER_BUFFER_KM}km border buffer)...")

    shop_filter = "|".join(config.SUPERMARKET_TAGS["shop"])
    failed_cells = []

    for i, cell in enumerate(cells, start=1):
        if i <= cells_done:
            continue  # already fetched in a previous run, skip
        try:
            result = query_with_retry(_build_query(cell, shop_filter))
            _extract_records(result, seen_ids, records, geoms)
        except Exception as e:
            failed_cells.append(cell)
            print(f"[WARN] Grid cell {i}/{len(cells)} failed after all retries "
                  f"(will retry once more at the end): {e}")

        if i % CHECKPOINT_SAVE_EVERY == 0 or i == len(cells):
            _save_checkpoint(records, geoms, i, len(cells))
            print(f"[INFO] Grid progress: {i}/{len(cells)} cells done, "
                  f"{len(records)} unique supermarkets so far. "
                  f"(checkpoint saved -- safe to interrupt now if needed)")

    # --- Retry pass: give every failed cell one more full attempt, with a
    # fresh mirror rotation, before accepting any gaps as final. ---
    still_failed = []
    if failed_cells:
        print(f"\n[INFO] Retrying {len(failed_cells)} previously-failed grid cell(s)...")
        for i, cell in enumerate(failed_cells, start=1):
            try:
                result = query_with_retry(_build_query(cell, shop_filter))
                added = _extract_records(result, seen_ids, records, geoms)
                print(f"[INFO] Retry {i}/{len(failed_cells)} succeeded, "
                      f"{added} new supermarket(s) recovered.")
            except Exception as e:
                still_failed.append(cell)
                print(f"[WARN] Retry {i}/{len(failed_cells)} failed again: {e}")

    if still_failed:
        print(f"\n[WARNING] {len(still_failed)} grid cell(s) could NOT be fetched "
              f"even after a retry pass. Supermarket data in these areas may be "
              f"INCOMPLETE, which could cause false positives (towns flagged as "
              f"underserved when a supermarket actually exists nearby):")
        for cell in still_failed:
            south, west, north, east = cell
            print(f"   - bbox: south={south:.3f}, west={west:.3f}, "
                  f"north={north:.3f}, east={east:.3f}")
        print("Consider re-running this script later (public mirrors recover "
              "over time), or manually checking these areas on OpenStreetMap.")
    else:
        print(f"\n[INFO] All grid cells fetched successfully (including retries) "
              f"-- no known data gaps.")

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
    print(f"\n[INFO] Fetched {len(gdf)} unique supermarkets/minimarkets total.")

    os.makedirs(os.path.dirname(config.SUPERMARKETS_CACHE_PATH), exist_ok=True)
    gdf.to_file(config.SUPERMARKETS_CACHE_PATH, driver="GPKG")
    print(f"[INFO] Cached supermarkets to {config.SUPERMARKETS_CACHE_PATH}")

    _clear_checkpoint()  # no longer needed now that the final cache is saved

    return gdf


if __name__ == "__main__":
    comuni = gpd.read_file(config.COMUNI_CACHE_PATH)
    supermarkets = fetch_supermarkets(comuni)