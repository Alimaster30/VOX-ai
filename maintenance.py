import argparse
import json
import logging

from src.logging_config import configure_logging
from src.persistence import run_maintenance


LOG_PATH = configure_logging("vox-maintenance")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VOX database maintenance cleanup.")
    parser.add_argument("--dry-run", action="store_true", help="Show cleanup counts without deleting records.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_maintenance(dry_run=args.dry_run)
    logger.info("Maintenance complete dry_run=%s total=%s", args.dry_run, result["total"])
    print(json.dumps(result, indent=2))
    print(f"Log file: {LOG_PATH}")


if __name__ == "__main__":
    main()
