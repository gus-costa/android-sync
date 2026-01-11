"""Termux keystore integration for secure credential storage."""

import subprocess
from dataclasses import dataclass


class KeystoreError(Exception):
    """Error accessing termux-keystore."""


@dataclass
class B2Credentials:
    """Backblaze B2 credentials."""

    key_id: str
    app_key: str


def get_secret(key_name: str) -> str:
    """Retrieve a secret from termux-keystore.

    Args:
        key_name: The name of the key to retrieve.

    Returns:
        The secret value.

    Raises:
        KeystoreError: If the key cannot be retrieved.
    """
    try:
        result = subprocess.run(
            ["termux-keystore", "get", key_name],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise KeystoreError(f"Failed to get key '{key_name}': {e.stderr}") from e
    except FileNotFoundError:
        raise KeystoreError(
            "termux-keystore not found. Is Termux:API installed?"
        ) from None


def get_b2_credentials(key_id_name: str, app_key_name: str) -> B2Credentials:
    """Retrieve B2 credentials from termux-keystore.

    Args:
        key_id_name: Keystore key name for the B2 key ID.
        app_key_name: Keystore key name for the B2 application key.

    Returns:
        B2Credentials with the retrieved values.

    Raises:
        KeystoreError: If credentials cannot be retrieved.
    """
    return B2Credentials(
        key_id=get_secret(key_id_name),
        app_key=get_secret(app_key_name),
    )
