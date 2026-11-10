# KiezRouter

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org/)
[![Leaflet](https://img.shields.io/badge/Leaflet-1.9.3-199900.svg)](https://leafletjs.com/)
[![DuckDB](https://img.shields.io/badge/DuckDB-Spatial-FFF000.svg)](https://duckdb.org/)

Local and open-source web map for route planning in Berlin. Plan routes by car or on foot, add multiple stops, search addresses and U-/S-Bahn stations, block streets, and export routes. Easy.

<img width="1648" height="833" alt="Screenshot 2026-11-10 at 03-37-36 KiezRouter – Berlin" src="https://github.com/user-attachments/assets/db474078-3fc1-4d71-bc4f-f7a17543dcd1" />

## Features

- **Route planning** – Car and pedestrian routing through Berlin
- **Multi-stop routes** – Add, reorder (drag & drop) and reverse stops
- **Address & station search** – Find addresses and U-/S-Bahn stations
- **Traffic/construction blocking** – Block entire streets or specific house number ranges; blocked segments shown as red lines on the map
- **GPS start** – Use your current location (works over HTTPS)
- **Share & export** – Shareable URLs, copy link, GPX export
- **Mobile friendly** – Responsive bottom sheet layout

## Quick Start

### Prerequisites
- Python 3.12
- Data files in `data/berlin/` (see setup below)

### Setup

```bash
pip install -r requirements.txt

# Build address and station indexes (requires data)
python scripts/build_address_index.py
python scripts/build_station_index.py

# Build routing graphs
python scripts/run_pipeline_routen.py --mode car
python scripts/run_pipeline_routen.py --mode walk
```

### Run

```bash
# HTTP (local only)
python scripts/route_app.py

# HTTPS for LAN/GPS access
python scripts/route_app.py --https
```

Open [https://localip:8443](https://localip:8443).

## Data

Required in `data/berlin/`:
- `berlin-latest.osm.pbf` – OSM data for Berlin
- `route_edges.duckdb`, `route_edges_walk.duckdb` – Routing graphs
- `adressen` table in `depot_berlin.duckdb` – Address index
- `stationen` table in `depot_berlin.duckdb` – Station index

## Tech Stack

- **Backend:** Python, DuckDB
- **Frontend:** Vanilla JavaScript, Leaflet
- **Routing:** Dijkstra's algorithm on OSM road network

This application was developed by [Max](https://github.com/kick-ban-ignore) in Berlin with ❤️ and ☕ and Kong Strong🧃.
