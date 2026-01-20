# Scheduling Feature Implementation Plan

**Reference Specification:** `docs/scheduling-spec.md`

## Phase 1: Dependencies and Project Setup

- [x] **Add croniter dependency**
  - Update `pyproject.toml` dependencies array
  - Add: `"croniter>=2.0.0"`
  - Reference: [Spec §6.1](scheduling-spec.md#61-new-dependencies)

- [x] **Add psutil dependency**
  - Update `pyproject.toml` dependencies array
  - Add: `"psutil>=5.9.0"`
  - Reference: [Spec §6.1](scheduling-spec.md#61-new-dependencies)

- [x] **Run dependency installation**
  - Execute: `uv sync`
  - Verify both packages installed correctly

## Phase 2: Configuration Schema Updates

- [x] **Update Config dataclass in `src/android_sync/config.py`**
  - Add `stale_job_timeout_hours: int = 24` to `Config` dataclass
  - Reference: [Spec §3.1](scheduling-spec.md#31-general-section)
  - Source: `src/android_sync/config.py` lines 10-20

- [x] **Update Schedule dataclass in `src/android_sync/config.py`**
  - Add `cron: str | None = None` field to `Schedule` dataclass (optional field)
  - If cron is None, schedule is manual-only
  - Reference: [Spec §3.2](scheduling-spec.md#32-schedule-section)
  - Source: `src/android_sync/config.py` lines 22-30

- [x] **Add cron expression validation in `src/android_sync/config.py`**
  - Import `croniter` and `croniter.CroniterBadCronError`
  - In `load_config()` function, after parsing schedules:
    - For each schedule with a cron expression (skip if None), validate using `croniter.is_valid()`
    - Raise clear error if invalid
  - Reference: [Spec §7.1](scheduling-spec.md#71-invalid-cron-expression)
  - Source: `src/android_sync/config.py` lines 70-90 (existing validation section)

- [x] **Update example config file**
  - Add `stale_job_timeout_hours = 24` to general section in `config.example.toml`
  - Add `cron = "0 3 * * *"` to some schedule examples
  - Add at least one example without cron (manual schedule)
  - Add comment explaining cron syntax and manual schedules
  - Reference: [Spec §3](scheduling-spec.md#3-configuration-schema)
  - Source: `config.example.toml`

## Phase 3: Scheduler Module Implementation

- [x] **Create new module: `src/android_sync/scheduler.py`**
  - Reference: [Spec §5.3](scheduling-spec.md#53-scheduler-module-functions)

- [x] **Implement ScheduleState dataclass**
  - Fields: schedule, last_run, next_run, status, started_at, finished_at, pid
  - Use `datetime | None` for nullable datetime fields (last_run, next_run, started_at, finished_at)
  - `next_run` is None for manual schedules (no cron expression)
  - Use `Literal["pending", "running", "success", "failed"]` for status
  - Reference: [Spec §4.2](scheduling-spec.md#42-state-file-schema)

- [x] **Implement get_state_directory() helper**
  - Returns `Path.home() / ".local" / "share" / "android-sync" / "state"`
  - Reference: [Spec §4.1](scheduling-spec.md#41-state-directory)

- [x] **Implement load_state() function**
  - Signature: `load_state(schedule_name: str, cron_expr: str | None) -> ScheduleState`
  - If file doesn't exist, create initial state
  - If cron_expr is not None: calculate next_run; else: next_run = None
  - Parse JSON, handle corruption by recreating state
  - Convert ISO datetime strings to datetime objects
  - Reference: [Spec §4.3 Initial State](scheduling-spec.md#43-state-lifecycle)
  - Error handling: [Spec §7.2](scheduling-spec.md#72-state-file-corruption)

- [x] **Implement save_state() function**
  - Signature: `save_state(state: ScheduleState) -> None`
  - Convert datetime objects to ISO strings
  - Write to JSON with pretty formatting
  - Ensure directory exists
  - Reference: [Spec §4.2](scheduling-spec.md#42-state-file-schema)

- [x] **Implement calculate_next_run() function**
  - Signature: `calculate_next_run(cron_expr: str, from_time: datetime) -> datetime`
  - Use croniter to get next execution time
  - Reference: [Spec §5.3.3](scheduling-spec.md#533-next-run-calculation)

- [x] **Implement check_stale_job() function**
  - Signature: `check_stale_job(state: ScheduleState, timeout_hours: int) -> bool`
  - Check if status is "running"
  - Use `psutil.pid_exists()` to verify PID
  - Calculate runtime, compare to timeout
  - Kill with SIGTERM if stale
  - Return True if stale, False otherwise
  - Reference: [Spec §5.3.1](scheduling-spec.md#531-stale-job-detection)

- [x] **Implement get_overdue_schedules() function**
  - Signature: `get_overdue_schedules(config: Config) -> list[tuple[str, float]]`
  - Load all states for schedules in config
  - Skip schedules without cron expressions (manual-only)
  - Handle stale jobs (call check_stale_job)
  - Reset failed jobs if past next_run time
  - Calculate overdue_minutes for each (only for schedules with next_run not None)
  - Sort by overdue_minutes descending
  - Reference: [Spec §5.3.4](scheduling-spec.md#534-overdue-schedule-detection)
  - Priority model: [Spec §2.2](scheduling-spec.md#22-priority-model)
  - Retry strategy: [Spec §2.3](scheduling-spec.md#23-failure-handling-and-retry-strategy)

- [x] **Implement spawn_background_job() function**
  - Signature: `spawn_background_job(schedule_name: str, config_path: Path) -> None`
  - Use `subprocess.Popen` with `start_new_session=True`
  - Redirect stdout/stderr to log file
  - Pass config path as argument
  - Reference: [Spec §5.3.2](scheduling-spec.md#532-background-job-spawning)

- [x] **Implement update_state_on_start() function**
  - Signature: `update_state_on_start(schedule_name: str, config: Config) -> None`
  - Load current state
  - Set status = "running"
  - Set started_at = now
  - Set pid = os.getpid()
  - Clear finished_at
  - Save state
  - Reference: [Spec §4.3 On Job Start](scheduling-spec.md#43-state-lifecycle)

- [x] **Implement update_state_on_finish() function**
  - Signature: `update_state_on_finish(schedule_name: str, config: Config, success: bool) -> None`
  - Load current state and schedule config (to get cron expression)
  - Set finished_at = now
  - If success:
    - Set status = "success"
    - Set last_run = now
    - If cron expression exists: calculate next_run using cron; else: keep next_run = None
  - Else:
    - Set status = "failed"
    - Keep next_run unchanged (retry at next scheduled time)
  - Clear pid
  - Save state
  - Reference: [Spec §4.3 On Job Success/Failure](scheduling-spec.md#43-state-lifecycle)
  - Retry strategy: [Spec §2.3](scheduling-spec.md#23-failure-handling-and-retry-strategy)

## Phase 4: CLI Command Implementations

- [x] **Add 'check' subcommand to `src/android_sync/cli.py`**
  - Add parser: `subparsers.add_parser('check', ...)`
  - Implement handler function
  - Load config
  - Call `get_overdue_schedules()`
  - If overdue schedules exist, spawn the most overdue one
  - Exit silently (no output unless error)
  - Reference: [Spec §5.2.1](scheduling-spec.md#521-new-android-sync-check)
  - Source: `src/android_sync/cli.py` line ~90 (where subparsers are defined)

- [x] **Add 'status' subcommand to `src/android_sync/cli.py`**
  - Add parser: `subparsers.add_parser('status', ...)`
  - Implement handler function
  - Load config
  - For each schedule, load state
  - Display formatted output with colors
  - Show type (Scheduled with cron vs Manual)
  - Show overdue status (only for scheduled)
  - Show "N/A" for next run on manual schedules
  - Reference: [Spec §5.2.2](scheduling-spec.md#522-new-android-sync-status)
  - Source: `src/android_sync/cli.py`

- [x] **Add 'reset' subcommand to `src/android_sync/cli.py`**
  - Add parser: `subparsers.add_parser('reset', ...)`
  - Add positional argument: schedule name
  - Implement handler function
  - Verify schedule exists in config
  - Load state, reset fields (status, pid, started_at, finished_at)
  - Calculate new next_run if cron exists; else next_run = None
  - Save state
  - Reference: [Spec §5.2.3](scheduling-spec.md#523-new-android-sync-reset-schedule)
  - Source: `src/android_sync/cli.py`

- [x] **Modify 'run' command in `src/android_sync/cli.py`**
  - Add call to `update_state_on_start()` before sync execution
  - Wrap execution in try/except
  - Add call to `update_state_on_finish()` after sync
    - Pass success=True if no exception
    - Pass success=False if exception caught
  - Reference: [Spec §5.2.4](scheduling-spec.md#524-modified-android-sync-run-schedule)
  - Source: `src/android_sync/cli.py` lines ~150-200 (run command implementation)

- [x] **Modify 'setup' command in `src/android_sync/cli.py`**
  - Add state directory creation
  - Create check script file
  - Make script executable (use `os.chmod`)
  - Register with termux-job-scheduler
  - Verify registration successful
  - Add error handling for missing termux-api
  - Reference: [Spec §5.2.5](scheduling-spec.md#525-modified-android-sync-setup)
  - Source: `src/android_sync/cli.py` lines ~100-140 (setup command implementation)

## Phase 5: Check Script Creation

- [x] **Create check script template in code**
  - Script content defined in `src/android_sync/cli.py` setup function
  - Shebang: `#!/data/data/com.termux/files/usr/bin/bash`
  - Body: `exec android-sync check`
  - Reference: [Spec §5.1](scheduling-spec.md#51-check-script)

- [x] **Implement script installation in setup command**
  - Write to `~/.local/share/android-sync/check-schedule.sh`
  - Make executable: `os.chmod(path, 0o755)`
  - Already covered in Phase 4, but verify script content matches spec

## Phase 6: Testing

- [x] **Create test file: `tests/test_scheduler.py`**
  - Reference: [Spec §8.1](scheduling-spec.md#81-unit-tests)

- [x] **Test ScheduleState serialization**
  - Test JSON round-trip (save → load)
  - Test datetime conversion
  - Test null handling

- [x] **Test calculate_next_run()**
  - Test various cron expressions
  - Verify next run is in the future
  - Test edge cases (end of month, etc.)

- [x] **Test check_stale_job()**
  - Mock `psutil.pid_exists()`
  - Test timeout detection
  - Test PID cleanup
  - Verify SIGTERM sent to stale jobs

- [x] **Test get_overdue_schedules()**
  - Mock state files
  - Test priority sorting (most overdue first)
  - Test failed job reset logic (reset to pending when next_run time arrives)
  - Test stale job handling integration
  - Test skipping manual schedules (no cron)
  - Reference: [Spec §2.3](scheduling-spec.md#23-failure-handling-and-retry-strategy)

- [x] **Test configuration validation**
  - Test invalid cron expressions (should raise error)
  - Test missing cron field (should be valid - manual schedule)
  - Test mix of scheduled and manual schedules
  - Verify error messages
  - Source: `tests/test_config.py` (extend existing tests)

- [x] **Test manual schedule behavior**
  - Create schedule without cron expression
  - Verify it's not picked up by check command
  - Verify it can be run manually
  - Verify state updates correctly (next_run stays None)

- [x] **Create integration test: full check cycle**
  - Mock config with multiple schedules
  - Create state files
  - Run check command
  - Verify correct schedule selected
  - Verify background job spawned

- [x] **Update existing tests if needed**
  - Run full test suite: `pytest tests/`
  - Fix any broken tests due to config schema changes
  - Source: `tests/test_config.py`, `tests/test_sync.py`

## Phase 7: Documentation

- [x] **Update README.md**
  - Add scheduling section
  - Explain cron syntax and examples
  - Explain difference between scheduled (with cron) and manual (without cron) schedules
  - Document new CLI commands (check, status, reset)
  - Document retry behavior (no immediate retry, waits for next cron time)
  - Add troubleshooting section
  - Reference: [Spec §3.2](scheduling-spec.md#32-schedule-section) for examples
  - Reference: [Spec §2.3](scheduling-spec.md#23-failure-handling-and-retry-strategy) for retry behavior
  - Source: `README.md`

- [x] **Update config.example.toml**
  - Already covered in Phase 2, but verify completeness
  - Add detailed comments explaining scheduling
  - Source: `config.example.toml`

- [x] **Create troubleshooting guide**
  - Common issues: missing termux-api, failed registration
  - How to check job scheduler status
  - How to reset failed jobs
  - Can be section in README or separate doc

## Phase 8: Manual Testing and Validation

- [x] **Test on actual Android device**
  - Run setup command
  - Verify check script created
  - Verify job scheduler registered
  - Reference: [Spec §8.3](scheduling-spec.md#83-manual-testing)

- [x] **Test check command manually**
  - Create test states (pending, overdue, running)
  - Run `android-sync check`
  - Verify correct behavior

- [ ] **Test device reboot persistence**
  - Reboot device
  - Verify job scheduler still registered
  - Wait for next 15-min trigger
  - Verify check runs

- [ ] **Test stale job handling**
  - Start a long-running job
  - Wait for timeout
  - Verify it's killed and marked failed

- [x] **Test status command**
  - Run with various state configurations
  - Verify output formatting
  - Check color coding

- [x] **Test reset command**
  - Reset a failed schedule
  - Verify state cleared correctly

- [ ] **Test priority selection**
  - Create multiple overdue schedules
  - Verify most overdue is selected first

- [ ] **Test retry/backoff behavior**
  - Force a job to fail (disconnect network, etc.)
  - Verify status set to "failed"
  - Verify next_run unchanged
  - Wait until next scheduled time
  - Verify job retries (status reset to pending)
  - Reference: [Spec §2.3](scheduling-spec.md#23-failure-handling-and-retry-strategy)

- [ ] **Test manual schedule**
  - Create schedule without cron expression
  - Run check command, verify it's not picked up
  - Run manually: `android-sync run manual_schedule`
  - Verify execution works and state updates
  - Verify next_run stays None

## Phase 9: Final Integration

- [ ] **Update version number**
  - Increment to 0.2.0 in `src/android_sync/__init__.py`
  - Update `pyproject.toml` version

- [x] **Run full test suite**
  - `pytest tests/ -v`
  - Ensure all tests pass

- [x] **Run linter**
  - `ruff check src/ tests/`
  - Fix any linting issues

- [ ] **Test complete workflow end-to-end**
  - Fresh install
  - Run setup
  - Configure mix of scheduled (with cron) and manual (without cron) schedules
  - Wait for scheduled execution
  - Verify automatic schedules run, manual schedules don't
  - Test manual execution of manual schedule
  - Verify success

## Dependencies Between Tasks

**Critical Path:**
1. Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 6 → Phase 9
2. Phase 5 can be done in parallel with Phase 3-4
3. Phase 7 can be done in parallel with Phase 6
4. Phase 8 must come after Phase 4

**Parallel Work Opportunities:**
- Phases 2 and 3 can partially overlap (config updates independent of scheduler logic)
- Phase 5 (check script) can be done anytime after Phase 1
- Phase 7 (docs) can start once spec is understood

## Estimated Complexity

**High Complexity:**
- Phase 3: Scheduler module (state management, stale detection, priority logic)
- Phase 4: CLI modifications (integration with existing code)

**Medium Complexity:**
- Phase 2: Config schema updates (simple but requires validation)
- Phase 6: Testing (comprehensive coverage needed)

**Low Complexity:**
- Phase 1: Dependencies (straightforward)
- Phase 5: Check script (simple wrapper)
- Phase 7: Documentation
- Phase 9: Final integration

## Key Files Modified

- `pyproject.toml` - Dependencies
- `src/android_sync/config.py` - Schema updates and validation
- `src/android_sync/scheduler.py` - New module
- `src/android_sync/cli.py` - New commands and modifications
- `config.example.toml` - Example updates
- `tests/test_scheduler.py` - New test file
- `tests/test_config.py` - Extended tests
- `README.md` - Documentation updates
