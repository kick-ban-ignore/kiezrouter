#!/usr/bin/env python3
"""Diagramme + Karten für die Region Berlin (Ortsteil-Ebene).

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/make_berlin_visuals.py
"""
import csv
import colorsys
import json
import os
import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDATA = os.path.join(REPO, "outputs", "data")
OUTCHART = os.path.join(REPO, "outputs", "charts")
OUTMAP = os.path.join(REPO, "outputs", "maps")
DB = os.path.join(REPO, "data", "berlin", "depot_berlin.duckdb")

plt.rcParams["font.family"] = "DejaVu Sans"


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def palette(n):
    """n unterscheidbare Farben (goldener Winkel im HSV-Raum)."""
    out = []
    for i in range(n):
        h = (i * 0.618033988749895) % 1.0
        s = 0.62 if i % 2 else 0.85
        v = 0.95 if i % 3 else 0.80
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


def abdeckungskurve():
    plt.figure(figsize=(8, 4.8))
    for scen in ("B40", "B20"):
        rows = read_csv(os.path.join(OUTDATA, f"{scen}_distanzverteilung.csv"))
        bw = sorted((float(r["bin_km"]), float(r["ewz"])) for r in rows)
        total = sum(e for _, e in bw)
        xs, ys, cum = [], [], 0.0
        for km, e in bw:
            cum += e
            xs.append(km + 2)
            ys.append(100.0 * cum / total)
        plt.plot(xs[1:], ys[1:], marker="o", label=f"k = {scen[1:]} Depots")
    plt.xlabel("Entfernung zum nächstgelegenen Depot (km, Bandobergrenze)")
    plt.ylabel("Anteil der Bevölkerung abgedeckt (%)")
    plt.title("Region Berlin: Abdeckungskurve (Luftlinie)")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "abdeckungskurve_berlin.png"), dpi=140)
    plt.close()


def autobahndistanzen():
    con = duckdb.connect(DB)
    con.execute("LOAD spatial;")
    bins = con.execute("""
        SELECT least(floor(abstand_autobahn_km / 2.0) * 2, 20) AS bin_km,
               sum(ewz) AS ewz
        FROM ot_autobahn GROUP BY 1 ORDER BY 1
    """).fetchall()
    con.close()
    total = sum(e for _, e in bins)
    plt.figure(figsize=(8, 4.4))
    plt.bar([int(k) for k, _ in bins], [e for _, e in bins],
            width=1.6, align="edge", edgecolor="black", linewidth=0.3)
    for k, v in bins:
        if v / total >= 0.02:
            plt.text(k + 0.9, v * 1.02, f"{100 * v / total:.1f} %".replace(".", ","),
                     ha="center", fontsize=8)
    plt.xlabel("Entfernung zum nächsten Autobahnsegment (km, Untergrenze des Bandes)")
    plt.ylabel("Einwohner im Band")
    plt.title("Region Berlin: Autobahnnähe der Bevölkerung je Ortsteil")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "autobahndistanzen_berlin.png"), dpi=140)
    plt.close()


def statisch(scen="B40"):
    con = duckdb.connect(DB)
    con.execute("LOAD spatial;")
    zuord = dict(con.execute(f"""
        SELECT ortsteil, depot_ags
        FROM read_csv_auto('{OUTDATA}/{scen}_zuordnung.csv', delim=';')
    """).fetchall())
    depotname = {r[0]: r[1] for r in con.execute(f"""
        SELECT DISTINCT depot_ags, depot_name
        FROM read_csv_auto('{OUTDATA}/{scen}_zuordnung.csv', delim=';')
    """).fetchall()}
    farben = palette(len(depotname))
    depot_farbe = {a: farben[i] for i, a in enumerate(sorted(depotname))}
    rows = con.execute("""
        SELECT ortsteil,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:25833', 'EPSG:4326', true))
        FROM ortsteile
    """).fetchall()
    depot_punkte = con.execute(f"""
        SELECT o.ortsteil,
               ST_Y(ST_Transform(o.centroid, 'EPSG:25833', 'EPSG:4326', true)),
               ST_X(ST_Transform(o.centroid, 'EPSG:25833', 'EPSG:4326', true))
        FROM ortsteile o
        WHERE o.ortsteil IN ({",".join(f"'{d}'" for d in sorted(depotname))})
    """).fetchall()
    con.close()

    from matplotlib.patches import Polygon as MplPolygon

    def patch(geom):
        if geom["type"] == "Polygon":
            yield geom["coordinates"][0]
        elif geom["type"] == "MultiPolygon":
            for p in geom["coordinates"]:
                yield p[0]

    fig, ax = plt.subplots(figsize=(10.5, 10.5))
    for ort, gj in rows:
        if ort not in zuord:
            continue
        fc = depot_farbe[zuord[ort]]
        for ring in patch(json.loads(gj)):
            ax.add_patch(MplPolygon(ring, closed=True, facecolor=fc,
                                    edgecolor="#555555", linewidth=0.25))
    for dep, lat, lon in depot_punkte:
        ax.plot(lon, lat, "k*", markersize=11, zorder=5)
        ax.annotate(dep, (lon, lat), xytext=(3, 3), textcoords="offset points",
                    fontsize=6.8, fontweight="bold", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.12", fc="white",
                              ec="none", alpha=0.7))
    ax.set_aspect(1.0)
    ax.set_title(f"Region Berlin – Szenario {scen[1:]}, k = {scen[1:]} Depots\n"
                 "(Ortsteile farbig nach Depot; * = Depot)", fontsize=11)
    ax.set_xlabel("Längengrad")
    ax.set_ylabel("Breitengrad")
    handles = [plt.Line2D([], [], marker="s", linestyle="", markersize=8,
                          color=depot_farbe[a], label=depotname[a])
               for a in sorted(depotname)]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.08),
              fontsize=6.5, ncols=5, title=f"Depot-Standorte (k = {scen[1:]})")
    ax.grid(True, linewidth=0.3, alpha=0.35)
    fig.tight_layout()
    out = os.path.join(OUTMAP, f"einzugsgebiete_{scen.lower()}_statisch.png")
    fig.savefig(out, dpi=145)
    plt.close(fig)
    print(f"  {out}")


def interaktiv(scen="B40"):
    import folium
    con = duckdb.connect(DB)
    con.execute("LOAD spatial;")
    zuord = dict(con.execute(f"""
        SELECT ortsteil, depot_ags
        FROM read_csv_auto('{OUTDATA}/{scen}_zuordnung.csv', delim=';')
    """).fetchall())
    depotname = {r[0]: r[1] for r in con.execute(f"""
        SELECT DISTINCT depot_ags, depot_name
        FROM read_csv_auto('{OUTDATA}/{scen}_zuordnung.csv', delim=';')
    """).fetchall()}
    farben = palette(len(depotname))
    depot_farbe = {a: farben[i] for i, a in enumerate(sorted(depotname))}
    rows = con.execute("""
        SELECT ortsteil, ewz,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:25833', 'EPSG:4326', true))
        FROM ortsteile
    """).fetchall()
    punkte = con.execute(f"""
        SELECT ortsteil,
               ST_Y(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true)),
               ST_X(ST_Transform(centroid, 'EPSG:25833', 'EPSG:4326', true))
        FROM ortsteile
        WHERE ortsteil IN ({",".join(f"'{d}'" for d in sorted(depotname))})
    """).fetchall()
    con.close()

    m = folium.Map(location=[52.5, 13.4], zoom_start=10, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        name="Esri Grau",
        attr="Tiles © Esri – Quellen: Esri, USGS",
        max_zoom=18,
    ).add_to(m)
    folium.TileLayer(
        tiles="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
        name="OpenStreetMap (DE)",
        attr="© OpenStreetMap-Mitwirkende (FOSSGIS-Server)",
        max_zoom=18,
        show=False,
    ).add_to(m)
    for ort, ewz, gj in rows:
        dep = zuord.get(ort)
        if dep is None:
            continue
        folium.GeoJson(
            json.loads(gj),
            style_function=lambda feature, c=depot_farbe[dep]: {
                "fillColor": c, "color": "#666", "weight": 0.35,
                "fillOpacity": 0.4,
            },
            highlight_function=lambda feature: {"weight": 2},
            tooltip=f"{ort} – {int(ewz):,} Einw. ".replace(",", ".")
                    + f"→ Depot {depotname[dep]}",
        ).add_to(m)
    for dep, lat, lon in punkte:
        folium.Marker(
            location=[lat, lon],
            icon=folium.Icon(color="black", icon_color=depot_farbe[dep],
                             icon="industry", prefix="fa"),
            tooltip=f"Depot {depotname[dep]}",
        ).add_to(m)
    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    m.save(os.path.join(OUTMAP, f"einzugsgebiete_{scen.lower()}.html"))
    print(f"  {OUTMAP}/einzugsgebiete_{scen.lower()}.html")


def grenznutzen():
    """Grenznutzen je weiterem Depot: aus Greedy-Verlauf (Diminishing Returns)."""
    rows = read_csv(os.path.join(OUTDATA, "B40_greedy_verlauf.csv"))
    ks = [int(r["schritt"]) for r in rows]
    y = [float(r["gew_mittel_distanz_km"]) for r in rows]
    deltas = [abs(y[i] - y[i - 1]) for i in range(1, len(y))]

    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax1.plot(ks, y, "o-", color="#1f77b4", label="einwohnergew. mittlere Distanz")
    ax1.set_xlabel("Anzahl Depots k (Reihenfolge Greedy-Auswahl)")
    ax1.set_ylabel("einwohnergewichtete mittlere Distanz (km)")
    ax1.set_title("Grenznutzen je weiterem Depot: Kurve flacht ab (Diminishing Returns)")
    ax2 = ax1.twinx()
    ax2.bar(ks[1:], deltas, alpha=0.35, color="#d62728",
            label="Verbesserung durch Depot Nr. k")
    ax2.set_ylabel("Verbesserung gegenüber k-1 (km)")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper right")
    ax1.grid(alpha=0.3)
    fig.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "grenznutzen.png"), dpi=140)
    plt.close(fig)


def auslastung():
    """Balance der Einzugsgebiete: Personal-/Auslastungsplanung pro Depot."""
    rows = read_csv(os.path.join(OUTDATA, "B40_depot_stats.csv"))
    rows.sort(key=lambda r: int(r["ewz_einzugsgebiet"]))
    names = [r["depot_name"] for r in rows]
    ewz = [int(r["ewz_einzugsgebiet"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.barh(range(len(rows)), ewz, color="#4daf4a", edgecolor="#333", linewidth=0.3)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("Einwohner im Einzugsgebiet")
    ax.set_title("Depot-Auslastungsgrad: Einwohner je Einzugsgebiet (k = 40)\n"
                 f"Balance-Verhältnis max/min ≈ {max(ewz)//min(ewz or [1]):,}"
                 .replace(",", "."))
    for i, v in enumerate(ewz):
        ax.text(v + max(ewz) * 0.01, i, f"{v:,}".replace(",", "."), va="center", fontsize=6)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    plt.savefig(os.path.join(OUTCHART, "auslastung.png"), dpi=140)
    plt.close(fig)


def main():
    os.makedirs(OUTCHART, exist_ok=True)
    os.makedirs(OUTMAP, exist_ok=True)
    abdeckungskurve()
    autobahndistanzen()
    grenznutzen()
    auslastung()
    for scen in ("B40", "B20"):
        statisch(scen)
        interaktiv(scen)
    print("Visuals Region Berlin erzeugt.")


if __name__ == "__main__":
    main()
