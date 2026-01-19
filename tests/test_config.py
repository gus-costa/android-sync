"""Tests for configuration loading."""

import tempfile
from pathlib import Path

import pytest

from android_sync.config import (
    ConfigError,
    load_config,
)

VALID_CONFIG = """\
[general]
bucket = "my-bucket"
log_dir = "/tmp/logs"
log_retention_days = 14
transfers = 2
secrets_file = "/path/to/secrets.gpg"

[profiles.photos]
sources = ["/storage/DCIM", "/storage/Pictures"]
destination = "photos"
exclude = ["*.tmp", ".thumbnails"]
track_removals = true

[profiles.documents]
sources = ["/storage/Documents"]
destination = "docs"

[schedules.daily]
profiles = ["photos", "documents"]

[schedules.hourly]
profiles = ["photos"]
"""


def write_config(content: str) -> Path:
    """Write config to a temp file and return path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(content)
        return Path(f.name)


class TestLoadConfig:
    def test_load_valid_config(self):
        path = write_config(VALID_CONFIG)
        try:
            config = load_config(path)

            assert config.bucket == "my-bucket"
            assert config.log_dir == Path("/tmp/logs")
            assert config.log_retention_days == 14
            assert config.transfers == 2
            assert config.secrets_file == Path("/path/to/secrets.gpg")

            assert "photos" in config.profiles
            assert "documents" in config.profiles

            photos = config.profiles["photos"]
            assert photos.sources == ["/storage/DCIM", "/storage/Pictures"]
            assert photos.destination == "photos"
            assert photos.exclude == ["*.tmp", ".thumbnails"]
            assert photos.track_removals is True

            docs = config.profiles["documents"]
            assert docs.exclude == []
            assert docs.track_removals is True  # default

            assert "daily" in config.schedules
            assert config.schedules["daily"].profiles == ["photos", "documents"]
        finally:
            path.unlink()

    def test_missing_bucket(self):
        config = """\
[general]
log_dir = "/tmp"
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="general.bucket"):
                load_config(path)
        finally:
            path.unlink()

    def test_missing_profile_sources(self):
        config = """\
[general]
bucket = "test"

[profiles.bad]
destination = "dest"
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="profiles.bad.sources"):
                load_config(path)
        finally:
            path.unlink()

    def test_schedule_references_unknown_profile(self):
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.bad]
profiles = ["nonexistent"]
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="unknown profile: nonexistent"):
                load_config(path)
        finally:
            path.unlink()

    def test_file_not_found(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config(Path("/nonexistent/config.toml"))

    def test_invalid_toml(self):
        path = write_config("this is [not valid toml")
        try:
            with pytest.raises(ConfigError, match="Invalid TOML"):
                load_config(path)
        finally:
            path.unlink()

    def test_default_values(self):
        config = """\
[general]
bucket = "test"
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.log_retention_days == 7
            assert cfg.transfers == 4
            # Default secrets file
            assert cfg.secrets_file.name == "secrets.gpg"
            # Default stale job timeout
            assert cfg.stale_job_timeout_hours == 24
        finally:
            path.unlink()


class TestSchedulingConfiguration:
    """Test scheduling-related configuration validation."""

    def test_valid_cron_expression(self):
        """Test that valid cron expressions are accepted."""
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.daily]
profiles = ["photos"]
cron = "0 3 * * *"
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.schedules["daily"].cron == "0 3 * * *"
        finally:
            path.unlink()

    def test_invalid_cron_expression(self):
        """Test that invalid cron expressions raise an error."""
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.bad]
profiles = ["photos"]
cron = "invalid cron"
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="invalid cron expression"):
                load_config(path)
        finally:
            path.unlink()

    def test_missing_cron_is_valid(self):
        """Test that schedules without cron (manual-only) are valid."""
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.manual]
profiles = ["photos"]
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.schedules["manual"].cron is None
        finally:
            path.unlink()

    def test_mixed_scheduled_and_manual(self):
        """Test mix of scheduled and manual schedules."""
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.scheduled]
profiles = ["photos"]
cron = "0 3 * * *"

[schedules.manual]
profiles = ["photos"]
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.schedules["scheduled"].cron == "0 3 * * *"
            assert cfg.schedules["manual"].cron is None
        finally:
            path.unlink()

    def test_stale_job_timeout_custom_value(self):
        """Test custom stale job timeout value."""
        config = """\
[general]
bucket = "test"
stale_job_timeout_hours = 48
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.stale_job_timeout_hours == 48
        finally:
            path.unlink()

    def test_various_cron_expressions(self):
        """Test various valid cron expression formats."""
        config = """\
[general]
bucket = "test"

[profiles.photos]
sources = ["/storage"]
destination = "photos"

[schedules.hourly]
profiles = ["photos"]
cron = "0 * * * *"

[schedules.every_six_hours]
profiles = ["photos"]
cron = "0 */6 * * *"

[schedules.weekly]
profiles = ["photos"]
cron = "0 2 * * 0"

[schedules.monthly]
profiles = ["photos"]
cron = "0 0 1 * *"
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.schedules["hourly"].cron == "0 * * * *"
            assert cfg.schedules["every_six_hours"].cron == "0 */6 * * *"
            assert cfg.schedules["weekly"].cron == "0 2 * * 0"
            assert cfg.schedules["monthly"].cron == "0 0 1 * *"
        finally:
            path.unlink()
