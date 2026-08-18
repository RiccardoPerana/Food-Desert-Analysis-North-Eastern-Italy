# Installation & File Placement

Everything in this bundle drops into your project folder as-is. This file
covers the pieces that are **not** in the bundle and where they need to go.

---

## 1. What's included

```
food-desert-analysis-project-main/
├── run.py                       ← the only script you invoke
├── requirements.txt
├── LICENSE
├── .gitignore  ·  .gitattributes
├── import_existing_data.ps1     ← optional, see step 3
├── INSTALL.md                   ← this file
│
├── food_desert/                 ← the package
│   ├── __init__.py  ·  paths.py  ·  config.py
│   ├── geo_utils.py  ·  routing.py  ·  overpass_utils.py
│   ├── fetch_comuni.py  ·  fetch_supermarkets.py  ·  fetch_map_layers.py
│   ├── population_istat.py  ·  export_spreadsheet.py
│   ├── pipeline.py  ·  diagnostics.py
│
├── tools/
│   └── check_config_refs.py     ← static check, run before committing
│
├── data/                        ← inputs (see step 2)
│   ├── istat/    committed
│   ├── osm/      gitignored
│   └── cache/    gitignored
│
├── output/                      ← working results, gitignored
└── docs/                        ← GitHub Pages serves this folder
    └── data/                    ← published GeoJSON, committed
```

---

## 2. What you need to add

| File | Goes in | Notes |
|---|---|---|
| `index.html` | **`docs/`** | NOT `data/`. See the warning below. |
| `nord-est-latest.osm.pbf` | `data/osm/` | [Geofabrik download](https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf) |
| `comuni veneto.csv` | `data/istat/` | Exact filename matters |
| `comuni friuli.csv` | `data/istat/` | Exact filename matters |
| `comuni trentino.csv` | `data/istat/` | Exact filename matters |
| `*.gpkg` caches | `data/cache/` | Optional but saves ~30 min |

### ⚠️ Your index.html must move out of `data/`

In this structure `data/` holds pipeline **inputs** and is gitignored.
Anything web-facing placed there will never reach GitHub Pages. The web map
belongs at `docs/index.html`.

You also need to update its data paths. It currently fetches from
`../output/` or similar; on GitHub Pages it must be a plain relative path:

```javascript
// Change every fetch to look like this:
fetch('data/towns.geojson')
fetch('data/routes.geojson')
fetch('data/cycling_lanes.geojson')
fetch('data/public_transport.geojson')
```

That single form works identically for local preview (`python run.py serve`)
and for the published site, because both serve `docs/` as the web root.

---

## 3. Bringing across your existing data

If you still have the old project folder, this moves the large files over so
nothing is re-downloaded or re-computed:

```powershell
powershell -ExecutionPolicy Bypass -File .\import_existing_data.ps1 `
    -From "C:\Users\ricca\Downloads\Food-Desert-Analysis-North-Eastern-Italy-main"
```

Add `-Copy` to leave the old folder untouched (needs the disk space twice).

It does **not** move `index.html` — do that by hand, per the warning above.

---

## 4. Setup

```powershell
cd C:\Users\ricca\Downloads\food-desert-analysis-project-main

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 5. Verify before running anything expensive

```powershell
python run.py paths                  # every input should read OK
python tools\check_config_refs.py    # should report 32 settings
```

`run.py paths` prints the resolved layout and marks each input `OK` or
`MISSING`. If something reads MISSING, fix it now — a missing cache file is
indistinguishable from "no cache yet", so the pipeline would quietly begin a
45-minute rebuild rather than telling you something is wrong.

Then confirm the working directory genuinely no longer matters:

```powershell
cd ..
python .\food-desert-analysis-project-main\run.py paths
```

Same output from a different directory means the path anchoring works.

---

## 6. Start the routing server

OSRM runs in Docker, in its own terminal tab, and must stay running while the
analysis executes. Note the path — the extract now lives in `data/osm/`:

```powershell
docker run -t -v "${PWD}/data/osm:/data" osrm/osrm-backend `
    osrm-extract -p /opt/foot.lua /data/nord-est-latest.osm.pbf

docker run -t -v "${PWD}/data/osm:/data" osrm/osrm-backend `
    osrm-partition /data/nord-est-latest.osrm

docker run -t -v "${PWD}/data/osm:/data" osrm/osrm-backend `
    osrm-customize /data/nord-est-latest.osrm

docker run -t -i -p 5000:5000 -v "${PWD}/data/osm:/data" osrm/osrm-backend `
    osrm-routed --algorithm mld /data/nord-est-latest.osrm
```

If you imported existing `.osrm*` files, skip straight to the last command.

---

## 7. Run it

```powershell
python run.py analyze     # the analysis -> output/
python run.py layers      # cycling + transport overlays -> output/
python run.py publish     # copy GeoJSON -> docs/data/
python run.py serve       # preview at http://localhost:8000

python run.py all         # all three build steps in order
```

Spot-check a specific comune at any point:

```powershell
python run.py diagnose "Torri di Quartesolo"
```
