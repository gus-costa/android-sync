"""Scheduling system for automatic, time-based sync execution."""

import json
import logging
import os
import signal
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import psutil
from croniter import croniter

from android_sync.config import Config

logger = logging.getLogger(__name__)


@dataclass
class ScheduleState:
    """State information for a schedule."""

    schedule: str
    last_run: datetime | None
    next_run: datetime | None
    status: Literal["pending", "running", "success", "failed"]
    started_at: datetime | None
    finished_at: datetime | None
    pid: int | None


def get_state_directory() -> Path:
    """Get the state directory path, creating it if needed."""
    state_dir = Path.home() / ".local" / "share" / "android-sync" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _get_state_path(schedule_name: str) -> Path:
    """Get the path to a schedule's state file."""
    return get_state_directory() / f"{schedule_name}.json"


def load_state(schedule_name: str, cron_expr: str | None) -> ScheduleState:
    """Load state for a schedule, creating initial state if file doesn't exist.

    Args:
        schedule_name: Name of the schedule
        cron_expr: Cron expression (None for manual schedules)

    Returns:
        ScheduleState object
    """
    state_path = _get_state_path(schedule_name)

    if not state_path.exists():
        # Create initial state
        next_run = None
        if cron_expr is not None:
            next_run = calculate_next_run(cron_expr, datetime.now())

        state = ScheduleState(
            schedule=schedule_name,
            last_run=None,
            next_run=next_run,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )
        save_state(state)
        return state

    # Load existing state
    try:
        with open(state_path) as f:
            data = json.load(f)

        # Convert ISO strings to datetime objects
        return ScheduleState(
            schedule=data["schedule"],
            last_run=(
                datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None
            ),
            next_run=(
                datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None
            ),
            status=data["status"],
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            finished_at=(
                datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None
            ),
            pid=data.get("pid"),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # State file corrupted, recreate
        logger.warning("State file corrupted for %s: %s. Recreating.", schedule_name, e)
        next_run = None
        if cron_expr is not None:
            next_run = calculate_next_run(cron_expr, datetime.now())

        state = ScheduleState(
            schedule=schedule_name,
            last_run=None,
            next_run=next_run,
            status="pending",
            started_at=None,
            finished_at=None,
            pid=None,
        )
        save_state(state)
        return state


def save_state(state: ScheduleState) -> None:
    """Save schedule state to file.

    Args:
        state: ScheduleState to save
    """
    state_path = _get_state_path(state.schedule)

    # Convert to dict and handle datetime serialization
    data = asdict(state)
    for key in ["last_run", "next_run", "started_at", "finished_at"]:
        if data[key] is not None:
            data[key] = data[key].isoformat()

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)


def calculate_next_run(cron_expr: str, from_time: datetime) -> datetime:
    """Calculate next run time from a given time using cron expression.

    Args:
        cron_expr: Standard cron expression (5 fields)
        from_time: Calculate next run from this time

    Returns:
        Next scheduled datetime
    """
    cron = croniter(cron_expr, from_time)
    return cron.get_next(datetime)


def check_stale_job(state: ScheduleState, timeout_hours: int) -> bool:
    """Check if a job is stale and handle it.

    Args:
        state: Current schedule state
        timeout_hours: Maximum hours a job can run

    Returns:
        True if job was stale (state should be updated)
        False if job is still running normally
    """
    if state.status != "running":
        return False

    if state.pid is None or state.started_at is None:
        # Inconsistent state, mark as stale
        return True

    # Check if PID exists
    if not psutil.pid_exists(state.pid):
        logger.warning("Job %s PID %d no longer exists", state.schedule, state.pid)
        return True

    # Check runtime
    runtime = datetime.now() - state.started_at
    runtime_hours = runtime.total_seconds() / 3600

    if runtime_hours > timeout_hours:
        logger.warning(
            "Job %s has been running for %.1f hours (timeout: %d), killing",
            state.schedule,
            runtime_hours,
            timeout_hours,
        )
        try:
            os.kill(state.pid, signal.SIGTERM)
        except ProcessLookupError:
            # Process already gone
            pass
        return True

    return False


def get_overdue_schedules(config: Config) -> list[tuple[str, float]]:
    """Get list of overdue schedules with their overdue minutes.

    Args:
        config: Application configuration

    Returns:
        List of (schedule_name, overdue_minutes) tuples, sorted by urgency (most overdue first)
    """
    overdue = []
    now = datetime.now()

    for schedule_name, schedule in config.schedules.items():
        # Skip manual schedules (no cron expression)
        if schedule.cron is None:
            continue

        # Load state
        state = load_state(schedule_name, schedule.cron)

        # Check for stale jobs
        if check_stale_job(state, config.stale_job_timeout_hours):
            # Job is stale, update state
            state.status = "failed"
            state.finished_at = now
            state.pid = None
            save_state(state)

        # Skip if already running (and not stale)
        if state.status == "running":
            continue

        # Reset failed jobs if it's time to retry
        if state.status == "failed" and state.next_run is not None:
            if now >= state.next_run:
                state.status = "pending"
                save_state(state)

        # Check if schedule is overdue
        if state.next_run is not None and now >= state.next_run:
            overdue_minutes = (now - state.next_run).total_seconds() / 60
            overdue.append((schedule_name, overdue_minutes))

    # Sort by urgency (most overdue first)
    overdue.sort(key=lambda x: x[1], reverse=True)

    return overdue


def spawn_background_job(schedule_name: str, config_path: Path) -> None:
    """Spawn detached background process for schedule execution.

    Args:
        schedule_name: Name of schedule to run
        config_path: Path to configuration file
    """
    log_dir = Path.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"schedule-{schedule_name}.log"

    with open(log_file, "a") as log:
        subprocess.Popen(
            ["android-sync", "--config", str(config_path), "run", schedule_name],
            start_new_session=True,  # Detach from parent
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=Path.home(),
        )


def update_state_on_start(schedule_name: str, config: Config) -> None:
    """Update state when a job starts.

    Args:
        schedule_name: Name of schedule being started
        config: Application configuration
    """
    schedule = config.schedules.get(schedule_name)
    if schedule is None:
        raise ValueError(f"Schedule not found: {schedule_name}")

    state = load_state(schedule_name, schedule.cron)
    state.status = "running"
    state.started_at = datetime.now()
    state.pid = os.getpid()
    state.finished_at = None
    save_state(state)


def update_state_on_finish(schedule_name: str, config: Config, success: bool) -> None:
    """Update state when a job finishes.

    Args:
        schedule_name: Name of schedule that finished
        config: Application configuration
        success: Whether the job succeeded
    """
    schedule = config.schedules.get(schedule_name)
    if schedule is None:
        raise ValueError(f"Schedule not found: {schedule_name}")

    state = load_state(schedule_name, schedule.cron)
    now = datetime.now()
    state.finished_at = now

    if success:
        state.status = "success"
        state.last_run = now
        # Calculate next run if this is a scheduled job
        if schedule.cron is not None:
            state.next_run = calculate_next_run(schedule.cron, now)
    else:
        state.status = "failed"
        # Keep next_run unchanged (retry at next scheduled time)

    state.pid = None
    save_state(state)
