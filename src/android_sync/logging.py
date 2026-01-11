"""Logging configuration with file and stdout output."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Path,
    retention_days: int = 30,
    verbose: bool = False,
) -> logging.Logger:
    """Configure logging to both file and stdout.

    Args:
        log_dir: Directory to store log files.
        retention_days: Number of days to keep log files.
        verbose: If True, set log level to DEBUG.

    Returns:
        Configured root logger.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    # Clean up old logs
    cleanup_old_logs(log_dir, retention_days)

    # Create log file with timestamp
    log_file = log_dir / f"android-sync-{datetime.now():%Y%m%d-%H%M%S}.log"

    level = logging.DEBUG if verbose else logging.INFO

    # Configure root logger
    logger = logging.getLogger("android_sync")
    logger.setLevel(level)

    # Clear existing handlers
    logger.handlers.clear()

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(file_handler)

    # Stdout handler
    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(stdout_handler)

    logger.info("Logging initialized: %s", log_file)
    return logger


def cleanup_old_logs(log_dir: Path, retention_days: int) -> int:
    """Remove log files older than retention_days.

    Args:
        log_dir: Directory containing log files.
        retention_days: Number of days to keep logs.

    Returns:
        Number of files removed.
    """
    if retention_days <= 0:
        return 0

    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0

    for log_file in log_dir.glob("android-sync-*.log"):
        if log_file.stat().st_mtime < cutoff.timestamp():
            log_file.unlink()
            removed += 1

    return removed
