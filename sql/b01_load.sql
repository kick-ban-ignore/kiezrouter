-- b01_load.sql (Region Berlin)
-- Bezirk- und Ortsteilgrenzen (OSM admin areas, Geofabrik Berlin-Extract) und
-- amtliche Einwohnerzahlen je Ortsteil (Statistik Berlin-Brandenburg, XLSX).
-- verwendet Variablen: admin_path, roads_path, pop_path

CREATE OR REPLACE TABLE ot_pop AS
SELECT * FROM read_csv_auto(getvariable('pop_path'), delim=';', header=true);

CREATE OR REPLACE TABLE berlin_staat AS
SELECT geom
FROM st_read(getvariable('admin_path'))
WHERE code = 1204;

CREATE OR REPLACE TABLE bezirke AS
SELECT a.name AS bezirk_name,
       ST_Transform(a.geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('admin_path')) a, berlin_staat b
WHERE a.code = 1209
  AND ST_Contains(b.geom, ST_Centroid(a.geom));

CREATE OR REPLACE TABLE ortsteile AS
WITH basis AS (
    SELECT a.name AS ortsteil_osm,
           ST_Transform(a.geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
    FROM st_read(getvariable('admin_path')) a, berlin_staat b
    WHERE a.code = 1210
      AND ST_Contains(b.geom, ST_Centroid(a.geom))
)
SELECT
    b.ortsteil_osm                  AS ortsteil,
    p.ewz                           AS ewz,
    z.bezirk_name                   AS bezirk,
    b.geom                          AS geom,
    ST_Centroid(b.geom)             AS centroid
FROM basis b
LEFT JOIN ot_pop p
  ON b.ortsteil_osm = p.ortsteil
    LEFT JOIN bezirke z
      ON ST_Contains(z.geom, ST_Centroid(b.geom));

CREATE OR REPLACE TABLE motorway AS
SELECT fclass, name, ref,
       ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('roads_path'))
WHERE fclass IN ('motorway', 'motorway_link', 'trunk', 'trunk_link');
