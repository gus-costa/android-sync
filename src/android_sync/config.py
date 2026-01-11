"""Configuration loading and validation."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KeystoreConfig:
    """Keystore key names for B2 credentials."""

    b2_key_id: str
    b2_app_key: str


@dataclass
class Profile:
    """A sync profile defining sources and destination."""

    name: str
    sources: list[str]
    destination: str
    exclude: list[str] = field(default_factory=list)
    track_removals: bool = True


@dataclass
class Schedule:
    """A schedule that groups profiles together."""

    name: str
    profiles: list[str]


@dataclass
class Config:
    """Main configuration container."""

    bucket: str
    log_dir: Path
    log_retention_days: int
    keystore: KeystoreConfig
    profiles: dict[str, Profile]
    schedules: dict[str, Schedule]
    transfers: int = 4


class ConfigError(Exception):
    """Configuration loading or validation error."""


def load_config(path: Path) -> Config:
    """Load and validate configuration from a TOML file."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML: {e}") from e

    return _parse_config(data)


def _parse_config(data: dict) -> Config:
    """Parse and validate configuration data."""
    general = data.get("general", {})
    keystore_data = data.get("keystore", {})
    profiles_data = data.get("profiles", {})
    schedules_data = data.get("schedules", {})

    # Validate required fields
    if "bucket" not in general:
        raise ConfigError("Missing required field: general.bucket")
    if "b2_key_id" not in keystore_data:
        raise ConfigError("Missing required field: keystore.b2_key_id")
    if "b2_app_key" not in keystore_data:
        raise ConfigError("Missing required field: keystore.b2_app_key")

    keystore = KeystoreConfig(
        b2_key_id=keystore_data["b2_key_id"],
        b2_app_key=keystore_data["b2_app_key"],
    )

    profiles = {}
    for name, profile_data in profiles_data.items():
        if "sources" not in profile_data:
            raise ConfigError(f"Missing required field: profiles.{name}.sources")
        if "destination" not in profile_data:
            raise ConfigError(f"Missing required field: profiles.{name}.destination")

        profiles[name] = Profile(
            name=name,
            sources=profile_data["sources"],
            destination=profile_data["destination"],
            exclude=profile_data.get("exclude", []),
            track_removals=profile_data.get("track_removals", True),
        )

    schedules = {}
    for name, schedule_data in schedules_data.items():
        if "profiles" not in schedule_data:
            raise ConfigError(f"Missing required field: schedules.{name}.profiles")

        # Validate profile references
        for profile_name in schedule_data["profiles"]:
            if profile_name not in profiles:
                raise ConfigError(
                    f"Schedule '{name}' references unknown profile: {profile_name}"
                )

        schedules[name] = Schedule(
            name=name,
            profiles=schedule_data["profiles"],
        )

    return Config(
        bucket=general["bucket"],
        log_dir=Path(general.get("log_dir", "/data/data/com.termux/files/home/logs")),
        log_retention_days=general.get("log_retention_days", 7),
        keystore=keystore,
        profiles=profiles,
        schedules=schedules,
        transfers=general.get("transfers", 4),
    )
