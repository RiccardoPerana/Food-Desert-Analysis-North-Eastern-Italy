# Setup Guide

Full instructions for running this pipeline yourself, from a clean machine
to a working spreadsheet and interactive map.

This guide is written for Windows 10/11 + VS Code, since that's the
environment it was built and tested on, but the steps translate directly
to macOS/Linux (just adjust the shell commands).

---

## 1. Install Python

Download Python 3.11+ from [python.org/downloads](https://www.python.org/downloads/).
On the installer's first screen, check **"Add python.exe to PATH"** before
installing.

Verify:
```powershell
python --version
```

---

## 2. Set Up the Project

Open this folder in VS Code, open a terminal (**Terminal → New Terminal**),
then:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Verify everything installed correctly:
```powershell
python -c "import osmnx, geopandas, shapely, pandas, requests, openpyxl, polyline, overpy; print('All imports OK')"
```

> **Windows note:** if `venv\Scripts\activate` fails with an execution
> policy error, run this once:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

## 3. Get Population Data (ISTAT)

Download comune-level population data from
**[istat.it/it/archivio/156224](https://www.istat.it/it/archivio/156224)**
("Popolazione residente comunale"). You need coverage for every region
you're analyzing — either one file per region, or a single whole-of-Italy
file if ISTAT offers one.

Put the file(s) in a `data/` folder in the project root, and list their
paths in `config.py`:
```python
ISTAT_POPULATION_CSVS = [
    "./data/comuni veneto.csv",
    "./data/comuni friuli.csv",
    "./data/comuni trentino.csv",
]
```

Expected columns (minor naming variants are handled automatically):
`Provincia`, `Codice Comune`, `Denominazione Comune` (or `Nome Comune`),
`Popolazione Totale`.

> **Note on Trentino-Alto Adige specifically:** ISTAT sometimes splits this
> region's data by province (Trento vs. Bolzano/Alto Adige) into separate
> downloads — make sure both are included if you're analyzing this region,
> or you'll silently end up missing ~40% of its comuni.

---

## 4. Set Up OSRM (Routing Engine)

This is the biggest one-time setup cost, but it's a one-time cost — once
built, it just runs.

### 4a. Install Docker Desktop
Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
On Windows, this requires WSL2 — the installer will prompt you to enable
it if needed (may require a restart).

### 4b. Download the road network extract
Pick the smallest [Geofabrik](https://download.geofabrik.de/europe/italy.html)
extract that covers your target area. For Veneto + Friuli-Venezia Giulia +
Trentino-Alto Adige, that's the **Nord-Est** regional extract:
```powershell
Invoke-WebRequest -Uri "https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf" -OutFile "nord-est-latest.osm.pbf"
```

### 4c. Build the routing data (one-time)
```powershell
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/nord-est-latest.osm.pbf
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/nord-est-latest.osrm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/nord-est-latest.osrm
```

### 4d. Start the routing server
Run this in its own terminal tab and **leave it running** for the rest of
your session — it's a live server, not a one-shot command:
```powershell
docker run -t -i -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/nord-est-latest.osrm
```
Wait for `running and waiting for requests` before moving on.

---

## 5. Configure Your Target Area

In `config.py`:

```python
# Single province
TARGET_LEVEL = "province"
TARGET_NAME = "Padova"

# Single region
TARGET_LEVEL = "region"
TARGET_NAME = "Veneto"

# Multiple regions at once
TARGET_LEVEL = "multi_region"
TARGET_REGIONS = ["Veneto", "Friuli-Venezia Giulia", "Trentino-Alto Adige"]
```

Other settings worth knowing about:
- `BORDER_BUFFER_KM` — how far to search past the target area's edge for
  supermarkets (handles the "nearest supermarket is in the next province"
  case). Default 12km.
- `DISTANCE_THRESHOLD_KM` — the walking-distance cutoff for "underserved."
  Default 3km.
- `DISTANCE_REVIEW_THRESHOLD_KM` — results at/beyond this distance get
  flagged in the spreadsheet as worth a manual sanity check, without being
  discarded. Default 8km.
- `SKIP_INFRASTRUCTURE_CHECK` — set `True` to test the pipeline using only
  the walking-route OSRM server (skips the sidewalk/cycling-lane check).
  Set `False` once you've also built a bike-profile OSRM server (repeat
  step 4c/4d with `/opt/bicycle.lua` on a different port).

---

## 6. Run the Pipeline

In a **separate** terminal from your OSRM server:

```powershell
venv\Scripts\activate
python pipeline.py
```

### What to expect timing-wise
- **First run, small area (single province):** a few minutes.
- **First run, multiple regions:** the comune boundary-fetching step
  (one lookup per comune via Nominatim) can take **1-2+ hours** for
  1,000+ comuni. This only happens once — results are cached.
- **Supermarket fetching:** also long on a first run across a large area
  (queries are split into a grid to stay within server timeouts). This
  step **checkpoints its progress** — if interrupted, just re-run
  `python fetch_supermarkets.py` and it resumes automatically instead of
  starting over.
- **Subsequent runs:** fast — comuni and supermarket data are both cached
  to disk (`data/comuni_cache.gpkg`, `data/supermarkets_cache.gpkg`).

Set `config.FORCE_REFRESH_CACHE = True` if you ever need to force a
complete re-fetch (e.g. after changing `TARGET_REGIONS`).

---

## 7. Generate Map Overlay Layers

```powershell
python fetch_map_layers.py
```
Fetches cycling lanes and public transport routes for the map's toggle
layers. Also long-running on first use across a large area; not cached
the same way as comuni/supermarkets, so re-run this if you change your
target area.

---

## 8. View the Interactive Map

Serve from the **project root** (not the `map` folder):
```powershell
python -m http.server 8000
```
Then open your browser to:
```
http://localhost:8000/map/
```

---

## 9. Optional: Verify Data Quality

`diagnose_supermarkets.py` compares your cached supermarket data against
a fresh live query for any town you specify — useful for spot-checking if
a specific result looks suspicious:
```powershell
python diagnose_supermarkets.py
```
Edit the `TOWN_NAMES` list at the top of the file to check different towns.

---

## Known Limitations

- **Public transport on the map reflects OSM's own data completeness** —
  strong for urban routes, inconsistent for regional/inter-urban coach
  lines. The map's toggle button is labeled accordingly.
- **Sidewalk/cycling-lane detection is tag-based** (OSM `sidewalk=*`,
  `cycleway=*`), so completeness depends on how thoroughly a given road
  has been mapped in your target area.
- **Public Overpass mirrors are shared, rate-limited infrastructure** —
  expect occasional slowdowns or retries during large-area fetches; the
  pipeline automatically rotates across multiple mirrors and retries
  transient failures, but very large areas (multi-region) are inherently
  slow on a first run.
- A small number of comuni may show `province: Unknown` or a
  polygon-centroid fallback for their center point if OSM/ISTAT naming
  doesn't match cleanly (bilingual names, historical mergers) — these are
  logged clearly in the console (`[WARN]`, `[FALLBACK]`) rather than
  silently dropped.