-- b04_metrics.sql (Region Berlin)
-- Kennzahlen je Szenario.
-- verwendet Variablen: scenario_name

CREATE TABLE IF NOT EXISTS scenario_metrics (
    scenario                     VARCHAR,
   anz_ot                         BIGINT,
    anz_depot                    BIGINT,
    ewz_gesamt                   BIGINT,
    dist_gew_km                  DOUBLE,   -- einwohnergewichtete mittlere Distanz
    dist_mittel_km               DOUBLE,   -- ungewichtete mittlere Distanz
    dist_max_km                  DOUBLE,
    pct_pop_5km                  DOUBLE,
    pct_pop_10km                 DOUBLE,
    pct_pop_autobahn_5km         DOUBLE
);

DELETE FROM scenario_metrics WHERE scenario = getvariable('scenario_name');

INSERT INTO scenario_metrics
SELECT
    getvariable('scenario_name'),
    count(*),
    count(DISTINCT depot_ags),
    sum(ewz),
    sum(ewz * dist_km) / sum(ewz),
    avg(dist_km),
    max(dist_km),
    100.0 * sum(ewz) FILTER (WHERE dist_km             <= 5) / sum(ewz),
    100.0 * sum(ewz) FILTER (WHERE dist_km             <= 10) / sum(ewz),
    100.0 * sum(ewz) FILTER (WHERE abstand_autobahn_km <= 5) / sum(ewz)
FROM zuordnung;

-- Depot-Detail: Einzugsgebiet konsolidiert je Depot (DEPOT-Bezirk, nicht der des zugeordneten Ortsteils)
CREATE OR REPLACE TABLE depot_stats AS
SELECT
    z.depot_ags,
    z.depot_name,
    d.bezirk                         AS bezirk_des_depots,
    count(*)                         AS anz_ot,
    sum(z.ewz)                       AS ewz_einzugsgebiet,
    round(sum(z.ewz * z.dist_km) / sum(z.ewz), 2) AS dist_gew_km
FROM zuordnung z
JOIN ortsteile d ON d.ortsteil = z.depot_ags
GROUP BY 1, 2, 3
ORDER BY ewz_einzugsgebiet DESC;

-- Doppelt gefilterte Risiko-Auswahl: weit vom Depot UND weit von der Autobahn.
-- Scharfe Schwellen: mindestens 4 km zum Depot und mindestens 5 km zur Autobahn.
CREATE OR REPLACE TABLE risiko_ortsteile AS
SELECT
    ortsteil,
    bezirk,
    ewz,
    round(dist_km, 1)                AS dist_zum_depot_km,
    round(abstand_autobahn_km, 1)    AS dist_zur_autobahn_km
FROM zuordnung
WHERE dist_km >= 4 AND abstand_autobahn_km >= 5
ORDER BY ewz DESC;

-- Versorgungsdefizite (>= 8 km zum Depot)
CREATE OR REPLACE TABLE unterversorgt AS
SELECT
    ortsteil,
    bezirk,
    ewz,
    round(dist_km, 1)               AS dist_km
FROM zuordnung
WHERE dist_km >= 8
ORDER BY ewz * dist_km DESC
LIMIT 15;
