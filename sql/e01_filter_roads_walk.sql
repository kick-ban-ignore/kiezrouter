-- e01_filter_roads_walk.sql (Fußgänger-Routenanalyse)
-- Fußgängertaugliche Klassen persistieren (EPSG:25833).
-- verwendet Variablen: roads_path
-- default_v_kmh ist Platzhalter; die Gehgeschwindigkeit wird im Treiber gesetzt.

CREATE OR REPLACE TABLE roh_kanten AS
SELECT
    fclass, osm_id, name, oneway, maxspeed, bridge, tunnel,
    ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('roads_path'))
WHERE fclass IN (
    'footway', 'path', 'pedestrian', 'steps',
    'track', 'track_grade1', 'track_grade2', 'track_grade3', 'track_grade4', 'track_grade5',
    'bridleway', 'cycleway', 'living_street',
    'residential', 'service', 'unclassified',
    'tertiary', 'tertiary_link', 'secondary', 'secondary_link',
    'primary', 'primary_link'
);

CREATE OR REPLACE TABLE kanten AS
SELECT
    fclass, osm_id, name, oneway, bridge, tunnel, maxspeed,
    5 AS default_v_kmh,
    round(ST_Length(geom), 1) AS length_m,
    ST_AsGeoJSON(geom) AS geojson
FROM roh_kanten;