#!/data/data/com.termux/files/usr/bin/bash
# Termux initialization script for android-sync
# Run with: bash scripts/termux-init.sh

set -e

echo "==> Updating package lists..."
pkg update -y

echo "==> Installing dependencies..."
pkg install -y python rclone termux-api

echo "==> Installing android-sync..."
pip install .

echo "==> Creating config directory..."
mkdir -p ~/.config/android-sync

if [ ! -f ~/.config/android-sync/config.toml ]; then
    cp config.example.toml ~/.config/android-sync/config.toml
    echo "==> Copied example config to ~/.config/android-sync/config.toml"
    echo "    Edit this file with your settings before running."
else
    echo "==> Config file already exists, skipping."
fi

echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Store your B2 credentials:"
echo "     termux-keystore set b2-key-id"
echo "     termux-keystore set b2-app-key"
echo ""
echo "  2. Edit your config:"
echo "     nano ~/.config/android-sync/config.toml"
echo ""
echo "  3. Test with dry-run:"
echo "     android-sync run --all --dry-run"
