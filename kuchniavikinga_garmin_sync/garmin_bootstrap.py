"""Bootstrap Garmin authentication tokens locally.

This script must be run from a residential IP (your Mac) to avoid Garmin's
datacenter IP blocking and CAPTCHA requirements.

It performs interactive login with MFA support, then stores tokens in Secret Manager.

Usage:
    From setup.sh: uses uv run with SECRET_NAME, PROJECT_ID, STATE_BUCKET env vars
    Direct: python -m kuchniavikinga_garmin_sync.garmin_bootstrap

Requirements:
    - Residential IP (not datacenter/VPN)
    - Garmin Connect credentials
    - gcloud CLI configured
"""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ha_garmin import GarminAuth, GarminMFARequired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_secret_exists(secret_name: str, project_id: str) -> tuple[bool, bool]:
    """Check if secret exists and has versions.
    
    Returns:
        (secret_exists, has_versions)
    """
    result = subprocess.run(
        ["gcloud", "secrets", "describe", secret_name, f"--project={project_id}"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return (False, False)
    
    # Check if it has versions
    versions_result = subprocess.run(
        ["gcloud", "secrets", "versions", "list", secret_name, 
         f"--project={project_id}", "--limit=1"],
        capture_output=True,
        text=True,
    )
    
    has_versions = versions_result.returncode == 0 and len(versions_result.stdout.strip().split('\n')) > 1
    
    return (True, has_versions)


def create_secret_if_missing(secret_name: str, project_id: str) -> None:
    """Create secret if it doesn't exist."""
    exists, _ = check_secret_exists(secret_name, project_id)
    
    if exists:
        logger.debug("Secret %s already exists", secret_name)
        return
    
    logger.info("Creating secret %s...", secret_name)
    subprocess.run(
        ["gcloud", "secrets", "create", secret_name, f"--project={project_id}",
         "--replication-policy=automatic"],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("✓ Secret created")


def add_secret_version(secret_name: str, project_id: str, token_data: str) -> None:
    """Add new secret version via stdin (no temp file on disk).
    
    Args:
        secret_name: Name of the secret
        project_id: GCP project ID
        token_data: JSON token data to store
    """
    logger.info("Adding tokens to Secret Manager...")
    
    try:
        subprocess.run(
            ["gcloud", "secrets", "versions", "add", secret_name,
             f"--project={project_id}", "--data-file=-"],
            input=token_data,
            text=True,
            check=True,
            capture_output=True,
        )
        logger.info("✓ Tokens added to Secret Manager")
    except subprocess.CalledProcessError as e:
        logger.error("Failed to add secret version: %s", e.stderr)
        raise


def cleanup_old_gcs_token(bucket: str) -> None:
    """Delete old GCS token file if it exists.
    
    Args:
        bucket: GCS bucket name (without gs:// prefix)
    """
    gcs_path = f"gs://{bucket}/.garmin_tokens.json"
    
    result = subprocess.run(
        ["gsutil", "ls", gcs_path],
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        logger.info("Found old GCS token file, deleting...")
        subprocess.run(
            ["gsutil", "rm", gcs_path],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("✓ Old GCS token file deleted")


def main() -> None:
    """Main bootstrap workflow."""
    logger.info("=" * 70)
    logger.info("Garmin Token Bootstrap")
    logger.info("=" * 70)
    logger.info("")
    logger.info("This script must be run from a residential IP (your Mac).")
    logger.info("It will:")
    logger.info("  1. Prompt for Garmin email/password")
    logger.info("  2. Handle MFA if needed")
    logger.info("  3. Store tokens in Secret Manager")
    logger.info("  4. Clean up old GCS token file if it exists")
    logger.info("")
    
    # Get configuration from environment
    secret_name = os.environ.get("SECRET_NAME")
    project_id = os.environ.get("PROJECT_ID")
    state_bucket = os.environ.get("STATE_BUCKET")
    
    if not secret_name or not project_id:
        logger.error("SECRET_NAME and PROJECT_ID environment variables required")
        sys.exit(1)
    
    # Check if secret already exists with versions
    exists, has_versions = check_secret_exists(secret_name, project_id)
    
    if exists and has_versions:
        logger.info("")
        logger.warning("Secret %s already exists with valid tokens.", secret_name)
        logger.warning("The Cloud Run job can already authenticate with the existing tokens.")
        logger.info("")
        response = input("Do you want to generate new tokens and add a new version? [y/N]: ").strip().lower()
        
        if response not in ('y', 'yes'):
            logger.info("")
            logger.info("Aborted. Existing tokens remain unchanged.")
            logger.info("")
            sys.exit(0)
        
        logger.info("")
        logger.info("Proceeding to generate new tokens...")
        logger.info("")
    
    # Prompt for credentials
    email = input("Garmin email: ").strip()
    if not email:
        logger.error("Email is required")
        sys.exit(1)
    
    import getpass
    password = getpass.getpass("Garmin password: ")
    if not password:
        logger.error("Password is required")
        sys.exit(1)
    
    # Perform login
    logger.info("Authenticating to Garmin Connect...")
    auth = GarminAuth()
    
    try:
        auth.login(email, password)
        logger.info("✓ Login successful")
        
    except GarminMFARequired:
        logger.info("MFA required. Check your email or authenticator app.")
        mfa_code = input("Enter MFA code: ").strip()
        
        auth.complete_mfa(mfa_code)
        logger.info("✓ MFA login successful")
    
    except Exception as e:
        logger.error("Authentication failed: %s", e)
        sys.exit(1)
    
    # Save tokens to temporary file to read the JSON
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        auth.save_session(str(temp_path))
        
        # Read token data
        with open(temp_path, 'r') as f:
            token_data = f.read()
        
        # Create secret if missing
        create_secret_if_missing(secret_name, project_id)
        
        # Add tokens to Secret Manager (via stdin, no temp file on disk for gcloud)
        add_secret_version(secret_name, project_id, token_data)
        
        # Clean up old GCS token file if it exists
        if state_bucket:
            try:
                cleanup_old_gcs_token(state_bucket)
            except Exception as e:
                logger.warning("Failed to clean up old GCS token (may not exist): %s", e)
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("✓ SUCCESS!")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Tokens stored in Secret Manager: %s", secret_name)
        logger.info("The Cloud Run job will load and auto-refresh them.")
        logger.info("")
        
    finally:
        # Always delete temp file
        temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
