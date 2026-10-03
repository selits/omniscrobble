"""Client for interacting with MDBList API.

Provides multi-source rating aggregation (Rotten Tomatoes Critics/Audience,
Metacritic, IMDb, Letterboxd) and watchlist synchronization for movies and TV shows.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("omniscrobble.mdblist_client")


class MDBListClient:
    """Asynchronous client for MDBList API."""

    BASE_URL = "https://mdblist.com/api"

    def __init__(
        self,
        config: type[Config] = Config,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.config = config
        self.api_key = api_key or getattr(config, "MDBLIST_API_KEY", "") or ""
        if transport is not None and client is None:
            client = httpx.AsyncClient(transport=transport, timeout=10.0)
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None
        self._cached_ratings: dict[str, dict[str, Any]] = {}

    def get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        if self._internal_client is None or self._internal_client.is_closed:
            self._internal_client = httpx.AsyncClient(timeout=10.0)
        return self._internal_client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def is_authenticated(self) -> bool:
        return bool(self.api_key)

    def is_enabled(self) -> bool:
        return self.is_configured()

    async def check_connection(self) -> dict[str, Any]:
        """Verify API key and fetch account information."""
        if not self.is_configured():
            return {
                "name": "MDBList",
                "configured": False,
                "authenticated": False,
                "enabled": True,
                "status": "unconfigured",
                "message": "MDBList API key is not configured.",
            }

        client = self.get_client()
        url = f"{self.BASE_URL}/user"
        try:
            resp = await client.get(url, params={"apikey": self.api_key})
            if resp.status_code == 200:
                data = resp.json()
                uname = data.get("username") or data.get("name") or "user"
                limits = data.get("api_limits", {})
                return {
                    "name": "MDBList",
                    "configured": True,
                    "authenticated": True,
                    "enabled": True,
                    "status": "connected",
                    "username": uname,
                    "user": uname,
                    "api_used": limits.get("used", 0),
                    "api_total": limits.get("total", 0),
                    "message": f"Connected as @{uname}",
                }
            return {
                "name": "MDBList",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": f"MDBList returned HTTP {resp.status_code}",
            }
        except Exception as e:
            logger.error("MDBList check_connection failed: %s", e)
            return {
                "name": "MDBList",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": str(e),
            }

    async def get_ratings(
        self,
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int | str] = None,
        media_type: str = "movie",
    ) -> Optional[dict[str, Any]]:
        """Fetch aggregate rating scores across Rotten Tomatoes, Metacritic, Letterboxd, and IMDb."""
        if not self.is_configured():
            return None

        cache_key = f"{imdb_id or ''}_{tmdb_id or ''}_{media_type}"
        if cache_key in self._cached_ratings:
            return self._cached_ratings[cache_key]

        params: dict[str, Any] = {"apikey": self.api_key}
        if imdb_id:
            params["i"] = imdb_id
        elif tmdb_id:
            params["tm"] = str(tmdb_id)
            params["m"] = "show" if media_type in ("show", "episode", "tv") else "movie"
        else:
            return None

        client = self.get_client()
        try:
            resp = await client.get(f"{self.BASE_URL}/", params=params)
            if resp.status_code == 200:
                data = resp.json()
                ratings_list = data.get("ratings", [])
                score_val = data.get("score") if data.get("score") is not None else data.get("score_average")
                formatted_ratings: dict[str, Any] = {
                    "title": data.get("title"),
                    "year": data.get("year"),
                    "score_average": data.get("score_average"),
                    "score": score_val,
                    "ratings": {},
                }
                for r in ratings_list:
                    src = r.get("source", "").lower()
                    formatted_ratings["ratings"][src] = {
                        "value": r.get("value"),
                        "score": r.get("score"),
                        "votes": r.get("votes"),
                    }
                    if src:
                        formatted_ratings[src] = r.get("value")
                self._cached_ratings[cache_key] = formatted_ratings
                return formatted_ratings
        except Exception as e:
            logger.error("MDBList get_ratings failed: %s", e)

        return None

    async def get_item_ratings(
        self,
        media_type: str = "movie",
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int | str] = None,
    ) -> Optional[dict[str, Any]]:
        """Alias for get_ratings() matching MultiTrackerManager convention."""
        return await self.get_ratings(imdb_id=imdb_id, tmdb_id=tmdb_id, media_type=media_type)

    async def add_to_watchlist(
        self,
        media_type: str,
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int | str] = None,
    ) -> dict[str, Any]:
        """Add media item to MDBList user watchlist/default list."""
        if not self.is_configured():
            return {"status": "error", "message": "MDBList not configured"}

        client = self.get_client()
        url = f"{self.BASE_URL}/lists/items/add"
        payload: dict[str, Any] = {
            "mediatype": "show" if media_type in ("show", "episode", "tv") else "movie"
        }
        if imdb_id:
            payload["imdb_id"] = imdb_id
        if tmdb_id:
            payload["tmdb_id"] = int(tmdb_id)

        try:
            resp = await client.post(url, params={"apikey": self.api_key}, json=payload)
            if resp.status_code in (200, 201):
                return {"status": "success", "data": resp.json()}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            logger.error("MDBList add_to_watchlist failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None
