#!/data/data/com.termux/files/usr/bin/bash
# Termux initialization script for android-sync
# Run with: bash scripts/termux-init.sh

set -e

echo "==> Updating package lists..."
pkg update -y

echo "==> Installing dependencies..."
pkg install -y python rclone termux-api gnupg

echo "==> Installing android-sync..."
pip install .

echo "==> Creating config directory..."
mkdir -p ~/.config/android-sync

if [ ! -f ~/.config/android-sync/config.toml ]; then
    cp config.example.toml ~/.config/android-sync/config.toml
    echo "==> Copied example config to ~/.config/android-sync/config.toml"
else
    echo "==> Config file already exists, skipping."
fi

echo ""
echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo ""
echo "  1. Get your B2 credentials from https://www.backblaze.com/"
echo ""
echo "  2. Initialize credentials (generates keystore key + encrypts secrets):"
echo "     android-sync setup"
echo ""
echo "  3. Edit your config:"
echo "     nano ~/.config/android-sync/config.toml"
echo ""
echo "  4. Test with dry-run:"
echo "     android-sync run --all --dry-run"
