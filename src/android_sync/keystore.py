"""Secure credential storage using termux-keystore and GPG.

This module implements a secure key derivation scheme:
1. A signing key is stored in Android Keystore (non-exportable)
2. A fixed message is signed to derive a deterministic passphrase
3. The passphrase decrypts a GPG-encrypted secrets file

The private key never leaves the hardware-backed keystore, providing
separation between the encryption key and encrypted data.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Fixed message for key derivation - changing this invalidates all secrets
DERIVATION_MESSAGE = b"android-sync-derive-secrets-v1"

# Default key alias in termux-keystore
DEFAULT_KEY_ALIAS = "android-sync"

# Signing algorithm (RSA with SHA512 for wider compatibility)
SIGN_ALGORITHM = "SHA512withRSA"


class KeystoreError(Exception):
    """Error accessing termux-keystore or decrypting secrets."""


@dataclass
class B2Credentials:
    """Backblaze B2 credentials."""

    key_id: str
    app_key: str


def _run_command(cmd: list[str], input_data: bytes | None = None) -> bytes:
    """Run a command and return stdout as bytes."""
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise KeystoreError(f"Command failed: {' '.join(cmd)}\n{e.stderr.decode()}") from e
    except FileNotFoundError:
        raise KeystoreError(f"Command not found: {cmd[0]}") from None


def key_exists(alias: str = DEFAULT_KEY_ALIAS) -> bool:
    """Check if a signing key exists in the keystore."""
    try:
        output = _run_command(["termux-keystore", "list"])
        return alias in output.decode()
    except KeystoreError:
        return False


def generate_key(alias: str = DEFAULT_KEY_ALIAS) -> None:
    """Generate a new signing key in the Android Keystore.

    Args:
        alias: Key alias to use.

    Raises:
        KeystoreError: If key generation fails.
    """
    if key_exists(alias):
        raise KeystoreError(f"Key '{alias}' already exists")

    _run_command([
        "termux-keystore", "generate", alias,
        "-a", "RSA",
        "-s", "4096",
    ])


def delete_key(alias: str = DEFAULT_KEY_ALIAS) -> None:
    """Delete a signing key from the keystore.

    Args:
        alias: Key alias to delete.

    Raises:
        KeystoreError: If deletion fails.
    """
    _run_command(["termux-keystore", "delete", alias])


def derive_passphrase(alias: str = DEFAULT_KEY_ALIAS) -> str:
    """Derive a passphrase by signing a fixed message.

    The signature is hashed with SHA-256 to produce a uniformly
    distributed 256-bit secret, returned as a hex string.

    Args:
        alias: Key alias to use for signing.

    Returns:
        Hex-encoded passphrase derived from signature.

    Raises:
        KeystoreError: If signing fails.
    """
    # Sign the fixed derivation message
    signature = _run_command(
        ["termux-keystore", "sign", alias, SIGN_ALGORITHM],
        input_data=DERIVATION_MESSAGE,
    )

    # Hash signature to get uniform distribution
    return hashlib.sha256(signature).hexdigest()


def encrypt_secrets(secrets: dict, output_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> None:
    """Encrypt secrets to a GPG file using derived passphrase.

    Args:
        secrets: Dictionary of secrets to encrypt.
        output_path: Path to write encrypted file.
        alias: Key alias for passphrase derivation.

    Raises:
        KeystoreError: If encryption fails.
    """
    passphrase = derive_passphrase(alias)
    plaintext = json.dumps(secrets, indent=2).encode()

    _run_command(
        [
            "gpg", "--batch", "--yes",
            "--symmetric",
            "--cipher-algo", "AES256",
            "--passphrase-fd", "0",
            "--output", str(output_path),
        ],
        input_data=passphrase.encode() + b"\n" + plaintext,
    )


def decrypt_secrets(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> dict:
    """Decrypt secrets from a GPG file using derived passphrase.

    Args:
        secrets_path: Path to encrypted secrets file.
        alias: Key alias for passphrase derivation.

    Returns:
        Dictionary of decrypted secrets.

    Raises:
        KeystoreError: If decryption fails or file not found.
    """
    if not secrets_path.exists():
        raise KeystoreError(f"Secrets file not found: {secrets_path}")

    passphrase = derive_passphrase(alias)

    # GPG reads passphrase from fd 0, then reads encrypted file
    plaintext = _run_command(
        [
            "gpg", "--batch", "--quiet",
            "--decrypt",
            "--passphrase-fd", "0",
            str(secrets_path),
        ],
        input_data=passphrase.encode() + b"\n",
    )

    try:
        return json.loads(plaintext)
    except json.JSONDecodeError as e:
        raise KeystoreError(f"Invalid secrets file format: {e}") from e


def get_b2_credentials(secrets_path: Path, alias: str = DEFAULT_KEY_ALIAS) -> B2Credentials:
    """Retrieve B2 credentials from encrypted secrets file.

    Args:
        secrets_path: Path to encrypted secrets file.
        alias: Key alias for passphrase derivation.

    Returns:
        B2Credentials with decrypted values.

    Raises:
        KeystoreError: If credentials cannot be retrieved.
    """
    secrets = decrypt_secrets(secrets_path, alias)

    if "b2_key_id" not in secrets:
        raise KeystoreError("Missing 'b2_key_id' in secrets file")
    if "b2_app_key" not in secrets:
        raise KeystoreError("Missing 'b2_app_key' in secrets file")

    return B2Credentials(
        key_id=secrets["b2_key_id"],
        app_key=secrets["b2_app_key"],
    )
