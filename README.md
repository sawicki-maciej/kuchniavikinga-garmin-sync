# Kuchnia Vikinga → Garmin Connect Nutrition Sync

Automatically sync meal deliveries from Kuchnia Vikinga (kuchniavikinga.pl) diet catering to Garmin Connect nutrition logging.

## Overview

This service:
- Fetches delivered meal data from Kuchnia Vikinga's Dietly-based customer panel
- Extracts calories and macronutrients (protein, carbs, fat) for each meal
- Logs them to Garmin Connect using the Quick Add nutrition feature
- Runs daily via Google Cloud Run Job (triggered by Cloud Scheduler)
- Maintains sync state in a GCS bucket to avoid duplicate entries

## Requirements

### Kuchnia Vikinga
- Active Kuchnia Vikinga subscription with delivered meals
- Account credentials (email + password) for panel.kuchniavikinga.pl
- **Limitation**: No official API. This uses the reverse-engineered internal Dietly API that the web panel uses. Subject to breakage if Dietly changes their API.

### Garmin Connect
- **Garmin Connect+ subscription** (required for nutrition logging API access)
- Account credentials (email + password)
- Initial nutrition setup: Open the Garmin Connect app → Nutrition → Set up your nutrition plan. This creates the meal slots (Breakfast, Lunch, Dinner, etc.) that the API requires.

## Architecture

```
Cloud Scheduler (daily 07:00 Europe/Warsaw)
           ↓
    Cloud Run Job
           ↓
     [kuchniavikinga-garmin-sync container]
           ↓
    ┌──────┴──────┐
    ↓             ↓
  Dietly API   Garmin API
  (read meals) (write nutrition)
           ↓
    GCS Bucket (state)
```

## How It Works

1. **Fetch meals**: Authenticate to Dietly API (panel.kuchniavikinga.pl), fetch all active orders, iterate delivered meals for "yesterday" (configurable backfill window)
2. **Extract nutrition**: For each meal, extract: `calories_kcal`, `protein_g`, `carbs_g`, `fat_g`, meal name, delivery date
3. **Check sync state**: Load `.sync_state.json` from GCS bucket to see which meals have already been synced
4. **Write to Garmin**: For each new meal, call `add_nutrition_log()` via ha-garmin library
5. **Update state**: Write updated sync state back to GCS bucket

## Deployment

Deployed on GCP Cloud Run Job in project `maciej-prod`, region `europe-west1`, following the pattern established by the Withings sync.

### Infrastructure Components

- **Cloud Run Job**: `kuchniavikinga-garmin-sync` (512 MiB, 1 vCPU, max retries 0)
- **Cloud Scheduler**: `kuchniavikinga-garmin-sync-daily` (07:00 Europe/Warsaw)
- **GCS Bucket**: `maciej-prod-kuchniavikinga-garmin-sync-state` (uniform access, private)
- **Secrets**:
  - `kuchniavikinga-garmin-sync-vikinga-email` - Kuchnia Vikinga / Dietly account email
  - `kuchniavikinga-garmin-sync-vikinga-password` - Kuchnia Vikinga / Dietly account password
  - `kuchniavikinga-garmin-sync-garmin-tokens` - Garmin OAuth tokens (bootstrapped once, auto-refreshed)
  - ~~`kuchniavikinga-garmin-sync-garmin-email`~~ (not used - token-based auth only)
  - ~~`kuchniavikinga-garmin-sync-garmin-password`~~ (not used - token-based auth only)
- **Service Account**: `kuchniavikinga-garmin-sync@maciej-prod.iam.gserviceaccount.com`
  - `roles/storage.objectUser` on state bucket
  - `roles/secretmanager.secretAccessor` on secrets
  - `roles/secretmanager.secretVersionAdder` on garmin-tokens (for auto-refresh)
  - `roles/run.invoker` on the job (not project-wide)

### One-Time Garmin Token Bootstrap

Before the first deployment, you must bootstrap Garmin OAuth tokens on a Mac (Garmin's auth flow requires a real browser environment):

```bash
# Install dependencies locally
uv pip install -e .

# Run the Garmin login flow (opens browser)
./contrib/cloudrun/setup.sh garmin-login

# This will:
# 1. Launch browser for Garmin OAuth
# 2. Save tokens to ~/.garminconnect/
# 3. Upload tokens to Secret Manager (kuchniavikinga-garmin-sync-garmin-tokens)
```

After this, the Cloud Run job will auto-refresh the tokens on each run.

### Setup Script

All provisioning is scripted in `contrib/cloudrun/setup.sh`. See deployment documentation in this repository.

## Configuration

Environment variables (set via Cloud Run Job config):

- `VIKINGA_EMAIL` - Injected from Secret Manager
- `VIKINGA_PASSWORD` - Injected from Secret Manager
- `STATE_BUCKET` - GCS bucket name (e.g. `maciej-prod-kuchniavikinga-garmin-sync-state`)
- `SYNC_DAYS_BACK` - How many days back to sync (default: 3)
- `SYNC_DATE` - Sync a specific date instead of lookback window (format: `YYYY-MM-DD`)
- `RESYNC_DATE` - **BROKEN**: Delete and re-log meals for a specific date (format: `YYYY-MM-DD`)
  - Note: The Garmin DELETE endpoint returns 404, so resync mode is currently not functional
- `DRY_RUN` - If `true`, log actions without writing to Garmin (default: `false`)
- `DUMP_RAW` - If `true`, dump raw API responses to logs (default: `false`)
- `LOG_LEVEL` - Logging level (default: `INFO`)

## Running Locally

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (fast Python package installer)
- Docker (for containerized testing)
- GCP credentials with access to the state bucket

### Local Development

```bash
# Install dependencies with uv
uv pip install -e .

# Set environment variables
export VIKINGA_EMAIL="your-email@example.com"
export VIKINGA_PASSWORD="your-password"
export STATE_BUCKET="local-test-bucket"  # or use a test GCS bucket
export DRY_RUN="true"  # recommended for testing
export LOG_LEVEL="DEBUG"

# Run the sync
python -m kuchniavikinga_garmin_sync.main
```

Note: Garmin authentication uses OAuth tokens stored in Secret Manager. For local dev, first run `./contrib/cloudrun/setup.sh garmin-login` to bootstrap tokens.

### Docker

```bash
# Build
docker build -t kuchniavikinga-garmin-sync .

# Run (with env file)
docker run --env-file .env.local kuchniavikinga-garmin-sync
```

## Idempotency & Safety

- **Sync state tracking**: Each synced meal is recorded with `(delivery_date, meal_name, nutrition_hash)` in `.sync_state.json`
- **Duplicate prevention**: Meals already in sync state are skipped
- **Garmin rate limits**: Sleeps 2 seconds between meal writes to avoid hitting Garmin API rate limits
- **Error handling**: Failed meal sync does not block other meals; errors are logged and state is updated only for successful writes
- **Dry run mode**: Test with `DRY_RUN=true` to see what would be synced without writing to Garmin

## Known Limitations & Fragility

### Dietly API Fragility
- **No official API**: Uses reverse-engineered internal JSON API from `panel.dietly.pl`
- **May break without notice**: Dietly can change endpoints, auth flow, or response schemas at any time
- **No MFA support**: If Dietly adds MFA, this integration will break
- **Company ID dependency**: Requires correct `company-id` header (currently hardcoded to `kuchniavikinga`)

### Nutrition Data Gaps
- Meal nutrition data from Dietly may be incomplete or approximate
- Only macros (calories, protein, carbs, fat) are synced; micronutrients are not captured
- Meal names from Dietly may not match Garmin's food database (always uses "Quick Add")

### Garmin Connect+ Required
- Nutrition logging requires a paid Garmin Connect+ subscription
- If subscription lapses, all writes will fail with 403 Forbidden

### Sync Window
- Only syncs "delivered" meals (typically yesterday's meals)
- Does not sync future planned meals
- Default 3-day lookback; safe thanks to stable meal key tracking (date + Vikinga delivery_id + meal_id)

## Monitoring

- **Cloud Run execution logs**: Check Cloud Logging for `resource.type="cloud_run_job"`
- **Success indicator**: Look for "Sync completed: X meals synced" in logs
- **Failure modes**:
  - Dietly auth failure: "Dietly login failed"
  - Garmin auth failure: "Garmin login failed"
  - No meals found: "No delivered meals found in date range"
  - Partial sync: "Synced X of Y meals (Z errors)"

## Testing

Run the test suite:

```bash
pytest tests/
```

Tests cover:
- Dietly API client (mocked responses)
- Garmin API client (mocked responses)
- Sync logic
- State management

## Maintenance

### If Dietly API Changes
1. Inspect browser network traffic on panel.kuchniavikinga.pl (DevTools → Network → XHR/Fetch)
2. Update `kuchniavikinga_garmin_sync/dietly_client.py` with new endpoints/schemas
3. Update integration tests in `tests/test_dietly_client.py`

### If Garmin API Changes
- The `ha-garmin` library is actively maintained by cyberjunky
- Update `requirements.txt` to the latest version
- Check [ha-garmin releases](https://github.com/cyberjunky/ha-garmin/releases) for breaking changes

## License

MIT

## Credits

- Uses [ha-garmin](https://github.com/cyberjunky/ha-garmin) by cyberjunky for Garmin Connect API access
- Dietly API reverse-engineering inspired by [dietly-autopilot](https://github.com/jtomaszewski/dietly-autopilot)
