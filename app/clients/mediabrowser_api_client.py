"""Base MediaBrowser (Jellyfin & Emby) REST API client for two-way library reconciliation."""

import logging
from typing import Any, Optional
import httpx

from app.jellyfin_parser import _extract_provider_ids

logger = logging.getLogger("omniscrobble.mediabrowser_api")


class BaseMediaBrowserClient:
    """Asynchronous client for interacting with Jellyfin and Emby REST APIs."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        server_type: str = "jellyfin",
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.user_id = user_id or ""
        self.server_type = server_type.lower()
        self.product_name = "Jellyfin" if self.server_type == "jellyfin" else "Emby"
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None

    def is_configured(self) -> bool:
        """Returns True if the server URL and token/API key are configured."""
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
            f"X-{self.product_name}-Client-Identifier": "omniscrobble",
            f"X-{self.product_name}-Product": "Omniscrobble",
            f"X-{self.product_name}-Version": "1.5.0",
        }
        if self.token:
            headers["X-Emby-Token"] = self.token
            headers["Authorization"] = (
                f'MediaBrowser Client="Omniscrobble", Device="Omniscrobble", DeviceId="omniscrobble", Version="1.5.0", Token="{self.token}"'
            )
        return headers

    async def check_connection(self) -> dict[str, Any]:
        """Verify connectivity with the media server and return server details."""
        if not self.is_configured():
            return {"status": "unconfigured", "message": f"{self.product_name} URL or token not configured"}

        url = f"{self.base_url}/System/Info"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "connected",
                    "server_name": data.get("ServerName", self.product_name),
                    "version": data.get("Version", ""),
                    "id": data.get("Id", ""),
                    "operating_system": data.get("OperatingSystem", ""),
                }
            elif resp.status_code in (401, 403):
                return {"status": "unauthorized", "message": f"Invalid {self.product_name} API key or token"}
            return {
                "status": "error",
                "status_code": resp.status_code,
                "message": f"{self.product_name} returned status {resp.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("Failed to connect to %s at %s: %s", self.product_name, self.base_url, exc)
            return {"status": "unreachable", "message": str(exc)}

    async def get_default_user_id(self) -> Optional[str]:
        """Fetch or auto-detect primary user ID if not explicitly specified."""
        if self.user_id:
            return self.user_id
        if not self.is_configured():
            return None

        url = f"{self.base_url}/Users"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                users = resp.json()
                if isinstance(users, list) and users:
                    # Prefer admin user or fallback to first user
                    admin = next(
                        (u for u in users if isinstance(u, dict) and u.get("Policy", {}).get("IsAdministrator")),
                        users[0],
                    )
                    uid = admin.get("Id") if isinstance(admin, dict) else None
                    if uid:
                        self.user_id = str(uid)
                        return self.user_id
        except httpx.RequestError as exc:
            logger.error("Failed to fetch users from %s: %s", self.product_name, exc)
        return None

    async def get_library_sections(self) -> list[dict[str, Any]]:
        """Return all movie and show library sections on the server."""
        if not self.is_configured():
            return []

        user_id = await self.get_default_user_id()
        client = self.get_client()
        try:
            if user_id:
                url = f"{self.base_url}/Users/{user_id}/Views"
            else:
                url = f"{self.base_url}/Library/MediaFolders"

            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code != 200:
                logger.error("Failed to fetch libraries from %s: HTTP %s", self.product_name, resp.status_code)
                return []

            data = resp.json()
            items = data.get("Items", []) if isinstance(data, dict) else []
            sections = []
            for item in items:
                c_type = str(item.get("CollectionType") or "").lower()
                sec_type = None
                if c_type in ("movies", "movie"):
                    sec_type = "movie"
                elif c_type in ("tvshows", "shows", "series"):
                    sec_type = "show"

                if sec_type:
                    sections.append({
                        "key": str(item.get("Id")),
                        "title": item.get("Name", ""),
                        "type": sec_type,
                    })
            return sections
        except httpx.RequestError as exc:
            logger.error("Network error fetching %s library sections: %s", self.product_name, exc)
            return []

    async def get_movies(self, section_key: str) -> list[dict[str, Any]]:
        """Fetch all movies in a given library section."""
        if not self.is_configured():
            return []

        user_id = await self.get_default_user_id()
        client = self.get_client()
        endpoint = f"/Users/{user_id}/Items" if user_id else "/Items"
        url = f"{self.base_url}{endpoint}"
        params = {
            "ParentId": section_key,
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "ProviderIds,UserData,ProductionYear",
        }
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            if resp.status_code != 200:
                logger.error("Failed to fetch movies from %s section %s: HTTP %s", self.product_name, section_key, resp.status_code)
                return []

            container = resp.json().get("Items", [])
            movies = []
            for item in container:
                rating_key = str(item.get("Id", ""))
                title = item.get("Name", "")
                year = item.get("ProductionYear")
                provider_ids = item.get("ProviderIds") or {}
                ids = _extract_provider_ids(provider_ids, item)

                user_data = item.get("UserData") or {}
                is_watched = bool(user_data.get("Played", False))
                view_count = int(user_data.get("PlayCount", 1 if is_watched else 0))
                last_viewed_at = user_data.get("LastPlayedDate")
                user_rating = user_data.get("Rating") or user_data.get("UserRating")

                movies.append({
                    "rating_key": rating_key,
                    "title": title,
                    "year": year,
                    "ids": ids,
                    "view_count": view_count,
                    "is_watched": is_watched,
                    "last_viewed_at": last_viewed_at,
                    "user_rating": user_rating,
                    "rating": user_rating,
                })
            return movies
        except httpx.RequestError as exc:
            logger.error("Network error fetching movies from %s: %s", self.product_name, exc)
            return []

    async def get_episodes(self, section_key: str) -> list[dict[str, Any]]:
        """Fetch all episodes in a given show library section."""
        if not self.is_configured():
            return []

        user_id = await self.get_default_user_id()
        client = self.get_client()
        endpoint = f"/Users/{user_id}/Items" if user_id else "/Items"
        url = f"{self.base_url}{endpoint}"
        params = {
            "ParentId": section_key,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "ProviderIds,UserData,SeriesName,IndexNumber,ParentIndexNumber,ProductionYear",
        }
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            if resp.status_code != 200:
                logger.error("Failed to fetch episodes from %s section %s: HTTP %s", self.product_name, section_key, resp.status_code)
                return []

            container = resp.json().get("Items", [])
            episodes = []
            for item in container:
                rating_key = str(item.get("Id", ""))
                series_title = item.get("SeriesName", "")
                season_index = item.get("ParentIndexNumber")
                episode_index = item.get("IndexNumber")
                episode_title = item.get("Name", "")
                year = item.get("ProductionYear")
                provider_ids = item.get("ProviderIds") or {}
                ids = _extract_provider_ids(provider_ids, item)

                user_data = item.get("UserData") or {}
                is_watched = bool(user_data.get("Played", False))
                view_count = int(user_data.get("PlayCount", 1 if is_watched else 0))
                last_viewed_at = user_data.get("LastPlayedDate")
                user_rating = user_data.get("Rating") or user_data.get("UserRating")

                episodes.append({
                    "rating_key": rating_key,
                    "series_title": series_title,
                    "season": season_index,
                    "episode": episode_index,
                    "title": episode_title,
                    "year": year,
                    "ids": ids,
                    "view_count": view_count,
                    "is_watched": is_watched,
                    "last_viewed_at": last_viewed_at,
                    "user_rating": user_rating,
                    "rating": user_rating,
                })
            return episodes
        except httpx.RequestError as exc:
            logger.error("Network error fetching episodes from %s: %s", self.product_name, exc)
            return []

    async def mark_as_watched(self, rating_key: str) -> bool:
        """Mark an item as played directly on the server."""
        if not self.is_configured() or not rating_key:
            return False

        user_id = await self.get_default_user_id()
        if not user_id:
            logger.error("Cannot mark watched on %s: no user_id available", self.product_name)
            return False

        url = f"{self.base_url}/Users/{user_id}/PlayedItems/{rating_key}"
        client = self.get_client()
        try:
            resp = await client.post(url, headers=self._get_headers())
            return resp.status_code in (200, 204)
        except httpx.RequestError as exc:
            logger.error("Failed to mark item %s as watched on %s: %s", rating_key, self.product_name, exc)
            return False

    async def mark_as_unwatched(self, rating_key: str) -> bool:
        """Mark an item as unplayed directly on the server."""
        if not self.is_configured() or not rating_key:
            return False

        user_id = await self.get_default_user_id()
        if not user_id:
            logger.error("Cannot mark unwatched on %s: no user_id available", self.product_name)
            return False

        url = f"{self.base_url}/Users/{user_id}/PlayedItems/{rating_key}"
        client = self.get_client()
        try:
            resp = await client.delete(url, headers=self._get_headers())
            return resp.status_code in (200, 204)
        except httpx.RequestError as exc:
            logger.error("Failed to mark item %s as unwatched on %s: %s", rating_key, self.product_name, exc)
            return False

    async def set_user_rating(self, rating_key: str, rating_10: float) -> bool:
        """Set user rating on the server."""
        if not self.is_configured() or not rating_key:
            return False

        user_id = await self.get_default_user_id()
        if not user_id:
            logger.error("Cannot set rating on %s: no user_id available", self.product_name)
            return False

        url = f"{self.base_url}/Users/{user_id}/Items/{rating_key}/Rating"
        params = {"rating": str(round(float(rating_10), 1))}
        client = self.get_client()
        try:
            resp = await client.post(url, headers=self._get_headers(), params=params)
            if resp.status_code in (200, 204):
                return True
            # Fallback for Jellyfin favorites/likes
            likes_val = "true" if float(rating_10) >= 6.0 else "false"
            resp2 = await client.post(url, headers=self._get_headers(), params={"likes": likes_val})
            return resp2.status_code in (200, 204)
        except httpx.RequestError as exc:
            logger.error("Failed to set rating for item %s on %s: %s", rating_key, self.product_name, exc)
            return False

    async def find_item(self, media: Any) -> Optional[dict[str, Any]]:
        """Find an item on Jellyfin/Emby matching media title, year, season, episode, and/or IDs."""
        if not self.is_configured():
            return None

        user_id = await self.get_default_user_id()
        endpoint = f"/Users/{user_id}/Items" if user_id else "/Items"
        url = f"{self.base_url}{endpoint}"
        client = self.get_client()

        media_type = getattr(media, "media_type", "movie")
        is_episode = media_type == "episode"
        item_type = "Episode" if is_episode else "Movie"
        search_title = getattr(media, "title", "")
        if is_episode:
            search_title = getattr(media, "show_title", None) or getattr(media, "grandparent_title", None) or search_title

        params = {
            "SearchTerm": search_title,
            "IncludeItemTypes": item_type,
            "Recursive": "true",
            "Fields": "ProviderIds,UserData,SeriesName,IndexNumber,ParentIndexNumber,ProductionYear",
        }
        try:
            resp = await client.get(url, headers=self._get_headers(), params=params)
            items = []
            if resp.status_code == 200:
                items = resp.json().get("Items", [])

            # Fallback for episode if direct search yielded no items: search Series first
            if is_episode and not items:
                series_resp = await client.get(
                    url,
                    headers=self._get_headers(),
                    params={"SearchTerm": search_title, "IncludeItemTypes": "Series", "Recursive": "true"},
                )
                if series_resp.status_code == 200:
                    series_list = series_resp.json().get("Items", [])
                    if series_list:
                        s_id = series_list[0].get("Id")
                        ep_resp = await client.get(
                            url,
                            headers=self._get_headers(),
                            params={
                                "ParentId": s_id,
                                "IncludeItemTypes": "Episode",
                                "Recursive": "true",
                                "ParentIndexNumber": getattr(media, "season", 1),
                                "IndexNumber": getattr(media, "episode", 1),
                                "Fields": "ProviderIds,UserData,SeriesName,IndexNumber,ParentIndexNumber,ProductionYear",
                            },
                        )
                        if ep_resp.status_code == 200:
                            items = ep_resp.json().get("Items", [])

            target_ids = getattr(media, "ids", {}) or {}

            for item in items:
                provider_ids = item.get("ProviderIds") or {}
                item_ids = _extract_provider_ids(provider_ids, item)

                # Match by provider IDs
                for id_type, id_val in target_ids.items():
                    if id_val and item_ids.get(id_type) == str(id_val):
                        return {
                            "rating_key": str(item.get("Id")),
                            "title": item.get("Name"),
                            "ids": item_ids,
                            "type": item.get("Type"),
                        }

                # Match by season/episode or title/year
                if is_episode:
                    p_index = item.get("ParentIndexNumber")
                    ep_index = item.get("IndexNumber")
                    m_season = getattr(media, "season", None)
                    m_episode = getattr(media, "episode", None)
                    if (
                        m_season is not None
                        and m_episode is not None
                        and p_index == m_season
                        and ep_index == m_episode
                    ):
                        return {
                            "rating_key": str(item.get("Id")),
                            "title": item.get("Name"),
                            "ids": item_ids,
                            "type": item.get("Type"),
                        }
                else:
                    item_name = item.get("Name", "").strip().lower()
                    m_title = str(getattr(media, "title", "")).strip().lower()
                    if item_name == m_title:
                        item_year = item.get("ProductionYear")
                        m_year = getattr(media, "year", None)
                        if not m_year or not item_year or int(item_year) == int(m_year):
                            return {
                                "rating_key": str(item.get("Id")),
                                "title": item.get("Name"),
                                "ids": item_ids,
                                "type": item.get("Type"),
                            }
        except httpx.RequestError as exc:
            logger.error("Failed to search %s for item %s: %s", self.product_name, search_title, exc)
        return None

