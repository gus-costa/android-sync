# Android Sync

Backup files from Android (Termux) to Backblaze B2 using rclone.

## Features

- One-way sync from device to B2 (no deletes, preserves versions)
- Configurable sync profiles for different file types
- Schedules to group profiles for batch execution
- Secure credential storage via termux-keystore
- Automatic detection and hiding of removed files
- Dry-run mode for previewing changes
- File and stdout logging with retention management

## Requirements

- Python 3.11+
- [rclone](https://rclone.org/) installed and in PATH
- [Termux](https://termux.dev/) with [Termux:API](https://wiki.termux.com/wiki/Termux:API)
- Backblaze B2 account

## Installation

```bash
# Install dependencies in Termux
pkg install python rclone termux-api

# Clone and install
git clone https://github.com/user/android-sync.git
cd android-sync
pip install .
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run android-sync --help
```

## Setup

### 1. Store B2 credentials in keystore

```bash
# Get your B2 credentials from https://www.backblaze.com/
termux-keystore set b2-key-id
# Enter your B2 application key ID

termux-keystore set b2-app-key
# Enter your B2 application key
```

### 2. Create configuration

```bash
mkdir -p ~/.config/android-sync
cp config.example.toml ~/.config/android-sync/config.toml
# Edit config.toml with your settings
```

### 3. Test with dry-run

```bash
android-sync run --all --dry-run
```

## Usage

```bash
# Run a schedule
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
```

### Keystore Keys

```toml
[keystore]
b2_key_id = "b2-key-id"          # Keystore key name for B2 key ID
b2_app_key = "b2-app-key"        # Keystore key name for B2 app key
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

Schedules group profiles:

```toml
[schedules.daily]
profiles = ["photos", "documents"]

[schedules.hourly]
profiles = ["photos"]
```

## Scheduling with Termux

Use `crond` or `termux-job-scheduler` to run backups automatically:

### Using crond

```bash
pkg install cronie termux-services
sv-enable crond

# Edit crontab
crontab -e
```

Add entries:

```cron
# Run daily backup at 2 AM
0 2 * * * android-sync run daily

# Run photos backup every hour
0 * * * * android-sync run hourly
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
