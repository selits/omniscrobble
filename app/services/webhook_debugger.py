"""In-memory raw webhook inspector and payload debugger for Omniscrobble.

Maintains a secure, secret-redacted ring buffer of recent raw incoming payloads
across Plex, Jellyfin, Emby, Radarr, Sonarr, and standalone players, enabling
1-click live payload inspection, filter simulation, and interactive replaying.
"""
from __future__ import annotations

import collections
import datetime
import logging
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("omniscrobble.webhook_debugger")

def is_sensitive_key(key: Any) -> bool:
    """Check if a header or payload key contains credential/secret patterns."""
    norm = str(key).lower().replace("-", "").replace("_", "")
    if any(p in norm for p in ("token", "secret", "password", "passwd", "apikey", "credential", "cookie", "authorization", "authenticate")):
        return True
    if "oauth" in norm or norm in ("auth", "authenticator"):
        return True
    return False


def sanitize_headers(headers: Optional[dict[str, str]]) -> dict[str, str]:
    """Return a copy of request headers with sensitive credentials redacted."""
    if not headers:
        return {}
    sanitized: dict[str, str] = {}
    for k, v in headers.items():
        if is_sensitive_key(k):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = str(v)
    return sanitized


def sanitize_payload(payload: Any) -> Any:
    """Recursively scrub sensitive credential tokens from captured payloads."""
    if isinstance(payload, dict):
        clean_dict = {}
        for k, v in payload.items():
            if is_sensitive_key(k):
                clean_dict[k] = "[REDACTED]"
            else:
                clean_dict[k] = sanitize_payload(v)
        return clean_dict
    elif isinstance(payload, list):
        return [sanitize_payload(i) for i in payload]
    return payload


class WebhookDebugger:
    """Thread-safe ring buffer capturing raw incoming payloads for diagnostics and replay."""

    def __init__(self, maxlen: int = 25):
        self._maxlen = maxlen
        self._buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=maxlen)
        self._auth_rejections: collections.deque[dict[str, Any]] = collections.deque(maxlen=100)
        self._lock = threading.Lock()

    def record_auth_rejection(self, source: str, endpoint: str) -> None:
        """Record an auth failure without retaining request headers, tokens, payloads, or IPs."""
        now = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            self._auth_rejections.append({
                "source": str(source).lower(),
                "endpoint": str(endpoint),
                "timestamp": now.isoformat(),
                "recorded_at": now.timestamp(),
            })

    def get_auth_rejection_summary(self, window_seconds: int = 86400) -> dict[str, Any]:
        """Summarize recent authentication failures using endpoint counts only."""
        window_seconds = max(1, int(window_seconds))
        cutoff = time.time() - window_seconds
        with self._lock:
            recent = [item for item in self._auth_rejections if item["recorded_at"] >= cutoff]
        by_endpoint: dict[str, int] = {}
        for item in recent:
            endpoint = item["endpoint"]
            by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
        return {
            "window_seconds": window_seconds,
            "count": len(recent),
            "last_attempt": recent[-1]["timestamp"] if recent else None,
            "by_endpoint": by_endpoint,
        }

    def record(
        self,
        source: str,
        endpoint: str,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
        status: str = "received",
        reason: Optional[str] = None,
        event: Optional[str] = None,
        media_title: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record an incoming raw webhook event into the ring buffer."""
        now = datetime.datetime.now()
        clean_payload = sanitize_payload(payload)
        clean_headers = sanitize_headers(headers)
        entry_id = f"wh_{uuid.uuid4().hex[:8]}"

        # Infer event type if not explicitly supplied
        inferred_event = event or clean_payload.get("event") or clean_payload.get("NotificationType") or clean_payload.get("action") or "unknown"
        inferred_title = media_title
        if not inferred_title and isinstance(clean_payload, dict):
            metadata = clean_payload.get("Metadata") or clean_payload.get("Item") or {}
            if isinstance(metadata, dict):
                inferred_title = metadata.get("title") or clean_payload.get("title")
            else:
                inferred_title = clean_payload.get("title")

        entry = {
            "id": entry_id,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "iso_timestamp": now.isoformat(),
            "source": source.lower(),
            "endpoint": endpoint,
            "headers": clean_headers,
            "payload": clean_payload,
            "status": status,
            "reason": reason or "",
            "event": str(inferred_event),
            "media_title": str(inferred_title or ""),
        }

        with self._lock:
            self._buffer.appendleft(entry)

        return dict(entry)

    def get_history(self, limit: int = 15) -> list[dict[str, Any]]:
        """Return the most recent recorded webhook payloads."""
        with self._lock:
            items = list(self._buffer)
        return items[:limit]

    def get_payload(self, entry_id: str) -> Optional[dict[str, Any]]:
        """Find a specific recorded webhook payload by unique ID."""
        with self._lock:
            for item in self._buffer:
                if item["id"] == entry_id:
                    return dict(item)
        return None

    def update_status(self, entry_id: str, status: str, reason: Optional[str] = None) -> bool:
        """Update the processing outcome for a captured webhook."""
        with self._lock:
            for item in self._buffer:
                if item["id"] == entry_id:
                    item["status"] = status
                    if reason:
                        item["reason"] = reason
                    return True
        return False

    def clear(self) -> None:
        """Purge the in-memory ring buffer."""
        with self._lock:
            self._buffer.clear()
            self._auth_rejections.clear()


webhook_debugger = WebhookDebugger()
