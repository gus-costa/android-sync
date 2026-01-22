"""Logging configuration with file and stdout output.

This module implements the logging system as specified in specs/logging-system.md.
It provides dual output (file + console), automatic log retention management, and
support for both main invocation logs and background job logs.

Key features:
- Timestamped log files: android-sync-YYYYMMDD-HHMMSS.log
- Schedule log files: schedule-*.log (append mode)
- Automatic cleanup based on retention policy (specs/logging-system.md §6)
- mtime-based retention (active logs continuously update mtime)
"""

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

    Cleans up old log files (both main and schedule logs) before creating
    a new timestamped log file. See specs/logging-system.md §6 for retention
    policy details.

    Args:
        log_dir: Directory to store log files.
        retention_days: Number of days to keep log files (both types).
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

    Cleans up both main invocation logs (android-sync-*.log) and background
    job logs (schedule-*.log) based on file modification time. Active schedules
    continuously update their log file mtime and avoid deletion.

    Specification Reference: specs/logging-system.md §6.2 Cleanup Algorithm

    Args:
        log_dir: Directory containing log files.
        retention_days: Number of days to keep logs. Set to 0 to disable cleanup.

    Returns:
        Number of files removed (both log types combined).
    """
    if retention_days <= 0:
        return 0

    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0

    # Clean up main invocation logs (specs/logging-system.md §6.2)
    for log_file in log_dir.glob("android-sync-*.log"):
        if log_file.stat().st_mtime < cutoff.timestamp():
            log_file.unlink()
            removed += 1

    # Clean up schedule logs (specs/logging-system.md §7.4)
    # mtime updated on each append, ensuring active schedules aren't deleted
    for log_file in log_dir.glob("schedule-*.log"):
        if log_file.stat().st_mtime < cutoff.timestamp():
            log_file.unlink()
            removed += 1

    return removed
