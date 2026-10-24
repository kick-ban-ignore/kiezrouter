-- b03_allocation.sql (Region Berlin)
-- Ordnet jedem Ortsteil das nächstgelegene Depot zu (Distanz Ortsteil-Zentroid,
-- EPSG:25833, Luftlinie).
-- verwendet Variablen: selected_ots (Liste der Ortsteile mit Depots), scenario_name

CREATE OR REPLACE TABLE zuordnung AS
WITH d AS (
    SELECT
        g.ortsteil              AS ortsteil,
        g.ewz                   AS ewz,
        g.bezirk                AS bezirk,
        dep.ortsteil            AS depot_ags,
        dep.ortsteil            AS depot_name,
        ST_Distance(g.centroid, dep.centroid) / 1000.0 AS dist_km
    FROM ortsteile g
    CROSS JOIN ortsteile dep
    WHERE list_contains(getvariable('selected_ots'), dep.ortsteil)
),
best AS (
    SELECT DISTINCT ON (ortsteil)
        ortsteil, depot_ags, depot_name, dist_km
    FROM d
    ORDER BY ortsteil, dist_km
)
SELECT
    b.ortsteil, b.depot_ags, b.depot_name, b.dist_km,
    g.ewz, g.bezirk,
    A.abstand_autobahn_km
FROM best b
JOIN ortsteile g USING (ortsteil)
LEFT JOIN ot_autobahn A USING (ortsteil);
