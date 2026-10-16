"""Bootstrap Garmin authentication tokens locally.

This script must be run from a residential IP (your Mac) to avoid Garmin's
datacenter IP blocking and CAPTCHA requirements.

It performs interactive login with MFA support, then stores the session tokens
in Google Secret Manager for the Cloud Run job to use.

Usage:
    python -m kuchniavikinga_garmin_sync.garmin_bootstrap

Requirements:
    - gcloud CLI authenticated with a user account
    - Access to Secret Manager in the GCP project
    - Residential IP (not datacenter/VPN)
"""

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from ha_garmin import GarminAuth, GarminMFARequired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# GCP configuration
PROJECT_ID = "maciej-prod"
EMAIL_SECRET = "kuchniavikinga-garmin-sync-garmin-email"
PASSWORD_SECRET = "kuchniavikinga-garmin-sync-garmin-password"
TOKENS_SECRET = "kuchniavikinga-garmin-sync-garmin-tokens"


def get_secret_value(secret_name: str, project_id: str) -> str:
    """Get the latest secret version from Secret Manager."""
    logger.info("Fetching secret %s from Secret Manager...", secret_name)
    try:
        result = subprocess.run(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                f"--secret={secret_name}",
                f"--project={project_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error("Failed to fetch secret %s: %s", secret_name, e.stderr)
        raise


def create_secret_if_missing(secret_name: str, project_id: str) -> None:
    """Create a secret if it doesn't exist."""
    logger.info("Checking if secret %s exists...", secret_name)
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "describe",
            secret_name,
            f"--project={project_id}",
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        logger.info("Secret %s already exists", secret_name)
        return
    
    logger.info("Creating secret %s...", secret_name)
    try:
        subprocess.run(
            [
                "gcloud",
                "secrets",
                "create",
                secret_name,
                f"--project={project_id}",
                "--replication-policy=automatic",
            ],
            check=True,
        )
        logger.info("Secret %s created successfully", secret_name)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create secret %s", secret_name)
        raise


def add_secret_version(secret_name: str, project_id: str, value: str) -> None:
    """Add a new version to a secret."""
    logger.info("Adding new version to secret %s...", secret_name)
    try:
        subprocess.run(
            [
                "gcloud",
                "secrets",
                "versions",
                "add",
                secret_name,
                f"--project={project_id}",
                "--data-file=-",
            ],
            input=value,
            text=True,
            check=True,
        )
        logger.info("New version added to secret %s", secret_name)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to add secret version: %s", e.stderr)
        raise


def grant_service_account_access(secret_name: str, project_id: str, service_account: str) -> None:
    """Grant service account access to a secret."""
    logger.info("Granting %s access to secret %s...", service_account, secret_name)
    
    # Grant secretAccessor role
    try:
        subprocess.run(
            [
                "gcloud",
                "secrets",
                "add-iam-policy-binding",
                secret_name,
                f"--project={project_id}",
                f"--member=serviceAccount:{service_account}",
                "--role=roles/secretmanager.secretAccessor",
            ],
            capture_output=True,
            check=True,
        )
        logger.info("Granted secretAccessor to %s", service_account)
    except subprocess.CalledProcessError:
        logger.warning("Failed to grant secretAccessor (may already exist)")
    
    # Grant secretVersionAdder role (for token refresh)
    try:
        subprocess.run(
            [
                "gcloud",
                "secrets",
                "add-iam-policy-binding",
                secret_name,
                f"--project={project_id}",
                f"--member=serviceAccount:{service_account}",
                "--role=roles/secretmanager.secretVersionAdder",
            ],
            capture_output=True,
            check=True,
        )
        logger.info("Granted secretVersionAdder to %s", service_account)
    except subprocess.CalledProcessError:
        logger.warning("Failed to grant secretVersionAdder (may already exist)")


def perform_login(email: str, password: str) -> Path:
    """Perform Garmin login and return the path to the session file.
    
    Returns:
        Path to the temporary session file containing tokens.
    """
    logger.info("Starting Garmin authentication for %s...", email[:4] + "***")
    
    auth = GarminAuth()
    
    try:
        result = auth.login(email, password)
        logger.info("Login result: %s", result)
        
        if result == "MFA_REQUIRED":
            logger.info("MFA required. Please check your email or authenticator app.")
            mfa_code = input("Enter MFA code: ").strip()
            
            result = auth.complete_mfa(mfa_code)
            logger.info("MFA result: %s", result)
        
        if result != "SUCCESS":
            logger.error("Authentication failed: %s", result)
            sys.exit(1)
        
        logger.info("Authentication successful!")
        
        # Save session to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            session_file = Path(f.name)
        
        auth.save_session(session_file)
        logger.info("Session saved to %s", session_file)
        
        return session_file
        
    except GarminMFARequired:
        logger.info("MFA required. Please check your email or authenticator app.")
        mfa_code = input("Enter MFA code: ").strip()
        
        result = auth.complete_mfa(mfa_code)
        logger.info("MFA result: %s", result)
        
        if result != "SUCCESS":
            logger.error("MFA authentication failed: %s", result)
            sys.exit(1)
        
        logger.info("Authentication successful!")
        
        # Save session to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            session_file = Path(f.name)
        
        auth.save_session(session_file)
        logger.info("Session saved to %s", session_file)
        
        return session_file


def main() -> None:
    """Main bootstrap workflow."""
    logger.info("=" * 60)
    logger.info("Garmin Token Bootstrap for Cloud Run")
    logger.info("=" * 60)
    logger.info("")
    logger.info("This script must be run from a residential IP (your Mac).")
    logger.info("It will:")
    logger.info("  1. Fetch Garmin credentials from Secret Manager")
    logger.info("  2. Perform interactive login (with MFA if needed)")
    logger.info("  3. Store session tokens in Secret Manager")
    logger.info("")
    
    # Get credentials from Secret Manager
    try:
        email = get_secret_value(EMAIL_SECRET, PROJECT_ID)
        password = get_secret_value(PASSWORD_SECRET, PROJECT_ID)
    except Exception as e:
        logger.error("Failed to fetch credentials from Secret Manager: %s", e)
        logger.info("")
        logger.info("You can also provide credentials manually:")
        email = input("Garmin email: ").strip()
        password = input("Garmin password: ").strip()
    
    # Perform login
    session_file = perform_login(email, password)
    
    try:
        # Read the session file
        with open(session_file, 'r') as f:
            session_data = f.read()
        
        # Verify it's valid JSON
        json.loads(session_data)
        
        logger.info("Session file size: %d bytes", len(session_data))
        
        # Create tokens secret if it doesn't exist
        create_secret_if_missing(TOKENS_SECRET, PROJECT_ID)
        
        # Add tokens as a new secret version
        add_secret_version(TOKENS_SECRET, PROJECT_ID, session_data)
        
        # Grant service account access
        # The service account name is based on the setup.sh naming
        service_account = f"kuchniavikinga-garmin-sync@{PROJECT_ID}.iam.gserviceaccount.com"
        grant_service_account_access(TOKENS_SECRET, PROJECT_ID, service_account)
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUCCESS!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("Garmin tokens have been stored in Secret Manager.")
        logger.info("The Cloud Run job can now authenticate without password login.")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Deploy or redeploy the job: ./setup.sh deploy")
        logger.info("  2. Test it: ./setup.sh test")
        logger.info("")
        
    finally:
        # Clean up temp file
        session_file.unlink(missing_ok=True)
        logger.debug("Cleaned up temporary session file")


if __name__ == "__main__":
    main()
