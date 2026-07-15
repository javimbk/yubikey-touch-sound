#!/usr/bin/env bash
set -euo pipefail

LABEL="com.$(whoami).yubikey-touch-sound"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" \
      "$HOME/.local/bin/yubikey-touch-sound.py" \
      "$HOME/Library/Logs/yubikey-touch-sound.log" \
      "$HOME/Library/Logs/yubikey-touch-sound.log.old"

echo "✓ Uninstalled $LABEL"
