#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

if [ -f "$DIR/.venv/bin/python" ]; then
    exec "$DIR/.venv/bin/python" main.py
elif [ -f "$DIR/venv/bin/python" ]; then
    exec "$DIR/venv/bin/python" main.py
else
    exec python3 main.py
fi
