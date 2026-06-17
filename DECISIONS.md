# Decisions Memo

Translate work for both parts of the project: what we decided, why in plain language, and what the limits are.

---

## Part 1 — Data Pipeline

### The staircase

```
S3 archive (citibike tripdata zips)
  → GCS: gs://msbai-dwd-csc9720-citibike-raw/   raw archives, untouched
  → GCS: .../uniform/*.csv.gz                    one 19-column CSV per archive
  → BigQuery: citibike.raw_trips                 loaded as-is, typed only
  → BigQuery: citibike.trips_unified             view: clean columns, derived fields
  → BigQuery: citibike.daily_summary             view: one row per date × region × rider type
  → BigQuery: citibike.daily_summary_materialized table built from that view
```

**Why GCS first?** The S3 archives are messy — nested zips, split files, filename typos, two schema eras. GCS gives a stable staging area to fix transform bugs without re-downloading 29 GB from S3 each time.

**Why load raw and clean in a view?** `raw_trips` lands with almost no transformation. All logic (timestamp parsing, distance, schema-era detection) lives in `trips_unified` as SQL. Fixing a parsing bug means updating a view in seconds, not re-running a 316M-row load.

---

### Decision 1 — What files actually exist in the archive

We listed the S3 bucket directly before writing any code. The archive is **not** a clean "one file per month":

| Type | Pattern | Notes |
|------|---------|-------|
| Yearly NYC zips | `20{13..23}-citibike-tripdata.zip` | Each contains nested zips, split CSVs, `__MACOSX/` junk |
| Monthly NYC zips | `YYYYMM-citibike-tripdata.zip` (2024+) | Pre-split `_1.csv` / `_2.csv` |
| Monthly JC zips | `JC-YYYYMM-citibike-tripdata.csv.zip` | Real naming typos (space instead of dash, "citbike" misspelling, `.zip` vs `.csv.zip`) |

The ETL processes recursively — unzip until a `.csv` appears, skip `__MACOSX/` and zero-byte entries — so every fragment is loaded regardless of how the archive was packaged.

---

### Decision 2 — Keep Jersey City; tag every row

NYC and JC are **separate systems** with separate station namespaces. Every row gets `system = 'NYC'` or `system = 'JC'` derived from the source filename at load time.

**Why:** JC is ~1% of all trips. Merging silently inflates NYC numbers with a different system. Without the tag, the dashboard's region filter is impossible — you can't show "NYC trips on rainy days" vs. "JC trips on rainy days."

**Limit:** Jan–Mar 2016 JC rows appear absent from the NYU reference table. We have *more* of those rows; we treat our copy as the more complete one.

---

### Decision 3 — Full history (2013–present); two schemas handled by explicit position mapping

We load everything back to June 2013. The data has two incompatible layouts:

| Era | Date range | Columns |
|-----|-----------|----------|
| Schema A | 2013 – Jan 2021 | 15 cols; title-case headers 2013–2017, lowercase 2018–Jan 2021; includes `birth_year`, `gender` |
| Schema B | Feb 2021 onward | 13 cols; GBFS-style names; includes `rideable_type`; no `birth_year` or `gender` |

**Why explicit mapping, not header autodetect:** BigQuery autodetect would treat `Trip Duration` and `tripduration` as separate columns, producing a sparse union. Explicit position mapping puts all 316M rows in one table with consistent columns.

`birth_year` and `gender` are NULL for every Schema B row — this is a data limitation, not a bug. Any analysis using those fields must be restricted to pre-Feb 2021 and labeled accordingly.

---

### Decision 4 — Straight-line distance, not Google Maps

Neither schema has a distance column. We compute:

```sql
ST_DISTANCE(ST_GEOGPOINT(start_lng, start_lat), ST_GEOGPOINT(end_lng, end_lat)) / 1000
```

**Why not Google Maps API:** 316M+ trips at any per-request rate would cost six figures and hit rate limits. Current routing also doesn't reflect the 2014 bike-lane network.

**Required disclosures:**
- This is station-to-station straight-line distance — real routes are typically 20–40% longer.
- Round trips (same start and end station) show **0 km** even if the rider rode for 45 minutes.
- Rows with `0,0` or missing coordinates are filtered before computing distance.

---

### Decision 5 — Trip date = start time, America/New_York local wall-clock

`trip_date = DATE(started_at)` where `started_at` is the source timestamp taken as a naive local datetime (no UTC conversion).

**Why start time:** the weather join captures conditions the rider *decided to ride in*. A trip starting at 11:58pm counts toward that day — that's when the weather-driven decision was made. Using end time would sometimes assign a trip to the next day's weather with no analytical benefit.

**Caveat:** trips crossing midnight are attributed entirely to the start day.

---

### Verification

Independent source of truth: `nyu-datasets.citibike.m_trips_unified` (319.2M rows).

Our final row count: **316,082,152** (~1% gap). Per-file reconciliation found:

- **Jan–Mar 2016:** we have ~29K *more* rows (NYU is missing those JC months).
- **Jan 2021:** we have ~1.1M fewer — our bucket received only one archive file for that month; NYU had additional source files.
- **Feb 2021 – Dec 2023:** 1.5–3.5% short, concentrated in the last split-chunk of each month — trailing chunks not delivered to our GCS bucket.

**Conclusion:** 100% recall on what was delivered to our bucket. The ~1% gap is a source availability difference, not a processing defect.

Spot-check vs. `nyu-datasets.citibike.m_daily_trips`:

| Date | Ours | NYU |
|------|------|-----|
| 2019-07-15 | 82,630 | 82,629 |
| 2024-01-01 | 48,709 | 48,709 |
| 2022-06-15 | 131,746 | 135,019 |
| 2023-12-25 | 30,585 | 31,713 |

---

## Part 2 — Dashboard

**Live URL:** https://citibike-dashboard-894638510471.us-central1.run.app

**Visitor:** a curious non-technical person — journalist, city planner, or Citibike manager — not a data analyst.

**Structure:** five tabs — `📊 Overview`, `🌡️ Weather Impact`, `🔬 Model (Stretch 1)`, `🔮 Forecast (Stretch 2)`, `💰 E-Bike Revenue (Stretch 3)` — all filterable by date range, region (NYC/JC), and rider type (member/casual). Sidebar lists stretch features explicitly so they are not overlooked.

---

### The five questions and why

| # | Question | Business reason |
|---|----------|-----------------|
| 1 | How has ridership grown, and how much does it swing with seasons? | Context before any weather story. The seasonal swing dwarfs year-over-year growth within any calendar year. |
| 2 | Does temperature predict ridership, and is it different for casual vs. member riders? | Temperature is the strongest single weather driver. The rider-type split shows who is weather-sensitive (casual) vs. habitual (member). |
| 3 | How does the casual rider share shift with temperature? | Casual riders have higher per-trip margin than members. They self-concentrate on warm days — exactly when weather-triggered promotions are most effective. |
| 4 | How much do rain and snow suppress ridership, by rider type? | Quantifies the revenue at risk from bad weather. Casual riders drop further than members, so bad weather hurts the high-margin segment most. |
| 5 | Is a given month above or below the seasonal baseline? | Separates "weather was good" from "we did something right (or wrong)." A month far below its seasonal average is worth a manager's attention. |

---

### Why only three filters (date, region, rider type)

These are the three dimensions a non-technical visitor can reason about that actually change the business story. More filters (station, bike type, hour of day) add cognitive load without serving any of the five questions and are deliberately excluded.

---

### Why a pre-joined daily table

1. **Identity.** If the app queried `nyu-datasets.weather` live, the Cloud Run service account would need cross-project read access to a dataset we don't own — not guaranteed. Pre-joining at ETL time means the app reads one table in our own project.
2. **Speed.** ~14k rows, read once, cached for 1 hour (`@st.cache_data(ttl=3600)`). Slider moves run on pandas only — never BigQuery.

---

### Findings — one sentence per chart

1. **Daily ridership** has grown several-fold since 2013 and swings roughly an order of magnitude between a peak summer day and a winter low.
2. **Trips rise with temperature for everyone, but casual riders bend far more steeply** — members commute through the cold, casual riders mostly wait for warm days.
3. **The casual share climbs from its lowest on freezing days to its highest on 80°F+ days** — the high-margin segment self-concentrates exactly when a promotion would reach them.
4. **Rain dents ridership and snow cuts it sharply; casual riders drop more than members** — bad weather thins the promotable segment first.
5. **Most months sit close to the seasonal baseline; the few that fall far below it are the ones worth a manager's attention** — those gaps are too large to blame on ordinary seasonal weather.

---

### Verification results

| Target | Status |
|--------|--------|
| Correctness — app math | ✅ `test_transforms.py` → 6/6 pass (hand-computed synthetic data) |
| Correctness — vs. source | ✅ Dashboard totals match `SELECT SUM(num_trips) FROM citibike.daily_summary_materialized` |
| Speed (cold ≤5s) | ✅ Single ~14k-row cached read; no per-interaction BigQuery |
| Reach (public, no login) | ✅ `--allow-unauthenticated`; opens in incognito with no sign-in |
| Clarity (claim per chart) | ✅ Every chart has an on-screen `st.caption` with a one-sentence finding |

---

### Diagnose — why deployment failures differ from local failures

Every failure we hit was about **environment and identity, not code**:

1. **No local credentials** → set `GCP_CREDENTIALS_KEY`, re-run `deploy.sh` from Cloud Shell.
2. **Cloud Run runs as a service account, not you** → the default Compute SA can't query BigQuery (403 after a successful deploy). Fixed by passing `--service-account claude-agent@msbai-dwd-csc9720.iam.gserviceaccount.com`.
3. **Cross-project reads need their own IAM grant** → avoided entirely by pre-joining weather at ETL time.

---

## Stretch Items

All three implemented. Assumptions and limits stated plainly below.

### Stretch 1 — Weather-adjusted ridership model

**Model:** `GradientBoostingRegressor` (200 trees, depth 4, lr=0.05)

**Features:** `tavg_f`, `prcp_in`, `snow_in`, day-of-week sin/cos, month sin/cos, year (fleet growth trend)

**Train/test split:** pre-2024 data (~3,800 days) → test on 2024+ (~500 days, never seen during training). Out-of-sample MAPE shown in the chart caption at runtime — not suppressed if large.

**What the residuals show:** days where actual ridership diverges more than 2σ from expectation signal something beyond weather — operational shortfalls, outages, or promotions working.

**Limits:** model aggregates all systems and rider types; cannot be re-filtered without retraining. `year` captures average growth but not sudden step-changes (e.g., COVID 2020).

---

### Stretch 2 — 7-day ridership forecast

**Source:** Open-Meteo free API (no API key) at NYC coordinates (40.7128°N, 74.0060°W), temperature in °F, precipitation in inches.

**Method:** same 8 features as Stretch 1 applied to forecast dates. The chart caption quotes the held-out MAPE from Stretch 1 as the honest accuracy proxy.

**Limits:** forecast error compounds model error. 1–2 day forecasts are typically accurate to ±2–4°F / ±0.1" precip. If the API is down, the chart shows a warning rather than crashing.

---

### Stretch 3 — E-bike revenue estimate

**Pricing schedule (Citibike 2025 rates, retrieved June 2026):**

| Rider type | Rate |
|-----------|------|
| Casual | $1.00 unlock + $0.26/min |
| Member | $0.17/min (no unlock fee) |

**Scope:** February 2021 onward, rows where `num_electric_trips > 0`. Classic bikes excluded — a member's classic trip has near-zero marginal revenue at this grain.

**Duration caveat:** `avg_trip_duration_minutes` is the group-level average across all trip types, not e-bike-specific. Treat estimates as ±20–30% order-of-magnitude figures.

**Framing:** 2025 pricing applied as a **consistent historical yardstick**, not reconstructed historical revenue. The exact schedule for every month across 2021–2026 is not available. The yardstick is useful for comparing weather conditions against each other, not for auditing Citibike's books.

---

## BigQuery objects

Project: `msbai-dwd-csc9720`

| Object | Type | Rows | Description |
|--------|------|------|-------------|
| `citibike.raw_trips` | Table | 316M | Loaded from GCS uniform CSVs, typed only — the audit trail |
| `citibike.trips_unified` | View | 316M | Parsed timestamps, derived fields, both schema eras reconciled |
| `citibike.daily_summary` | View | ~17k | One row per (date × system × rider type) |
| `citibike.daily_summary_materialized` | Table | ~17k | Physical materialization of `daily_summary` |
| `citibike.daily_summary_with_weather` | Table | ~17k | `daily_summary_materialized` LEFT JOIN weather — dashboard source |

To reproduce the row counts:

```sql
-- Part 1: trip totals
SELECT COUNT(*) FROM `msbai-dwd-csc9720.citibike.trips_unified`;

-- Part 2: daily summary check (must equal trips_unified count)
SELECT SUM(num_trips) FROM `msbai-dwd-csc9720.citibike.daily_summary_materialized`;

-- Weather join coverage
SELECT
  COUNT(*) AS total_rows,
  COUNTIF(tavg_f IS NOT NULL) AS rows_with_weather,
  ROUND(COUNTIF(tavg_f IS NOT NULL) / COUNT(*) * 100, 1) AS pct_matched
FROM `msbai-dwd-csc9720.citibike.daily_summary_with_weather`;
```
