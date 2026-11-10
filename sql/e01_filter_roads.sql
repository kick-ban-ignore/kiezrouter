-- e01_filter_roads.sql (Routenanalyse)
-- Fahrzeugtaugliche Klassen persistieren (EPSG:25833), Länge + Zeitkosten je Feature.
-- verwendet Variablen: roads_path
-- Geschwindigkeiten: maxspeed wenn plausibel (5..130), sonst Klassen-Default.

CREATE OR REPLACE TABLE roh_kanten AS
SELECT
    fclass, osm_id, name, oneway, maxspeed, bridge, tunnel,
    ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('roads_path'))
WHERE fclass IN (
    'motorway', 'motorway_link', 'trunk', 'trunk_link',
    'primary', 'primary_link', 'secondary', 'secondary_link',
    'tertiary', 'tertiary_link', 'unclassified', 'residential',
    'living_street', 'service'
);

CREATE OR REPLACE TABLE kanten AS
SELECT
    fclass, osm_id, name, oneway, bridge, tunnel, maxspeed,
    CASE fclass
        WHEN 'motorway' THEN 100
        WHEN 'motorway_link' THEN 60
        WHEN 'trunk' THEN 80
        WHEN 'trunk_link' THEN 50
        WHEN 'primary' THEN 50
        WHEN 'primary_link' THEN 30
        WHEN 'secondary' THEN 50
        WHEN 'secondary_link' THEN 30
        WHEN 'tertiary' THEN 30
        WHEN 'tertiary_link' THEN 30
        WHEN 'unclassified' THEN 30
        WHEN 'residential' THEN 30
        WHEN 'living_street' THEN 20
        ELSE 20   -- service
    END AS default_v_kmh,
    round(ST_Length(geom), 1) AS length_m,
    ST_AsGeoJSON(geom) AS geojson
FROM roh_kanten;
