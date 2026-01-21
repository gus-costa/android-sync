# Sync Engine Specification

**Version:** 1.0
**Date:** 2026-01-20
**Status:** Draft

## 1. Overview

This specification defines the sync engine for android-sync, which orchestrates file synchronization from Android device to Backblaze B2 cloud storage using rclone.

### 1.1 Goals

- Reliable one-way sync from device to cloud
- Support for multiple operational modes (sync vs copy)
- Efficient parallel transfers with configurable concurrency
- Preview mode (dry-run) for verification before syncing
- Intelligent removal tracking (hide vs delete)
- Clear progress reporting and error handling

### 1.2 Non-Goals

- Bidirectional sync (cloud to device)
- Incremental snapshots (rclone handles deduplication)
- Delta compression (B2 handles this)
- Real-time file watching (scheduled batch execution only)
- Progress persistence across interruptions

## 2. Architecture

### 2.1 Execution Model

```
User/Scheduler
     ↓
  sync_profile()
     ↓
  ┌────────────────────┐
  │ For each source:   │
  │ 1. Verify path     │
  │ 2. Build command   │
  │ 3. Execute rclone  │
  │ 4. Parse output    │
  └────────────────────┘
     ↓
  Aggregate results
     ↓
  Return SyncResult
```

**Key Characteristics:**
- Sequential processing of sources (not parallel)
- Fail-fast on individual source errors (logged, continue to next source)
- Profile-level success requires all sources to succeed
- Dry-run and live modes use different output parsing strategies

### 2.2 Two Operational Modes

**Mode 1: Sync (track_removals=True)**
- Uses `rclone sync` command
- Deletes remote files not present in source
- "True mirroring" behavior
- Default mode

**Mode 2: Copy (track_removals=False)**
- Uses `rclone copy` command
- Only adds and updates files
- Never deletes remote files
- Safer for append-only backups

**Why two modes:**
- **Sync mode**: For users who want exact mirror (deleted local files should be hidden remotely)
- **Copy mode**: For users who want accumulation (never remove cloud files even if deleted locally)

**Design Decision:**
- Use boolean flag rather than enum for simplicity
- Flag name `track_removals` clearly indicates behavior
- Default to true (safer: tracks deletions)

### 2.3 B2 Remote Format

**On-the-Fly Remote Configuration:**
```
:b2:bucket_name/path
```

**Example:**
```python
":b2:my-backup-bucket/photos/Camera"
```

**Why this format:**
- No rclone config file needed
- Credentials passed via environment variables
- Simpler setup (one less file to manage)
- Config-file-less approach recommended by rclone for automation

**Construction:**
```python
def _b2_remote(bucket: str, path: str) -> str:
    return f":b2:{bucket}/{path}"
```

**B2 Provider:**
- Uses rclone's built-in B2 backend
- Supports all B2 features (versioning, lifecycle, etc.)
- Efficient chunked uploads for large files

### 2.4 Directory Structure Mapping

**Source Path:**
```
/storage/emulated/0/DCIM/Camera/IMG_001.jpg
```

**Destination:**
```
bucket/photos/Camera/IMG_001.jpg
         ↑       ↑
    profile.dest source.name
```

**Algorithm (sync.py:90-93):**
```python
source_path = Path(source)
relative_dest = f"{profile.destination}/{source_path.name}"
dest = _b2_remote(bucket, relative_dest)
```

**Example Mappings:**

| Source | Profile Dest | Remote Path |
|--------|--------------|-------------|
| `/storage/emulated/0/DCIM` | `photos` | `bucket/photos/DCIM/` |
| `/storage/emulated/0/Pictures` | `photos` | `bucket/photos/Pictures/` |
| `/sdcard/Documents` | `docs` | `bucket/docs/Documents/` |

**Rationale:**
- Preserves top-level source directory name (provides context)
- Allows multiple sources to same destination without collision
- User controls organization via `profile.destination`
- Simple and predictable

## 3. Rclone Integration

### 3.1 Command Construction

**Location:** `src/android_sync/sync.py:178-213`

**Function:** `_build_rclone_cmd(...) -> list[str]`

**Base Command:**
```bash
rclone {sync|copy} SOURCE DEST [FLAGS]
```

**Operation Selection:**
```python
operation = "sync" if sync_deletes else "copy"
```

### 3.2 Rclone Flags

**Always Used:**

1. `--transfers N` (default: 4)
   - Parallel file transfers
   - Trade-off: Speed vs API rate limits vs memory
   - Higher = faster but more memory and B2 API calls
   - Default 4 is conservative for mobile device

2. `--checksum`
   - Use file checksums instead of modification time
   - B2 provides SHA1 checksums
   - More reliable than timestamps (timezone issues, fat32 limitations)
   - Slightly slower but much more accurate
   - **Critical for correctness**

3. `-v` (verbose)
   - Enables detailed output
   - Required for statistics parsing
   - Shows progress information
   - Logs each file transfer

**Conditional Flags:**

4. `--dry-run` (when dry_run=True)
   - Preview mode
   - Shows what would be transferred/deleted
   - No actual changes made
   - Used for user verification

5. `--progress` (when dry_run=False)
   - Shows progress bar
   - Real-time transfer statistics
   - Better UX for interactive use
   - Not used in dry-run (would interfere with parsing)

6. `--exclude PATTERN` (for each exclude pattern)
   - Exclude files matching glob pattern
   - Can be specified multiple times
   - Examples: `*.tmp`, `.thumbnails`, `*.bak`

**Notable Absent Flags:**

- **NO** `--no-traverse`: Would skip tree walking, risking missed deletions
- **NO** `--fast-list`: B2 charges for list operations, not cost-effective
- **NO** `--ignore-existing`: Want to update changed files
- **NO** `--update`: Too permissive, checksum is better
- **NO** `--retries N`: Using rclone defaults (10 retries)
- **NO** `--low-level-retries N`: Using rclone defaults (10)
- **NO** `--timeout`: Using rclone defaults
- **NO** `--contimeout`: Using rclone defaults

**Rationale for Defaults:**
- Rclone's default retry logic is robust
- Timeout handling is well-tested in rclone
- Simpler command = fewer things to configure/debug
- Can add flags later if needed based on usage

### 3.3 Credential Handling

**Location:** `src/android_sync/sync.py:28-41`

**Function:** `_rclone_env(credentials: B2Credentials) -> dict[str, str]`

**Environment Variables:**
```python
env = os.environ.copy()
env["RCLONE_B2_ACCOUNT"] = credentials.key_id
env["RCLONE_B2_KEY"] = credentials.app_key
```

**Variable Names:**
- `RCLONE_B2_ACCOUNT`: B2 Key ID (application key ID)
- `RCLONE_B2_KEY`: B2 Application Key (secret)

**Security Benefits:**

1. **Not in process list**
   - `ps aux` shows command args, not environment
   - Prevents casual observation of credentials

2. **Not in shell history**
   - No command-line args with credentials
   - History files don't capture env vars

3. **Not in logs**
   - Rclone doesn't log env var values
   - Sync module never logs credentials

4. **Short-lived**
   - Only exists during subprocess execution
   - Cleared when process exits
   - Not persisted to disk

**Limitations:**
- Environment accessible via `/proc/<pid>/environ` (requires same UID)
- Memory dumps could reveal credentials
- Acceptable for this use case (mobile device, user's own processes)

**Why environment over config file:**
- No config file to manage or secure
- No risk of committing credentials to git
- Simpler automation
- Recommended by rclone for scripting

## 4. Execution Modes

### 4.1 Dry-Run Mode

**Purpose:** Preview changes without making them

**Activation:** `sync_profile(..., dry_run=True)`

**Execution Flow:**
1. Add `--dry-run` flag to rclone command
2. Capture stdout and stderr
3. Parse stderr for file lists (NOTICE lines)
4. Group files by directory
5. Display summary to user
6. Return SyncResult with preview data

**Output Parsing (sync.py:216-241):**

**rclone dry-run output format:**
```
NOTICE: path/to/file.jpg: Skipped copy as --dry-run is set (size 1.2MB)
NOTICE: path/to/old.jpg: Skipped delete as --dry-run is set
NOTICE: path/to/updated.png: Skipped update as --dry-run is set
```

**Regex Patterns:**
```python
transfer_pattern = re.compile(r"NOTICE: (.+): Skipped (?:copy|update)")
delete_pattern = re.compile(r"NOTICE: (.+): Skipped delete")
```

**Why separate patterns:**
- Distinguish transfers from deletions
- Allows separate reporting
- User sees what will be added/updated vs removed

**Extraction Algorithm:**
```python
for line in output.splitlines():
    if match := transfer_pattern.search(line):
        transfers.append(match.group(1))  # File path
    elif match := delete_pattern.search(line):
        deletes.append(match.group(1))
```

**Directory Grouping (sync.py:271-292):**

**Algorithm:**
```python
for file_path in files:
    parts = Path(file_path).parts
    dir_key = str(Path(*parts[:depth]))  # depth=1 by default
    counts[dir_key] += 1
```

**Example:**
```
Input: ['DCIM/Camera/IMG_001.jpg', 'DCIM/Camera/IMG_002.jpg', 'Pictures/screenshot.png']
Output: {'DCIM': 2, 'Pictures': 1}
```

**Why group by directory:**
- More digestible than listing thousands of files
- Shows distribution of changes
- Helps user understand scope of sync

**Summary Display (sync.py:295-321):**
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

### 4.2 Live Execution Mode

**Purpose:** Perform actual sync

**Activation:** `sync_profile(..., dry_run=False)`

**Execution Flow:**
1. Add `--progress` flag to rclone command
2. Stream stderr to terminal in real-time (user feedback)
3. Also capture stderr for statistics parsing
4. Wait for completion
5. Parse statistics from captured output
6. Return SyncResult with actual counts

**Streaming Output (sync.py:120-139):**

**Why stream:**
- Provides real-time progress feedback
- User sees transfer happening
- Long-running operations don't appear frozen
- Progress bar updates in real-time

**Implementation:**
```python
process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, env=env)
stderr_lines = []
for line in process.stderr:
    sys.stderr.write(line)      # Display to user
    sys.stderr.flush()          # Immediate output
    stderr_lines.append(line)   # Save for parsing
process.wait()
```

**Dual capture:**
- Real-time: `sys.stderr.write(line)` - user sees progress
- Buffered: `stderr_lines.append(line)` - for parsing after completion

**Statistics Parsing (sync.py:244-268):**

**rclone statistics format:**
```
Transferred:   141.303 GiB / 141.303 GiB, 100%, 25.123 MiB/s, ETA 0s
Transferred:        52449 / 52449, 100%
Deleted:               10 / 10, 100%
```

**Extraction:**
```python
# File count line: "Transferred: X / Y, 100%" (no units)
transfer_match = re.search(r"Transferred:\s+(\d+)\s*/\s*\d+,", output)
if transfer_match:
    stats["transfers"] = int(transfer_match.group(1))

# Delete count line: "Deleted: X / Y"
delete_match = re.search(r"Deleted:\s+(\d+)\s*/\s*\d+,", output)
if delete_match:
    stats["deletes"] = int(delete_match.group(1))
```

**Why this regex:**
- Byte line has units ("GiB", "MiB") - won't match `\d+` pattern
- File line has only integers - will match
- Distinguishes between two "Transferred" lines

**Note on deletes:**
- Only present with `rclone sync` (not `rclone copy`)
- Will be 0 with track_removals=False

## 5. Error Handling

### 5.1 Missing Source Directories

**Detection (sync.py:85-88):**
```python
source_path = Path(source)
if not source_path.exists():
    logger.warning("Source path does not exist: %s", source)
    continue
```

**Behavior:**
- Log warning (not error)
- Skip to next source
- Do not abort entire profile
- Partial sync is better than no sync

**Rationale:**
- USB drives may be unmounted
- SD cards may be removed
- Temporary paths may not exist
- Other sources can still be synced

**User visibility:**
- Warning in log file
- Summary shows which sources were synced

### 5.2 Rclone Failures

**Detection (sync.py:145-155):**
```python
except subprocess.CalledProcessError as e:
    error_msg = e.stderr if e.stderr else str(e)
    logger.error("Sync failed for %s: %s", source, error_msg)
    return SyncResult(
        profile_name=profile.name,
        success=False,
        error=error_msg,
        # ... zero counts
    )
```

**Failure Types:**

1. **Network errors**
   - Connection timeout
   - DNS resolution failure
   - B2 API unavailable
   - **Action:** rclone retries automatically (10 attempts by default)

2. **Authentication errors**
   - Invalid credentials
   - Expired token
   - **Action:** Fail immediately (no retries)

3. **Bucket errors**
   - Bucket doesn't exist
   - No permission to write
   - **Action:** Fail immediately

4. **Filesystem errors**
   - Cannot read source file
   - Permission denied
   - **Action:** Skip file, continue with others

**Return Value:**
- `success=False`
- `error` field contains stderr output
- Zero counts for transfers/deletes
- Allows caller to handle failure

**Caller Responsibility:**
- Check `SyncResult.success`
- Log or display `SyncResult.error`
- Decide whether to abort or continue

**Schedule Behavior:**
- Individual profile failure doesn't abort schedule
- Schedule succeeds only if all profiles succeed
- Allows partial success

### 5.3 Interrupted Transfers

**Behavior:**
- No progress persistence in android-sync
- rclone handles partial file uploads
- B2 multipart uploads are atomic
- Interrupted transfers resume on next sync (rclone compares checksums)

**User Action:**
- Re-run sync command
- rclone will check existing files and resume
- No data loss, some duplicate work

**Future Enhancement:**
- Could add `--track-renames` flag to rclone
- Could add progress database
- Current approach is simple and works

## 6. Result Reporting

### 6.1 SyncResult Dataclass

**Location:** `src/android_sync/sync.py:44-56`

**Structure:**
```python
@dataclass
class SyncResult:
    profile_name: str           # Profile that was synced
    success: bool               # Overall success/failure
    files_transferred: int      # Count of files uploaded
    bytes_transferred: int      # Total bytes (currently always 0)
    hidden_files: list[str]     # Files deleted/hidden
    error: str | None           # Error message if failed
    # For dry-run mode:
    files_by_directory: dict[str, int]   # Transfer count by dir
    hidden_by_directory: dict[str, int]  # Delete count by dir
```

**Field Meanings:**

- **profile_name**: Identifies which profile completed
- **success**: `True` if all sources synced, `False` if any failed
- **files_transferred**: Count of files uploaded/updated
  - Dry-run: Count of files that would be transferred
  - Live: Actual count from rclone statistics
- **bytes_transferred**: Total bytes (not currently populated)
  - Placeholder for future enhancement
  - Could parse byte count from rclone output
- **hidden_files**: List of file paths deleted
  - Dry-run: List of paths that would be deleted
  - Live: Placeholder list (count only, not paths)
- **error**: Error message if `success=False`
  - Contains rclone stderr output
  - `None` if successful
- **files_by_directory**: Dry-run only, grouped counts
- **hidden_by_directory**: Dry-run only, grouped counts

**Usage:**
```python
result = sync_profile(profile, ...)
if result.success:
    logger.info("Transferred %d files", result.files_transferred)
else:
    logger.error("Sync failed: %s", result.error)
```

### 6.2 Logging

**Log Levels:**

**INFO:**
- Sync start: `"Syncing profile: photos"`
- Sync complete: `"Profile photos complete"`
- Dry-run summary (formatted tables)

**WARNING:**
- Missing source directory: `"Source path does not exist: /path"`

**ERROR:**
- Sync failure: `"Sync failed for /path: error message"`

**DEBUG** (with `--verbose`):**
- Full rclone command: `"Running: rclone sync ..."`

**What's NOT Logged:**
- B2 credentials (security)
- Individual file paths in live mode (too verbose)
- Byte-level transfer details (use rclone -vv if needed)

**Log File Location:**
- Configured in `general.log_dir`
- Format: `android-sync-YYYYMMDD-HHMMSS.log`
- One file per invocation

## 7. Performance Characteristics

### 7.1 Parallelism

**Transfer Parallelism:**
- Controlled by `--transfers` flag
- Default: 4 parallel transfers
- Configurable via `general.transfers` in config

**Source Sequencing:**
- Sources processed sequentially (one at a time)
- Each source can have parallel file transfers
- No parallelism across sources

**Why sequential sources:**
- Simpler error handling
- More predictable resource usage
- Avoids overwhelming B2 API rate limits
- Easier to debug

**Concurrency Model:**
```
Profile
  ├─ Source 1 → (4 parallel transfers) → Wait for completion
  ├─ Source 2 → (4 parallel transfers) → Wait for completion
  └─ Source 3 → (4 parallel transfers) → Wait for completion
```

### 7.2 Memory Usage

**Factors:**
- Number of parallel transfers (4 by default)
- File buffer sizes (rclone defaults)
- Output capture (stderr lines buffered)

**Typical Usage:**
- Small files: ~10-50 MB total
- Large files: ~100-200 MB total
- Acceptable for mobile device

**Memory Optimization:**
- Stream stderr instead of buffering entire output
- Use generator patterns where possible
- Rely on rclone's memory management

### 7.3 Network Efficiency

**Checksum-Based Sync:**
- Only transfers changed files
- B2 provides SHA1 checksums
- Compares checksum instead of downloading file
- Much more efficient than naive sync

**B2 API Efficiency:**
- Multipart uploads for large files (handled by rclone)
- Connection pooling (handled by rclone)
- Retry logic with exponential backoff (rclone)

**Cost Considerations:**
- List operations cost money on B2
- `--checksum` requires list operation per file
- Trade-off: Accuracy vs API cost
- Accuracy chosen (worth the small API cost)

## 8. Testing Strategy

### 8.1 Unit Tests

**File:** `tests/test_sync.py` (293 lines)

**Test Coverage:**

1. **_b2_remote()**
   - Correct format construction
   - Path handling

2. **_rclone_env()**
   - Environment variable construction
   - Credential security

3. **_build_rclone_cmd()**
   - Sync vs copy operation selection
   - Flag construction
   - Dry-run vs live mode
   - Exclude pattern handling

4. **_parse_dry_run_output()**
   - Transfer line parsing
   - Delete line parsing
   - Edge cases (malformed lines)

5. **_parse_rclone_stats()**
   - Statistics extraction
   - Byte vs file line distinction

6. **_group_by_directory()**
   - Directory grouping at different depths
   - Edge cases (root files, deep paths)

7. **sync_profile()** (mocked)
   - Success path
   - Missing source handling
   - rclone failure handling
   - Result aggregation

### 8.2 Integration Tests

**Manual Testing Scenarios:**

1. **Dry-run verification**
   - Run dry-run
   - Verify file list matches expected
   - Run actual sync
   - Verify same files transferred

2. **Removal tracking**
   - Create files, sync
   - Delete files locally
   - Sync again with track_removals=True
   - Verify files hidden in B2 (not deleted)

3. **Multiple sources**
   - Configure profile with 3+ sources
   - Verify all synced to correct paths
   - Check directory structure in B2

4. **Exclude patterns**
   - Create files matching exclude patterns
   - Verify not transferred
   - Create files not matching
   - Verify transferred

5. **Network interruption**
   - Start large sync
   - Kill network
   - Verify graceful failure
   - Restore network, re-sync
   - Verify resume from where left off

6. **Credential failure**
   - Use invalid credentials
   - Verify clear error message
   - Verify no partial uploads

### 8.3 Test Environment

**Requirements:**
- rclone installed
- Mock B2 credentials (test account or mocked)
- Test data with known file sizes
- Network access for integration tests

## 9. Operational Considerations

### 9.1 Bandwidth Management

**Current:** No bandwidth limiting

**User Control:**
- Adjust `general.transfers` in config
- Lower = less bandwidth usage
- Higher = faster sync

**Future Enhancement:**
- Could add `--bwlimit` flag to rclone
- Format: `--bwlimit 10M` (10 MB/s)

### 9.2 B2 Costs

**API Calls:**
- List operations: Cost money (small)
- Download operations: Free
- Upload operations: Free
- Storage: Costs per GB-month

**Cost Optimization:**
- Use `--checksum` (one list per file, but accurate)
- Don't use `--fast-list` (batch list costs same as incremental)
- Infrequent syncing reduces list operations

**Version Management:**
- B2 keeps versions by default
- Lifecycle rules can clean up old versions
- Not managed by android-sync (user's responsibility)

### 9.3 Hidden Files in B2

**What are hidden files:**
- B2's version lifecycle feature
- Files marked as "hidden" instead of deleted
- Don't appear in normal listings
- Accessible via version history
- Can be cleaned up later

**Why hide instead of delete:**
- Preserves file history
- Allows recovery from accidental deletion
- Separates sync logic from cleanup logic
- Safer default behavior

**How to clean up:**
- Use B2 lifecycle rules
- Use separate cleanup script
- Manual cleanup via B2 web UI
- Not handled by android-sync

**Cost implication:**
- Hidden files count toward storage
- User should configure lifecycle rules
- Recommendation: Delete hidden files after 30-90 days

## 10. Security Considerations

### 10.1 Credential Exposure

**Protected:**
- Credentials in environment variables (not command args)
- Never logged
- Not in process list
- Short-lived in memory

**See Also:**
- `specs/security-keystore.md` for credential storage
- Credentials retrieved from encrypted GPG file
- Passed to sync via environment

### 10.2 Data in Transit

**TLS/SSL:**
- B2 API uses HTTPS
- All transfers encrypted in transit
- Handled by rclone and B2

**No Additional Encryption:**
- Files stored in plaintext on B2
- User's responsibility to encrypt sensitive files before sync
- Could use rclone's `--crypt` remote (future enhancement)

### 10.3 Data at Rest

**B2 Storage:**
- B2 encrypts data at rest (AES-256)
- B2's responsibility
- Transparent to android-sync

**Local Files:**
- Not encrypted by android-sync
- Android's filesystem encryption (if enabled)
- User's responsibility

## 11. Future Enhancements (Out of Scope)

- Bidirectional sync (cloud to device)
- Real-time file watching (inotify)
- Progress persistence across interruptions
- Bandwidth limiting (`--bwlimit`)
- Client-side encryption (`--crypt` remote)
- Byte count in SyncResult
- Parallel source processing
- More detailed statistics (per-file progress)
- Retry configuration (timeout, max retries)
- Alternative backends (S3, GCS, etc.)

## 12. References

- [rclone Documentation](https://rclone.org/docs/)
- [rclone B2 Backend](https://rclone.org/b2/)
- [Backblaze B2 API](https://www.backblaze.com/b2/docs/)
- [B2 File Versioning](https://www.backblaze.com/b2/docs/file_versions.html)
- [rclone Filtering](https://rclone.org/filtering/)
