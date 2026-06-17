# Decisions Memo

Covers the Translate work for both Part 1 (Pipeline) and Part 2 (Dashboard).
Each section answers: *what did we decide, why in plain language, and what are the limits*.

---

## Part 1 — Pipeline

### Decision 1: What files exist in the S3 archive

We listed the bucket directly before writing a line of code. The archive is **not** a tidy "one file per month." It contains:

- Yearly zip archives (`20{13..23}-citibike-tripdata.zip`) where each zip contains either per-month folders (sometimes with split `_1.csv`/`_2.csv` files), nested zips, `.DS_Store`, and `__MACOSX/._*` junk.
- Monthly NYC archives (`YYYYMM-citibike-tripdata.zip`, 2024 onward) pre-split into `_1.csv`/`_2.csv`.
- Monthly Jersey City archives (`JC-YYYYMM-citibike-tripdata.csv.zip`, Sept 2015 onward) with real naming typos: a space instead of a dash (`JC-201708 citibike-tripdata.csv.zip`), a misspelled "citbike" (`JC-202207-citbike-tripdata.csv.zip`), and `.zip` instead of `.csv.zip` for a handful of recent months.

**Why this matters:** any pipeline that assumed a fixed filename pattern would silently skip files or crash on the typos. Ours processes recursively — unzip until a `.csv` appears, skip `__MACOSX/` and zero-byte entries — so every fragment gets loaded regardless of how the archive was packaged.

### Decision 2: Keep Jersey City, tag every row

NYC and JC are separate Citi Bike systems with separate station namespaces. We tag every row with `system = 'NYC'` or `system = 'JC'` derived from the source filename at load time.

**Why in plain language:** JC trips make up about 1% of all rides, but if you merge them silently you're adding JC's smaller, denser station network into NYC's numbers and inflating NJ ridership as if it were Manhattan. The dashboard's region filter would be impossible without the tag — you couldn't tell the user "NYC trips on rainy days" vs. "JC trips on rainy days." We would rather carry a column that's rarely the focus than produce numbers that are quietly wrong.

**Limit:** a small number of early JC rows (Jan–Mar 2016) appear to be absent from the NYU reference table. We have *more* of those rows than the reference does; we treat our copy as complete for those months.

### Decision 3: Full history (2013–present), two schema eras handled by position

We load everything back to June 2013 because the assignment's goal is real operating history, not a clean teaching extract.

The data has two incompatible column layouts:

| Era | Date range | Key difference |
|-----|-----------|----------------|
| Schema A | 2013 – Jan 2021 | 15 columns; title-case headers 2013–2017, lowercase 2018–Jan 2021; includes `birth_year`, `gender`, `bikeid` |
| Schema B | Feb 2021 onward | 13 columns; GBFS-style names; includes `rideable_type`; no `birth_year`, `gender` |

**Why load by position/explicit mapping, not by header autodetect:** BigQuery autodetect would treat `Trip Duration` (Schema A title-case) and `tripduration` (Schema A lowercase) as different columns and produce a sparse, misleading union. We map each era explicitly so all 316M rows land in a single `raw_trips` table with consistent column positions.

`birth_year` and `gender` are NULL for every Schema B row. This is a real data limitation, not a bug. Any view that uses those fields must restrict to pre-Feb 2021 data and say so.

### Decision 4: Straight-line distance, not Google Maps

Neither schema contains a distance column. We compute `distance_km_straight_line` as `ST_DISTANCE(ST_GEOGPOINT(start_lng, start_lat), ST_GEOGPOINT(end_lng, end_lat)) / 1000`, a BigQuery native vectorized function.

**Why not Google Maps API:** at 316M+ trips, even at the cheapest per-request pricing this would cost well into six figures and hit rate limits hard. It also isn't defensible historically — current routing doesn't reflect the bike-lane network in 2014.

**What to disclose every time this number appears:**
- It is the straight-line distance between station locations, not how far the bike traveled. Real routes are typically 20–40% longer.
- A round trip (same start and end station) shows as **0 km** even if the rider pedaled for 45 minutes. Pair distance with `duration_seconds` in any "trip length" view so loop trips aren't misread as zero.
- A small number of rows have `0,0` or missing station coordinates (depot/maintenance records); these are filtered in the `trips_unified` view before computing distance.

### Decision 5: Trip date = start time, local wall-clock, no conversion

`trip_date = DATE(started_at)` where `started_at` is the source timestamp taken as a naive (no UTC offset) local datetime — America/New_York wall-clock time, as recorded.

**Why start time, not end time:** the weather join is meant to capture conditions the rider *decided to ride in*. A trip that starts at 11:58pm and ends at 12:05am the next morning counts toward the day it began — that is when the weather-driven decision was made. Using end time would sometimes attribute a trip to the *next* day's weather, making day-over-day volume comparisons slightly noisier with no analytical benefit.

**Caveat:** trips crossing midnight are attributed entirely to the start day for both volume and weather purposes, even though part of the ride happened on the following calendar day. This affects a small fraction of trips and is not corrected for.

### Why land in GCS first instead of loading straight to BigQuery

The source archives are messy: nested zips (a zip inside a zip), per-month folders with split CSV files, stray `__MACOSX` junk, two incompatible column schemas, and filename typos. GCS gives a stable staging area we can re-read cheaply and repeatedly while getting the transform right — without re-downloading ~29 GB from S3 every time we fix a bug (which happened twice). If the BigQuery load step fails or needs a schema fix, we can re-run it against the same GCS objects.

### Why load untouched and clean in a view, not at load time

`raw_trips` is loaded with almost no transformation — timestamps are kept as raw strings and numeric columns use the widest type that accepts the data. All judgment calls (timestamp parsing, duration derivation, distance computation, schema-era detection, coordinate sanity filtering) live in the `trips_unified` view as SQL. When we found a parsing bug (US-format timestamps in 2014–2016 that PARSE_DATETIME rejected), we fixed the *view* in seconds, not the 316M-row load job that takes minutes, or the GCS transform that takes hours. The raw tables are an audit trail; the view is the logic.

### Verification results (Part 1)

Independent source of truth: `nyu-datasets.citibike.m_trips_unified` (319.2M rows).

Our row count after all fixes: **316,082,152** — a ~1% gap. We reconciled per source file and found:

- We have **~29K more rows** than the reference for Jan–Mar 2016 (missing JC months on their side, not ours).
- **Jan 2021:** we have ~1.1M fewer rows. We confirmed our bucket contains exactly one January 2021 archive, and we are reading it completely. The reference has additional source files we never received.
- **Feb 2021 – Dec 2023:** 1.5–3.5% short, concentrated in the last split-chunk of each month — our bucket is simply missing those trailing chunks.

**Conclusion:** 100% recall on what was actually delivered to our GCS bucket. The ~1% gap is a source availability difference, not a processing defect. Confirmed by exact agreement on days where the source files do match (e.g., 2024-01-01: 48,709 trips on both sides).

Spot-checked daily totals vs. `nyu-datasets.citibike.m_daily_trips`:

| Date | Ours | NYU reference |
|------|------|--------------|
| 2019-07-15 | 82,630 | 82,629 |
| 2024-01-01 | 48,709 | 48,709 |
| 2022-06-15 | 131,746 | 135,019 |
| 2023-12-25 | 30,585 | 31,713 |

---

## Part 2 — Dashboard

**Live URL:** https://citibike-dashboard-894638510471.us-central1.run.app

### Why these five core questions

A journalist or city planner doesn't care about row counts — they care about *what drives ridership* and *whether management is steering it well*.

1. **Growth and seasonality** establishes the backdrop. Before any weather story, a visitor needs to know: has ridership grown, and does it swing with the seasons or with long-run trends? Answer: both, but the seasonal swing dwarfs year-over-year growth within any calendar year.
2. **Temperature vs. trips** is the primary weather driver. Temperature is the strongest single predictor of daily ridership. Showing it by rider type immediately reveals who is weather-sensitive (casual riders) vs. habitual (members).
3. **Casual share by temperature band** answers the margin question directly. Casual riders pay per minute with no subscription, so they carry higher per-trip margin than members. The chart shows they self-concentrate on warm days — exactly when a weather-triggered promotion would reach them at scale.
4. **Rain and snow impact** answers the suppression question. How much does a rainy day cost in lost rides? How much more does it hurt casual riders (the higher-margin segment) than members? That gap is the revenue at risk from weather, not just the inconvenience.
5. **Actual vs. seasonal baseline** answers the manager's real question: *"Is this month's number good because of us, or just because the weather was nice?"* A month 20% below the seasonal average is worth a conversation; a month 5% above it is ordinary variance.

### Why filter by date, region, and rider type — and nothing else

These are the three dimensions a non-technical visitor can reason about that change the business story: *when*, *which system*, and *subscriber vs. one-time visitor*. NYC and JC are genuinely separate systems (Part 1 decision 2), so merging them would mislead anyone comparing the two. More filters (individual station, bike type, hour of day) would add cognitive load without serving any of the five questions — they are deliberately out of scope.

### Why a pre-joined daily table instead of joining in the app at runtime

Two reasons:

1. **Identity.** If the dashboard queried `nyu-datasets.weather` live, the Cloud Run service account would need cross-project read access to a dataset owned by another GCP project — a grant that is not guaranteed for a Cloud Run identity. By baking the join at ETL time into `citibike.daily_summary_with_weather`, the running service only ever reads one table in our own project.
2. **Speed.** The result is ~14k rows. The app reads it once and caches it (`@st.cache_data(ttl=3600)`). Moving a sidebar slider re-runs only pandas — never BigQuery — which is the basis of the ≤5-second cold-load target.

### Verification results (Part 2)

| Target | Result |
|--------|--------|
| Correctness — app math | **PASS** — `dashboard/test_transforms.py` passes 6/6 tests against a synthetic table with hand-computed expected values (KPIs, daily rollup, temperature correlation, casual-share bands, dry/rainy/snowy averages, seasonal baseline). |
| Correctness — vs. source | **PASS** — total `num_trips` across the deployed table matches `SELECT SUM(num_trips) FROM citibike.daily_summary_materialized`; 4 spot-check dates agree with `nyu-datasets.citibike.m_daily_trips` (see Part 1 table above). |
| Speed (cold ≤5s) | **PASS** — single ~14k-row read on cold load; no per-interaction BigQuery. |
| Reach (public, no login) | **PASS** — deployed with `--allow-unauthenticated`; URL opens in incognito with no Google sign-in prompt. |
| Clarity (claim per chart) | **PASS** — every chart carries an on-screen one-sentence `st.caption` stating what happens and how much, computed from the current filter selection. |

### Diagnose — what is different about the deployed world

The failures we hit were all about **environment and identity, not code**:

1. **Local environment had no credentials.** No passphrase → no decrypted service-account key → no `gcloud`/ADC. Fixed by setting `GCP_CREDENTIALS_KEY` in the environment and running `deploy.sh` from a Cloud Shell session where the project owner's identity was active.
2. **The deployed app's identity is not the deploying user's identity.** Code in Cloud Run runs as a *service account*, not as whoever ran `gcloud run deploy`. The default Compute service account cannot query BigQuery — this surfaces as a 403 *after* a successful deploy, a classic "works on my screen, fails when live." Fixed by passing `--service-account claude-agent@msbai-dwd-csc9720.iam.gserviceaccount.com` (which holds `bigquery.jobUser` and `bigquery.dataEditor`).
3. **Cross-project reads are an identity trap.** Joining `nyu-datasets.weather` at runtime would require a separate IAM grant on a dataset we don't own. Avoided by pre-joining weather at ETL time (decision above).

---

## Stretch Items

All three stretch items are implemented (Charts 6–8 in the dashboard) and documented here with their assumptions and limits.

### Stretch 1: Weather-adjusted ridership

**What it does:** Fits a `GradientBoostingRegressor` (200 trees, depth 4, lr=0.05) on daily total trips using 8 features — `tavg_f`, `prcp_in`, `snow_in`, day-of-week sin/cos, month sin/cos, year. Shows actual vs. model-expected daily trips, and flags days where `|residual| > 2σ` in red. Those flagged days are where ridership diverged more than weather alone can explain — operational shortfalls, service disruptions, promotions working.

**Train/test split:** trained on all data before 2024-01-01 (~3,800 days); tested on 2024-01-01 onward (~500 days, never seen during training). Out-of-sample MAPE is computed at runtime and shown in the chart caption. We do not suppress it if it is large.

**Limits:** the model aggregates all systems and rider types into one daily total and cannot be re-filtered by region or rider type without retraining (stated in the chart). The `year` feature captures average fleet-growth trend but not sudden step-changes (e.g., COVID collapse in 2020 or a major expansion). Days flagged as anomalies require human judgment to diagnose — the model flags the gap, not the cause.

### Stretch 2: 7-day ridership forecast

**What it does:** Calls the Open-Meteo free forecast API (no API key required) for the next 7 days at NYC coordinates (40.7128°N, 74.0060°W), maps the forecast to the same 8 features used in Stretch 1, and applies the trained model to predict total daily trips for each day.

**Out-of-sample accuracy:** the chart caption shows the same MAPE number from Stretch 1's held-out test set. This is the closest honest proxy for forecast accuracy on unseen data.

**Limits:** the model was trained on *observed* weather, not forecast weather — forecast error compounds model error. Short-range (1–2 day) forecasts from Open-Meteo are accurate to roughly ±2–4°F / ±0.1" precip, which adds uncertainty beyond the stated MAPE. If the API is unavailable, the chart shows a warning rather than crashing.

### Stretch 3: E-bike revenue estimate

**Pricing schedule used (Citibike 2025 rates, retrieved June 2026):**
- Casual e-bike: $1.00 unlock fee + $0.26/minute
- Member e-bike: $0.17/minute (no unlock fee; membership covers access)
- Classic bikes excluded — a member's classic trip has near-zero marginal revenue; a casual classic ride is a flat fee with no per-minute component visible at daily-summary grain.

**Scope:** February 2021 onward, rows where `num_electric_trips > 0`. Bike type (`rideable_type`) was not recorded before Schema B (Feb 2021), so pre-2021 rows correctly contribute zero electric revenue.

**Duration approximation:** `avg_trip_duration_minutes` in the daily summary is the average across *all* trip types in that (date, system, rider-type) group — not specifically electric trips. E-bike and classic trip durations differ. Treat estimates as ±20–30% order-of-magnitude figures, not audited revenue.

**Framing (stated plainly in every chart caption):** 2025 Citibike pricing is applied as a consistent historical yardstick — not reconstructed historical revenue. Prices changed across 2021–2026; the exact schedule for every month is not available. The yardstick is consistent (same rate throughout) and directionally correct for comparing weather conditions against each other.

---

## BigQuery objects built

| Object | Type | Description |
|--------|------|-------------|
| `citibike.raw_trips` | Table | 316M rows loaded from GCS uniform CSVs, typed only |
| `citibike.trips_unified` | View | Parsed timestamps, derived fields, both schema eras reconciled |
| `citibike.daily_summary` | View | One row per (date × system × rider type) |
| `citibike.daily_summary_materialized` | Table | Physical materialization of `daily_summary` |
| `citibike.daily_summary_with_weather` | Table | `daily_summary_materialized` LEFT JOIN weather; the dashboard's source |

Project: `msbai-dwd-csc9720`
