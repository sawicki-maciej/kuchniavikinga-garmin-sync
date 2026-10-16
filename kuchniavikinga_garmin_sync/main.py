"""Main entry point for the sync service."""

import asyncio
import logging
import os
import sys

from .sync import VikingaGarminSync


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_env_var(name: str, required: bool = True) -> str | None:
    """Get environment variable with validation."""
    value = os.getenv(name)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


async def main() -> int:
    """Main async entry point."""
    # Setup logging
    log_level = get_env_var("LOG_LEVEL", required=False) or "INFO"
    setup_logging(log_level)

    logger = logging.getLogger(__name__)
    logger.info("Vikinga → Garmin Connect Sync starting")

    try:
        # Load configuration from environment
        vikinga_email = get_env_var("VIKINGA_EMAIL")
        vikinga_password = get_env_var("VIKINGA_PASSWORD")
        
        # Note: Garmin authentication now uses tokens from Secret Manager
        # GARMIN_EMAIL and GARMIN_PASSWORD are no longer used by the job
        # Run the bootstrap locally first to generate tokens
        
        state_bucket = get_env_var("STATE_BUCKET", required=False)
        days_back = int(get_env_var("SYNC_DAYS_BACK", required=False) or "1")
        dry_run = get_env_var("DRY_RUN", required=False) == "true"
        dump_raw = get_env_var("DUMP_RAW", required=False) == "true"
        
        # Parse SYNC_DATE if provided (format: YYYY-MM-DD)
        sync_date = None
        sync_date_str = get_env_var("SYNC_DATE", required=False)
        if sync_date_str:
            from datetime import datetime
            try:
                sync_date = datetime.strptime(sync_date_str, "%Y-%m-%d").date()
                logger.info("SYNC_DATE specified: %s", sync_date)
            except ValueError as e:
                logger.error("Invalid SYNC_DATE format (expected YYYY-MM-DD): %s", e)
                return 1

        # Create and run sync
        sync = VikingaGarminSync(
            vikinga_email=vikinga_email,
            vikinga_password=vikinga_password,
            state_bucket=state_bucket,
            days_back=days_back,
            dry_run=dry_run,
            dump_raw=dump_raw,
        )

        result = await sync.run(sync_date=sync_date)

        # Log results
        logger.info("Sync results: %s", result)

        # Exit with error if there were failures
        if result["errors"] > 0:
            logger.error("Sync completed with errors")
            return 1

        logger.info("Sync completed successfully")
        return 0

    except Exception as e:
        logger.exception("Sync failed with exception: %s", e)
        return 1


def run() -> None:
    """Synchronous entry point wrapper."""
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
