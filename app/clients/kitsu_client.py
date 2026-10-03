"""Client for interacting with Kitsu JSON:API v1.

Completes Omniscrobble's Anime Big Three (AniList, MyAnimeList, and Kitsu)
by providing automated anime discovery, episode progress updates, and ratings.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("omniscrobble.kitsu_client")


class KitsuClient:
    """Asynchronous client for Kitsu JSON:API v1."""

    BASE_URL = "https://kitsu.app/api/edge"

    def __init__(
        self,
        config: type[Config] = Config,
        api_token: Optional[str] = None,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.config = config
        self.api_token = api_token or getattr(config, "KITSU_API_TOKEN", getattr(config, "KITSU_API_KEY", "")) or ""
        self.user_id = user_id or getattr(config, "KITSU_USER_ID", "") or ""
        self.user_name = user_name or getattr(config, "KITSU_USER_NAME", "") or ""
        if transport is not None and client is None:
            client = httpx.AsyncClient(transport=transport, timeout=10.0)
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(timeout=10.0)
        return self._internal_client

    def is_configured(self) -> bool:
        return bool(self.api_token)

    def is_authenticated(self) -> bool:
        return bool(self.api_token)

    def is_enabled(self) -> bool:
        return self.is_configured()

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def check_connection(self) -> dict[str, Any]:
        """Verify API token and resolve Kitsu user identity."""
        if not self.is_configured():
            return {
                "name": "Kitsu",
                "configured": False,
                "authenticated": False,
                "enabled": True,
                "status": "unconfigured",
                "message": "Kitsu API token is not configured.",
            }

        client = self.get_client()
        url = f"{self.BASE_URL}/users?filter[self]=true"
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    user_data = data[0]
                    self.user_id = str(user_data.get("id", ""))
                    attrs = user_data.get("attributes", {})
                    self.user_name = attrs.get("name") or "user"
                    return {
                        "name": "Kitsu",
                        "configured": True,
                        "authenticated": True,
                        "enabled": True,
                        "status": "connected",
                        "username": self.user_name,
                        "user": self.user_name,
                        "user_id": self.user_id,
                        "message": f"Connected as @{self.user_name}",
                    }
            return {
                "name": "Kitsu",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": f"Kitsu returned HTTP {resp.status_code}",
            }
        except Exception as e:
            logger.error("Kitsu check_connection failed: %s", e)
            return {
                "name": "Kitsu",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": str(e),
            }

    async def search_anime(self, title: str, year: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Search Kitsu for anime by title and optional year."""
        client = self.get_client()
        url = f"{self.BASE_URL}/anime"
        params = {"filter[text]": title, "page[limit]": 5}

        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            if resp.status_code != 200:
                return None
            results = resp.json().get("data", [])
            if not results:
                return None

            if year:
                for item in results:
                    start_date = item.get("attributes", {}).get("startDate") or ""
                    if str(year) in start_date:
                        return self._format_anime_result(item)

            return self._format_anime_result(results[0])
        except Exception as e:
            logger.error("Kitsu search_anime failed for '%s': %s", title, e)
            return None

    def _format_anime_result(self, item: dict[str, Any]) -> dict[str, Any]:
        attrs = item.get("attributes", {})
        titles = attrs.get("titles", {})
        canonical_title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or ""
        ep_count = attrs.get("episodeCount")
        return {
            "id": str(item["id"]),
            "kitsu_id": int(item["id"]),
            "title": canonical_title,
            "episodes": ep_count,
            "episode_count": ep_count,
            "subtype": attrs.get("subtype"),
            "status": attrs.get("status"),
        }

    async def _find_library_entry(self, anime_id: int | str) -> Optional[str]:
        """Find existing library entry ID for the user and anime."""
        if not self.user_id:
            await self.check_connection()
        if not self.user_id:
            return None

        client = self.get_client()
        url = f"{self.BASE_URL}/library-entries"
        params = {
            "filter[userId]": str(self.user_id),
            "filter[animeId]": str(anime_id),
        }
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    return str(data[0]["id"])
        except Exception as e:
            logger.error("Error finding Kitsu library entry: %s", e)
        return None

    async def update_progress(
        self,
        anime_id: int,
        episode_number: int,
        total_episodes: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update anime episode progress and status on Kitsu."""
        if not self.is_configured():
            return {"status": "error", "message": "Kitsu not configured"}

        client = self.get_client()
        entry_id = await self._find_library_entry(anime_id)
        is_completed = total_episodes is not None and episode_number >= total_episodes
        status_val = "completed" if is_completed else "current"

        try:
            if entry_id:
                # Update existing entry
                url = f"{self.BASE_URL}/library-entries/{entry_id}"
                payload = {
                    "data": {
                        "id": entry_id,
                        "type": "libraryEntries",
                        "attributes": {
                            "progress": episode_number,
                            "status": status_val,
                        },
                    }
                }
                resp = await client.patch(url, headers=self._get_headers(), json=payload)
            else:
                # Create new entry
                if not self.user_id:
                    await self.check_connection()
                url = f"{self.BASE_URL}/library-entries"
                payload = {
                    "data": {
                        "type": "libraryEntries",
                        "attributes": {
                            "progress": episode_number,
                            "status": status_val,
                        },
                        "relationships": {
                            "anime": {"data": {"type": "anime", "id": str(anime_id)}},
                            "user": {"data": {"type": "users", "id": str(self.user_id)}},
                        },
                    }
                }
                resp = await client.post(url, headers=self._get_headers(), json=payload)

            if resp.status_code in (200, 201):
                return {
                    "status": "success",
                    "kitsu_id": anime_id,
                    "progress": episode_number,
                    "completed": is_completed,
                }
            return {"status": "error", "error": f"Kitsu HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error("Kitsu update_progress failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def update_rating(self, anime_id: int, rating: float | int) -> dict[str, Any]:
        """Update rating on Kitsu.
        
        Kitsu uses a ratingTwenty scale (2 to 20), corresponding to 1-10 stars.
        """
        if not self.is_configured():
            return {"status": "error", "message": "Kitsu not configured"}

        rating_twenty = max(2, min(20, int(round(float(rating) * 2))))
        entry_id = await self._find_library_entry(anime_id)
        client = self.get_client()

        try:
            if entry_id:
                url = f"{self.BASE_URL}/library-entries/{entry_id}"
                payload = {
                    "data": {
                        "id": entry_id,
                        "type": "libraryEntries",
                        "attributes": {"ratingTwenty": rating_twenty},
                    }
                }
                resp = await client.patch(url, headers=self._get_headers(), json=payload)
            else:
                if not self.user_id:
                    await self.check_connection()
                url = f"{self.BASE_URL}/library-entries"
                payload = {
                    "data": {
                        "type": "libraryEntries",
                        "attributes": {
                            "ratingTwenty": rating_twenty,
                            "status": "current",
                        },
                        "relationships": {
                            "anime": {"data": {"type": "anime", "id": str(anime_id)}},
                            "user": {"data": {"type": "users", "id": str(self.user_id)}},
                        },
                    }
                }
                resp = await client.post(url, headers=self._get_headers(), json=payload)

            if resp.status_code in (200, 201):
                return {"status": "success", "kitsu_id": anime_id, "ratingTwenty": rating_twenty}
            return {"status": "error", "error": f"Kitsu HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error("Kitsu update_rating failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def delete_progress(self, anime_id: int | str) -> dict[str, Any]:
        """Delete anime library entry from Kitsu."""
        entry_id = await self._find_library_entry(anime_id)
        if not entry_id:
            return {"status": "ignored", "reason": "No entry found on Kitsu"}

        client = self.get_client()
        url = f"{self.BASE_URL}/library-entries/{entry_id}"
        try:
            resp = await client.delete(url, headers=self._get_headers())
            if resp.status_code in (200, 204):
                return {"status": "deleted", "kitsu_id": anime_id, "success": True}
            return {"status": "error", "error": f"Kitsu HTTP {resp.status_code}"}
        except Exception as e:
            logger.error("Kitsu delete_progress failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None
