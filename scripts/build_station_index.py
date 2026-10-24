#!/usr/bin/env python3
"""Stationsindex Berlin: U-Bahn/S-Bahn-Stationen aus dem OSM-PBF in eine
DuckDB-Tabelle der Depot-Datenbank. Ergebnis: ~325 Stationen.

Ausführen: .venv/bin/python scripts/build_station_index.py
"""
import csv
import os
import osmium
import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
PBF = os.path.join(DATA, "berlin-latest.osm.pbf")
CSV = os.path.join(DATA, "stationen.csv")
DEPOT_DB = os.path.join(DATA, "depot_berlin.duckdb")


class StationHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def node(self, n):
        t = n.tags
        name = t.get("name")
        if not name:
            return
        is_rail = (t.get("railway") in ("station", "halt", "subway_entrance")
                   or t.get("subway") == "yes"
                   or t.get("light_rail") == "yes"
                   or t.get("train") == "yes")
        if not is_rail:
            return
        if t.get("public_transport") == "station" or t.get("railway") in ("station", "halt"):
            pass
        else:
            # only accept nodes that are actually stations, not stop_positions
            if t.get("public_transport") != "station" and t.get("railway") not in ("station", "halt"):
                return
        if t.get("station") == "subway" or t.get("subway") == "yes":
            typ = "U-Bahn"
        elif t.get("station") == "light_rail" or t.get("light_rail") == "yes":
            typ = "S-Bahn"
        elif t.get("train") == "yes":
            typ = "S-Bahn"
        else:
            return
        self.rows.append((name, typ, n.location.lon, n.location.lat))


def main():
    if not os.path.exists(PBF):
        print(f"FEHLER: PBF fehlt: {PBF}", file=__import__("sys").stderr)
        __import__("sys").exit(1)
    h = StationHandler()
    h.apply_file(PBF)
    seen = set()
    dedup = []
    for name, typ, lon, lat in h.rows:
        if (name, typ) in seen:
            continue
        seen.add((name, typ))
        dedup.append((name, typ, lon, lat))
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["name", "typ", "lon", "lat"])
        w.writerows(dedup)
    con = duckdb.connect(DEPOT_DB)
    con.execute("""CREATE OR REPLACE TABLE stationen AS
        SELECT name, typ, lon, lat
        FROM read_csv_auto(?, delim=';', header=true)""", [CSV])
    n = con.execute("SELECT count(*) FROM stationen").fetchone()[0]
    con.close()
    print(f"Stationen geschrieben: {n}")


if __name__ == "__main__":
    main()