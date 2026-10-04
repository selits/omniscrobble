"""Plex Media Server REST API client for two-way library reconciliation and scrobbling."""

import logging
from typing import Any, Optional
import httpx

from app.config import Config
from app.plex_parser import parse_plex_ids

logger = logging.getLogger("omniscrobble.plex_api")


class PlexApiClient:
    """Asynchronous client for interacting with the local Plex Media Server REST API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = (base_url or Config.PLEX_URL).rstrip("/")
        self.token = token or Config.PLEX_TOKEN
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None

    def is_configured(self) -> bool:
        """Returns True if the Plex server URL and token are configured."""
        return bool(self.base_url and self.token)

    def get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(timeout=15.0)
        return self._internal_client

    async def close(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Plex-Client-Identifier": "omniscrobble",
            "X-Plex-Product": "Omniscrobble",
            "X-Plex-Version": "1.5.0",
        }
        if self.token:
            headers["X-Plex-Token"] = self.token
        return headers

    async def check_connection(self) -> dict[str, Any]:
        """Verify connectivity with the Plex server and return basic server details."""
        if not self.is_configured():
            return {"status": "unconfigured", "message": "Plex URL or token not configured"}

        url = f"{self.base_url}/identity"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json().get("MediaContainer", {})
                return {
                    "status": "connected",
                    "machine_identifier": data.get("machineIdentifier", ""),
                    "version": data.get("version", ""),
                }
            return {
                "status": "error",
                "status_code": resp.status_code,
                "message": f"Plex returned status {resp.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("Failed to connect to Plex at %s: %s", self.base_url, exc)
            return {"status": "unreachable", "message": str(exc)}

    async def get_library_sections(self) -> list[dict[str, Any]]:
        """Return all movie and show library sections on the Plex server."""
        if not self.is_configured():
            return []

        url = f"{self.base_url}/library/sections"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error("Failed to fetch library sections: HTTP %s", resp.status_code)
                return []

            data = resp.json().get("MediaContainer", {})
            directories = data.get("Directory", [])
            sections = []
            for sec in directories:
                sec_type = sec.get("type")
                if sec_type in ("movie", "show"):
                    sections.append({
                        "key": str(sec.get("key")),
                        "title": sec.get("title", ""),
                        "type": sec_type,
                        "uuid": sec.get("uuid", ""),
                    })
            return sections
        except httpx.RequestError as exc:
            logger.error("Network error fetching library sections: %s", exc)
            return []

    async def get_movies(self, section_key: str) -> list[dict[str, Any]]:
        """Fetch all movies in a given library section."""
        if not self.is_configured():
            return []

        url = f"{self.base_url}/library/sections/{section_key}/all?type=1"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error("Failed to fetch movies from section %s: HTTP %s", section_key, resp.status_code)
                return []

            container = resp.json().get("MediaContainer", {})
            metadata_list = container.get("Metadata", [])
            movies = []
            for item in metadata_list:
                rating_key = str(item.get("ratingKey", ""))
                title = item.get("title", "")
                year = item.get("year")
                guid_list = item.get("Guid", [])
                legacy_guid = item.get("guid", "")
                ids = parse_plex_ids(guid_list, legacy_guid)

                view_count = int(item.get("viewCount", 0))
                last_viewed_at = item.get("lastViewedAt")
                user_rating = item.get("userRating")

                movies.append({
                    "rating_key": rating_key,
                    "title": title,
                    "year": year,
                    "ids": ids,
                    "guid": legacy_guid,
                    "view_count": view_count,
                    "is_watched": view_count > 0,
                    "last_viewed_at": last_viewed_at,
                    "user_rating": user_rating,
                })
            return movies
        except httpx.RequestError as exc:
            logger.error("Network error fetching movies from section %s: %s", section_key, exc)
            return []

    async def get_episodes(self, section_key: str) -> list[dict[str, Any]]:
        """Fetch all episodes across all TV shows in a given show library section."""
        if not self.is_configured():
            return []

        url = f"{self.base_url}/library/sections/{section_key}/all?type=4"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error("Failed to fetch episodes from section %s: HTTP %s", section_key, resp.status_code)
                return []

            container = resp.json().get("MediaContainer", {})
            metadata_list = container.get("Metadata", [])
            episodes = []
            for item in metadata_list:
                rating_key = str(item.get("ratingKey", ""))
                grandparent_title = item.get("grandparentTitle", "")
                grandparent_rating_key = str(item.get("grandparentRatingKey", ""))
                season_index = item.get("parentIndex")
                episode_index = item.get("index")
                episode_title = item.get("title", "")
                year = item.get("year")
                guid_list = item.get("Guid", [])
                legacy_guid = item.get("guid", "")
                ids = parse_plex_ids(guid_list, legacy_guid)

                view_count = int(item.get("viewCount", 0))
                last_viewed_at = item.get("lastViewedAt")
                user_rating = item.get("userRating")

                episodes.append({
                    "rating_key": rating_key,
                    "series_title": grandparent_title,
                    "grandparent_rating_key": grandparent_rating_key,
                    "season": season_index,
                    "episode": episode_index,
                    "title": episode_title,
                    "year": year,
                    "ids": ids,
                    "guid": legacy_guid,
                    "view_count": view_count,
                    "is_watched": view_count > 0,
                    "last_viewed_at": last_viewed_at,
                    "user_rating": user_rating,
                })
            return episodes
        except httpx.RequestError as exc:
            logger.error("Network error fetching episodes from section %s: %s", section_key, exc)
            return []

    async def mark_as_watched(self, rating_key: str) -> bool:
        """Mark an item as watched (scrobble) directly on the Plex server."""
        if not self.is_configured() or not rating_key:
            return False

        url = f"{self.base_url}/:/scrobble"
        params = {"key": rating_key, "identifier": "com.plexapp.plugins.library"}
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            return resp.status_code == 200
        except httpx.RequestError as exc:
            logger.error("Failed to mark rating_key %s as watched: %s", rating_key, exc)
            return False

    async def mark_as_unwatched(self, rating_key: str) -> bool:
        """Mark an item as unwatched (unscrobble) directly on the Plex server."""
        if not self.is_configured() or not rating_key:
            return False

        url = f"{self.base_url}/:/unscrobble"
        params = {"key": rating_key, "identifier": "com.plexapp.plugins.library"}
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            return resp.status_code == 200
        except httpx.RequestError as exc:
            logger.error("Failed to mark rating_key %s as unwatched: %s", rating_key, exc)
            return False

    async def set_user_rating(self, rating_key: str, rating_10: float) -> bool:
        """Set user rating (0-10 scale) on the Plex server."""
        if not self.is_configured() or not rating_key:
            return False

        url = f"{self.base_url}/:/rate"
        params = {
            "key": rating_key,
            "identifier": "com.plexapp.plugins.library",
            "rating": str(round(float(rating_10), 1)),
        }
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            return resp.status_code == 200
        except httpx.RequestError as exc:
            logger.error("Failed to set rating for rating_key %s: %s", rating_key, exc)
            return False

    async def find_item(self, media: Any) -> Optional[dict[str, Any]]:
        """Find an item on Plex matching media title, year, season, episode, and/or IDs."""
        if not self.is_configured():
            return None

        client = self.get_client()
        media_type = getattr(media, "media_type", "movie")
        is_episode = media_type == "episode"
        search_title = getattr(media, "title", "")
        if is_episode:
            search_title = getattr(media, "show_title", None) or getattr(media, "grandparent_title", None) or search_title

        url = f"{self.base_url}/search"
        params = {"query": search_title}
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            if resp.status_code != 200:
                return None

            container = resp.json().get("MediaContainer", {})
            metadata_list = container.get("Metadata", [])
            target_ids = getattr(media, "ids", {}) or {}

            for item in metadata_list:
                guid_list = item.get("Guid", [])
                legacy_guid = item.get("guid", "")
                item_ids = parse_plex_ids(guid_list, legacy_guid)

                # Match by provider IDs
                for id_type, id_val in target_ids.items():
                    if id_val and item_ids.get(id_type) == str(id_val):
                        return {
                            "rating_key": str(item.get("ratingKey")),
                            "title": item.get("title"),
                            "ids": item_ids,
                            "type": item.get("type"),
                        }

                # Match by season/episode or title/year
                if is_episode:
                    p_index = item.get("parentIndex")
                    ep_index = item.get("index")
                    m_season = getattr(media, "season", None)
                    m_episode = getattr(media, "episode", None)
                    if (
                        m_season is not None
                        and m_episode is not None
                        and p_index == m_season
                        and ep_index == m_episode
                    ):
                        return {
                            "rating_key": str(item.get("ratingKey")),
                            "title": item.get("title"),
                            "ids": item_ids,
                            "type": item.get("type"),
                        }
                else:
                    item_title = item.get("title", "").strip().lower()
                    m_title = str(getattr(media, "title", "")).strip().lower()
                    if item_title == m_title:
                        item_year = item.get("year")
                        m_year = getattr(media, "year", None)
                        if not m_year or not item_year or int(item_year) == int(m_year):
                            return {
                                "rating_key": str(item.get("ratingKey")),
                                "title": item.get("title"),
                                "ids": item_ids,
                                "type": item.get("type"),
                            }
        except httpx.RequestError as exc:
            logger.error("Failed to search Plex for item %s: %s", search_title, exc)
        return None

