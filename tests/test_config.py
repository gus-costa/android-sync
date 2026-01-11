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

[keystore]
b2_key_id = "my-key-id"
b2_app_key = "my-app-key"

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

            assert config.keystore.b2_key_id == "my-key-id"
            assert config.keystore.b2_app_key == "my-app-key"

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
[keystore]
b2_key_id = "key"
b2_app_key = "secret"
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="general.bucket"):
                load_config(path)
        finally:
            path.unlink()

    def test_missing_keystore_credentials(self):
        config = """\
[general]
bucket = "test"
"""
        path = write_config(config)
        try:
            with pytest.raises(ConfigError, match="keystore.b2_key_id"):
                load_config(path)
        finally:
            path.unlink()

    def test_missing_profile_sources(self):
        config = """\
[general]
bucket = "test"

[keystore]
b2_key_id = "key"
b2_app_key = "secret"

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

[keystore]
b2_key_id = "key"
b2_app_key = "secret"

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

[keystore]
b2_key_id = "key"
b2_app_key = "secret"
"""
        path = write_config(config)
        try:
            cfg = load_config(path)
            assert cfg.log_retention_days == 7
            assert cfg.transfers == 4
        finally:
            path.unlink()
