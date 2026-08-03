# Food Desert Analysis Pipeline — Padua Province (scalable to Veneto / Italy)

Finds comuni (municipalities) that:
1. Have no supermarket/minimarket of their own
2. Have their name-label point (city center) more than 3km — by real
   routed walking distance — from the nearest supermarket
3. (Phase 2) Have no sidewalk or cycling lane along that specific route

Outputs: an Excel spreadsheet + an interactive Leaflet web map.

---

## Where You Are (fresh restart)

You already have, from before:
- ✅ Python 3.14 + venv working
- ✅ All pip dependencies installed
- ✅ `data/istat_population_comuni.csv` in place
- ✅ Docker Desktop + WSL2 working
- ✅ `nord-est-latest.osm.pbf` downloaded and processed (extract, partition,
  customize all completed successfully) — this file should still be sitting
  in your project folder, no need to redo those steps.

This zip replaces every `.py` file with a clean, consistent version. **Copy
these over your existing files entirely** (don't hand-merge with old
versions) to avoid the mismatched-edits problem from before.

---

## 1. Re-confirm your environment still works

Open VS Code in `food_desert_pipeline`, open a terminal:

```powershell
venv\Scripts\activate
python -c "import osmnx, geopandas, shapely, pandas, requests, openpyxl, polyline, overpy; print('All imports OK')"
```

---

## 2. Start the OSRM routing server

You already built the Nord-Est OSRM data — you do NOT need to repeat the
`osrm-extract` / `osrm-partition` / `osrm-customize` steps. Just start the
server, in its own dedicated terminal tab (Terminal → New Terminal):

```powershell
docker run -t -i -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/nord-est-latest.osrm
```

Wait for `running and waiting for requests`, then **leave this tab alone**
for the rest of your session — it's a live server, not a one-shot command.

---

## 3. Run the pipeline (in a SEPARATE terminal tab)

```powershell
venv\Scripts\activate
python pipeline.py
```

`config.py` currently has `SKIP_INFRASTRUCTURE_CHECK = True` — this means
criterion 3 (sidewalk/cycling check) is intentionally skipped for now, so
you can validate the full pipeline end-to-end using only the walking-route
OSRM server you already built. Results will be labeled
`"NOT CHECKED (testing mode)"` in that column so this is never mistaken for
final data.

This run should now take a few minutes, not hours — the comuni/label-point
fetching was rebuilt to use only a handful of small, targeted Overpass
queries instead of ~200 heavy ones, and the retry logic now automatically
rotates across multiple public Overpass mirrors (`overpass.kumi.systems`,
`overpass-api.de`, `overpass.osm.ch`) if one happens to be down or unstable.

---

## 4. Once Phase 1 (testing mode) looks right

Check `output/food_desert_towns.xlsx` and `output/towns.geojson`. If
Campodoro and Bevadoro-type towns show up as expected, you're ready for
Phase 2: build a second OSRM server with the **bike** profile (same
`nord-est-latest.osm.pbf`, but extracted with `/opt/bicycle.lua` instead of
`/opt/foot.lua`, run on a different port like 5001), then set
`SKIP_INFRASTRUCTURE_CHECK = False` in `config.py` and re-run.

---

## 5. View the web map

```powershell
cd map
python -m http.server 8000
```
Open `http://localhost:8000`. It reads directly from `../output/*.geojson`.

---

## 6. Known limitations

- Distance is measured by **walking route**. Swap
  `config.OSRM_PROFILE_WALK` to `"driving"` if you'd rather use driving
  distance as the headline number.
- Infrastructure checking (once enabled) is tag-based (OSM `sidewalk=*`,
  `cycleway=*`). Rural Veneto OSM completeness varies by comune — consider
  a manual spot-check of a sample before publishing final figures.
- A small percentage of comuni may fall back to polygon-centroid for their
  center point if OSM's `admin_centre` tagging is missing AND the bulk
  place-node fallback doesn't find a name match. These are logged with
  `[FALLBACK]` — check the console output after each run.
