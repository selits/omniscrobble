#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "==> Fetching latest changes from GitHub..."
git fetch origin
git reset --hard origin/main
chmod +x start.sh upgrade.sh

echo "==> Updating Python dependencies..."
if [ -f "$DIR/.venv/bin/python" ]; then
    "$DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
    "$DIR/.venv/bin/python" -m pip install --quiet -r requirements.txt
elif [ -f "$DIR/venv/bin/python" ]; then
    "$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
    "$DIR/venv/bin/python" -m pip install --quiet -r requirements.txt
elif command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --quiet -r requirements.txt
fi

echo "==> Updating systemd service definition..."
mkdir -p "$HOME/.config/systemd/user"
if [ -f "plex-trakt.service" ]; then
    # Dynamically inject the actual installation directory into the service file
    sed "s|%h/plex-trakt-webhook|$DIR|g" plex-trakt.service > "$HOME/.config/systemd/user/omniscrobble.service"
    # Maintain plex-trakt.service as a backward-compatible alias for existing setups
    cp "$HOME/.config/systemd/user/omniscrobble.service" "$HOME/.config/systemd/user/plex-trakt.service"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable omniscrobble 2>/dev/null || true
fi

# Ensure user service keeps running after SSH logout
loginctl enable-linger "$USER" 2>/dev/null || true

echo "==> Starting/Restarting service..."
# Gracefully transition active plex-trakt service to omniscrobble
if systemctl --user is-active --quiet plex-trakt 2>/dev/null; then
    systemctl --user stop plex-trakt 2>/dev/null || true
fi
systemctl --user restart omniscrobble 2>/dev/null || systemctl --user restart plex-trakt 2>/dev/null || true

echo "==> Checking service status..."
sleep 1
if systemctl --user is-active --quiet omniscrobble 2>/dev/null; then
    echo "✓ omniscrobble service is active and running!"
    systemctl --user status omniscrobble --no-pager -n 5 2>/dev/null || true
elif systemctl --user is-active --quiet plex-trakt 2>/dev/null; then
    echo "✓ plex-trakt service is active and running!"
    systemctl --user status plex-trakt --no-pager -n 5 2>/dev/null || true
else
    echo "Service restart command issued."
fi

echo "==> Upgrade complete!"
