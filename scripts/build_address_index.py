#!/usr/bin/env python3
"""Adressindex Berlin: extrahiert Nodes mit addr:housenumber aus dem
Geofabrik-OSM-PBF in eine DuckDB-Tabelle der Depot-Datenbank.

Ergebnisadressen: ~400-500k Punkte (OSM-Hausnummern, Berlin).
Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/build_address_index.py
"""
import os
import sys
import duckdb
import osmium

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
PBF = os.path.join(DATA, "berlin-latest.osm.pbf")
CSV = os.path.join(DATA, "adressen.csv")
DEPOT_DB = os.path.join(DATA, "depot_berlin.duckdb")

CSV_HEADER = ["strasse", "hnr", "lon", "lat"]


class AdressenHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def node(self, n):
        tags = n.tags
        street = tags.get("addr:street")
        hnr = tags.get("addr:housenumber")
        if not street or not hnr:
            return
        name = tags.get("addr:housename")
        if tags.get("addr:interpolation"):        # interpolierte sind keine Punkte
            return
        self.rows.append((street, hnr, n.location.lon, n.location.lat))


def main():
    for p in (PBF,):
        if not os.path.exists(p):
            fail = f"PBF fehlt: {PBF}";
            print(f"FEHLER: {fail}", file=sys.stderr)
            sys.exit(1)

    h = AdressenHandler()
    h.apply_file(PBF)
    rows = h.rows
    print(f"Adressen extrahiert: {len(rows)}")

    # Eine Koordinate pro (Straße, Hausnummer) – OSM kann mehrere Knoten pro
    # Adresse liefern (Eingang, Gebäudezentrum); Duplikate würden die
    # Vorschlagsliste sonst mit identischen Einträgen füllen.
    seen = set()
    dedup = []
    for s, hnr, lon, lat in rows:
        if (s, hnr) in seen:
            continue
        seen.add((s, hnr))
        dedup.append((s, hnr, lon, lat))
    print(f"dedupliziert: {len(dedup)}")

    with open(CSV, "w", newline="", encoding="utf-8") as f:
        import csv
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_HEADER)
        w.writerows(dedup)

    con = duckdb.connect(DEPOT_DB)
    con.execute("""CREATE OR REPLACE TABLE adressen AS
        SELECT strasse, hnr, lon, lat
        FROM read_csv_auto(?, delim=';', header=true)""", [CSV])
    n = con.execute("SELECT count(*) FROM adressen").fetchone()[0]
    str_n = con.execute("SELECT count(DISTINCT strasse) FROM adressen").fetchone()[0]
    con.close()
    print(f"Tabelle 'adressen' geschrieben: {n} Adressen, {str_n} Straßen")


if __name__ == "__main__":
    main()
