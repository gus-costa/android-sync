"""Tests for sync logic."""

import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from android_sync.config import Profile
from android_sync.keystore import B2Credentials
from android_sync.sync import (
    _b2_remote,
    _build_rclone_cmd,
    _group_by_directory,
    _parse_dry_run_output,
    _parse_rclone_stats,
    _rclone_env,
    sync_profile,
)


@pytest.fixture
def credentials():
    return B2Credentials(key_id="test-key-id", app_key="test-app-key")


@pytest.fixture
def profile():
    return Profile(
        name="test-profile",
        sources=["/storage/DCIM"],
        destination="photos",
        exclude=["*.tmp", ".thumbnails"],
        track_removals=True,
    )


class TestB2Remote:
    def test_builds_remote_string(self):
        remote = _b2_remote("my-bucket", "photos/Camera")
        assert remote == ":b2:my-bucket/photos/Camera"


class TestRcloneEnv:
    def test_sets_credentials_in_env(self, credentials):
        env = _rclone_env(credentials)
        assert env["RCLONE_B2_ACCOUNT"] == "test-key-id"
        assert env["RCLONE_B2_KEY"] == "test-app-key"

    def test_preserves_existing_env(self, credentials):
        import os

        env = _rclone_env(credentials)
        # Should contain PATH and other system env vars
        assert "PATH" in env or os.name == "nt"


class TestBuildRcloneCmd:
    def test_sync_command_with_deletes(self):
        cmd = _build_rclone_cmd(
            source="/storage/DCIM",
            dest=":b2:bucket/photos",
            exclude=[],
            transfers=4,
            dry_run=False,
            sync_deletes=True,
        )

        assert cmd[0] == "rclone"
        assert cmd[1] == "sync"
        assert "--checksum" in cmd
        assert "--progress" in cmd

    def test_copy_command_without_deletes(self):
        cmd = _build_rclone_cmd(
            source="/storage/DCIM",
            dest=":b2:bucket/photos",
            exclude=[],
            transfers=4,
            dry_run=False,
            sync_deletes=False,
        )

        assert cmd[0] == "rclone"
        assert cmd[1] == "copy"
        assert "--checksum" in cmd

    def test_with_exclude_patterns(self):
        cmd = _build_rclone_cmd(
            source="/src",
            dest=":b2:bucket/dest",
            exclude=["*.tmp", "*.bak"],
            transfers=2,
            dry_run=False,
            sync_deletes=True,
        )

        exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
        assert len(exclude_indices) == 2
        assert cmd[exclude_indices[0] + 1] == "*.tmp"
        assert cmd[exclude_indices[1] + 1] == "*.bak"

    def test_dry_run_flag(self):
        cmd = _build_rclone_cmd(
            source="/src",
            dest=":b2:bucket/dest",
            exclude=[],
            transfers=4,
            dry_run=True,
            sync_deletes=True,
        )

        assert "--dry-run" in cmd
        assert "--progress" not in cmd


class TestSyncProfile:
    @patch("android_sync.sync.subprocess.run")
    def test_sync_success(self, mock_run, profile, credentials):
        mock_run.return_value = MagicMock(stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            profile.sources = [tmpdir]

            result = sync_profile(
                profile=profile,
                bucket="test-bucket",
                credentials=credentials,
                dry_run=False,
            )

            assert result.success
            assert result.profile_name == "test-profile"
            mock_run.assert_called_once()

    @patch("android_sync.sync.subprocess.run")
    def test_sync_missing_source_skipped(self, mock_run, profile, credentials):
        profile.sources = ["/nonexistent/path"]

        result = sync_profile(
            profile=profile,
            bucket="test-bucket",
            credentials=credentials,
            dry_run=True,
        )

        assert result.success
        # rclone should not be called for non-existent sources
        mock_run.assert_not_called()

    @patch("android_sync.sync.subprocess.run")
    def test_dry_run_captures_output(self, mock_run, profile, credentials):
        mock_run.return_value = MagicMock(
            stderr="NOTICE: file.jpg: Skipped copy as --dry-run is set",
            returncode=0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            profile.sources = [tmpdir]

            result = sync_profile(
                profile=profile,
                bucket="test-bucket",
                credentials=credentials,
                dry_run=True,
            )

            assert result.success
            assert result.files_transferred == 1

    @patch("android_sync.sync.subprocess.run")
    def test_sync_failure(self, mock_run, profile, credentials):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "rclone", stderr="connection failed"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            profile.sources = [tmpdir]

            result = sync_profile(
                profile=profile,
                bucket="test-bucket",
                credentials=credentials,
                dry_run=False,
            )

            assert not result.success
            assert "connection failed" in result.error


class TestParseDryRunOutput:
    def test_parses_transfers_and_deletes(self):
        output = """
2024/01/15 10:00:00 NOTICE: DCIM/Camera/IMG_001.jpg: Skipped copy as --dry-run is set
2024/01/15 10:00:00 NOTICE: DCIM/Camera/IMG_002.jpg: Skipped copy as --dry-run is set
2024/01/15 10:00:00 INFO: Some other log line
2024/01/15 10:00:00 NOTICE: old_file.txt: Skipped delete as --dry-run is set
2024/01/15 10:00:00 NOTICE: Pictures/photo.png: Skipped update as --dry-run is set
"""
        transfers, deletes = _parse_dry_run_output(output)

        assert len(transfers) == 3
        assert "DCIM/Camera/IMG_001.jpg" in transfers
        assert "DCIM/Camera/IMG_002.jpg" in transfers
        assert "Pictures/photo.png" in transfers
        assert len(deletes) == 1
        assert "old_file.txt" in deletes

    def test_only_transfers(self):
        output = "2024/01/15 10:00:00 NOTICE: file.txt: Skipped copy as --dry-run is set"
        transfers, deletes = _parse_dry_run_output(output)

        assert transfers == ["file.txt"]
        assert deletes == []

    def test_only_deletes(self):
        output = "2024/01/15 10:00:00 NOTICE: old_file.txt: Skipped delete as --dry-run is set"
        transfers, deletes = _parse_dry_run_output(output)

        assert transfers == []
        assert deletes == ["old_file.txt"]

    def test_empty_output(self):
        transfers, deletes = _parse_dry_run_output("")
        assert transfers == []
        assert deletes == []


class TestParseRcloneStats:
    def test_parses_transfers_and_deletes(self):
        output = """
Transferred:        141.303 GiB / 141.303 GiB, 100%, 524.104 KiB/s, ETA 0s
Checks:                 61 / 61, 100%
Deleted:                10 / 10, 100%
Transferred:         52449 / 52449, 100%
Elapsed time:     78h31m44.6s
"""
        stats = _parse_rclone_stats(output)
        assert stats["transfers"] == 52449
        assert stats["deletes"] == 10

    def test_transfers_only(self):
        output = """
Transferred:        2.339 KiB / 2.339 KiB, 100%, 0 B/s, ETA -
Transferred:            5 / 5, 100%
Elapsed time:         0.6s
"""
        stats = _parse_rclone_stats(output)
        assert stats["transfers"] == 5
        assert stats["deletes"] == 0

    def test_empty_output(self):
        stats = _parse_rclone_stats("")
        assert stats["transfers"] == 0
        assert stats["deletes"] == 0


class TestGroupByDirectory:
    def test_groups_by_top_level(self):
        files = [
            "DCIM/Camera/IMG_001.jpg",
            "DCIM/Camera/IMG_002.jpg",
            "DCIM/Screenshots/screen.png",
            "Pictures/photo.jpg",
        ]
        grouped = _group_by_directory(files)

        assert grouped == {"DCIM": 3, "Pictures": 1}

    def test_groups_by_depth_2(self):
        files = [
            "DCIM/Camera/IMG_001.jpg",
            "DCIM/Camera/IMG_002.jpg",
            "DCIM/Screenshots/screen.png",
        ]
        grouped = _group_by_directory(files, depth=2)

        assert grouped == {"DCIM/Camera": 2, "DCIM/Screenshots": 1}

    def test_empty_list(self):
        grouped = _group_by_directory([])
        assert grouped == {}

    def test_single_file_no_directory(self):
        # A bare filename gets grouped under itself
        grouped = _group_by_directory(["file.txt"])
        assert grouped == {"file.txt": 1}
