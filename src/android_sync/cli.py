"""Command-line interface for android-sync."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config
from .keystore import KeystoreError, get_b2_credentials
from .logging import setup_logging
from .sync import SyncResult, sync_profile

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "android-sync" / "config.toml"


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="android-sync",
        description="Backup files from Android to Backblaze B2 using rclone",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run sync operation")
    run_group = run_parser.add_mutually_exclusive_group(required=True)
    run_group.add_argument(
        "schedule",
        nargs="?",
        help="Schedule name to run",
    )
    run_group.add_argument(
        "--profile",
        "-p",
        help="Run a single profile",
    )
    run_group.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Run all profiles",
    )
    run_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview what would be synced without making changes",
    )

    # list command
    list_parser = subparsers.add_parser("list", help="List profiles and schedules")
    list_parser.add_argument(
        "type",
        choices=["profiles", "schedules"],
        help="What to list",
    )

    args = parser.parse_args()

    # Load config
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Setup logging
    logger = setup_logging(config.log_dir, config.log_retention_days, args.verbose)

    if args.command == "list":
        return cmd_list(config, args.type)

    if args.command == "run":
        return cmd_run(config, args, logger)

    return 0


def cmd_list(config: Config, list_type: str) -> int:
    """Handle list command."""
    if list_type == "profiles":
        print("Profiles:")
        for name, profile in config.profiles.items():
            sources = ", ".join(profile.sources)
            print(f"  {name}: {sources} -> {profile.destination}")
    else:
        print("Schedules:")
        for name, schedule in config.schedules.items():
            profiles = ", ".join(schedule.profiles)
            print(f"  {name}: [{profiles}]")
    return 0


def cmd_run(config: Config, args: argparse.Namespace, logger) -> int:
    """Handle run command."""
    # Get credentials
    try:
        credentials = get_b2_credentials(
            config.keystore.b2_key_id,
            config.keystore.b2_app_key,
        )
    except KeystoreError as e:
        logger.error("Failed to get credentials: %s", e)
        return 1

    # Determine which profiles to run
    profiles_to_run: list[str] = []

    if args.all:
        profiles_to_run = list(config.profiles.keys())
    elif args.profile:
        if args.profile not in config.profiles:
            logger.error("Unknown profile: %s", args.profile)
            return 1
        profiles_to_run = [args.profile]
    elif args.schedule:
        if args.schedule not in config.schedules:
            logger.error("Unknown schedule: %s", args.schedule)
            return 1
        profiles_to_run = config.schedules[args.schedule].profiles

    if not profiles_to_run:
        logger.error("No profiles to run")
        return 1

    # Run sync for each profile
    results: list[SyncResult] = []
    for profile_name in profiles_to_run:
        profile = config.profiles[profile_name]
        result = sync_profile(
            profile=profile,
            bucket=config.bucket,
            credentials=credentials,
            transfers=config.transfers,
            dry_run=args.dry_run,
        )
        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r.success)
    total_files = sum(r.files_transferred for r in results)
    total_hidden = sum(len(r.hidden_files) for r in results)

    logger.info(
        "Sync complete: %d/%d profiles succeeded, %d files transferred, %d files hidden",
        success_count,
        len(results),
        total_files,
        total_hidden,
    )

    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
