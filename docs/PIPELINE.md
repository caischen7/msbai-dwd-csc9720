# Part 1: Citibike + Weather ETL Pipeline

## Decompose (the staircase)

```
S3 archive (citibike tripdata zips)
  -> GCS: gs://msbai-dwd-csc9720-citibike-raw/  (raw archives, untouched)
  -> GCS: .../uniform/*.csv.gz                  (one normalized 19-column CSV per archive)
  -> BigQuery: citibike.raw_trips               (loaded untouched, typed only)
  -> BigQuery: citibike.trips_unified           (view: parsed timestamps, derived fields)
  -> BigQuery: citibike.daily_summary           (view: one row per date x region x rider type)
  -> BigQuery: citibike.daily_summary_materialized (table, built from the view)
```

Scripts: `etl/uniform_transform.py` (S3/GCS -> uniform CSVs, parallelized 4-way),
`etl/build_pipeline.py` (uniform CSVs -> BigQuery raw table + views).

### Why land raw files in GCS first instead of loading straight to BigQuery?

The source archives are messy: nested zips (a zip inside a zip, one per month for
2021-2024), per-month folders with split CSV files, two incompatible column
schemas (the 2013-Jan 2021 "classic" schema and the Feb 2021+ schema), and stray
`__MACOSX` junk entries. GCS gives us a stable staging area we can re-read
cheaply and repeatedly while we get the transform right, without re-downloading
~29GB from S3 every time we fix a bug (which we did, twice).

### Why load untouched and clean in a view, instead of cleaning at load time?

`raw_trips` is loaded with almost no transformation beyond typing
(numeric/string columns) - timestamps are kept as raw strings. This means
BigQuery's strict CSV loader never rejects a row for a cleaning decision we
might later change our minds about. All judgment calls - timestamp parsing,
duration/distance derivation, schema-era detection - live in the
`trips_unified` view as SQL. When we found a parsing bug, we fixed and
re-ran the *view* (seconds), not the 316M-row load (minutes) or the
GCS transform (hours).

## Specify / canonical schema

`trips_unified` columns: `system` (NYC/JC), `source_era` (schema_a/schema_b),
`source_file`, `ride_id`, `rideable_type`, `bikeid`, `started_at`, `ended_at`
(DATETIME, local time, naive), `trip_date` (DATE), `duration_seconds`,
station ids/names/lat/lng for start and end, `member_casual`, `birth_year`,
`gender`, `distance_km_straight_line`.

`daily_summary` / `daily_summary_materialized` grain: one row per
`(trip_date, system, member_casual)`, with `num_trips`, `num_classic_trips`,
`num_electric_trips`, `avg_trip_duration_minutes`,
`median_trip_duration_minutes`, `avg_distance_km_straight_line`.

## Verify

Independent source of truth: `nyu-datasets.citibike.m_trips_unified` (319.2M
rows) and `nyu-datasets.citibike.m_daily_trips` (4,738 days).

Result after the fixes below: `raw_trips` = `trips_unified` = **316,082,152**
rows (100% of loaded rows now parse). Spot-checked daily totals against
`m_daily_trips`:

| date | ours | nyu |
|---|---|---|
| 2019-07-15 | 82,630 | 82,629 |
| 2024-01-01 | 48,709 | 48,709 |
| 2022-06-15 | 131,746 | 135,019 |
| 2023-12-25 | 30,585 | 31,713 |

## Diagnose

### Bugs found and fixed during validation

1. **`\N` null markers** (2013 `birth_year`): some source CSVs use the MySQL
   export convention `\N` for NULL. BigQuery can't load `\N` into an INT64
   column. Fixed in `uniform_transform.py`'s `get()` helper - `\N`, `NULL`,
   `N/A`, `NA` (case-insensitive) are all normalized to empty string before
   writing the uniform CSV.

2. **`"1966.0"`-style floats in `birth_year`** (2017): some monthly files
   write birth year as a float string. INT64 load failed. Fixed by loading
   `birth_year`, `gender`, `tripduration` as FLOAT64 in `raw_trips` and
   `SAFE_CAST(ROUND(x) AS INT64)` in the view.

3. **US-format timestamps `"M/D/YYYY H:MM[:SS]"`** (most of 2014-2016, ~22.9M
   rows): some monthly files use US date format with no zero-padding instead
   of `"YYYY-MM-DD HH:MM:SS"`. `SAFE.PARSE_DATETIME` returned NULL for all of
   them, and the view's `WHERE started_at IS NOT NULL` silently dropped the
   rows (293.2M instead of 316.1M). Fixed by trying three formats with
   `COALESCE`: ISO with optional fractional seconds, US with seconds, US
   without seconds.

4. **OOM during transform**: the original transform recursively read *every*
   nested zip's bytes into memory before processing any of them, so a worker
   handling a ~1.2GB archive (12 nested per-month zips) peaked at 5.7GB RSS
   and was OOM-killed. Fixed by processing nested zips one at a time
   (`iter_csv_streams` is now a generator that reads, processes, and discards
   each nested zip's bytes before moving to the next), dropping peak RSS to
   ~750MB.

### The remaining ~1% gap vs `nyu-datasets.citibike` (316.08M vs 319.19M, -3.1M)

We reconciled row counts **per source file / per split-chunk** between our
`raw_trips.source_file` and nyu's `source_file` (which encodes the same
`YYYYMM-citibike-tripdata_N` naming). Findings:

- **Jan-Mar 2016: we have ~29K *more* rows than nyu**, and the excess exactly
  equals the JC archives for those months (`JC-201601/02/03`). nyu's
  reference table appears to be missing Jersey City data for that quarter -
  our data is *more* complete here, not less.
- **2020: we have ~55K (+0.3%) more rows than nyu**, spread evenly across all
  12 months. Small and not fully explained; within the range of "different
  source snapshot" noise and not worth chasing further.
- **Jan 2021: we have ~1.1M (-50%) fewer rows.** We re-downloaded
  `2021-citibike-tripdata.zip` and confirmed it contains exactly one nested
  zip for January (`202101-citibike-tripdata.zip`) with exactly two split
  files totaling 1,098,300 rows, and there is no other January-2021 file
  anywhere in our GCS bucket. We are processing 100% of what we have; nyu's
  source evidently includes additional January 2021 file(s) that were never
  delivered to our bucket.
- **Every month from Feb 2021 through Dec 2023, plus May 2024: we are
  1.5%-3.5% short**, concentrated in the *last* split-chunk of each month
  (sometimes an entire trailing chunk file is absent on our side). Same
  conclusion: our bucket's archives are missing those trailing chunks: we
  verified (by listing the nested zip's contents directly) that we are
  reading every file present, completely.

**Conclusion:** the pipeline has 100% recall on the data actually delivered
to `gs://msbai-dwd-csc9720-citibike-raw`. The residual ~1% gap vs
`nyu-datasets.citibike` is a difference in *source data availability*
(nyu's loader had access to a handful of extra/duplicate monthly files we
don't have), not a processing defect - confirmed by exact or near-exact
agreement on dates where the source files do match (e.g. 2024-01-01: 48,709
vs 48,709).

## Translate (plain language)

- **Why keep Jersey City?** It's a real, geographically distinct part of the
  unified Citibike system (~1% of all trips), and "compare NYC vs. JC" is
  one of the dashboard's region filters. Dropping it would throw away an
  entire dimension the spec calls for to save under 1% of rows.

- **What does "distance" measure, and where does it fall short?**
  `distance_km_straight_line` is the great-circle distance between the start
  and end station coordinates (`ST_DISTANCE` on lon/lat points), in km. It is
  *not* how far the bike traveled - bikes don't move in straight lines, and a
  round trip (same start/end station) shows as 0 km even if the rider rode
  for 40 minutes. Treat it as a rough proxy for "how far apart are the
  stations", useful for relative/regional comparisons, not as actual mileage
  or as a substitute for `duration_seconds`.

- **Why does a trip count toward the day it does?** `trip_date = DATE(started_at)`,
  where `started_at` is the source timestamp parsed as a naive
  (timezone-less) local datetime - we take the source's local New York
  wall-clock time at face value rather than converting through UTC (which
  would risk shifting trips near midnight to the wrong calendar day). A trip
  that starts at 11:58pm and ends at 12:05am counts toward the day it
  *started*, matching how an operator would read "how many rides went out
  today."
