"""Validated, ordered automation rules for media events."""

from __future__ import annotations

from app.services.persistence_guard import persisted_mutation

from datetime import datetime, time
import threading
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.atomic_writer import atomic_write_json

_ACTIONS = {"allow", "suppress", "route", "review"}
_TRACKERS = {"trakt", "simkl", "anilist", "mal", "tmdb", "kitsu", "letterboxd", "serializd", "mdblist"}
_MEDIA_TYPES = {"movie", "episode", "show"}
_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


class AutomationRuleError(ValueError):
    """Raised when a rule is invalid or unsafe to save."""


class AutomationRulesManager:
    """Persists rules and evaluates them against a normalized media event."""

    def __init__(self, rules_file: Path):
        self.rules_file = Path(rules_file)
        self._lock = threading.RLock()
        self._rules: list[dict[str, Any]] = []
        self._future_schema_version: int | None = None
        self._load()

    @staticmethod
    def _clean_list(value: Any, field: str, allowed: set[str] | None = None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise AutomationRuleError(f"{field} must be a list.")
        result: list[str] = []
        for raw in value:
            item = str(raw).strip()
            if not item:
                continue
            if allowed is not None and item.lower() not in allowed and item != "*":
                raise AutomationRuleError(f"Unsupported {field} value: {item}.")
            if not any(existing.lower() == item.lower() for existing in result):
                result.append(item)
        return result

    @classmethod
    def validate_rule(cls, payload: dict[str, Any], *, existing_id: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AutomationRuleError("Rule must be an object.")
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 80:
            raise AutomationRuleError("Rule name must contain 1–80 characters.")
        action = str(payload.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            raise AutomationRuleError("Action must be allow, suppress, route, or review.")
        conditions = payload.get("conditions")
        if not isinstance(conditions, dict):
            raise AutomationRuleError("Conditions must be an object.")
        servers = cls._clean_list(conditions.get("servers"), "servers", {"plex", "jellyfin", "emby"})
        libraries = cls._clean_list(conditions.get("libraries"), "libraries")
        media_types = cls._clean_list(conditions.get("media_types"), "media_types", _MEDIA_TYPES)
        devices = cls._clean_list(conditions.get("devices"), "devices")
        users = cls._clean_list(conditions.get("users"), "users")
        time_window = conditions.get("time_window")
        clean_window = None
        if time_window is not None:
            if not isinstance(time_window, dict):
                raise AutomationRuleError("time_window must be an object.")
            start = str(time_window.get("start") or "")
            end = str(time_window.get("end") or "")
            if len(start) != 5 or len(end) != 5:
                raise AutomationRuleError("Time window start/end must use HH:MM format.")
            try:
                time.fromisoformat(start)
                time.fromisoformat(end)
            except ValueError as exc:
                raise AutomationRuleError("Time window start/end must use HH:MM format.") from exc
            raw_days = time_window.get("days", [])
            if not isinstance(raw_days, list):
                raise AutomationRuleError("Time window days must be a list of weekday names.")
            days = [str(day).lower()[:3] for day in raw_days]
            if not days or any(day not in _DAYS for day in days):
                raise AutomationRuleError("Time window days must include one or more weekday names.")
            timezone = str(time_window.get("timezone") or "UTC")
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise AutomationRuleError("Time window timezone must be a valid IANA timezone.") from exc
            clean_window = {"start": start, "end": end, "days": list(dict.fromkeys(days)), "timezone": timezone}
        if not any((servers, libraries, media_types, devices, users, clean_window)):
            raise AutomationRuleError("Add at least one condition; match-all rules are not allowed.")
        if not clean_window and not any(
            values and "*" not in values
            for values in (servers, libraries, media_types, devices, users)
        ):
            raise AutomationRuleError("Wildcard-only conditions match every event; add a specific condition to narrow the rule.")
        trackers = [value.lower() for value in cls._clean_list(payload.get("trackers"), "trackers", _TRACKERS)]
        profiles = cls._clean_list(payload.get("profiles"), "profiles")
        if action == "route" and not trackers and not profiles:
            raise AutomationRuleError("A route rule must select at least one tracker or profile.")
        if action != "route" and (trackers or profiles):
            raise AutomationRuleError("Trackers and profiles can only be set for route actions.")
        try:
            priority = int(payload.get("priority", 100))
        except (TypeError, ValueError) as exc:
            raise AutomationRuleError("Priority must be a whole number from 0 to 10000.") from exc
        if not 0 <= priority <= 10000:
            raise AutomationRuleError("Priority must be a whole number from 0 to 10000.")
        return {
            "id": str(existing_id or payload.get("id") or f"rule_{uuid.uuid4().hex[:10]}"),
            "name": name,
            "enabled": bool(payload.get("enabled", True)),
            "priority": priority,
            "conditions": {
                "servers": servers,
                "libraries": libraries,
                "media_types": media_types,
                "devices": devices,
                "users": users,
                "time_window": clean_window,
            },
            "action": action,
            "trackers": trackers,
            "profiles": profiles,
        }

    def _load(self) -> None:
        self._future_schema_version = None
        try:
            import json
            payload = json.loads(self.rules_file.read_text(encoding="utf-8"))
            version = payload.get("version", 0) if isinstance(payload, dict) else None
            if type(version) is int and version > 1:
                self._future_schema_version = version
            items = payload.get("rules", []) if isinstance(payload, dict) else []
            self._rules = [self.validate_rule(item, existing_id=str(item.get("id") or "")) for item in items if isinstance(item, dict)]
        except FileNotFoundError:
            self._rules = []
        except (OSError, ValueError, TypeError):
            self._rules = []

    def _save(self) -> None:
        if self._future_schema_version is not None:
            raise AutomationRuleError("Automation rules use a newer schema; refusing to overwrite them with this version.")
        atomic_write_json(self.rules_file, {"version": 1, "rules": self._rules})

    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(rule) for rule in sorted(self._rules, key=lambda rule: (rule["priority"], self._rules.index(rule)))]

    @persisted_mutation("_rules", AutomationRuleError)
    def replace_rules(self, payload: Any) -> list[dict[str, Any]]:
        validated = self.validate_rules(payload)
        with self._lock:
            self._rules = validated
            self._save()
            return self.list_rules()

    @classmethod
    def validate_rules(cls, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            raise AutomationRuleError("Rules must be provided as a list.")
        if len(payload) > 200:
            raise AutomationRuleError("A maximum of 200 automation rules is supported.")
        validated = [cls.validate_rule(item) for item in payload]
        ids = [rule["id"] for rule in validated]
        if len(ids) != len(set(ids)):
            raise AutomationRuleError("Rule IDs must be unique.")
        for index, rule in enumerate(validated):
            if rule["action"] == "suppress" and any(other["priority"] == rule["priority"] and other["action"] != "suppress" for other in validated[:index]):
                raise AutomationRuleError("A suppress rule cannot follow a non-suppress rule at the same priority.")
        return validated

    @staticmethod
    def _value_matches(values: list[str], actual: Any, *, case_sensitive: bool = False) -> bool:
        if not values:
            return True
        if actual is None:
            return False
        actual_text = str(actual).strip()
        if "*" in values:
            return True
        return any((value == actual_text if case_sensitive else value.lower() == actual_text.lower()) for value in values)

    @classmethod
    def _matches(cls, rule: dict[str, Any], event: dict[str, Any], now: datetime) -> tuple[bool, list[str]]:
        conditions = rule["conditions"]
        checks = (
            ("servers", event.get("server")),
            ("libraries", event.get("library")),
            ("media_types", event.get("media_type")),
            ("users", event.get("user")),
        )
        reasons: list[str] = []
        for field, actual in checks:
            expected = conditions.get(field) or []
            if not cls._value_matches(expected, actual):
                return False, []
            if expected:
                reasons.append(f"{field.replace('_', ' ')} matched {actual}")
        expected_devices = conditions.get("devices") or []
        actual_devices = [event.get("device"), event.get("player")]
        if expected_devices and not any(cls._value_matches(expected_devices, actual) for actual in actual_devices if actual):
            return False, []
        if expected_devices:
            reasons.append("device/player matched")
        window = conditions.get("time_window")
        if window:
            local = now.astimezone(ZoneInfo(window["timezone"]))
            day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[local.weekday()]
            start = time.fromisoformat(window["start"])
            end = time.fromisoformat(window["end"])
            current = local.timetz().replace(tzinfo=None)
            in_range = start <= current <= end if start <= end else current >= start or current <= end
            valid_day = day in window["days"]
            # For overnight windows, the after-midnight segment belongs to the previous day's window.
            if start > end and current <= end:
                prior = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[(local.weekday() - 1) % 7]
                valid_day = prior in window["days"]
            if not (in_range and valid_day):
                return False, []
            reasons.append(f"time matched {window['start']}–{window['end']} {window['timezone']}")
        return True, reasons or ["conditions matched"]

    def evaluate(self, event: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        return self.evaluate_rules(event, rules=None, now=now)

    def evaluate_rules(self, event: dict[str, Any], rules: list[dict[str, Any]] | None, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        with self._lock:
            selected_rules = self._rules if rules is None else rules
            ordered = sorted(enumerate(selected_rules), key=lambda pair: (pair[1]["priority"], pair[0]))
        matched: list[tuple[dict[str, Any], list[str]]] = []
        for _, rule in ordered:
            if not rule["enabled"]:
                continue
            does_match, reasons = self._matches(rule, event, now)
            if does_match:
                matched.append((rule, reasons))
        if not matched:
            return {"decision": "allow", "reason": "No automation rule matched; default action is allow.", "matched_rule": None, "conflicts": []}
        winner, reasons = matched[0]
        conflicts = [
            {"id": rule["id"], "name": rule["name"], "action": rule["action"], "priority": rule["priority"]}
            for rule, _ in matched[1:]
            if rule["action"] != winner["action"]
        ]
        reason = f"Matched '{winner['name']}' (priority {winner['priority']}): " + "; ".join(reasons)
        if conflicts:
            reason += f". {len(conflicts)} lower-precedence conflicting rule(s) also matched."
        return {
            "decision": winner["action"],
            "reason": reason,
            "matched_rule": {"id": winner["id"], "name": winner["name"], "priority": winner["priority"], "action": winner["action"]},
            "trackers": list(winner["trackers"]),
            "profiles": list(winner["profiles"]),
            "conflicts": conflicts,
        }
