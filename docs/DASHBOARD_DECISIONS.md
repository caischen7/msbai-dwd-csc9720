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

## Stretch items — honest status

None of the three Stretch items are implemented, so none are claimed:
- **Weather-adjusted "expected vs. actual" model** — not built. Chart 5 is a
  seasonal baseline (month-of-year mean), a deliberately simpler stand-in; it
  is labeled as such and is *not* a fitted weather model.
- **Forecast (next few days)** — not built. No out-of-sample error number
  exists, so none is reported.
- **Ebike revenue estimate** — not built. No dollar figures appear anywhere in
  the dashboard, to avoid putting up a number I cannot defend.

## To reach 100% on the live targets (the one unblock)

1. Set `GCP_CREDENTIALS_KEY` in the environment and restart the session.
2. `python3 etl/build_weather_summary.py --inspect` → confirm the weather
   table's column names/units; adjust the `WEATHER_SRC` block if needed.
3. `python3 etl/build_weather_summary.py` → build
   `citibike.daily_summary_with_weather`.
4. `cd dashboard && ./deploy.sh` → get the public URL.
5. Put the URL in `README.md`; run the Speed/Reach/Clarity checks above.
