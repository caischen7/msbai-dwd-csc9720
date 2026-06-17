# msbai-dwd-csc9720

Class project for *Dealing with Data*: an ETL pipeline that loads the full
Citibike trip history (2013–2026) into BigQuery, joins it to NYC daily weather,
and serves an interactive Streamlit dashboard on Cloud Run.

## Public dashboard URL

**https://citibike-dashboard-894638510471.us-central1.run.app**

No login required. Loads the full interactive dashboard (12 charts) from a
cached daily table — no per-interaction BigQuery queries.

## What's here

```
etl/                       Part 1 — S3 -> GCS -> BigQuery pipeline
  uniform_transform.py     S3/GCS archives -> normalized 19-col CSVs (parallel)
  build_pipeline.py        uniform CSVs -> raw_trips + trips_unified + daily_summary(+_materialized)
  build_weather_summary.py daily_summary_materialized JOIN weather -> daily_summary_with_weather
dashboard/                 Part 2 — Streamlit app
  app.py                   the dashboard (reads one cached daily table)
  transforms.py            pure aggregation logic (no Streamlit/BigQuery)
  test_transforms.py       offline correctness check (hand-computed answers)
  Dockerfile               Cloud Run container
  deploy.sh                one-command deploy to a public Cloud Run URL
docs/
  PIPELINE.md              Part 1 design decisions + validation
  DASHBOARD_SPEC.md        Part 2 spec: questions, filters, slices, verify targets
  DASHBOARD_DECISIONS.md   Part 2 memo: defends the spec + reports verification
DECISIONS.md               Combined decisions memo for both parts (Translate work)
```

## The dashboard

Five tabs, all filterable by date range, region (NYC/JC), and rider type
(member/casual). Data source: `citibike.daily_summary_with_weather`
(~14k rows) cached once per hour — no per-click BigQuery queries.

| Tab | Contents |
|-----|----------|
| 📊 Overview | KPI cards, daily ridership trend, YoY growth %, weekday vs. weekend split |
| 🌡️ Weather Impact | Temperature scatter, casual-share by temp band, rain/snow suppression |
| 🔬 Model (Stretch 1) | GradientBoosting weather model, actual-vs-expected, anomaly table |
| 🔮 Forecast (Stretch 2) | 7-day predicted ridership from Open-Meteo API (no key needed) |
| 💰 E-Bike Revenue (Stretch 3) | Daily revenue estimate using 2025 Citibike pricing (Feb 2021+) |

### Run the tests (no cloud access needed)

```bash
python3 dashboard/test_transforms.py     # 6/6 should pass
```

### Run locally

```bash
pip install -r dashboard/requirements.txt
# requires active GCP credentials (Application Default Credentials)
streamlit run dashboard/app.py
```

### Build the data table the dashboard reads

```bash
python3 etl/build_weather_summary.py --inspect   # confirm weather schema/units
python3 etl/build_weather_summary.py             # build daily_summary_with_weather
```

### Deploy to a public URL

```bash
cd dashboard && ./deploy.sh                       # builds via Cloud Build, deploys to Cloud Run
```

## Teaching team access

The following people have been granted **collaborator access** to this GitHub repo
and **BigQuery Data Viewer** access to the `msbai-dwd-csc9720` GCP project:

- pi1@stern.nyu.edu
- it2190@stern.nyu.edu

## Cost controls

A **$10 billing budget alert** is set on the `msbai-dwd-csc9720` GCP project
(Billing → Budgets & alerts). The Cloud Run dashboard scales to zero when not in
use and costs essentially nothing to leave running for grading.

## Cloud setup

Credentials are managed by the `cloud-bootstrap` skill; see
[`CLAUDE.md`](CLAUDE.md). Authentication is automatic via the `SessionStart`
hook **when the passphrase env var is set**.
