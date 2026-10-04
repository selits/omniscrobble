import time
from typing import Any, Optional

try:
    from app.services.notifier import format_media_title, get_trakt_url
    from app.plex_parser import ParsedMedia
except ImportError:
    from notifier import format_media_title, get_trakt_url
    from plex_parser import ParsedMedia


def mask_username_simple(username: Optional[str]) -> str:
    """Mask username for non-admin viewers."""
    if not username:
        return ""
    if len(username) <= 2:
        return username[0] + "*" * (len(username) - 1) if len(username) == 2 else "*"
    if len(username) <= 4:
        return username[:1] + "*" * (len(username) - 1)
    return username[:2] + "*" * (len(username) - 2)


class PlaybackManager:
    """Tracks active Plex streaming sessions and recently finished playback."""

    def __init__(self, stale_timeout_seconds: int = 21600):
        self.stale_timeout_seconds = stale_timeout_seconds
        self.sessions: dict[str, dict[str, Any]] = {}
        self.recently_finished: Optional[dict[str, Any]] = None

    @staticmethod
    def estimate_position(session: dict[str, Any], now: Optional[float] = None) -> Optional[tuple[float, float]]:
        """Estimate (current_sec, duration_sec) for a session, or None if duration is unknown.

        A missing/None view_offset_ms is treated as 0 (playback started from the beginning).
        Only advances with wall-clock time while the session state is "playing".
        """
        dur_ms = session.get("duration_ms")
        if not dur_ms or dur_ms <= 0:
            return None
        now = time.time() if now is None else now
        dur_sec = dur_ms / 1000.0
        offset_sec = (session.get("view_offset_ms") or 0) / 1000.0
        current_sec = offset_sec
        if session.get("state", "playing") == "playing":
            elapsed = max(0.0, now - session.get("updated_at", now))
            current_sec = min(dur_sec, offset_sec + elapsed)
        return current_sec, dur_sec

    def _get_key(self, media: ParsedMedia) -> str:
        player_identifier = media.player or media.device or "default"
        return f"{media.username}:{player_identifier}"

    def update_playback(self, media: ParsedMedia, state: str = "playing") -> dict[str, Any]:
        """Record or update active streaming session."""
        key = self._get_key(media)
        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)

        now = time.time()
        existing_session = self.sessions.get(key)
        last_hb = existing_session.get("last_heartbeat_at", now) if existing_session else now

        session = {
            "key": key,
            "username": media.username,
            "player": media.player or "Plex Client",
            "device": media.device or "",
            "media_type": media.media_type,
            "title": title_str,
            "show_title": media.show_title,
            "season": media.season,
            "episode": media.episode,
            "year": media.year,
            "state": state,  # "playing" or "paused"
            "progress": round(media.progress, 1),
            "duration_ms": media.duration_ms,
            "view_offset_ms": media.view_offset_ms,
            "updated_at": now,
            "last_heartbeat_at": last_hb,
            "trakt_url": trakt_url,
            "poster_url": media.poster_url,
            "backdrop_url": media.backdrop_url,
            "ids": media.ids,
            "parsed_media": media,
        }
        self.sessions[key] = session
        return session

    def stop_playback(self, media: ParsedMedia) -> Optional[dict[str, Any]]:
        """Remove session when playback stops or scrobbles, saving to recently finished."""
        key = self._get_key(media)
        existing = self.sessions.pop(key, None)

        # Fallback search if player wasn't specified accurately
        if not existing:
            for k, s in list(self.sessions.items()):
                if s.get("username") == media.username:
                    existing = self.sessions.pop(k, None)
                    break

        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)
        finished_entry = {
            "title": title_str,
            "username": media.username,
            "player": media.player or (existing.get("player") if existing else "Plex Client"),
            "device": media.device or (existing.get("device") if existing else ""),
            "media_type": media.media_type,
            "progress": round(media.progress, 1),
            "finished_at": time.time(),
            "trakt_url": trakt_url,
            "poster_url": media.poster_url or (existing.get("poster_url") if existing else None),
            "backdrop_url": media.backdrop_url or (existing.get("backdrop_url") if existing else None),
            "remaining_str": "Finished",
        }
        self.recently_finished = finished_entry
        return finished_entry

    def get_active_sessions(self, is_admin: bool = True) -> list[dict[str, Any]]:
        """Return non-stale active streaming sessions, applying privacy masking if needed."""
        now = time.time()
        active = []
        for key, s in list(self.sessions.items()):
            if now - s.get("updated_at", 0) > self.stale_timeout_seconds:
                self.sessions.pop(key, None)
                continue
            item = dict(s)
            item.pop("parsed_media", None)

            # Estimate real-time playback progress when streaming
            pos = self.estimate_position(s, now)
            if pos:
                current_sec, dur_sec = pos
                rem_min = int(round(max(0.0, dur_sec - current_sec) / 60.0))
                if s.get("state") == "playing":
                    est_prog = min(99.0, max(0.0, (current_sec / dur_sec) * 100.0))
                    item["progress"] = round(est_prog, 1)
                    item["remaining_str"] = f"{rem_min}m left"
                else:
                    item["remaining_str"] = f"{rem_min}m left (paused)"

            if not is_admin:
                item["username"] = mask_username_simple(item["username"])
                item["player"] = ""
                item["device"] = ""
                item["key"] = f"{item['username']}:client"
            active.append(item)

        active.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return active

    def get_heartbeat_candidates(self, interval_seconds: int = 600) -> list[dict[str, Any]]:
        """Return active streaming sessions in playing state that need keep-alive heartbeats."""
        now = time.time()
        candidates = []
        for key, s in list(self.sessions.items()):
            if s.get("state") != "playing":
                continue
            if now - s.get("updated_at", 0) > self.stale_timeout_seconds:
                continue
            last_hb = s.get("last_heartbeat_at", s.get("updated_at", now))
            if now - last_hb >= interval_seconds:
                candidates.append(s)
        return candidates

    def record_heartbeat(self, key: str, progress: Optional[float] = None) -> None:
        """Update heartbeat timestamp and optional estimated progress for an active session."""
        if key in self.sessions:
            self.sessions[key]["last_heartbeat_at"] = time.time()
            if progress is not None:
                self.sessions[key]["progress"] = round(progress, 1)

    def get_active_count(self) -> int:
        """Return the number of currently active playback sessions."""
        return len(self.get_active_sessions(is_admin=True))


    def get_recently_finished(self, is_admin: bool = True) -> Optional[dict[str, Any]]:
        """Return recently finished media item if within 24h, applying privacy masking if needed."""
        if not self.recently_finished:
            return None
        if time.time() - self.recently_finished.get("finished_at", 0) > 86400:
            self.recently_finished = None
            return None

        entry = dict(self.recently_finished)
        if not is_admin:
            entry["username"] = mask_username_simple(entry["username"])
            entry["player"] = ""
            entry["device"] = ""
        return entry

    def clear(self) -> None:
        """Clear all active and finished playback history."""
        self.sessions.clear()
        self.recently_finished = None


playback_mgr = PlaybackManager()
