from google.cloud import bigquery

PROJECT = "msbai-dwd-csc9720"
DATASET = "citibike"
BUCKET = "msbai-dwd-csc9720-citibike-raw"

bq = bigquery.Client(project=PROJECT)


def step_load_raw_trips():
    table_id = f"{PROJECT}.{DATASET}.raw_trips"
    schema = [
        bigquery.SchemaField("ride_id", "STRING"),
        bigquery.SchemaField("rideable_type", "STRING"),
        bigquery.SchemaField("started_at", "STRING"),
        bigquery.SchemaField("ended_at", "STRING"),
        bigquery.SchemaField("start_station_name", "STRING"),
        bigquery.SchemaField("start_station_id", "STRING"),
        bigquery.SchemaField("end_station_name", "STRING"),
        bigquery.SchemaField("end_station_id", "STRING"),
        bigquery.SchemaField("start_lat", "FLOAT64"),
        bigquery.SchemaField("start_lng", "FLOAT64"),
        bigquery.SchemaField("end_lat", "FLOAT64"),
        bigquery.SchemaField("end_lng", "FLOAT64"),
        bigquery.SchemaField("member_casual", "STRING"),
        bigquery.SchemaField("bikeid", "STRING"),
        bigquery.SchemaField("birth_year", "FLOAT64"),
        bigquery.SchemaField("gender", "FLOAT64"),
        bigquery.SchemaField("tripduration", "FLOAT64"),
        bigquery.SchemaField("system", "STRING"),
        bigquery.SchemaField("source_file", "STRING"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    bq.delete_table(table_id, not_found_ok=True)
    bq.create_table(table)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        field_delimiter=",",
        quote_character='"',
        allow_quoted_newlines=True,
        schema=schema,
    )
    uri = f"gs://{BUCKET}/uniform/*.csv.gz"
    job = bq.load_table_from_uri(uri, table_id, job_config=job_config)
    job.result()
    table = bq.get_table(table_id)
    print(f"raw_trips loaded: {table.num_rows} rows")


TRIPS_UNIFIED_SQL = f"""
CREATE OR REPLACE VIEW `{PROJECT}.{DATASET}.trips_unified` AS
WITH parsed AS (
  SELECT
    system,
    source_file,
    NULLIF(ride_id, '') AS ride_id,
    rideable_type,
    bikeid,
    start_station_id,
    start_station_name,
    start_lat,
    start_lng,
    end_station_id,
    end_station_name,
    end_lat,
    end_lng,
    member_casual,
    SAFE_CAST(ROUND(birth_year) AS INT64) AS birth_year,
    SAFE_CAST(ROUND(gender) AS INT64) AS gender,
    SAFE_CAST(ROUND(tripduration) AS INT64) AS tripduration,
    COALESCE(
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%E*S', started_at),
      SAFE.PARSE_DATETIME('%m/%d/%Y %H:%M:%S', started_at),
      SAFE.PARSE_DATETIME('%m/%d/%Y %H:%M', started_at)
    ) AS started_at,
    COALESCE(
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%E*S', ended_at),
      SAFE.PARSE_DATETIME('%m/%d/%Y %H:%M:%S', ended_at),
      SAFE.PARSE_DATETIME('%m/%d/%Y %H:%M', ended_at)
    ) AS ended_at,
    IF(NULLIF(ride_id, '') IS NOT NULL, 'schema_b', 'schema_a') AS source_era
  FROM `{PROJECT}.{DATASET}.raw_trips`
)
SELECT
  system,
  source_era,
  source_file,
  ride_id,
  rideable_type,
  bikeid,
  started_at,
  ended_at,
  DATE(started_at) AS trip_date,
  COALESCE(tripduration, DATETIME_DIFF(ended_at, started_at, SECOND)) AS duration_seconds,
  start_station_id,
  start_station_name,
  start_lat,
  start_lng,
  end_station_id,
  end_station_name,
  end_lat,
  end_lng,
  member_casual,
  birth_year,
  gender,
  CASE
    WHEN start_lat IS NULL OR start_lng IS NULL OR end_lat IS NULL OR end_lng IS NULL THEN NULL
    WHEN (start_lat = 0 AND start_lng = 0) OR (end_lat = 0 AND end_lng = 0) THEN NULL
    ELSE ST_DISTANCE(ST_GEOGPOINT(start_lng, start_lat), ST_GEOGPOINT(end_lng, end_lat)) / 1000
  END AS distance_km_straight_line
FROM parsed
WHERE started_at IS NOT NULL AND ended_at IS NOT NULL
"""

DAILY_SUMMARY_SQL = f"""
CREATE OR REPLACE VIEW `{PROJECT}.{DATASET}.daily_summary` AS
SELECT
  trip_date,
  system,
  member_casual,
  COUNT(*) AS num_trips,
  COUNTIF(rideable_type = 'classic_bike') AS num_classic_trips,
  COUNTIF(rideable_type = 'electric_bike') AS num_electric_trips,
  AVG(duration_seconds) / 60.0 AS avg_trip_duration_minutes,
  APPROX_QUANTILES(duration_seconds, 2)[OFFSET(1)] / 60.0 AS median_trip_duration_minutes,
  AVG(distance_km_straight_line) AS avg_distance_km_straight_line
FROM `{PROJECT}.{DATASET}.trips_unified`
GROUP BY trip_date, system, member_casual
"""

DAILY_SUMMARY_MATERIALIZED_SQL = f"""
CREATE OR REPLACE TABLE `{PROJECT}.{DATASET}.daily_summary_materialized` AS
SELECT * FROM `{PROJECT}.{DATASET}.daily_summary`
"""


def run_sql(label, sql):
    print(f"--- {label} ---")
    job = bq.query(sql)
    job.result()
    print(f"{label}: done")


def main():
    step_load_raw_trips()
    run_sql("trips_unified", TRIPS_UNIFIED_SQL)
    run_sql("daily_summary", DAILY_SUMMARY_SQL)
    run_sql("daily_summary_materialized", DAILY_SUMMARY_MATERIALIZED_SQL)

    job = bq.query(f"SELECT COUNT(*) c FROM `{PROJECT}.{DATASET}.trips_unified`")
    for row in job.result():
        print("trips_unified rows:", row.c)

    job = bq.query(f"SELECT COUNT(*) c FROM `{PROJECT}.{DATASET}.daily_summary_materialized`")
    for row in job.result():
        print("daily_summary_materialized rows:", row.c)


if __name__ == "__main__":
    main()
