-- d02_15min_score.sql
-- Flächenanteil je Ortsteil, der in 1100 m Luftlinie (~15 Gehminuten) zu einer
-- Kategorie liegt: Buffer-Vereinigung je Kategorie, Schnitt mit Ortsteil-Polygon.
-- Ausgabe: Anteil 0-1 je Kategorie, Komposit-Score (Mittelwert der 5 Anteile),
-- stadtweite einwohnergewichtete Durchschnittswerte, Liste der Unterversorger.

CREATE OR REPLACE TABLE buffer_vereinigt AS
SELECT kategorie, ST_Union_Agg(ST_Buffer(geom, 1100)) AS zone
FROM (
    SELECT 'essen' AS kategorie, geom FROM poi_essentials WHERE kategorie = 'essen'
    UNION ALL SELECT 'apotheke', geom FROM poi_essentials WHERE kategorie = 'apotheke'
    UNION ALL SELECT 'arzt', geom FROM poi_essentials WHERE kategorie = 'arzt'
    UNION ALL SELECT 'schule', geom FROM poi_essentials WHERE kategorie = 'schule'
    UNION ALL SELECT 'oeffis', geom FROM transit
) u
GROUP BY 1;


CREATE OR REPLACE TABLE ortsteil_abdeckung AS
WITH lang AS (
    SELECT o.ortsteil, o.ewz, o.bezirk, b.kategorie,
           ST_Area(ST_Intersection(o.geom, b.zone)) / ST_Area(o.geom) AS anteil
    FROM depot.ortsteile o CROSS JOIN buffer_vereinigt b
),
wide AS (
    SELECT ortsteil, ewz, bezirk,
        coalesce(max(anteil) FILTER (WHERE kategorie = 'essen'),    0) AS anteil_essen,
        coalesce(max(anteil) FILTER (WHERE kategorie = 'apotheke'), 0) AS anteil_apotheke,
        coalesce(max(anteil) FILTER (WHERE kategorie = 'arzt'),     0) AS anteil_arzt,
        coalesce(max(anteil) FILTER (WHERE kategorie = 'schule'),   0) AS anteil_schule,
        coalesce(max(anteil) FILTER (WHERE kategorie = 'oeffis'),   0) AS anteil_oeffis
    FROM lang
    GROUP BY 1, 2, 3
)
SELECT
    *,
    round((anteil_essen + anteil_apotheke + anteil_arzt + anteil_schule
           + anteil_oeffis) / 5.0, 4) AS score
FROM wide;


CREATE OR REPLACE TABLE stadt_summary AS
SELECT
    ewz_gesamt,
    round(pct_essen, 1)    AS pct_essen,
    round(pct_apotheke, 1) AS pct_apotheke,
    round(pct_arzt, 1)     AS pct_arzt,
    round(pct_schule, 1)   AS pct_schule,
    round(pct_oeffis, 1)   AS pct_oeffis,
    round(pct_score, 1)    AS pct_score
FROM (
    SELECT sum(ewz) AS ewz_gesamt,
        100.0 * sum(ewz * anteil_essen)    / sum(ewz) AS pct_essen,
        100.0 * sum(ewz * anteil_apotheke) / sum(ewz) AS pct_apotheke,
        100.0 * sum(ewz * anteil_arzt)     / sum(ewz) AS pct_arzt,
        100.0 * sum(ewz * anteil_schule)   / sum(ewz) AS pct_schule,
        100.0 * sum(ewz * anteil_oeffis)   / sum(ewz) AS pct_oeffis,
        100.0 * sum(ewz * score)           / sum(ewz) AS pct_score
    FROM ortsteil_abdeckung
);


CREATE OR REPLACE TABLE mangel_ortsteile AS
SELECT ortsteil, bezirk, ewz, score,
       round(anteil_essen, 2)   AS anteil_essen,
       round(anteil_oeffis, 2)  AS anteil_oeffis
FROM ortsteil_abdeckung
WHERE score < 0.5
ORDER BY ewz * (0.5 - score) DESC;
