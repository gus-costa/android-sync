# Configuration Schema Specification

**Version:** 1.0
**Date:** 2026-01-20
**Status:** Draft

## 1. Overview

This specification defines the configuration file format for android-sync, including all fields, types, validation rules, and defaults.

### 1.1 Goals

- Single source of truth for all configuration
- Type-safe parsing with clear error messages
- Reasonable defaults for optional fields
- Validation at load time (fail fast)
- Clear documentation of all options

### 1.2 Non-Goals

- Runtime configuration changes (config is immutable after load)
- Configuration file validation tool (could be added later)
- Configuration migration tools
- Multiple config file merging (single file only)
- Environment variable overrides

## 2. File Format

### 2.1 Format: TOML

**File Format:** TOML (Tom's Obvious, Minimal Language)

**Version:** TOML 1.0.0

**Parser:** Python's `tomllib` (standard library, Python 3.11+)

**Why TOML:**
- Human-readable and writable
- Clear structure for nested data
- Type-safe (distinguishes strings, integers, arrays, tables)
- Standard library support (no dependencies)
- Better than JSON for config files (supports comments)
- Simpler than YAML (no complex edge cases)

**Example:**
```toml
[general]
bucket = "my-bucket"      # String
transfers = 4             # Integer
log_retention_days = 7    # Integer

[profiles.photos]         # Table
sources = ["/path1", "/path2"]  # Array of strings
```

### 2.2 File Location

**Default Location:**
```
~/.config/android-sync/config.toml
```

**XDG Compliance:**
- Follows XDG Base Directory Specification
- `$XDG_CONFIG_HOME/android-sync/config.toml` if `XDG_CONFIG_HOME` is set
- Falls back to `~/.config/android-sync/config.toml`

**Override:**
```bash
android-sync --config /path/to/config.toml run daily
```

**Global Flag:**
- `--config PATH` available on all commands
- Must be absolute or relative to current directory
- Expanded before use (tilde and environment variables)

**File Permissions:**
- No special permission requirements
- Typically 0644 (user read/write, group/other read)
- Contains no sensitive data (credentials in separate file)

### 2.3 Character Encoding

**Encoding:** UTF-8 (required by TOML spec)

**Line Endings:** Unix (LF) or Windows (CRLF) - TOML supports both

## 3. Configuration Structure

### 3.1 Top-Level Sections

```toml
[general]       # Global settings (required)
[profiles.*]    # Sync profiles (optional, but typical)
[schedules.*]   # Schedule definitions (optional)
```

**Section Meanings:**
- **general**: Global settings applying to all profiles
- **profiles**: Named sync profile definitions
- **schedules**: Named schedule definitions grouping profiles

**Order Independence:**
- Sections can appear in any order
- Profiles and schedules are dictionaries (unordered)
- References validated after loading entire file

## 4. General Section

### 4.1 Schema

```toml
[general]
bucket = "string"                    # REQUIRED
log_dir = "path"                     # OPTIONAL (has default)
log_retention_days = integer         # OPTIONAL (default: 7)
secrets_file = "path"                # OPTIONAL (has default)
transfers = integer                  # OPTIONAL (default: 4)
stale_job_timeout_hours = integer    # OPTIONAL (default: 24)
```

### 4.2 Field Definitions

#### bucket (REQUIRED)

**Type:** String

**Description:** Backblaze B2 bucket name

**Validation:**
- Must be present (raises `ConfigError` if missing)
- No format validation (B2 API will reject invalid names)
- Typically lowercase, alphanumeric with hyphens

**Example:**
```toml
bucket = "my-backup-bucket"
```

**Notes:**
- Bucket must exist before running sync
- User must have write permission to bucket
- All profiles sync to this bucket (different paths within)

#### log_dir (OPTIONAL)

**Type:** String (path)

**Default:** `"/data/data/com.termux/files/home/logs"`

**Description:** Directory for log files

**Validation:**
- Converted to `Path` object
- Created if doesn't exist (mkdir -p)
- Parent directory must be writable

**Example:**
```toml
log_dir = "/data/data/com.termux/files/home/logs/android-sync"
```

**Notes:**
- Can use tilde expansion (`~/logs/android-sync`)
- Can use environment variables (via Path expansion)
- Logs are named `android-sync-YYYYMMDD-HHMMSS.log`

#### log_retention_days (OPTIONAL)

**Type:** Integer

**Default:** `7`

**Range:** `>= 0`

**Description:** Number of days to keep log files

**Validation:**
- Must be non-negative integer
- `0` disables cleanup (keeps all logs)

**Example:**
```toml
log_retention_days = 30
```

**Behavior:**
- Cleanup runs at start of each logged command
- Deletes logs older than N days (based on file mtime)
- See `specs/logging-system.md` for details

**Recommendations:**
- Mobile devices: 7-14 days (limited storage)
- Desktop/server: 30-90 days
- Debugging: 0 (disable cleanup)

#### secrets_file (OPTIONAL)

**Type:** String (path)

**Default:** `~/.local/share/android-sync/secrets.gpg`

**Description:** Path to GPG-encrypted secrets file

**Validation:**
- Converted to `Path` object
- File must exist before running sync (not during setup)
- Must be readable by user

**Example:**
```toml
secrets_file = "/custom/path/secrets.gpg"
```

**Notes:**
- XDG-compliant default location
- File created by `android-sync setup` command
- Contains B2 credentials encrypted with keystore-derived passphrase
- See `specs/security-keystore.md` for details

**Why customizable:**
- Multiple installations (different configs, different secrets)
- Custom backup locations
- Testing with different credentials

#### transfers (OPTIONAL)

**Type:** Integer

**Default:** `4`

**Range:** `>= 1` (no hard maximum, but practical limits apply)

**Description:** Number of parallel file transfers for rclone

**Validation:**
- Must be positive integer
- No explicit upper bound (rclone limits apply)

**Example:**
```toml
transfers = 8
```

**Trade-offs:**
- **Higher values:**
  - Pros: Faster sync, better bandwidth utilization
  - Cons: More memory usage, more API calls, may hit rate limits
- **Lower values:**
  - Pros: Less memory, gentler on API, more stable
  - Cons: Slower sync

**Recommendations:**
- Mobile device (3G/4G): 2-4
- Mobile device (WiFi): 4-8
- Fast connection: 8-16
- Slow connection: 1-2

**Effect:**
- Passed to rclone as `--transfers N`
- Applies to all profiles
- Per-source parallelism (sources are sequential)

#### stale_job_timeout_hours (OPTIONAL)

**Type:** Integer

**Default:** `24`

**Range:** `1-168` (1 hour to 1 week)

**Description:** Maximum hours a scheduled job can run before being killed

**Validation:**
- Must be in range 1-168
- Enforced at config load time

**Example:**
```toml
stale_job_timeout_hours = 48
```

**Behavior:**
- Applies only to scheduled jobs (not manual runs)
- Check runs during `android-sync check` command
- Stale jobs are killed with SIGTERM
- See `specs/scheduling.md` for details

**Recommendations:**
- Fast network, small data: 6-12 hours
- Slow network, large data: 24-48 hours
- Very large data: 48-72 hours
- Maximum: 168 hours (1 week)

## 5. Profiles Section

### 5.1 Schema

```toml
[profiles.<name>]
sources = ["path1", "path2", ...]   # REQUIRED
destination = "string"               # REQUIRED
exclude = ["pattern1", ...]          # OPTIONAL (default: [])
track_removals = boolean             # OPTIONAL (default: true)
```

**Profile Naming:**
- Name is the table key: `[profiles.photos]` → name is "photos"
- Must be unique within config
- Used in schedules to reference profile
- No naming restrictions (use valid TOML keys)

### 5.2 Field Definitions

#### sources (REQUIRED)

**Type:** Array of strings (paths)

**Description:** Local directories to sync to B2

**Validation:**
- Must be present
- Must be non-empty array
- Each path should exist (warning if missing, not error)

**Example:**
```toml
sources = [
    "/storage/emulated/0/DCIM/Camera",
    "/storage/emulated/0/Pictures",
]
```

**Behavior:**
- Each source synced independently
- Sources processed sequentially (one at a time)
- Missing sources logged as warning, not error
- Each source creates subdirectory in destination

**Path Handling:**
- Absolute paths recommended
- Relative paths resolved from current working directory
- Tilde expansion supported
- Environment variables supported

**Directory Structure:**
- Source: `/storage/emulated/0/DCIM`
- Destination: `photos`
- Remote path: `bucket/photos/DCIM/`
- Preserves source directory name

#### destination (REQUIRED)

**Type:** String (path within bucket)

**Description:** Prefix path within B2 bucket

**Validation:**
- Must be present
- Can be empty string (sync to bucket root)
- No leading or trailing slashes needed

**Example:**
```toml
destination = "photos"
```

**Behavior:**
- Combined with source directory name
- Full path: `bucket/{destination}/{source_name}/`

**Examples:**

| destination | source | Remote path |
|-------------|--------|-------------|
| `"photos"` | `/sdcard/DCIM` | `bucket/photos/DCIM/` |
| `""` | `/sdcard/DCIM` | `bucket/DCIM/` |
| `"backup/photos"` | `/sdcard/DCIM` | `bucket/backup/photos/DCIM/` |

**Multiple Sources:**
- All sources in same profile go to same destination prefix
- Each source gets its own subdirectory
- No collisions if source names are unique

#### exclude (OPTIONAL)

**Type:** Array of strings (glob patterns)

**Default:** `[]` (no exclusions)

**Description:** File patterns to exclude from sync

**Validation:**
- Must be array of strings
- Patterns are rclone glob patterns

**Example:**
```toml
exclude = [
    "*.tmp",
    ".thumbnails",
    "*.pending",
    "~$*",
]
```

**Pattern Syntax:**
- `*` matches any characters except `/`
- `**` matches any characters including `/`
- `?` matches single character
- `[abc]` matches any character in set
- `{a,b}` matches either a or b

**Common Patterns:**
- `*.tmp` - Temporary files
- `.thumbnails` - Thumbnail cache
- `*.bak` - Backup files
- `~$*` - Office temp files
- `.DS_Store` - macOS metadata
- `Thumbs.db` - Windows thumbnails

**Behavior:**
- Each pattern passed to rclone as `--exclude`
- Applies to all sources in profile
- Case-sensitive (depends on filesystem)

**Performance:**
- Many exclude patterns can slow down sync
- rclone checks each file against each pattern
- Keep exclude list minimal

#### track_removals (OPTIONAL)

**Type:** Boolean

**Default:** `true`

**Description:** Whether to mark removed files as hidden in B2

**Validation:**
- Must be boolean (`true` or `false`)

**Example:**
```toml
track_removals = true
```

**Behavior:**
- `true`: Use `rclone sync` (deletes remote files not in source)
- `false`: Use `rclone copy` (only adds/updates, never deletes)

**When to use `true`:**
- Want exact mirror of local files
- Deleted local files should be removed from backup
- Default behavior (safer)

**When to use `false`:**
- Want accumulation (never delete from cloud)
- Paranoid about accidental local deletions
- Separate cleanup process

**Hidden Files:**
- Deleted files marked as "hidden" (not deleted)
- Don't appear in normal B2 listings
- Accessible via version history
- Cleaned up by B2 lifecycle rules (user's responsibility)

### 5.3 Example Profiles

**Photos Profile:**
```toml
[profiles.photos]
sources = [
    "/storage/emulated/0/DCIM/Camera",
    "/storage/emulated/0/Pictures",
]
destination = "photos"
exclude = ["*.tmp", ".thumbnails"]
track_removals = true
```

**Documents Profile:**
```toml
[profiles.documents]
sources = ["/storage/emulated/0/Documents"]
destination = "documents"
exclude = ["*.bak", "~$*", "*.tmp"]
```

**Everything Profile:**
```toml
[profiles.everything]
sources = [
    "/storage/emulated/0/DCIM",
    "/storage/emulated/0/Pictures",
    "/storage/emulated/0/Documents",
    "/storage/emulated/0/Download",
]
destination = "full-backup"
track_removals = false  # Never delete (safety)
```

## 6. Schedules Section

### 6.1 Schema

```toml
[schedules.<name>]
profiles = ["profile1", "profile2", ...]  # REQUIRED
cron = "cron_expression"                  # OPTIONAL (manual if omitted)
```

**Schedule Naming:**
- Name is the table key: `[schedules.daily]` → name is "daily"
- Must be unique within config
- Used in CLI commands: `android-sync run daily`
- No naming restrictions (use valid TOML keys)

### 6.2 Field Definitions

#### profiles (REQUIRED)

**Type:** Array of strings (profile names)

**Description:** List of profiles to execute in this schedule

**Validation:**
- Must be present
- Must be non-empty array
- Each profile name must exist in `[profiles]` section
- Raises `ConfigError` if unknown profile referenced

**Example:**
```toml
profiles = ["photos", "documents", "downloads"]
```

**Behavior:**
- Profiles executed sequentially in array order
- All profiles must succeed for schedule to succeed
- If any profile fails, schedule fails (but continues to other profiles)
- Individual profile failure doesn't abort schedule execution

**Order:**
- Explicit: profiles executed in listed order
- Useful for dependencies (e.g., sync database before documents)

#### cron (OPTIONAL)

**Type:** String (cron expression)

**Default:** `null` (manual schedule)

**Description:** Cron expression for automatic scheduling

**Validation:**
- Must be valid cron expression (5 fields)
- Validated using `croniter.is_valid()`
- Raises `ConfigError` if invalid

**Format:**
```
minute hour day_of_month month day_of_week
```

**Field Ranges:**
- minute: 0-59
- hour: 0-23
- day_of_month: 1-31
- month: 1-12 (or Jan-Dec)
- day_of_week: 0-6 (0=Sunday, or Mon-Sun)

**Special Characters:**
- `*` - Any value
- `,` - Value list (1,3,5)
- `-` - Range (1-5)
- `/` - Step (*/6 = every 6)

**Examples:**
```toml
cron = "0 3 * * *"       # Daily at 3:00 AM
cron = "0 */6 * * *"     # Every 6 hours
cron = "30 2 * * 0"      # Every Sunday at 2:30 AM
cron = "0 0 1 * *"       # First day of month at midnight
cron = "0 9,15,21 * * *" # Three times daily (9 AM, 3 PM, 9 PM)
```

**Behavior:**
- If present: Schedule is automatic (picked up by `android-sync check`)
- If absent: Schedule is manual (only runs via `android-sync run <name>`)
- Uses system timezone (Android device timezone)
- See `specs/scheduling.md` for details

**Manual Schedules:**
```toml
[schedules.manual_backup]
profiles = ["photos", "documents"]
# No cron field - only runs when explicitly called
```

### 6.3 Example Schedules

**Daily Backup:**
```toml
[schedules.daily]
profiles = ["photos", "documents", "downloads"]
cron = "0 3 * * *"  # 3 AM daily
```

**Frequent Photos:**
```toml
[schedules.photos_frequent]
profiles = ["photos"]
cron = "0 */6 * * *"  # Every 6 hours
```

**Weekly Full Backup:**
```toml
[schedules.weekly]
profiles = ["everything"]
cron = "0 2 * * 0"  # Sunday 2 AM
```

**Manual Backup:**
```toml
[schedules.manual]
profiles = ["photos", "documents"]
# No cron - run via: android-sync run manual
```

## 7. Configuration Loading

### 7.1 Loading Process

**Location:** `src/android_sync/config.py:50-61`

**Steps:**
```
1. Check file exists
2. Open file (binary mode for tomllib)
3. Parse TOML
4. Validate structure
5. Create dataclass objects
6. Return Config object
```

**Error Handling:**
- File not found: `ConfigError` with path
- Invalid TOML: `ConfigError` with parse error
- Missing required field: `ConfigError` with field name
- Invalid reference: `ConfigError` with details

### 7.2 Validation Order

1. **TOML Parsing:** Syntax validation
2. **General Section:** Required field presence
3. **Profiles Section:**
   - Required fields per profile
   - Type validation (arrays, strings, booleans)
4. **Schedules Section:**
   - Required fields per schedule
   - Profile reference validation
   - Cron expression validation
5. **Cross-References:** All profile references exist

### 7.3 Default Value Resolution

**Precedence (highest to lowest):**
1. Explicit value in TOML
2. Dataclass field default
3. Hardcoded constant

**Example:**
```python
log_retention_days = general.get("log_retention_days", 7)
#                                                      ↑
#                                              default value
```

### 7.4 Immutability

**After Loading:**
- Config object is immutable
- No runtime modifications
- Changes require config file edit + restart

**Why Immutable:**
- Simpler reasoning (no state changes)
- Thread-safe (if needed)
- Easier to debug (config matches file)

## 8. Data Model

### 8.1 Dataclasses

**Location:** `src/android_sync/config.py:12-43`

**Classes:**
```python
@dataclass
class Profile:
    name: str
    sources: list[str]
    destination: str
    exclude: list[str] = field(default_factory=list)
    track_removals: bool = True

@dataclass
class Schedule:
    name: str
    profiles: list[str]
    cron: str | None = None

@dataclass
class Config:
    bucket: str
    log_dir: Path
    log_retention_days: int
    secrets_file: Path
    profiles: dict[str, Profile]
    schedules: dict[str, Schedule]
    transfers: int = 4
    stale_job_timeout_hours: int = 24
```

**Why Dataclasses:**
- Type safety (IDE autocomplete, type checkers)
- Automatic `__init__`, `__repr__`, `__eq__`
- Explicit field types
- Default value support
- Immutable (can use frozen=True if needed)

### 8.2 Type Conversions

**String → Path:**
- `log_dir`: `Path(general.get("log_dir", ...))`
- `secrets_file`: `Path(secrets_file_str)`

**String → List:**
- Already list in TOML (arrays)
- No conversion needed

**Dict → Objects:**
- Profiles: `dict[str, Profile]`
- Schedules: `dict[str, Schedule]`
- Keyed by name for fast lookup

## 9. Error Messages

### 9.1 Error Categories

**File Errors:**
```
ConfigError: Config file not found: /path/to/config.toml
```

**Parse Errors:**
```
ConfigError: Invalid TOML: Expected ']' at line 10
```

**Validation Errors:**
```
ConfigError: Missing required field: general.bucket
ConfigError: Missing required field: profiles.photos.sources
ConfigError: Schedule 'daily' references unknown profile: typo
ConfigError: Schedule 'daily' has invalid cron expression: '0 25 * * *'
```

### 9.2 Error Handling Best Practices

**Clear Messages:**
- Always include field path
- Show what was expected
- Show what was found (if applicable)

**Early Failure:**
- Fail at config load time (not during sync)
- Don't start sync with invalid config

**Helpful Errors:**
- Suggest fixes when possible
- Reference documentation

**Example:**
```python
if "bucket" not in general:
    raise ConfigError("Missing required field: general.bucket")
```

## 10. Example Configuration

### 10.1 Minimal Configuration

```toml
[general]
bucket = "my-bucket"

[profiles.photos]
sources = ["/storage/emulated/0/DCIM"]
destination = "photos"

[schedules.daily]
profiles = ["photos"]
cron = "0 3 * * *"
```

### 10.2 Complete Configuration

```toml
[general]
bucket = "my-backup-bucket"
log_dir = "/data/data/com.termux/files/home/logs/android-sync"
log_retention_days = 30
secrets_file = "~/.local/share/android-sync/secrets.gpg"
transfers = 8
stale_job_timeout_hours = 48

[profiles.photos]
sources = [
    "/storage/emulated/0/DCIM/Camera",
    "/storage/emulated/0/Pictures",
]
destination = "photos"
exclude = ["*.tmp", ".thumbnails", "*.pending"]
track_removals = true

[profiles.documents]
sources = ["/storage/emulated/0/Documents"]
destination = "documents"
exclude = ["*.bak", "~$*"]
track_removals = true

[profiles.downloads]
sources = ["/storage/emulated/0/Download"]
destination = "downloads"
exclude = ["*.tmp", "*.part", "*.crdownload"]
track_removals = false

[schedules.daily]
profiles = ["photos", "documents"]
cron = "0 3 * * *"

[schedules.frequent_photos]
profiles = ["photos"]
cron = "0 */6 * * *"

[schedules.weekly_full]
profiles = ["photos", "documents", "downloads"]
cron = "0 2 * * 0"

[schedules.manual_backup]
profiles = ["photos", "documents"]
```

## 11. Schema Evolution

### 11.1 Adding New Fields

**Process:**
1. Add field to dataclass with default value
2. Add field to parsing logic with `get(key, default)`
3. Update documentation
4. Old configs continue to work (use defaults)

**Example:**
```python
# Add new field
@dataclass
class Config:
    # ... existing fields ...
    new_field: int = 42  # Default value

# Parse with default
new_field = general.get("new_field", 42)
```

**Backward Compatibility:**
- Always provide defaults for new fields
- Never require new fields in existing sections
- Old configs work without changes

### 11.2 Removing Fields

**Process:**
1. Deprecate field (document as deprecated)
2. Keep parsing but ignore value
3. After grace period, remove from parsing

**Example:**
```python
# Deprecated field - still parse but ignore
deprecated = general.get("old_field")
if deprecated is not None:
    logger.warning("Field 'old_field' is deprecated and ignored")
```

### 11.3 Changing Field Types

**Not Supported:**
- Breaking change
- Requires migration tool
- Avoid if possible

**Alternative:**
- Add new field with new name
- Deprecate old field
- Support both during transition

### 11.4 Versioning

**Current:** No version field in config

**Future:** Could add version field
```toml
[general]
version = "1.0"
```

**Benefits:**
- Explicit schema version
- Enable migrations
- Clear deprecation path

## 12. Testing

### 12.1 Test Coverage

**Tests Location:** `tests/test_config.py` (290 lines)

**Test Cases:**
1. Load valid config
2. Missing config file
3. Invalid TOML syntax
4. Missing required fields
5. Unknown profile reference
6. Invalid cron expression
7. Default value resolution
8. Path expansion
9. Type validation
10. Edge cases (empty arrays, null values)

### 12.2 Test Fixtures

**Fixture Files:**
- `tests/fixtures/valid_config.toml`
- `tests/fixtures/minimal_config.toml`
- `tests/fixtures/invalid_toml.toml`
- `tests/fixtures/missing_required.toml`

## 13. Operational Considerations

### 13.1 Config File Management

**Location:**
- Store in version control (without secrets)
- Keep separate from secrets file
- Use different configs for different devices

**Backup:**
- Config file is plain text (easy to backup)
- Can be reconstructed from documentation
- Secrets file is the critical backup

**Sharing:**
- Safe to share (no credentials)
- Can use as template for other devices
- Document any device-specific paths

### 13.2 Troubleshooting

**Invalid Config:**
```bash
android-sync --config config.toml run daily
# Error: ConfigError: Missing required field: general.bucket
```

**Unknown Profile:**
```bash
android-sync run typo
# Error: Unknown profile: typo
```

**Invalid Cron:**
```bash
# Error: Schedule 'daily' has invalid cron expression: '0 25 * * *'
```

## 14. Future Enhancements (Out of Scope)

- Config file validation tool (`android-sync validate-config`)
- Config migration tool (schema v1 → v2)
- Environment variable overrides (`ANDROID_SYNC_BUCKET`)
- Multiple config file merging (system + user)
- Config file generation wizard
- JSON schema for IDE autocomplete
- Profile inheritance (base profile + overrides)
- Schedule priorities (which schedule runs first)
- Per-profile transfer limits
- Global exclude patterns

## 15. References

- [TOML Specification](https://toml.io/en/)
- [Python tomllib Documentation](https://docs.python.org/3/library/tomllib.html)
- [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html)
- [Cron Expression Format](https://crontab.guru/)
- [croniter Library](https://github.com/pallets-eco/croniter)
