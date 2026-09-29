"""Intelligent anime detection, title normalization, and ID resolution service.

Determines whether media is anime via metadata heuristics, provider IDs, or
AniList GraphQL searches, resolving unified AniList and MyAnimeList IDs with
persistent caching.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.config import Config
from app.plex_parser import ParsedMedia

logger = logging.getLogger("omniscrobble.anime_resolver")


class AnimeResolver:
    """Detects anime and resolves AniList and MyAnimeList identifiers."""

    def __init__(
        self,
        config: type[Config] = Config,
        anilist_client: Optional[AniListClient] = None,
        mal_client: Optional[MyAnimeListClient] = None,
        cache_file: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.anilist = anilist_client or AniListClient(config=config)
        self.mal = mal_client or MyAnimeListClient(config=config)
        self.cache_file = cache_file or config.ANIME_CACHE_FILE
        self._cache: dict[str, Any] = {}
        self.load_cache()

    def load_cache(self) -> None:
        """Load resolved anime metadata cache from disk."""
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                self._cache = json.load(f)
            logger.debug("Loaded %d items from anime cache.", len(self._cache))
        except Exception as e:
            logger.error("Failed to load anime cache from %s: %s", self.cache_file, e)

    def save_cache(self) -> None:
        """Persist anime metadata cache to disk."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error("Failed to save anime cache: %s", e)

    @staticmethod
    def clean_title(title: str) -> str:
        """Normalize title by removing release tags, brackets, and extra whitespace."""
        if not title:
            return ""
        # Remove release tags like [1080p], [Dual Audio], (2024), etc.
        cleaned = re.sub(r"\[.*?\]", "", title)
        cleaned = re.sub(r"\(.*?\)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def is_explicit_anime(self, media: ParsedMedia) -> bool:
        """Check if media is explicitly marked as anime through IDs, libraries, or GUIDs."""
        # 1. Direct IDs
        if any(k in media.ids for k in ("anilist", "mal", "anidb", "kitsu")):
            return True

        # 2. Library Section
        lib = (media.library_section_title or "").lower()
        if any(term in lib for term in ("anime", "donghua", "animation")):
            return True

        # 3. Plex Agent / GUID
        raw_meta = media.raw_payload.get("Metadata", {}) if isinstance(media.raw_payload, dict) else {}
        guid = str(raw_meta.get("guid") or "").lower()
        if any(agent in guid for agent in ("hama", "anidb", "anilist", "myanimelist")):
            return True

        return False

    async def resolve(self, media: ParsedMedia) -> Optional[dict[str, Any]]:
        """Resolve anime metadata, returning AniList & MAL identifiers if matched.
        
        Returns None if media is determined not to be anime.
        """
        title = media.show_title or media.title
        clean_t = self.clean_title(title)
        if not clean_t:
            return None

        year = media.show_year or media.year
        cache_key = f"{clean_t.lower()}_{year or ''}".strip("_")

        # 1. Check in-memory / disk cache
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if not entry.get("is_anime"):
                return None
            return {
                "is_anime": True,
                "title": entry.get("title", clean_t),
                "anilist_id": entry.get("anilist_id"),
                "mal_id": entry.get("mal_id"),
                "format": entry.get("format"),
                "episodes": entry.get("episodes"),
                "episode_number": media.episode or 1,
                "source": "cache",
            }

        # 2. Check direct IDs present on media object
        anilist_id = media.ids.get("anilist")
        mal_id = media.ids.get("mal")

        if anilist_id:
            # We already have the AniList ID directly
            media_info = await self.anilist.get_media_by_id(int(anilist_id))
            resolved = {
                "is_anime": True,
                "title": (media_info.get("title_preferred") if media_info else clean_t),
                "anilist_id": int(anilist_id),
                "mal_id": (media_info.get("idMal") if media_info else mal_id),
                "format": media_info.get("format") if media_info else None,
                "episodes": media_info.get("episodes") if media_info else None,
                "episode_number": media.episode or 1,
                "source": "direct_id",
            }
            self._cache[cache_key] = {
                "is_anime": True,
                "anilist_id": resolved["anilist_id"],
                "mal_id": resolved["mal_id"],
                "title": resolved["title"],
                "format": resolved["format"],
                "episodes": resolved["episodes"],
            }
            self.save_cache()
            return resolved

        # 3. Query AniList Search if explicit anime or auto-detection enabled
        if self.is_explicit_anime(media) or self.config.ANIME_AUTO_DETECT:
            search_res = await self.anilist.search_anime(clean_t, year=year)
            if search_res:
                resolved = {
                    "is_anime": True,
                    "title": search_res.get("title_preferred") or clean_t,
                    "anilist_id": search_res.get("id"),
                    "mal_id": search_res.get("idMal") or mal_id,
                    "format": search_res.get("format"),
                    "episodes": search_res.get("episodes"),
                    "episode_number": media.episode or 1,
                    "source": "anilist_search",
                }
                self._cache[cache_key] = {
                    "is_anime": True,
                    "anilist_id": resolved["anilist_id"],
                    "mal_id": resolved["mal_id"],
                    "title": resolved["title"],
                    "format": resolved["format"],
                    "episodes": resolved["episodes"],
                }
                self.save_cache()
                logger.info(
                    "Anime resolved: '%s' -> AniList ID: %s, MAL ID: %s",
                    clean_t,
                    resolved["anilist_id"],
                    resolved["mal_id"],
                )
                return resolved

        # 4. Not an anime or no match found -> store negative result to avoid redundant searches
        self._cache[cache_key] = {"is_anime": False}
        self.save_cache()
        return None
