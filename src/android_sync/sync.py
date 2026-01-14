"""Sync logic using rclone for Backblaze B2."""

import logging
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Profile
from .keystore import B2Credentials

logger = logging.getLogger("android_sync.sync")


class SyncError(Exception):
    """Error during sync operation."""


def _b2_remote(bucket: str, path: str) -> str:
    """Build a B2 remote string.

    Returns format: :b2:bucket/path
    """
    return f":b2:{bucket}/{path}"


def _rclone_env(credentials: B2Credentials) -> dict[str, str]:
    """Build environment variables for rclone with B2 credentials.

    Using env vars prevents credentials from appearing in:
    - Process list (ps aux)
    - Shell history
    - Log files
    """
    import os

    env = os.environ.copy()
    env["RCLONE_B2_ACCOUNT"] = credentials.key_id
    env["RCLONE_B2_KEY"] = credentials.app_key
    return env


@dataclass
class SyncResult:
    """Result of a sync operation."""

    profile_name: str
    success: bool
    files_transferred: int
    bytes_transferred: int
    hidden_files: list[str]
    error: str | None = None
    # For dry-run summaries
    files_by_directory: dict[str, int] = field(default_factory=dict)
    hidden_by_directory: dict[str, int] = field(default_factory=dict)


def sync_profile(
    profile: Profile,
    bucket: str,
    credentials: B2Credentials,
    transfers: int = 4,
    dry_run: bool = False,
) -> SyncResult:
    """Sync a profile to B2.

    Args:
        profile: Profile configuration.
        bucket: B2 bucket name.
        credentials: B2 credentials.
        transfers: Number of parallel transfers.
        dry_run: If True, show what would be done without making changes.

    Returns:
        SyncResult with operation details.
    """
    logger.info("Syncing profile: %s", profile.name)

    all_transfers: list[str] = []
    all_deletes: list[str] = []
    env = _rclone_env(credentials)

    for source in profile.sources:
        source_path = Path(source)
        if not source_path.exists():
            logger.warning("Source path does not exist: %s", source)
            continue

        # Determine relative path structure in bucket
        # e.g., /storage/emulated/0/DCIM/Camera -> bucket/photos/DCIM/Camera
        relative_dest = f"{profile.destination}/{source_path.name}"
        dest = _b2_remote(bucket, relative_dest)

        cmd = _build_rclone_cmd(
            source=source,
            dest=dest,
            exclude=profile.exclude,
            transfers=transfers,
            dry_run=dry_run,
            sync_deletes=profile.track_removals,
        )

        logger.debug("Running: %s", " ".join(cmd))

        try:
            if dry_run:
                # Capture output for dry-run summary
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )
                transfers_list, deletes_list = _parse_dry_run_output(result.stderr)
                all_transfers.extend(transfers_list)
                all_deletes.extend(deletes_list)
            else:
                # Show progress in real-time
                subprocess.run(
                    cmd,
                    stderr=None,  # Let stderr go to terminal for progress
                    check=True,
                    env=env,
                )

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            logger.error("Sync failed for %s: %s", source, error_msg)
            return SyncResult(
                profile_name=profile.name,
                success=False,
                files_transferred=0,
                bytes_transferred=0,
                hidden_files=[],
                error=error_msg,
            )

    # Show summary
    files_by_dir: dict[str, int] = {}
    hidden_by_dir: dict[str, int] = {}
    if dry_run:
        files_by_dir = _group_by_directory(all_transfers)
        hidden_by_dir = _group_by_directory(all_deletes)
        _print_dry_run_summary(profile.name, files_by_dir, hidden_by_dir)
    else:
        logger.info("Profile %s complete", profile.name)

    return SyncResult(
        profile_name=profile.name,
        success=True,
        files_transferred=len(all_transfers),
        bytes_transferred=0,
        hidden_files=all_deletes,
        files_by_directory=files_by_dir,
        hidden_by_directory=hidden_by_dir,
    )


def _build_rclone_cmd(
    source: str,
    dest: str,
    exclude: list[str],
    transfers: int,
    dry_run: bool,
    sync_deletes: bool,
) -> list[str]:
    """Build the rclone command.

    Args:
        sync_deletes: If True, use 'sync' (deletes remote files not in source).
                      If False, use 'copy' (only adds/updates, never deletes).
    """
    operation = "sync" if sync_deletes else "copy"
    cmd = [
        "rclone",
        operation,
        source,
        dest,
        "--transfers",
        str(transfers),
        "--checksum",
        "-v",
    ]

    for pattern in exclude:
        cmd.extend(["--exclude", pattern])

    if dry_run:
        cmd.append("--dry-run")
    else:
        # Show progress bar for actual transfers
        cmd.append("--progress")

    return cmd


def _parse_dry_run_output(output: str) -> tuple[list[str], list[str]]:
    """Extract file paths from rclone dry-run output.

    rclone outputs lines like:
    NOTICE: path/to/file.jpg: Skipped copy as --dry-run is set
    NOTICE: path/to/file.jpg: Skipped delete as --dry-run is set

    Returns:
        Tuple of (files_to_transfer, files_to_delete)
    """
    transfers = []
    deletes = []

    transfer_pattern = re.compile(r"NOTICE: (.+): Skipped (?:copy|update)")
    delete_pattern = re.compile(r"NOTICE: (.+): Skipped delete")

    for line in output.splitlines():
        match = transfer_pattern.search(line)
        if match:
            transfers.append(match.group(1))
            continue
        match = delete_pattern.search(line)
        if match:
            deletes.append(match.group(1))

    return transfers, deletes


def _group_by_directory(files: list[str], depth: int = 1) -> dict[str, int]:
    """Group files by their top-level directory.

    Args:
        files: List of file paths.
        depth: Directory depth to group by (1 = top-level).

    Returns:
        Dict mapping directory name to file count.
    """
    counts: dict[str, int] = defaultdict(int)

    for file_path in files:
        parts = Path(file_path).parts
        if len(parts) >= depth:
            # Use the first N parts as the directory key
            dir_key = str(Path(*parts[:depth]))
        else:
            dir_key = str(Path(*parts[:-1])) if len(parts) > 1 else "."
        counts[dir_key] += 1

    return dict(sorted(counts.items()))


def _print_dry_run_summary(
    profile_name: str,
    files_by_dir: dict[str, int],
    hidden_by_dir: dict[str, int],
) -> None:
    """Print a formatted dry-run summary."""
    total_files = sum(files_by_dir.values())
    total_hidden = sum(hidden_by_dir.values())

    logger.info("=" * 50)
    logger.info("Dry-run summary for profile '%s'", profile_name)
    logger.info("=" * 50)

    if total_files > 0:
        logger.info("Files to transfer: %d", total_files)
        for dir_name, count in files_by_dir.items():
            logger.info("  %s: %d files", dir_name, count)
    else:
        logger.info("Files to transfer: 0 (already synced)")

    if total_hidden > 0:
        logger.info("Files to delete: %d", total_hidden)
        for dir_name, count in hidden_by_dir.items():
            logger.info("  %s: %d files", dir_name, count)

    logger.info("=" * 50)


