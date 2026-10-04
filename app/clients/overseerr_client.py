"""Client for interacting with Overseerr / Jellyseerr API."""
from __future__ import annotations

import logging
from typing import Any, Optional, Union
import httpx

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("overseerr_client")


class OverseerrClient:
    """Asynchronous client for Overseerr and Jellyseerr REST API v1."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = (base_url or getattr(Config, "OVERSEERR_URL", "")).rstrip("/")
        self.api_key = api_key or getattr(Config, "OVERSEERR_API_KEY", "")
        self._external_client = client
        self._internal_client: Optional[httpx.AsyncClient] = None

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
        """Verify connectivity with Overseerr/Jellyseerr and return system status details."""
        if not self.is_configured:
            return {"status": "unconfigured", "message": "Overseerr URL or API key not configured"}

        url = f"{self.base_url}/api/v1/status"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                app_name = "Jellyseerr" if "jellyseerr" in str(data.get("commitTag", "")).lower() or "jellyseerr" in self.base_url.lower() else "Overseerr"
                return {
                    "status": "connected",
                    "app_name": app_name,
                    "version": data.get("version", "unknown"),
                    "commit_tag": data.get("commitTag", ""),
                }
            return {"status": "error", "message": f"Overseerr HTTP {resp.status_code}"}
        except Exception as e:
            logger.warning("Failed to connect to Overseerr at %s: %s", self.base_url, e)
            return {"status": "error", "message": str(e)}

    async def get_request_counts(self) -> dict[str, int]:
        """Fetch summary request statistics (total, pending, approved, available)."""
        if not self.is_configured:
            return {}

        url = f"{self.base_url}/api/v1/request/count"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "total": int(data.get("total", 0)),
                    "pending": int(data.get("pending", 0)),
                    "approved": int(data.get("approved", 0)),
                    "available": int(data.get("available", 0)),
                }
            return {}
        except Exception as e:
            logger.warning("Error fetching Overseerr request counts: %s", e)
            return {}

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search media on Overseerr/Jellyseerr by title query."""
        if not self.is_configured or not query:
            return []

        url = f"{self.base_url}/api/v1/search"
        client = self.get_client()
        try:
            resp = await client.get(url, params={"query": query}, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            return []
        except Exception as e:
            logger.error("Error searching Overseerr for '%s': %s", query, e)
            return []

    async def get_media_details(self, media_type: str, tmdb_id: int) -> Optional[dict[str, Any]]:
        """Fetch media information from Overseerr including current request and availability status."""
        if not self.is_configured or not tmdb_id:
            return None

        m_type = "movie" if media_type.lower() in ("movie", "movies") else "tv"
        url = f"{self.base_url}/api/v1/{m_type}/{tmdb_id}"
        client = self.get_client()
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.warning("Error fetching Overseerr media details for %s ID %s: %s", m_type, tmdb_id, e)
            return None

    async def has_media(self, media_type: str, tmdb_id: int) -> bool:
        """Check if media is already requested, processing, or available in Overseerr/Jellyseerr."""
        details = await self.get_media_details(media_type, tmdb_id)
        if not details:
            return False

        media_info = details.get("mediaInfo")
        if not media_info:
            return False

        # Status 2 = PENDING, 3 = PROCESSING, 4 = PARTIALLY_AVAILABLE, 5 = AVAILABLE
        status = media_info.get("status", 1)
        return status in (2, 3, 4, 5)

    async def request_media(
        self,
        media_type: str,
        tmdb_id: int,
        seasons: Optional[Union[str, list[int]]] = None,
        is_4k: bool = False,
    ) -> dict[str, Any]:
        """Submit a media request to Overseerr/Jellyseerr."""
        if not self.is_configured:
            return {"success": False, "error": "Overseerr is not configured"}

        m_type = "movie" if media_type.lower() in ("movie", "movies") else "tv"
        url = f"{self.base_url}/api/v1/request"
        payload: dict[str, Any] = {
            "mediaType": m_type,
            "mediaId": int(tmdb_id),
            "is4k": bool(is_4k),
        }

        if m_type == "tv":
            if seasons is None or seasons == "all":
                payload["seasons"] = "all"
            elif isinstance(seasons, list):
                payload["seasons"] = seasons
            else:
                payload["seasons"] = "all"

        client = self.get_client()
        try:
            resp = await client.post(url, json=payload, headers=self._get_headers())
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"success": True, "request": data}
            elif resp.status_code == 409:
                return {"success": True, "skipped": True, "reason": "Already requested or available"}
            else:
                err_text = resp.text
                try:
                    err_json = resp.json()
                    err_text = err_json.get("message", err_text)
                except Exception:
                    pass
                return {"success": False, "error": f"HTTP {resp.status_code}: {err_text}"}
        except Exception as e:
            logger.error("Error submitting Overseerr request for %s ID %s: %s", m_type, tmdb_id, e)
            return {"success": False, "error": str(e)}
