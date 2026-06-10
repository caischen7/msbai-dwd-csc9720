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
