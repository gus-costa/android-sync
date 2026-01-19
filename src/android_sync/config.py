"""Configuration loading and validation."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from croniter import croniter

DEFAULT_SECRETS_FILE = Path.home() / ".local" / "share" / "android-sync" / "secrets.gpg"


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
    cron: str | None = None


@dataclass
class Config:
    """Main configuration container."""

    bucket: str
    log_dir: Path
    log_retention_days: int
    secrets_file: Path
    profiles: dict[str, Profile]
    schedules: dict[str, Schedule]
    transfers: int = 4
    stale_job_timeout_hours: int = 24


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
    profiles_data = data.get("profiles", {})
    schedules_data = data.get("schedules", {})

    # Validate required fields
    if "bucket" not in general:
        raise ConfigError("Missing required field: general.bucket")

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

        # Validate cron expression if present
        cron_expr = schedule_data.get("cron")
        if cron_expr is not None:
            if not croniter.is_valid(cron_expr):
                raise ConfigError(
                    f"Schedule '{name}' has invalid cron expression: '{cron_expr}'"
                )

        schedules[name] = Schedule(
            name=name,
            profiles=schedule_data["profiles"],
            cron=cron_expr,
        )

    secrets_file_str = general.get("secrets_file")
    if secrets_file_str:
        secrets_file = Path(secrets_file_str)
    else:
        secrets_file = DEFAULT_SECRETS_FILE

    return Config(
        bucket=general["bucket"],
        log_dir=Path(general.get("log_dir", "/data/data/com.termux/files/home/logs")),
        log_retention_days=general.get("log_retention_days", 7),
        secrets_file=secrets_file,
        profiles=profiles,
        schedules=schedules,
        transfers=general.get("transfers", 4),
        stale_job_timeout_hours=general.get("stale_job_timeout_hours", 24),
    )
