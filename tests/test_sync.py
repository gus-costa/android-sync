"""Tests for sync logic."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from android_sync.config import Profile
from android_sync.keystore import B2Credentials
from android_sync.sync import (
    _b2_remote,
    _build_rclone_copy_cmd,
    _detect_removed_files,
    _list_local_files,
    _rclone_env,
    _should_exclude,
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


class TestBuildRcloneCopyCmd:
    def test_basic_command(self):
        cmd = _build_rclone_copy_cmd(
            source="/storage/DCIM",
            dest=":b2,account=key,key=secret:bucket/photos",
            exclude=[],
            transfers=4,
            dry_run=False,
        )

        assert cmd[0] == "rclone"
        assert cmd[1] == "copy"
        assert cmd[2] == "/storage/DCIM"
        assert cmd[3] == ":b2,account=key,key=secret:bucket/photos"
        assert "--transfers" in cmd
        assert "4" in cmd
        assert "--dry-run" not in cmd

    def test_with_exclude_patterns(self):
        cmd = _build_rclone_copy_cmd(
            source="/src",
            dest=":b2,account=key,key=secret:bucket/dest",
            exclude=["*.tmp", "*.bak"],
            transfers=2,
            dry_run=False,
        )

        # Find exclude flags
        exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
        assert len(exclude_indices) == 2
        assert cmd[exclude_indices[0] + 1] == "*.tmp"
        assert cmd[exclude_indices[1] + 1] == "*.bak"

    def test_dry_run_flag(self):
        cmd = _build_rclone_copy_cmd(
            source="/src",
            dest=":b2,account=key,key=secret:bucket/dest",
            exclude=[],
            transfers=4,
            dry_run=True,
        )

        assert "--dry-run" in cmd


class TestShouldExclude:
    def test_matches_simple_pattern(self):
        assert _should_exclude(Path("/path/to/file.tmp"), ["*.tmp"])
        assert _should_exclude(Path("/path/to/.thumbnails"), [".thumbnails"])

    def test_no_match(self):
        assert not _should_exclude(Path("/path/to/file.jpg"), ["*.tmp", "*.bak"])

    def test_multiple_patterns(self):
        patterns = ["*.tmp", "*.bak", ".DS_Store"]
        assert _should_exclude(Path("/x/.DS_Store"), patterns)
        assert _should_exclude(Path("/x/file.bak"), patterns)
        assert not _should_exclude(Path("/x/file.txt"), patterns)


class TestListLocalFiles:
    def test_list_files_in_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "file1.jpg").touch()
            (base / "file2.jpg").touch()
            (base / "subdir").mkdir()
            (base / "subdir" / "file3.jpg").touch()

            profile = Profile(
                name="test",
                sources=[str(base)],
                destination="dest",
                exclude=[],
            )

            files = _list_local_files(profile)

            assert len(files) == 3
            dir_name = base.name
            assert f"{dir_name}/file1.jpg" in files
            assert f"{dir_name}/file2.jpg" in files
            assert f"{dir_name}/subdir/file3.jpg" in files

    def test_excludes_matching_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "photo.jpg").touch()
            (base / "temp.tmp").touch()
            (base / ".thumbnails").touch()

            profile = Profile(
                name="test",
                sources=[str(base)],
                destination="dest",
                exclude=["*.tmp", ".thumbnails"],
            )

            files = _list_local_files(profile)

            dir_name = base.name
            assert f"{dir_name}/photo.jpg" in files
            assert f"{dir_name}/temp.tmp" not in files
            assert f"{dir_name}/.thumbnails" not in files


class TestSyncProfile:
    @patch("android_sync.sync._hide_removed_files")
    @patch("android_sync.sync.subprocess.run")
    def test_sync_success(self, mock_run, mock_hide, profile, credentials):
        mock_run.return_value = MagicMock(stderr="Transferred: 5 files", returncode=0)
        mock_hide.return_value = []

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

    @patch("android_sync.sync._detect_removed_files")
    @patch("android_sync.sync.subprocess.run")
    def test_sync_missing_source_skipped(
        self, mock_run, mock_detect, profile, credentials
    ):
        profile.sources = ["/nonexistent/path"]
        mock_detect.return_value = []

        result = sync_profile(
            profile=profile,
            bucket="test-bucket",
            credentials=credentials,
            dry_run=True,
        )

        assert result.success
        # rclone copy should not be called for non-existent sources
        mock_run.assert_not_called()

    @patch("android_sync.sync._detect_removed_files")
    @patch("android_sync.sync.subprocess.run")
    def test_dry_run_detects_but_doesnt_hide(
        self, mock_run, mock_detect, profile, credentials
    ):
        mock_run.return_value = MagicMock(stderr="", returncode=0)
        mock_detect.return_value = ["removed/file.jpg"]

        with tempfile.TemporaryDirectory() as tmpdir:
            profile.sources = [tmpdir]

            result = sync_profile(
                profile=profile,
                bucket="test-bucket",
                credentials=credentials,
                dry_run=True,
            )

            assert result.success
            # detect_removed_files should be called, not hide_removed_files
            mock_detect.assert_called_once()

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


class TestDetectRemovedFiles:
    @patch("android_sync.sync._list_local_files")
    @patch("android_sync.sync._list_remote_files")
    def test_detects_removed_files(self, mock_remote, mock_local, profile, credentials):
        mock_remote.return_value = {"DCIM/a.jpg", "DCIM/b.jpg", "DCIM/c.jpg"}
        mock_local.return_value = {"DCIM/a.jpg", "DCIM/c.jpg"}

        removed = _detect_removed_files(profile, "bucket", credentials)

        assert removed == ["DCIM/b.jpg"]

    @patch("android_sync.sync._list_local_files")
    @patch("android_sync.sync._list_remote_files")
    def test_no_removed_files(self, mock_remote, mock_local, profile, credentials):
        mock_remote.return_value = {"DCIM/a.jpg"}
        mock_local.return_value = {"DCIM/a.jpg", "DCIM/b.jpg"}

        removed = _detect_removed_files(profile, "bucket", credentials)

        assert removed == []
