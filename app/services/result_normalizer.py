"""Normalize tracker responses for delivery and retry decisions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class NormalizedResult:
    state: str
    category: str
    retryable: bool = False
    status_code: int | None = None


_TRANSIENT_WORDS = (
    "connect", "timeout", "network", "service unavailable", "rate limit",
    "slow_down", "temporarily unavailable", "temporarily blocked",
)
_SUCCESS_STATES = {"success", "ok", "200", "201", "202", "204", "deleted"}
_SKIPPED_STATES = {"skipped", "ignored", "disabled", "not_configured", "unsupported"}


def normalize_result(result: Any) -> NormalizedResult:
    """Classify varied client response shapes without retaining response text."""
    if isinstance(result, int) and not isinstance(result, bool) and 100 <= result <= 599:
        if 200 <= result < 300 or result == 409:
            return NormalizedResult("success", "delivered", False, result)
        if result == 429 or result >= 500:
            return NormalizedResult("failed", "transient", True, result)
        if 400 <= result < 500:
            return NormalizedResult("failed", "authorization_or_configuration" if result in {401, 403} else "permanent", False, result)
    if not isinstance(result, (dict, list)):
        return NormalizedResult("success" if result else "skipped", "delivered" if result else "intentional_skip")

    statuses: list[str] = []
    codes: list[int] = []
    messages: list[str] = []
    flags: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("status", "state"):
                item = value.get(key)
                if isinstance(item, (str, int)):
                    normalized = str(item).strip().lower()
                    statuses.append(normalized)
                    if normalized.isdigit() and 100 <= int(normalized) <= 599:
                        codes.append(int(normalized))
            for key in ("code", "status_code", "http_status"):
                try:
                    code = int(value.get(key))
                except (TypeError, ValueError):
                    continue
                if 100 <= code <= 599:
                    codes.append(code)
            for key in ("status",):
                item = value.get(key)
                if isinstance(item, int) and 100 <= item <= 599:
                    codes.append(item)
            for key in ("error", "reason", "message", "detail"):
                item = value.get(key)
                if isinstance(item, str):
                    messages.append(item.lower())
                    codes.extend(int(match) for match in re.findall(r"\b(?:http\s*)?(\d{3})\b", item.lower()) if 400 <= int(match) <= 599)
            if value.get("queued"):
                flags.add("queued")
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(result)
    if "queued" in flags:
        return NormalizedResult("queued", "queued")
    code = next((c for c in codes if c >= 400), next(iter(codes), None))
    text = " ".join(messages + statuses)
    if code == 429 or (code is not None and code >= 500) or any(word in text for word in _TRANSIENT_WORDS):
        return NormalizedResult("failed", "transient", True, code)
    if code in {401, 403} or any(term in text for term in ("unauthorized", "not authenticated", "not configured", "invalid token")):
        return NormalizedResult("failed", "authorization_or_configuration", False, code)
    if code == 409:
        return NormalizedResult("success", "already_delivered", False, code)
    if code is not None and 400 <= code < 500:
        return NormalizedResult("failed", "permanent", False, code)
    if any(status in {"error", "failed", "failure"} for status in statuses) or any("http 4" in m for m in messages):
        return NormalizedResult("failed", "unknown_failure", False, code)
    if any(status in _SUCCESS_STATES for status in statuses):
        return NormalizedResult("success", "delivered")
    if any(status in _SKIPPED_STATES for status in statuses):
        return NormalizedResult("skipped", "unsupported" if "unsupported" in statuses else "intentional_skip")
    if any(status.isdigit() and 200 <= int(status) < 300 for status in statuses):
        return NormalizedResult("success", "delivered", False, code)
    if isinstance(result, dict) and result.get("reason"):
        return NormalizedResult("skipped", "intentional_skip")
    return NormalizedResult("success", "delivered", False, code)
