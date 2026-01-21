"""Scheduling system for automatic, time-based sync execution.

This module implements the scheduling functionality as defined in specs/scheduling.md.
It provides state management, cron-based scheduling, stale job detection, and background
job spawning for unattended sync execution on Android devices.

Key components:
- State management (§4): ScheduleState tracking with persistence
- Stale job detection (§5.3.1): Timeout-based job cleanup
- Background spawning (§5.3.2): Detached process execution
- Next run calculation (§5.3.3): Cron-based scheduling
- Overdue detection (§5.3.4): Priority-based schedule selection
"""

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
    """State information for a schedule.

    Implements the state file schema defined in §4.2 of specs/scheduling.md.

    Attributes:
        schedule: Schedule name from configuration
        last_run: Last successful run completion time (nullable)
        next_run: Calculated next execution time; null for manual schedules (§4.3)
        status: Current state - "pending", "running", "success", or "failed"
        started_at: When current/last run started (nullable)
        finished_at: When current/last run finished (nullable)
        pid: Process ID of running job (nullable)
    """

    schedule: str
    last_run: datetime | None
    next_run: datetime | None
    status: Literal["pending", "running", "success", "failed"]
    started_at: datetime | None
    finished_at: datetime | None
    pid: int | None


def get_state_directory() -> Path:
    """Get the state directory path, creating it if needed.

    Returns the XDG-compliant state directory as specified in §4.1.
    Location: ~/.local/share/android-sync/state/

    Returns:
        Path object for the state directory
    """
    state_dir = Path.home() / ".local" / "share" / "android-sync" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _get_state_path(schedule_name: str) -> Path:
    """Get the path to a schedule's state file."""
    return get_state_directory() / f"{schedule_name}.json"


def load_state(schedule_name: str, cron_expr: str | None) -> ScheduleState:
    """Load state for a schedule, creating initial state if file doesn't exist.

    Implements initial state creation (§4.3) and state file corruption handling (§7.2).
    For new schedules, creates pending state with calculated next_run (if cron exists).
    For manual schedules (cron_expr=None), next_run is always None (§4.3).

    Args:
        schedule_name: Name of the schedule
        cron_expr: Cron expression (None for manual schedules)

    Returns:
        ScheduleState object
    """
    state_path = _get_state_path(schedule_name)

    if not state_path.exists():
        # Create initial state (§4.3 Initial State)
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
        # State file corrupted, recreate (§7.2 State File Corruption)
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

    Persists state as JSON with ISO 8601 datetime formatting as specified in §4.2.

    Args:
        state: ScheduleState to save
    """
    state_path = _get_state_path(state.schedule)

    # Convert to dict and handle datetime serialization (§4.2 State File Schema)
    data = asdict(state)
    for key in ["last_run", "next_run", "started_at", "finished_at"]:
        if data[key] is not None:
            data[key] = data[key].isoformat()

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)


def calculate_next_run(cron_expr: str, from_time: datetime) -> datetime:
    """Calculate next run time from a given time using cron expression.

    Implements §5.3.3 Next Run Calculation using croniter library.
    Uses system timezone (Android device timezone) as specified in §3.2.

    Args:
        cron_expr: Standard cron expression (5 fields: minute hour day_of_month month day_of_week)
        from_time: Calculate next run from this time

    Returns:
        Next scheduled datetime
    """
    cron = croniter(cron_expr, from_time)
    return cron.get_next(datetime)


def check_stale_job(state: ScheduleState, timeout_hours: int) -> bool:
    """Check if a job is stale and handle it.

    Implements §5.3.1 Stale Job Detection. A job is considered stale if:
    1. PID no longer exists (crashed/killed externally)
    2. PID exists but process start time doesn't match (PID reused by different process - §9.3)
    3. Runtime exceeds timeout_hours (kills with SIGTERM per §9.3)

    Stale jobs are marked as failed and follow retry behavior per §2.3.

    Args:
        state: Current schedule state
        timeout_hours: Maximum hours a job can run (from config.stale_job_timeout_hours)

    Returns:
        True if job was stale (state should be updated)
        False if job is still running normally
    """
    if state.status != "running":
        return False

    if state.pid is None or state.started_at is None:
        # Inconsistent state, mark as stale
        return True

    # Check if PID exists (§5.3.1 algorithm step 2)
    if not psutil.pid_exists(state.pid):
        logger.warning("Job %s PID %d no longer exists", state.schedule, state.pid)
        return True

    # Verify process start time to prevent PID hijacking (§9.3 Security Considerations)
    # If PID was reused by a different process, mark as stale to avoid killing innocent process
    try:
        proc = psutil.Process(state.pid)
        proc_start = datetime.fromtimestamp(proc.create_time())
        # Allow 60 second tolerance for clock precision and process startup time
        time_diff = abs((proc_start - state.started_at).total_seconds())
        if time_diff > 60:
            logger.warning(
                "Job %s PID %d start time mismatch (diff: %.1fs), "
                "PID likely reused by different process",
                state.schedule,
                state.pid,
                time_diff,
            )
            return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # Process disappeared or we can't access it
        logger.warning("Job %s PID %d no longer accessible", state.schedule, state.pid)
        return True

    # Check runtime (§5.3.1 algorithm step 3)
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
            # Use SIGTERM for graceful shutdown (§9.3 PID Hijacking mitigation)
            os.kill(state.pid, signal.SIGTERM)
        except ProcessLookupError:
            # Process already gone
            pass
        return True

    return False


def get_overdue_schedules(config: Config) -> list[tuple[str, float]]:
    """Get list of overdue schedules with their overdue minutes.

    Implements §5.3.4 Overdue Schedule Detection. This function:
    1. Skips manual schedules (no cron expression per §4.3)
    2. Handles stale jobs (marks as failed per §4.3 On Stale Detection)
    3. Resets failed jobs at retry time (per §2.3 Retry Behavior)
    4. Calculates urgency using priority model (§2.2)

    Args:
        config: Application configuration

    Returns:
        List of (schedule_name, overdue_minutes) tuples, sorted by urgency (most overdue first)
    """
    overdue = []
    now = datetime.now()

    for schedule_name, schedule in config.schedules.items():
        # Skip manual schedules (§4.3 Manual Schedule Behavior)
        if schedule.cron is None:
            continue

        # Load state
        state = load_state(schedule_name, schedule.cron)

        # Check for stale jobs (§5.3.1)
        if check_stale_job(state, config.stale_job_timeout_hours):
            # Job is stale, update state (§4.3 On Stale Detection)
            state.status = "failed"
            state.finished_at = now
            state.pid = None
            save_state(state)

        # Skip if already running (and not stale)
        if state.status == "running":
            continue

        # Reset failed jobs if it's time to retry (§4.3 On Schedule Time)
        if state.status == "failed" and state.next_run is not None:
            if now >= state.next_run:
                state.status = "pending"
                save_state(state)

        # Check if schedule is overdue (§2.2 Priority Model)
        if state.next_run is not None and now >= state.next_run:
            overdue_minutes = (now - state.next_run).total_seconds() / 60
            overdue.append((schedule_name, overdue_minutes))

    # Sort by urgency - most overdue first (§2.2)
    overdue.sort(key=lambda x: x[1], reverse=True)

    return overdue


def spawn_background_job(schedule_name: str, config_path: Path) -> None:
    """Spawn detached background process for schedule execution.

    Implements §5.3.2 Background Job Spawning. Creates a fully detached process
    that survives parent exit (§2.1 Execution Phase). Logs to file for debugging.

    Args:
        schedule_name: Name of schedule to run
        config_path: Path to configuration file
    """
    log_dir = Path.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"schedule-{schedule_name}.log"

    with open(log_file, "a") as log:
        # Create detached process (§5.3.2 Implementation)
        subprocess.Popen(
            ["android-sync", "--config", str(config_path), "run", schedule_name],
            start_new_session=True,  # Detach from parent - survives check script exit
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=Path.home(),
        )


def update_state_on_start(schedule_name: str, config: Config) -> None:
    """Update state when a job starts.

    Implements §4.3 On Job Start state transition. Sets status to "running"
    and records PID for stale job detection.

    Args:
        schedule_name: Name of schedule being started
        config: Application configuration
    """
    schedule = config.schedules.get(schedule_name)
    if schedule is None:
        raise ValueError(f"Schedule not found: {schedule_name}")

    state = load_state(schedule_name, schedule.cron)
    # Update state per §4.3 On Job Start
    state.status = "running"
    state.started_at = datetime.now()
    state.pid = os.getpid()
    state.finished_at = None
    save_state(state)


def update_state_on_finish(schedule_name: str, config: Config, success: bool) -> None:
    """Update state when a job finishes.

    Implements §4.3 On Job Success/Failure state transitions and §2.3 Retry Behavior.
    On success: calculates next run time from current time.
    On failure: keeps next_run unchanged - no immediate retry, waits for next scheduled time.

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
        # §4.3 On Job Success
        state.status = "success"
        state.last_run = now
        # Calculate next run if this is a scheduled job
        if schedule.cron is not None:
            state.next_run = calculate_next_run(schedule.cron, now)
    else:
        # §4.3 On Job Failure + §2.3 Retry Behavior
        state.status = "failed"
        # Keep next_run unchanged - no immediate retry, will retry at next scheduled time

    state.pid = None
    save_state(state)
