"""Tests for the scheduler module."""

import fcntl
import os
import signal
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import psutil
import pytest

from android_sync.config import Config, Profile, Schedule
from android_sync.scheduler import (
    ScheduleState,
    calculate_next_run,
    check_stale_job,
    get_overdue_schedules,
    get_state_directory,
    load_state,
    save_state,
    spawn_background_job,
    update_state_on_finish,
    update_state_on_start,
)


def create_test_config(**kwargs):
    """Helper to create a Config with default test values."""
    defaults = {
        "bucket": "test-bucket",
        "log_dir": Path("/tmp/logs"),
        "log_retention_days": 7,
        "secrets_file": Path("/test/secrets.gpg"),
        "profiles": {},
        "schedules": {},
        "transfers": 4,
        "stale_job_timeout_hours": 24,
    }
    defaults.update(kwargs)
    return Config(**defaults)


class TestScheduleState:
    """Test ScheduleState dataclass and serialization."""

    def test_state_serialization_round_trip(self, tmp_path):
        """Test saving and loading state preserves all fields."""
        # Create a state with all fields populated
        now = datetime.now()
        state = ScheduleState(
            schedule="test_schedule",
            last_run=now - timedelta(days=1),
            next_run=now + timedelta(days=1),
            status="success",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1),
            pid=12345,
        )

        # Save and load
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)
            loaded_state = load_state("test_schedule", "0 3 * * *")

        # Verify all fields match (with datetime precision tolerance)
        assert loaded_state.schedule == state.schedule
        assert loaded_state.status == state.status
        assert loaded_state.pid == state.pid
        assert abs((loaded_state.last_run - state.last_run).total_seconds()) < 1
        assert abs((loaded_state.next_run - state.next_run).total_seconds()) < 1
        assert abs((loaded_state.started_at - state.started_at).total_seconds()) < 1
        assert abs((loaded_state.finished_at - state.finished_at).total_seconds()) < 1

    def test_state_with_null_fields(self, tmp_path):
        """Test serialization handles null datetime fields correctly."""
        state = ScheduleState(
            schedule="test_schedule",
            last_run=None,
            next_run=None,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)
            loaded_state = load_state("test_schedule", None)

        assert loaded_state.schedule == "test_schedule"
        assert loaded_state.last_run is None
        assert loaded_state.next_run is None
        assert loaded_state.status == "pending"
        assert loaded_state.started_at is None
        assert loaded_state.finished_at is None
        assert loaded_state.pid is None

    def test_corrupted_state_file_recovery(self, tmp_path):
        """Test that corrupted state files are recreated with defaults."""
        state_path = tmp_path / "test_schedule.json"
        state_path.write_text("invalid json {{{")

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            state = load_state("test_schedule", "0 3 * * *")

        # Should create a new state with defaults
        assert state.schedule == "test_schedule"
        assert state.status == "pending"
        assert state.next_run is not None  # Calculated from cron
        assert state.last_run is None

    def test_initial_state_creation_with_cron(self, tmp_path):
        """Test creating initial state for scheduled job."""
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            state = load_state("new_schedule", "0 3 * * *")

        assert state.schedule == "new_schedule"
        assert state.status == "pending"
        assert state.next_run is not None  # Should be calculated
        assert state.last_run is None
        assert state.pid is None

    def test_initial_state_creation_without_cron(self, tmp_path):
        """Test creating initial state for manual-only schedule."""
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            state = load_state("manual_schedule", None)

        assert state.schedule == "manual_schedule"
        assert state.status == "pending"
        assert state.next_run is None  # Manual schedules have no next_run
        assert state.last_run is None


class TestCalculateNextRun:
    """Test cron expression evaluation."""

    def test_daily_cron(self):
        """Test daily cron at 3 AM."""
        base_time = datetime(2026, 1, 19, 0, 0, 0)
        next_run = calculate_next_run("0 3 * * *", base_time)

        assert next_run.hour == 3
        assert next_run.minute == 0
        assert next_run.day == 19  # Same day since we're before 3 AM

    def test_hourly_cron(self):
        """Test every 6 hours cron."""
        base_time = datetime(2026, 1, 19, 1, 30, 0)
        next_run = calculate_next_run("0 */6 * * *", base_time)

        # Next run should be at 6:00
        assert next_run.hour == 6
        assert next_run.minute == 0

    def test_weekly_cron(self):
        """Test weekly cron (Sunday at 2 AM)."""
        # Monday, Jan 20, 2026
        base_time = datetime(2026, 1, 20, 0, 0, 0)
        next_run = calculate_next_run("0 2 * * 0", base_time)

        # Next Sunday is Jan 25 (not 26)
        assert next_run.day == 25
        assert next_run.hour == 2
        assert next_run.minute == 0

    def test_next_run_is_in_future(self):
        """Test that calculated next run is always in the future."""
        now = datetime.now()
        next_run = calculate_next_run("0 3 * * *", now)

        assert next_run > now


class TestCheckStaleJob:
    """Test stale job detection and cleanup."""

    def test_non_running_job_not_stale(self):
        """Test that non-running jobs are not considered stale."""
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )

        assert check_stale_job(state, 24) is False

    @patch("android_sync.scheduler.psutil.Process")
    def test_running_job_within_timeout(self, mock_process_class):
        """Test that running job within timeout is not stale."""
        job_start_time = datetime.now() - timedelta(hours=1)
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=job_start_time,
            finished_at=None,
            pid=os.getpid(),
        )

        # Mock process with matching start time (so PID check passes)
        mock_proc = Mock()
        mock_proc.create_time.return_value = job_start_time.timestamp()
        mock_process_class.return_value = mock_proc

        assert check_stale_job(state, 24) is False

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    def test_running_job_beyond_timeout(self, mock_process_class, mock_pid_exists):
        """Test that job running beyond timeout is marked stale."""
        mock_pid_exists.return_value = True

        # State says job started 25 hours ago
        job_start_time = datetime.now() - timedelta(hours=25)
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=job_start_time,
            finished_at=None,
            pid=12345,
        )

        # Mock process with matching start time (so PID check passes)
        mock_proc = Mock()
        mock_proc.create_time.return_value = job_start_time.timestamp()
        mock_process_class.return_value = mock_proc

        with patch("android_sync.scheduler.os.kill") as mock_kill:
            result = check_stale_job(state, 24)

        assert result is True
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    @patch("android_sync.scheduler.psutil.pid_exists")
    def test_pid_no_longer_exists(self, mock_pid_exists):
        """Test that job with non-existent PID is marked stale."""
        mock_pid_exists.return_value = False

        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=datetime.now() - timedelta(hours=1),
            finished_at=None,
            pid=99999,
        )

        assert check_stale_job(state, 24) is True

    def test_inconsistent_state_running_without_pid(self):
        """Test that running state without PID is marked stale."""
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=datetime.now(),
            finished_at=None,
            pid=None,
        )

        assert check_stale_job(state, 24) is True

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    @patch("android_sync.scheduler.os.kill")
    def test_kill_handles_process_already_gone(
        self, mock_kill, mock_process_class, mock_pid_exists
    ):
        """Test that ProcessLookupError during kill is handled gracefully."""
        mock_pid_exists.return_value = True
        mock_kill.side_effect = ProcessLookupError

        # State says job started 25 hours ago
        job_start_time = datetime.now() - timedelta(hours=25)
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=job_start_time,
            finished_at=None,
            pid=12345,
        )

        # Mock process with matching start time
        mock_proc = Mock()
        mock_proc.create_time.return_value = job_start_time.timestamp()
        mock_process_class.return_value = mock_proc

        # Should not raise exception
        result = check_stale_job(state, 24)
        assert result is True

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    def test_pid_reused_by_different_process(self, mock_process_class, mock_pid_exists):
        """Test PID hijacking mitigation - detects when PID is reused (§9.3)."""
        mock_pid_exists.return_value = True

        # State says job started 1 hour ago
        job_start_time = datetime.now() - timedelta(hours=1)
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=job_start_time,
            finished_at=None,
            pid=12345,
        )

        # Mock process shows it started 5 minutes ago (different process!)
        mock_proc = Mock()
        actual_proc_start = datetime.now() - timedelta(minutes=5)
        mock_proc.create_time.return_value = actual_proc_start.timestamp()
        mock_process_class.return_value = mock_proc

        # Should detect PID hijacking and mark as stale
        result = check_stale_job(state, 24)
        assert result is True
        mock_process_class.assert_called_once_with(12345)

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    def test_pid_start_time_within_tolerance(self, mock_process_class, mock_pid_exists):
        """Test that PID with matching start time (within tolerance) is not marked stale."""
        mock_pid_exists.return_value = True

        # State says job started 1 hour ago
        job_start_time = datetime.now() - timedelta(hours=1)
        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=job_start_time,
            finished_at=None,
            pid=12345,
        )

        # Mock process shows it started at same time (within 30 second tolerance)
        mock_proc = Mock()
        actual_proc_start = job_start_time + timedelta(seconds=30)
        mock_proc.create_time.return_value = actual_proc_start.timestamp()
        mock_process_class.return_value = mock_proc

        # Should NOT mark as stale (same process)
        result = check_stale_job(state, 24)
        assert result is False

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    def test_pid_process_access_denied(self, mock_process_class, mock_pid_exists):
        """Test that AccessDenied exception when checking PID is handled (§9.3)."""
        mock_pid_exists.return_value = True
        mock_process_class.side_effect = psutil.AccessDenied(pid=12345)

        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=datetime.now() - timedelta(hours=1),
            finished_at=None,
            pid=12345,
        )

        # Should mark as stale if we can't access the process
        result = check_stale_job(state, 24)
        assert result is True

    @patch("android_sync.scheduler.psutil.pid_exists")
    @patch("android_sync.scheduler.psutil.Process")
    def test_pid_process_no_such_process(self, mock_process_class, mock_pid_exists):
        """Test that NoSuchProcess exception when checking PID is handled (§9.3)."""
        mock_pid_exists.return_value = True
        mock_process_class.side_effect = psutil.NoSuchProcess(pid=12345)

        state = ScheduleState(
            schedule="test",
            last_run=None,
            next_run=None,
            status="running",
            started_at=datetime.now() - timedelta(hours=1),
            finished_at=None,
            pid=12345,
        )

        # Should mark as stale if process disappeared
        result = check_stale_job(state, 24)
        assert result is True


class TestGetOverdueSchedules:
    """Test overdue schedule detection and prioritization."""

    def test_no_schedules(self, tmp_path):
        """Test with no schedules configured."""
        config = create_test_config(schedules={})

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            overdue = get_overdue_schedules(config)

        assert overdue == []

    def test_single_overdue_schedule(self, tmp_path):
        """Test single overdue schedule is detected."""
        config = create_test_config(
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *")
            }
        )

        # Create state that's overdue
        past_time = datetime.now() - timedelta(hours=2)
        state = ScheduleState(
            schedule="daily",
            last_run=None,
            next_run=past_time,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)
            overdue = get_overdue_schedules(config)

        assert len(overdue) == 1
        assert overdue[0][0] == "daily"
        assert overdue[0][1] > 100  # More than 100 minutes overdue

    def test_multiple_overdue_schedules_prioritized(self, tmp_path):
        """Test multiple overdue schedules are sorted by urgency."""
        config = create_test_config(
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *"),
                "hourly": Schedule(name="hourly", profiles=["docs"], cron="0 * * * *"),
                "weekly": Schedule(name="weekly", profiles=["all"], cron="0 2 * * 0"),
            }
        )

        now = datetime.now()
        states = {
            "daily": ScheduleState(
                schedule="daily",
                last_run=None,
                next_run=now - timedelta(hours=2),  # 2 hours overdue
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
            "hourly": ScheduleState(
                schedule="hourly",
                last_run=None,
                next_run=now - timedelta(minutes=30),  # 30 minutes overdue
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
            "weekly": ScheduleState(
                schedule="weekly",
                last_run=None,
                next_run=now - timedelta(hours=12),  # 12 hours overdue (most urgent)
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
        }

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            for state in states.values():
                save_state(state)
            overdue = get_overdue_schedules(config)

        assert len(overdue) == 3
        # Most overdue should be first (weekly)
        assert overdue[0][0] == "weekly"
        assert overdue[1][0] == "daily"
        assert overdue[2][0] == "hourly"

    def test_manual_schedules_skipped(self, tmp_path):
        """Test that schedules without cron are never overdue."""
        config = create_test_config(
            schedules={
                "manual": Schedule(name="manual", profiles=["photos"], cron=None),
                "scheduled": Schedule(name="scheduled", profiles=["docs"], cron="0 3 * * *"),
            }
        )

        past_time = datetime.now() - timedelta(hours=2)
        states = {
            "manual": ScheduleState(
                schedule="manual",
                last_run=None,
                next_run=None,  # Manual schedules have no next_run
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
            "scheduled": ScheduleState(
                schedule="scheduled",
                last_run=None,
                next_run=past_time,
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
        }

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            for state in states.values():
                save_state(state)
            overdue = get_overdue_schedules(config)

        # Only scheduled should be in the list
        assert len(overdue) == 1
        assert overdue[0][0] == "scheduled"

    @patch("android_sync.scheduler.psutil.Process")
    def test_running_jobs_skipped(self, mock_process_class, tmp_path):
        """Test that currently running jobs are not selected."""
        config = create_test_config(
            schedules={
                "running": Schedule(name="running", profiles=["photos"], cron="0 3 * * *"),
                "pending": Schedule(name="pending", profiles=["docs"], cron="0 * * * *"),
            }
        )

        now = datetime.now()
        past_time = now - timedelta(hours=1)
        job_start = now - timedelta(minutes=10)
        states = {
            "running": ScheduleState(
                schedule="running",
                last_run=None,
                next_run=past_time,
                status="running",
                started_at=job_start,
                finished_at=None,
                pid=os.getpid(),
            ),
            "pending": ScheduleState(
                schedule="pending",
                last_run=None,
                next_run=past_time,
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
        }

        # Mock process with matching start time so PID check passes
        mock_proc = Mock()
        mock_proc.create_time.return_value = job_start.timestamp()
        mock_process_class.return_value = mock_proc

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            for state in states.values():
                save_state(state)
            overdue = get_overdue_schedules(config)

        # Only pending should be in the list
        assert len(overdue) == 1
        assert overdue[0][0] == "pending"

    @patch("android_sync.scheduler.check_stale_job")
    def test_stale_job_handling(self, mock_check_stale, tmp_path):
        """Test that stale jobs are marked as failed then reset to pending."""
        mock_check_stale.return_value = True  # Job is stale

        config = create_test_config(
            schedules={
                "stale": Schedule(name="stale", profiles=["photos"], cron="0 3 * * *")
            }
        )

        now = datetime.now()
        state = ScheduleState(
            schedule="stale",
            last_run=None,
            next_run=now - timedelta(hours=1),  # Past, so will trigger retry
            status="running",
            started_at=now - timedelta(hours=25),  # Running for 25 hours
            finished_at=None,
            pid=12345,
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)
            overdue = get_overdue_schedules(config)

            # Load state to verify it was updated
            updated_state = load_state("stale", "0 3 * * *")

        # State should be reset to pending (failed, but next_run was in past so reset)
        assert updated_state.status == "pending"
        assert updated_state.pid is None
        assert updated_state.finished_at is not None

        # Should be in overdue list (reset to pending and overdue)
        assert len(overdue) == 1
        assert overdue[0][0] == "stale"

    def test_failed_job_reset_on_next_schedule(self, tmp_path):
        """Test that failed jobs are reset to pending when next_run time arrives."""
        config = create_test_config(
            schedules={
                "failed": Schedule(name="failed", profiles=["photos"], cron="0 3 * * *")
            }
        )

        now = datetime.now()
        # Failed job with next_run in the past
        state = ScheduleState(
            schedule="failed",
            last_run=now - timedelta(days=2),
            next_run=now - timedelta(hours=1),  # Retry time has arrived
            status="failed",
            started_at=None,
            finished_at=now - timedelta(days=1),
            pid=None,
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)
            overdue = get_overdue_schedules(config)

            # Load state to verify it was updated
            updated_state = load_state("failed", "0 3 * * *")

        # State should be reset to pending
        assert updated_state.status == "pending"

        # Should be in overdue list
        assert len(overdue) == 1
        assert overdue[0][0] == "failed"


class TestUpdateStateOnStart:
    """Test state updates when job starts."""

    def test_update_state_on_start(self, tmp_path):
        """Test state is correctly updated when job starts."""
        config = create_test_config(
            schedules={
                "test": Schedule(name="test", profiles=["photos"], cron="0 3 * * *")
            }
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            update_state_on_start("test", config)
            state = load_state("test", "0 3 * * *")

        assert state.status == "running"
        assert state.started_at is not None
        assert state.pid == os.getpid()
        assert state.finished_at is None

    def test_update_state_on_start_invalid_schedule(self):
        """Test error when schedule not found."""
        config = create_test_config(schedules={})

        with pytest.raises(ValueError, match="Schedule not found"):
            update_state_on_start("nonexistent", config)


class TestUpdateStateOnFinish:
    """Test state updates when job finishes."""

    def test_update_state_on_success(self, tmp_path):
        """Test state is correctly updated on successful completion."""
        config = create_test_config(
            schedules={
                "test": Schedule(name="test", profiles=["photos"], cron="0 3 * * *")
            }
        )

        # Start the job first
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            update_state_on_start("test", config)

            # Finish successfully
            update_state_on_finish("test", config, success=True)
            state = load_state("test", "0 3 * * *")

        assert state.status == "success"
        assert state.last_run is not None
        assert state.finished_at is not None
        assert state.next_run is not None  # Should calculate next run
        assert state.pid is None

    def test_update_state_on_failure(self, tmp_path):
        """Test state is correctly updated on failure."""
        config = create_test_config(
            schedules={
                "test": Schedule(name="test", profiles=["photos"], cron="0 3 * * *")
            }
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            # Create initial state with next_run
            initial_state = ScheduleState(
                schedule="test",
                last_run=None,
                next_run=datetime.now() + timedelta(hours=1),
                status="running",
                started_at=datetime.now(),
                finished_at=None,
                pid=os.getpid(),
            )
            save_state(initial_state)
            original_next_run = initial_state.next_run

            # Finish with failure
            update_state_on_finish("test", config, success=False)
            state = load_state("test", "0 3 * * *")

        assert state.status == "failed"
        assert state.finished_at is not None
        # next_run should be unchanged (retry at next scheduled time)
        assert abs((state.next_run - original_next_run).total_seconds()) < 1
        assert state.pid is None

    def test_update_state_on_finish_manual_schedule(self, tmp_path):
        """Test state update for manual schedule (no cron)."""
        config = create_test_config(
            schedules={
                "manual": Schedule(name="manual", profiles=["photos"], cron=None)
            }
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            update_state_on_start("manual", config)
            update_state_on_finish("manual", config, success=True)
            state = load_state("manual", None)

        assert state.status == "success"
        assert state.next_run is None  # Manual schedules don't calculate next_run


class TestSpawnBackgroundJob:
    """Test background job spawning."""

    @patch("android_sync.scheduler.subprocess.Popen")
    def test_spawn_background_job(self, mock_popen, tmp_path):
        """Test that background job is spawned with correct parameters."""
        config_path = Path("/test/config.toml")
        home_path = tmp_path / "home"
        home_path.mkdir()

        with patch("android_sync.scheduler.Path.home", return_value=home_path):
            spawn_background_job("test_schedule", config_path)

        # Verify Popen was called correctly
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args

        # Check command
        assert call_args[0][0] == [
            "android-sync",
            "--config",
            "/test/config.toml",
            "run",
            "test_schedule",
        ]

        # Check detachment
        assert call_args[1]["start_new_session"] is True
        assert call_args[1]["cwd"] == home_path


class TestGetStateDirectory:
    """Test state directory creation."""

    def test_state_directory_path(self, tmp_path):
        """Test that state directory path follows XDG spec."""
        home = tmp_path / "home"
        with patch("android_sync.scheduler.Path.home", return_value=home):
            state_dir = get_state_directory()

        expected = home / ".local" / "share" / "android-sync" / "state"
        assert state_dir == expected

    def test_state_directory_created(self, tmp_path):
        """Test that state directory is created if it doesn't exist."""
        home = tmp_path / "home"
        with patch("android_sync.scheduler.Path.home", return_value=home):
            state_dir = get_state_directory()

        assert state_dir.exists()
        assert state_dir.is_dir()


class TestIntegrationCheckCycle:
    """Integration tests for the full check cycle."""

    def test_full_check_cycle_single_overdue(self, tmp_path):
        """Test complete check cycle with single overdue schedule."""
        config = create_test_config(
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *")
            }
        )

        # Create overdue state
        past_time = datetime.now() - timedelta(hours=2)
        state = ScheduleState(
            schedule="daily",
            last_run=None,
            next_run=past_time,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)

            # Get overdue schedules (simulates check command)
            overdue = get_overdue_schedules(config)

        assert len(overdue) == 1
        assert overdue[0][0] == "daily"

    def test_full_cycle_with_job_execution(self, tmp_path):
        """Test complete cycle: overdue → start → finish → next_run calculated."""
        config = create_test_config(
            schedules={
                "test": Schedule(name="test", profiles=["photos"], cron="0 3 * * *")
            }
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            # Initial: create overdue schedule
            past_time = datetime.now() - timedelta(hours=1)
            state = ScheduleState(
                schedule="test",
                last_run=None,
                next_run=past_time,
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            )
            save_state(state)

            # Check shows it's overdue
            overdue = get_overdue_schedules(config)
            assert len(overdue) == 1

            # Simulate job start
            update_state_on_start("test", config)
            state_after_start = load_state("test", "0 3 * * *")
            assert state_after_start.status == "running"
            assert state_after_start.pid is not None

            # Job should not appear in overdue while running
            overdue = get_overdue_schedules(config)
            assert len(overdue) == 0

            # Simulate job finish (success)
            update_state_on_finish("test", config, success=True)
            state_after_finish = load_state("test", "0 3 * * *")
            assert state_after_finish.status == "success"
            assert state_after_finish.last_run is not None
            assert state_after_finish.next_run > datetime.now()  # Future
            assert state_after_finish.pid is None

            # Should not be overdue anymore
            overdue = get_overdue_schedules(config)
            assert len(overdue) == 0

    def test_failure_retry_workflow(self, tmp_path):
        """Test failure → wait for next scheduled time → retry workflow."""
        config = create_test_config(
            schedules={
                "test": Schedule(name="test", profiles=["photos"], cron="0 */6 * * *")
            }
        )

        now = datetime.now()
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            # Job starts
            update_state_on_start("test", config)

            # Job fails
            update_state_on_finish("test", config, success=False)
            state_after_failure = load_state("test", "0 */6 * * *")
            assert state_after_failure.status == "failed"

            # next_run should be in the future (next scheduled time)
            assert state_after_failure.next_run > now

            # Failed job should not be in overdue list yet (next_run is in future)
            overdue = get_overdue_schedules(config)
            assert len(overdue) == 0

            # Manually set next_run to the past to simulate time passing
            state_after_failure.next_run = now - timedelta(minutes=30)
            save_state(state_after_failure)

            # Now it should be reset to pending and appear in overdue list
            overdue = get_overdue_schedules(config)
            assert len(overdue) == 1

            # State should be reset to pending
            state_reset = load_state("test", "0 */6 * * *")
            assert state_reset.status == "pending"

    def test_multiple_schedules_priority(self, tmp_path):
        """Test that most overdue schedule is selected first."""
        config = create_test_config(
            schedules={
                "recent": Schedule(name="recent", profiles=["p1"], cron="0 * * * *"),
                "old": Schedule(name="old", profiles=["p2"], cron="0 3 * * *"),
                "medium": Schedule(name="medium", profiles=["p3"], cron="0 */6 * * *"),
            }
        )

        now = datetime.now()
        states = {
            "recent": ScheduleState(
                schedule="recent",
                last_run=None,
                next_run=now - timedelta(minutes=30),  # 30 min overdue
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
            "old": ScheduleState(
                schedule="old",
                last_run=None,
                next_run=now - timedelta(hours=5),  # 5 hours overdue
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
            "medium": ScheduleState(
                schedule="medium",
                last_run=None,
                next_run=now - timedelta(hours=2),  # 2 hours overdue
                status="pending",
                started_at=None,
                finished_at=None,
                pid=None,
            ),
        }

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            for state in states.values():
                save_state(state)

            overdue = get_overdue_schedules(config)

        # Should be sorted by overdue time (most overdue first)
        assert len(overdue) == 3
        assert overdue[0][0] == "old"  # Most overdue
        assert overdue[1][0] == "medium"
        assert overdue[2][0] == "recent"

    def test_stale_job_detected_and_cleaned(self, tmp_path):
        """Test that stale jobs are detected during check and cleaned up."""
        config = create_test_config(
            schedules={
                "stale": Schedule(name="stale", profiles=["photos"], cron="0 3 * * *")
            },
            stale_job_timeout_hours=1
        )

        now = datetime.now()
        # Create a job that's been running for 2 hours (stale)
        state = ScheduleState(
            schedule="stale",
            last_run=None,
            next_run=now - timedelta(hours=3),  # Past, so will trigger retry
            status="running",
            started_at=now - timedelta(hours=2),  # Started 2 hours ago
            finished_at=None,
            pid=99999,  # Non-existent PID
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            save_state(state)

            # Check for overdue schedules (should detect and clean stale job)
            overdue = get_overdue_schedules(config)

            # Load state to verify it was updated
            state_after_check = load_state("stale", "0 3 * * *")

        # State should be reset to pending (failed, but next_run was in past so reset)
        assert state_after_check.status == "pending"
        assert state_after_check.pid is None
        assert state_after_check.finished_at is not None

        # Should be in overdue list (reset to pending and overdue)
        assert len(overdue) == 1
        assert overdue[0][0] == "stale"

    def test_concurrent_execution_prevention(self, tmp_path):
        """Test that concurrent check commands are prevented by file locking."""
        import argparse

        from android_sync.cli import cmd_check

        config = create_test_config(
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *")
            }
        )

        now = datetime.now()
        state = ScheduleState(
            schedule="daily",
            last_run=None,
            next_run=now - timedelta(hours=1),  # Overdue
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )

        # Patch get_state_directory for both scheduler and cli modules
        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path), \
             patch("android_sync.cli.get_state_directory", return_value=tmp_path):
            save_state(state)

            # Create args namespace with config path
            args = argparse.Namespace(config=Path("/tmp/config.toml"))

            # Acquire lock manually to simulate concurrent execution
            lock_file_path = tmp_path / "check.lock"
            lock_file = open(lock_file_path, "w")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            try:
                # Try to run check command while lock is held
                with patch("android_sync.cli.spawn_background_job") as mock_spawn:
                    result = cmd_check(config, args)

                    # Should exit without spawning job (lock is held)
                    assert result == 0
                    mock_spawn.assert_not_called()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

            # Now run again without lock - should spawn job
            with patch("android_sync.cli.spawn_background_job") as mock_spawn:
                result = cmd_check(config, args)

                # Should successfully spawn job
                assert result == 0
                mock_spawn.assert_called_once()

class TestDryRunStateIsolation:
    """Test that dry-run mode never updates schedule state (Spec: CLI Architecture §5.1)."""

    def test_dry_run_does_not_create_state_file(self, tmp_path):
        """Test that dry-run doesn't create state file for new schedule."""
        # Create config with actual profile
        config = create_test_config(
            profiles={
                "photos": Profile(
                    name="photos",
                    sources=["/test/photos"],
                    destination="photos",
                )
            },
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *"),
            },
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            from android_sync.cli import cmd_run
            from android_sync.sync import SyncResult

            # Mock credentials and sync
            mock_creds = {"key_id": "test", "application_key": "test"}
            with patch("android_sync.cli.get_b2_credentials", return_value=mock_creds):
                with patch("android_sync.cli.sync_profile") as mock_sync:
                    mock_sync.return_value = SyncResult(
                        profile_name="photos",
                        success=True,
                        files_transferred=10,
                        bytes_transferred=1000,
                        hidden_files=[]
                    )

                    mock_logger = Mock()
                    args = Mock(schedule="daily", profile=None, all=False, dry_run=True)

                    result = cmd_run(config, args, mock_logger)

                    # Command should succeed
                    assert result == 0

                    # State file should NOT be created
                    state_file = tmp_path / "daily.json"
                    assert not state_file.exists()

    def test_dry_run_does_not_modify_existing_state(self, tmp_path):
        """Test that dry-run doesn't modify existing state file."""
        config = create_test_config(
            profiles={
                "photos": Profile(
                    name="photos",
                    sources=["/test/photos"],
                    destination="photos",
                )
            },
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *"),
            },
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            # Create initial state
            initial_state = ScheduleState(
                schedule="daily",
                last_run=datetime(2026, 1, 19, 3, 0, 0),
                next_run=datetime(2026, 1, 20, 3, 0, 0),
                status="success",
                started_at=None,
                finished_at=datetime(2026, 1, 19, 3, 5, 0),
                pid=None,
            )
            save_state(initial_state)

            from android_sync.cli import cmd_run
            from android_sync.sync import SyncResult

            mock_creds = {"key_id": "test", "application_key": "test"}
            with patch("android_sync.cli.get_b2_credentials", return_value=mock_creds):
                with patch("android_sync.cli.sync_profile") as mock_sync:
                    mock_sync.return_value = SyncResult(
                        profile_name="photos",
                        success=True,
                        files_transferred=10,
                        bytes_transferred=1000,
                        hidden_files=[]
                    )

                    mock_logger = Mock()
                    args = Mock(schedule="daily", profile=None, all=False, dry_run=True)

                    result = cmd_run(config, args, mock_logger)
                    assert result == 0

                    # Load state and verify it's unchanged
                    current_state = load_state("daily", "0 3 * * *")
                    assert current_state.status == initial_state.status
                    assert current_state.last_run == initial_state.last_run
                    assert current_state.next_run == initial_state.next_run
                    assert current_state.pid is None

    def test_live_run_updates_state_after_dry_run(self, tmp_path):
        """Test that live run updates state correctly after a dry-run."""
        config = create_test_config(
            profiles={
                "photos": Profile(
                    name="photos",
                    sources=["/test/photos"],
                    destination="photos",
                )
            },
            schedules={
                "daily": Schedule(name="daily", profiles=["photos"], cron="0 3 * * *"),
            },
        )

        with patch("android_sync.scheduler.get_state_directory", return_value=tmp_path):
            from android_sync.cli import cmd_run
            from android_sync.sync import SyncResult

            mock_creds = {"key_id": "test", "application_key": "test"}
            with patch("android_sync.cli.get_b2_credentials", return_value=mock_creds):
                with patch("android_sync.cli.sync_profile") as mock_sync:
                    mock_sync.return_value = SyncResult(
                        profile_name="photos",
                        success=True,
                        files_transferred=10,
                        bytes_transferred=1000,
                        hidden_files=[]
                    )

                    mock_logger = Mock()

                    # 1. First run with dry-run
                    args_dry = Mock(schedule="daily", profile=None, all=False, dry_run=True)
                    result = cmd_run(config, args_dry, mock_logger)
                    assert result == 0

                    # State file should not exist
                    state_file = tmp_path / "daily.json"
                    assert not state_file.exists()

                    # 2. Now run without dry-run (live run)
                    args_live = Mock(schedule="daily", profile=None, all=False, dry_run=False)
                    result = cmd_run(config, args_live, mock_logger)
                    assert result == 0

                    # State file should now exist and be updated
                    assert state_file.exists()

                    # Verify state was updated correctly
                    state = load_state("daily", "0 3 * * *")
                    assert state.status == "success"
                    assert state.last_run is not None
                    assert state.next_run is not None
                    assert state.next_run > datetime.now()
                    assert state.pid is None


class TestScheduleLogRetention:
    """Integration tests for schedule log retention behavior (specs/logging-system.md §7.4)."""

    def test_schedule_log_mtime_updates_on_run(self, tmp_path):
        """Test that running a scheduled job updates the log's mtime.

        This verifies that active schedules won't have their logs deleted by the
        retention policy, since mtime is updated on each append operation.

        Specification Reference: specs/logging-system.md §7.4
        Implementation Reference: implementation-plan.md §5.2
        """
        from android_sync.logging import cleanup_old_logs

        # Create a schedule log file with old mtime
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        schedule_log = log_dir / "schedule-test.log"
        schedule_log.write_text("Initial log content\n")

        # Set mtime to 10 days ago
        import os
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(schedule_log, (old_time, old_time))

        # Verify the old mtime was set
        initial_mtime = schedule_log.stat().st_mtime
        assert initial_mtime == old_time

        # Simulate a scheduled job appending to the log
        # (In real usage, spawn_background_job opens the file in append mode)
        import time
        time.sleep(0.1)  # Ensure time passes for mtime difference
        with open(schedule_log, "a") as log:
            log.write("New log entry from scheduled run\n")

        # Verify mtime was updated to recent time
        updated_mtime = schedule_log.stat().st_mtime
        assert updated_mtime > initial_mtime
        assert updated_mtime > (datetime.now() - timedelta(seconds=5)).timestamp()

        # Run cleanup with short retention (7 days)
        # The file should NOT be deleted because mtime is now recent
        removed = cleanup_old_logs(log_dir, retention_days=7)

        # Assert file was NOT deleted (mtime was updated by append)
        assert removed == 0
        assert schedule_log.exists()
        assert "New log entry" in schedule_log.read_text()

    def test_abandoned_schedule_logs_cleaned_up(self, tmp_path):
        """Test that abandoned schedule logs (old mtime) are cleaned up.

        This verifies that inactive schedules (not run for N days) have old mtime
        and get cleaned up by the retention policy.

        Specification Reference: specs/logging-system.md §7.4
        Implementation Reference: implementation-plan.md §5.2
        """
        from android_sync.logging import cleanup_old_logs

        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create abandoned schedule log with old mtime
        abandoned_log = log_dir / "schedule-abandoned.log"
        abandoned_log.write_text("Old log content from inactive schedule\n")

        # Set mtime to 10 days ago (beyond retention period)
        import os
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(abandoned_log, (old_time, old_time))

        # Create a recent schedule log that should be kept
        active_log = log_dir / "schedule-active.log"
        active_log.write_text("Recent log content from active schedule\n")
        # Leave mtime as current (just created)

        # Run cleanup with retention_days=7
        removed = cleanup_old_logs(log_dir, retention_days=7)

        # Assert only the abandoned log was deleted
        assert removed == 1
        assert not abandoned_log.exists()
        assert active_log.exists()
