#!/usr/bin/env python3
"""Routenplaner-App (lokal): Webserver mit JSON-API auf http://127.0.0.1:8777.

Start:  .venv/bin/python scripts/route_app.py
Endpunkte:
  /                      -> route_app.html (Frontend)
  /ortsteile             -> Liste der 97 Ortsteile (JSON)
  /route?from=X&to=Y     -> Route (Ortsteilnamen)
  /route?from_lat=..&from_lon=..&to_lat=..&to_lon=..  -> Route (Klick-Punkte)
"""
import argparse
import json
import heapq
import math
import os
import pickle
import re
import socket
import ssl
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_pipeline_routen import (oneway_entropy, load_edges, build_adjacency,
                                 dijkstra, edges_on_path)

import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
EDGES_DB = os.path.join(DATA, "route_edges.duckdb")
DEPOT_DB = os.path.join(DATA, "depot_berlin.duckdb")
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_app.html")
PORT = 8777


def fail(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


STATE = {"orte": {}, "stationen": [], "car": None, "walk": None}
READY = threading.Event()          # car graph ready
READY_WALK = threading.Event()     # walk graph ready


def load_graph(mode="car"):
    global STATE
    edges_db = os.path.join(DATA, "route_edges.duckdb" if mode == "car" else "route_edges_walk.duckdb")
    cache = os.path.join(DATA, "route_graph.pkl" if mode == "car" else "route_graph_walk.pkl")
    db_mtime = os.path.getmtime(edges_db)
    cached = None
    if os.path.exists(cache) and os.path.getmtime(cache) >= db_mtime:
        try:
            with open(cache, "rb") as f:
                cached = pickle.load(f)
            print(f"[{mode}] Graph aus Cache geladen.")
        except Exception as e:
            print(f"[{mode}] Cache lesen fehlgeschlagen ({e}) – baue neu.")
            cached = None

    if cached is None:
        import run_pipeline_routen as rp
        # build oneway map only for car
        if mode == "car":
            con = duckdb.connect(edges_db, read_only=True)
            oneway_map, _ = rp.oneway_entropy(con)
            if oneway_map is None:
                oneway_map = None
        else:
            con = duckdb.connect(edges_db, read_only=True)
            oneway_map = None
        # load edges via rp.load_edges, but it reads MODE global; set it
        rp.configure(mode)
        edges, meta = rp.load_edges(con, oneway_map)
        con.close()
        adj, n_dir = rp.build_adjacency(edges)
        try:
            with open(cache, "wb") as f:
                pickle.dump({"edges": edges, "meta": meta, "adj": adj}, f)
            print(f"[{mode}] Graph-Cache geschrieben.")
        except Exception as e:
            print(f"[{mode}] Cache schreiben fehlgeschlagen: {e}")
    else:
        edges, meta, adj = cached["edges"], cached["meta"], cached["adj"]

    # ortsteile shared (load once)
    if not STATE["orte"]:
        con_d = duckdb.connect(DEPOT_DB, read_only=True)
        con_d.execute("LOAD spatial;")
        orte = con_d.execute("""
            SELECT ortsteil, ST_X(centroid), ST_Y(centroid)
            FROM ortsteile ORDER BY ortsteil
        """).fetchall()
        STATE["orte"] = {r[0]: (r[1], r[2]) for r in orte}
        # stations (U/S-Bahn)
        try:
            st_rows = con_d.execute("SELECT name, typ, lon, lat FROM stationen").fetchall()
            STATE["stationen"] = [{"name": r[0], "typ": r[1], "lon": r[2], "lat": r[3]} for r in st_rows]
        except Exception:
            STATE["stationen"] = []
        con_d.close()

    snap = {name: nearest(adj, x, y) for name, (x, y) in STATE["orte"].items()}
    STATE[mode] = {"edges": edges, "meta": meta, "adj": adj, "snap": snap}
    print(f"[{mode}] Graph geladen: {len(edges):,} Kanten, {len(adj):,} Knoten")


def nearest(adj, x, y):
    return min(adj, key=lambda k: (k[0] - x) ** 2 + (k[1] - y) ** 2)


def wgs_to_utm(lon, lat):
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    x, y = con.execute("""
        SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(
            ST_SetCRS(ST_MakePoint(?, ?), 'EPSG:4326'), 'EPSG:25833', true) p)""",
        [lon, lat]).fetchone()
    con.close()
    return x, y



def route_payload(src, tgt, mode="car", blocked_ids=None, blocked_meta=None):
    st = STATE.get(mode)
    if not st:
        return {"error": "Graph nicht geladen."}
    if src == tgt:
        return {"error": "Start und Ziel identisch."}
    dist, prev = dijkstra(st["adj"], src, blocked_ids)
    if tgt not in dist:
        msg = "Ziel im Graph nicht erreichbar."
        if blocked_ids:
            msg = "Ziel nicht erreichbar – Blockierungen zu restriktiv?"
        return {"error": msg, **(blocked_meta or {})}
    used = edges_on_path(prev, tgt)
    edges = st["edges"]
    meta = st["meta"]
    road_m = sum(edges[i][3] for i in used)
    minutes = dist[tgt] / 60.0
    # Straßenliste
    streets, names = [], []
    for i in used:
        name = meta[i][1]
        flag = (" BRÜCKE" if meta[i][2] else "") + (" TUNNEL" if meta[i][3] else "")
        key_t = (name, flag)
        if names and names[-1][0] == key_t:
            names[-1][1] += 1
        else:
            names.append([key_t, 1])
    for (name, flag), n in names:
        streets.append({"name": name + flag, "kanten": n})
    # Geometrie
    node_path = [edges[used[0]][0]] + [edges[i][1] for i in used]
    wkt = "LINESTRING (" + ", ".join(f"{x:.1f} {y:.1f}" for x, y in node_path) + ")"
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    geo = con.execute(
        "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText(?), "
        "'EPSG:25833', 'EPSG:4326', true))", [wkt]).fetchone()[0]
    con.close()
    coords = [[c[1], c[0]] for c in json.loads(geo)["coordinates"]]
    src_named, tgt_named = nearest_name(src), nearest_name(tgt)
    air = math.hypot(src[0] - tgt[0], src[1] - tgt[1]) / 1000.0
    return {
        "km": round(road_m / 1000.0, 2),
        "minuten": round(minutes, 1),
        "luft_km": round(air, 2),
        "inflation": round(road_m / 1000.0 / air, 2) if air > 0 else None,
        "strassen": streets,
        "route": coords,
        "start": src_named,
        "ziel": tgt_named,
        **(blocked_meta or {}),
    }


def route_payload_multi(points, mode="car", blocked_ids=None, blocked_meta=None):
    st = STATE.get(mode)
    if not st:
        return {"error": "Graph nicht geladen.", **(blocked_meta or {})}
    if len(points) < 2:
        return {"error": "mindestens 2 Punkte"}
    edges = st["edges"]
    meta = st["meta"]
    adj = st["adj"]
    total_km = 0.0
    total_min = 0.0
    all_coords = []
    all_streets = []
    for i in range(len(points) - 1):
        src, tgt = points[i], points[i + 1]
        if src == tgt:
            continue
        dist, prev = dijkstra(adj, src, blocked_ids)
        if tgt not in dist:
            msg = f"Segment {i+1} nicht erreichbar."
            if blocked_ids:
                msg = f"Segment {i+1} nicht erreichbar – Blockierungen zu restriktiv?"
            return {"error": msg, **(blocked_meta or {})}
        used = edges_on_path(prev, tgt)
        road_m = sum(edges[e][3] for e in used)
        total_km += road_m / 1000.0
        total_min += dist[tgt] / 60.0
        node_path = [edges[used[0]][0]] + [edges[e][1] for e in used]
        wkt = "LINESTRING (" + ", ".join(f"{x:.1f} {y:.1f}" for x, y in node_path) + ")"
        con = duckdb.connect()
        con.execute("LOAD spatial;")
        geo = con.execute(
            "SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromText(?), "
            "'EPSG:25833', 'EPSG:4326', true))", [wkt]).fetchone()[0]
        con.close()
        seg_coords = [[c[1], c[0]] for c in json.loads(geo)["coordinates"]]
        if all_coords:
            all_coords.extend(seg_coords[1:])
        else:
            all_coords.extend(seg_coords)
        names = []
        for e in used:
            name = meta[e][1]
            flag = (" BRÜCKE" if meta[e][2] else "") + (" TUNNEL" if meta[e][3] else "")
            key_t = (name, flag)
            if names and names[-1][0] == key_t:
                names[-1][1] += 1
            else:
                names.append([key_t, 1])
        for (name, flag), n in names:
            all_streets.append({"name": name + flag, "kanten": n})
    s = points[0]; t = points[-1]
    sn = nearest_name(s); tn = nearest_name(t)
    air = math.hypot(s[0] - t[0], s[1] - t[1]) / 1000.0
    return {
        "km": round(total_km, 2),
        "minuten": round(total_min, 1),
        "luft_km": round(air, 2),
        "inflation": round(total_km / air, 2) if air > 0 else None,
        "strassen": all_streets,
        "route": all_coords,
        "start": sn,
        "ziel": tn,
        **(blocked_meta or {}),
    }


def _norm(s):
    return s.replace("ß", "ss").replace("ẞ", "ss").lower()


BLOCK_SNAP_M = 30.0   # Abstand-Schwellwert Adresse -> Kante (Teilabschnitt)


def _hnr_int(hnr):
    """Führenden Integer aus Hausnummer ('12a' -> 12, '3-5' -> 3)."""
    m = re.match(r"\s*(\d+)", hnr or "")
    return int(m.group(1)) if m else None


def _point_seg_dist(px, py, ax, ay, bx, by):
    """Abstand Punkt -> Strecke (a,b) in Meter (EPSG:25833)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _address_points_in_range(street, f, t):
    """Adresspunkte der Straße, deren Hausnummer im Bereich [f, t] liegt
    (None = offen). Liefert [(x, y)] in EPSG:25833."""
    con = duckdb.connect(DEPOT_DB, read_only=True)
    norm_col = "lower(replace(replace(strasse, 'ß', 'ss'), 'ẞ', 'ss'))"
    rows = con.execute(f"""
        SELECT hnr, lon, lat FROM adressen WHERE {norm_col} = ?""",
        [_norm(street)]).fetchall()
    con.close()
    pts = []
    for hnr, lon, lat in rows:
        n = _hnr_int(hnr)
        if n is None:
            continue
        if f is not None and n < f:
            continue
        if t is not None and n > t:
            continue
        pts.append((lon, lat))
    # einmal transformieren (eine Verbindung, viele Punkte)
    out = []
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    for lon, lat in pts:
        x, y = con.execute("""
            SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(
                ST_SetCRS(ST_MakePoint(?, ?), 'EPSG:4326'), 'EPSG:25833', true) p)""",
            [lon, lat]).fetchone()
        out.append((x, y))
    con.close()
    return out


def blocked_edge_ids(mode, entries):
    """Löst Block-Einträge zu Kanten-IDs auf.
    entries: Liste {s, f?, t?} — s = Straßenname, f/t = Hausnummer von/bis.
    Ohne f/t: alle Kanten mit exakt normalisiertem Namen.
    Mit f/t: Adresspunkte im Nummernbereich -> Kanten derselben Straße
    innerhalb BLOCK_SNAP_M um einen Punkt.
    Liefert (ids_set, applied_labels, ignored [{s, grund}])."""
    st = STATE.get(mode)
    if not st:
        return set(), [], [{"s": e.get("s", "?"), "grund": "Graph nicht geladen."} for e in entries]
    # Kanten per normalisiertem Namen indizieren (einmal pro Modus)
    name_idx = st.setdefault("name_idx", {})
    if not name_idx:
        for i, e in enumerate(st["meta"]):
            nm = _norm(e[1] or "")
            if nm:
                name_idx.setdefault(nm, []).append(i)
    ids, applied, ignored = set(), [], []
    for e in entries:
        s = (e.get("s") or "").strip()
        if not s:
            continue
        edge_list = name_idx.get(_norm(s), [])
        if not edge_list:
            ignored.append({"s": s, "grund": "kein Namens-Treffer"})
            continue
        f_raw, t_raw = (e.get("f") or "").strip(), (e.get("t") or "").strip()
        if not f_raw and not t_raw:
            ids.update(edge_list)
            applied.append(f"{s} (ganze)")
            continue
        f = _hnr_int(f_raw) if f_raw else None
        t = _hnr_int(t_raw) if t_raw else None
        span = f"Nr. {f_raw or '?'}–{t_raw or '?'}"
        pts = _address_points_in_range(s, f, t)
        if not pts:
            ignored.append({"s": f"{s} ({span})", "grund": "keine Adressen im Nummernbereich"})
            continue
        before = len(ids)
        for i in edge_list:
            u, v = st["edges"][i][0], st["edges"][i][1]
            if any(_point_seg_dist(px, py, u[0], u[1], v[0], v[1]) <= BLOCK_SNAP_M
                   for px, py in pts):
                ids.add(i)
        if len(ids) == before:
            ignored.append({"s": f"{s} ({span})", "grund": "keine Kante im Umkreis der Adressen"})
        else:
            applied.append(f"{s} ({span})")
    return ids, applied, ignored


def geocode(q):
    """Adresse suchen (Suffix: Hausnummer optional). Liefert max 5 Vorschläge."""
    q = q.strip()
    if len(q) < 3:
        return []
    tokens = q.rsplit(" ", 1)
    con = duckdb.connect(DEPOT_DB, read_only=True)
    norm_col = "lower(replace(replace(strasse, 'ß', 'ss'), 'ẞ', 'ss'))"
    if len(tokens) == 2 and any(c.isdigit() for c in tokens[1]):
        street_pat = f"%{_norm(tokens[0])}%"
        hnr = _norm(tokens[1])
        rows = con.execute(f"""
            SELECT strasse, hnr, min(lon), min(lat) FROM adressen
            WHERE {norm_col} LIKE ? AND lower(replace(replace(hnr, 'ß', 'ss'), 'ẞ', 'ss')) LIKE ?
            GROUP BY strasse, hnr
            ORDER BY CASE WHEN lower(replace(replace(hnr, 'ß', 'ss'), 'ẞ', 'ss')) = ? THEN 0 ELSE 1 END,
                     strasse, length(hnr), hnr
            LIMIT 5""", [street_pat, f"%{hnr}%", hnr]).fetchall()
        if not rows:
            con.close()
            return []
    else:
        rows = con.execute(f"""
            SELECT strasse, hnr, min(lon), min(lat) FROM adressen
            WHERE {norm_col} LIKE ?
            GROUP BY strasse, hnr
            ORDER BY strasse, length(hnr), hnr
            LIMIT 5""", [f"%{_norm(q)}%"]).fetchall()
    out = []
    con_arg = duckdb.connect()
    con_arg.execute("LOAD spatial;")
    for strasse, hnr, lon, lat in rows:
        x, y = con_arg.execute("""
            SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(
                ST_SetCRS(ST_MakePoint(?, ?), 'EPSG:4326'), 'EPSG:25833', true) p)""",
            [lon, lat]).fetchone()
        ot = min(STATE["orte"],
                 key=lambda n: (STATE["orte"][n][0] - x) ** 2 + (STATE["orte"][n][1] - y) ** 2)
        out.append({"label": f"{strasse} {hnr} ({ot})", "lon": lon, "lat": lat})
    con_arg.close()
    # U/S-Bahn-Stationen einbeziehen (wie Straßen durchsuchbar)
    qn = _norm(q)
    for s in STATE.get("stationen", []):
        if qn and qn not in _norm(s["name"]):
            continue
        if len(out) >= 8:
            break
        out.append({"label": f"{s['name']} ({s['typ']})", "lon": s["lon"], "lat": s["lat"]})
    con.close()
    return out[:8]


def nearest_name(node):
    """Nächstgelegener Ortsteil zum Knoten (Labeling)."""
    return min(STATE["orte"],
               key=lambda n: (STATE["orte"][n][0] - node[0]) ** 2
                             + (STATE["orte"][n][1] - node[1]) ** 2)



class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parse = urllib.parse.urlparse(self.path)
        if parse.path == "/health":
            self._json({"ready": READY.is_set(), "walk_ready": READY_WALK.is_set()})
            return
        if not READY.is_set() and parse.path in ("/ortsteile", "/geocode", "/route"):
            self._json({"error": "Graph wird geladen…", "loading": True}, 503)
            return
        if parse.path == "/":
            body = open(HTML, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parse.path == "/ortsteile":
            self._json(sorted(STATE["orte"]))
            return
        if parse.path == "/geocode":
            q = urllib.parse.parse_qs(parse.query).get("q", [""])[0]
            self._json(geocode(q))
            return
        if parse.path == "/stationen":
            q = urllib.parse.parse_qs(parse.query).get("q", [""])[0].strip().lower()
            res = []
            for s in STATE.get("stationen", []):
                if q and q not in s["name"].lower():
                    continue
                res.append(s)
                if len(res) >= 8:
                    break
            self._json(res)
            return
        if parse.path == "/streets":
            q = urllib.parse.parse_qs(parse.query).get("q", [""])[0].strip()
            if len(q) < 2:
                self._json([])
                return
            con = duckdb.connect(DEPOT_DB, read_only=True)
            norm_col = "lower(replace(replace(strasse, 'ß', 'ss'), 'ẞ', 'ss'))"
            rows = con.execute(f"""
                SELECT DISTINCT strasse FROM adressen
                WHERE {norm_col} LIKE ?
                ORDER BY strasse LIMIT 8""", [f"%{_norm(q)}%"]).fetchall()
            con.close()
            self._json([r[0] for r in rows])
            return
        if parse.path == "/route":
            q = urllib.parse.parse_qs(parse.query)
            mode = q.get("mode", ["car"])[0]
            if mode not in ("car", "walk"):
                mode = "car"
            st = STATE.get(mode)
            if not st:
                self._json({"error": "Graph wird geladen…", "loading": True}, 503)
                return
            # Straßen-Blockierungen (Bauarbeiten/Stau): JSON-Liste {s, f?, t?}
            blocked_entries, blocked_meta, blocked_ids = [], {}, None
            raw_blocked = q.get("blocked", [""])[0]
            if raw_blocked.strip():
                try:
                    arr = json.loads(raw_blocked)
                    if not isinstance(arr, list):
                        raise ValueError("JSON-Liste erwartet")
                    blocked_entries = [e for e in arr
                                       if isinstance(e, dict) and (e.get("s") or "").strip()]
                except (ValueError, json.JSONDecodeError) as e:
                    self._json({"error": f"Ungültiges blocked-Format: {e}"}, 400)
                    return
                try:
                    blocked_ids, applied, ignored = blocked_edge_ids(mode, blocked_entries)
                    blocked_meta = {
                        "blocked_applied": applied,
                        "blocked_ignored": ignored,
                        "blocked_edges": len(blocked_ids),
                    }
                except Exception as e:
                    self._json({"error": f"Blockierung konnte nicht aufgelöst werden: {e}"}, 500)
                    return
            try:
                if "points" in q:
                    pts = []
                    for seg in q["points"][0].split(";"):
                        la, lo = seg.split(",")
                        pts.append((float(la), float(lo)))
                    if len(pts) < 2:
                        raise ValueError("mindestens 2 Punkte")
                    coords = [nearest(st["adj"], *wgs_to_utm(lo, la)) for la, lo in pts]
                    self._json(route_payload_multi(coords, mode, blocked_ids, blocked_meta))
                elif "from" in q:
                    src = st["snap"][q["from"][0]]
                    tgt = st["snap"][q["to"][0]]
                    self._json(route_payload(src, tgt, mode, blocked_ids, blocked_meta))
                else:
                    src = nearest(st["adj"],
                                  *wgs_to_utm(float(q["from_lon"][0]),
                                              float(q["from_lat"][0])))
                    tgt = nearest(st["adj"],
                                  *wgs_to_utm(float(q["to_lon"][0]),
                                              float(q["to_lat"][0])))
                    self._json(route_payload(src, tgt, mode, blocked_ids, blocked_meta))
            except (KeyError, ValueError) as e:
                self._json({"error": f"Ungültige Anfrage: {e}"}, 400)
            return
        self._json({"error": "unbekannter Pfad"}, 404)


CERT = os.path.join(DATA, "kiezrouter.crt")
KEY = os.path.join(DATA, "kiezrouter.key")


def ensure_cert(host):
    """Erzeugt ein selbstsigniertes Zertifikat (falls nötig) mit SAN für localhost + LAN-IP."""
    if os.path.exists(CERT) and os.path.exists(KEY):
        return CERT, KEY
    # IP-Adressen sammeln für SAN (localhost + LAN + Docker-Interfaces)
    ips = {"127.0.0.1"}
    try:
        ips.add(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except Exception:
        pass
    # hostname -I (alle lokalen IPs)
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
        for tok in out.stdout.split():
            ips.add(tok)
    except Exception:
        pass
    # Default-Route-Interface IP
    try:
        out = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True)
        for tok in out.stdout.split():
            if tok.count(".") == 3:
                ips.add(tok)
    except Exception:
        pass
    # nur valide IPv4
    ips = {ip for ip in ips if ip.count(".") == 3}
    san = "DNS:localhost," + ",".join(f"IP:{ip}" for ip in sorted(ips))
    subj = "/CN=KiezRouter"
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", KEY, "-out", CERT, "-days", "365", "-nodes",
        "-subj", subj, "-addext", f"subjectAltName={san}",
    ]
    print("Erzeuge selbstsigniertes Zertifikat …")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  Zertifikat: {CERT}")
    return CERT, KEY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0", help="Bind-Adresse (0.0.0.0 = ganzes LAN)")
    parser.add_argument("--port", type=int, default=None, help="Port (Default: 8777 HTTP, 8443 HTTPS)")
    parser.add_argument("--https", action="store_true", help="HTTPS aktivieren (nötig für GPS auf dem Handy)")
    args = parser.parse_args()

    if args.port is None:
        args.port = 8443 if args.https else PORT

    threading.Thread(target=_load_graph_bg, daemon=True).start()
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        lan_ip = "<deine-ip>"
    scheme = "https" if args.https else "http"
    print(f"KiezRouter läuft auf {scheme}://0.0.0.0:{args.port} (Strg+C beenden)")
    print(f"  Lokal:  {scheme}://127.0.0.1:{args.port}")
    print(f"  LAN:    {scheme}://{lan_ip}:{args.port}")
    if args.https:
        print("  Hinweis: selbstsigniertes Zertifikat – Browser-Warnung einmalig akzeptieren.")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    if args.https:
        cert, key = ensure_cert(args.host)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()


def _load_graph_bg():
    try:
        load_graph("car")
    except SystemExit:
        pass
    except Exception as e:
        print(f"FEHLER beim Laden des Auto-Graphen: {e}", file=sys.stderr)
    finally:
        READY.set()
    # Walk graph in background (non-blocking for car mode)
    try:
        load_graph("walk")
    except SystemExit:
        pass
    except Exception as e:
        print(f"FEHLER beim Laden des Fußgänger-Graphen: {e}", file=sys.stderr)
    finally:
        READY_WALK.set()


if __name__ == "__main__":
    main()
