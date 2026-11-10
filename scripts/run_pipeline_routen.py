#!/usr/bin/env python3
"""Routenanalyse Berlin: routierbarer Graph aus dem OSM-Roads-Shapefile,
Dijkstra von Depots zu Ortsteil-Zentroiden, Fahrzeit-Abdeckung,
Brücken-/Tunnel-Risiko.

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/run_pipeline_routen.py
"""
import csv
import heapq
import json
import math
import os
import statistics
import argparse
import sys
import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
OUT = os.path.join(REPO, "outputs", "data")
ROADS = os.path.join(DATA, "gis_osm_roads_free_1.shp")
DEPOT_DB = os.path.join(DATA, "depot_berlin.duckdb")
EDGES_DB = os.path.join(DATA, "route_edges.duckdb")

SCENARIOS = ["B20", "B40"]
NODE_PRECISION = 7          # Dezimalstellen für Knoten-Matching (~1 cm)
LCC_MIN_SHARE = 0.95        # Konnektivität-Schwelle
SPEED_LIMIT_OK = (5, 130)

MODE = "car"
EDGES_DB = os.path.join(DATA, "route_edges.duckdb")
NODE_DELAY_S = 8
WALK_SPEED = {
    "footway": 5.0, "path": 5.0, "pedestrian": 5.0, "steps": 3.0,
    "track": 4.0, "track_grade1": 4.0, "track_grade2": 4.0, "track_grade3": 4.0,
    "track_grade4": 4.0, "track_grade5": 4.0, "bridleway": 5.0, "cycleway": 5.0,
    "living_street": 5.0, "residential": 5.0, "service": 5.0, "unclassified": 5.0,
    "tertiary": 5.0, "tertiary_link": 5.0, "secondary": 5.0, "secondary_link": 5.0,
    "primary": 5.0, "primary_link": 5.0,
}
WALK_SPEED_DEFAULT = 5.0


def configure(mode):
    global MODE, EDGES_DB, NODE_DELAY_S, SPEED_FACTOR, DEFAULT_SPEED_FACTOR
    MODE = mode
    if mode == "walk":
        EDGES_DB = os.path.join(DATA, "route_edges_walk.duckdb")
        NODE_DELAY_S = 0
        SPEED_FACTOR = {}
        DEFAULT_SPEED_FACTOR = None
    else:
        EDGES_DB = os.path.join(DATA, "route_edges.duckdb")
        NODE_DELAY_S = 8
        SPEED_FACTOR = {
            "motorway": 0.85, "motorway_link": 0.85, "trunk": 0.85, "trunk_link": 0.85,
        }
        DEFAULT_SPEED_FACTOR = 0.60

BANDS = (15, 30, 45)        # Minuten-Bänder für Abdeckung

# Verkehrsmodell (Stichwort: freifließendDate Geschwindigkeit realistisch senken).
# Dokumentierte Annahme: Stadtverkehr mit Lichtsignal- und Ampel-Auswirkung:
#   motorway/trunk:         Faktor 0,85  (hauptsächlich fließend)
#   andere Straßenklassen:  Faktor 0,60  (ampel/starke Durchquerungen)
# Kein Tagesprofil, keine Sperrungen, keine Wende-/Abbiegezeiten.
SPEED_FACTOR = {
    "motorway": 0.85, "motorway_link": 0.85, "trunk": 0.85, "trunk_link": 0.85,
}
DEFAULT_SPEED_FACTOR = 0.60
NODE_DELAY_S = 8   # Sekunden Stau-/Lichtsignalwartezeit bei Knotenpunkten (Grad ≥ 3)


def fail(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def split_sql(sql):
    """Zerlegt SQL in Anweisungen (siehe run_pipeline_berlin.py)."""
    stmts, buf = [], []
    i, n = 0, len(sql)
    while i < n:
        if sql[i] == "'":
            j = sql.find("'", i + 1)
            buf.append(sql[i:j + 1] if j != -1 else sql[i:])
            i = j + 1 if j != -1 else n
            continue
        if sql[i:i+2] == '--':
            j = sql.find('\n', i)
            i = n if j == -1 else j + 1
            continue
        if sql[i:i+2] == '/*':
            j = sql.find('*/', i + 2)
            i = n if j == -1 else j + 2
            continue
        if sql[i] == ';':
            s = ''.join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
            i += 1
            continue
        buf.append(sql[i])
        i += 1
    s = ''.join(buf).strip()
    if s:
        stmts.append(s)
    return stmts


def run_sql(con, name):
    with open(os.path.join(REPO, "sql", name)) as f:
        stmts = split_sql(f.read())
        print(f"--> {name} ({len(stmts)} Anweisungen)")
        for stmt in stmts:
            con.execute(stmt)


def oneway_entropy(con):
    """Klärt die Einbahn-Semantik empirisch.
    Konvention: Der Buchstabe, der bei motorway dominiert, wird als
    „nur vorwärts (Geometrierichtung)" interpretiert; der Buchstabe,
    der bei residential dominiert, als „beidseitig".
    'T' (= oneway yes, z. B. Oberwasserstraße) gilt zusätzlich als
    nur vorwärts. Bei Nichtunterscheidung: None (bidirektional, dokumentiert)."""
    rows = con.execute("""
        SELECT fclass, oneway, count(*) n
        FROM kanten GROUP BY 1, 2
    """).fetchall()
    by_class = {}
    for fclass, oneway, n in rows:
        by_class.setdefault(fclass, {})[oneway] = n
    motorway = by_class.get("motorway", {})
    residential = by_class.get("residential", {})
    if not motorway or not residential:
        return None, by_class
    fwd = max(motorway, key=motorway.get)
    both = max(residential, key=residential.get)
    if fwd == both:
        return None, by_class
    return {"forward_only": fwd, "bidirectional": both}, by_class


def load_edges(con, oneway_map):
    """Kanten lesen; Liste (u_coord, v_coord, sekunden, laenge_m, edge_id)
    plus Metadaten je edge_id. Im Fußgänger-Modus: Gehgeschwindigkeit,
    alle Kanten bidirektional."""
    rows = con.execute("""
        SELECT fclass, osm_id, name, oneway, bridge, tunnel, maxspeed,
               default_v_kmh, geojson
        FROM kanten
    """).fetchall()
    edges, meta = [], []
    for fclass, osm_id, name, oneway, bridge, tunnel, maxspeed, v_def, gj in rows:
        if MODE == "walk":
            v = WALK_SPEED.get(fclass, WALK_SPEED_DEFAULT)
        else:
            v = maxspeed if maxspeed is not None and SPEED_LIMIT_OK[0] <= maxspeed <= SPEED_LIMIT_OK[1] else v_def
            v *= SPEED_FACTOR.get(fclass, DEFAULT_SPEED_FACTOR)
        ring = json.loads(gj)["coordinates"]
        keyed = [(round(x, NODE_PRECISION), round(y, NODE_PRECISION)) for x, y in ring]
        forward_only = False if MODE == "walk" else (
            oneway_map is not None and (
                oneway == oneway_map.get("forward_only") or oneway == "T"))
        for a, b in zip(keyed, keyed[1:]):
            seg_m = math.hypot(b[0] - a[0], b[1] - a[1])   # EPSG:25833 = Meter
            seconds = seg_m / (v / 3.6)
            edge_id = len(edges)
            edges.append((a, b, seconds, seg_m, edge_id, forward_only))
            meta.append((fclass, name or (osm_id + ""), bridge == "T",
                          tunnel == "T", osm_id))
    return edges, meta


def build_adjacency(edges):
    """Adjazenzlisten. Nur forward_only-Kanten sind einseitig; sonst beide.
    Kosten = Kantenzeit + Knotenverzögerung am ABFAHRTSKnoten (Grad ≥ 3)."""
    junction = {}
    for a, b, seconds, length_m, edge_id, forward_only in edges:
        junction[a] = junction.get(a, 0) + 1
        if not forward_only:
            junction[b] = junction.get(b, 0) + 1
    junction_cost = {
        k: (NODE_DELAY_S if degree >= 3 else 0.0)
        for k, degree in junction.items()
    }
    adj = {}
    n_dir = 0
    for a, b, seconds, length_m, edge_id, forward_only in edges:
        wait = junction_cost.get(a, 0.0)
        adj.setdefault(a, []).append((b, seconds + wait, edge_id))
        if not forward_only:
            wait_b = junction_cost.get(b, 0.0)
            adj.setdefault(b, []).append((a, seconds + wait_b, edge_id))
        n_dir += 1 if forward_only else 0
    return adj, n_dir


def lcc_share(node_count, adj):
    seen = set()
    best = 0
    for start in list(adj):
        if start in seen:
            continue
        size, stack = 0, [start]
        seen.add(start)
        while stack:
            u = stack.pop()
            size += 1
            for v, _w, _e in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        best = max(best, size)
    return best / node_count, best


def dijkstra(adj, source, blocked=None):
    """Dijkstra; optional werden Kanten mit edge_id in `blocked` übersprungen
    (Straßensperren: Bauarbeiten/Stau)."""
    dist = {source: 0.0}
    prev = {}
    heap = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        for v, w, edge_id in adj.get(u, []):
            if blocked and edge_id in blocked:
                continue
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, edge_id)
                heapq.heappush(heap, (nd, v))
    return dist, prev


def edges_on_path(prev, target):
    """Kanten des Dijkstra-Pfads in Fahrtrichtung start -> ziel
    (Rückverfolgung von target rückwärts, dann Reihenfolge umdrehen)."""
    used, u = [], target
    while u in prev:
        _, edge_id = prev[u]
        used.append(edge_id)
        u, _ = prev[u]
    used.reverse()
    return used


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["car", "walk"], default="car")
    args = parser.parse_args()
    configure(args.mode)

    if not os.path.exists(ROADS) or not os.path.exists(DEPOT_DB):
        fail("Quelldateien fehlen – data/download.sh ausführen?")

    sql_file = "e01_filter_roads.sql" if MODE == "car" else "e01_filter_roads_walk.sql"
    con = duckdb.connect(EDGES_DB)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"SET VARIABLE roads_path = '{ROADS}'")
    run_sql(con, sql_file)

    if MODE == "car":
        oneway_map, dist = oneway_entropy(con)
        print("--> Einbahn-Klären:", oneway_map if oneway_map else
              "NICHT ERKENNBAR -> bidirektional (dokumentiert)")
        print("    motorway-Verteilung:", dist.get("motorway"))
    else:
        oneway_map = None
        print("--> Fußgänger-Modus: alle Kanten bidirektional")

    edges, meta = load_edges(con, oneway_map)
    node_key_set = {k for e in edges for k in (e[0], e[1])}
    adj, n_dir = build_adjacency(edges)
    share, lcc_size = lcc_share(len(node_key_set), adj)
    print(f"--> Kanten: {len(edges)} | gerichtet: {n_dir} | Knoten: "
          f"{len(node_key_set)} | LCC: {share:.1%}")
    if share < LCC_MIN_SHARE:
        fail(f"Konnektivität zu niedrig: LCC {share:.1%} < {LCC_MIN_SHARE:.0%}")

    # Ortsteile + Depots aus der Bestands-DB
    con_d = duckdb.connect(DEPOT_DB, read_only=True)
    con_d.execute("LOAD spatial;")
    orte = con_d.execute("""
        SELECT ortsteil, ewz, ST_X(centroid), ST_Y(centroid)
        FROM ortsteile ORDER BY ortsteil
    """).fetchall()
    ortsteile = {r[0]: r for r in orte}

    def nearest_node(x, y):
        """Nächster Graph-Knoten (brute force über Koordinaten)."""
        best, best_d2 = None, float("inf")
        for k in adj:
            dx, dy = k[0] - x, k[1] - y
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2, best = d2, k
        return best

    snap = {o: nearest_node(r[2], r[3]) for o, r in ortsteile.items()}
    if any(v is None for v in snap.values()):
        fail("Ortsteil ohne abgedeckten Graph-Knoten (Snap fehlgeschlagen).")

    def luft_km(u, v):
        dx, dy = u[0] - v[0], u[1] - v[1]
        return math.hypot(dx, dy) / 1000.0

    for scen in SCENARIOS:
        path_csv = os.path.join(OUT, f"{scen}_zuordnung.csv")
        with open(path_csv, encoding="utf-8") as f:
            depots = list(dict.fromkeys(
                row["depot_ags"] for row in csv.DictReader(f, delimiter=";")
            ))

        # EIN Dijkstra je Depot (nicht je Depot-Ortsteil-Paar)
        print(f"--> {scen}: Dijkstra je {len(depots)} Depot(s) ...")
        dmaps = {}
        for depot in depots:
            src = nearest_node(*ortsteile[depot][2:4])
            if src not in adj:
                fail(f"Depot-Knoten nicht im Graph: {depot}")
            dmaps[depot] = dijkstra(adj, src)

        rows_out, bridge_load, tunnel_load = [], {}, {}
        medians = []
        for ort in sorted(ortsteile):
            target = snap[ort]
            best = None
            for depot in depots:
                dist_t, prev = dmaps[depot]
                if target not in dist_t:
                    continue
                fahrt_min = dist_t[target] / 60.0
                if best is None or fahrt_min < best[0]:
                    used = edges_on_path(prev, target)
                    road_m = sum(edges[i][3] for i in used)
                    best = (fahrt_min, road_m, depot, used)
            if best is None:
                fail(f"Keine Route gefunden: {ort}")
            fmin, road_m, depot, used = best
            air = luft_km(ortsteile[depot][2:4], ortsteile[ort][2:4])
            infl = road_m / 1000.0 / air if air > 0 else 0.0
            medians.append(infl)
            uses_b = any(meta[i][2] for i in used)
            uses_t = any(meta[i][3] for i in used)
            rows_out.append((depot, ort, round(road_m / 1000.0, 2),
                             round(fmin, 1), round(air, 2), round(infl, 2),
                             uses_b, uses_t))
            for i in used:
                if meta[i][2]:
                    bridge_load[i] = bridge_load.get(i, 0) + ortsteile[ort][1]
                if meta[i][3]:
                    tunnel_load[i] = tunnel_load.get(i, 0) + ortsteile[ort][1]

        write_csv(os.path.join(OUT, f"routen_{scen}_matrix.csv"),
                  ["depot", "ortsteil", "strasse_km", "fahrt_min", "luft_km",
                   "inflation_faktor", "nutzt_bruecke", "nutzt_tunnel"],
                  rows_out)

        # Fahrzeit-Abdeckung
        ewz_total = sum(r[1] for r in ortsteile.values())
        cov = []
        for band in BANDS:
            share = sum(ortsteile[ort][1]
                        for (depot, ort, _r, fmin, _a, _i, _b, _t) in rows_out
                        if fmin <= band)
            cov.append((band, round(100.0 * share / ewz_total, 1)))
        # Luftlinien-Vergleich: 5-km/10-km-Bänder derselben
        cov_air = []
        for band in (5.0, 10.0):
            share = sum(ortsteile[ort][1]
                        for (depot, ort, _r, fmin, air, _i, _b, _t) in rows_out
                        if float(air) <= band)
            cov_air.append((band, round(100.0 * share / ewz_total, 1)))

        write_csv(os.path.join(OUT, f"routen_{scen}_abdeckung.csv"),
                  ["typ", "band", "anteil_ewz_pct"],
                  [("fahrzeit_min", b, v) for b, v in cov]
                  + [("luft_km", b, v) for b, v in cov_air])

        # Brücken-/Tunnel-Risiko (top nach betroffener Einwohnerzahl)
        rows_risk = []
        for load_index, load_map, adverb in (("bruecke", bridge_load, "bruecke"),
                                             ("tunnel", tunnel_load, "tunnel")):
            ranked = sorted(load_map.items(), key=lambda kv: -kv[1])[:15]
            for edge_id, ewz_effect in ranked:
                fclass, name, is_b, is_t, osm_id = meta[edge_id]
                rows_risk.append((adverb, fclass, name, osm_id,
                                  round(ewz_effect / 1000.0, 1)))
        write_csv(os.path.join(OUT, f"routen_{scen}_bruecken_tunnel.csv"),
                  ["typ", "fclass", "name", "osm_id", "betroffene_ewz_tsd"],
                  rows_risk)

        med = statistics.median(medians)
        print(f"--> {scen}: mediane Inflation {med:.2f} | Abdeckung: "
              + ", ".join(f"{b}min {v}%" for b, v in cov))

    con.close()
    con_d.close()
    print("--> Routenanalyse fertig.")


if __name__ == "__main__":
    main()

