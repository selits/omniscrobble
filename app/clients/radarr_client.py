"""Client for interacting with Radarr API."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("radarr_client")


class RadarrClient:
    """Asynchronous client for Radarr v3/v4 REST API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = (base_url or Config.RADARR_URL).rstrip("/")
        self.api_key = api_key or Config.RADARR_API_KEY
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None
        self._cached_movies: list[dict[str, Any]] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 300.0  # 5 minutes in-memory cache

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

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
        return {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def check_connection(self) -> dict[str, Any]:
        """Verify connectivity with Radarr and return system status details."""
        if not self.is_configured:
            return {"status": "unconfigured", "message": "Radarr URL or API key not configured"}

        url = f"{self.base_url}/api/v3/system/status"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": "connected",
                    "app_name": data.get("appName", "Radarr"),
                    "version": data.get("version", "unknown"),
                }
            return {"status": "error", "message": f"Radarr HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_movies(
        self, client: Optional[httpx.AsyncClient] = None, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch all movies from Radarr, using an in-memory cache."""
        if not self.is_configured:
            return []

        now = time.time()
        if not force_refresh and self._cached_movies and (now - self._cache_timestamp < self._cache_ttl):
            return self._cached_movies

        url = f"{self.base_url}/api/v3/movie"
        active_client = client or self.get_client()

        try:
            resp = await active_client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    simplified = [
                        {
                            "id": m.get("id"),
                            "title": m.get("title", ""),
                            "year": m.get("year"),
                            "tmdbId": m.get("tmdbId"),
                            "imdbId": m.get("imdbId"),
                            "status": m.get("status", ""),
                            "monitored": m.get("monitored", True),
                            "hasFile": m.get("hasFile", False),
                        }
                        for m in data
                        if m.get("title")
                    ]
                    # Sort alphabetically
                    simplified.sort(key=lambda x: x["title"].lower())
                    self._cached_movies = simplified
                    self._cache_timestamp = now
                    return self._cached_movies
            else:
                logger.warning(f"Radarr API returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Failed to fetch movies from Radarr ({url}): {e}")

        return self._cached_movies

    async def search_movies(
        self,
        query: str = "",
        client: Optional[httpx.AsyncClient] = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Search cached Radarr movies by title substring/prefix."""
        all_movies = await self.get_movies(client=client)
        if not query or not query.strip():
            return all_movies[:limit]

        q = query.strip().lower()
        starts_with = [m for m in all_movies if m["title"].lower().startswith(q)]
        contains = [
            m for m in all_movies if q in m["title"].lower() and not m["title"].lower().startswith(q)
        ]
        results = starts_with + contains
        return results[:limit]

    async def get_root_folders(self) -> list[dict[str, Any]]:
        """Fetch available root storage folders from Radarr."""
        if not self.is_configured:
            return []
        url = f"{self.base_url}/api/v3/rootfolder"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error fetching root folders from Radarr: {e}")
        return []

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        """Fetch available quality profiles from Radarr."""
        if not self.is_configured:
            return []
        url = f"{self.base_url}/api/v3/qualityprofile"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error fetching quality profiles from Radarr: {e}")
        return []

    async def lookup_movie(self, term: str) -> list[dict[str, Any]]:
        """Look up movie in Radarr using term (tmdbId, imdbId, or title search)."""
        if not self.is_configured or not term:
            return []
        url = f"{self.base_url}/api/v3/movie/lookup"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers(), params={"term": term})
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error looking up movie in Radarr for term {term}: {e}")
        return []

    async def has_movie(
        self,
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> bool:
        """Check if movie already exists in Radarr library."""
        movies = await self.get_movies()
        clean_imdb = imdb_id.lower() if imdb_id else None
        clean_title = title.strip().lower() if title else None

        for m in movies:
            if tmdb_id and m.get("tmdbId") and int(m["tmdbId"]) == int(tmdb_id):
                return True
            if clean_imdb and m.get("imdbId") and str(m["imdbId"]).lower() == clean_imdb:
                return True
            if clean_title and m.get("title", "").strip().lower() == clean_title:
                return True
        return False

    async def add_movie(
        self,
        movie_data: dict[str, Any],
        root_folder_path: Optional[str] = None,
        quality_profile_id: Optional[int] = None,
        monitored: bool = True,
        search_for_movie: bool = True,
    ) -> dict[str, Any]:
        """Add a movie to Radarr library."""
        if not self.is_configured:
            return {"success": False, "error": "Radarr not configured"}

        # Determine root folder
        r_path = root_folder_path or Config.RADARR_ROOT_FOLDER
        if not r_path:
            folders = await self.get_root_folders()
            if folders:
                r_path = folders[0].get("path")
        if not r_path:
            return {"success": False, "error": "No root folder available in Radarr"}

        # Determine quality profile
        qp_id = quality_profile_id or Config.RADARR_QUALITY_PROFILE_ID
        if not qp_id:
            profiles = await self.get_quality_profiles()
            if profiles:
                qp_id = profiles[0].get("id")
        if not qp_id:
            return {"success": False, "error": "No quality profile available in Radarr"}

        payload = dict(movie_data)
        payload["rootFolderPath"] = r_path
        payload["qualityProfileId"] = qp_id
        payload["monitored"] = monitored
        payload["addOptions"] = {
            "searchForMovie": search_for_movie,
        }

        url = f"{self.base_url}/api/v3/movie"
        client = self.get_client()
        try:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            if resp.status_code in (200, 201):
                self.clear_cache()
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": f"Radarr HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error(f"Failed to add movie to Radarr: {e}")
            return {"success": False, "error": str(e)}

    def clear_cache(self) -> None:
        self._cached_movies = []
        self._cache_timestamp = 0.0


# Expose parse_radarr_webhook for convenient client module access
from app.clients.sonarr_client import parse_radarr_webhook  # noqa: E402
