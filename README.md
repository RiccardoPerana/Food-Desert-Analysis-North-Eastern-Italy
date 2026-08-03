# Food Desert Analysis Pipeline — Northeastern Italy (Scalable)

Identifies Italian municipalities that:
1. Have no supermarket or minimarket of their own
2. Have a real, road-routed walking distance of more than 3km from the
   nearest supermarket
   
Results are exported as an Excel spreadsheet and an interactive web map.
Cycling lane and public transport overlays are included on the map so
you can visually check for safe walking/cycling infrastructure along
each town's route to its nearest supermarket.

Below are screenshots showing the output:
<img width="1440" height="1080" alt="Map Screenshot 2" src="https://github.com/user-attachments/assets/b04ca958-c1d0-42d1-be39-97c3d96881d9" />
<img width="1436" height="780" alt="Spreadsheet Output Screenshot" src="https://github.com/user-attachments/assets/92b1816b-1bf1-4b8d-be01-42c577533584" />
<img width="1440" height="1080" alt="Map Screeshot 1" src="https://github.com/user-attachments/assets/2a29bfd8-d365-4015-b8a9-9701ee733396" />

All geographic data is read from a local OpenStreetMap file rather than
live web queries, so runs are fast (minutes, not hours) and don't depend
on the availability of free public map-query servers.

---
 
## Prerequisites
 
- **Python 3.9+** (any modern version — no special version pinning needed)
- **Docker Desktop** (used to run the routing engine, OSRM)
- **~2GB free disk space** (for the regional map data file)
- Windows, macOS, or Linux — all instructions below use Windows PowerShell
  syntax; adjust for your shell if needed (e.g. `cp` instead of `Copy-Item`,
  `rm` instead of `Remove-Item`)
---
 
## 1. Clone the repository and set up Python
 
```powershell
git clone <your-repo-url>
cd food_desert_pipeline
 
python -m venv venv
venv\Scripts\activate        # macOS/Linux: source venv/bin/activate
 
pip install -r requirements.txt
```
 
---
 
## 2. Download the regional map data
 
This single file is used for **two** things: the local supermarket/comuni/
cycling-lane data, and the routing engine (OSRM) in the next step.
 
```powershell
Invoke-WebRequest -Uri "https://download.geofabrik.de/europe/italy/nord-est-latest.osm.pbf" -OutFile "nord-est-latest.osm.pbf"
```
 
Place it in the project's root folder (same folder as `pipeline.py`).
It covers Veneto, Trentino-Alto Adige, and Friuli-Venezia Giulia — matching
this project's three target regions.
 
> To analyze a different area, download the matching extract from
> [download.geofabrik.de/europe/italy.html](https://download.geofabrik.de/europe/italy.html)
> and update `OSM_PBF_PATH` in `config.py` accordingly.
 
---
 
## 3. Set up the routing engine (OSRM)
 
OSRM calculates real walking distances along actual roads. It runs in
Docker and uses the same file you just downloaded.
 
```powershell
# One-time setup: extract, partition, and customize the routing data
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/nord-est-latest.osm.pbf
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/nord-est-latest.osrm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/nord-est-latest.osrm
```
 
This is a one-time step — expect it to take 10-40+ minutes depending on
your machine, and to use several GB of disk space temporarily.
 
**Every time you want to run the pipeline**, start the routing server in
its own terminal window and leave it running:
 
```powershell
docker run -t -i -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/nord-est-latest.osrm
```
 
Wait for it to print `running and waiting for requests` before moving on.
If you ever see `"connection actively refused"` errors while running the
pipeline, this server isn't running — start it first.
 
---
 
## 4. Population data
 
Three small ISTAT population CSVs are already included in `data/` (one
per region). If you'd rather download fresh copies yourself:
 
1. Go to [https://www.istat.it/comunicato-territoriale/censimento-della-popolazione-dati-regionali-anno-2024/]
2. Download the "Popolazione residente comunale" data, filtered by region
3. Save as `data/comuni veneto.csv`, `data/comuni friuli.csv`, `data/comuni trentino.csv`
> **Known ISTAT quirk**: these files are sometimes exported in Latin-1
> encoding with occasional corrupted accented characters (e.g. "Arsiè"
> becoming a garbled symbol). `population_istat.py` handles the encoding
> automatically, but if you download a fresh file and see match warnings
> for accented town names, open the CSV in a plain text editor and check
> for garbled characters manually.
 
---
 
## 5. Run the analysis
 
In a **separate terminal** from the OSRM server (leave that one running):
 
```powershell
venv\Scripts\activate
python pipeline.py
```
 
This fetches municipal boundaries, attaches population data, fetches
supermarkets, and evaluates every town against the criteria. Results:
- `output/food_desert_towns.xlsx` — full spreadsheet, every matched town
- `output/towns.geojson`, `output/routes.geojson` — data for the web map
First run takes longer (building local caches); subsequent runs are much
faster since towns and supermarket data get cached in `data/*.gpkg`.
 
---
 
## 6. Fetch the map overlay layers
 
```powershell
python fetch_map_layers.py
```
 
This fetches cycling lanes and public transport routes (also from the
local file) for the map's toggleable overlays. Produces:
- `output/cycling_lanes.geojson`
- `output/public_transport.geojson`
---
 
## 7. Host the interactive web map
 
**Important**: run the server from the **project root**, not the `map`
folder — the map's file paths depend on this.
 
```powershell
python -m http.server 8000
```
 
Open your browser to:
 
```
http://localhost:8000/map/
```
 
You should see red markers for every underserved town. Click one to see
its details and highlighted route. Toggle **Cycling lanes** and
**Public transport** on/off using the buttons in the top-right corner
(cycling lanes only render once zoomed in close, to avoid visual clutter).
 
---
 
## Configuration
 
All settings live in `config.py`:
 
| Setting | Purpose |
|---|---|
| `TARGET_LEVEL` / `TARGET_REGIONS` | Which area to analyze (`"multi_region"` + a list, `"region"` + one name, or `"province"` + `TARGET_NAME`) |
| `DISTANCE_THRESHOLD_KM` | The "too far from a supermarket" cutoff (default 3km) |
| `DISTANCE_REVIEW_THRESHOLD_KM` | Results at/beyond this distance get flagged in the spreadsheet as worth a manual look, rather than being silently trusted (default 4km) |
| `BORDER_BUFFER_KM` | How far to search beyond the target area's edge for supermarkets, to correctly handle towns whose nearest supermarket sits just across a provincial/regional border |
| `FORCE_REFRESH_CACHE` | Set to `True` to ignore cached data and re-fetch everything from scratch |
| `OSM_PBF_PATH` | Path to the local map data file from Step 2 |
 
---
 
## Troubleshooting a Specific Town
 
If a result looks suspicious (a town shown as underserved when you know
it has a nearby supermarket, or vice versa), use the diagnostic tool:
 
```powershell
python diagnose_supermarkets.py
```
 
Edit the `TOWN_NAMES` list near the top of that file to check a specific
town. It shows the cached data around that town, cross-checks it against
your final spreadsheet, and (network permitting) compares it against a
live query for a second opinion.
 
---
 
## Known Limitations
 
- **Cross-region border towns**: the local map file covers exactly
  Veneto + Trentino-Alto Adige + Friuli-Venezia Giulia. A town right on
  the outer edge whose true nearest supermarket sits just across that
  outer border (into Lombardy, Emilia-Romagna, Austria, or Slovenia)
  won't see it. This doesn't affect internal borders between the three
  target regions themselves, which are fully covered.
- **Data snapshot age**: the local map file is a snapshot from whenever
  it was downloaded. OpenStreetMap data changes constantly as volunteers
  edit it, so very recently opened/closed supermarkets may not be
  reflected. Re-download the `.osm.pbf` file periodically for the most
  current data.
- **No automated sidewalk/cycling-lane check**: rather than an automated
  pass/fail check for safe infrastructure, the map lets you check this
  visually — select a town, toggle "Cycling lanes" on, and zoom in to see
  whether a lane runs along the highlighted route.
