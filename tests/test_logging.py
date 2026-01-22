"""Tests for logging system.

Tests cover log file creation, retention cleanup, and dual output configuration.
See specs/logging-system.md for detailed specifications.
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from android_sync.logging import cleanup_old_logs, setup_logging


class TestCleanupOldLogs:
    """Tests for cleanup_old_logs() function (specs/logging-system.md §6.2)."""

    def test_cleanup_disabled_with_zero_retention(self):
        """Test that retention_days=0 disables cleanup entirely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create old log files
            old_main_log = log_dir / "android-sync-20200101-000000.log"
            old_schedule_log = log_dir / "schedule-daily.log"
            old_main_log.touch()
            old_schedule_log.touch()

            # Set mtime to 30 days ago
            old_time = (datetime.now() - timedelta(days=30)).timestamp()
            old_main_log.touch()
            old_schedule_log.touch()
            # Use os.utime to set old mtime
            import os
            os.utime(old_main_log, (old_time, old_time))
            os.utime(old_schedule_log, (old_time, old_time))

            # Run cleanup with retention_days=0
            removed = cleanup_old_logs(log_dir, retention_days=0)

            # Assert no files deleted
            assert removed == 0
            assert old_main_log.exists()
            assert old_schedule_log.exists()

    def test_cleanup_old_logs_includes_schedule_logs(self):
        """Test that cleanup handles both main and schedule logs.

        Specification Reference: specs/logging-system.md §6.2
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create mix of old and new log files
            old_main = log_dir / "android-sync-20200101-000000.log"
            new_main = log_dir / "android-sync-20260120-000000.log"
            old_schedule = log_dir / "schedule-old.log"
            new_schedule = log_dir / "schedule-active.log"

            # Create all files
            for f in [old_main, new_main, old_schedule, new_schedule]:
                f.touch()

            # Set mtime: old files to 10 days ago, new files to now
            import os
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            os.utime(old_main, (old_time, old_time))
            os.utime(old_schedule, (old_time, old_time))

            # Run cleanup with retention_days=7
            removed = cleanup_old_logs(log_dir, retention_days=7)

            # Assert only old files (both types) are deleted
            assert removed == 2
            assert not old_main.exists()
            assert not old_schedule.exists()
            assert new_main.exists()
            assert new_schedule.exists()

    def test_cleanup_schedule_logs_respects_retention(self):
        """Test that schedule logs follow retention policy.

        Active schedules (recent mtime) should be kept.
        Inactive schedules (old mtime) should be deleted.

        Specification Reference: specs/logging-system.md §7.4
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create schedule logs with different ages
            old_schedule = log_dir / "schedule-daily.log"
            recent_schedule = log_dir / "schedule-frequent.log"
            old_schedule.touch()
            recent_schedule.touch()

            # Set mtime: old to 10 days ago, recent to 2 days ago
            import os
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            recent_time = (datetime.now() - timedelta(days=2)).timestamp()
            os.utime(old_schedule, (old_time, old_time))
            os.utime(recent_schedule, (recent_time, recent_time))

            # Run cleanup with retention_days=7
            removed = cleanup_old_logs(log_dir, retention_days=7)

            # Assert old file deleted, recent file retained
            assert removed == 1
            assert not old_schedule.exists()
            assert recent_schedule.exists()

    def test_cleanup_returns_correct_count(self):
        """Test that cleanup returns accurate count of removed files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create multiple old files
            import os
            old_time = (datetime.now() - timedelta(days=10)).timestamp()

            old_files = [
                log_dir / "android-sync-20200101-000000.log",
                log_dir / "android-sync-20200102-000000.log",
                log_dir / "schedule-old1.log",
                log_dir / "schedule-old2.log",
            ]

            for f in old_files:
                f.touch()
                os.utime(f, (old_time, old_time))

            # Run cleanup
            removed = cleanup_old_logs(log_dir, retention_days=7)

            # Assert correct count
            assert removed == 4
            for f in old_files:
                assert not f.exists()

    def test_cleanup_ignores_non_matching_files(self):
        """Test that cleanup only removes matching log files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create various files
            import os
            old_time = (datetime.now() - timedelta(days=10)).timestamp()

            # Files that should be cleaned up
            old_main = log_dir / "android-sync-20200101-000000.log"
            old_schedule = log_dir / "schedule-test.log"

            # Files that should NOT be cleaned up (wrong names)
            other_file = log_dir / "other.log"
            txt_file = log_dir / "android-sync-notes.txt"

            for f in [old_main, old_schedule, other_file, txt_file]:
                f.touch()
                os.utime(f, (old_time, old_time))

            # Run cleanup
            removed = cleanup_old_logs(log_dir, retention_days=7)

            # Assert only matching files removed
            assert removed == 2
            assert not old_main.exists()
            assert not old_schedule.exists()
            assert other_file.exists()
            assert txt_file.exists()


class TestSetupLogging:
    """Tests for setup_logging() function."""

    def test_setup_logging_creates_log_file(self):
        """Test that setup_logging creates a log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            logger = setup_logging(log_dir, retention_days=7, verbose=False)

            # Assert log file was created
            log_files = list(log_dir.glob("android-sync-*.log"))
            assert len(log_files) == 1
            assert logger is not None

    def test_setup_logging_runs_cleanup(self):
        """Test that setup_logging runs cleanup before creating new log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create old log file
            import os
            old_log = log_dir / "android-sync-20200101-000000.log"
            old_log.touch()
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            os.utime(old_log, (old_time, old_time))

            # Run setup_logging
            setup_logging(log_dir, retention_days=7, verbose=False)

            # Assert old log was cleaned up
            assert not old_log.exists()
            # Assert new log was created
            log_files = list(log_dir.glob("android-sync-*.log"))
            assert len(log_files) == 1

    def test_setup_logging_creates_directory(self):
        """Test that setup_logging creates log directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs" / "subdir"

            # Directory should not exist yet
            assert not log_dir.exists()

            # Run setup_logging
            setup_logging(log_dir, retention_days=7, verbose=False)

            # Assert directory was created
            assert log_dir.exists()
            # Assert log file was created
            log_files = list(log_dir.glob("android-sync-*.log"))
            assert len(log_files) == 1
