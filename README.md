# KiezRouter

> 🚗→🚶 Offline routing on the Berlin OSM street graph — addresses, U/S-Bahn POIs,
> multi-stop, car & pedestrian modes. Static analysis SQL included, no server stack.

```
   ┌────────────────────────────────────────────────────────┐
   │  ┌──────────┐   ┌──────────┐   ┌──────────┐            │
   │  │  start   │──▶│  stop 1  │──▶│  stop n  │──▶ ┌─────┐ │
   │  └──────────┘   └──────────┘   └──────────┘    │ziel │ │
   │                                                └─────┘ │
   │   Dijkstra · 632k nodes · 664k edges · ~3 s cold       │
   └────────────────────────────────────────────────────────┘
```

---

**Status** · in progress · supported modes: `car`, `walk`

| Badge | Detail |
|---|---|
| ![stack](https://img.shields.io/badge/stack-duckdb%20%2B%20osmium%20%2B%20vanillaJS-blue) | ![python](https://img.shields.io/badge/python-3.12-green) |
| ![license](https://img.shields.io/badge/data-ODbL-yellow) | ![size](https://img.shields.io/badge/address_index-289k-red) |

## TL;DR

Local web app, zero framework in the backend (`http.server`), zero framework in the
frontend (Leaflet + vanilla JS). All geocoding and routing offline, data from a
single Geofabrik extract + a single OSM PBF — nothing leaves your machine.

```
.venv/bin/python scripts/route_app.py          # http://127.0.0.1:8777
.venv/bin/python scripts/route_app.py --https  # https://127.0.0.1:8443 (self-signed, for mobile GPS)
```

## Stack

| Layer      | Tech                                                        |
|------------|-------------------------------------------------------------|
| Backend    | Python 3.12 · stdlib `http.server` · DuckDB 1.5 (spatial)   |
| Graph      | Dijkstra on `epsg:25833` UTM-33N projected edge list        |
| Indexing   | pyosmium (`osmium.SimpleHandler`) for PBF → DuckDB row scan |
| Frontend   | Leaflet 1.9.3 · vanilla JS · AbortController debounce       |
| Transport  | `http.server` or `ssl.SSLContext` (self-signed cert w/ LAN IP in SAN) |
| Caching    | pickled adjacency list, invalidated by DB mtime             |

## Endpoints

| Method | Path                             | Params                                       | Response                                      |
|--------|----------------------------------|----------------------------------------------|-----------------------------------------------|
| GET    | `/health`                        | –                                            | `{"ready": bool, "walk_ready": bool}`         |
| GET    | `/geocode`                       | `q` (street [+ house number])                | up to 8 `{label, lon, lat}`                   |
| GET    | `/stationen`                     | `q` (station name)                           | max 8 `{name, typ, lon, lat}`                 |
| GET    | `/streets`                       | `q` (street name)                            | max 8 distinct street names                   |
| GET    | `/route`                         | `from_lat`/`from_lon`/`to_lat`/`to_lon` **or** `points=lat,lon;lat,lon;…` + `mode=car\|walk` + optional `blocked=Name1;Name2` | `{km, minuten, luft_km, inflation, strassen[], route[], start, ziel, blocked_applied[], blocked_ignored[], blocked_edges}` |

Railway POIs (`station=subway` → **U-Bahn**, `light_rail`/`train` → **S-Bahn**) are
merged into `/geocode` results as `Name (U-Bahn)` / `Name (S-Bahn)`.

## Graph internals

```
e01_filter_roads.sql       ┌──────────────────────────────────────────────┐
---  car edges         ──▶ │  kanten (fclass, osm_id, oneway, maxspeed,   │
e01_filter_roads_walk.sql  │         default_v_kmh, bridge, tunnel)       │
---  foot edges        ──▶ └──────────────────────────────────────────────┘
                                          │
                                          ▼  ST_Transform('EPSG:4326' → 'EPSG:25833')
                           ┌──────────────────────────────────────────────┐
   run_pipeline_routen.py──▶│  edges: (u, v, seconds, meters, id, fwd_only)│
                           │  adj:   u → [(v, cost, edge_id), …]           │
                           └──────────────────────────────────────────────┘
```

| Mode | Edge classes included | Direction   | Speed model                     |
|------|----------------------|-------------|---------------------------------|
| car  | motorway, trunk, primary, secondary, tertiary, unclassified, residential, living_street, service (+links) | oneway-aware | `maxspeed × 0.85` (fast classes) / `× 0.60` (urban), + 8 s junction delay (`deg ≥ 3`) |
| walk | footway, path, pedestrian, steps, track_*, bridleway, cycleway, living_street, residential, service, unclassified, tertiary*, secondary*, primary* | bidirectional | 5 km/h (3 km/h on `steps`) |

Per-edge speed comes from `maxspeed` when plausible (5–130 km/h), else the
class default. No turn-penalties, no traffic profiles.

### Blocked streets (Baustellen / Stau)

`/route?...&blocked=<JSON array>` — entries are `{"s": "Street", "f": "1", "t": "15"}`
(`f`/`t` optional house-number range, also one-sided). Resolution:

- **Whole street** (no `f`/`t`): every edge whose normalized name matches.
- **Partial segment**: address points of that street whose house number
  (leading integer, `12a` → 12) falls in the range are snapped to the nearest
  edge of the same street (`BLOCK_SNAP_M = 30`); only those edges are blocked.
  Works wherever OSM has `addr:housenumber` data — otherwise the entry is
  reported as ignored ("keine Adressen im Nummernbereich"), never silently skipped.

Dijkstra skips blocked edges during expansion; the blocklist lives client-side
(right-side 🚧 panel, localStorage) and rides along with every request — no
server state. Response reports `blocked_applied`, `blocked_ignored` (with reason)
and `blocked_edges`. If the blocklist disconnects start/target, the API answers
*"Ziel nicht erreichbar – Blockierungen zu restriktiv?"* instead of hanging.

### Cost function

```
cost(u → v) = seg_m / (v_kmh / 3.6)          # edge traversal
            + 8 [s] · 1[deg(u) ≥ 3]          # junction delay (car)
```

The resulting seconds are what you see as `Fahrzeit`/`Laufzeit`.

## Feature pipeline

| Step | Why it matters |
|------|----------------|
| ß/ss normalization + `lower()` on both sides | otherwise ~55 % of streets (ß) wouldn't match on `upper()` (Python folds `ß → SS`, DuckDB keeps `ẞ`) |
| exact-match first on `hnr` (ORDER BY CASE <br> WHEN hnr = ? THEN 0 ELSE 1 END) | avoids `Straße 5` suggestions dominated by `15/25/5x` |
| `GROUP BY strasse, hnr` | OSM has ≥ 1 node per address (entrance + building centroid) |
| degenerate pick of `min(lat/lon)` per node | deterministic coordinate per (strasse, hnr) |
| walking mode skips `oneway_entropy()` | pedestrians are direction-invariant |
| graph cache `route_graph{,_walk}.pkl` | cold start 26 s → ~8–10 s (580 MB → 12 MB pickle) |

## Mobile / GPS

Browsers require a **secure context** for `navigator.geolocation` — plain HTTP
on a LAN IP won't do. `--https` serves a self-signed cert whose SAN contains the
LAN IP, so phones can accept it once and then use GPS.

```
gitignore: data/ (raw OSM, PBF, .duckdb), outputs/, kiezrouter.crt/.key,
           *.key|*.crt|*.pem|.env — nothing secret ever lands in the repo.
```

## Data pipeline

```
geofabrik-berlin-latest-free.shp.zip ──e01_filter_roads.sql──▶ route_edges.duckdb
berlin-latest.osm.pbf ──(pyosmium nodes)──▶ depot_berlin.duckdb.adressen     (~289k)
berlin-latest.osm.pbf ──(pyosmium stations)─▶ depot_berlin.duckdb.stationen  (~347)
```

## Attribution

© OpenStreetMap contributors via [Geofabrik](https://download.geofabrik.de) extracts (ODbL).
Tiles: Esri World Gray Canvas / OpenStreetMap-DE (FOSSGIS).
