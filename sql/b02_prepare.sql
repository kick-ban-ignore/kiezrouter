-- b02_prepare.sql (Region Berlin)
-- Autobahnnähe je Ortsteil (Distanz Ortsteilzentrum -> nächstes Segment, km).
CREATE OR REPLACE TABLE ot_autobahn AS
SELECT
    o.ortsteil    AS ortsteil,
    o.ewz         AS ewz,
    round(min(ST_Distance(o.centroid, m.geom)) / 1000.0, 3) AS abstand_autobahn_km
FROM ortsteile o, motorway m
WHERE m.fclass IN ('motorway', 'motorway_link')
GROUP BY 1, 2;
