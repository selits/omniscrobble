import time
from typing import Any, Optional

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

    def _get_key(self, media: ParsedMedia) -> str:
        player_identifier = media.player or media.device or "default"
        return f"{media.username}:{player_identifier}"

    def update_playback(self, media: ParsedMedia, state: str = "playing") -> dict[str, Any]:
        """Record or update active streaming session."""
        key = self._get_key(media)
        title_str = format_media_title(media)
        trakt_url = get_trakt_url(media)

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
            "updated_at": time.time(),
            "trakt_url": trakt_url,
            "ids": media.ids,
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
            if not is_admin:
                item["username"] = mask_username_simple(item["username"])
            active.append(item)

        active.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return active

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
        return entry

    def clear(self) -> None:
        """Clear all active and finished playback history."""
        self.sessions.clear()
        self.recently_finished = None


playback_mgr = PlaybackManager()
