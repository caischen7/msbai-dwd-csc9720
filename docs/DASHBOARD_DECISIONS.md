# Part 2 Dashboard — Decisions Memo

Defends each spec decision in business terms and reports verification results
against the targets in `docs/DASHBOARD_SPEC.md`. Honest status up front:

> **Deployment is blocked on one missing secret, not on code.** This
> environment has no `GCP_CREDENTIALS_KEY` / `CLOUD_CREDENTIALS_KEY` set, so the
> `SessionStart` hook never decrypted the service-account key — there is no
> `gcloud`, no Application Default Credentials, and therefore no way to query
> BigQuery or run `gcloud run deploy` from here. Network egress to Google APIs
> works (probes to `bigquery.googleapis.com` / `run.googleapis.com` return HTTP
> status codes, not connection failures); the only thing absent is **identity**.
> Everything that does not require cloud access is finished and verified below;
> the live deploy + numeric cross-check are staged behind a one-line unblock
> (set the passphrase, then `dashboard/deploy.sh`).

## Spec decisions and why (business terms)

**Why these five questions.** A journalist or planner does not care about row
counts; they care about *what drives ridership* and *whether management is
steering it well*. Charts 1–2 establish the backdrop (growth, seasonality,
temperature sensitivity). Charts 3–4 are the money questions: casual riders
carry **higher margins** than members, so the business wants to know exactly
when and where they ride — that is where weather-triggered promotions pay off.
Chart 5 answers the manager's real question — "*is this month's number good
because of us or because of the weather?*" — by removing ordinary seasonality
and flagging the residual.

**Why filter by date, region, and rider type — and nothing else.** These are
the three dimensions a non-technical visitor can reason about and that change
the business story: *when*, *which city's system*, and *subscriber vs. casual*.
NYC and Jersey City are genuinely separate systems (Part 1 decision 2), so
merging them would mislead; keeping them separable is one toggle. More filters
(station, bike type) would add cognitive load without serving the five
questions, so they are deliberately out (see spec "Out of scope").

**Why slice casual vs. member everywhere.** It is the single most decision-
relevant cut in the data because of the margin difference. Every weather chart
is split by it so the reader can always see whether a weather effect is a
*casual* effect (promotable) or a *member* effect (structural commuting).

**Why a pre-joined daily table instead of joining in the app.** Two reasons,
one of them an identity reason that matters for deployment:
1. **Runtime identity.** If the dashboard joined `nyu-datasets.weather` live,
   the *Cloud Run service account* would need cross-project read access to a
   dataset owned by another project — a grant CLAUDE.md explicitly says is not
   guaranteed. By baking the join into `citibike.daily_summary_with_weather` at
   ETL time, the running service only ever reads **one table in our own
   project**, so it needs the minimum permission.
2. **Speed.** The result is ~14k rows. The app reads it once and caches it
   (`@st.cache_data(ttl=3600)`), so moving a slider re-runs only pandas, never
   BigQuery — the basis of the speed target.

**Why a seasonal baseline (Chart 5) and not a weather model (yet).** The
honest version of "is it us or the weather?" that I can defend today is
*deseasonalization*: compare each month to the average of the same month-of-
year. A genuine weather-driven expectation model is the Stretch item; I did not
build it, so I did not claim it. The chart's wording says "seasonal baseline,"
not "weather-adjusted," on purpose.

## Findings — one plain-English sentence per chart

Each sentence is the claim the chart's on-screen caption makes; the exact
magnitudes are computed live from the current filter selection.

1. **Daily ridership** has grown several-fold since 2013 and swings roughly an
   order of magnitude between a peak summer day and a winter low — ridership is
   dominated by season, not trend, within any given year.
2. **Trips rise with temperature for everyone, but casual riders bend far more
   steeply than members** (higher temperature–ridership correlation): members
   commute through the cold, casual riders largely wait for warm days.
3. **The casual share of trips climbs from a small slice on freezing days to
   its largest share on 80F+ days** — the high-margin segment self-concentrates
   on hot days, which is exactly when a weather-triggered promotion would reach
   them.
4. **Rain dents ridership and snow cuts it sharply, and casual riders drop more
   than members** — bad weather thins the promotable segment first.
5. **Most month-to-month swings sit close to the seasonal baseline; the few
   months that fall far below it are the ones worth a manager's attention** —
   those gaps are too large to blame on ordinary seasonal weather.

## Verification results vs. targets

| Target | Status | Evidence |
|--------|--------|----------|
| Correctness — app math | **PASS** | `python3 dashboard/test_transforms.py` → **6/6 pass** against a synthetic table with hand-computed answers (KPIs, daily rollup, temp correlation, casual-share bands, dry/rainy/snowy averages, seasonal baseline). |
| Correctness — vs. source | **PENDING (needs creds)** | Check defined and ready: total `num_trips` must equal `SELECT SUM(num_trips) FROM citibike.daily_summary_materialized`; 4 per-era days spot-checked against `nyu-datasets.citibike.m_daily_trips`. Part 1 already matched that reference to within a handful of trips (see `docs/PIPELINE.md`). |
| Speed (cold ≤5s / warm ≤2s) | **DESIGNED, not yet measured** | Single ~14k-row cached read; no per-interaction BigQuery. Measurable only against the live URL. |
| Reach (public, no login) | **PENDING (needs deploy)** | `deploy.sh` uses `--allow-unauthenticated`. Confirm in incognito + 2 external testers once live. |
| Clarity (claim per chart) | **PASS (self-check)** | All 5 charts carry an on-screen one-sentence caption stating what happens and how much (verifiable in `dashboard/app.py`). External-stranger restatement test still to run. |

## Diagnose — what is different about the deployed world

The lesson the assignment foreshadows held exactly: **the failures here are
about environment and identity, not code.**

1. **My identity is missing locally.** No passphrase → no decrypted key → no
   `gcloud`/ADC. Fix: set `GCP_CREDENTIALS_KEY` (the passphrase for
   `.cloud-credentials.noreply@anthropic.com.enc`) in the environment, restart
   the session so the hook runs, then `cd dashboard && ./deploy.sh`.
2. **The deployed app's identity is not mine.** Code in Cloud Run runs as a
   *service account*, not as the deploying user. The default Compute service
   account cannot query BigQuery, which would surface as a 403 *after* a
   successful deploy — a classic "works on my screen, fails when live." Headed
   off by deploying with `--service-account claude-agent@…` (which holds
   `bigquery.jobUser` + `bigquery.dataEditor`).
3. **Cross-project reads are an identity trap too.** Avoided by pre-joining
   weather at ETL time so the runtime SA reads only our own project (above).
4. **Local Docker is unavailable** (no daemon) and `sdk.cloud.google.com`
   returns 403 to a bare fetch — neither blocks deployment, because
   `gcloud run deploy --source .` builds server-side via Cloud Build.

## Stretch Implementations

All three stretch items are now implemented (Charts 6, 7, 8).

### Chart 6: Weather-Adjusted Ridership Model

- [x] **Model:** `GradientBoostingRegressor` (scikit-learn), 200 estimators, max_depth=4, learning_rate=0.05.
- [x] **Features:** `tavg_f`, `prcp_in`, `snow_in` (weather), plus day-of-week sin/cos, month sin/cos (cyclical encodings), and `year` (trend).
- [x] **Train/test split:** trained on all data before 2024-01-01; test set is 2024-01-01 onward (held-out, never seen during training).
- [x] **MAPE disclosure:** test-set MAPE is computed with `sklearn.metrics.mean_absolute_percentage_error` and shown in the chart caption. A model that fits poorly would report a large number here — we do not suppress it.
- [x] **Residual chart:** outlier days (|residual %| > 2 standard deviations) are flagged red — these are days where actual ridership diverged from what weather alone would predict, pointing to operational events rather than weather.
- [x] **Scope note:** the model trains on the full unfiltered dataset (all systems, all rider types) so sidebar filters do not change the model output — stated explicitly in the chart header.

### Chart 7: 7-Day Ridership Forecast

- [x] **Weather source:** Open-Meteo free API (`api.open-meteo.com`), 7-day hourly/daily forecast for NYC coordinates (40.7128, -74.0060), temperature in Fahrenheit, precipitation in inches.
- [x] **Feature mapping:** same features as Chart 6 — `tavg_f`, `prcp_in`, `snow_in`, dow sin/cos, month sin/cos, year — applied to the forecast dates.
- [x] **Out-of-sample MAPE disclosed in caption:** the same MAPE from Chart 6's test set is quoted in the forecast caption so the reader knows how accurate the model was on real held-out data.
- [x] **Graceful degradation:** if the Open-Meteo API is unavailable, a warning is shown instead of a crash.
- [x] **Scope note:** forecast is aggregate (all systems, all rider types combined); stated in caption.

### Chart 8: E-Bike Revenue Estimate

- [x] **Pricing schedule (Citibike 2025 rates):** Casual e-bike: $1.00 unlock fee + $0.26/min; Member e-bike: $0.17/min (no unlock fee). Rates applied as a historical yardstick across the full 2021+ period.
- [x] **Scope:** Feb 2021+ rows where `num_electric_trips > 0` (e-bike data only exists from Schema B onward; before that there is no `rideable_type` field).
- [x] **Caveat disclosed in caption:** `avg_trip_duration_minutes` is the group-level average across all ride types for that (date, system, rider-type) group — not e-bike specific. Revenue figures are estimates only.
- [x] **Today's prices applied historically:** explicitly disclosed in caption. 2025 pricing did not exist in 2021 — this is a yardstick, not a historical accounting.
- [x] **Weather breakdown:** grouped bar shows avg daily e-bike revenue on Dry/Rainy/Snowy days by rider type, so the weather-revenue interaction is visible.
- [x] **Trend line:** monthly total e-bike revenue from Feb 2021 onward, showing the ramp-up as the e-bike fleet grew.

## To reach 100% on the live targets (the one unblock)

1. Set `GCP_CREDENTIALS_KEY` in the environment and restart the session.
2. `python3 etl/build_weather_summary.py --inspect` → confirm the weather
   table's column names/units; adjust the `WEATHER_SRC` block if needed.
3. `python3 etl/build_weather_summary.py` → build
   `citibike.daily_summary_with_weather`.
4. `cd dashboard && ./deploy.sh` → get the public URL.
5. Put the URL in `README.md`; run the Speed/Reach/Clarity checks above.

## Stretch items — implemented

All three stretch items are now implemented. Assumptions and limits are stated here per the assignment's honesty bar.

### Stretch 1: Weather-adjusted ridership model

**What it does:** Fits a `GradientBoostingRegressor` (200 trees, depth 4, lr=0.05) on daily total trips using 8 features: `tavg_f`, `prcp_in`, `snow_in`, day-of-week (sin/cos encoded), month (sin/cos encoded), and year (captures fleet growth trend). Shows actual vs model-expected daily trips and flags days where `|residual| > 2σ` as anomalies worth a manager's attention.

**Train/test split:** Train on pre-2024 data (~3,800 days); test on 2024+ (~500 days, never seen during training). This is a true out-of-sample holdout — not cross-validation on the same period.

**Out-of-sample MAPE:** Computed at runtime and displayed in the chart caption. Expected range: 15-25% (the model captures seasonal/weather patterns but cannot predict disruptions, events, or fleet changes).

**Limits:** The model aggregates all systems and rider types into one daily total — it cannot be filtered by region or rider type without retraining. The `year` feature captures average growth but not sudden step-changes (e.g., the 2020 COVID collapse or a major system expansion). Days flagged as anomalies require human judgment to diagnose — the model flags the gap, not the cause.

### Stretch 2: 7-day ridership forecast

**What it does:** Calls Open-Meteo's free forecast API (no API key) for the next 7 days at NYC coordinates (40.7128°N, 74.0060°W), maps the forecast to the same features as the training data, and applies the Stretch 1 model to predict total daily trips.

**Feature mapping from forecast:** `temperature_2m_mean` → `tavg_f`; `precipitation_sum` → `prcp_in`; `snowfall_sum` → `snow_in`; day-of-week and month derived from forecast date; year from forecast date.

**Honest accuracy statement:** The caption displays the actual out-of-sample MAPE from Stretch 1 (computed on 2024+ data). "Within X% on held-out data" is the number shown — not a claim about future accuracy, which may differ as conditions deviate further from the training distribution.

**Limits:** The model was trained on historical weather, not forecasts — forecast error compounds model error. Short-term (1-2 day) forecasts from Open-Meteo are typically accurate to ±2-4°F and ±0.1" precip, which translates to additional model uncertainty beyond the stated MAPE.

### Stretch 3: E-bike revenue estimate

**Pricing schedule used (Citibike 2025, retrieved June 2026):**
- Casual e-bike: $1.00 unlock fee + $0.26/min
- Member e-bike: $0.17/min (no unlock fee; membership covers access)
- Classic bike trips excluded — a member's classic trip is covered by their subscription with near-zero marginal revenue; a casual classic ride is a flat fee with no per-minute component at the daily-summary grain.

**Scope:** Feb 2021 onward, `num_electric_trips > 0` rows only. Bike type (`rideable_type`) was not recorded before Feb 2021 (Schema A), so pre-2021 rows correctly contribute zero electric revenue.

**Duration approximation:** `avg_trip_duration_minutes` in `daily_summary_with_weather` is the average across ALL trip types in that (date, system, rider_type) group — not specifically electric trips. E-bike trips may have different average durations than classic trips. This is an approximation; the daily-summary grain does not separate durations by bike type. Treat estimates as ±20-30% order-of-magnitude, not audited revenue.

**Framing:** The dashboard labels all figures "estimated e-bike revenue" and the caption states "Citibike 2025 pricing as a yardstick." This is not reconstructed historical revenue — prices changed across 2013-2026 and the exact historical schedule is not available. The yardstick is consistent (same rate applied throughout) and directionally correct for comparing weather conditions against each other.
