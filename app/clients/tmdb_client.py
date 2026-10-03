"""Client for interacting with The Movie Database (TMDb) v3/v4 API.

Supports account watchlist synchronization, favorites, and 1-10 star ratings
for movies and TV series.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("omniscrobble.tmdb_client")


class TMDbClient:
    """Asynchronous client for TMDb API."""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(
        self,
        config: type[Config] = Config,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        session_id: Optional[str] = None,
        account_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.config = config
        self.api_key = api_key or getattr(config, "TMDB_API_KEY", "") or ""
        self.access_token = access_token or getattr(config, "TMDB_READ_ACCESS_TOKEN", getattr(config, "TMDB_ACCESS_TOKEN", "")) or ""
        self.session_id = session_id or getattr(config, "TMDB_SESSION_ID", "") or ""
        self.account_id = account_id or getattr(config, "TMDB_ACCOUNT_ID", "") or ""
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
        """Return True if TMDb credentials (access token or api key) are present."""
        return bool(self.access_token or self.api_key)

    def is_authenticated(self) -> bool:
        """Return True if user session or authenticated access token is configured."""
        return bool(self.access_token or (self.api_key and self.session_id))

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _get_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.api_key and not self.access_token:
            params["api_key"] = self.api_key
        if self.session_id:
            params["session_id"] = self.session_id
        return params

    async def check_connection(self) -> dict[str, Any]:
        """Verify API authentication and fetch user account info if authenticated."""
        if not self.is_configured():
            return {
                "name": "TMDb",
                "configured": False,
                "authenticated": False,
                "enabled": True,
                "status": "unconfigured",
                "message": "TMDb API Key or Access Token is missing.",
            }

        client = self.get_client()
        try:
            # If session is present, check account details
            if self.session_id or self.access_token:
                url = f"{self.BASE_URL}/account"
                params = self._get_params()
                resp = await client.get(url, headers=self._get_headers(), params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    username = data.get("username", "user")
                    self.account_id = str(data.get("id", ""))
                    return {
                        "name": "TMDb",
                        "configured": True,
                        "authenticated": True,
                        "enabled": True,
                        "status": "connected",
                        "username": username,
                        "account_id": self.account_id,
                        "message": f"Connected as @{username}",
                    }

            # Otherwise, check basic API key validity via configuration endpoint
            url = f"{self.BASE_URL}/configuration"
            resp = await client.get(url, headers=self._get_headers(), params=self._get_params())
            if resp.status_code == 200:
                return {
                    "name": "TMDb",
                    "configured": True,
                    "authenticated": bool(self.session_id or self.access_token),
                    "enabled": True,
                    "status": "connected_readonly" if not self.session_id else "connected",
                    "message": "API key valid (read-only without session)",
                }
            return {
                "name": "TMDb",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": f"TMDb API returned HTTP {resp.status_code}",
            }
        except Exception as e:
            logger.error("TMDb check_connection failed: %s", e)
            return {
                "name": "TMDb",
                "configured": True,
                "authenticated": False,
                "enabled": True,
                "status": "error",
                "message": str(e),
            }

    async def sync_rating(
        self,
        media_type: str,
        tmdb_id: int | str,
        rating: float | int,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> dict[str, Any]:
        """Post a 0.5 - 10.0 user rating to TMDb.
        
        Args:
            media_type: 'movie' | 'tv' | 'episode'
            tmdb_id: Numerical TMDb ID
            rating: 1-10 star rating
            season: Optional TV season number (for episode ratings)
            episode: Optional TV episode number (for episode ratings)
        """
        if not self.is_configured():
            return {"status": "error", "message": "TMDb not configured"}

        # TMDb rating is 0.5 - 10.0 in steps of 0.5
        val = max(0.5, min(10.0, float(rating)))
        if media_type == "episode" and season is not None and episode is not None:
            url = f"{self.BASE_URL}/tv/{tmdb_id}/season/{season}/episode/{episode}/rating"
        else:
            endpoint_type = "tv" if media_type in ("show", "tv") else "movie"
            url = f"{self.BASE_URL}/{endpoint_type}/{tmdb_id}/rating"
        client = self.get_client()

        try:
            resp = await client.post(
                url,
                headers=self._get_headers(),
                params=self._get_params(),
                json={"value": val},
            )
            if resp.status_code in (200, 201):
                return {"status": "success", "rating": val, "tmdb_id": tmdb_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error("TMDb sync_rating failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def sync_watchlist(self, media_type: str, tmdb_id: int | str, watchlist: bool = True) -> dict[str, Any]:
        """Add or remove an item from the user's TMDb Watchlist."""
        if not self.is_configured():
            return {"status": "error", "message": "TMDb not configured"}

        account_id = self.account_id or "account_id"
        endpoint_type = "tv" if media_type in ("show", "tv", "episode") else "movie"
        url = f"{self.BASE_URL}/account/{account_id}/watchlist"
        client = self.get_client()

        payload = {
            "media_type": endpoint_type,
            "media_id": int(tmdb_id),
            "watchlist": watchlist,
        }

        try:
            resp = await client.post(
                url,
                headers=self._get_headers(),
                params=self._get_params(),
                json=payload,
            )
            if resp.status_code in (200, 201):
                return {
                    "status": "success",
                    "action": "add" if watchlist else "remove",
                    "watchlist": watchlist,
                    "tmdb_id": tmdb_id,
                }
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error("TMDb sync_watchlist failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        if self._internal_client and not self._internal_client.is_closed:
            await self._internal_client.aclose()
            self._internal_client = None
