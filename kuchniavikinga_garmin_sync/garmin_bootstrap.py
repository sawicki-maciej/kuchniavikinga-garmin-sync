"""Bootstrap Garmin authentication tokens locally.

This script must be run from a residential IP (your Mac) to avoid Garmin's
datacenter IP blocking and CAPTCHA requirements.

It performs interactive login with MFA support, saves tokens locally,
uploads to GCS, and cleans up.

Usage:
    From setup.sh: uses uv run with GCS_BUCKET env var
    Direct: python -m kuchniavikinga_garmin_sync.garmin_bootstrap

Requirements:
    - Residential IP (not datacenter/VPN)
    - Garmin Connect credentials
    - gcloud/gsutil configured (for GCS upload)
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from ha_garmin import GarminAuth, GarminMFARequired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Token file location (same as withings-sync default)
TOKEN_FILE = Path.home() / ".garmin_tokens.json"


def upload_to_gcs(gcs_path: str) -> None:
    """Upload token file to GCS.
    
    Args:
        gcs_path: Full GCS path like gs://bucket/path/to/file.json
    """
    logger.info("Uploading tokens to %s...", gcs_path)
    
    try:
        subprocess.run(
            ["gsutil", "cp", str(TOKEN_FILE), gcs_path],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("✓ Upload successful")
    except subprocess.CalledProcessError as e:
        logger.error("Failed to upload to GCS: %s", e.stderr)
        raise
    except FileNotFoundError:
        logger.error("gsutil not found. Please install Google Cloud SDK.")
        raise


def main() -> None:
    """Main bootstrap workflow."""
    logger.info("=" * 70)
    logger.info("Garmin Token Bootstrap (like withings-sync)")
    logger.info("=" * 70)
    logger.info("")
    logger.info("This script must be run from a residential IP (your Mac).")
    logger.info("It will:")
    logger.info("  1. Prompt for Garmin email/password")
    logger.info("  2. Handle MFA if needed")
    logger.info("  3. Save tokens locally")
    logger.info("  4. Upload to GCS")
    logger.info("  5. Delete local file")
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
    
    # Save tokens locally
    auth.save_session(str(TOKEN_FILE))
    logger.info("✓ Tokens saved to: %s", TOKEN_FILE)
    
    # Upload to GCS if bucket is provided
    gcs_bucket = os.environ.get("GCS_BUCKET")
    if gcs_bucket:
        gcs_path = f"gs://{gcs_bucket}/.garmin_tokens.json"
        
        try:
            upload_to_gcs(gcs_path)
            
            # Delete local file after successful upload
            TOKEN_FILE.unlink()
            logger.info("✓ Local token file deleted")
            
            logger.info("")
            logger.info("=" * 70)
            logger.info("✓ SUCCESS!")
            logger.info("=" * 70)
            logger.info("")
            logger.info("Tokens uploaded to: %s", gcs_path)
            logger.info("The Cloud Run job will download and auto-refresh them.")
            logger.info("")
            
        except Exception as e:
            logger.error("Upload failed, keeping local file: %s", TOKEN_FILE)
            logger.error("Error: %s", e)
            sys.exit(1)
    else:
        logger.info("")
        logger.info("=" * 70)
        logger.info("✓ Tokens Generated!")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Tokens saved to: %s", TOKEN_FILE)
        logger.info("")
        logger.info("To upload to GCS manually:")
        logger.info("  gsutil cp %s gs://YOUR-BUCKET/.garmin_tokens.json", TOKEN_FILE)
        logger.info("")


if __name__ == "__main__":
    main()
