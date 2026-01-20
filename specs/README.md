# android-sync Specifications

This directory contains detailed technical specifications for all major components of android-sync. Each specification documents architecture, design decisions, implementation details, and operational considerations for a specific subsystem.

## Purpose

These specifications serve as:

- **Design documentation** for implementation
- **Reference material** for understanding system behavior
- **Maintenance guide** for future enhancements
- **Architecture overview** for new contributors

## Specifications Overview

### Core System Components

#### [configuration-schema.md](configuration-schema.md)
**Configuration file format and validation**

Defines the TOML configuration schema including general settings, sync profiles, and schedules. Covers field definitions, defaults, validation rules, and error handling.

**Key Topics:**
- TOML file structure (`[general]`, `[profiles.*]`, `[schedules.*]`)
- Field types and validation
- Default values and optional fields
- Configuration loading and error messages

#### [security-keystore.md](security-keystore.md)
**Credential storage and encryption system**

Details the three-layer security architecture for protecting Backblaze B2 credentials using Android's hardware-backed Keystore and GPG encryption.

**Key Topics:**
- RSA 4096-bit key generation in Android Keystore
- Passphrase derivation via signing + SHA-256
- GPG AES-256 symmetric encryption
- Threat model and attack surface analysis

#### [logging-system.md](logging-system.md)
**Logging infrastructure and retention**

Specifies the dual-output logging system (file + console), log format, retention policy, and operational procedures.

**Key Topics:**
- Log format and levels (DEBUG, INFO, WARNING, ERROR)
- Timestamped log files with automatic cleanup
- Verbose mode for troubleshooting
- Background job logging strategy

### Functional Components

#### [sync-engine.md](sync-engine.md)
**File synchronization orchestration**

Describes how android-sync orchestrates rclone to perform one-way sync from device to B2 cloud storage.

**Key Topics:**
- Two operational modes: sync (mirror) vs copy (append-only)
- Rclone integration and flag selection
- Checksum-based comparison (SHA1)
- Dry-run preview mode
- Result parsing and reporting

#### [scheduling.md](scheduling.md)
**Automatic time-based execution**

Defines the scheduling system that enables unattended execution using Android's JobScheduler API.

**Key Topics:**
- Periodic check pattern (every 15 minutes)
- Cron expression parsing and next-run calculation
- State management and persistence
- Stale job detection and cleanup
- Network and battery constraints
- Priority-based execution when multiple schedules are overdue

#### [cli-architecture.md](cli-architecture.md)
**Command-line interface design**

Documents the CLI structure, commands, argument parsing, and user interactions.

**Key Topics:**
- Six commands: `setup`, `run`, `list`, `check`, `status`, `reset`
- Global options (`--config`, `--verbose`, `--version`)
- State management through command interactions
- Error handling and exit codes

## Reading Order

### For New Contributors

1. **Start here:** [configuration-schema.md](configuration-schema.md) - Understand the config file structure
2. **Security context:** [security-keystore.md](security-keystore.md) - How credentials are protected
3. **Core functionality:** [sync-engine.md](sync-engine.md) - How syncing actually works
4. **Automation:** [scheduling.md](scheduling.md) - How automatic execution works
5. **User interface:** [cli-architecture.md](cli-architecture.md) - How users interact with the system
6. **Operations:** [logging-system.md](logging-system.md) - Troubleshooting and monitoring

### For Understanding a Feature

- **Setting up credentials:** security-keystore.md → cli-architecture.md (§3.1)
- **Configuring syncs:** configuration-schema.md (§5, §6) → sync-engine.md
- **Automatic scheduling:** scheduling.md → cli-architecture.md (§3.4, §3.5)
- **Troubleshooting:** logging-system.md (§9) → sync-engine.md (§5)

### For Implementing Changes

- **Adding config fields:** configuration-schema.md (§11)
- **Modifying sync behavior:** sync-engine.md → configuration-schema.md (§5.2)
- **Changing schedule logic:** scheduling.md (§5.3) → cli-architecture.md (§3.4)
- **Adding CLI commands:** cli-architecture.md (§3)

## Specification Structure

Each specification follows a consistent format:

### Standard Sections

1. **Overview** - Purpose, goals, and non-goals
2. **Architecture** - High-level design and execution model
3. **Component Details** - Implementation specifics with code references
4. **Configuration** - Related config fields and options
5. **Error Handling** - Failure modes and recovery strategies
6. **Testing** - Test strategy and coverage
7. **Security** - Threat model and mitigations (where applicable)
8. **Operations** - Day-to-day usage and troubleshooting
9. **Future Enhancements** - Out-of-scope features for consideration
10. **References** - External documentation and standards

### Code References

Specifications include precise code references in the format:

```
Location: src/android_sync/module.py:123-145
```

These line numbers are approximate and may drift as code evolves. Use them as starting points for exploration.

## Component Dependencies

```
┌─────────────────┐
│  CLI Commands   │ ◄── cli-architecture.md
└────────┬────────┘
         │
    ┌────▼────────────────────────┐
    │                             │
┌───▼────────┐          ┌─────────▼──────┐
│   Config   │          │   Scheduler    │
│  Loading   │          │   (check cmd)  │
└───┬────────┘          └─────────┬──────┘
    │                             │
    │ configuration-schema.md     │ scheduling.md
    │                             │
┌───▼──────────┐         ┌────────▼───────┐
│ Credentials  │         │  State Mgmt    │
│  Decryption  │         │  (JSON files)  │
└───┬──────────┘         └────────────────┘
    │
    │ security-keystore.md
    │
┌───▼──────────┐
│ Sync Engine  │ ◄── sync-engine.md
│  (rclone)    │
└───┬──────────┘
    │
┌───▼──────────┐
│   Logging    │ ◄── logging-system.md
└──────────────┘
```

## Version Information

**Specification Version:** 1.0
**Last Updated:** 2026-01-20
**Status:** Draft (all specs)

All specifications are living documents that evolve with the codebase. Version numbers track major architectural changes, not minor clarifications.

## Contributing

When modifying the codebase:

1. **Read relevant specs first** to understand design intent
2. **Update specs when behavior changes** to keep documentation synchronized
3. **Add new specs** for new major components
4. **Reference spec sections** in commit messages (e.g., "Implements §3.2 from scheduling.md")

When writing or updating specs:

- Use clear, precise language
- Include concrete examples
- Document the "why" not just the "what"
- Reference actual code locations
- Consider security and operational implications

## Cross-References

Specifications frequently reference each other:

- **CLI → Scheduling:** Command interactions (check, status, reset)
- **CLI → Security:** Credential setup flow
- **Sync Engine → Config:** Profile and schedule definitions
- **Scheduling → Logging:** Background job log files
- **Security → Config:** Secrets file location

Follow cross-references to understand how components integrate.

## Relationship to Code

Specifications describe **intended behavior**, not always current implementation state. When specs and code diverge:

1. **Spec is authoritative** for design intent
2. **Code is authoritative** for current behavior
3. **File an issue** to track the discrepancy
4. **Update spec or code** to resolve the divergence

Code references in specs (e.g., `src/android_sync/sync.py:178-213`) are:

- **Navigation hints** for finding relevant code
- **Approximate** (line numbers drift as code changes)
- **Not contractual** (code can be refactored)

## Additional Resources

- **README.md** (project root) - User-facing documentation and setup guide
- **tests/** - Executable specifications (test cases)
- **src/android_sync/** - Implementation code
- **CLAUDE.md** (project root) - Development guidelines and instructions
