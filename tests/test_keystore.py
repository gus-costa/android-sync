"""Tests for termux-keystore integration."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from android_sync.keystore import (
    B2Credentials,
    KeystoreError,
    get_b2_credentials,
    get_secret,
)


class TestGetSecret:
    @patch("android_sync.keystore.subprocess.run")
    def test_get_secret_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="secret-value\n")
        result = get_secret("my-key")

        assert result == "secret-value"
        mock_run.assert_called_once_with(
            ["termux-keystore", "get", "my-key"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("android_sync.keystore.subprocess.run")
    def test_get_secret_strips_whitespace(self, mock_run):
        mock_run.return_value = MagicMock(stdout="  value  \n\n")
        result = get_secret("key")
        assert result == "value"

    @patch("android_sync.keystore.subprocess.run")
    def test_get_secret_command_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "cmd", stderr="key not found"
        )

        with pytest.raises(KeystoreError, match="Failed to get key 'my-key'"):
            get_secret("my-key")

    @patch("android_sync.keystore.subprocess.run")
    def test_get_secret_keystore_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(KeystoreError, match="termux-keystore not found"):
            get_secret("key")


class TestGetB2Credentials:
    @patch("android_sync.keystore.get_secret")
    def test_get_b2_credentials_success(self, mock_get_secret):
        mock_get_secret.side_effect = ["my-key-id", "my-app-key"]

        creds = get_b2_credentials("key-id-name", "app-key-name")

        assert isinstance(creds, B2Credentials)
        assert creds.key_id == "my-key-id"
        assert creds.app_key == "my-app-key"

        assert mock_get_secret.call_count == 2
        mock_get_secret.assert_any_call("key-id-name")
        mock_get_secret.assert_any_call("app-key-name")

    @patch("android_sync.keystore.get_secret")
    def test_get_b2_credentials_propagates_error(self, mock_get_secret):
        mock_get_secret.side_effect = KeystoreError("key not found")

        with pytest.raises(KeystoreError):
            get_b2_credentials("bad-key", "app-key")
