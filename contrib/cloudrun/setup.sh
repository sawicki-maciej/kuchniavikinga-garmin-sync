#!/usr/bin/env bash
#
# Kuchnia Vikinga → Garmin Connect sync deployment script
# Idempotent deployment to GCP Cloud Run Job
#
# Usage:
#   ./setup.sh deploy   - Deploy/update all infrastructure
#   ./setup.sh test     - Run job once with default settings
#   ./setup.sh test --dry-run - Run job once with DRY_RUN=true
#   ./setup.sh logs     - Show recent execution logs
#   ./setup.sh destroy  - Remove all infrastructure

set -euo pipefail

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-maciej-prod}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE_NAME="kuchniavikinga-garmin-sync"
STATE_BUCKET="${PROJECT_ID}-${SERVICE_NAME}-state"
SERVICE_ACCOUNT="${SERVICE_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
JOB_NAME="${SERVICE_NAME}"
SCHEDULER_NAME="${SERVICE_NAME}-daily"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Secret names (must match existing secrets exactly)
SECRET_VIKINGA_EMAIL="kuchniavikinga-garmin-sync-vikinga-email"
SECRET_VIKINGA_PASSWORD="kuchniavikinga-garmin-sync-vikinga-password"
SECRET_GARMIN_EMAIL="kuchniavikinga-garmin-sync-garmin-email"
SECRET_GARMIN_PASSWORD="kuchniavikinga-garmin-sync-garmin-password"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Check if gcloud is configured
check_gcloud() {
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI not found. Please install Google Cloud SDK."
        exit 1
    fi

    local current_project
    current_project=$(gcloud config get-value project 2>/dev/null || echo "")
    
    if [[ "$current_project" != "$PROJECT_ID" ]]; then
        log_warn "Current gcloud project is '$current_project', switching to '$PROJECT_ID'"
        gcloud config set project "$PROJECT_ID"
    fi
}

# Enable required APIs
enable_apis() {
    log_info "Enabling required GCP APIs..."
    
    local apis=(
        "run.googleapis.com"
        "cloudscheduler.googleapis.com"
        "cloudbuild.googleapis.com"
        "secretmanager.googleapis.com"
        "storage.googleapis.com"
        "iam.googleapis.com"
    )
    
    for api in "${apis[@]}"; do
        if gcloud services list --enabled --filter="name:$api" --format="value(name)" 2>/dev/null | grep -q "$api"; then
            log_info "  ✓ $api already enabled"
        else
            log_info "  Enabling $api..."
            gcloud services enable "$api" --project="$PROJECT_ID"
        fi
    done
}

# Create GCS bucket for state storage
create_bucket() {
    log_info "Creating GCS bucket for state storage..."
    
    if gsutil ls -b "gs://${STATE_BUCKET}" &>/dev/null; then
        log_info "  ✓ Bucket gs://${STATE_BUCKET} already exists"
    else
        log_info "  Creating bucket gs://${STATE_BUCKET}..."
        gsutil mb -p "$PROJECT_ID" -l "$REGION" -b on "gs://${STATE_BUCKET}"
        gsutil uniformbucketlevelaccess set on "gs://${STATE_BUCKET}"
        log_info "  ✓ Bucket created"
    fi
}

# Verify secrets exist
verify_secrets() {
    log_info "Verifying required secrets exist..."
    
    local secrets=(
        "$SECRET_VIKINGA_EMAIL"
        "$SECRET_VIKINGA_PASSWORD"
        "$SECRET_GARMIN_EMAIL"
        "$SECRET_GARMIN_PASSWORD"
    )
    
    local missing_secrets=()
    
    for secret in "${secrets[@]}"; do
        if gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
            log_info "  ✓ Secret $secret exists"
        else
            log_error "  ✗ Secret $secret not found"
            missing_secrets+=("$secret")
        fi
    done
    
    if [[ ${#missing_secrets[@]} -gt 0 ]]; then
        log_error "Missing required secrets. Create them with:"
        for secret in "${missing_secrets[@]}"; do
            echo "  gcloud secrets create $secret --project=$PROJECT_ID --replication-policy=automatic"
            echo "  echo -n 'VALUE' | gcloud secrets versions add $secret --data-file=- --project=$PROJECT_ID"
        done
        exit 1
    fi
}

# Create service account
create_service_account() {
    log_info "Creating service account..."
    
    if gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  ✓ Service account $SERVICE_ACCOUNT already exists"
    else
        log_info "  Creating service account..."
        gcloud iam service-accounts create "${SERVICE_NAME}" \
            --display-name="Kuchnia Vikinga Garmin Sync Service" \
            --project="$PROJECT_ID"
        log_info "  ✓ Service account created"
    fi
}

# Grant IAM permissions
grant_permissions() {
    log_info "Granting IAM permissions..."
    
    # Grant storage.objectUser on state bucket
    log_info "  Granting storage.objectUser on bucket..."
    gsutil iam ch "serviceAccount:${SERVICE_ACCOUNT}:objectUser" "gs://${STATE_BUCKET}" 2>/dev/null || true
    
    # Grant secretAccessor on each secret
    local secrets=(
        "$SECRET_VIKINGA_EMAIL"
        "$SECRET_VIKINGA_PASSWORD"
        "$SECRET_GARMIN_EMAIL"
        "$SECRET_GARMIN_PASSWORD"
    )
    
    for secret in "${secrets[@]}"; do
        log_info "  Granting secretAccessor on $secret..."
        gcloud secrets add-iam-policy-binding "$secret" \
            --member="serviceAccount:${SERVICE_ACCOUNT}" \
            --role="roles/secretmanager.secretAccessor" \
            --project="$PROJECT_ID" \
            --condition=None \
            >/dev/null 2>&1 || true
    done
    
    # Grant run.invoker for Cloud Scheduler
    log_info "  Ensuring service account has run.invoker role..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SERVICE_ACCOUNT}" \
        --role="roles/run.invoker" \
        --condition=None \
        >/dev/null 2>&1 || true
    
    log_info "  ✓ Permissions granted"
}

# Build and push container image
build_image() {
    log_info "Building container image..."
    
    local repo_root
    repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
    
    log_info "  Building image: $IMAGE_NAME"
    gcloud builds submit "$repo_root" \
        --tag="$IMAGE_NAME" \
        --project="$PROJECT_ID" \
        --timeout=10m
    
    log_info "  ✓ Image built and pushed"
}

# Deploy Cloud Run Job
deploy_job() {
    log_info "Deploying Cloud Run Job..."
    
    local secret_mounts=(
        "VIKINGA_EMAIL=${SECRET_VIKINGA_EMAIL}:latest"
        "VIKINGA_PASSWORD=${SECRET_VIKINGA_PASSWORD}:latest"
        "GARMIN_EMAIL=${SECRET_GARMIN_EMAIL}:latest"
        "GARMIN_PASSWORD=${SECRET_GARMIN_PASSWORD}:latest"
    )
    
    local secret_args=""
    for mount in "${secret_mounts[@]}"; do
        secret_args="$secret_args --set-secrets=$mount"
    done
    
    # Check if job exists
    if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Updating existing job..."
        # shellcheck disable=SC2086
        gcloud run jobs update "$JOB_NAME" \
            --region="$REGION" \
            --image="$IMAGE_NAME" \
            --service-account="$SERVICE_ACCOUNT" \
            --max-retries=0 \
            --task-timeout=30m \
            --memory=512Mi \
            --cpu=1 \
            --set-env-vars="STATE_BUCKET=${STATE_BUCKET},SYNC_DAYS_BACK=1,DRY_RUN=false,LOG_LEVEL=INFO" \
            $secret_args \
            --project="$PROJECT_ID"
    else
        log_info "  Creating new job..."
        # shellcheck disable=SC2086
        gcloud run jobs create "$JOB_NAME" \
            --region="$REGION" \
            --image="$IMAGE_NAME" \
            --service-account="$SERVICE_ACCOUNT" \
            --max-retries=0 \
            --task-timeout=30m \
            --memory=512Mi \
            --cpu=1 \
            --set-env-vars="STATE_BUCKET=${STATE_BUCKET},SYNC_DAYS_BACK=1,DRY_RUN=false,LOG_LEVEL=INFO" \
            $secret_args \
            --project="$PROJECT_ID"
    fi
    
    log_info "  ✓ Cloud Run Job deployed"
}

# Create Cloud Scheduler job
create_scheduler() {
    log_info "Creating Cloud Scheduler job..."
    
    # Check if scheduler job exists
    if gcloud scheduler jobs describe "$SCHEDULER_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Updating existing scheduler job..."
        gcloud scheduler jobs update http "$SCHEDULER_NAME" \
            --location="$REGION" \
            --schedule="0 7 * * *" \
            --time-zone="Europe/Warsaw" \
            --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
            --http-method=POST \
            --oauth-service-account-email="$SERVICE_ACCOUNT" \
            --project="$PROJECT_ID" \
            >/dev/null 2>&1 || {
                log_warn "  Update failed, deleting and recreating..."
                gcloud scheduler jobs delete "$SCHEDULER_NAME" \
                    --location="$REGION" \
                    --project="$PROJECT_ID" \
                    --quiet
                gcloud scheduler jobs create http "$SCHEDULER_NAME" \
                    --location="$REGION" \
                    --schedule="0 7 * * *" \
                    --time-zone="Europe/Warsaw" \
                    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
                    --http-method=POST \
                    --oauth-service-account-email="$SERVICE_ACCOUNT" \
                    --project="$PROJECT_ID"
            }
    else
        log_info "  Creating new scheduler job..."
        gcloud scheduler jobs create http "$SCHEDULER_NAME" \
            --location="$REGION" \
            --schedule="0 7 * * *" \
            --time-zone="Europe/Warsaw" \
            --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
            --http-method=POST \
            --oauth-service-account-email="$SERVICE_ACCOUNT" \
            --project="$PROJECT_ID"
    fi
    
    log_info "  ✓ Scheduler job configured (runs daily at 07:00 Europe/Warsaw)"
}

# Deploy all infrastructure
cmd_deploy() {
    log_info "Starting deployment to project: $PROJECT_ID, region: $REGION"
    
    check_gcloud
    enable_apis
    verify_secrets
    create_bucket
    create_service_account
    grant_permissions
    build_image
    deploy_job
    create_scheduler
    
    log_info ""
    log_info "✓ Deployment complete!"
    log_info ""
    log_info "Resources created:"
    log_info "  • Cloud Run Job: $JOB_NAME"
    log_info "  • GCS Bucket: gs://${STATE_BUCKET}"
    log_info "  • Service Account: $SERVICE_ACCOUNT"
    log_info "  • Scheduler: $SCHEDULER_NAME (07:00 Europe/Warsaw daily)"
    log_info ""
    log_info "Next steps:"
    log_info "  • Test: ./setup.sh test --dry-run"
    log_info "  • View logs: ./setup.sh logs"
    log_info "  • Manual run: gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
}

# Run the job once (for testing)
cmd_test() {
    local dry_run="false"
    
    # Parse flags
    if [[ "${1:-}" == "--dry-run" ]]; then
        dry_run="true"
        log_info "Running job in DRY RUN mode (will not write to Garmin)"
    else
        log_info "Running job in LIVE mode"
    fi
    
    check_gcloud
    
    log_info "Executing Cloud Run Job: $JOB_NAME"
    
    # Execute with environment override
    local execution_name
    if [[ "$dry_run" == "true" ]]; then
        execution_name=$(gcloud run jobs execute "$JOB_NAME" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --update-env-vars="DRY_RUN=true" \
            --format="value(metadata.name)" 2>&1 | tail -1)
    else
        execution_name=$(gcloud run jobs execute "$JOB_NAME" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --format="value(metadata.name)" 2>&1 | tail -1)
    fi
    
    if [[ -z "$execution_name" ]]; then
        log_error "Failed to execute job"
        exit 1
    fi
    
    log_info "Job execution started: $execution_name"
    log_info "Waiting for completion..."
    
    # Wait for completion (timeout after 5 minutes)
    local timeout=300
    local elapsed=0
    local status=""
    
    while [[ $elapsed -lt $timeout ]]; do
        status=$(gcloud run jobs executions describe "$execution_name" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --format="value(status.conditions[0].type)" 2>/dev/null || echo "")
        
        if [[ "$status" == "Completed" ]]; then
            log_info "✓ Job completed successfully"
            break
        elif [[ "$status" == "Failed" ]]; then
            log_error "✗ Job execution failed"
            log_info "Fetching logs..."
            cmd_logs
            exit 1
        fi
        
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    if [[ $elapsed -ge $timeout ]]; then
        log_warn "Timeout waiting for job completion"
    fi
    
    log_info ""
    log_info "Fetching execution logs..."
    gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME AND resource.labels.location=$REGION" \
        --project="$PROJECT_ID" \
        --limit=50 \
        --format="table(timestamp,textPayload)" \
        --order=asc \
        --freshness=5m
}

# Show recent job logs
cmd_logs() {
    check_gcloud
    
    log_info "Fetching recent logs for job: $JOB_NAME"
    log_info ""
    
    gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME AND resource.labels.location=$REGION" \
        --project="$PROJECT_ID" \
        --limit=100 \
        --format="table(timestamp,severity,textPayload)" \
        --order=desc
}

# Destroy all infrastructure
cmd_destroy() {
    log_warn "This will DELETE all resources for $SERVICE_NAME"
    echo -n "Are you sure? Type 'yes' to confirm: "
    read -r confirmation
    
    if [[ "$confirmation" != "yes" ]]; then
        log_info "Aborted"
        exit 0
    fi
    
    check_gcloud
    
    log_info "Destroying infrastructure..."
    
    # Delete Cloud Scheduler job
    if gcloud scheduler jobs describe "$SCHEDULER_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Deleting scheduler job..."
        gcloud scheduler jobs delete "$SCHEDULER_NAME" \
            --location="$REGION" \
            --project="$PROJECT_ID" \
            --quiet
    fi
    
    # Delete Cloud Run Job
    if gcloud run jobs describe "$JOB_NAME" --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
        log_info "  Deleting Cloud Run Job..."
        gcloud run jobs delete "$JOB_NAME" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --quiet
    fi
    
    # Delete container images
    log_info "  Deleting container images..."
    gcloud container images delete "$IMAGE_NAME" \
        --project="$PROJECT_ID" \
        --quiet 2>/dev/null || log_warn "    No images to delete"
    
    # Delete GCS bucket (ask for confirmation)
    if gsutil ls -b "gs://${STATE_BUCKET}" &>/dev/null; then
        log_warn "  Delete state bucket gs://${STATE_BUCKET}? (contains sync state)"
        echo -n "  Type 'yes' to delete: "
        read -r bucket_confirm
        if [[ "$bucket_confirm" == "yes" ]]; then
            log_info "  Deleting bucket..."
            gsutil -m rm -r "gs://${STATE_BUCKET}" || true
        else
            log_info "  Keeping bucket"
        fi
    fi
    
    # Service account - ask for confirmation
    if gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project="$PROJECT_ID" &>/dev/null; then
        log_warn "  Delete service account $SERVICE_ACCOUNT?"
        echo -n "  Type 'yes' to delete: "
        read -r sa_confirm
        if [[ "$sa_confirm" == "yes" ]]; then
            log_info "  Deleting service account..."
            gcloud iam service-accounts delete "$SERVICE_ACCOUNT" \
                --project="$PROJECT_ID" \
                --quiet
        else
            log_info "  Keeping service account"
        fi
    fi
    
    log_info "✓ Destruction complete"
    log_warn "Note: Secrets were NOT deleted (manual cleanup required if needed)"
}

# Main command dispatcher
main() {
    local command="${1:-}"
    
    case "$command" in
        deploy)
            cmd_deploy
            ;;
        test)
            shift
            cmd_test "$@"
            ;;
        logs)
            cmd_logs
            ;;
        destroy)
            cmd_destroy
            ;;
        *)
            echo "Kuchnia Vikinga → Garmin Connect Sync - Deployment Script"
            echo ""
            echo "Usage: $0 <command>"
            echo ""
            echo "Commands:"
            echo "  deploy          Deploy/update all infrastructure"
            echo "  test            Run job once (live mode)"
            echo "  test --dry-run  Run job once with DRY_RUN=true"
            echo "  logs            Show recent execution logs"
            echo "  destroy         Remove all infrastructure (with confirmation)"
            echo ""
            echo "Environment variables (optional):"
            echo "  GCP_PROJECT_ID  GCP project ID (default: maciej-prod)"
            echo "  GCP_REGION      GCP region (default: europe-west1)"
            echo ""
            exit 1
            ;;
    esac
}

main "$@"
