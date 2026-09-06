#!/usr/bin/env bash
# install_launcher.sh — Installs Instagram Digest launcher to Desktop and Ubuntu GNOME applications menu
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$SCRIPT_DIR/launch.sh"

DESKTOP_ENTRY="[Desktop Entry]
Version=1.0
Type=Application
Name=Instagram Digest
GenericName=Weekly Video Briefing
Comment=High-Signal Anti-Doomscroll Weekly Instagram Digest
Exec=$SCRIPT_DIR/launch.sh
Path=$SCRIPT_DIR
Icon=$SCRIPT_DIR/assets/icon.svg
Terminal=false
Categories=AudioVideo;Video;Network;Utility;
Keywords=Instagram;Reels;Digest;Video;
StartupNotify=true
"

# 1. Install to GNOME Applications Menu (for Search and Dock Pinning)
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
echo "$DESKTOP_ENTRY" > "$APP_DIR/InstagramDigest.desktop"
chmod 755 "$APP_DIR/InstagramDigest.desktop"
echo "Installed to GNOME Applications menu: $APP_DIR/InstagramDigest.desktop"

# 2. Install to User Desktop (if Desktop directory exists)
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP)"
else
    DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
fi

if [[ -n "$DESKTOP_DIR" && -d "$DESKTOP_DIR" ]]; then
    echo "$DESKTOP_ENTRY" > "$DESKTOP_DIR/InstagramDigest.desktop"
    chmod 755 "$DESKTOP_DIR/InstagramDigest.desktop"
    # Mark as trusted if gio is available
    if command -v gio >/dev/null 2>&1; then
        gio set "$DESKTOP_DIR/InstagramDigest.desktop" metadata::trusted true || true
    fi
    echo "Installed to User Desktop: $DESKTOP_DIR/InstagramDigest.desktop"
fi

echo ""
echo "================================================================="
echo " Instagram Digest Desktop Launcher Installed!"
echo " 1. Search 'Instagram Digest' in Ubuntu Dash."
echo " 2. Right-click and choose 'Pin to Dash' / 'Add to Favorites'."
echo " 3. Or double-click the desktop shortcut."
echo "================================================================="
