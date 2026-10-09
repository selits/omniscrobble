#!/usr/bin/env python3
"""Bundle ordered dashboard JavaScript source modules for the browser."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "app" / "static" / "dashboard_modules"
OUTPUT = ROOT / "app" / "static" / "dashboard.js"
MODULES = (
    "api.js",
    "core.js",
    "accounts.js",
    "settings.js",
    "reconciliation.js",
    "playback.js",
    "activity.js",
    "cowatch.js",
    "diagnostics.js",
    "webhook_tests.js",
    "arr.js",
    "tracker_auth.js",
    "webhook_inspector.js",
    "wrapped.js",
    "analytics_export.js",
    "watch_lists.js",
    "automation_rules.js",
    "command_palette.js",
)


def build_bundle() -> str:
    chunks = []
    for module in MODULES:
        module_path = MODULE_DIR / module
        if not module_path.is_file():
            raise FileNotFoundError(f"Dashboard source module missing: {module_path}")
        chunks.append(f"// ---- {module} ----\n{module_path.read_text(encoding='utf-8').rstrip()}\n")
    return "\n".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the browser bundle is out of date")
    args = parser.parse_args()
    bundle = build_bundle()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != bundle:
            print("Dashboard JavaScript bundle is out of date; run scripts/build_dashboard_bundle.py")
            return 1
        print("Dashboard JavaScript bundle is up to date.")
        return 0
    OUTPUT.write_text(bundle, encoding="utf-8")
    print(f"Bundled {len(MODULES)} dashboard modules into {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
