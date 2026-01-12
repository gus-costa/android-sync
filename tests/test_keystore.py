"""Tests for keystore and secrets management."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from android_sync.keystore import (
    DERIVATION_MESSAGE,
    B2Credentials,
    KeystoreError,
    _run_command,
    decrypt_secrets,
    derive_passphrase,
    encrypt_secrets,
    generate_key,
    get_b2_credentials,
    key_exists,
)


class TestRunCommand:
    @patch("android_sync.keystore.subprocess.run")
    def test_run_command_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"output")
        result = _run_command(["test", "cmd"])

        assert result == b"output"
        mock_run.assert_called_once()

    @patch("android_sync.keystore.subprocess.run")
    def test_run_command_with_input(self, mock_run):
        mock_run.return_value = MagicMock(stdout=b"signed")
        result = _run_command(["sign"], input_data=b"data")

        assert result == b"signed"
        mock_run.assert_called_once_with(
            ["sign"],
            input=b"data",
            capture_output=True,
            check=True,
        )

    @patch("android_sync.keystore.subprocess.run")
    def test_run_command_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "cmd", stderr=b"error message"
        )

        with pytest.raises(KeystoreError, match="Command failed"):
            _run_command(["bad", "cmd"])

    @patch("android_sync.keystore.subprocess.run")
    def test_run_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(KeystoreError, match="Command not found"):
            _run_command(["nonexistent"])


class TestKeyExists:
    @patch("android_sync.keystore._run_command")
    def test_key_exists_true(self, mock_run):
        mock_run.return_value = b"android-sync\nother-key\n"
        assert key_exists("android-sync") is True

    @patch("android_sync.keystore._run_command")
    def test_key_exists_false(self, mock_run):
        mock_run.return_value = b"other-key\n"
        assert key_exists("android-sync") is False

    @patch("android_sync.keystore._run_command")
    def test_key_exists_error(self, mock_run):
        mock_run.side_effect = KeystoreError("failed")
        assert key_exists("android-sync") is False


class TestGenerateKey:
    @patch("android_sync.keystore.key_exists")
    @patch("android_sync.keystore._run_command")
    def test_generate_key_success(self, mock_run, mock_exists):
        mock_exists.return_value = False
        generate_key("test-key")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "generate" in cmd
        assert "test-key" in cmd
        assert "RSA" in cmd

    @patch("android_sync.keystore.key_exists")
    def test_generate_key_already_exists(self, mock_exists):
        mock_exists.return_value = True

        with pytest.raises(KeystoreError, match="already exists"):
            generate_key("existing-key")


class TestDerivePassphrase:
    @patch("android_sync.keystore._run_command")
    def test_derive_passphrase(self, mock_run):
        # Mock signature bytes
        mock_run.return_value = b"fake-signature-bytes"

        passphrase = derive_passphrase("test-alias")

        # Should be a hex string (64 chars for sha256)
        assert len(passphrase) == 64
        assert all(c in "0123456789abcdef" for c in passphrase)

        # Verify signing was called with correct params
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "sign" in call_args[0][0]
        assert call_args[1]["input_data"] == DERIVATION_MESSAGE

    @patch("android_sync.keystore._run_command")
    def test_derive_passphrase_deterministic(self, mock_run):
        # Same signature should produce same passphrase
        mock_run.return_value = b"consistent-signature"

        p1 = derive_passphrase("alias")
        p2 = derive_passphrase("alias")

        assert p1 == p2


class TestEncryptDecryptSecrets:
    @patch("android_sync.keystore.derive_passphrase")
    @patch("android_sync.keystore._run_command")
    def test_encrypt_secrets(self, mock_run, mock_derive):
        mock_derive.return_value = "a" * 64  # fake passphrase

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "secrets.gpg"
            secrets = {"b2_key_id": "key123", "b2_app_key": "secret456"}

            encrypt_secrets(secrets, output_path)

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "gpg" in cmd
            assert "--symmetric" in cmd
            assert "AES256" in cmd

    @patch("android_sync.keystore.derive_passphrase")
    @patch("android_sync.keystore._run_command")
    def test_decrypt_secrets(self, mock_run, mock_derive):
        mock_derive.return_value = "a" * 64
        mock_run.return_value = b'{"b2_key_id": "key", "b2_app_key": "secret"}'

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.gpg"
            secrets_path.touch()  # Create empty file

            result = decrypt_secrets(secrets_path)

            assert result == {"b2_key_id": "key", "b2_app_key": "secret"}

    @patch("android_sync.keystore.derive_passphrase")
    @patch("android_sync.keystore._run_command")
    def test_decrypt_secrets_invalid_json(self, mock_run, mock_derive):
        mock_derive.return_value = "a" * 64
        mock_run.return_value = b"not valid json"

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.gpg"
            secrets_path.touch()

            with pytest.raises(KeystoreError, match="Invalid secrets file format"):
                decrypt_secrets(secrets_path)

    def test_decrypt_secrets_file_not_found(self):
        with pytest.raises(KeystoreError, match="not found"):
            decrypt_secrets(Path("/nonexistent/secrets.gpg"))


class TestGetB2Credentials:
    @patch("android_sync.keystore.decrypt_secrets")
    def test_get_b2_credentials_success(self, mock_decrypt):
        mock_decrypt.return_value = {
            "b2_key_id": "my-key-id",
            "b2_app_key": "my-app-key",
        }

        creds = get_b2_credentials(Path("/fake/secrets.gpg"))

        assert isinstance(creds, B2Credentials)
        assert creds.key_id == "my-key-id"
        assert creds.app_key == "my-app-key"

    @patch("android_sync.keystore.decrypt_secrets")
    def test_get_b2_credentials_missing_key_id(self, mock_decrypt):
        mock_decrypt.return_value = {"b2_app_key": "secret"}

        with pytest.raises(KeystoreError, match="Missing 'b2_key_id'"):
            get_b2_credentials(Path("/fake/secrets.gpg"))

    @patch("android_sync.keystore.decrypt_secrets")
    def test_get_b2_credentials_missing_app_key(self, mock_decrypt):
        mock_decrypt.return_value = {"b2_key_id": "key"}

        with pytest.raises(KeystoreError, match="Missing 'b2_app_key'"):
            get_b2_credentials(Path("/fake/secrets.gpg"))
