"""Command-line interface for android-sync."""

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_SECRETS_FILE, Config, ConfigError, load_config
from .keystore import (
    DEFAULT_KEY_ALIAS,
    KeystoreError,
    encrypt_secrets,
    generate_key,
    get_b2_credentials,
    key_exists,
)
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

    # setup command
    setup_parser = subparsers.add_parser("setup", help="Initialize keystore and secrets")
    setup_parser.add_argument(
        "--secrets-file",
        type=Path,
        default=DEFAULT_SECRETS_FILE,
        help=f"Path for encrypted secrets file (default: {DEFAULT_SECRETS_FILE})",
    )
    setup_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing secrets file",
    )

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

    if args.command == "setup":
        return cmd_setup(args)

    # Load config for other commands
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


def cmd_setup(args: argparse.Namespace) -> int:
    """Handle setup command - initialize keystore and encrypt secrets."""
    print("Setting up android-sync...")

    # Generate keystore key if needed
    if not key_exists(DEFAULT_KEY_ALIAS):
        print(f"Generating signing key '{DEFAULT_KEY_ALIAS}' in Android Keystore...")
        try:
            generate_key(DEFAULT_KEY_ALIAS)
            print("  Key generated successfully.")
        except KeystoreError as e:
            print(f"Error generating key: {e}", file=sys.stderr)
            return 1
    else:
        print(f"Signing key '{DEFAULT_KEY_ALIAS}' already exists.")

    # Check if secrets file exists
    secrets_file = args.secrets_file
    if secrets_file.exists() and not args.force:
        print(f"Error: Secrets file already exists: {secrets_file}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        return 1

    # Prompt for credentials
    print()
    print("Enter your Backblaze B2 credentials:")
    key_id = input("  Key ID: ").strip()
    app_key = getpass.getpass("  Application Key: ").strip()

    if not key_id or not app_key:
        print("Error: Both Key ID and Application Key are required.", file=sys.stderr)
        return 1

    # Create secrets directory
    secrets_file.parent.mkdir(parents=True, exist_ok=True)

    # Encrypt secrets
    print(f"Encrypting secrets to {secrets_file}...")
    try:
        secrets = {
            "b2_key_id": key_id,
            "b2_app_key": app_key,
        }
        encrypt_secrets(secrets, secrets_file, DEFAULT_KEY_ALIAS)
        print("  Secrets encrypted successfully.")
    except KeystoreError as e:
        print(f"Error encrypting secrets: {e}", file=sys.stderr)
        return 1

    print()
    print("Setup complete! You can now run syncs with:")
    print("  android-sync run --all --dry-run")
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
    # Get credentials from encrypted secrets
    try:
        credentials = get_b2_credentials(config.secrets_file, DEFAULT_KEY_ALIAS)
    except KeystoreError as e:
        logger.error("Failed to get credentials: %s", e)
        logger.error("Run 'android-sync setup' to initialize credentials.")
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
