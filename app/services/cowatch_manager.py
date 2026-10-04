import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

try:
    from app.config import Config
    from app.plex_parser import ParsedMedia
    from app.services.atomic_writer import atomic_write_json
except ImportError:
    from config import Config
    from plex_parser import ParsedMedia
    from atomic_writer import atomic_write_json

logger = logging.getLogger("cowatch_manager")

_YEAR_PARENS_RE = re.compile(r"\s*[\(\[]\d{4}[\)\]]")
_PUNCTUATION_RE = re.compile(r"[^\w\s]")


class CowatchManager:
    """Manages Watch Together / Co-Watching rules and shared TV shows."""

    def __init__(self, config: type[Config] = Config):
        self.config = config
        self.data_file: Path = getattr(config, "CO_WATCH_DATA_FILE", getattr(config, "BASE_DIR", Path(".")) / "data" / "cowatch_shows.json")
        self.devices_file: Path = getattr(
            config,
            "CO_WATCH_DEVICES_DATA_FILE",
            getattr(config, "BASE_DIR", Path(".")) / "data" / "cowatch_devices.json",
        )
        self._shows: list[str] = []
        self._devices: list[str] = []
        self._load_shows()
        self._load_devices()

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
            atomic_write_json(self.data_file, self._shows)
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

    def _load_devices(self) -> None:
        """Load configured devices from persistent JSON file and merge with initial config."""
        loaded: list[str] = []
        if self.devices_file.exists():
            try:
                with open(self.devices_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        loaded = [d.strip() for d in data if d and d.strip()]
            except Exception as e:
                logger.error(f"Error loading co-watch devices from {self.devices_file}: {e}")

        # Merge with config.CO_WATCH_PLAYERS so .env additions are always honored
        env_devices = [p.strip() for p in getattr(self.config, "CO_WATCH_PLAYERS", []) if p and p.strip()]
        combined = list(loaded)
        for d in env_devices:
            if not any(x.lower() == d.lower() for x in combined):
                combined.append(d)

        self._devices = combined
        if env_devices and len(combined) != len(loaded):
            self._save_devices()

    def _save_devices(self) -> None:
        """Persist current devices to disk."""
        try:
            atomic_write_json(self.devices_file, self._devices)
        except Exception as e:
            logger.error(f"Error saving co-watch devices to {self.devices_file}: {e}")

    def get_devices(self) -> list[str]:
        """Return the current list of allowed co-watching devices/players."""
        env_devices = [p.strip() for p in getattr(self.config, "CO_WATCH_PLAYERS", []) if p and p.strip()]
        combined = list(self._devices)
        for d in env_devices:
            if not any(x.lower() == d.lower() for x in combined):
                combined.append(d)
        return combined

    def add_device(self, device_name: str) -> list[str]:
        """Add a player/device to the co-watching whitelist."""
        clean = device_name.strip()
        current = self.get_devices()
        if clean and not any(d.lower() == clean.lower() for d in current):
            self._devices.append(clean)
            self._save_devices()
            logger.info(f"Added '{clean}' to co-watch devices list.")
        return self.get_devices()

    def remove_device(self, device_name: str) -> list[str]:
        """Remove a player/device from the co-watching whitelist."""
        clean = device_name.strip().lower()
        self._devices = [d for d in self._devices if d.strip().lower() != clean]
        if hasattr(self.config, "CO_WATCH_PLAYERS") and isinstance(self.config.CO_WATCH_PLAYERS, list):
            self.config.CO_WATCH_PLAYERS = [
                p for p in self.config.CO_WATCH_PLAYERS if p.strip().lower() != clean
            ]
        self._save_devices()
        logger.info(f"Removed '{device_name}' from co-watch devices list.")
        return self.get_devices()

    def set_cowatch_movies(self, enabled: bool) -> bool:
        """Toggle movie co-watching dynamically."""
        self.config.CO_WATCH_MOVIES = enabled
        logger.info(f"Co-watching movies setting updated to: {enabled}")
        return self.config.CO_WATCH_MOVIES

    def _normalize(self, text: str) -> str:
        """Strip remake years e.g. '(2024)', special punctuation, and lowercase."""
        cleaned = _YEAR_PARENS_RE.sub("", text)
        cleaned = _PUNCTUATION_RE.sub("", cleaned)
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
        allowed_players = [p.lower() for p in self.get_devices()]
        if allowed_players:
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
            "co_watch_players": self.get_devices(),
        }


cowatch_mgr = CowatchManager()
