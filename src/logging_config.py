import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import SETTINGS, resolve_project_path


_CONFIGURED = False


def log_level_from_name(name: str) -> int:
    return getattr(logging, name.strip().upper(), logging.INFO)


def configure_logging(app_name: str = "vox") -> Path:
    global _CONFIGURED

    log_dir = Path(resolve_project_path(SETTINGS.log_dir))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{app_name}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_from_name(SETTINGS.log_level))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    if not _CONFIGURED:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root_logger.addHandler(console)
        _CONFIGURED = True

    handler_name = f"vox-rotating-file:{app_name}:{log_path}"
    if not any(getattr(handler, "name", None) == handler_name for handler in root_logger.handlers):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max(1024, SETTINGS.log_max_bytes),
            backupCount=max(1, SETTINGS.log_backup_count),
            encoding="utf-8",
        )
        file_handler.name = handler_name
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Keep noisy third-party loggers from drowning out VOX request/job logs.
    for logger_name in ("werkzeug", "urllib3", "httpx"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    os.environ.setdefault("VOX_ACTIVE_LOG_FILE", str(log_path))
    return log_path
