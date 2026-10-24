#!/usr/bin/env python3
"""Pipeline „15-Minuten-Stadt Berlin" (Ortsteil-Ebene).

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/run_pipeline_15min.py
"""
import os
import sys
import csv
import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
OUT = os.path.join(REPO, "outputs", "data")

POIS = os.path.join(DATA, "gis_osm_pois_free_1.shp")
TRANSPORT = os.path.join(DATA, "gis_osm_transport_free_1.shp")
DEPOT_DB = os.path.join(DATA, "depot_berlin.duckdb")
DB = os.path.join(DATA, "access_15min.duckdb")


def fail(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def split_sql(sql):
    """Zerlegt SQL in Anweisungen; beachtet String-Literale und Kommentare."""
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
        sql = f.read().replace("{{ortsteil_db}}", DEPOT_DB)
        stmts = split_sql(sql)
        print(f"--> {name} ({len(stmts)} Anweisungen)")
        for stmt in stmts:
            con.execute(stmt)


def main():
    for p in (POIS, TRANSPORT, DEPOT_DB):
        if not os.path.exists(p):
            fail(f"Quelldatei fehlt: {p}\nbash data/download.sh ausführen (README).")
    os.makedirs(OUT, exist_ok=True)

    con = duckdb.connect(DB)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"SET VARIABLE pois_path = '{POIS}'")
    con.execute(f"SET VARIABLE transport_path = '{TRANSPORT}'")
    con.execute(f"SET VARIABLE ortsteil_db = '{DEPOT_DB}'")

    for script in ("d01_15min_load.sql", "d02_15min_score.sql"):
        run_sql(con, script)

    # Seiten-Konsistenz: jede Kategorie muss belegt sein, sonst Abbruch
    kat = con.execute("SELECT kategorie, count(*) FROM buffer_vereinigt GROUP BY 1").fetchall()
    if len(kat) != 5:
        fail(f"Erwartet 5 Kategorien, gefunden: {kat}")

    con.execute(f"""
        COPY stadt_summary TO '{OUT}/stadt_summary_15min.csv' (HEADER, DELIMITER ';')
    """)
    con.execute(f"""
        COPY (SELECT * FROM ortsteil_abdeckung ORDER BY score)
             TO '{OUT}/ortsteil_abdeckung_15min.csv' (HEADER, DELIMITER ';')
    """)
    con.execute(f"""
        COPY mangel_ortsteile TO '{OUT}/mangel_ortsteile_15min.csv' (HEADER, DELIMITER ';')
    """)

    print("--> Fertig. Stadtweite Kennzahlen (Einwohnergewicht zuerst):")
    for row in con.execute("SELECT * FROM stadt_summary ORDER BY 2").fetchall():
        print("  ", row)
    count = con.execute("SELECT count(*), sum(ewz) FROM mangel_ortsteile").fetchone()
    print(f"    Mangel-Ortsteile (Score < 0,5): {count[0]} mit {count[1]:,} Einw.")


if __name__ == "__main__":
    main()
