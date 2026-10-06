-- SQLite / Home Assistant recorder. Read-only, bounded to <= 721 hourly rows.
-- Reconstruct each stored hourly mean as constant inside its bucket.
-- Coverage below is BUCKET coverage, NOT verified device availability.
WITH
clock AS (SELECT CAST(strftime('%s', 'now') AS INTEGER) AS now_ts),
windows(label, seconds) AS (VALUES ('24h', 86400), ('7d', 604800), ('30d', 2592000)),
history AS MATERIALIZED (
  SELECT s.start_ts, s.mean,
    CASE m.unit_of_measurement WHEN 'W' THEN 1.0 WHEN 'kW' THEN 1000.0 END AS to_w
  FROM clock AS c
  CROSS JOIN statistics_meta AS m
  CROSS JOIN statistics AS s
  WHERE s.metadata_id = m.id
    AND m.statistic_id = 'sensor.atorch_at24_power'
    AND m.source = 'recorder' AND m.mean_type = 1
    AND m.unit_of_measurement IN ('W', 'kW')
    AND s.start_ts > c.now_ts - 2592000 - 3600
    AND s.start_ts <= c.now_ts - 3600
    AND s.start_ts = CAST(s.start_ts AS INTEGER)
    AND s.start_ts % 3600 = 0
    AND typeof(s.mean) IN ('real', 'integer')
    AND ABS(s.mean * CASE m.unit_of_measurement WHEN 'W' THEN 1.0 ELSE 1000.0 END) <= 1.7976931348623157e308
),
overlap AS (
  SELECT w.label, w.seconds, h.mean * h.to_w AS watts, h.start_ts,
    MIN(h.start_ts + 3600, c.now_ts) - MAX(h.start_ts, c.now_ts - w.seconds) AS duration
  FROM windows AS w CROSS JOIN clock AS c
  LEFT JOIN history AS h ON h.start_ts + 3600 > c.now_ts - w.seconds
),
aggregates AS (
  SELECT label, seconds, COUNT(watts) AS buckets,
    COALESCE(SUM(duration), 0.0) AS covered_seconds,
    -- Scale before summing to avoid overflow from multiplying W by seconds.
    SUM(watts * (duration * 1.0 / seconds)) AS scaled_watts,
    MAX(start_ts + 3600) AS newest_bucket_end
  FROM overlap GROUP BY label, seconds
),
projections AS (
  SELECT *, 100.0 * covered_seconds / seconds AS coverage,
    CASE WHEN covered_seconds >= 0.90 * seconds THEN
      scaled_watts / (covered_seconds * 1.0 / seconds) * 0.72
    END AS monthly
  FROM aggregates
)
SELECT
  MAX(CASE WHEN label = '24h' AND ABS(monthly) <= 1.7976931348623157e308 THEN monthly END) AS monthly_24h,
  MAX(CASE WHEN label = '7d' AND ABS(monthly) <= 1.7976931348623157e308 THEN monthly END) AS monthly_7d,
  MAX(CASE WHEN label = '30d' AND ABS(monthly) <= 1.7976931348623157e308 THEN monthly END) AS monthly_30d,
  MAX(CASE WHEN label = '24h' THEN coverage END) AS coverage_24h,
  MAX(CASE WHEN label = '7d' THEN coverage END) AS coverage_7d,
  MAX(CASE WHEN label = '30d' THEN coverage END) AS coverage_30d,
  MAX(CASE WHEN label = '24h' THEN buckets END) AS buckets_24h,
  MAX(CASE WHEN label = '7d' THEN buckets END) AS buckets_7d,
  MAX(CASE WHEN label = '30d' THEN buckets END) AS buckets_30d,
  MAX(newest_bucket_end) AS newest_bucket_end,
  (SELECT now_ts FROM clock) AS computed_at,
  3600 AS resolution_seconds, 90 AS minimum_coverage,
  'recorded_hourly_statistics_v1' AS method
FROM projections;
