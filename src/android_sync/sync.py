"""Sync logic using rclone for Backblaze B2."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Profile
from .keystore import B2Credentials

logger = logging.getLogger("android_sync.sync")


class SyncError(Exception):
    """Error during sync operation."""


def _b2_remote(bucket: str, path: str) -> str:
    """Build a B2 remote string.

    Returns format: :b2:bucket/path
    Credentials are passed via environment variables to avoid leaking in logs/process list.
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

    total_transferred = 0
    total_bytes = 0
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

        cmd = _build_rclone_copy_cmd(
            source=source,
            dest=dest,
            exclude=profile.exclude,
            transfers=transfers,
            dry_run=dry_run,
        )

        logger.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            logger.debug("rclone output: %s", result.stdout)

            # Parse stats from output (simplified)
            stats = _parse_rclone_stats(result.stdout)
            total_transferred += stats.get("files", 0)
            total_bytes += stats.get("bytes", 0)

        except subprocess.CalledProcessError as e:
            logger.error("Sync failed for %s: %s", source, e.stderr)
            return SyncResult(
                profile_name=profile.name,
                success=False,
                files_transferred=total_transferred,
                bytes_transferred=total_bytes,
                hidden_files=[],
                error=e.stderr,
            )

    # Handle removed files
    hidden_files: list[str] = []
    if profile.track_removals and not dry_run:
        hidden_files = _hide_removed_files(profile, bucket, credentials)
    elif profile.track_removals and dry_run:
        hidden_files = _detect_removed_files(profile, bucket, credentials)
        if hidden_files:
            logger.info("Would hide %d removed files (dry-run)", len(hidden_files))

    logger.info(
        "Profile %s complete: %d files, %d bytes transferred",
        profile.name,
        total_transferred,
        total_bytes,
    )

    return SyncResult(
        profile_name=profile.name,
        success=True,
        files_transferred=total_transferred,
        bytes_transferred=total_bytes,
        hidden_files=hidden_files,
    )


def _build_rclone_copy_cmd(
    source: str,
    dest: str,
    exclude: list[str],
    transfers: int,
    dry_run: bool,
) -> list[str]:
    """Build the rclone copy command."""
    cmd = [
        "rclone",
        "copy",
        source,
        dest,
        "--transfers",
        str(transfers),
        "--stats-one-line",
        "-v",
    ]

    for pattern in exclude:
        cmd.extend(["--exclude", pattern])

    if dry_run:
        cmd.append("--dry-run")

    return cmd


def _parse_rclone_stats(output: str) -> dict:
    """Parse basic stats from rclone output."""
    # This is simplified - rclone's JSON output would be more reliable
    # but requires --use-json-log flag
    stats = {"files": 0, "bytes": 0}

    for line in output.splitlines():
        if "Transferred:" in line:
            # Try to extract numbers (very basic parsing)
            parts = line.split()
            for i, part in enumerate(parts):
                if part.isdigit():
                    stats["files"] = int(part)
                    break

    return stats


def _list_remote_files(
    profile: Profile,
    bucket: str,
    credentials: B2Credentials,
) -> set[str]:
    """List all files in the remote destination."""
    remote_files: set[str] = set()
    env = _rclone_env(credentials)

    for source in profile.sources:
        source_path = Path(source)
        relative_dest = f"{profile.destination}/{source_path.name}"
        remote = _b2_remote(bucket, relative_dest)

        cmd = [
            "rclone",
            "lsf",
            remote,
            "--recursive",
            "--files-only",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
            for line in result.stdout.strip().splitlines():
                if line:
                    # Store as relative path from destination
                    remote_files.add(f"{source_path.name}/{line}")
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to list remote files for %s: %s", relative_dest, e.stderr)

    return remote_files


def _list_local_files(profile: Profile) -> set[str]:
    """List all local files in source directories."""
    local_files: set[str] = set()

    for source in profile.sources:
        source_path = Path(source)
        if not source_path.exists():
            continue

        for file_path in source_path.rglob("*"):
            if file_path.is_file():
                # Check exclude patterns
                if _should_exclude(file_path, profile.exclude):
                    continue
                # Store as relative path
                relative = f"{source_path.name}/{file_path.relative_to(source_path)}"
                local_files.add(relative)

    return local_files


def _should_exclude(file_path: Path, patterns: list[str]) -> bool:
    """Check if a file matches any exclude pattern."""
    from fnmatch import fnmatch

    name = file_path.name
    for pattern in patterns:
        if fnmatch(name, pattern):
            return True
    return False


def _detect_removed_files(
    profile: Profile,
    bucket: str,
    credentials: B2Credentials,
) -> list[str]:
    """Detect files that exist in remote but not locally."""
    remote_files = _list_remote_files(profile, bucket, credentials)
    local_files = _list_local_files(profile)

    removed = remote_files - local_files
    return sorted(removed)


def _hide_removed_files(
    profile: Profile,
    bucket: str,
    credentials: B2Credentials,
) -> list[str]:
    """Hide files in B2 that no longer exist locally."""
    removed = _detect_removed_files(profile, bucket, credentials)

    if not removed:
        return []

    logger.info("Hiding %d removed files in B2", len(removed))
    env = _rclone_env(credentials)

    hidden: list[str] = []
    for relative_path in removed:
        remote_path = _b2_remote(bucket, f"{profile.destination}/{relative_path}")

        cmd = [
            "rclone",
            "backend",
            "hide",
            remote_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
            logger.debug("Hidden: %s", relative_path)
            hidden.append(relative_path)
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to hide %s: %s", relative_path, e.stderr)

    return hidden
