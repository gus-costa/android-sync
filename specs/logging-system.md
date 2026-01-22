# Logging System Specification

**Version:** 1.0
**Date:** 2026-01-20
**Status:** Draft

## 1. Overview

This specification defines the logging system for android-sync, including log format, retention policy, output destinations, and operational procedures.

### 1.1 Goals

- Comprehensive logging for troubleshooting and auditing
- Dual output (file + console) for flexibility
- Automatic log retention management (disk space)
- Clear, parseable log format
- Minimal performance overhead

### 1.2 Non-Goals

- Structured logging (JSON, XML)
- Real-time log aggregation
- Log rotation (instead: one file per invocation)
- Remote logging (syslog, cloud)
- Log level filtering per module

## 2. Architecture

### 2.1 Dual Output Model

```
Logger (android_sync)
    ├─ FileHandler → android-sync-YYYYMMDD-HHMMSS.log
    └─ StreamHandler → stdout
```

**Why Dual Output:**
- **File**: Persistent record for debugging and auditing
- **Console**: Immediate feedback during execution
- Both use identical format

**Synchronization:**
- Same log level for both handlers
- Same formatter for both handlers
- Same messages to both outputs
- No filtering difference

### 2.2 Logger Hierarchy

**Root Logger:**
```python
logger = logging.getLogger("android_sync")
```

**Subloggers:**
```python
sync_logger = logging.getLogger("android_sync.sync")
```

**Hierarchy Benefits:**
- All loggers inherit root level
- Can filter by module if needed
- Clear message source in logs

**Current Usage:**
- Root logger used by most modules
- `android_sync.sync` used by sync module
- Easy to add more subloggers

## 3. Log Format

### 3.1 Format String

```python
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
```

### 3.2 Example Output

```
2026-01-19 03:15:42 [INFO] android_sync: Logging initialized: /path/to/android-sync-20260119-031542.log
2026-01-19 03:15:42 [INFO] android_sync.sync: Syncing profile: photos
2026-01-19 03:15:45 [DEBUG] android_sync.sync: Running: rclone sync /source :b2:bucket/dest --transfers 4 --checksum -v
2026-01-19 03:20:15 [INFO] android_sync.sync: Profile photos complete
2026-01-19 03:20:15 [INFO] android_sync: Sync complete: 1/1 profiles succeeded, 1250 files transferred, 10 files hidden
```

### 3.3 Format Components

**%(asctime)s:**
- Timestamp when log record was created
- Format: `YYYY-MM-DD HH:MM:SS`
- Example: `2026-01-19 03:15:42`
- Uses local timezone (device timezone)

**[%(levelname)s]:**
- Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Wrapped in brackets for clarity
- Fixed width for alignment (DEBUG/ERROR are 5 chars)

**%(name)s:**
- Logger name: `android_sync` or `android_sync.<module>`
- Indicates which module generated the log
- Helps with filtering and troubleshooting

**%(message)s:**
- Actual log message
- User-defined content
- Can include formatted strings

### 3.4 Why This Format

**Timestamp First:**
- Chronological sorting
- Easy to scan for time ranges
- Standard log format convention

**Level in Brackets:**
- Visual separation
- Easy to grep for errors: `grep "\[ERROR\]"`

**Module Name:**
- Identify source of message
- Debug module-specific issues
- Filter logs by module

**Simple Format:**
- Human-readable
- Easy to parse with standard tools (grep, awk)
- Not verbose (no thread IDs, process IDs)
- Suitable for single-threaded application

## 4. Log Levels

### 4.1 Level Definitions

**DEBUG:**
- Detailed diagnostic information
- Only shown with `--verbose` flag
- Includes rclone commands, state transitions
- Example: `Running: rclone sync ...`

**INFO (default):**
- General informational messages
- Sync start/completion
- File transfer summaries
- Example: `Syncing profile: photos`

**WARNING:**
- Potentially problematic situations
- Missing source directories (non-fatal)
- Stale job detection
- Example: `Source path does not exist: /path`

**ERROR:**
- Error events
- Sync failures
- Credential errors
- Example: `Sync failed for /path: error message`

**CRITICAL:**
- Not currently used
- Reserved for catastrophic failures

### 4.2 Level Usage Guidelines

**Use DEBUG for:**
- Commands being executed
- State file contents
- Detailed step-by-step flow
- Information useful only for troubleshooting

**Use INFO for:**
- Normal operation events
- Start/completion of operations
- Summary statistics
- User-facing status updates

**Use WARNING for:**
- Recoverable errors
- Missing optional resources
- Potential issues that don't stop execution
- Situations user should be aware of

**Use ERROR for:**
- Failures that affect operation
- Missing required resources
- Credential problems
- Situations that require user action

### 4.3 Verbose Mode

**Activation:**
```bash
android-sync --verbose run daily
# OR
android-sync -v run daily
```

**Effect:**
- Sets log level to DEBUG
- Shows all log messages
- Includes rclone commands
- More detailed error messages

**Use Cases:**
- Troubleshooting sync failures
- Understanding what files are being synced
- Debugging state transitions
- Reporting issues

## 5. Log File Management

### 5.1 File Naming Convention

**Format:**
```
android-sync-YYYYMMDD-HHMMSS.log
```

**Examples:**
```
android-sync-20260119-031542.log  # Jan 19, 2026 at 03:15:42
android-sync-20260120-150320.log  # Jan 20, 2026 at 15:03:20
```

**Implementation:**
```python
log_file = log_dir / f"android-sync-{datetime.now():%Y%m%d-%H%M%S}.log"
```

**Properties:**
- **Unique**: Timestamp includes seconds (no collisions for invocations >1s apart)
- **Sortable**: Alphabetical sort = chronological sort
- **Parseable**: Timestamp extractable from filename
- **Standard**: ISO-like date format (no spaces)

### 5.2 File Location

**Default Directory:**
```
/data/data/com.termux/files/home/logs
```

**Configurable:**
```toml
[general]
log_dir = "/custom/path/to/logs"
```

**Directory Creation:**
```python
log_dir.mkdir(parents=True, exist_ok=True)
```
- Creates directory if it doesn't exist
- Creates parent directories as needed
- Idempotent (safe to call repeatedly)

### 5.3 File Permissions

**Permissions:**
- Created with default umask
- Typically 0600 on Termux (user read/write only)
- No special hardening

**Security:**
- Logs don't contain credentials
- Safe to read by user
- May contain file paths and error messages

### 5.4 Log Rotation

**Strategy:** One file per invocation (no rotation)

**Rationale:**
- Each invocation is separate
- Files are typically small (<1 MB)
- Simpler than time-based or size-based rotation
- Avoids complexity of multi-process file locking

**Cleanup:**
- Retention-based deletion (see §6)
- Old files removed before creating new file

## 6. Log Retention

### 6.1 Retention Policy

**Configuration:**
```toml
[general]
log_retention_days = 7  # Default
```

**Behavior:**
- Logs older than N days are deleted
- Deletion happens at start of logging setup
- Based on file modification time (mtime)
- Applies to both main logs and schedule logs

**Special Value:**
```toml
log_retention_days = 0  # Disable cleanup
```
- Keeps all logs forever
- Useful for debugging or external log management

### 6.2 Cleanup Algorithm

**Location:** `src/android_sync/logging.py:59-80`

**Process:**
```
1. Calculate cutoff time: now - retention_days
2. Glob for log files:
   - android-sync-*.log (main invocation logs)
   - schedule-*.log (background job logs)
3. For each file:
   - Check file mtime
   - If mtime < cutoff: Delete file
4. Return count of deleted files
```

**Implementation:**
```python
cutoff = datetime.now() - timedelta(days=retention_days)

# Clean up main logs
for log_file in log_dir.glob("android-sync-*.log"):
    if log_file.stat().st_mtime < cutoff.timestamp():
        log_file.unlink()
        removed += 1

# Clean up schedule logs
for log_file in log_dir.glob("schedule-*.log"):
    if log_file.stat().st_mtime < cutoff.timestamp():
        log_file.unlink()
        removed += 1
```

**When Cleanup Runs:**
- At start of `setup_logging()`
- Before creating new log file
- Ensures cleanup even if process crashes
- Also runs when background jobs start (each job calls setup_logging)

**Why mtime:**
- Simpler than parsing filename timestamp
- Robust to clock changes
- Standard filesystem metadata
- Works for both timestamped files (main logs) and static names (schedule logs)

### 6.3 Retention Recommendations

**Mobile Device (Termux):**
- **Default:** 7 days
- **Conservative:** 3-5 days (limited storage)
- **Generous:** 14-30 days

**Desktop/Server:**
- **Default:** 30 days
- **Conservative:** 14 days
- **Generous:** 90 days

**Debugging:**
- **Temporary:** 0 (no cleanup)
- Remember to re-enable after debugging

**Factors:**
- Sync frequency (daily = more logs)
- Disk space available
- Compliance requirements
- Troubleshooting needs

## 7. Background Job Logging

### 7.1 Separate Log Files

**Location:** `src/android_sync/scheduler.py:252-254`

**Background Job Logs:**
```python
log_dir = Path.home() / "logs"
log_file = log_dir / f"schedule-{schedule_name}.log"
```

**Example:**
```
~/logs/schedule-daily.log
~/logs/schedule-weekly.log
```

**Why Separate:**
- Background jobs spawned by `check` command
- No terminal to display output
- Separate file per schedule (not per invocation)
- Append mode (all runs in one file)

### 7.2 Append Mode

**Implementation:**
```python
with open(log_file, "a") as log:
    subprocess.Popen(
        ["android-sync", "--config", str(config_path), "run", schedule_name],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
```

**Characteristics:**
- File opened in append mode
- Multiple runs accumulate in same file
- File mtime updated on each append
- Cleaned up by retention policy (same as main logs, see §6.2)

### 7.3 Log Format

**Format:** Same as normal logs
- Dual output (file + stdout) in `run` command
- stdout redirected to schedule log file
- stderr merged with stdout

**Example Content:**
```
2026-01-19 03:00:00 [INFO] android_sync: Logging initialized: /path/to/android-sync-20260119-030000.log
2026-01-19 03:00:00 [INFO] android_sync.sync: Syncing profile: photos
...
2026-01-19 03:15:00 [INFO] android_sync: Sync complete: 1/1 profiles succeeded, 1250 files transferred, 10 files hidden
```

### 7.4 Retention and Cleanup

**Schedule Log Retention:**
- Schedule logs follow same retention policy as main logs (§6.1)
- Files older than `log_retention_days` are deleted automatically
- Cleanup runs when any logging is initialized (including background jobs)
- mtime updated on each append, ensuring active logs aren't deleted

**Why Append Mode Works with Retention:**
- Each job execution appends to schedule log, updating mtime
- Active schedules continuously update their log files
- Inactive schedules (not run for N days) have old mtime and get cleaned up
- This naturally handles both old content and abandoned schedules

**File Locking:**
- Multiple jobs for same schedule could theoretically conflict
- Scheduler prevents this (one job at a time per scheduling.md §2.2)
- Manual runs could conflict with scheduled runs (user responsibility)
- OS-level append is atomic for small writes, reducing corruption risk

**Monitoring Recommendations:**

1. **Check log sizes periodically:**
   ```bash
   du -h ~/logs/schedule-*.log
   ```

2. **Verify retention is working:**
   ```bash
   # List schedule logs with modification times
   ls -lht ~/logs/schedule-*.log
   ```

3. **Manual cleanup if needed:**
   ```bash
   # Force cleanup by updating mtime to old date
   touch -d "60 days ago" ~/logs/schedule-old.log
   # Next logging init will clean it up
   ```

## 8. What Gets Logged

### 8.1 Always Logged (INFO)

**Initialization:**
```
Logging initialized: /path/to/log/file.log
```

**Sync Operations:**
```
Syncing profile: photos
Profile photos complete
Sync complete: 1/1 profiles succeeded, 1250 files transferred, 10 files hidden
```

**Dry-Run Summaries:**
```
==================================================
Dry-run summary for profile 'photos'
==================================================
Files to transfer: 1250
  DCIM: 1000 files
  Pictures: 250 files
Files to delete: 10
  DCIM: 10 files
==================================================
```

### 8.2 Conditional Logging (DEBUG)

**Requires `--verbose` flag:**

**Rclone Commands:**
```
Running: rclone sync /source :b2:bucket/dest --transfers 4 --checksum -v
```

**State Transitions:**
```
State file corrupted: /path/to/state.json
```

### 8.3 Warnings (WARNING)

**Missing Sources:**
```
Source path does not exist: /storage/emulated/0/DCIM
```

**Stale Jobs:**
```
Job for schedule 'daily' has been running for 25 hours (stale)
```

### 8.4 Errors (ERROR)

**Credential Failures:**
```
Failed to get credentials: Secrets file not found: /path/to/secrets.gpg
Run 'android-sync setup' to initialize credentials.
```

**Sync Failures:**
```
Sync failed for /storage/emulated/0/DCIM: Connection timeout
```

**Unknown References:**
```
Unknown profile: typo
Unknown schedule: typo
```

### 8.5 Never Logged

**Credentials:**
- B2 Key ID
- B2 Application Key
- GPG passphrase
- Any secret material

**Large Data:**
- Individual file paths (in live mode)
- Full file lists (unless dry-run summary)
- Raw rclone output (unless debug)

## 9. Log Analysis

### 9.1 Common Queries

**Find all errors:**
```bash
grep "\[ERROR\]" android-sync-*.log
```

**Find all syncs for a profile:**
```bash
grep "Syncing profile: photos" android-sync-*.log
```

**Find recent logs:**
```bash
ls -lt android-sync-*.log | head -5
```

**Count files transferred:**
```bash
grep "Sync complete" android-sync-*.log | \
  awk '{print $NF " " $(NF-1)}' | \
  grep "files transferred"
```

**Find logs from specific date:**
```bash
ls android-sync-20260119-*.log
```

### 9.2 Troubleshooting Patterns

**Sync failures:**
1. Check latest log for ERROR messages
2. Look for rclone errors (network, credentials)
3. Check if source paths exist (WARNING)
4. Verify credentials (KeystoreError)

**Missing schedule runs:**
1. Check `android-sync status`
2. Look for check command logs
3. Verify termux-job-scheduler is running
4. Check schedule state files

**Slow syncs:**
1. Enable verbose logging
2. Count files transferred
3. Check rclone transfer speed
4. Adjust transfers parameter

## 10. Performance Characteristics

### 10.1 Overhead

**Logging Overhead:**
- Minimal (< 1% of total execution time)
- Dominated by I/O operations
- Buffered writes reduce impact

**Disk I/O:**
- Two write operations per log message (file + stdout)
- Stdout buffered by OS
- File buffered by Python logging

**Memory:**
- Log messages not buffered in memory
- Immediate write to disk
- No memory accumulation

### 10.2 File Sizes

**Typical Sizes:**
- Dry-run: 1-10 KB
- Small sync (< 100 files): 10-50 KB
- Medium sync (100-1000 files): 50-200 KB
- Large sync (> 1000 files): 200 KB - 1 MB

**Factors:**
- Number of files
- Verbosity level
- Error messages
- Dry-run summaries

**Disk Space Estimate:**
- Daily syncs, 7-day retention: ~7-70 MB
- Hourly syncs, 7-day retention: ~200-500 MB

## 11. Operational Procedures

### 11.1 Enabling Debug Logging

**Temporary (one command):**
```bash
android-sync --verbose run daily
```

**For troubleshooting:**
```bash
# 1. Disable log cleanup
vim ~/.config/android-sync/config.toml
# Set: log_retention_days = 0

# 2. Run with verbose
android-sync --verbose run daily

# 3. Examine logs
cat /path/to/android-sync-*.log | grep ERROR

# 4. Re-enable cleanup
vim ~/.config/android-sync/config.toml
# Set: log_retention_days = 7
```

### 11.2 Cleaning Up Logs

**Automatic (via retention):**
- Set `log_retention_days` in config
- Cleanup runs automatically

**Manual:**
```bash
# Delete all logs older than 30 days
find ~/logs -name "android-sync-*.log" -mtime +30 -delete

# Delete all logs
rm ~/logs/android-sync-*.log

# Delete schedule logs
rm ~/logs/schedule-*.log
```

### 11.3 Backing Up Logs

**Why Backup:**
- Long-term audit trail
- Compliance requirements
- Historical analysis

**How:**
```bash
# Compress and archive
tar -czf android-sync-logs-2026-01.tar.gz ~/logs/android-sync-2026-01*.log

# Move to archive location
mv android-sync-logs-2026-01.tar.gz /path/to/archive/
```

### 11.4 Monitoring Disk Space

**Check log directory size:**
```bash
du -sh ~/logs
```

**Check individual log sizes:**
```bash
ls -lh ~/logs/android-sync-*.log | tail -10
```

**Set up monitoring:**
```bash
# Alert if logs directory > 100 MB
if [ $(du -sm ~/logs | cut -f1) -gt 100 ]; then
  echo "Warning: Log directory exceeds 100 MB"
fi
```

## 12. Testing

### 12.1 Unit Tests

**Test Coverage:**
- Log format correctness
- File naming convention
- Directory creation
- Retention cleanup algorithm
- Handler configuration

**Mock Filesystem:**
- Create temporary log directory
- Generate test log files
- Verify cleanup behavior

### 12.2 Integration Tests

**Manual Testing:**
1. Run with and without --verbose
2. Verify file and console output match
3. Check log file creation
4. Verify retention cleanup
5. Test with log_retention_days = 0

## 13. Security Considerations

### 13.1 What's Safe to Log

**Safe:**
- File paths (public)
- Bucket names (public)
- Timestamps
- File counts
- Error messages (non-sensitive)

**Not Safe (Never Logged):**
- B2 credentials
- GPG passphrase
- Any secret material

### 13.2 Log File Access

**Who Can Read:**
- User who ran command
- Root (on rooted devices)
- Apps with storage permission (Android 10+)

**Protection:**
- Filesystem permissions (0600)
- Log directory in user home
- No additional encryption

### 13.3 Log Rotation Security

**Concern:** Old logs may contain sensitive paths

**Mitigation:**
- Retention-based deletion
- User can manually scrub logs
- No automatic remote upload

## 14. Future Enhancements (Out of Scope)

- Structured logging (JSON format)
- Log level per module
- Remote logging (syslog, cloud)
- Log aggregation for multiple devices
- Real-time log streaming
- Log file compression
- External log rotation (logrotate integration)
- Size-based rotation for schedule logs (in addition to time-based)
- Separate retention policy for schedule vs main logs
- Log file size limits per file
- Circular buffer for memory-constrained devices
- Python `RotatingFileHandler` for schedule logs instead of append

## 15. References

- [Python logging Documentation](https://docs.python.org/3/library/logging.html)
- [Logging HOWTO](https://docs.python.org/3/howto/logging.html)
- [Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html)
- [Log File Rotation](https://www.man7.org/linux/man-pages/man8/logrotate.8.html)
