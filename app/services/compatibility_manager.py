"""Read-only app, local data schema, and explicit integration test compatibility checks."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CompatibilityManager:
    RULESET_VERSION = 1
    LAST_REVIEWED = "2026-10-09"
    PYTHON_MINIMUM = (3, 10)
    SCHEMAS = {
        "settings": {"current": 1, "source": "app/services/settings_manager.py"},
        "watch_lists": {"current": 1, "source": "app/services/watch_list_manager.py"},
        "automation_rules": {"current": 1, "source": "app/services/automation_rules.py"},
        "dashboard_accounts": {"current": 1, "source": "app/services/dashboard_auth_manager.py"},
        "offline_queue": {"current": 1, "source": "app/services/queue_manager.py"},
    }
    INTEGRATION_RULES = {
        "sonarr": {
            "supported_major_versions": [3, 4],
            "source": "https://sonarr.tv/docs/api/",
            "basis": "The official Sonarr API documentation states its v3 API docs apply to Sonarr v3 and v4.",
        },
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._integration_checks: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _classify_schema(name: str, observed: Any, exists: bool = True) -> dict[str, Any]:
        rule = CompatibilityManager.SCHEMAS[name]
        current = rule["current"]
        if not exists:
            status, version, action = "not_created", None, "No data exists yet; Omniscrobble will create it using the current format."
        elif type(observed) is not int or observed < 0:
            status, version, action = "unknown", observed if isinstance(observed, (str, int)) else None, "The saved format version could not be identified; review a backup before changing this data."
        elif observed > current:
            status, version, action = "unsupported", observed, "This data was written by a newer format. Keep a backup and upgrade Omniscrobble before editing it."
        elif observed < current:
            status, version, action = "legacy_supported", observed, "This older format is still readable; it will be written in the current format when the data is next saved."
        else:
            status, version, action = "supported", observed, "Saved format matches the current supported version."
        return {"id": name, "status": status, "observed_version": version, "current_version": current,
                "source": rule["source"], "action": action}

    @staticmethod
    def _json_version(path: Path, field: str = "version") -> tuple[bool, Any]:
        if not path.exists():
            return False, None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return True, value.get(field, 0) if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return True, None

    def record_integration_check(self, integration: str, status: str, version: Any = None) -> None:
        """Remember only the result and a conservative version string from an explicit test."""
        raw_version = str(version or "")
        clean_version = raw_version[:40] if re.fullmatch(r"[A-Za-z0-9._+ -]{1,40}", raw_version) else "unknown"
        rule = self.INTEGRATION_RULES.get(integration)
        match = re.match(r"^v?(\d+)(?:\.|$)", clean_version, re.IGNORECASE)
        if not rule or not match:
            compatibility = "unknown"
            explanation = "No reviewed version threshold is available for this integration/version."
        else:
            major = int(match.group(1))
            supported = rule["supported_major_versions"]
            if major in supported:
                compatibility = "supported"
                explanation = rule["basis"]
            elif major < min(supported):
                compatibility = "unsupported"
                explanation = f"This major version is below the reviewed supported range ({min(supported)}–{max(supported)})."
            else:
                compatibility = "unknown"
                explanation = "This version is newer than the reviewed range and needs a rule update before classification."
        with self._lock:
            self._integration_checks[integration] = {
                "status": "reachable" if status == "connected" else "unreachable",
                "version": clean_version or "unknown",
                "compatibility": compatibility,
                "explanation": explanation,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    def inspect(
        self, *, app_version: str, python_version: tuple[int, int], settings_version: Any,
        settings_exists: bool, watch_lists_path: Path, automation_path: Path,
        accounts_path: Path, queue_path: Path,
    ) -> dict[str, Any]:
        """Build a redacted compatibility report without contacting integrations."""
        auto_exists, auto_version = self._json_version(automation_path)
        watch_lists_exists, watch_list_version = self._json_version(watch_lists_path)
        accounts_exists, accounts_version = self._json_version(accounts_path)
        if queue_path.exists():
            try:
                with sqlite3.connect(f"{queue_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
                    queue_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            except (OSError, sqlite3.Error, TypeError, ValueError):
                queue_version = None
            queue_exists = True
        else:
            queue_version, queue_exists = None, False
        schemas = [
            self._classify_schema("settings", settings_version, settings_exists),
            self._classify_schema("watch_lists", watch_list_version, watch_lists_exists),
            self._classify_schema("automation_rules", auto_version, auto_exists),
            self._classify_schema("dashboard_accounts", accounts_version, accounts_exists),
            self._classify_schema("offline_queue", queue_version, queue_exists),
        ]
        python_supported = python_version >= self.PYTHON_MINIMUM
        with self._lock:
            integrations = {name: dict(result) for name, result in self._integration_checks.items()}
        errors = [row for row in schemas if row["status"] == "unsupported"]
        warnings = [row for row in schemas if row["status"] in {"unknown", "legacy_supported"}]
        if not python_supported or errors:
            status = "action_required"
        elif warnings:
            status = "warning"
        else:
            status = "compatible"
        return {
            "status": status,
            "application": {"name": "Omniscrobble", "version": app_version},
            "runtime": {"python_version": f"{python_version[0]}.{python_version[1]}", "minimum_supported": "3.10", "status": "supported" if python_supported else "unsupported"},
            "schemas": schemas,
            "integrations": integrations,
            "integration_version_policy": "Unknown versions remain unknown. Integration reachability and version are recorded only after an explicit System connection test.",
            "ruleset": {
                "version": self.RULESET_VERSION,
                "last_reviewed": self.LAST_REVIEWED,
                "sources": ["AGENTS.md Python 3.10+ requirement", *[row["source"] for row in schemas], *[rule["source"] for rule in self.INTEGRATION_RULES.values()]],
                "integration_rules": self.INTEGRATION_RULES,
            },
            "summary": (
                f"{len(errors)} unsupported local format(s) and {len(warnings)} legacy or unknown format(s)."
                if errors or warnings else "Runtime and saved local formats are compatible."
            ),
        }
