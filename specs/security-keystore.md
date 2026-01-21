# Security and Keystore Specification

**Version:** 1.0
**Date:** 2026-01-20
**Status:** Draft

## 1. Overview

This specification defines the credential storage and encryption system for android-sync, which protects Backblaze B2 credentials using Android's hardware-backed Keystore combined with GPG encryption.

### 1.1 Goals

- Store B2 credentials securely on-device without requiring user to re-enter them
- Leverage hardware-backed security features of Android devices
- Prevent credential exposure in config files, command-line arguments, or logs
- Make credentials device-specific and non-exportable
- Use standard, auditable encryption tools (GPG)

### 1.2 Non-Goals

- Credential backup or recovery mechanisms
- Multi-device credential synchronization
- Password-based encryption (uses hardware key derivation)
- Support for non-Android platforms
- Real-time credential rotation

## 2. Architecture

### 2.1 Three-Layer Security Model

The system uses a layered approach that separates key material from encrypted data:

```
Layer 1: Android Keystore (Hardware-backed)
         ├─ RSA 4096-bit signing key
         ├─ Non-exportable
         └─ Stored in TEE/Secure Element

Layer 2: Key Derivation
         ├─ Sign fixed message with RSA key
         ├─ Hash signature → 256-bit passphrase
         └─ Deterministic (same input → same output)

Layer 3: GPG Encryption
         ├─ AES-256 symmetric encryption
         ├─ Passphrase from Layer 2
         └─ Encrypted secrets.gpg on disk
```

**Why this architecture:**

1. **Hardware-backed security**: Private key protected by Android's TEE/Secure Element
2. **Separation of concerns**: Encryption key (in hardware) separate from encrypted data (on disk)
3. **Deterministic derivation**: Same key always produces same passphrase (no stored salt needed)
4. **Standard encryption**: GPG is well-audited and widely trusted

### 2.2 Why RSA Signing (Not Direct Encryption)

The design uses RSA signing rather than RSA encryption for key derivation for several reasons:

**Technical:**
- Android Keystore signing support is more reliable across devices than encryption
- Signing produces deterministic output (same input → same signature)
- RSA encryption would require padding schemes that may not be consistent

**Security:**
- Signing keys can be hardware-backed on more Android devices
- TEE/SE support for signing is more universal
- Signature verification not needed (only generation matters)

**Practical:**
- Better compatibility with termux-keystore API
- Simpler implementation (no need to handle encryption block size limits)
- Signature provides sufficient entropy for passphrase derivation

## 3. Components

### 3.1 Key Generation

**Location:** `src/android_sync/keystore.py:65-81`

**Function:** `generate_key(alias: str = DEFAULT_KEY_ALIAS) -> None`

**Process:**
1. Check if key already exists (prevents accidental overwrite)
2. Generate non-exportable RSA 4096-bit key using termux-keystore
3. Store in Android Keystore with specified alias

**Command:**
```bash
termux-keystore generate android-sync -a RSA -s 4096
```

**Key Properties:**
- **Algorithm**: RSA
- **Size**: 4096 bits (strong security, performance acceptable for signing)
- **Exportability**: Non-exportable (private key never leaves keystore)
- **Usage**: Sign only (not used for encryption/verification)
- **Alias**: `android-sync` (default, configurable)

**Error Handling:**
- If key exists: Raises `KeystoreError` (prevents accidental key replacement)
- If termux-keystore unavailable: Raises `KeystoreError` with installation instructions
- If generation fails: Propagates error from termux-keystore

**Security Properties:**
- Key stored in Android Keystore (TEE/Secure Element on supported devices)
- Cannot be exported or copied to another device
- Survives app uninstall (keystore is system-wide)
- Requires device unlock on most devices (depends on Android version/config)

### 3.2 Passphrase Derivation

**Location:** `src/android_sync/keystore.py:96-118`

**Function:** `derive_passphrase(alias: str = DEFAULT_KEY_ALIAS) -> str`

**Algorithm:**
```python
1. sign = RSA_Sign(key, DERIVATION_MESSAGE)
2. passphrase = SHA256(sign).hexdigest()
3. return passphrase  # 64 hex characters (256 bits)
```

**Fixed Derivation Message:**
```python
DERIVATION_MESSAGE = b"android-sync-derive-secrets-v1"
```

**Why a fixed message:**
- Deterministic: Same key always produces same passphrase
- No salt needed: Reduces storage requirements and complexity
- Version-tagged: Allows future algorithm changes (`-v1` suffix)
- Domain-separated: Specific to android-sync (prevents key reuse attacks)

**Signature Algorithm:**
```python
SIGN_ALGORITHM = "SHA512withRSA"
```

**Why SHA512withRSA:**
- Wide compatibility with Android devices
- SHA512 provides strong collision resistance
- RSA 4096 with SHA512 is standard and well-tested

**Post-Processing:**
```python
hashlib.sha256(signature).hexdigest()
```

**Why hash the signature:**
- **Uniform distribution**: RSA signatures may have statistical biases
- **Fixed length**: SHA256 always produces 256 bits (64 hex chars)
- **Extra layer**: Defense in depth (even if RSA has weakness, SHA256 provides additional protection)

**Output Format:**
- 64 hexadecimal characters
- Example: `a3f5c8e2...` (256 bits of entropy)
- Used directly as GPG symmetric key passphrase

**Security Analysis:**
- **Entropy**: 256 bits (cryptographically strong)
- **Brute force**: 2^256 attempts needed (infeasible)
- **Rainbow tables**: Not applicable (signature is unique to device's key)
- **Passphrase exposure**: Only exists in memory during encrypt/decrypt operations

### 3.3 Secret Encryption

**Location:** `src/android_sync/keystore.py:121-144`

**Function:** `encrypt_secrets(secrets: dict, output_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> None`

**Process:**
```
1. Derive passphrase from keystore
2. Serialize secrets to JSON
3. Encrypt with GPG (AES-256, passphrase-based)
4. Write to secrets.gpg file
```

**GPG Command:**
```bash
gpg --batch --yes \
    --symmetric \
    --cipher-algo AES256 \
    --passphrase-fd 0 \
    --output secrets.gpg
```

**Flag Meanings:**
- `--batch`: Non-interactive mode (no prompts)
- `--yes`: Overwrite existing file without confirmation
- `--symmetric`: Use symmetric encryption (not public-key)
- `--cipher-algo AES256`: Explicitly use AES-256 (strongest)
- `--passphrase-fd 0`: Read passphrase from stdin (secure)
- `--output`: Output file path

**Input Data Format:**
```
passphrase\n
{
  "b2_key_id": "...",
  "b2_app_key": "..."
}
```

**Why passphrase via stdin:**
- Not visible in process list (`ps aux`)
- Not stored in shell history
- Not passed as environment variable (which could leak via `/proc`)
- More secure than `--passphrase` flag

**Secrets File Format:**
```json
{
  "b2_key_id": "0123456789abcdef",
  "b2_app_key": "K001234567890abcdefghijklmnopqr"
}
```

**File Location:**
- Default: `~/.local/share/android-sync/secrets.gpg`
- Configurable via `general.secrets_file` in config
- XDG-compliant location

**File Permissions:**
- Created with default umask (typically 0600 on Termux)
- Only readable by user
- No special permission hardening (relies on filesystem permissions)

### 3.4 Secret Decryption

**Location:** `src/android_sync/keystore.py:147-179`

**Function:** `decrypt_secrets(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> dict`

**Process:**
```
1. Check secrets file exists
2. Derive passphrase from keystore
3. Decrypt with GPG
4. Parse JSON
5. Validate format
```

**GPG Command:**
```bash
gpg --batch --quiet \
    --decrypt \
    --passphrase-fd 0 \
    secrets.gpg
```

**Flag Meanings:**
- `--batch`: Non-interactive
- `--quiet`: Suppress informational messages
- `--decrypt`: Decrypt mode
- `--passphrase-fd 0`: Read passphrase from stdin

**Input Data Format:**
```
passphrase\n
```

**Validation:**
1. File existence check (prevents confusing error messages)
2. GPG decryption (validates passphrase/integrity)
3. JSON parsing (validates format)
4. Field presence check (validates required fields in get_b2_credentials)

**Error Handling:**
- Missing file: Clear error with file path
- Wrong passphrase: GPG error (wrong key or corrupted file)
- Invalid JSON: Clear parsing error
- Missing fields: Specific field name in error message

### 3.5 Credential Retrieval

**Location:** `src/android_sync/keystore.py:182-205`

**Function:** `get_b2_credentials(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> B2Credentials`

**Process:**
```
1. Decrypt secrets file
2. Validate required fields exist
3. Return B2Credentials dataclass
```

**Required Fields:**
- `b2_key_id`: Backblaze B2 Key ID
- `b2_app_key`: Backblaze B2 Application Key

**Validation:**
- Field existence checked explicitly (lines 197-200)
- Clear error messages if fields missing
- No validation of field content (B2 API will reject invalid credentials)

**Return Type:**
```python
@dataclass
class B2Credentials:
    key_id: str
    app_key: str
```

**Usage:**
- Called by sync engine to get credentials for rclone
- Credentials passed to rclone via environment variables
- Short-lived in memory (only during sync operation)

## 4. Security Properties

### 4.1 Threat Model

**Assumptions:**
- Device physical security is maintained (device not compromised)
- Android OS is trusted (no malicious OS modifications)
- Termux and termux-api are trusted
- GPG implementation is trusted
- User has authorized access to device (can unlock screen)

**Protected Against:**

1. **Credential exposure in config files**
   - Mitigation: Credentials never stored in plaintext config
   - Credentials only in encrypted secrets.gpg

2. **Credential exposure in command-line arguments**
   - Mitigation: Passphrase passed via stdin, not command args
   - Credentials passed to rclone via environment variables

3. **Credential exposure in process list**
   - Mitigation: No credentials in `ps aux` output
   - Environment variables not visible to other users

4. **Credential exposure in logs**
   - Mitigation: Credentials never logged
   - Only "Retrieved credentials" logged, not content

5. **Secrets file theft**
   - Mitigation: File is GPG-encrypted with AES-256
   - Passphrase derived from hardware-backed key
   - Cannot decrypt without access to device's keystore

6. **Key extraction**
   - Mitigation: Private key is non-exportable
   - Hardware-backed (TEE/Secure Element on supported devices)
   - Cannot copy key to another device

7. **Passive observation**
   - Mitigation: No credentials visible in normal operation
   - Screen lock protects keystore access on most devices

**NOT Protected Against:**

1. **Root access on device**
   - Root can potentially access keystore or memory
   - No protection against compromised device

2. **Physical device theft with unlock capability**
   - If attacker can unlock device, can access keystore
   - Device encryption and screen lock are user's responsibility

3. **Malware on device**
   - Malware could call termux-keystore APIs
   - Malware could read credentials from memory during sync
   - Android app sandboxing provides some protection

4. **Screen unlock bypass**
   - If attacker bypasses lock screen, can access keystore
   - Depends on Android security model

5. **Secrets file loss**
   - If secrets.gpg is deleted, credentials are lost
   - No backup or recovery mechanism
   - User must re-run setup with new credentials

6. **Key deletion from keystore**
   - If android-sync key is deleted, secrets become unrecoverable
   - No key backup (intentional design decision)

### 4.2 Cryptographic Details

**Algorithms:**
- **Key Generation**: RSA 4096-bit
- **Signing**: SHA512withRSA
- **Passphrase Derivation**: SHA-256 (of RSA signature)
- **Symmetric Encryption**: AES-256 (GPG)

**Key Strength:**
- RSA 4096: ~140 bits of security (NIST guideline: secure beyond 2030)
- AES-256: 256 bits of security (NIST guideline: secure indefinitely)
- SHA-256: 256 bits of security against preimage attacks

**Known Limitations:**
- RSA signing is deterministic (same input → same signature)
  - This is intentional for key derivation
  - Not a weakness in this use case (signature is not verified)

- No forward secrecy
  - If key is compromised, all past secrets can be decrypted
  - Acceptable for backup credential storage

- No key rotation mechanism
  - Same key used for lifetime of installation
  - Rotation would require re-encrypting secrets

### 4.3 Attack Surface Analysis

**Attack Vectors:**

1. **Command Injection via alias parameter**
   - **Risk**: Medium
   - **Mitigation**: subprocess.run with list arguments (not shell=True)
   - **Status**: Protected

2. **Path Traversal via secrets_path**
   - **Risk**: Low
   - **Mitigation**: Path is user-controlled (config file)
   - **Impact**: User can only access their own files
   - **Status**: Acceptable

3. **Timing Attacks on passphrase derivation**
   - **Risk**: Low
   - **Mitigation**: None (Python not designed for constant-time crypto)
   - **Impact**: Signature already unique to device, timing leak is minimal
   - **Status**: Acceptable for this use case

4. **Memory Dump of passphrase**
   - **Risk**: High (if attacker has memory access)
   - **Mitigation**: Passphrase only in memory during encrypt/decrypt
   - **Status**: Inherent limitation of credential management

5. **GPG Vulnerabilities**
   - **Risk**: Low
   - **Mitigation**: Use standard GPG, keep system updated
   - **Status**: Relies on GPG security

6. **Termux-keystore vulnerabilities**
   - **Risk**: Medium
   - **Mitigation**: None (dependency on termux-api)
   - **Status**: Trust relationship

### 4.4 Comparison with Alternatives

**Why not Python cryptography libraries (cryptography, pycryptodome)?**

Advantages of GPG approach:
- Standard tool, widely audited
- Available on Termux without extra dependencies
- Familiar to security-conscious users
- Well-documented and understood
- Interoperable (can decrypt manually with gpg command)

Disadvantages:
- Requires GPG binary (additional dependency)
- Less direct control over crypto parameters
- Subprocess overhead

Decision: GPG chosen for auditability and standardization.

**Why not Android Keystore direct encryption?**

Advantages of signing + GPG approach:
- Better compatibility across Android versions
- Deterministic key derivation (no salt storage needed)
- GPG provides standard encrypted file format

Disadvantages of direct encryption:
- Requires storing encrypted data in keystore (size limits)
- Non-deterministic encryption (need to store IV/salt)
- More complex keystore API usage

Decision: Signing + GPG chosen for simplicity and compatibility.

**Why not password-based encryption?**

Advantages of keystore approach:
- No user password to remember/enter
- Hardware-backed security
- Automatic on device (no manual unlock)

Disadvantages:
- Tied to device (cannot move to another device)
- No recovery if key deleted

Decision: Keystore chosen for automation and security.

## 5. Key Lifecycle

### 5.1 Initial Setup

**Command:** `android-sync setup`

**Process:**
1. Check if key exists in keystore
2. If not, generate new RSA 4096-bit key
3. Prompt user for B2 credentials
4. Encrypt credentials to secrets.gpg
5. Verify encryption by decrypting and checking fields

**User Interaction:**
```
Enter B2 Key ID: [user input]
Enter B2 Application Key: [user input, hidden]
```

**Files Created:**
- `~/.local/share/android-sync/secrets.gpg` (encrypted credentials)

**Keystore State:**
- Key `android-sync` created in Android Keystore

**Idempotency:**
- If secrets.gpg exists: Skip credential setup (unless --force flag)
- If key exists: Reuse existing key (do not regenerate)

**Force Re-setup:**
```bash
android-sync setup --force
```
- Overwrites existing secrets.gpg
- Reuses existing keystore key (does not regenerate)

### 5.2 Normal Operation

**Frequency:** Every sync operation

**Process:**
1. Read config to get secrets_path
2. Call `get_b2_credentials(secrets_path)`
3. Decrypt secrets using keystore key
4. Pass credentials to rclone via environment variables
5. Credentials cleared from memory after sync

**Keystore Interaction:**
- One signing operation per sync
- No key modification
- Read-only access to key

**Performance:**
- Signing operation: <100ms typically
- Acceptable overhead for sync operation

### 5.3 Key Rotation (Not Supported)

**Current Limitation:**
- No automated key rotation
- Changing DERIVATION_MESSAGE invalidates all secrets
- No migration path for algorithm upgrades

**Manual Rotation Process (if needed):**
1. Export current credentials (decrypt manually with gpg)
2. Delete old key: `termux-keystore delete android-sync`
3. Run `android-sync setup --force`
4. Re-enter credentials

**Future Enhancement:**
- Could support versioned derivation messages
- Secrets file could include version metadata
- Migration tool could re-encrypt with new key

### 5.4 Key Deletion

**Intentional Deletion:**
```bash
termux-keystore delete android-sync
```

**Consequences:**
- secrets.gpg becomes unrecoverable
- User must run `android-sync setup` again
- B2 credentials must be re-entered

**Accidental Deletion:**
- No recovery mechanism
- No key backup exists
- This is intentional (non-exportable keys cannot be backed up)

**Mitigation:**
- User should keep B2 credentials in password manager
- Can re-run setup at any time with same credentials

### 5.5 Secrets File Corruption

**Detection:**
- GPG decryption fails
- JSON parsing fails
- Missing required fields

**Recovery:**
- Run `android-sync setup --force`
- Re-enter B2 credentials
- Creates new secrets.gpg

**Prevention:**
- Regular backups of secrets.gpg (encrypted, safe to backup)
- Note: Backup only useful on same device (tied to keystore key)

## 6. Implementation Details

### 6.1 Module Structure

**File:** `src/android_sync/keystore.py` (205 lines)

**Public API:**
```python
# Exceptions
class KeystoreError(Exception)

# Data Types
@dataclass
class B2Credentials:
    key_id: str
    app_key: str

# Key Management
def key_exists(alias: str = DEFAULT_KEY_ALIAS) -> bool
def generate_key(alias: str = DEFAULT_KEY_ALIAS) -> None
def delete_key(alias: str = DEFAULT_KEY_ALIAS) -> None

# Cryptographic Operations
def derive_passphrase(alias: str = DEFAULT_KEY_ALIAS) -> str
def encrypt_secrets(secrets: dict, output_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> None
def decrypt_secrets(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> dict

# High-level API
def get_b2_credentials(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> B2Credentials
```

**Constants:**
```python
DERIVATION_MESSAGE = b"android-sync-derive-secrets-v1"
DEFAULT_KEY_ALIAS = "android-sync"
SIGN_ALGORITHM = "SHA512withRSA"
```

**Private Functions:**
```python
def _run_command(cmd: list[str], input_data: bytes | None = None) -> bytes
```

### 6.2 Error Handling

**KeystoreError Usage:**
- Raised for all keystore/crypto errors
- Contains descriptive error message
- Includes stderr from failed commands
- Chained exceptions preserve original error

**Error Categories:**

1. **Command not found** (termux-keystore or gpg missing)
   - Message: "Command not found: {command}"
   - Suggests installation instructions

2. **Key already exists**
   - Message: "Key '{alias}' already exists"
   - Prevents accidental overwrite

3. **Secrets file not found**
   - Message: "Secrets file not found: {path}"
   - Suggests running setup

4. **Invalid secrets format**
   - Message: "Invalid secrets file format: {json_error}"
   - Suggests file corruption

5. **Missing credential fields**
   - Message: "Missing '{field}' in secrets file"
   - Specific field name provided

6. **Command failed** (termux-keystore or gpg error)
   - Message: "Command failed: {command}\n{stderr}"
   - Includes full error output

### 6.3 Dependencies

**External Commands:**
- `termux-keystore`: Android Keystore access via Termux
  - Package: termux-api
  - Installation: `pkg install termux-api`
  - Also requires Termux:API app from F-Droid/Play Store

- `gpg`: GNU Privacy Guard
  - Package: gnupg
  - Installation: `pkg install gnupg`
  - Standard on most Linux systems

**Python Standard Library:**
- `hashlib`: SHA-256 hashing
- `json`: Secrets serialization
- `subprocess`: Command execution
- `dataclasses`: B2Credentials type
- `pathlib`: Path handling

**No Third-Party Python Dependencies**
- Keeps installation simple
- Reduces attack surface
- GPG and keystore via system tools

## 7. Testing Strategy

### 7.1 Unit Tests

**File:** `tests/test_keystore.py` (206 lines)

**Test Coverage:**
- Key generation and existence checking
- Passphrase derivation (determinism, length, format)
- Secret encryption and decryption
- Credential retrieval
- Error handling (missing files, invalid JSON, missing fields)
- Command failure simulation

**Mocking Strategy:**
- Mock `subprocess.run` to simulate termux-keystore and gpg
- Test passphrase derivation with known signature
- Test error paths with simulated failures

**Key Test Cases:**
1. `test_key_exists_true/false`: Key existence detection
2. `test_generate_key`: Successful key generation
3. `test_generate_key_already_exists`: Duplicate key prevention
4. `test_derive_passphrase`: Deterministic derivation
5. `test_encrypt_decrypt_roundtrip`: Full cycle
6. `test_missing_secrets_file`: Error handling
7. `test_invalid_json`: Format validation
8. `test_missing_credential_fields`: Required field checking

### 7.2 Integration Tests

**Manual Testing:**
1. Full setup on real Termux device
2. Multiple encrypt/decrypt cycles
3. Key deletion and re-setup
4. Secrets file corruption recovery
5. Permission checks on secrets.gpg

**Security Testing:**
1. Verify credentials not in logs
2. Verify credentials not in process list
3. Verify secrets.gpg is encrypted (cannot `cat` and read)
4. Verify key is non-exportable (termux-keystore limitations)

### 7.3 Test Environment

**Requirements:**
- Termux with termux-api package
- GPG installed
- Python 3.11+
- Mock framework for unit tests

**Challenges:**
- Hardware keystore not available in CI/CD
- Must mock termux-keystore operations
- Integration tests require real Android device

## 8. Security Auditing

### 8.1 Audit Checklist

- [ ] No credentials in config files
- [ ] No credentials in command-line arguments
- [ ] No credentials in logs
- [ ] Passphrase passed via stdin (not args)
- [ ] RSA 4096 key size
- [ ] AES-256 symmetric encryption
- [ ] Non-exportable key flag set
- [ ] Secrets file has restrictive permissions
- [ ] No hardcoded credentials in code
- [ ] Error messages don't leak secrets
- [ ] Subprocess uses list args (not shell=True)
- [ ] Input validation on file paths
- [ ] JSON parsing handles malformed input

### 8.2 Code Review Focus Areas

1. **Subprocess invocation** (lines 40-53)
   - Verify list arguments (no shell injection)
   - Check input_data handling (binary data)

2. **Passphrase handling** (lines 121-144, 147-179)
   - Verify passphrase only in memory during operation
   - Check stdin usage (not command args)

3. **Error messages** (throughout)
   - Ensure no secrets in error text
   - Verify error chaining preserves context

4. **Constants** (lines 18-25)
   - Verify DERIVATION_MESSAGE is truly fixed
   - Check algorithm choices are current best practices

## 9. Operational Considerations

### 9.1 Backup Strategy

**What to backup:**
- `secrets.gpg` file (encrypted, safe to backup)
- B2 credentials themselves (in password manager)

**What NOT to backup:**
- Keystore key (non-exportable, cannot backup)

**Backup usability:**
- `secrets.gpg` backup only useful on same device
- If restoring to new device, must re-run setup

**Recommendation:**
- Keep B2 credentials in secure password manager
- Backup secrets.gpg for convenience, not security

### 9.2 Multi-Device Scenarios

**Limitation:**
- Credentials tied to device's keystore
- Cannot share credentials between devices

**Workaround:**
- Each device runs its own `android-sync setup`
- Each device has separate secrets.gpg
- All devices can use same B2 bucket (separate B2 app keys or same)

### 9.3 Device Migration

**When upgrading to new device:**
1. Note B2 credentials (from password manager)
2. Install android-sync on new device
3. Run `android-sync setup`
4. Enter same B2 credentials

**No automated migration:**
- Intentional design (non-exportable keys)
- Forces explicit credential re-entry
- Provides security reset

## 10. Future Enhancements (Out of Scope)

- Versioned derivation messages (support algorithm upgrades)
- Multiple credential sets (different B2 accounts)
- Credential rotation reminders
- Biometric unlock integration
- Key attestation (verify hardware-backed key)
- Secrets file integrity checking (HMAC or signature)
- Multiple backend support (S3, GCS, etc.)

## 11. References

- [Android Keystore System](https://developer.android.com/training/articles/keystore)
- [Termux:API Documentation](https://wiki.termux.com/wiki/Termux:API)
- [GPG Manual](https://www.gnupg.org/documentation/manuals/gnupg/)
- [NIST Key Management Guidelines](https://csrc.nist.gov/publications/detail/sp/800-57-part-1/rev-5/final)
- [RSA Key Sizes](https://www.keylength.com/)
