#!/bin/bash
# Deploy the Citibike + weather dashboard to Cloud Run as a public service.
#
# Prereqs (see docs/DASHBOARD_DECISIONS.md "Diagnose"):
#   - gcloud is authenticated as a principal that can deploy (the cloud-auth
#     hook does this automatically when GCP_CREDENTIALS_KEY is set).
#   - The table citibike.daily_summary_with_weather exists
#     (build it with: python3 etl/build_weather_summary.py).
#
# Run from the dashboard/ directory:  ./deploy.sh
set -euo pipefail

PROJECT="msbai-dwd-csc9720"
REGION="us-central1"
SERVICE="citibike-dashboard"
# Run the service AS the service account that already holds bigquery.jobUser +
# bigquery.dataEditor. This is the crux of the deployment: the code that runs
# in Cloud Run is NOT "you", it is this service account, and it is the one that
# must be allowed to query BigQuery. The default Compute service account is not.
RUNTIME_SA="claude-agent@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT"

# APIs needed to build (Cloud Build) and serve (Run); harmless if already on.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# --source . builds the image with Cloud Build (server-side; no local Docker
# needed), then deploys it. --allow-unauthenticated makes the URL public with
# no Google login — this is the "Reach" target.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

echo
echo "Deployed. Public URL:"
gcloud run services describe "$SERVICE" --region "$REGION" \
  --format 'value(status.url)'
