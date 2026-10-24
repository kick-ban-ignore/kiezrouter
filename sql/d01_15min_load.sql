-- d01_15min_load.sql (15-Minuten-Stadt)
-- Wesentliche Alltags-Dienstleistungen (POIs) und Verkehrsknoten aus dem
-- Geofabrik-Berlin-Extract; projiziert auf EPSG:25833.
-- verwendet Variablen: pois_path, transport_path
-- ortsteile werden per ATTACH aus der Bestands-DB gelesen; Treiber ersetzt
-- den Platzhalter {{ortsteil_db}} durch den absoluten DB-Pfad.

ATTACH IF NOT EXISTS '{{ortsteil_db}}' AS depot;

CREATE OR REPLACE TABLE poi_essentials AS
SELECT
    CASE fclass
        WHEN 'supermarket'                THEN 'essen'
        WHEN 'pharmacy'                   THEN 'apotheke'
        WHEN 'doctor'                     THEN 'arzt'
        WHEN 'clinic'                     THEN 'arzt'
        WHEN 'school'                     THEN 'schule'
        ELSE fclass
    END AS kategorie,
    name,
    ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('pois_path'))
WHERE fclass IN ('supermarket', 'pharmacy', 'doctor', 'clinic', 'school');

CREATE OR REPLACE TABLE transit AS
SELECT fclass AS kategorie_raw, name,
       ST_Transform(geom, 'EPSG:4326', 'EPSG:25833', true) AS geom
FROM st_read(getvariable('transport_path'))
WHERE fclass IN ('railway_station', 'tram_stop');
-- ÖPNV = schienengebundenes Netz (U/S-Bahn sowie Tram). bus_stop (6.498 Haltestellen)
-- wird bewusst ausgeschlossen: ihre flächendeckende Dichte würde die Kategorie
-- überall auf ~100 % bringen und aussagelos machen.
