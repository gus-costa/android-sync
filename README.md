# Android Sync

Backup files from Android (Termux) to Backblaze B2 using rclone.

## Features

- One-way sync from device to B2 (no deletes, preserves versions)
- Configurable sync profiles for different file types
- Automatic time-based scheduling with cron expressions
- Schedules to group profiles for batch execution
- Secure credential storage using Android Keystore + GPG
- Automatic detection and hiding of removed files
- Dry-run mode for previewing changes
- File and stdout logging with retention management
- Robust failure handling with automatic retries

## Requirements

- Python 3.11+
- [rclone](https://rclone.org/) installed and in PATH
- [Termux](https://termux.dev/) with [Termux:API](https://wiki.termux.com/wiki/Termux:API)
- GPG (gnupg)
- Backblaze B2 account

## Installation (Termux)

### Quick setup

```bash
git clone https://github.com/user/android-sync.git
cd android-sync
bash scripts/termux-init.sh
```

### Manual installation

```bash
pkg install python rclone termux-api gnupg
git clone https://github.com/user/android-sync.git
cd android-sync
pip install .
```

## Setup

### 1. Initialize credentials

The setup command generates a signing key in Android Keystore, encrypts your B2 credentials, and configures automatic scheduling:

```bash
android-sync setup
# Prompts for Key ID and Application Key interactively
```

This creates:
- A non-exportable RSA key in Android Keystore (`android-sync`)
- An encrypted secrets file at `~/.local/share/android-sync/secrets.gpg`
- State directory and scheduler configuration

**Note:** The setup command is idempotent and safe to run multiple times. If credentials already exist, it will skip the credential setup and only configure scheduling. To change credentials, use the `--force` flag.
```bash
android-sync setup --force
```

### 2. Create configuration

```bash
mkdir -p ~/.config/android-sync
cp config.example.toml ~/.config/android-sync/config.toml
# Edit config.toml with your settings (bucket name, profiles, etc.)
```

### 3. Test with dry-run

```bash
android-sync run --all --dry-run
```

## Usage

### Running Backups

```bash
# Run a schedule (manual or automatic)
android-sync run daily

# Run a single profile
android-sync run --profile photos

# Run all profiles
android-sync run --all

# Preview changes without syncing
android-sync run daily --dry-run

# Verbose logging
android-sync run daily --verbose

# Use custom config file
android-sync --config /path/to/config.toml run daily
```

### Scheduling Commands

```bash
# Check status of all schedules
android-sync status

# Reset a failed or stale schedule
android-sync reset daily

# Manually check for overdue schedules (normally runs automatically)
android-sync check
```

### Listing

```bash
# List profiles and schedules
android-sync list profiles
android-sync list schedules
```

## Configuration

Configuration is stored in TOML format at `~/.config/android-sync/config.toml`.

### General Settings

```toml
[general]
bucket = "my-backup-bucket"      # B2 bucket name
log_dir = "/path/to/logs"        # Log directory
log_retention_days = 30          # Days to keep logs
transfers = 4                    # Parallel transfers
stale_job_timeout_hours = 24     # Max hours before job is killed (default: 24)
secrets_file = "/path/to/secrets.gpg"  # Optional, defaults to ~/.local/share/android-sync/secrets.gpg
```

### Profiles

Profiles define what to backup:

```toml
[profiles.photos]
sources = [                      # Local directories to sync
    "/storage/emulated/0/DCIM",
    "/storage/emulated/0/Pictures",
]
destination = "photos"           # Subfolder in bucket
exclude = ["*.tmp", ".thumbnails"]  # Patterns to exclude
track_removals = true            # Hide removed files in B2
```

### Schedules

Schedules group profiles for execution. Add a `cron` field for automatic scheduling:

```toml
[schedules.daily]
profiles = ["photos", "documents"]
cron = "0 3 * * *"  # Daily at 3 AM (automatic)

[schedules.manual_backup]
profiles = ["photos"]
# No cron field - manual execution only
```

See the [Automatic Scheduling](#automatic-scheduling) section for more details on cron expressions.

## Security Model

Credentials are protected using a hardware-backed key derivation scheme:

1. **Android Keystore** stores a non-exportable RSA signing key
2. A fixed message is signed to derive a deterministic passphrase
3. The passphrase encrypts/decrypts a GPG secrets file containing B2 credentials

This provides separation between the encryption key (in hardware) and encrypted data (on disk). The private key never leaves the Android Keystore.

## Automatic Scheduling

Android Sync includes built-in time-based scheduling that uses Android's JobScheduler for reliable, unattended execution.

### Setup

During initial setup, scheduling is automatically configured:

```bash
android-sync setup
```

This creates a check script and registers it with `termux-job-scheduler` to run every 15 minutes. The system persists across device reboots.

### Scheduled vs Manual Schedules

Schedules can be configured in two ways:

**Scheduled (with cron expression):**
```toml
[schedules.daily]
profiles = ["photos", "documents"]
cron = "0 3 * * *"  # Runs automatically at 3 AM daily
```

**Manual (without cron expression):**
```toml
[schedules.manual_backup]
profiles = ["photos", "documents"]
# No cron field - only runs when explicitly called
```

### Cron Expression Format

Cron expressions use 5 fields: `minute hour day_of_month month day_of_week`

Common examples:
- `0 3 * * *` - Daily at 3:00 AM
- `0 */6 * * *` - Every 6 hours (0:00, 6:00, 12:00, 18:00)
- `30 2 * * 0` - Every Sunday at 2:30 AM
- `0 0 1 * *` - First day of month at midnight
- `0 9,15,21 * * *` - Three times daily (9 AM, 3 PM, 9 PM)

### Monitoring Schedules

Check the status of all schedules:

```bash
android-sync status
```

Output shows:
- Schedule type (Scheduled/Manual)
- Current status (pending/running/success/failed)
- Last run and next scheduled run times
- Whether a schedule is overdue
- PID of running jobs

### Managing Schedules

Reset a failed or stale schedule:

```bash
android-sync reset <schedule_name>
```

Manually trigger any schedule (scheduled or manual):

```bash
android-sync run <schedule_name>
```

Check for overdue schedules (normally called automatically):

```bash
android-sync check
```

### How It Works

1. **Every 15 minutes**, Android JobScheduler triggers a check
2. The check examines all schedules with cron expressions
3. If any are overdue, the **most overdue** schedule is executed
4. Jobs run in the background, independent of the check process
5. State is tracked in `~/.local/share/android-sync/state/`

### Failure Handling and Retries

When a scheduled job fails:
- Status is marked as "failed"
- The next scheduled time remains unchanged
- The job will **retry at its next scheduled time** (no immediate retry)
- This prevents retry storms and provides natural backoff

For example, an hourly schedule that fails will retry in 1 hour. A daily schedule will retry the next day.

### Stale Job Detection

If a job runs longer than the configured timeout (default: 24 hours):
- The process is killed with SIGTERM
- Status is marked as failed
- The job will retry at its next scheduled time

Configure timeout in `config.toml`:

```toml
[general]
stale_job_timeout_hours = 24  # Default: 24, range: 1-168
```

### Priority Selection

When multiple schedules are overdue:
- The schedule with the highest overdue time is selected first
- This naturally prioritizes daily schedules over weekly schedules
- Only one schedule executes at a time

### Troubleshooting

**Check if the job scheduler is registered:**

```bash
termux-job-scheduler list
```

**Common issues:**

- **Missing termux-api**: Install with `pkg install termux-api`
- **Job not running**: Check device battery optimization settings for Termux
- **Failed schedules**: Use `android-sync status` to see failure details, then `android-sync reset <schedule>` to retry
- **Stale jobs**: Check logs in the configured log directory

**Manual re-registration (if needed):**

```bash
termux-job-scheduler schedule \
  --script ~/.local/share/android-sync/check-schedule.sh \
  --period-ms 900000 \
  --persisted true
```

## Removed File Handling

When `track_removals = true` (default), files deleted from the device are marked as "hidden" in B2 rather than deleted. This:

- Preserves file history in B2
- Allows a separate cleanup script to manage deletions
- Prevents accidental data loss

Hidden files don't appear in normal B2 listings but remain accessible via version history.

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```

## License

MIT
