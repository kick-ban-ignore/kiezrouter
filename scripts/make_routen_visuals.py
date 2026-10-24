#!/usr/bin/env python3
"""Visuals für die Routenanalyse (Inflation, Abdeckung, Fahrzeit-Karte).

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/make_routen_visuals.py
"""
import csv
import colorsys
import json
import os
import statistics
import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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


def inflation():
    rows = read_csv(os.path.join(OUTDATA, "routen_B40_matrix.csv"))
    xs = [float(r["luft_km"]) for r in rows]
    ys = [float(r["strasse_km"]) for r in rows]
    infl = [float(r["inflation_faktor"]) for r in rows if float(r["luft_km"]) > 0.2]
    plt.figure(figsize=(8, 5))
    plt.scatter(xs, ys, s=28, alpha=0.8, color="#1f77b4",
                edgecolors="white", linewidth=0.5)
    hi = max(max(xs), max(ys)) * 1.05
    plt.plot([0, hi], [0, hi], color="#888", linewidth=1, label="1:1-Linie")
    plt.plot([0, hi], [0, 1.3 * hi], color="#d62728", linewidth=1,
             linestyle="--", label="1,3-faktorierte Linie (urbane Referenz)")
    plt.text(0.03, 0.93, f"mediane Inflation: {statistics.median(infl):.2f}".replace(".", ","),
             transform=plt.gca().transAxes, fontsize=10)
    plt.xlabel("Luftlinie Depot → Ortsteilzentrum (km)")
    plt.ylabel("Straßenstrecke (km)")
    plt.title("Berlin Depot-Routen: Straßenstrecken gegen Luftlinie (k = 40)")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "routen_inflation.png"), dpi=140)
    plt.close()


def abdeckung():
    szen = {}
    for scen in ("B20", "B40"):
        rows = read_csv(os.path.join(OUTDATA, f"routen_{scen}_abdeckung.csv"))
        szen[scen] = {}
        for r in rows:
            szen[scen][f"{r['typ']}|{r['band']}"] = float(r["anteil_ewz_pct"])

    plt.figure(figsize=(8, 4.8))
    xs = [f"{b} min" for b in (15, 30, 45)]
    xx = range(len(xs))
    b20 = [next(v for k, v in szen["B20"].items() if k.startswith("fahrzeit_min")
               and k.endswith(b)) for b in ("|15", "|30", "|45")]
    b40 = [next(v for k, v in szen["B40"].items() if k.startswith("fahrzeit_min")
               and k.endswith(b)) for b in ("|15", "|30", "|45")]
    w = 0.35
    plt.bar([i - w/2 for i in xx], b20, w, label="k = 20", color="#1f77b4")
    plt.bar([i + w/2 for i in xx], b40, w, label="k = 40", color="#ff7f00")
    plt.xticks(list(xx), xs)
    plt.ylabel("Anteil Einwohner im Band (%)")
    plt.title("Fahrzeit-Abdeckung je Depot-Szenario (Straßenroute)")
    plt.legend()
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "routen_abdeckung.png"), dpi=140)
    plt.close()


def karte():
    rows = read_csv(os.path.join(OUTDATA, "routen_B40_matrix.csv"))
    fmin = {r["ortsteil"]: float(r["fahrt_min"]) for r in rows}
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    con.execute(f"ATTACH '{DB}' AS depot (READ_ONLY)")
    g = con.execute("""
        SELECT ortsteil,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:25833', 'EPSG:4326', true))
        FROM depot.ortsteile
    """).fetchall()
    mitte = con.execute("""
        SELECT ortsteil,
               ST_Y(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true)),
               ST_X(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true))
        FROM depot.ortsteile
    """).fetchall()
    con.close()

    cmap = plt.get_cmap("RdYlGn_r")
    vmax = min(max(fmin.values()), 30)
    fig, ax = plt.subplots(figsize=(10.5, 10.5))
    xs, ys = [], []
    for ort, gj in g:
        if ort not in fmin:
            continue
        fc = cmap(min(fmin[ort], vmax) / vmax)
        geom = json.loads(gj)
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = poly[0]
            ax.add_patch(plt.Polygon(ring, closed=True, facecolor=fc,
                                     edgecolor="#777", linewidth=0.25))
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    slowest = sorted(fmin.items(), key=lambda kv: -kv[1])[:5]
    koords = {r[0]: (r[1], r[2]) for r in mitte}
    for ort, t in slowest:
        if ort in koords:
            lat, lon = koords[ort]
            ax.annotate(f"{ort}\n{t:.0f} min", (lon, lat), fontsize=8,
                        fontweight="bold", ha="center",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white",
                                  ec="#b2182b", alpha=0.85))
    ax.set_aspect(1.0)
    ax.set_title("Fahrzeit vom nächsten Depot je Ortsteil (k = 40, Straßenrouten)",
                 fontsize=11)
    ax.set_xlabel("Längengrad")
    ax.set_ylabel("Breitengrad")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=0, vmax=vmax))
    cbar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("Fahrzeit (min, bis 30 min dargestellt)")
    ax.grid(True, linewidth=0.25, alpha=0.3)
    fig.tight_layout()
    out = os.path.join(OUTMAP, "fahrzeit_karte_statisch.png")
    fig.savefig(out, dpi=145)
    plt.close(fig)
    print(f"  {out}")


def main():
    os.makedirs(OUTCHART, exist_ok=True)
    os.makedirs(OUTMAP, exist_ok=True)
    inflation()
    abdeckung()
    karte()
    print("Visuals Routenanalyse erzeugt.")


if __name__ == "__main__":
    main()
