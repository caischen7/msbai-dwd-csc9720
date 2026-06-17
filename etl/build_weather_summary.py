"""Build `citibike.daily_summary_with_weather` — the single small table the
Streamlit dashboard reads.

This is the bottom of the staircase from docs/PIPELINE.md:

    daily_summary_materialized (one row per date x system x rider type)
      LEFT JOIN  nyu-datasets.weather.m_weather_daily_nyc  (one row per date)
      -> daily_summary_with_weather  (the dashboard's source table)

Why a LEFT JOIN: a missing weather day must still show its trips (we would
rather under-annotate weather than silently drop ridership). Rows with no
weather match get NULL temp/precip, which the dashboard already treats as
"unknown weather" via dropna() before the weather-specific charts.

Why pre-join into a physical table instead of joining in the dashboard:
  1. The dashboard's runtime service account then only needs to read ONE table
     in *our* project — it never needs cross-project read access to
     `nyu-datasets.weather`. (That grant is not guaranteed for the Cloud Run
     identity; see docs/DASHBOARD_DECISIONS.md, "Diagnose".)
  2. The result is tiny (~14k rows), so the app loads it once and caches it.

-------------------------------------------------------------------------------
ASSUMPTION TO CONFIRM ON FIRST LIVE RUN  (run with --inspect first):

  `nyu-datasets.weather.m_weather_daily_nyc` is assumed to expose:
    date  DATE          -- confirmed in CLAUDE.md (the documented join key)
    tavg, tmax, tmin    -- daily temperatures, ASSUMED already in FAHRENHEIT
    prcp                -- precipitation, ASSUMED in INCHES
    snow                -- snowfall,      ASSUMED in INCHES

  These names/units are conventional for an NWS/GHCN-derived daily table but
  are NOT verified from this environment (no BigQuery access at authoring
  time). `python build_weather_summary.py --inspect` prints the real schema
  and a 5-row sample. If the columns differ, edit WEATHER_SRC below — it is the
  ONE place the mapping lives. If temps are in Celsius, switch the marked line
  to the *9/5+32 conversion. Nothing else in the pipeline or dashboard changes.
-------------------------------------------------------------------------------
"""

from google.cloud import bigquery

PROJECT = "msbai-dwd-csc9720"
DATASET = "citibike"
WEATHER_TABLE = "nyu-datasets.weather.m_weather_daily_nyc"

bq = bigquery.Client(project=PROJECT)


def inspect_weather():
    """Print the weather table's real schema + a sample so the mapping below
    can be confirmed/corrected before the first build."""
    print(f"--- INFORMATION_SCHEMA for {WEATHER_TABLE} ---")
    proj, dset, tbl = WEATHER_TABLE.split(".")
    for row in bq.query(f"""
        SELECT column_name, data_type
        FROM `{proj}.{dset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = '{tbl}'
        ORDER BY ordinal_position
    """).result():
        print(f"  {row.column_name:<24} {row.data_type}")
    print(f"--- 5-row sample of {WEATHER_TABLE} ---")
    for row in bq.query(f"SELECT * FROM `{WEATHER_TABLE}` ORDER BY date LIMIT 5").result():
        print(" ", dict(row))


# The ONE place the weather-source mapping lives. Adjust here if --inspect
# shows different column names or units.
WEATHER_SRC = f"""
  SELECT
    date AS w_date,
    tavg_f,
    tmax_f,
    tmin_f,
    prcp_inches AS prcp_in,
    snow_inches AS snow_in,
    CAST(is_rainy AS BOOL) AS is_rainy,
    CAST(COALESCE(is_snowy, 0) AS BOOL) AS is_snowy
  FROM `{WEATHER_TABLE}`
"""

BUILD_SQL = f"""
CREATE OR REPLACE TABLE `{PROJECT}.{DATASET}.daily_summary_with_weather` AS
WITH weather AS ({WEATHER_SRC})
SELECT
  d.trip_date,
  d.system,
  d.member_casual,
  d.num_trips,
  d.num_classic_trips,
  d.num_electric_trips,
  d.avg_trip_duration_minutes,
  d.median_trip_duration_minutes,
  d.avg_distance_km_straight_line,
  w.tavg_f,
  w.tmax_f,
  w.tmin_f,
  w.prcp_in,
  w.snow_in,
  w.is_rainy,
  w.is_snowy
FROM `{PROJECT}.{DATASET}.daily_summary_materialized` d
LEFT JOIN weather w
  ON d.trip_date = w.w_date
"""


def build():
    print("--- building daily_summary_with_weather ---")
    bq.query(BUILD_SQL).result()
    table_id = f"{PROJECT}.{DATASET}.daily_summary_with_weather"

    n_rows = next(bq.query(f"SELECT COUNT(*) c FROM `{table_id}`").result()).c
    n_src = next(bq.query(
        f"SELECT COUNT(*) c FROM `{PROJECT}.{DATASET}.daily_summary_materialized`"
    ).result()).c
    n_weather = next(bq.query(
        f"SELECT COUNTIF(tavg_f IS NOT NULL) c FROM `{table_id}`"
    ).result()).c

    print(f"daily_summary_with_weather: {n_rows} rows")
    print(f"  source daily_summary_materialized: {n_src} rows "
          f"({'MATCH' if n_rows == n_src else 'MISMATCH — LEFT JOIN should preserve row count!'})")
    print(f"  rows with a weather match: {n_weather} "
          f"({100 * n_weather / n_rows:.1f}%)")


if __name__ == "__main__":
    import sys

    if "--inspect" in sys.argv:
        inspect_weather()
    else:
        build()
