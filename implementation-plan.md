# Scheduling Feature Implementation Plan

**Reference Specification:** `scheduling.md`

## Phase 1: Dependencies and Project Setup ✅

- [x] **Add croniter dependency**
  - Update `pyproject.toml` dependencies array
  - Add: `"croniter>=2.0.0"`
  - Reference: [Spec §6.1](scheduling.md#61-new-dependencies)
  - **STATUS:** VERIFIED in pyproject.toml line 8

- [x] **Add psutil dependency**
  - Update `pyproject.toml` dependencies array
  - Add: `"psutil>=5.9.0"`
  - Reference: [Spec §6.1](scheduling.md#61-new-dependencies)
  - **STATUS:** VERIFIED in pyproject.toml line 9

- [x] **Run dependency installation**
  - Execute: `uv sync`
  - Verify both packages installed correctly
  - **STATUS:** COMPLETE (both packages imported successfully in code)

## Phase 2: Configuration Schema Updates ✅

- [x] **Update Config dataclass in `src/android_sync/config.py`**
  - Add `stale_job_timeout_hours: int = 24` to `Config` dataclass
  - Reference: [Spec §3.1](scheduling.md#31-general-section)
  - **STATUS:** VERIFIED at config.py:43

- [x] **Update Schedule dataclass in `src/android_sync/config.py`**
  - Add `cron: str | None = None` field to `Schedule` dataclass (optional field)
  - If cron is None, schedule is manual-only
  - Reference: [Spec §3.2](scheduling.md#32-schedule-section)
  - **STATUS:** VERIFIED at config.py:29

- [x] **Add cron expression validation in `src/android_sync/config.py`**
  - Import `croniter` and `croniter.CroniterBadCronError`
  - In `load_config()` function, after parsing schedules:
    - For each schedule with a cron expression (skip if None), validate using `croniter.is_valid()`
    - Raise clear error if invalid
  - Reference: [Spec §7.1](scheduling.md#71-invalid-cron-expression)
  - **STATUS:** VERIFIED at config.py:101-107

- [x] **Update example config file**
  - Add `stale_job_timeout_hours = 24` to general section in `config.example.toml`
  - Add `cron = "0 3 * * *"` to some schedule examples
  - Add at least one example without cron (manual schedule)
  - Add comment explaining cron syntax and manual schedules
  - Reference: [Spec §3](scheduling.md#3-configuration-schema)
  - **STATUS:** VERIFIED at config.example.toml:21-23, 51-72

## Phase 3: Scheduler Module Implementation ✅

- [x] **Create new module: `src/android_sync/scheduler.py`**
  - Reference: [Spec §5.3](scheduling.md#53-scheduler-module-functions)
  - **STATUS:** VERIFIED - module exists with 313 lines

- [x] **Implement ScheduleState dataclass**
  - Fields: schedule, last_run, next_run, status, started_at, finished_at, pid
  - Use `datetime | None` for nullable datetime fields
  - `next_run` is None for manual schedules (no cron expression)
  - Use `Literal["pending", "running", "success", "failed"]` for status
  - Reference: [Spec §4.2](scheduling.md#42-state-file-schema)
  - **STATUS:** VERIFIED at scheduler.py:21-32

- [x] **Implement get_state_directory() helper**
  - Returns `Path.home() / ".local" / "share" / "android-sync" / "state"`
  - Reference: [Spec §4.1](scheduling.md#41-state-directory)
  - **STATUS:** VERIFIED at scheduler.py:34-38

- [x] **Implement load_state() function**
  - Signature: `load_state(schedule_name: str, cron_expr: str | None) -> ScheduleState`
  - If file doesn't exist, create initial state
  - If cron_expr is not None: calculate next_run; else: next_run = None
  - Parse JSON, handle corruption by recreating state
  - Convert ISO datetime strings to datetime objects
  - Reference: [Spec §4.3 Initial State](scheduling.md#43-state-lifecycle)
  - Error handling: [Spec §7.2](scheduling.md#72-state-file-corruption)
  - **STATUS:** VERIFIED at scheduler.py:46-116

- [x] **Implement save_state() function**
  - Signature: `save_state(state: ScheduleState) -> None`
  - Convert datetime objects to ISO strings
  - Write to JSON with pretty formatting
  - Ensure directory exists
  - Reference: [Spec §4.2](scheduling.md#42-state-file-schema)
  - **STATUS:** VERIFIED at scheduler.py:119-136

- [x] **Implement calculate_next_run() function**
  - Signature: `calculate_next_run(cron_expr: str, from_time: datetime) -> datetime`
  - Use croniter to get next execution time
  - Reference: [Spec §5.3.3](scheduling.md#533-next-run-calculation)
  - **STATUS:** VERIFIED at scheduler.py:138-149

- [x] **Implement check_stale_job() function**
  - Signature: `check_stale_job(state: ScheduleState, timeout_hours: int) -> bool`
  - Check if status is "running"
  - Use `psutil.pid_exists()` to verify PID
  - Calculate runtime, compare to timeout
  - Kill with SIGTERM if stale
  - Return True if stale, False otherwise
  - Reference: [Spec §5.3.1](scheduling.md#531-stale-job-detection)
  - **STATUS:** VERIFIED at scheduler.py:152-194

- [x] **Implement get_overdue_schedules() function**
  - Signature: `get_overdue_schedules(config: Config) -> list[tuple[str, float]]`
  - Load all states for schedules in config
  - Skip schedules without cron expressions (manual-only)
  - Handle stale jobs (call check_stale_job)
  - Reset failed jobs if past next_run time
  - Calculate overdue_minutes for each
  - Sort by overdue_minutes descending
  - Reference: [Spec §5.3.4](scheduling.md#534-overdue-schedule-detection)
  - **STATUS:** VERIFIED at scheduler.py:196-242

- [x] **Implement spawn_background_job() function**
  - Signature: `spawn_background_job(schedule_name: str, config_path: Path) -> None`
  - Use `subprocess.Popen` with `start_new_session=True`
  - Redirect stdout/stderr to log file
  - Pass config path as argument
  - Reference: [Spec §5.3.2](scheduling.md#532-background-job-spawning)
  - **STATUS:** VERIFIED at scheduler.py:245-264

- [x] **Implement update_state_on_start() function**
  - Signature: `update_state_on_start(schedule_name: str, config: Config) -> None`
  - Load current state, set status = "running", started_at = now, pid = os.getpid()
  - Clear finished_at, save state
  - Reference: [Spec §4.3 On Job Start](scheduling.md#43-state-lifecycle)
  - **STATUS:** VERIFIED at scheduler.py:266-283

- [x] **Implement update_state_on_finish() function**
  - Signature: `update_state_on_finish(schedule_name: str, config: Config, success: bool) -> None`
  - Load current state and schedule config (to get cron expression)
  - Set finished_at = now
  - If success: set status = "success", last_run = now, calculate next_run if cron exists
  - Else: set status = "failed", keep next_run unchanged (retry at next scheduled time)
  - Clear pid, save state
  - Reference: [Spec §4.3 On Job Success/Failure](scheduling.md#43-state-lifecycle)
  - **STATUS:** VERIFIED at scheduler.py:285-313

## Phase 4: CLI Command Implementations ✅

- [x] **Add 'check' subcommand to `src/android_sync/cli.py`**
  - Add parser: `subparsers.add_parser('check', ...)`
  - Implement handler function
  - Load config, call `get_overdue_schedules()`
  - If overdue schedules exist, spawn the most overdue one
  - Exit silently (no output unless error)
  - Reference: [Spec §5.2.1](scheduling.md#521-new-android-sync-check)
  - **STATUS:** VERIFIED at cli.py:111, 347-360

- [x] **Add 'status' subcommand to `src/android_sync/cli.py`**
  - Add parser: `subparsers.add_parser('status', ...)`
  - Implement handler function with formatted output and colors
  - Show type (Scheduled with cron vs Manual), overdue status, "N/A" for next run on manual schedules
  - Reference: [Spec §5.2.2](scheduling.md#522-new-android-sync-status)
  - **STATUS:** VERIFIED at cli.py:114, 363-416

- [x] **Add 'reset' subcommand to `src/android_sync/cli.py`**
  - Add parser with positional argument for schedule name
  - Verify schedule exists in config
  - Load state, reset fields, calculate new next_run if cron exists, save state
  - Reference: [Spec §5.2.3](scheduling.md#523-new-android-sync-reset-schedule)
  - **STATUS:** VERIFIED at cli.py:117-121, 419-448

- [x] **Modify 'run' command in `src/android_sync/cli.py`**
  - Add call to `update_state_on_start()` before sync execution
  - Wrap execution in try/finally
  - Add call to `update_state_on_finish()` after sync (success=True/False based on exception)
  - Reference: [Spec §5.2.4](scheduling.md#524-modified-android-sync-run-schedule)
  - **STATUS:** VERIFIED at cli.py:308-309, 340-344

- [x] **Modify 'setup' command in `src/android_sync/cli.py`**
  - Add state directory creation
  - Create check script file
  - Make script executable (use `os.chmod`)
  - Register with termux-job-scheduler (with --job-id flag)
  - Verify registration successful
  - Add error handling for missing termux-api
  - Reference: [Spec §5.2.5](scheduling.md#525-modified-android-sync-setup)
  - **STATUS:** VERIFIED at cli.py:207-252

## Phase 5: Check Script Creation ✅

- [x] **Create check script template in code**
  - Script content defined in `src/android_sync/cli.py` setup function
  - Shebang: `#!/data/data/com.termux/files/usr/bin/bash`
  - Body: `exec android-sync check`
  - Reference: [Spec §5.1](scheduling.md#51-check-script)
  - **STATUS:** VERIFIED at cli.py:216-219

- [x] **Implement script installation in setup command**
  - Write to `~/.local/share/android-sync/check-schedule.sh`
  - Make executable: `os.chmod(path, 0o755)`
  - **STATUS:** VERIFIED at cli.py:216-222

## Phase 6: Testing ✅

- [x] **Create test file: `tests/test_scheduler.py`**
  - Reference: [Spec §8.1](scheduling.md#81-unit-tests)
  - **STATUS:** VERIFIED - 887 lines, 70 test methods

- [x] **Test ScheduleState serialization**
  - Test JSON round-trip (save → load)
  - Test datetime conversion
  - Test null handling
  - **STATUS:** VERIFIED - 5 tests in TestScheduleState class

- [x] **Test calculate_next_run()**
  - Test various cron expressions
  - Verify next run is in the future
  - Test edge cases (end of month, etc.)
  - **STATUS:** VERIFIED - 4 tests in TestCalculateNextRun class

- [x] **Test check_stale_job()**
  - Mock `psutil.pid_exists()`
  - Test timeout detection
  - Test PID cleanup
  - Verify SIGTERM sent to stale jobs
  - **STATUS:** VERIFIED - 6 tests in TestCheckStaleJob class

- [x] **Test get_overdue_schedules()**
  - Mock state files
  - Test priority sorting (most overdue first)
  - Test failed job reset logic
  - Test stale job handling integration
  - Test skipping manual schedules (no cron)
  - **STATUS:** VERIFIED - 6 tests in TestGetOverdueSchedules class

- [x] **Test configuration validation**
  - Test invalid cron expressions (should raise error)
  - Test missing cron field (should be valid - manual schedule)
  - Test mix of scheduled and manual schedules
  - Verify error messages
  - **STATUS:** VERIFIED - 6 tests in TestSchedulingConfiguration class (test_config.py)

- [x] **Test manual schedule behavior**
  - Create schedule without cron expression
  - Verify it's not picked up by check command
  - Verify it can be run manually
  - Verify state updates correctly (next_run stays None)
  - **STATUS:** VERIFIED - tests in test_scheduler.py cover this comprehensively

- [x] **Create integration test: full check cycle**
  - Mock config with multiple schedules
  - Create state files
  - Run check command
  - Verify correct schedule selected
  - Verify background job spawned
  - **STATUS:** VERIFIED - TestIntegrationCheckCycle class with 7 comprehensive integration tests

- [x] **Update existing tests if needed**
  - Run full test suite: `pytest tests/`
  - Fix any broken tests due to config schema changes
  - **STATUS:** VERIFIED - All 88 tests passing (100% pass rate)

## Phase 7: Documentation ✅

- [x] **Update README.md**
  - Add scheduling section
  - Explain cron syntax and examples
  - Explain difference between scheduled (with cron) and manual (without cron) schedules
  - Document new CLI commands (check, status, reset)
  - Document retry behavior (no immediate retry, waits for next cron time)
  - Add troubleshooting section
  - **STATUS:** VERIFIED - Comprehensive "Automatic Scheduling" section at README.md:181-321

- [x] **Update config.example.toml**
  - Add detailed comments explaining scheduling
  - **STATUS:** VERIFIED - Extensive comments and examples at config.example.toml:21-23, 50-72

- [x] **Create troubleshooting guide**
  - Common issues: missing termux-api, failed registration
  - How to check job scheduler status
  - How to reset failed jobs
  - **STATUS:** VERIFIED - Troubleshooting section at README.md:298-320

## Phase 8: Manual Testing and Validation

- [x] **Test on actual Android device**
  - Run setup command
  - Verify check script created
  - Verify job scheduler registered
  - Reference: [Spec §8.3](scheduling.md#83-manual-testing)
  - **STATUS:** Done manually

- [x] **Test check command manually**
  - Create test states (pending, overdue, running)
  - Run `android-sync check`
  - Verify correct behavior
  - **STATUS:** Done manually

- [x] **Test device reboot persistence**
  - Reboot device
  - Verify job scheduler still registered
  - Wait for next 15-min trigger
  - Verify check runs
  - **STATUS:** Done manually

- [ ] **Test stale job handling**
  - Start a long-running job
  - Wait for timeout
  - Verify it's killed and marked failed
  - **STATUS:** PENDING - requires actual Android device (automated test exists)

- [x] **Test status command**
  - Run with various state configurations
  - Verify output formatting
  - Check color coding
  - **STATUS:** Done manually

- [x] **Test reset command**
  - Reset a failed schedule
  - Verify state cleared correctly
  - **STATUS:** Done manually

- [ ] **Test priority selection**
  - Create multiple overdue schedules
  - Verify most overdue is selected first
  - **STATUS:** PENDING - requires actual Android device (automated test exists)

- [ ] **Test retry/backoff behavior**
  - Force a job to fail (disconnect network, etc.)
  - Verify status set to "failed"
  - Verify next_run unchanged
  - Wait until next scheduled time
  - Verify job retries (status reset to pending)
  - Reference: [Spec §2.3](scheduling.md#23-failure-handling-and-retry-strategy)
  - **STATUS:** PENDING - requires actual Android device (automated test exists)

- [ ] **Test manual schedule**
  - Create schedule without cron expression
  - Run check command, verify it's not picked up
  - Run manually: `android-sync run manual_schedule`
  - Verify execution works and state updates
  - Verify next_run stays None
  - **STATUS:** PENDING - requires actual Android device (automated test exists)

## Phase 9: Final Integration

- [ ] **Update version number**
  - Increment to 0.2.0 in `src/android_sync/__init__.py`
  - Update `pyproject.toml` version
  - **STATUS:** PENDING - currently at 0.1.0

- [x] **Run full test suite**
  - `pytest tests/ -v`
  - Ensure all tests pass
  - **STATUS:** COMPLETE - All 88 tests passing

- [x] **Run linter**
  - `ruff check src/ tests/`
  - Fix any linting issues
  - **STATUS:** COMPLETE - No linting issues found

- [ ] **Test complete workflow end-to-end**
  - Fresh install
  - Run setup
  - Configure mix of scheduled (with cron) and manual (without cron) schedules
  - Wait for scheduled execution
  - Verify automatic schedules run, manual schedules don't
  - Test manual execution of manual schedule
  - Verify success
  - **STATUS:** PENDING - requires actual Android device

---

## Gap Analysis: Missing Features for Unattended Operation

### 1. Concurrent Execution Prevention (File Locking) ✅

**Status:** IMPLEMENTED
**Spec Reference:** §7.4

File locking has been implemented to prevent concurrent execution of the check command:
- Uses `fcntl` module for Unix-based file locking
- Lock file: `~/.local/share/android-sync/check.lock`
- If locked, exits silently (next check in 15 min)
- Non-blocking lock acquisition (LOCK_EX | LOCK_NB)
- Proper cleanup in finally block

**Implementation:**
- Modified `cmd_check()` in `src/android_sync/cli.py` at line 349-378
- Added comprehensive test: `test_concurrent_execution_prevention` in `tests/test_scheduler.py`
- Test count updated: 89 tests (all passing)

**Priority:** HIGH - Correctness issue resolved

## Key Files Modified

- `pyproject.toml` - Dependencies ✅
- `src/android_sync/config.py` - Schema updates and validation ✅
- `src/android_sync/scheduler.py` - New module (313 lines) ✅
- `src/android_sync/cli.py` - New commands and modifications ✅
- `config.example.toml` - Example updates ✅
- `tests/test_scheduler.py` - New test file (887 lines, 70 tests) ✅
- `tests/test_config.py` - Extended tests ✅
- `README.md` - Documentation updates ✅
