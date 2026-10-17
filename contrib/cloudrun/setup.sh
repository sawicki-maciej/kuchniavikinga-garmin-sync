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
SECRET_GARMIN_TOKENS="kuchniavikinga-garmin-sync-garmin-tokens"

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
    
    # Only verify the Vikinga credentials (Garmin tokens are created by bootstrap)
    local secrets=(
        "$SECRET_VIKINGA_EMAIL"
        "$SECRET_VIKINGA_PASSWORD"
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
    
    # Note: Garmin tokens secret is created by ./setup.sh garmin-login
    log_info "  Note: Garmin tokens ($SECRET_GARMIN_TOKENS) will be created by './setup.sh garmin-login'"
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
# Grant IAM permissions
grant_permissions() {
    log_info "Granting IAM permissions..."
    
    # Grant storage.objectUser on state bucket
    log_info "  Granting storage.objectUser on bucket..."
    gsutil iam ch "serviceAccount:${SERVICE_ACCOUNT}:objectUser" "gs://${STATE_BUCKET}" 2>/dev/null || true
    
    # Grant secretAccessor on credential secrets
    local secrets=(
        "$SECRET_VIKINGA_EMAIL"
        "$SECRET_VIKINGA_PASSWORD"
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
    
    # Grant secretAccessor and secretVersionAdder on Garmin tokens secret
    log_info "  Granting secretAccessor on $SECRET_GARMIN_TOKENS..."
    gcloud secrets add-iam-policy-binding "$SECRET_GARMIN_TOKENS" \
        --member="serviceAccount:${SERVICE_ACCOUNT}" \
        --role="roles/secretmanager.secretAccessor" \
        --project="$PROJECT_ID" \
        --condition=None \
        >/dev/null 2>&1 || true
    
    log_info "  Granting secretVersionAdder on $SECRET_GARMIN_TOKENS (for token refresh)..."
    gcloud secrets add-iam-policy-binding "$SECRET_GARMIN_TOKENS" \
        --member="serviceAccount:${SERVICE_ACCOUNT}" \
        --role="roles/secretmanager.secretVersionAdder" \
        --project="$PROJECT_ID" \
        --condition=None \
        >/dev/null 2>&1 || true
    
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
    
    # Only mount secrets that are actually used
    local secret_mounts=(
        "VIKINGA_EMAIL=${SECRET_VIKINGA_EMAIL}:latest"
        "VIKINGA_PASSWORD=${SECRET_VIKINGA_PASSWORD}:latest"
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
    local sync_date=""
    
    # Parse flags
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                dry_run="true"
                shift
                ;;
            --date)
                sync_date="$2"
                shift 2
                ;;
            *)
                log_error "Unknown flag: $1"
                echo "Usage: $0 test [--dry-run] [--date YYYY-MM-DD]"
                exit 1
                ;;
        esac
    done
    
    if [[ "$dry_run" == "true" ]]; then
        log_info "Running job in DRY RUN mode (will not write to Garmin)"
    else
        log_info "Running job in LIVE mode"
    fi
    
    if [[ -n "$sync_date" ]]; then
        log_info "Syncing specific date: $sync_date"
    fi
    
    check_gcloud
    
    log_info "Executing Cloud Run Job: $JOB_NAME"
    
    # Build environment overrides
    local env_vars=""
    if [[ "$dry_run" == "true" ]]; then
        env_vars="DRY_RUN=true"
    fi
    if [[ -n "$sync_date" ]]; then
        if [[ -n "$env_vars" ]]; then
            env_vars="${env_vars},SYNC_DATE=${sync_date}"
        else
            env_vars="SYNC_DATE=${sync_date}"
        fi
    fi
    
    # Execute with environment overrides
    local execution_name
    if [[ -n "$env_vars" ]]; then
        execution_name=$(gcloud run jobs execute "$JOB_NAME" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --update-env-vars="$env_vars" \
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
    
    while [[ $elapsed -lt $timeout ]]; do
        # Get the full execution status
        local exec_info
        exec_info=$(gcloud run jobs executions describe "$execution_name" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --format="json" 2>/dev/null || echo "{}")
        
        # Check completion status
        local completed
        completed=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('completionTime', ''))" 2>/dev/null || echo "")
        
        if [[ -n "$completed" ]]; then
            # Job has completed, check the result
            local succeeded
            succeeded=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('succeededCount', 0))" 2>/dev/null || echo "0")
            
            local failed
            failed=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('failedCount', 0))" 2>/dev/null || echo "0")
            
            if [[ "$succeeded" == "1" ]]; then
                log_info "✓ Job completed successfully"
                break
            elif [[ "$failed" == "1" ]]; then
                log_error "✗ Job execution failed"
                log_info "Fetching logs..."
                cmd_logs
                exit 1
            else
                log_error "✗ Job completed with unexpected status (succeeded=$succeeded, failed=$failed)"
                log_info "Fetching logs..."
                cmd_logs
                exit 1
            fi
        fi
        
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    if [[ $elapsed -ge $timeout ]]; then
        log_warn "Timeout waiting for job completion"
        exit 1
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

# Resync meals for a specific date (delete from Garmin and re-log with correct macros)
cmd_resync() {
    check_gcloud
    
    local resync_date=""
    local dry_run="false"
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --date)
                resync_date="$2"
                shift 2
                ;;
            --dry-run)
                dry_run="true"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Usage: $0 resync --date YYYY-MM-DD [--dry-run]"
                exit 1
                ;;
        esac
    done
    
    if [[ -z "$resync_date" ]]; then
        log_error "--date is required for resync"
        echo "Usage: $0 resync --date YYYY-MM-DD [--dry-run]"
        exit 1
    fi
    
    # Validate date format
    if ! [[ "$resync_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        log_error "Invalid date format. Expected YYYY-MM-DD, got: $resync_date"
        exit 1
    fi
    
    log_info "Resyncing meals for date: $resync_date"
    if [[ "$dry_run" == "true" ]]; then
        log_warn "DRY RUN mode - will not make actual changes"
    else
        log_warn "This will delete and re-log all meals for $resync_date"
        read -p "Continue? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Cancelled"
            exit 0
        fi
    fi
    
    log_info "Executing resync job..."
    
    # Build env vars for resync mode
    local env_vars="STATE_BUCKET=$STATE_BUCKET,RESYNC_DATE=$resync_date"
    if [[ "$dry_run" == "true" ]]; then
        env_vars="$env_vars,DRY_RUN=true"
    fi
    
    # Execute the job with RESYNC_DATE
    local execution_name
    execution_name=$(gcloud run jobs execute "$JOB_NAME" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --update-env-vars="$env_vars" \
        --format="value(metadata.name)" 2>&1 | tail -1)
    
    if [[ -z "$execution_name" ]]; then
        log_error "Failed to execute resync job"
        exit 1
    fi
    
    log_info "Resync job execution started: $execution_name"
    log_info "Waiting for completion..."
    
    # Wait for completion (timeout after 5 minutes)
    local timeout=300
    local elapsed=0
    
    while [[ $elapsed -lt $timeout ]]; do
        local exec_info
        exec_info=$(gcloud run jobs executions describe "$execution_name" \
            --region="$REGION" \
            --project="$PROJECT_ID" \
            --format="json" 2>/dev/null || echo "{}")
        
        local completed
        completed=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('completionTime', ''))" 2>/dev/null || echo "")
        
        if [[ -n "$completed" ]]; then
            local succeeded
            succeeded=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('succeededCount', 0))" 2>/dev/null || echo "0")
            
            local failed
            failed=$(echo "$exec_info" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('status', {}).get('failedCount', 0))" 2>/dev/null || echo "0")
            
            if [[ "$succeeded" == "1" ]]; then
                log_info "✓ Resync job completed successfully"
                break
            elif [[ "$failed" == "1" ]]; then
                log_error "✗ Resync job execution failed"
                log_info "Fetching logs..."
                cmd_logs
                exit 1
            else
                log_error "✗ Resync job completed with unexpected status (succeeded=$succeeded, failed=$failed)"
                log_info "Fetching logs..."
                cmd_logs
                exit 1
            fi
        fi
        
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    if [[ $elapsed -ge $timeout ]]; then
        log_warn "Timeout waiting for resync job completion"
        exit 1
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

# Bootstrap Garmin authentication tokens
cmd_garmin_login() {
    check_gcloud
    
    # Check if uv is available
    if ! command -v uv &> /dev/null; then
        log_error "uv not found. Please install it:"
        log_error "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    
    log_info "========================================================"
    log_info "Garmin Token Bootstrap"
    log_info "========================================================"
    log_info ""
    log_info "This command must be run from a residential IP (your Mac)."
    log_info "It will:"
    log_info "  1. Prompt for Garmin email/password"
    log_info "  2. Handle MFA interactively"
    log_info "  3. Add tokens to Secret Manager"
    log_info "  4. Clean up old GCS token file if it exists"
    log_info ""
    log_info "This avoids datacenter IP blocks and CAPTCHA on Cloud Run."
    log_info ""
    
    # Get repo root
    local repo_root
    repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
    
    # Run the bootstrap script with uv
    log_info "Running Garmin authentication bootstrap with uv..."
    cd "$repo_root"
    
    SECRET_NAME="$SECRET_GARMIN_TOKENS" PROJECT_ID="$PROJECT_ID" STATE_BUCKET="$STATE_BUCKET" \
        uv run --project "$repo_root" python -m kuchniavikinga_garmin_sync.garmin_bootstrap
    
    local exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        log_info ""
        log_info "✓ Garmin tokens successfully stored in Secret Manager"
        log_info ""
    else
        log_error "Bootstrap failed with exit code $exit_code"
        exit 1
    fi
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
        resync)
            shift
            cmd_resync "$@"
            ;;
        logs)
            cmd_logs
            ;;
        destroy)
            cmd_destroy
            ;;
        garmin-login)
            cmd_garmin_login
            ;;
        *)
            echo "Kuchnia Vikinga → Garmin Connect Sync - Deployment Script"
            echo ""
            echo "Usage: $0 <command>"
            echo ""
            echo "Commands:"
            echo "  deploy          Deploy/update all infrastructure"
            echo "  garmin-login    Bootstrap Garmin tokens (run on Mac with residential IP)"
            echo "  test            Run job once (live mode)"
            echo "  test --dry-run  Run job once with DRY_RUN=true"
            echo "  test --date YYYY-MM-DD  Run job for specific date"
            echo "  resync --date YYYY-MM-DD  Resync (delete & re-log) all meals for a date"
            echo "  resync --date YYYY-MM-DD --dry-run  Preview what would be resynced"
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
