"""Thread-safe loop prevention cache to suppress incoming echo webhooks during reverse sync."""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("omniscrobble.loop_prevention")


class LoopPreventionManager:
    """Tracks media items recently updated by Omniscrobble to suppress bounce-back webhooks."""

    def __init__(self, default_ttl: float = 120.0):
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self._ignored_keys: dict[str, float] = {}

    def _cleanup_expired(self, now: float) -> None:
        """Remove keys whose TTL has expired."""
        expired = [k for k, exp in self._ignored_keys.items() if now >= exp]
        for k in expired:
            del self._ignored_keys[k]

    def ignore(self, key: str, ttl: Optional[float] = None) -> None:
        """Register a ratingKey or GUID to be ignored for incoming webhooks."""
        if not key:
            return
        str_key = str(key).strip()
        if not str_key:
            return

        ttl_seconds = ttl if ttl is not None else self.default_ttl
        now = time.time()
        expiry = now + ttl_seconds

        with self._lock:
            self._cleanup_expired(now)
            self._ignored_keys[str_key] = expiry
        logger.debug("Loop prevention: ignoring key '%s' for %.1fs", str_key, ttl_seconds)

    def is_ignored(self, key: str) -> bool:
        """Return True if key is currently suppressed."""
        if not key:
            return False
        str_key = str(key).strip()
        if not str_key:
            return False

        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            if str_key in self._ignored_keys:
                logger.info("Loop prevention: suppressed echo event for key '%s'", str_key)
                return True
        return False

    def clear(self) -> None:
        """Clear all suppressed keys."""
        with self._lock:
            self._ignored_keys.clear()

    def get_active_count(self) -> int:
        """Return count of currently active suppressed keys."""
        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            return len(self._ignored_keys)


loop_prevention = LoopPreventionManager()
