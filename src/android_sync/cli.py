"""Command-line interface for android-sync."""

import argparse
import getpass
import os
import subprocess
import sys
from datetime import datetime
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
from .scheduler import (
    get_overdue_schedules,
    get_state_directory,
    spawn_background_job,
    update_state_on_finish,
    update_state_on_start,
)
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

    # check command
    subparsers.add_parser("check", help="Check for overdue schedules and spawn job if needed")

    # status command
    subparsers.add_parser("status", help="Display status of all schedules")

    # reset command
    reset_parser = subparsers.add_parser("reset", help="Reset a schedule's state")
    reset_parser.add_argument(
        "schedule",
        help="Schedule name to reset",
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

    # Setup logging (only for commands that need it)
    if args.command in ["run", "check"]:
        logger = setup_logging(config.log_dir, config.log_retention_days, args.verbose)
    else:
        logger = None

    if args.command == "list":
        return cmd_list(config, args.type)

    if args.command == "run":
        return cmd_run(config, args, logger)

    if args.command == "check":
        return cmd_check(config, args)

    if args.command == "status":
        return cmd_status(config)

    if args.command == "reset":
        return cmd_reset(config, args.schedule)

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

    # Setup scheduler
    print()
    print("Setting up scheduler...")

    # Create state directory
    state_dir = get_state_directory()
    print(f"  Created state directory: {state_dir}")

    # Create check script
    check_script_path = state_dir.parent / "check-schedule.sh"
    check_script_content = """#!/data/data/com.termux/files/usr/bin/bash
exec android-sync check
"""
    check_script_path.write_text(check_script_content)
    os.chmod(check_script_path, 0o755)
    print(f"  Created check script: {check_script_path}")

    # Register with termux-job-scheduler
    print("  Registering with termux-job-scheduler...")
    try:
        subprocess.run(
            [
                "termux-job-scheduler",
                "schedule",
                "--script",
                str(check_script_path),
                "--period-ms",
                "900000",  # 15 minutes
                "--persisted",
                "true",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        print("  Job scheduler registered successfully.")
    except FileNotFoundError:
        print("  Warning: termux-job-scheduler not found.", file=sys.stderr)
        print("  Install termux-api package to enable automatic scheduling:", file=sys.stderr)
        print("    pkg install termux-api", file=sys.stderr)
        print("  Manual scheduling will still work.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"  Warning: Failed to register job scheduler: {e.stderr}", file=sys.stderr)
        print("  Manual scheduling will still work.", file=sys.stderr)

    print()
    print("Setup complete! You can now:")
    print("  - Run syncs manually: android-sync run --all --dry-run")
    print("  - Check schedule status: android-sync status")
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
    schedule_name = None

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
        schedule_name = args.schedule
        profiles_to_run = config.schedules[args.schedule].profiles

    if not profiles_to_run:
        logger.error("No profiles to run")
        return 1

    # Update state when running a schedule
    if schedule_name:
        update_state_on_start(schedule_name, config)

    # Run sync for each profile
    success = False
    try:
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

        success = all(r.success for r in results)
        return 0 if success else 1
    finally:
        # Update state when running a schedule
        if schedule_name:
            update_state_on_finish(schedule_name, config, success)


def cmd_check(config: Config, args: argparse.Namespace) -> int:
    """Handle check command - check for overdue schedules and spawn one job if needed."""
    # Get overdue schedules
    overdue_schedules = get_overdue_schedules(config)

    if not overdue_schedules:
        # No overdue schedules, exit silently
        return 0

    # Spawn the most overdue schedule
    schedule_name, overdue_minutes = overdue_schedules[0]
    spawn_background_job(schedule_name, args.config)

    return 0


def cmd_status(config: Config) -> int:
    """Handle status command - display status of all schedules."""
    from .scheduler import load_state

    if not config.schedules:
        print("No schedules configured.")
        return 0

    now = datetime.now()

    for schedule_name, schedule in config.schedules.items():
        state = load_state(schedule_name, schedule.cron)

        print(f"\nSchedule: {schedule_name}")

        # Type
        if schedule.cron:
            print(f"  Type: Scheduled (cron: {schedule.cron})")
        else:
            print("  Type: Manual (no automatic scheduling)")

        # Status
        status_str = state.status
        if state.status == "success":
            status_str = f"\033[32m{state.status}\033[0m"  # Green
        elif state.status == "running":
            status_str = f"\033[33m{state.status}\033[0m"  # Yellow
            if state.pid:
                status_str += f" (PID {state.pid})"
        elif state.status == "failed":
            status_str = f"\033[31m{state.status}\033[0m"  # Red
        print(f"  Status: {status_str}")

        # Last run / started
        if state.status == "running" and state.started_at:
            print(f"  Started: {state.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        elif state.last_run:
            print(f"  Last Run: {state.last_run.strftime('%Y-%m-%d %H:%M:%S')}")
        elif state.status == "failed" and state.started_at:
            print(f"  Last Attempt: {state.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

        # Next run
        if state.next_run:
            print(f"  Next Run: {state.next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            # Overdue?
            if now >= state.next_run:
                overdue_minutes = (now - state.next_run).total_seconds() / 60
                print(f"  Overdue: \033[31mYes ({int(overdue_minutes)} minutes)\033[0m")
            else:
                print("  Overdue: No")
        else:
            print("  Next Run: N/A")

    return 0


def cmd_reset(config: Config, schedule_name: str) -> int:
    """Handle reset command - reset a schedule's state."""
    from .scheduler import calculate_next_run, load_state, save_state

    if schedule_name not in config.schedules:
        print(f"Error: Unknown schedule: {schedule_name}", file=sys.stderr)
        return 1

    schedule = config.schedules[schedule_name]
    state = load_state(schedule_name, schedule.cron)

    # Reset state
    state.status = "pending"
    state.pid = None
    state.started_at = None
    state.finished_at = None

    # Calculate new next_run if scheduled
    if schedule.cron is not None:
        state.next_run = calculate_next_run(schedule.cron, datetime.now())
    else:
        state.next_run = None

    save_state(state)

    print(f"Schedule '{schedule_name}' has been reset.")
    if state.next_run:
        print(f"Next run: {state.next_run.strftime('%Y-%m-%d %H:%M:%S')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
