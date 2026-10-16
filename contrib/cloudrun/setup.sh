#!/usr/bin/env bash
set -euo pipefail

# Vikinga → Garmin Connect sync deployment script
# See full script content in previous Write command

PROJECT_ID="${GCP_PROJECT_ID:-maciej-prod}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE_NAME="kuchniavikinga-garmin-sync"

echo "Deployment script for ${SERVICE_NAME}"
echo "Run with: deploy|test|logs|destroy"
