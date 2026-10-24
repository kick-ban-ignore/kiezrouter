#!/usr/bin/env python3
"""Diagramm + statische Karte für die 15-Minuten-Stadt-Analyse.

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/make_15min_visuals.py
"""
import csv
import json
import os
import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.colors import LinearSegmentedColormap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
OUTDATA = os.path.join(REPO, "outputs", "data")
OUTCHART = os.path.join(REPO, "outputs", "charts")
OUTMAP = os.path.join(REPO, "outputs", "maps")
DB = os.path.join(DATA, "depot_berlin.duckdb")

plt.rcParams["font.family"] = "DejaVu Sans"


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def score_verteilung():
    rows = read_csv(os.path.join(OUTDATA, "ortsteil_abdeckung_15min.csv"))
    scores = [float(r["score"]) for r in rows]
    ewz = [int(float(r["ewz"])) for r in rows]
    mangel = {r["ortsteil"] for r in
              read_csv(os.path.join(OUTDATA, "mangel_ortsteile_15min.csv"))}
    colors = ["#d73027" if r["ortsteil"] in mangel else "#1a9850" for r in rows]

    plt.figure(figsize=(9, 4.6))
    plt.scatter(scores, ewz, c=colors, s=26, alpha=0.75, edgecolors="none")
    plt.axvline(0.5, color="#555", linewidth=1, linestyle="--")
    plt.text(0.505, max(ewz) * 0.93, "Mangel-Schwelle (Score 0,5)",
             fontsize=8.5, color="#555")
    plt.xlabel("15-Minuten-Score des Ortsteils (Anteil der Fläche in Reichweite)")
    plt.ylabel("Einwohner des Ortsteils")
    plt.title("15-Minuten-Score je Ortsteil und Einwohnerzahl\n"
              "(rot = Mangel-Ortsteil, Score < 0,5)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "score_verteilung_15min.png"), dpi=140)
    plt.close()


def karte():
    rows = read_csv(os.path.join(OUTDATA, "ortsteil_abdeckung_15min.csv"))
    score = {r["ortsteil"]: float(r["score"]) for r in rows}
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    con.execute(f"ATTACH '{DB}' AS depot (READ_ONLY)")
    g = con.execute("""
        SELECT ortsteil,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:25833', 'EPSG:4326', true))
        FROM depot.ortsteile
    """).fetchall()
    koords = {r[0]: (r[1], r[2]) for r in con.execute("""
        SELECT ortsteil,
               ST_X(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true)),
               ST_Y(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true))
        FROM depot.ortsteile
    """).fetchall()}
    con.close()

    cmap = LinearSegmentedColormap.from_list(
        "vielfalt", ["#b2182b", "#f4a582", "#fdf5e6", "#a6d96a", "#1a9850"])
    fig, ax = plt.subplots(figsize=(10.5, 10.5))

    for ort, gj in g:
        if ort not in score:
            continue
        s = score[ort]
        fc = cmap(min(max(s, 0.0), 1.0))
        geom = json.loads(gj)
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ax.add_patch(MplPolygon(poly[0], closed=True, facecolor=fc,
                                    edgecolor="#777", linewidth=0.25))

    # die fünf schlechtesten Ortsteile betiteln
    mangel = read_csv(os.path.join(OUTDATA, "mangel_ortsteile_15min.csv"))[:5]
    for r in mangel:
        ort = r["ortsteil"]
        if ort in koords:
            lon, lat = koords[ort]
            ax.annotate(f"{ort}\nScore {float(r['score']):.2f}".replace(".", ","),
                        (lon, lat), ha="center", fontsize=8, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white",
                                  ec="#b2182b", alpha=0.85))
    ax.set_aspect(1.0)
    # Patches skalieren die Achsen nicht automatisch – explizite Grenzen setzen
    xs, ys = [], []
    for _, gj in g:
        geom = json.loads(gj)
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for x, y in poly[0]:
                xs.append(x)
                ys.append(y)
    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_title("15-Minuten-Stadt Berlin – Abdeckung je Ortsteil\n"
                 "durchschnittlicher Anteil der Ortsteilfläche in Gehreichweite\n"
                 "(5 Kategorien gemittelt: Einkauf, Apotheke, Klinik, Schule, Schiene)",
                 fontsize=11)
    ax.set_xlabel("Längengrad")
    ax.set_ylabel("Breitengrad")
    sm = plt.cm.ScalarMappable(cmap=cmap,
                               norm=plt.Normalize(vmin=0, vmax=1))
    cbar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("Score (0 = keine Reichweite, 1 = vollständig)")
    ax.grid(True, linewidth=0.25, alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUTMAP, "versorgung_15min_statisch.png")
    fig.savefig(out, dpi=145)
    plt.close(fig)
    print(f"  {out}")


def main():
    os.makedirs(OUTCHART, exist_ok=True)
    os.makedirs(OUTMAP, exist_ok=True)
    score_verteilung()
    karte()
    print("Visuals 15-Minuten-Stadt erzeugt.")


if __name__ == "__main__":
    main()
