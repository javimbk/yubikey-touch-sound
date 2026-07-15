#!/usr/bin/env bash
set -euo pipefail

LABEL="com.$(whoami).yubikey-touch-sound"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_TARGET="$HOME/.local/bin/yubikey-touch-sound.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/yubikey-touch-sound.log"

# A bare /usr/bin/python3 stub (no Command Line Tools) would make the agent
# crash-loop instead of failing here, so check up front.
if ! /usr/bin/python3 -c 'pass' 2>/dev/null; then
    echo "✗ /usr/bin/python3 is not usable — install the Xcode Command Line Tools first:" >&2
    echo "    xcode-select --install" >&2
    exit 1
fi

mkdir -p "$HOME/.local/bin" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cp "$REPO_DIR/yubikey-touch-sound.py" "$SCRIPT_TARGET"
chmod +x "$SCRIPT_TARGET"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$SCRIPT_TARGET</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
</dict>
</plist>
EOF

# Reload if already installed, so reruns pick up script changes.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

# Start from a fresh log so the health check below can't match stale lines
# (and so reinstalls keep the log from growing forever).
if [ -f "$LOG_FILE" ]; then
    mv "$LOG_FILE" "$LOG_FILE.old"
fi

launchctl bootstrap "gui/$(id -u)" "$PLIST"

# A doomed install (e.g. an account that may not read the log stream) can
# still look alive right after bootstrap — give it time to fail before
# declaring success.
echo "Verifying (5s)..."
sleep 5
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -q "state = running" \
    && ! tail -5 "$LOG_FILE" 2>/dev/null | grep -q "log stream died immediately"; then
    echo "✓ Installed and running as $LABEL"
    echo "  Test it: touch-gated action (e.g. SSH with your YubiKey) should now ping."
else
    echo "✗ Agent installed but not healthy — check $LOG_FILE" >&2
    tail -5 "$LOG_FILE" 2>/dev/null >&2 || true
    exit 1
fi
