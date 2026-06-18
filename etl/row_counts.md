# BigQuery Row Counts — Verification

Recorded 2026-06-18. Reproduce with the queries below.

## GCS Bucket: `msbai-dwd-csc9720-citibike-raw`

| Metric | Value |
|--------|-------|
| Total objects | 341 |
| Total size | 42.50 GB |

## BigQuery Tables

| Table / View | Type | Row count |
|-------------|------|-----------|
| `msbai-dwd-csc9720.citibike.raw_trips` | Table | 316,082,152 |
| `msbai-dwd-csc9720.citibike.trips_unified` | View | 316,082,152 |
| `msbai-dwd-csc9720.citibike.daily_summary_materialized` | Table | 17,537 |

NYU reference (`nyu-datasets.citibike.m_trips_unified`): 319,189,169 rows.
Gap: ~3.1M rows (~1%). See `DECISIONS.md` for per-file reconciliation.

## Verification Queries

```sql
-- Raw trip count
SELECT COUNT(*) FROM `msbai-dwd-csc9720.citibike.raw_trips`;

-- Clean view (must match raw)
SELECT COUNT(*) FROM `msbai-dwd-csc9720.citibike.trips_unified`;

-- Daily summary row count
SELECT COUNT(*) FROM `msbai-dwd-csc9720.citibike.daily_summary_materialized`;

-- Sum of daily trips must equal raw count
SELECT SUM(num_trips) FROM `msbai-dwd-csc9720.citibike.daily_summary_materialized`;

-- NYC vs JC split
SELECT system, COUNT(*) as trips
FROM `msbai-dwd-csc9720.citibike.trips_unified`
GROUP BY system;

-- Date range
SELECT MIN(trip_date), MAX(trip_date)
FROM `msbai-dwd-csc9720.citibike.trips_unified`;
```
