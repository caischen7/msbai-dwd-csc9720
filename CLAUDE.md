## Cloud Credentials

This repo uses the `cloud-bootstrap` skill to manage GCP access.

- **Provider:** GCP
- **Project:** `msbai-dwd-csc9720`
- **Service account:** `claude-agent@msbai-dwd-csc9720.iam.gserviceaccount.com`
- **Roles granted:**
  - `roles/bigquery.dataEditor` — create/write tables in the project's BigQuery dataset for Citibike + weather data
  - `roles/bigquery.jobUser` — run BigQuery load/query jobs for the ETL pipeline and dashboard
  - `roles/run.developer` — deploy/update the Streamlit dashboard as a Cloud Run service
  - `roles/storage.admin` — create/manage the GCS bucket holding raw Citibike data

### Storage

Raw Citibike trip data (downloaded from the public `s3://tripdata` bucket) is mirrored to the GCS bucket `msbai-dwd-csc9720-citibike-raw`.

Note: reading `nyu-datasets.weather.m_weather_daily_nyc` is a cross-project read. The `nyu-datasets` project owner must separately grant this service account (or the relevant user) read access to that dataset; it is not covered by the roles above.

### Multi-user setup

This is a multi-user setup: each team member has their own encrypted credentials file, `.cloud-credentials.<git-email>.enc`, encrypted with their own passphrase (`GCP_CREDENTIALS_KEY` or `CLOUD_CREDENTIALS_KEY` environment variable). Passphrases are never shared or committed.

### Authentication

Authentication happens automatically via the `SessionStart` hook (`.claude/hooks/cloud-auth.sh`), which decrypts this user's credentials file and activates `gcloud` + Application Default Credentials for the session. No manual steps are needed as long as the passphrase environment variable is set.

### Adding a team member

When a new team member opens this repo and has no matching `.cloud-credentials.<email>.enc` file, the `cloud-bootstrap` skill's "Add Team Member" workflow creates a new key for the same service account, encrypted with that member's own passphrase.

### Escalating permissions

If a command fails with a permission error (403 / access denied), the `cloud-bootstrap` skill's "Permission Escalation" workflow identifies the likely missing role and asks the project owner to grant it via the GCP console. The agent does not modify IAM policies on its own in this case.

## Citibike Data Pipeline Design Decisions

These decisions are based on directly inspecting the contents of `https://s3.amazonaws.com/tripdata/` (object listing, zip central directories, and sample CSV headers from multiple years) — not assumptions about naming conventions.

### 1. What files actually exist (and why it matters)

The bucket is **not** a tidy "one file per month" archive. It contains several inconsistent layouts:

- **Yearly archives `20{13..23}-citibike-tripdata.zip`** (NYC only): each is a *zip of zips/CSVs* with messy internal structure that varies by year:
  - 2013: a flat mix of loose per-month CSVs, empty month-named folders, `.DS_Store`, `__MACOSX/._*` AppleDouble junk files, and even a manually split `12_December/201312-citibike-tripdata_1.csv`.
  - 2017: per-month folders, each containing the month's data pre-split into `_1.csv`/`_2.csv` (GitHub/Excel row-limit splits), no single canonical file per month.
  - 2021: per-month folders each containing a *nested zip* (`202101-citibike-tripdata.zip`, etc.), again with `__MACOSX` junk.
- **Monthly NYC archives `YYYYMM-citibike-tripdata.zip`** (2024 onward): each contains the month's data pre-split into `_1.csv`/`_2.csv` (e.g. `202401-citibike-tripdata_1.csv` / `_2.csv`).
- **Monthly Jersey City archives `JC-YYYYMM-citibike-tripdata.csv.zip`** (Sept 2015 onward): usually one CSV, but with real naming inconsistencies that a naive glob/regex would miss:
  - `JC-201708 citibike-tripdata.csv.zip` — space instead of `-`.
  - `JC-202207-citbike-tripdata.csv.zip` — misspelled "citbike".
  - `JC-202510-citibike-tripdata.zip`, `JC-202601-citibike-tripdata.zip`, `JC-202604-citibike-tripdata.zip` — `.zip` instead of `.csv.zip`.
  - Some include `__MACOSX/._*` junk entries alongside the real CSV.
- `index.html` is a webpage, not data — excluded.

**Implication:** the ETL cannot assume a fixed filename pattern or a single CSV per archive. It must (a) recursively unzip until it finds actual `*.csv` files, (b) explicitly filter out `__MACOSX/`, `.DS_Store`, and zero-byte directory entries, and (c) treat every remaining CSV as a fragment to be unioned, not as "the" file for that month.

### 2. NYC vs. Jersey City — yes, distinguish them

NYC and Jersey City are operated as separate Citi Bike systems with separate station namespaces (JC station IDs in the old schema are small integers like `3185`/`3211`/`3273`, which can collide numerically with NYC station IDs). They are shipped as separate files (`JC-` prefix vs. no prefix), which makes tagging cheap and reliable.

**Decision:** every loaded row gets a `system` column (`'NYC'` or `'JC'`) derived from the source filename at load time, *before* any union. This lets the dashboard filter/segment by system and avoids silently merging two different bike-share networks (different fleet sizes, different geography, different growth curves) into one undifferentiated "citywide" number.

### 3. How far back, and handling the two schemas

**Decision: load the full history back to 2013**, since the project goal is to analyze real operating data over time (this is the whole point of using "messy" real data rather than a clean teaching extract).

By directly inspecting headers from 2016, 2017, 2020, 2021 (Jan), and 2021 (Feb) onward, there are exactly **two schema eras**, with a header-casing wrinkle inside the older one:

- **Schema A — "classic" (2013 through Jan 2021), 15 columns:**
  `tripduration, starttime, stoptime, start station id, start station name, start station latitude, start station longitude, end station id, end station name, end station latitude, end station longitude, bikeid, usertype, birth year, gender`
  - 2013–2017 files use **Title Case headers with spaces** (`Trip Duration`, `Start Time`, `Bike ID`, ...).
  - 2018–Jan 2021 files use **lowercase, no-space headers** (`tripduration`, `starttime`, `bikeid`, ...).
  - Same 15 fields, same order, in both — only the header text differs.
- **Schema B — "GBFS-style" (Feb 2021 onward), 13 columns:**
  `ride_id, rideable_type, started_at, ended_at, start_station_name, start_station_id, end_station_name, end_station_id, start_lat, start_lng, end_lat, end_lng, member_casual`
  - No `bikeid`, `birth year`, or `gender` at all from this point forward.
  - The transition is a clean month boundary: NYC/JC `*-202101-*` files are Schema A, `*-202102-*` onward are Schema B.

**Decision:**
- Define one canonical BigQuery table schema with: `system, source_file, started_at, ended_at, duration_seconds, ride_id, rideable_type_or_bike_id, start_station_id, start_station_name, start_lat, start_lng, end_station_id, end_station_name, end_lat, end_lng, user_type, birth_year, gender`.
- Load by **position/explicit column mapping per schema era**, not BigQuery header autodetect — autodetect would treat `Trip Duration` and `tripduration` as different columns and silently produce sparse/duplicated columns.
- `birth_year` and `gender` are `NULL` for every Schema-B (Feb 2021+) row — this is a real data limitation, not a load bug, and must be **disclosed** in any dashboard view that breaks down by age/gender (those views are 2013–Jan 2021 only).
- `duration_seconds`: present directly in Schema A (`tripduration`); for Schema B, compute as `TIMESTAMP_DIFF(ended_at, started_at, SECOND)`.

### 4. Trip distance — not in the data; compute, with caveats

**Looked first:** neither schema has a distance/length column. Both schemas do have start/end station latitude/longitude.

**Decision:** compute **straight-line (great-circle) distance** between start and end station coordinates using BigQuery's native `ST_DISTANCE(ST_GEOGPOINT(start_lng, start_lat), ST_GEOGPOINT(end_lng, end_lat))`. This is a vectorized SQL geo function — it scales to the full 300M+ row history at query/load time with no external calls, no per-row API cost, and no rate limits.

**Rejected: Google Maps Directions/Routes API.** At 300M+ trips, even at the cheapest published per-request rate this would cost well into six figures and hit rate limits hard; it's also not defensible historically — current street/bike-lane routing doesn't reflect the bike network as it existed in, say, 2014.

**Must disclose in the dashboard:**
- This is **straight-line distance between station locations**, labeled `distance_km_straight_line` — not actual ridden distance. Real bike routes following streets/lanes are typically 20–40% longer than straight-line.
- **Round trips** (start station == end station, common for leisure/loop rides) get `distance_km_straight_line = 0` even though the bike clearly traveled. This will under-represent total mileage for loop trips. Pair distance with `duration_seconds` in any "trip length" visualization so loop trips aren't misread as "no trip."
- A small number of historical rows have `0,0` or missing station coordinates (depot/maintenance records); these should be filtered or flagged rather than treated as valid zero-distance trips.

### 5. What is a "day"? (for the weather join)

Citibike timestamps (`starttime`/`started_at`, `stoptime`/`ended_at`) are recorded as naive local-time values (no UTC offset or timezone field) in **America/New_York** local time — the timezone the system actually operates in, and the same timezone `nyu-datasets.weather.m_weather_daily_nyc` uses for its `date` column.

**Decision:** a trip's "day" is the **calendar date of `started_at`, in America/New_York local time** (taken at face value, with no further timezone conversion since the source is already local). This is the join key against `m_weather_daily_nyc.date`.

**Why start time, not end time:** the weather join is meant to capture the conditions a rider experienced/decided to ride in. For the small fraction of trips that cross midnight, using the start time means a late-night ride that ends after midnight is still attributed to the day it began — which is when the weather-driven decision to take the trip was made. Using end time would instead occasionally attribute a trip to the *next* day's weather, which is harder to justify and would also shift trip counts for "today" depending on how long trips ran, making day-over-day volume comparisons noisier.

**Caveat to disclose:** trips starting just before midnight and ending just after are counted entirely under the start day for both volume and weather-join purposes, even though part of the ride happened on the following calendar day. This affects a tiny fraction of trips and is not corrected for.
