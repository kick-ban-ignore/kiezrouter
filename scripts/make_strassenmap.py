#!/usr/bin/env python3
"""Rendert das Straßennetz von Berlin: statische Karte (PNG) und
interaktive Karte (HTML, Hauptweiter zusätzlich).

Ausführen im Repo-Wurzelverzeichnis:  .venv/bin/python scripts/make_strassenmap.py
"""
import json
import math
import os
import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "berlin")
OUTMAP = os.path.join(REPO, "outputs", "maps")
ROADS = os.path.join(DATA, "gis_osm_roads_free_1.shp")

plt.rcParams["font.family"] = "DejaVu Sans"

CLASS_COLORS = {
    "motorway": "#e34f4f", "motorway_link": "#e34f4f",
    "trunk": "#f0932b", "trunk_link": "#f0932b",
    "primary": "#f7d038", "primary_link": "#f7d038",
    "secondary": "#fdff9a", "secondary_link": "#fdff9a",
    "tertiary": "#9fbaff", "tertiary_link": "#9fbaff",
    "unclassified": "#dfe4ea", "residential": "#dfe4ea",
    "living_street": "#dfe4ea", "service": "#c8cdd4",
    "footway": "#7a8ba3", "path": "#7a8ba3", "cycleway": "#5b708c",
    "steps": "#7a8ba3", "track_grade4": "#a9b2c1",
}


def statisch():
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    rows = con.execute(f"""
        SELECT fclass,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true))
        FROM st_read('{ROADS}')
    """).fetchall()
    con.close()

    segments, colors = [], []
    startx = starty = float("inf")
    endx = endy = -float("inf")
    for fclass, gj in rows:
        ring = json.loads(gj)["coordinates"]
        col = CLASS_COLORS.get(fclass, "#dfe4ea")
        for a, b in zip(ring, ring[1:]):
            segments.append(((a[0], a[1]), (b[0], b[1])))
            colors.append(col)
            startx = min(startx, a[0], b[0]); endx = max(endx, a[0], b[0])
            starty = min(starty, a[1], b[1]); endy = max(endy, a[1], b[1])

    fig, ax = plt.subplots(figsize=(12, 12), facecolor="#1b1f27")
    ax.set_facecolor("#1b1f27")
    lc = LineCollection(segments, colors=colors, linewidths=0.45)
    ax.add_collection(lc)
    if not segments:
        raise SystemExit("FEHLER: keine Segmente gebaut – Datenprüfung nötig.")
    print(f"  Segmente: {len(segments):,} | X {startx:.0f}–{endx:.0f} | Y {starty:.0f}–{endy:.0f}")
    ax.set_xlim(startx, endx)
    ax.set_ylim(starty, endy)
    ax.set_aspect(1.0)

    # Einbahn-Markierung: weiß gestrichelte Überlappung + Richtungspfeile
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    owa = con.execute(f"""
        SELECT fclass,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true))
        FROM st_read('{ROADS}')
        WHERE oneway IN ('F', 'T')
          AND fclass IN ('motorway','motorway_link','trunk','trunk_link',
                         'primary','primary_link','secondary','secondary_link',
                         'tertiary','tertiary_link','unclassified','residential',
                         'living_street','service')
    """).fetchall()
    con.close()
    dashed = []
    qx, qy, qdx, qdy = [], [], [], []
    for fclass, gj in owa:
        ring = json.loads(gj)["coordinates"]
        for a, b in zip(ring, ring[1:]):
            dashed.append(((a[0], a[1]), (b[0], b[1])))
        dxway = ring[-1][0] - ring[0][0]
        dyway = ring[-1][1] - ring[0][1]
        if math.hypot(dxway, dyway) < 5:
            continue
        import math as _m
        norm = _m.hypot(dxway, dyway)
        qx.append((ring[0][0] + ring[-1][0]) / 2)
        qy.append((ring[0][1] + ring[-1][1]) / 2)
        qdx.append(dxway / norm * 180)
        qdy.append(dyway / norm * 180)
    dashed_lc = LineCollection(dashed, colors="#ffffff", linewidths=0.45,
                               linestyles=(0, (5, 4)), alpha=0.75)
    ax.add_collection(dashed_lc)
    ax.quiver(qx, qy, qdx, qdy, color="#ffd166",
              angles="xy", scale_units="xy", scale=1, width=0.002,
              headwidth=4, headlength=5, headaxislength=4.5, alpha=0.9)
    print(f"  Einbahn-Ways mit Markierung: {len(qx):,}")

    ax.set_title("Straßennetz Berlin nach Klasse; gestrichelt weiß + Pfeile = Einbahn",
                 color="white", fontsize=11, pad=12)
    ax.set_xlabel("Easting (EPSG:25833, m)", color="white")
    ax.set_ylabel("Northing (EPSG:25833, m)", color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#555")
    ax.grid(True, color="#33363d", linewidth=0.3)
    fig.tight_layout()
    out = os.path.join(OUTMAP, "strassennetz_statisch.png")
    fig.savefig(out, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  {out}")


def interaktiv():
    import folium
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    rows = con.execute(f"""
        SELECT fclass,
               ST_AsGeoJSON(ST_Transform(geom, 'EPSG:4326', 'EPSG:4326', true))
        FROM st_read('{ROADS}')
        WHERE fclass IN ('motorway', 'motorway_link', 'trunk', 'trunk_link',
                         'primary', 'primary_link', 'secondary', 'secondary_link')
    """).fetchall()
    con.close()

    m = folium.Map(location=[52.52, 13.40], zoom_start=11, tiles=None,
                   prefer_canvas=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        name="Esri Dunkel",
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

    width = {"motorway": 4, "motorway_link": 2.5, "trunk": 3.5, "trunk_link": 2,
             "primary": 2.5, "primary_link": 1.5, "secondary": 1.8,
             "secondary_link": 1}
    for fclass, gj in rows:
        ring = json.loads(gj)["coordinates"]
        folium.PolyLine(
            [(lat, lon) for lon, lat in ring],
            color=CLASS_COLORS.get(fclass, "#888"),
            weight=width.get(fclass, 1),
            opacity=0.85,
            tooltip=f"{fclass}",
        ).add_to(m)

    # Einbahn-Aufsicht: weiß gestrichelt (alle Fahrzeugklassen, Canvas-Renderer)
    con = duckdb.connect()
    con.execute("LOAD spatial;")
    owa = con.execute(f"""
        SELECT fclass, ST_AsGeoJSON(geom)
        FROM st_read('{ROADS}')
        WHERE oneway IN ('F', 'T')
          AND fclass IN ('motorway','motorway_link','trunk','trunk_link',
                         'primary','primary_link','secondary','secondary_link',
                         'tertiary','tertiary_link','unclassified','residential',
                         'living_street','service')
    """).fetchall()
    con.close()
    n_ein = 0
    for fclass, gj in owa:
        ring = json.loads(gj)["coordinates"]
        folium.PolyLine(
            [(lat, lon) for lon, lat in ring],
            color="#ffffff", weight=1.2, opacity=0.6, dashArray="4 6",
            tooltip=f"Einbahnstraße ({fclass})",
        ).add_to(m)
        n_ein += 1
    print(f"  Einbahn-PolyLines: {n_ein:,}")
    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    out = os.path.join(OUTMAP, "strassennetz.html")
    m.save(out)
    print(f"  {out}")


def main():
    os.makedirs(OUTMAP, exist_ok=True)
    statisch()
    interaktiv()


if __name__ == "__main__":
    main()
