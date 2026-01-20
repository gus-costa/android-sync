# CLI Architecture Specification

**Version:** 1.0
**Date:** 2026-01-20
**Status:** Draft

## 1. Overview

This specification defines the command-line interface architecture for android-sync, including command structure, argument parsing, state management, and command interactions.

### 1.1 Goals

- Clear, intuitive command structure
- Consistent error handling across all commands
- Proper state management for scheduled operations
- Idempotent operations where possible
- Helpful error messages and exit codes

### 1.2 Non-Goals

- Interactive TUI (terminal user interface)
- Real-time progress dashboards
- Command history or undo functionality
- Shell completion scripts (could be added later)
- Multiple output formats (JSON, XML, etc.)

## 2. Command Structure

### 2.1 Top-Level Commands

```
android-sync [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS]
```

**Available Commands:**
- `setup` - Initialize keystore and credentials
- `run` - Execute sync operations
- `list` - List profiles and schedules
- `check` - Check for overdue schedules (scheduler invokes)
- `status` - Display schedule status
- `reset` - Reset schedule state

**Command Count:** 6 commands

**Design Principle:**
- Commands are verbs (setup, run, list, check, status, reset)
- Each command has single responsibility
- No command aliases (clarity over brevity)

### 2.2 Global Options

**Available on all commands:**

```bash
--version              # Show version and exit
--config PATH, -c PATH # Override config file location
--verbose, -v          # Enable debug logging
```

**Global Options Details:**

**--version:**
```bash
android-sync --version
# Output: android-sync 1.0.0
```
- Shows version string from `__version__`
- Exits immediately (doesn't run command)
- Standard behavior for all CLI tools

**--config PATH:**
```bash
android-sync --config /custom/config.toml run daily
```
- Overrides default config location
- Default: `~/.config/android-sync/config.toml`
- Applies to all commands except `setup`
- Validated before command execution

**--verbose:**
```bash
android-sync --verbose run daily
```
- Enables debug logging (Python logging DEBUG level)
- Only affects commands with logging (`run`, `check`)
- Shows rclone commands, state transitions, detailed errors
- Useful for troubleshooting

### 2.3 Argument Parsing

**Parser:** Python `argparse` (standard library)

**Structure:**
1. Main parser with global options
2. Subparsers for each command
3. Command-specific arguments

**Location:** `src/android_sync/cli.py:36-122`

**Why argparse:**
- Standard library (no dependencies)
- Automatic help generation
- Type conversion and validation
- Mutually exclusive groups
- Subcommand support

## 3. Command Reference

### 3.1 setup Command

**Purpose:** Initialize keystore and encrypt credentials

**Syntax:**
```bash
android-sync setup [--secrets-file PATH] [--force]
```

**Arguments:**

**--secrets-file PATH:**
- Path for encrypted secrets file
- Default: `~/.local/share/android-sync/secrets.gpg`
- Optional override for custom location

**--force, -f:**
- Overwrite existing secrets file
- Re-prompt for credentials
- Useful for credential rotation

**Execution Flow:**
```
1. Check if keystore key exists
   - If not: Generate RSA 4096-bit key
   - If yes: Reuse existing key

2. Check if secrets file exists
   - If exists and not --force: Skip credential setup
   - Otherwise: Prompt for credentials and encrypt

3. Create state directory

4. Create check-schedule.sh script

5. Register with termux-job-scheduler
   - If fails: Print warning but continue
```

**Idempotency:**
- Safe to run multiple times
- Skips steps that are already complete
- Only overwrites with --force flag

**Interactivity:**
```
Enter your Backblaze B2 credentials:
  Key ID: [user input]
  Application Key: [hidden input]
```

**Exit Codes:**
- 0: Success
- 1: Error (key generation failed, encryption failed, etc.)

**Example:**
```bash
# Initial setup
android-sync setup

# Re-setup with new credentials
android-sync setup --force

# Custom secrets location
android-sync setup --secrets-file /custom/secrets.gpg
```

**Implementation:** `src/android_sync/cli.py:159-257`

### 3.2 run Command

**Purpose:** Execute sync operations

**Syntax:**
```bash
android-sync run SCHEDULE [--dry-run]
android-sync run --profile PROFILE [--dry-run]
android-sync run --all [--dry-run]
```

**Arguments (Mutually Exclusive):**

**SCHEDULE (positional):**
- Schedule name from config
- Executes all profiles in schedule
- Updates schedule state (last_run, next_run, etc.)

**--profile PROFILE, -p PROFILE:**
- Single profile name from config
- Does NOT update schedule state
- Useful for testing or manual single-profile runs

**--all, -a:**
- Execute all profiles in config
- Does NOT update schedule state
- Useful for one-off full backups

**--dry-run, -n:**
- Preview mode
- Shows what would be synced
- No actual transfers
- Safe to run anytime

**Execution Flow:**
```
1. Load config

2. Decrypt credentials from secrets file

3. Determine profiles to run
   - From schedule, or
   - Single profile, or
   - All profiles

4. If running schedule:
   - Update state: status="running", set PID, started_at

5. Execute each profile sequentially
   - Load credentials
   - Call sync_profile()
   - Collect results

6. Log summary (success count, files transferred)

7. If running schedule:
   - Update state: status="success"/"failed", calculate next_run

8. Return exit code
```

**State Management:**
- Only updates state when running a schedule (not --profile or --all)
- State updates wrapped in try-finally (always updates even on error)
- State reflects last run time and next scheduled time

**Exit Codes:**
- 0: All profiles succeeded
- 1: Any profile failed OR credential error OR unknown profile/schedule

**Examples:**
```bash
# Preview a schedule
android-sync run daily --dry-run

# Run a schedule (updates state)
android-sync run daily

# Run single profile (no state update)
android-sync run --profile photos

# Run all profiles (no state update)
android-sync run --all

# Verbose dry-run
android-sync --verbose run daily --dry-run
```

**Implementation:** `src/android_sync/cli.py:275-344`

### 3.3 list Command

**Purpose:** List configured profiles and schedules

**Syntax:**
```bash
android-sync list profiles
android-sync list schedules
```

**Arguments:**

**type (positional, required):**
- Choices: `profiles`, `schedules`
- Which entities to list

**Output Format:**

**Profiles:**
```
Profiles:
  photos: /storage/emulated/0/DCIM, /storage/emulated/0/Pictures -> photos
  documents: /storage/emulated/0/Documents -> documents
```

**Schedules:**
```
Schedules:
  daily: [photos, documents, downloads]
  weekly: [everything]
```

**Exit Codes:**
- 0: Success

**Examples:**
```bash
android-sync list profiles
android-sync list schedules
```

**Notes:**
- Simple, script-friendly output
- No colors or formatting
- One line per item

**Implementation:** `src/android_sync/cli.py:260-272`

### 3.4 check Command

**Purpose:** Check for overdue schedules and spawn one background job if needed

**Syntax:**
```bash
android-sync check
```

**Arguments:** None

**Execution Flow:**
```
1. Load config

2. Get list of overdue schedules
   - Calls get_overdue_schedules()
   - Returns list sorted by overdue time (descending)

3. If no overdue schedules:
   - Exit silently (exit code 0)

4. If overdue schedules exist:
   - Select most overdue (first in list)
   - Spawn background job: android-sync run <schedule>
   - Exit immediately (don't wait for job)
```

**Background Job:**
- Detached process (survives check process exit)
- Stdout/stderr to log file
- Process updates own state
- Independent lifecycle

**Silent Operation:**
- No output unless error
- Designed for cron-like invocation
- Errors printed to stderr

**Invocation:**
- Called by termux-job-scheduler every 15 minutes
- Can be called manually for testing
- Check script: `~/.local/share/android-sync/check-schedule.sh`

**Exit Codes:**
- 0: Success (job spawned or no action needed)
- 1: Config error

**Examples:**
```bash
# Manual check (for testing)
android-sync check
```

**Notes:**
- At most ONE job spawned per invocation
- Multiple overdue schedules handled in priority order
- Next check (15 min later) spawns next overdue schedule

**Implementation:** `src/android_sync/cli.py:347-360`

### 3.5 status Command

**Purpose:** Display current status of all schedules

**Syntax:**
```bash
android-sync status
```

**Arguments:** None

**Output Format:**
```
Schedule: daily
  Type: Scheduled (cron: 0 3 * * *)
  Status: success
  Last Run: 2026-01-19 03:00:00
  Next Run: 2026-01-20 03:00:00
  Overdue: No

Schedule: photos_frequent
  Type: Scheduled (cron: 0 */6 * * *)
  Status: running (PID 12345)
  Started: 2026-01-19 09:00:00
  Next Run: 2026-01-19 12:00:00
  Overdue: No

Schedule: manual_backup
  Type: Manual (no automatic scheduling)
  Status: success
  Last Run: 2026-01-18 14:30:00
  Next Run: N/A
```

**Color Coding:**
- Green: success
- Yellow: running
- Red: failed, overdue

**Fields Displayed:**

**Type:**
- "Scheduled" + cron expression
- "Manual" if no cron

**Status:**
- pending, running, success, failed
- PID shown if running

**Last Run / Started / Last Attempt:**
- Depends on status
- Human-readable timestamps

**Next Run:**
- Calculated from cron expression
- "N/A" for manual schedules

**Overdue:**
- "Yes" (red) with minutes count if now >= next_run
- "No" otherwise

**Exit Codes:**
- 0: Success

**Examples:**
```bash
android-sync status
```

**Implementation:** `src/android_sync/cli.py:363-416`

### 3.6 reset Command

**Purpose:** Reset a schedule's state (useful for failed/stale jobs)

**Syntax:**
```bash
android-sync reset SCHEDULE
```

**Arguments:**

**SCHEDULE (positional, required):**
- Schedule name from config
- Must be configured schedule

**Execution Flow:**
```
1. Load config

2. Validate schedule exists

3. Load current state

4. Reset state fields:
   - status = "pending"
   - pid = None
   - started_at = None
   - finished_at = None

5. Recalculate next_run:
   - If scheduled: Calculate from now using cron
   - If manual: Set to None

6. Save state

7. Print confirmation
```

**Use Cases:**
- Failed schedule stuck in "failed" state
- Stale job (running too long)
- Want to reset next_run time
- Testing/development

**Exit Codes:**
- 0: Success
- 1: Unknown schedule

**Examples:**
```bash
# Reset a failed schedule
android-sync reset daily

# Reset a stale schedule
android-sync reset photos_frequent
```

**Notes:**
- Does NOT kill running process
- Only resets state file
- Recalculates next_run from current time
- Manual schedules set next_run to null

**Implementation:** `src/android_sync/cli.py:419-448`

## 4. Command Interactions

### 4.1 Setup → Run Flow

```
1. User runs: android-sync setup
   - Generates keystore key
   - Prompts for credentials
   - Encrypts secrets to secrets.gpg
   - Creates state directory
   - Registers scheduler

2. User runs: android-sync run daily --dry-run
   - Loads config
   - Decrypts secrets using keystore key
   - Runs sync in dry-run mode
   - Shows preview

3. User runs: android-sync run daily
   - Updates schedule state (running)
   - Executes sync
   - Updates state (success/failed)
```

### 4.2 Scheduler Flow

```
Every 15 minutes:
1. termux-job-scheduler invokes check-schedule.sh

2. check-schedule.sh calls: android-sync check
   - Loads all schedule states
   - Checks for stale jobs
   - Finds overdue schedules

3. If overdue schedule exists:
   - Spawns: android-sync run <schedule>
   - Background process updates state
   - Process logs to file

4. check command exits

Meanwhile:
1. Background run command executing
   - Updates state: status="running"
   - Syncs profiles
   - Updates state: status="success"/"failed"
   - Calculates next_run

2. 15 minutes later, check runs again
   - Sees successful run
   - Schedule not overdue yet
   - Exits (no action)
```

### 4.3 Manual Intervention Flow

```
1. User checks status:
   android-sync status
   # Sees: Schedule 'daily' - Status: failed

2. User investigates logs

3. User resets schedule:
   android-sync reset daily
   # Schedule state: status="pending", recalculates next_run

4. User manually runs:
   android-sync run daily
   # Updates state on success
```

## 5. State Management

### 5.1 When State is Updated

**run command:**
- Updates state ONLY when running a schedule (not --profile or --all)
- On start: `update_state_on_start(schedule_name, config)`
  - Sets: status="running", pid, started_at
- On finish: `update_state_on_finish(schedule_name, config, success)`
  - Sets: status="success"/"failed", last_run (if success), finished_at, next_run
  - Clears: pid

**check command:**
- Reads all states
- Calls `get_overdue_schedules()` which handles stale job detection
- Stale jobs updated to status="failed"
- Does NOT update states directly (only reads)

**reset command:**
- Resets state to "pending"
- Recalculates next_run

**status command:**
- Read-only (displays current state)

**list command:**
- Doesn't interact with state

**setup command:**
- Doesn't interact with state

### 5.2 State Lifecycle

```
Initial (no state file):
  status = "pending"
  all fields null

setup creates state directory

First check with cron schedule:
  Creates state file
  Calculates next_run
  status = "pending"

First overdue check:
  Spawns background job

Background job starts:
  status = "running"
  pid = os.getpid()
  started_at = now()

Background job succeeds:
  status = "success"
  last_run = now()
  finished_at = now()
  next_run = calculate_next_run(cron, now())
  pid = None

Next check:
  Sees status="success", not overdue
  No action

Eventually overdue:
  Spawns new background job
  Cycle repeats
```

### 5.3 State Consistency

**Guaranteed:**
- State always updated in try-finally block
- Even if sync fails, state is updated
- PID always cleared on completion

**Not Guaranteed:**
- If process killed with SIGKILL: State may be stale
- Stale detection handles this (checks if PID exists)

## 6. Error Handling

### 6.1 Error Categories

**Configuration Errors:**
```python
except ConfigError as e:
    print(f"Error: {e}", file=sys.stderr)
    return 1
```
- Missing config file
- Invalid TOML
- Missing required fields
- Unknown profile reference

**Credential Errors:**
```python
except KeystoreError as e:
    logger.error("Failed to get credentials: %s", e)
    logger.error("Run 'android-sync setup' to initialize credentials.")
    return 1
```
- Missing secrets file
- Decryption failure
- Missing credential fields

**Validation Errors:**
```python
if args.profile not in config.profiles:
    logger.error("Unknown profile: %s", args.profile)
    return 1
```
- Unknown profile name
- Unknown schedule name

**Sync Errors:**
- Individual profile failures don't abort schedule
- All profiles attempted
- Exit code reflects overall success

### 6.2 Exit Code Standards

**Exit Codes:**
- **0**: Complete success
- **1**: Error (config, credentials, sync failure, etc.)
- **2**: Not used (reserved for future)

**Command Exit Codes:**

| Command | Success | Failure |
|---------|---------|---------|
| setup | 0 | 1 (key gen failed, encryption failed) |
| run | 0 (all succeed) | 1 (any fail, or credential error) |
| list | 0 | N/A (no failure mode) |
| check | 0 | 1 (config error) |
| status | 0 | N/A (no failure mode) |
| reset | 0 | 1 (unknown schedule) |

**Script Integration:**
```bash
if android-sync run daily; then
    echo "Backup successful"
else
    echo "Backup failed" >&2
    exit 1
fi
```

### 6.3 Error Message Format

**Standard Format:**
```
Error: <specific error message>
```

**With Suggestions:**
```
Error: Failed to get credentials: <details>
Run 'android-sync setup' to initialize credentials.
```

**With Color (in status):**
- Red for errors and overdue
- Yellow for running
- Green for success

## 7. Logging

### 7.1 When Logging is Enabled

**Commands with logging:**
- `run` - Needs sync operation logs
- `check` - Background execution logs

**Commands without logging:**
- `setup` - Uses print() for user feedback
- `list` - Simple output, no logging needed
- `status` - Display only, no logging
- `reset` - Simple operation, uses print()

### 7.2 Logging Setup

**Location:** `src/android_sync/cli.py:136-138`

```python
if args.command in ["run", "check"]:
    logger = setup_logging(config.log_dir, config.log_retention_days, args.verbose)
```

**Setup Function:**
- Creates log directory
- Cleans old logs (based on retention_days)
- Creates timestamped log file
- Sets up file and console handlers
- Returns logger instance

**Log File Naming:**
```
android-sync-YYYYMMDD-HHMMSS.log
```

**See Also:** `specs/logging-system.md`

## 8. Entry Point

### 8.1 Main Function

**Location:** `src/android_sync/cli.py:34-156`

**Flow:**
```
1. Create argument parser
2. Add global options
3. Add subparsers for each command
4. Parse arguments
5. Route to command handler
6. Return exit code
```

**Package Entry Point:**

**pyproject.toml:**
```toml
[project.scripts]
android-sync = "android_sync.cli:main"
```

**Installation:**
```bash
pip install .
# Creates executable: android-sync
# Calls: android_sync.cli.main()
```

### 8.2 Command Routing

**Pattern:**
```python
if args.command == "setup":
    return cmd_setup(args)

# Load config for other commands
try:
    config = load_config(args.config)
except ConfigError as e:
    print(f"Error: {e}", file=sys.stderr)
    return 1

if args.command == "run":
    return cmd_run(config, args, logger)

# ... other commands
```

**Why this pattern:**
- Clear separation between commands
- setup doesn't need config
- Other commands fail fast on config error
- Each command function returns exit code

## 9. Testing

### 9.1 Unit Tests

**Test Coverage:**

**Argument Parsing:**
- Valid arguments for each command
- Invalid arguments
- Mutually exclusive groups
- Global options

**Command Functions:**
- Mock filesystem operations
- Mock keystore operations
- Mock subprocess calls
- Verify state updates

**Error Handling:**
- Config file not found
- Invalid credentials
- Unknown profile/schedule

### 9.2 Integration Tests

**Manual Testing:**

1. **Setup flow:**
   - Fresh installation
   - Re-run setup (idempotency)
   - setup --force (credential rotation)

2. **Run flow:**
   - Dry-run → actual run
   - Single profile, all profiles, schedule
   - Verify state updates

3. **Scheduler flow:**
   - Trigger check manually
   - Verify background job spawns
   - Verify state transitions

4. **Error scenarios:**
   - Missing config file
   - Invalid credentials
   - Unknown profile name
   - Failed sync

## 10. Design Decisions

### 10.1 Why Subcommands (Not Flags)

**Chosen:**
```bash
android-sync run daily
android-sync status
```

**Rejected:**
```bash
android-sync --run daily
android-sync --status
```

**Rationale:**
- Clearer intent (verb-based)
- Better help organization
- Easier to add new commands
- Standard CLI pattern

### 10.2 Why Mutually Exclusive Groups

**For run command:**
- Either schedule OR --profile OR --all
- Prevents ambiguity
- Clear error messages
- Enforced by argparse

### 10.3 Why Separate check and run

**Could have been:**
```bash
android-sync scheduler
# Internal loop checking and running
```

**Instead:**
```bash
android-sync check  # Called by scheduler
android-sync run <schedule>  # Spawned by check
```

**Rationale:**
- Separation of concerns
- check is lightweight (exits quickly)
- run can be long-running
- Easier to test independently
- Allows manual invocation of both

### 10.4 Why --dry-run on run (Not Separate Command)

**Chosen:**
```bash
android-sync run daily --dry-run
```

**Rejected:**
```bash
android-sync preview daily
```

**Rationale:**
- Dry-run is a mode, not a separate operation
- Standard flag across many tools (rsync, rclone, etc.)
- Same logic, different execution mode

## 11. Future Enhancements (Out of Scope)

- Shell completion (bash, zsh, fish)
- JSON output format (`--format json`)
- Progress bars for sync operations
- Interactive mode (select profiles, confirm)
- Schedule pause/resume commands
- Rollback command (restore from B2)
- Log viewing command (`android-sync logs`)
- Config validation command (`android-sync validate`)
- Batch operations (run multiple schedules)
- Priority-based schedule execution

## 12. References

- [argparse Documentation](https://docs.python.org/3/library/argparse.html)
- [Exit Status Conventions](https://www.gnu.org/software/libc/manual/html_node/Exit-Status.html)
- [Command Line Interface Guidelines](https://clig.dev/)
- [POSIX Utility Conventions](https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap12.html)
