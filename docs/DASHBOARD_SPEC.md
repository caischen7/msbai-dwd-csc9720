# Part 2 Dashboard — Specification

This is the spec I am committing to *before* claiming the dashboard is done. It
states who the dashboard is for, the questions it answers, what a visitor can
filter and slice, and the concrete, checkable targets that define "done." The
decisions memo (`docs/DASHBOARD_DECISIONS.md`) defends each choice and reports
results against these targets.

## Who it is for

A **curious, non-technical visitor** — picture a journalist or a NYC
transportation planner, not a data analyst. They can read a line chart and a
bar chart. They will not write SQL, will not read a methodology footnote
before looking at the picture, and will leave if the first screen is blank for
more than a few seconds. Every chart therefore has to carry its own one-
sentence, plain-English takeaway on the screen, next to the chart.

## The questions it answers

1. **How has ridership grown, and how seasonal is it?**
   (Chart 1 — daily trips over time + 28-day trend.)
2. **How strongly does temperature drive ridership, and is that the same for
   casual riders and members?**
   (Chart 2 — trips vs. temperature, split by rider type.)
3. **Are casual riders — the high-margin segment — concentrated on warm days?
   I.e., is there a weather-targetable promotion opportunity?**
   (Chart 3 — casual share of trips by temperature band.)
4. **How much does rain or snow actually suppress ridership, and for whom?**
   (Chart 4 — average trips/day on dry vs. rainy vs. snowy days, by rider type.)
5. **"Is it us or the weather?" — when ridership moves, is it ordinary
   seasonality, or something operational worth a manager's attention?**
   (Chart 5 — monthly actual vs. a seasonal baseline, with the biggest
   over/under-performing months called out.)

Questions 3 and 4 are the business core: casual riders carry **significantly
higher margins** than members, so knowing *when* (which weather) and *where*
(which region) they ride most is directly actionable for promotions.

## What a visitor can filter

- **Date range** — any window from 2013-06 to the latest loaded month.
- **Region** — NYC and/or Jersey City (separate bike-share systems; see
  Part 1 decision 2).
- **Rider type** — member (subscriber) and/or casual.

All three filters apply to all five charts simultaneously.

## Which slices are exposed

The underlying table is one row per **(date × region × rider type)**, so the
dashboard can slice by:
- region (NYC vs. JC),
- rider type (member vs. casual),
- time (day, and month-of-year for the seasonal baseline),
- temperature band, and dry/rainy/snowy day type (from the weather join).

These are real group-by dimensions, not hard-coded columns.

## What "good" means — Verify targets (concrete, checkable by someone else)

| # | Target | How it is checked | Pass threshold |
|---|--------|-------------------|----------------|
| **Correctness — app math** | The charts compute what they claim from their input. | `python3 dashboard/test_transforms.py` against a synthetic table with hand-computed answers. | **6/6 tests pass.** |
| **Correctness — vs. source** | The dashboard's totals equal the daily table they come from. | Sum `num_trips` over the full date range in the dashboard; compare to `SELECT SUM(num_trips) FROM citibike.daily_summary_materialized`. Spot-check 4 specific days (one per era: 2013, 2019, 2021 cutover, a recent one) against `nyu-datasets.citibike.m_daily_trips`. | Total **exact match** to our own table; per-day within a **handful of trips** of the NYU reference. |
| **Speed** | First meaningful paint for a visitor who will not wait. | Hard refresh the public URL (cold cache disabled), stopwatch to first chart. Repeat warm. | **Cold ≤ 5 s, warm ≤ 2 s.** The app reads the ~14k-row table once via `@st.cache_data(ttl=3600)`; no BigQuery call on slider moves. |
| **Reach** | The URL is truly public — no Google login wall. | Open in a private/incognito window with no Google session; have at least **2 other people** open it on their own devices/networks. | **All testers reach the dashboard with no login.** |
| **Clarity** | A stranger understands each chart unaided. | Every chart has a one-sentence caption stating *what happens and how much*. Show it to someone who has not seen the project; ask them to state each chart's point in one sentence. | **All 5 charts** carry a visible claim; tester restates each correctly. |

## Out of scope (stated so it is not mistaken for missing)

- Station-level / map views — the daily grain has no per-station rows.
- Age/gender breakdowns — those fields are NULL from Feb 2021 onward
  (Part 1 decision 3), so a time-series of them would be misleading.
- The three Stretch items (weather-adjusted model, forecast, ebike revenue)
  are explicitly optional; their status is reported in the decisions memo.
