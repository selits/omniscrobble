import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

try:
    from app.config import Config
    from app.plex_parser import ParsedMedia
except ImportError:
    from config import Config
    from plex_parser import ParsedMedia

logger = logging.getLogger("cowatch_manager")


class CowatchManager:
    """Manages Watch Together / Co-Watching rules and shared TV shows."""

    def __init__(self, config: type[Config] = Config):
        self.config = config
        self.data_file: Path = config.CO_WATCH_DATA_FILE
        self._shows: list[str] = []
        self._load_shows()

    def _load_shows(self) -> None:
        """Load configured shows from persistent JSON file and merge with initial config."""
        loaded: list[str] = []
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        loaded = [s.strip() for s in data if s and s.strip()]
            except Exception as e:
                logger.error(f"Error loading co-watch shows from {self.data_file}: {e}")

        # Merge with config.CO_WATCH_SHOWS so .env additions are always honored
        env_shows = [s.strip() for s in self.config.CO_WATCH_SHOWS if s and s.strip()]
        combined = list(loaded)
        for s in env_shows:
            if not any(x.lower() == s.lower() for x in combined):
                combined.append(s)

        self._shows = combined
        if env_shows and len(combined) != len(loaded):
            self._save_shows()

    def _save_shows(self) -> None:
        """Persist current shows to disk."""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._shows, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving co-watch shows to {self.data_file}: {e}")

    def get_shows(self) -> list[str]:
        """Return the current list of shared co-watched shows."""
        return list(self._shows)

    def add_show(self, show_name: str) -> list[str]:
        """Add a show to the co-watching whitelist."""
        clean = show_name.strip()
        if clean and not any(s.lower() == clean.lower() for s in self._shows):
            self._shows.append(clean)
            self._save_shows()
            logger.info(f"Added '{clean}' to co-watch shows list.")
        return list(self._shows)

    def remove_show(self, show_name: str) -> list[str]:
        """Remove a show from the co-watching whitelist."""
        clean = show_name.strip().lower()
        self._shows = [s for s in self._shows if s.strip().lower() != clean]
        self._save_shows()
        logger.info(f"Removed '{show_name}' from co-watch shows list.")
        return list(self._shows)

    def set_cowatch_movies(self, enabled: bool) -> bool:
        """Toggle movie co-watching dynamically."""
        self.config.CO_WATCH_MOVIES = enabled
        logger.info(f"Co-watching movies setting updated to: {enabled}")
        return self.config.CO_WATCH_MOVIES

    def _normalize(self, text: str) -> str:
        """Strip remake years e.g. '(2024)', special punctuation, and lowercase."""
        cleaned = re.sub(r"\s*[\(\[]\d{4}[\)\]]", "", text)
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        return cleaned.strip().lower()

    def is_cowatch_show(self, show_title: Optional[str]) -> bool:
        """Check if a given show title matches any co-watched show."""
        if not show_title:
            return False
        normalized_input = self._normalize(show_title)
        for show in self._shows:
            if self._normalize(show) == normalized_input:
                return True
        return False

    def check_cowatch_eligibility(self, media: ParsedMedia) -> tuple[bool, str]:
        """Check if an event should co-watch, returning (eligible, reason)."""
        target_user = self.config.CO_WATCH_USER
        if not target_user:
            return False, "Target user not configured"

        # If the event came directly from the co-watch user, don't duplicate back to them
        if media.username.strip().lower() == target_user.strip().lower():
            return False, "Self playback by partner"

        # Player/Device whitelist check (if configured)
        if self.config.CO_WATCH_PLAYERS:
            allowed_players = [p.lower() for p in self.config.CO_WATCH_PLAYERS]
            media_player = (media.player or "").lower()
            media_device = (media.device or "").lower()
            if not any(p in (media_player, media_device) for p in allowed_players):
                player_name = media.player or media.device or "Unknown"
                return False, f"Device '{player_name}' not in CO_WATCH_PLAYERS"

        # Movie check
        if media.media_type == "movie":
            if self.config.CO_WATCH_MOVIES:
                return True, "Movie co-watching enabled"
            return False, "Movie co-watching disabled"

        # Episode check
        if media.media_type == "episode":
            show_name = media.show_title or media.title
            if self.is_cowatch_show(show_name):
                return True, f"Shared show: {show_name}"
            return False, f"'{show_name}' not in shared whitelist"

        return False, f"Unsupported media type: {media.media_type}"

    def should_cowatch(self, media: ParsedMedia) -> bool:
        """Determine if an incoming event should trigger dual-scrobbling."""
        eligible, _ = self.check_cowatch_eligibility(media)
        return eligible

    def get_status(self) -> dict[str, Any]:
        """Return the current co-watching status summary."""
        return {
            "co_watch_user": self.config.CO_WATCH_USER,
            "enabled": bool(self.config.CO_WATCH_USER),
            "shows": self.get_shows(),
            "co_watch_movies": self.config.CO_WATCH_MOVIES,
            "co_watch_players": self.config.CO_WATCH_PLAYERS,
        }


cowatch_mgr = CowatchManager()
