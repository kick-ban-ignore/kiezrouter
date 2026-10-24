#!/usr/bin/env python3
"""Pipeline Region Berlin (Ortsteil-Ebene).

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/run_pipeline_berlin.py
"""
import os
import sys
import csv
import duckdb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
OUT = os.path.join(REPO, "outputs", "data")

ADMIN = os.path.join(DATA, "gis_osm_adminareas_a_free_1.shp")
ROADS = os.path.join(DATA, "gis_osm_roads_free_1.shp")
POP = os.path.join(DATA, "ortsteile_einwohner.csv")
DB = os.path.join(DATA, "depot_berlin.duckdb")

SCENARIOS = [("B20", 20), ("B40", 40)]

METRIC_COLS = [
    "scenario", "anz_ot", "anz_depot", "ewz_gesamt", "dist_gew_km",
    "dist_mittel_km", "dist_max_km", "pct_pop_5km", "pct_pop_10km",
    "pct_pop_autobahn_5km",
]


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


def greedy_kmedian(con, k):
    """Greedy p-Median über die Ortsteile: wählt k Depot-Ortsteile,
    die die einwohnergewichtete mittlere Distanz minimieren.
    Rückgabe: (Auswahl, Trace [(Schritt, Ortsteil, gew. Distanz)])."""
    cand = [r[0] for r in
            con.execute("SELECT ortsteil FROM ortsteile ORDER BY ewz DESC").fetchall()]
    sel: list[str] = []
    trace: list[tuple] = []
    while len(sel) < k:
        best_c, best_score = None, None
        for c in cand:
            if c in sel:
                continue
            in_list = ",".join(f"'{a}'" for a in sel + [c])
            q = f"""
                SELECT sum(ewz * md) / sum(ewz) FROM (
                    SELECT g.ewz, min(d.dist_km) AS md
                    FROM ot_kandidat_dist d
                    JOIN ortsteile g USING (ortsteil)
                    WHERE d.depot_ags IN ({in_list})
                    GROUP BY g.ortsteil, g.ewz
                )
            """
            score = con.execute(q).fetchone()[0]
            if best_score is None or score < best_score:
                best_score, best_c = score, c
        sel.append(best_c)
        trace.append((len(sel), best_c, best_score))
        print(f"    {len(sel)}/{k}: {best_c} (gew. mittl. Distanz {best_score:.1f} km)")
    return sel, trace


def check_sources():
    for p in (ADMIN, ROADS, POP):
        if not os.path.exists(p):
            fail(f"Quelldatei fehlt: {p}\nSiehe README: data/download.sh – Region Berlin.")


def run_sql(con, name):
    with open(os.path.join(REPO, "sql", name)) as f:
        branches = split_sql(f.read())
        print(f"--> {name} ({len(branches)} Anweisungen)")
        for stmt in branches:
            con.execute(stmt)


def main():
    check_sources()
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect(DB)
    con.execute("INSTALL spatial; LOAD spatial;")

    con.execute(f"SET VARIABLE admin_path = '{ADMIN}'")
    con.execute(f"SET VARIABLE roads_path = '{ROADS}'")
    con.execute(f"SET VARIABLE pop_path = '{POP}'")

    for script in ("b01_load.sql", "b02_prepare.sql"):
        run_sql(con, script)

    # Konsistenz: ohne Einwohnerzahl gelöste Namen müssen leer sein
    missing = con.execute(
        "SELECT ortsteil, ewz FROM ortsteile WHERE ewz IS NULL").fetchall()
    if missing:
        fail(f"Keine Einwohnerzahl gefunden für: {missing}")

    con.execute("""
        CREATE OR REPLACE TABLE ot_kandidat_dist AS
        SELECT g.ortsteil, d.ortsteil AS depot_ags,
               ST_Distance(g.centroid, d.centroid) / 1000.0 AS dist_km
        FROM ortsteile g CROSS JOIN ortsteile d
    """)

    for scen, k in SCENARIOS:
        print(f"--> Greedy-Auswahl {scen} (k={k})")
        sel, trace = greedy_kmedian(con, k)
        with open(os.path.join(OUT, f"{scen}_greedy_verlauf.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["schritt", "ortsteil", "gew_mittel_distanz_km"])
            w.writerows(trace)
        sel_list = "[" + ",".join(f"'{a}'" for a in sel) + "]"
        con.execute(f"SET VARIABLE selected_ots = {sel_list}")
        con.execute(f"SET VARIABLE scenario_name = '{scen}'")

        for script in ("b03_allocation.sql", "b04_metrics.sql"):
            run_sql(con, script)

        pre = os.path.join(OUT, f"{scen}_")
        con.execute(f"COPY zuordnung TO '{pre}zuordnung.csv' (HEADER, DELIMITER ';')")
        con.execute(f"COPY depot_stats TO '{pre}depot_stats.csv' (HEADER, DELIMITER ';')")
        con.execute(f"COPY unterversorgt TO '{pre}unterversorgt.csv' (HEADER, DELIMITER ';')")
        con.execute(f"COPY risiko_ortsteile TO '{pre}risiko_ortsteile.csv' (HEADER, DELIMITER ';')")

        bins = con.execute(f"""
            SELECT '{scen}' AS scenario,
                   floor(dist_km / 2.0) * 2 AS bin_km,
                   sum(ewz) AS ewz
            FROM zuordnung GROUP BY 1, 2 ORDER BY 2
        """).fetchall()
        with open(os.path.join(OUT, f"{scen}_distanzverteilung.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["scenario", "bin_km", "ewz"])
            w.writerows(bins)

    con.execute(f"""
        COPY scenario_metrics TO '{OUT}/szenario_kennzahlen_berlin.csv'
        (HEADER, DELIMITER ';')
    """)
    con.execute(f"""
        COPY (SELECT * FROM ot_autobahn ORDER BY abstand_autobahn_km DESC)
             TO '{OUT}/autobahnaehe_berlin.csv' (HEADER, DELIMITER ';')
    """)

    rows = con.execute("SELECT * FROM scenario_metrics ORDER BY scenario").fetchall()
    print("--> Fertig. Kennzahlen je Szenario (Region Berlin):")
    print(" | ".join(METRIC_COLS))
    for row in rows:
        print(" | ".join(f"{x:,.1f}" if isinstance(x, float) else str(x) for x in row))


if __name__ == "__main__":
    main()
