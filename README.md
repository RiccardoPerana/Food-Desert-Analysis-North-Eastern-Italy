# Food Desert Analysis — North-Eastern Italy

A data pipeline and interactive map identifying towns in Veneto, Friuli-Venezia
Giulia, and Trentino-Alto Adige where access to a supermarket is genuinely
difficult — no shop in town, a long walk to the nearest one, and no safe
sidewalk or cycling route to get there.

## Why This Project

Italy's population is aging, and for elderly residents without a car, the
distance to the nearest supermarket — and whether they can safely walk or
cycle there — is a real quality-of-life issue. This project combines open
map data with official population statistics to systematically find and
quantify which towns are most affected, across an area of over 1,000
comuni (municipalities).

## What It Does

For every comune in the target area, the pipeline checks three criteria:

1. **No supermarket or minimarket of its own**
2. **The town center is more than 3km, by real routed walking distance,
   from the nearest supermarket** — not straight-line distance, actual
   road-network distance
3. **No sidewalk or cycling lane exists along that specific route**

Towns meeting all three are flagged as underserved. Critically, the
analysis correctly handles the case where a town's nearest supermarket is
across a provincial or regional border — it never artificially restricts
the search to administrative boundaries.

## Outputs

- **An Excel spreadsheet** listing every matched town: name, province,
  population, distance to the nearest supermarket, and the supermarket
  itself — with a built-in "flagged for review" column for any result
  that looks like it might warrant a manual sanity check.
- **An interactive web map** (Leaflet + OpenStreetMap) with:
  - Zoomable, pannable view restricted to the region of interest
  - Highlighted, clickable markers for every underserved town
  - Toggleable overlays for cycling lanes and urban public transport
  - Click any town for a popup with its stats, and see its route to the
    nearest supermarket highlighted on the map

## Data Sources

- **[OpenStreetMap](https://www.openstreetmap.org/)** (via the Overpass
  API) — town locations, supermarket locations, road network, sidewalks,
  cycling lanes, and public transport routes
- **[ISTAT](https://www.istat.it/)** (Italy's national statistics
  institute) — official comune-level population figures
- **[OSRM](http://project-osrm.org/)** (self-hosted) — real routed
  walking/cycling distances, not straight-line estimates

## Tech Stack

Python (GeoPandas, Shapely, OSMnx, openpyxl) for the data pipeline;
Leaflet.js for the web map; Docker for a self-hosted OSRM routing engine.

## Setup

This is a data-heavy pipeline that queries public map APIs and requires a
locally-run routing engine. See [SETUP.md](SETUP.md) for full instructions,
including:
- Getting ISTAT population data
- Setting up a local OSRM instance via Docker
- Running the pipeline and generating the map

## Known Limitations

- **Public transport coverage on the map reflects OpenStreetMap's own
  data completeness**, which is strong for urban routes but inconsistent
  for regional/inter-urban coach lines — a town's real inter-urban bus
  connection may not appear even if it exists.
- **Cycling/sidewalk infrastructure detection is tag-based** (OSM
  `sidewalk=*`, `cycleway=*`), so completeness depends on how thoroughly
  a given road has been mapped.
- Population and boundary data come from a specific snapshot in time;
  Italian comuni occasionally merge or rename, which can very occasionally
  cause a name-matching gap (surfaced clearly in the console output when
  it happens, not silently dropped).

## License

[Choose one, e.g. MIT — see LICENSE file]

