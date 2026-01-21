# Scheduling Feature Specification

**Version:** 1.0
**Date:** 2026-01-19
**Status:** Draft

## 1. Overview

This specification defines a time-based scheduling system for android-sync that enables automatic, unattended execution of sync schedules using Android's JobScheduler API via termux-job-scheduler.

### 1.1 Goals

- Enable reliable, time-based execution of sync schedules
- Work within Android's background execution constraints
- Survive device reboots
- Handle network failures and retry appropriately
- Respect network and battery constraints to avoid unnecessary data usage and battery drain
- Require zero manual intervention after initial setup

### 1.2 Non-Goals

- Precise timing (tolerance: ~12 hours from scheduled time is acceptable)
- Real-time notifications on sync completion
- Parallel execution of multiple schedules
- Backward compatibility with previous versions

## 2. Architecture

### 2.1 Execution Model

The scheduling system uses a **periodic check pattern**:

1. **JobScheduler Trigger** (every 15 minutes)
   - Android JobScheduler wakes the check script
   - Only triggers when configured constraints are met (network, battery)
   - Persists across device reboots

2. **Check Phase** (runs quickly, exits immediately)
   - Load configuration and state files
   - Calculate which schedules are overdue
   - Handle stale jobs
   - Spawn at most one background job if needed
   - Exit (must complete in <30 seconds to avoid Android timeout)

3. **Execution Phase** (runs in background)
   - Detached background process runs the sync
   - Updates state file on start and completion
   - Independent of check phase lifecycle

**Constraint Handling:**
- Network and battery constraints are enforced at the JobScheduler level
- If constraints aren't met when a schedule is due, the check doesn't run at all
- Job remains overdue and will be picked up on the next check cycle (within 15 minutes of constraints being satisfied)
- No explicit retry logic needed - JobScheduler handles constraint waiting automatically

### 2.2 Priority Model

When multiple schedules are overdue, select by **urgency**:

- Calculate `overdue_minutes = now - next_scheduled_run`
- Execute the schedule with the highest overdue time
- This naturally prioritizes daily over weekly tasks

### 2.3 Failure Handling and Retry Strategy

**On Successful Execution:**
- Set status to "success"
- Calculate next run time from current time using cron expression
- Clear failure counters

**On Failed Execution:**
- Set status to "failed"
- Keep next_run unchanged (no immediate retry)
- Record failure time

**Retry Behavior:**
- Failed jobs do NOT retry immediately
- Wait until the next scheduled cron time: `now >= next_run`
- When scheduled time arrives, reset status to "pending"
- Job becomes eligible for execution on next check cycle
- No exponential backoff (rely on cron schedule intervals)

**Stale Job Handling:**
- If job runs longer than configured timeout, kill it with SIGTERM
- Mark as failed
- Follow same retry behavior as regular failures

**Rationale:**
- Simplicity: No complex backoff logic needed
- Predictability: Jobs always retry at their normal scheduled times
- Resource-friendly: Prevents retry storms from multiple failed schedules
- Natural spacing: Cron intervals provide built-in backoff (e.g., hourly schedule = 1hr backoff)

## 3. Configuration Schema

### 3.1 General Section

New optional field in `[general]`:

```toml
[general]
bucket = "my-bucket"
# ... existing fields ...
stale_job_timeout_hours = 24  # Optional, default: 24
```

**Field Definitions:**

- `stale_job_timeout_hours` (integer, optional, default: 24)
  - Maximum hours a job can run before being considered stale
  - If exceeded and PID still exists, job is killed
  - Minimum: 1, Maximum: 168 (1 week)

**Hardcoded Constraints:**

The scheduler enforces the following constraints for all scheduled jobs (not configurable):

- **Network Requirement**: Scheduled checks only run when any network connection is available (WiFi or cellular)
  - Prevents sync attempts when device is offline
  - Uses Android JobScheduler's `--network any` constraint

- **Battery Not Low**: Scheduled checks only run when battery is not in low state
  - Android defines "low battery" as typically below 15% charge
  - Prevents large syncs from draining battery when already low
  - Uses Android JobScheduler's `--battery-not-low` constraint

These constraints ensure reliable, unattended operation without requiring user configuration.

### 3.2 Schedule Section

New optional field for each schedule:

```toml
[schedules.daily]
profiles = ["photos", "documents"]  # Existing
cron = "0 3 * * *"  # Optional: enables automatic scheduling

[schedules.photos_frequent]
profiles = ["photos"]
cron = "0 */6 * * *"  # Every 6 hours

[schedules.manual_only]
profiles = ["all"]
# No cron field - manual execution only via `android-sync run manual_only`

[schedules.weekly]
profiles = ["all"]
cron = "0 2 * * 0"  # Sundays at 2 AM
```

**Field Definitions:**

- `cron` (string, optional)
  - Standard cron expression (5 fields)
  - Format: `minute hour day_of_month month day_of_week`
  - Uses system timezone (Android device timezone)
  - Validated using `croniter` library
  - If omitted: schedule is manual-only (won't be executed by scheduler)
  - Manual schedules can still be run via `android-sync run <schedule_name>`

**Cron Examples:**

```
0 3 * * *      # Daily at 3:00 AM
0 */6 * * *    # Every 6 hours (0:00, 6:00, 12:00, 18:00)
30 2 * * 0     # Every Sunday at 2:30 AM
0 0 1 * *      # First day of month at midnight
0 9,15,21 * * * # Three times daily (9 AM, 3 PM, 9 PM)
```

## 4. State Management

### 4.1 State Directory

State files are stored in XDG-compliant location:

```
~/.local/share/android-sync/
├── state/
│   ├── daily.json
│   ├── weekly.json
│   └── photos_frequent.json
└── check-schedule.sh
```

### 4.2 State File Schema

**File name:** `<schedule_name>.json`

**Schema:**

```json
{
  "schedule": "daily",
  "last_run": "2026-01-19T03:00:00",
  "next_run": "2026-01-20T03:00:00",
  "status": "success",
  "started_at": "2026-01-19T03:00:00",
  "finished_at": "2026-01-19T03:15:00",
  "pid": 12345
}
```

**Field Definitions:**

- `schedule` (string): Schedule name from config
- `last_run` (ISO 8601 datetime, nullable): Last successful run completion time
- `next_run` (ISO 8601 datetime, nullable): Calculated next execution time based on cron; null for manual schedules
- `status` (enum): One of: `"pending"`, `"running"`, `"success"`, `"failed"`
- `started_at` (ISO 8601 datetime, nullable): When current/last run started
- `finished_at` (ISO 8601 datetime, nullable): When current/last run finished
- `pid` (integer, nullable): Process ID of running job (null if not running)

**Status Transitions:**

```
pending → running → success → pending (next schedule)
                 ↓
                failed → pending (on next schedule time)
                    ↓
                  stale (detected, reset to pending)
```

### 4.3 State Lifecycle

**Initial State (file doesn't exist):**
- Create on first check (for scheduled) or first run (for manual)
- Set `status = "pending"`
- If cron expression exists: calculate `next_run` from current time
- If manual schedule (no cron): set `next_run = null`
- All other fields null

**On Job Start:**
- Set `status = "running"`
- Set `started_at = now`
- Set `pid = os.getpid()`
- Clear `finished_at`

**On Job Success:**
- Set `status = "success"`
- Set `last_run = now`
- Set `finished_at = now`
- If cron expression exists: calculate `next_run` from `now` using cron
- If manual schedule: keep `next_run = null`
- Clear `pid`

**On Job Failure:**
- Set `status = "failed"`
- Set `finished_at = now`
- Keep `next_run` unchanged (will retry at next scheduled time for scheduled jobs)
- Clear `pid`

**On Stale Detection:**
- Kill process if PID exists
- Set `status = "failed"`
- Set `finished_at = now`
- Clear `pid`

**On Schedule Time (for failed scheduled jobs):**
- If `status == "failed"` and `now >= next_run` and `next_run != null`:
  - Reset to `status = "pending"`
  - This allows the job to be picked up on next check

**Manual Schedule Behavior:**
- Manual schedules (no cron) are never picked up by `check` command
- Always have `next_run = null`
- Can only be executed via `android-sync run <schedule_name>`
- State tracking works the same (status, last_run, etc.)

## 5. Components

### 5.1 Check Script

**Location:** `~/.local/share/android-sync/check-schedule.sh`

**Purpose:** Minimal wrapper script invoked by termux-job-scheduler

**Content:**
```bash
#!/data/data/com.termux/files/usr/bin/bash
exec android-sync check
```

**Characteristics:**
- Must be executable (`chmod +x`)
- Uses absolute shebang for Termux
- Delegates all logic to Python CLI

### 5.2 CLI Commands

#### 5.2.1 New: `android-sync check`

**Purpose:** Check for overdue schedules and spawn one background job if needed

**Algorithm:**
1. Load configuration
2. For each schedule in config:
   - Skip if schedule has no cron expression (manual-only)
   - Load or create state file
   - If `status == "running"`:
     - Check if stale (§5.3.1)
     - Skip if still running and not stale
   - If `status == "failed"`:
     - Check if `now >= next_run` (time to retry)
     - If yes, reset to `status = "pending"`
   - If `status == "pending"` or `status == "success"`:
     - Check if `now >= next_run` (overdue)
     - If overdue, calculate `overdue_minutes`
3. Select schedule with highest `overdue_minutes`
4. If found, spawn background job (§5.3.2)
5. Exit (entire process must complete quickly)

**Exit Codes:**
- 0: Success (no job needed or job spawned successfully)
- 1: Configuration error
- 2: State file error

**Output:**
- Silent unless error (suitable for cron-like invocation)

#### 5.2.2 New: `android-sync status`

**Purpose:** Display current status of all schedules

**Output Format:**
```
Schedule: daily
  Type: Scheduled (cron: 0 3 * * *)
  Status: success
  Last Run: 2026-01-19 03:00:00
  Next Run: 2026-01-20 03:00:00
  Overdue: No

Schedule: weekly
  Type: Scheduled (cron: 0 2 * * 0)
  Status: running (PID 12345)
  Started: 2026-01-19 02:00:00
  Next Run: 2026-01-26 02:00:00
  Overdue: No

Schedule: photos_frequent
  Type: Scheduled (cron: 0 */6 * * *)
  Status: failed
  Last Attempt: 2026-01-19 09:00:00
  Next Retry: 2026-01-19 12:00:00
  Overdue: Yes (30 minutes)

Schedule: manual_backup
  Type: Manual (no automatic scheduling)
  Status: success
  Last Run: 2026-01-18 14:30:00
  Next Run: N/A
```

**Features:**
- Color-coded status (green=success, yellow=running, red=failed)
- Human-readable timestamps
- Indicates if schedule is currently overdue
- Shows PID for running jobs

#### 5.2.3 New: `android-sync reset <schedule>`

**Purpose:** Manually reset a schedule's state (useful for failed/stale jobs)

**Arguments:**
- `<schedule>`: Name of schedule to reset

**Behavior:**
- Load state file
- Reset to `status = "pending"`
- Clear `pid`, `started_at`, `finished_at`
- Calculate new `next_run` from current time
- Save state

**Exit Codes:**
- 0: Success
- 1: Schedule not found
- 2: State file error

#### 5.2.4 Modified: `android-sync run <schedule>`

**New Behavior:**

**Before execution (if not dry-run):**
- Call `update_state_on_start(schedule_name)`
- This sets status, PID, started_at

**After execution (if not dry-run):**
- Call `update_state_on_finish(schedule_name, success=True/False)`
- This updates status, calculates next_run, clears PID

**Compatibility:**
- Can still be run manually (not just from scheduler)
- State updates happen regardless of invocation method
- Dry-run mode (--dry-run flag) does NOT update state

#### 5.2.5 Modified: `android-sync setup`

**New Steps:**

After credential setup:
1. Create state directory: `~/.local/share/android-sync/state/`
2. Write check script: `~/.local/share/android-sync/check-schedule.sh`
3. Make script executable: `chmod +x check-schedule.sh`
4. Register with termux-job-scheduler:
   ```bash
   termux-job-scheduler schedule \
     --script ~/.local/share/android-sync/check-schedule.sh \
     --job-id 1 \
     --period-ms 900000 \
     --persisted true \
     --network any \
     --battery-not-low
   ```
5. Verify registration successful

**Constraints:**
- Network and battery constraints are always enabled (hardcoded)
- `--network any`: Job only runs when device has network connectivity
- `--battery-not-low`: Job only runs when battery is not in low state (~15%+)

**Error Handling:**
- Check if `termux-job-scheduler` is available
- If registration fails, provide troubleshooting instructions

### 5.3 Scheduler Module Functions

#### 5.3.1 Stale Job Detection

```python
def check_stale_job(state: ScheduleState, timeout_hours: int) -> bool:
    """Check if a job is stale and handle it.

    Returns:
        True if job was stale (reset state)
        False if job is still running normally
    """
```

**Algorithm:**
1. If `status != "running"`, return False
2. Check if PID exists: `psutil.pid_exists(state.pid)`
3. If PID exists:
   - Calculate runtime: `now - started_at`
   - If runtime > timeout_hours:
     - Kill process: `os.kill(pid, signal.SIGTERM)`
     - Mark as stale: return True
   - Else: return False (still running, not stale)
4. If PID doesn't exist (crashed/killed externally):
   - Mark as stale: return True

#### 5.3.2 Background Job Spawning

```python
def spawn_background_job(schedule_name: str, config_path: Path) -> None:
    """Spawn detached background process for schedule execution."""
```

**Implementation:**
```python
subprocess.Popen(
    ['android-sync', '--config', str(config_path), 'run', schedule_name],
    start_new_session=True,  # Detach from parent
    stdout=log_file,
    stderr=subprocess.STDOUT,
    cwd=Path.home()
)
```

**Characteristics:**
- Fully detached (survives parent process exit)
- Logs to appropriate file
- No stdin/stdout/stderr connection to parent

#### 5.3.3 Next Run Calculation

```python
def calculate_next_run(cron_expr: str, from_time: datetime) -> datetime:
    """Calculate next run time from a given time using cron expression."""
```

**Implementation:**
```python
from croniter import croniter

cron = croniter(cron_expr, from_time)
return cron.get_next(datetime)
```

#### 5.3.4 Overdue Schedule Detection

```python
def get_overdue_schedules(config: Config) -> list[tuple[str, float]]:
    """Get list of overdue schedules with their overdue minutes.

    Returns:
        List of (schedule_name, overdue_minutes) tuples, sorted by urgency
    """
```

**Algorithm:**
1. Load all states
2. Filter for overdue: `now >= next_run`
3. Calculate: `overdue_minutes = (now - next_run).total_seconds() / 60`
4. Sort by overdue_minutes descending
5. Return list

## 6. Dependencies

### 6.1 New Dependencies

**croniter** (>=2.0.0)
- Purpose: Parse and evaluate cron expressions
- License: MIT
- Well-maintained, standard library for cron parsing

**psutil** (>=5.9.0)
- Purpose: Check if PID exists (stale job detection)
- License: BSD-3-Clause
- Cross-platform, reliable process utilities

### 6.2 External Tools

**termux-job-scheduler**
- Provided by: termux-api package
- Purpose: Register persistent jobs with Android JobScheduler
- Installation: `pkg install termux-api`

## 7. Error Handling

### 7.1 Invalid Cron Expression

**Scenario:** Malformed cron syntax

**Handling:**
- Validation error during config load
- Use `croniter.is_valid()` for validation
- Clear error message: "Schedule '{name}' has invalid cron expression: '{cron}'"
- Exit with code 1
- Note: Missing cron expression is valid (manual schedule)

### 7.2 State File Corruption

**Scenario:** JSON parse error or missing required fields

**Handling:**
- Log warning
- Recreate state file with defaults
- Continue execution

### 7.3 Job Scheduler Registration Failure

**Scenario:** `termux-job-scheduler` command fails

**Handling:**
- Check if termux-api is installed
- Provide installation instructions
- Exit setup with code 1

### 7.4 Concurrent Execution Prevention

**Scenario:** Check script runs while previous check is still processing

**Handling:**
- Use file lock during check phase
- Lock file: `~/.local/share/android-sync/check.lock`
- If locked, exit silently (next check in 15 min)

### 7.5 Constraint Not Met (Network/Battery)

**Scenario:** Schedule is overdue but network is unavailable or battery is low

**Handling:**
- JobScheduler prevents check script from running at all
- No error logged (this is expected behavior)
- Job remains in overdue state
- When constraints are satisfied, next periodic check (within 15 minutes) will pick up the overdue job

**User Visibility:**
- `android-sync status` will show schedule as overdue
- No indication of constraint waiting (Android JobScheduler limitation)
- User can manually run schedule if needed: `android-sync run <schedule_name>`

## 8. Testing Strategy

### 8.1 Unit Tests

**scheduler.py:**
- State file load/save
- Cron parsing and next run calculation
- Stale job detection logic
- Overdue schedule prioritization

**config.py:**
- Validation of cron expressions
- Configuration loading with scheduling fields

### 8.2 Integration Tests

- Full check → spawn → execute → update state cycle
- Multiple schedules with different priorities
- Failed job retry behavior
- Stale job detection and cleanup

### 8.3 Manual Testing

- Device reboot (persisted flag verification)
- Long-running jobs (stale detection)
- Network failure during sync (failure handling)
- Multiple overdue schedules (priority selection)
- Network constraint: disable WiFi/cellular, verify check doesn't run, re-enable and verify execution
- Battery constraint: test with battery below ~15%, verify check doesn't run, charge and verify execution

## 9. Security Considerations

### 9.1 Command Injection

**Risk:** Malicious cron expression or schedule name

**Mitigation:**
- Cron expressions validated by croniter
- Schedule names validated against config keys
- No shell interpolation in subprocess calls

### 9.2 State File Tampering

**Risk:** Manual modification of state files

**Mitigation:**
- State files stored in user directory (standard permissions)
- Robust JSON parsing with error recovery
- State validation on load (discard invalid data)

### 9.3 PID Hijacking

**Risk:** Killing unrelated process with reused PID

**Mitigation:**
- Check process start time if psutil supports it
- Only kill if process has been running beyond timeout
- Use SIGTERM (graceful) not SIGKILL

## 10. Future Enhancements (Out of Scope)

- Detailed execution history (beyond last run)
- Dynamic schedule adjustment (e.g., delay non-critical syncs to off-peak hours)
  - Note: Basic battery/network constraints are hardcoded (see §3.1, §7.5)
  - Future: Could add smart scheduling based on usage patterns, time-of-day preferences
- Configurable constraint options (currently hardcoded):
  - Per-schedule constraint overrides
  - Disable constraints for specific schedules
  - Unmetered network requirement (WiFi-only) option
- Multiple timezone support
- Schedule templates

## 11. References

- [croniter documentation](https://github.com/pallets-eco/croniter)
- [Android JobScheduler](https://developer.android.com/reference/android/app/job/JobScheduler)
- [termux-job-scheduler](https://wiki.termux.com/wiki/Termux:API#termux-job-scheduler)
- [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html)
