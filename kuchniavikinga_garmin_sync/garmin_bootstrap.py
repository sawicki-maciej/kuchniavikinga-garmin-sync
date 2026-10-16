"""Bootstrap Garmin authentication tokens locally.

This script must be run from a residential IP (your Mac) to avoid Garmin's
datacenter IP blocking and CAPTCHA requirements.

It performs interactive login with MFA support, then saves the session tokens
to a local file that you manually upload to GCS.

Usage:
    python -m kuchniavikinga_garmin_sync.garmin_bootstrap

Requirements:
    - Residential IP (not datacenter/VPN)
    - Garmin Connect credentials
"""

import logging
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
    logger.info("  3. Save tokens to %s", TOKEN_FILE)
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
    
    # Save tokens
    auth.save_session(str(TOKEN_FILE))
    logger.info("")
    logger.info("=" * 70)
    logger.info("✓ SUCCESS!")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Tokens saved to: %s", TOKEN_FILE)
    logger.info("")
    logger.info("Next step: Upload to GCS")
    logger.info("")
    logger.info("  gsutil cp %s gs://YOUR-STATE-BUCKET/.garmin_tokens.json", TOKEN_FILE)
    logger.info("")
    logger.info("Replace YOUR-STATE-BUCKET with your actual state bucket name.")
    logger.info("The job will download this file at startup and auto-refresh tokens.")
    logger.info("")


if __name__ == "__main__":
    main()
