from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional
import httpx
try:
    from app.config import Config
    from app.services.atomic_writer import atomic_write_json
except ImportError:
    from config import Config
    from atomic_writer import atomic_write_json

logger = logging.getLogger("trakt_client")


def _retry_delay(retry_after: Optional[str], attempt: int) -> float:
    """Honor Trakt's Retry-After value; otherwise use bounded exponential backoff."""
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return float(min(30, 2 ** attempt))


class TraktClient:
    def __init__(
        self,
        config: type[Config] = Config,
        tokens_file: Optional[Path] = None,
        client: Optional[httpx.AsyncClient] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.config = config
        try:
            from app.services.settings_manager import settings_mgr
            stored_creds = settings_mgr.get_tracker_credentials("trakt", mask=False)
        except Exception:
            stored_creds = {}
        self.client_id = client_id or stored_creds.get("client_id") or getattr(config, "TRAKT_CLIENT_ID", "")
        self.client_secret = client_secret or stored_creds.get("client_secret") or getattr(config, "TRAKT_CLIENT_SECRET", "")
        self.api_url = getattr(config, "TRAKT_API_URL", "https://api.trakt.tv")
        self.tokens_file = tokens_file if tokens_file is not None else config.TRAKT_TOKENS_FILE
        self._tokens: Optional[dict[str, Any]] = None
        self._http_client: Optional[httpx.AsyncClient] = client
        self.access_token: Optional[str] = None

    def update_credentials(self, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> None:
        """Update client credentials in-memory dynamically."""
        if client_id is not None:
            self.client_id = client_id
        if client_secret is not None:
            self.client_secret = client_secret

    @property
    def effective_client_id(self) -> str:
        if self.client_id:
            return self.client_id
        try:
            from app.services.settings_manager import settings_mgr
            val = settings_mgr.get_tracker_credentials("trakt", mask=False).get("client_id", "")
            if val:
                return val
        except Exception:
            pass
        return getattr(self.config, "TRAKT_CLIENT_ID", "")

    @property
    def effective_client_secret(self) -> str:
        if self.client_secret:
            return self.client_secret
        try:
            from app.services.settings_manager import settings_mgr
            val = settings_mgr.get_tracker_credentials("trakt", mask=False).get("client_secret", "")
            if val:
                return val
        except Exception:
            pass
        return getattr(self.config, "TRAKT_CLIENT_SECRET", "")

    def is_enabled(self) -> bool:
        """Check if Trakt tracking is enabled in dynamic settings or configuration."""
        try:
            from app.services.settings_manager import settings_mgr
            return settings_mgr.is_tracker_enabled("trakt")
        except Exception:
            return getattr(self.config, "TRAKT_ENABLED", True)

    def delete_tokens(self) -> None:
        """Clear tokens from memory and delete tokens file from disk."""
        self._tokens = None
        self.access_token = None
        if self.tokens_file.exists():
            try:
                self.tokens_file.unlink()
                logger.info(f"Successfully deleted Trakt tokens file: {self.tokens_file}")
            except Exception as e:
                logger.error(f"Failed to delete Trakt tokens file {self.tokens_file}: {e}")

    def get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _get_headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.effective_client_id,
        }
        if authenticated:
            token = await self.get_valid_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def load_tokens(self) -> Optional[dict[str, Any]]:
        if self._tokens:
            return self._tokens
        if self.tokens_file.exists():
            try:
                from app.services.crypto_manager import crypto_mgr
                self._tokens = crypto_mgr.read_secure_json(self.tokens_file)
                return self._tokens
            except Exception as e:
                logger.error(f"Error loading Trakt tokens from {self.tokens_file}: {e}")
        return None

    def save_tokens(self, tokens: dict[str, Any]) -> None:
        self._tokens = tokens
        from app.services.crypto_manager import crypto_mgr
        crypto_mgr.write_secure_json(self.tokens_file, tokens)
        logger.info(f"Successfully saved Trakt tokens to {self.tokens_file}")

    def is_authenticated(self) -> bool:
        if self.access_token:
            return True
        tokens = self.load_tokens()
        return bool(tokens and tokens.get("access_token"))

    def get_token_info(self) -> dict[str, Any]:
        """Return token status, health, and remaining days until auto-renewal."""
        if self.access_token:
            return {"status": "healthy", "healthy": True, "days_remaining": 90}
        tokens = self.load_tokens()
        if not tokens or not tokens.get("access_token"):
            return {"status": "none", "healthy": False, "days_remaining": 0}

        created_at = tokens.get("created_at", 0)
        expires_in = tokens.get("expires_in", 0)
        if not created_at or not expires_in:
            return {"status": "healthy", "healthy": True, "days_remaining": 90}

        now = time.time()
        expires_at = created_at + expires_in
        seconds_remaining = expires_at - now
        days_remaining = max(0, int(seconds_remaining // 86400))

        if seconds_remaining <= 0:
            return {
                "status": "expired",
                "healthy": False,
                "days_remaining": 0,
                "created_at": created_at,
                "expires_in": expires_in,
            }

        return {
            "status": "healthy",
            "healthy": True,
            "days_remaining": days_remaining,
            "created_at": created_at,
            "expires_in": expires_in,
        }

    async def get_valid_token(self) -> Optional[str]:
        if self.access_token:
            return self.access_token
        tokens = self.load_tokens()
        if not tokens:
            return None

        access_token = tokens.get("access_token")
        created_at = tokens.get("created_at", 0)
        expires_in = tokens.get("expires_in", 0)
        now = time.time()

        # If token expires within 24 hours (or is expired), refresh it
        if created_at and expires_in and (now >= created_at + expires_in - 86400):
            logger.info("Trakt access token near expiry, refreshing...")
            refreshed = await self.refresh_token()
            if refreshed:
                return refreshed.get("access_token")
            return access_token

        return access_token

    # ------------------ OAuth Device Code Flow ------------------

    async def generate_device_code(self) -> dict[str, Any]:
        """Request a device code from Trakt."""
        cid = self.effective_client_id
        if not cid:
            raise ValueError("TRAKT_CLIENT_ID is not configured in Settings Hub or .env")

        url = f"{self.api_url}/oauth/device/code"
        client = self.get_client()
        res = await client.post(url, json={"client_id": cid})
        if res.status_code != 200:
            raise RuntimeError(f"Failed to generate device code ({res.status_code}): {res.text}")
        return res.json()

    async def poll_for_token(self, device_code: str) -> dict[str, Any]:
        """Poll Trakt token endpoint for user approval of device code.

        Returns tokens dict when approved, or raises an exception.
        """
        cid = self.effective_client_id
        csec = self.effective_client_secret
        if not cid or not csec:
            raise ValueError("TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET are required for authentication. Please configure them in Settings Hub or .env.")

        url = f"{self.api_url}/oauth/device/token"
        payload = {
            "code": device_code,
            "client_id": cid,
            "client_secret": csec,
        }
        client = self.get_client()
        res = await client.post(url, json=payload)

        if res.status_code == 200:
            tokens = res.json()
            if "created_at" not in tokens:
                tokens["created_at"] = int(time.time())
            self.save_tokens(tokens)
            return tokens
        elif res.status_code == 400:
            return {"status": "pending"}
        elif res.status_code == 401:
            raise PermissionError(f"Invalid client credentials ({res.status_code}). Please verify your Trakt Client ID and Client Secret in Settings Hub.")
        elif res.status_code == 404:
            raise RuntimeError("Invalid device code.")
        elif res.status_code == 409:
            raise RuntimeError("Device code has already been used.")
        elif res.status_code == 410:
            raise TimeoutError("Device code has expired. Please request a new one.")
        elif res.status_code == 418:
            raise PermissionError("User denied the authorization request on Trakt.")
        elif res.status_code == 429:
            logger.warning("Trakt rate limit encountered during polling.")
            return {"status": "slow_down"}
        else:
            raise RuntimeError(f"Unexpected status from Trakt ({res.status_code}): {res.text}")

    async def refresh_token(self) -> Optional[dict[str, Any]]:
        """Refresh single-use OAuth access/refresh tokens."""
        tokens = self.load_tokens()
        if not tokens or not tokens.get("refresh_token"):
            logger.error("No refresh token available.")
            return None

        cid = self.effective_client_id
        csec = self.effective_client_secret
        if not cid or not csec:
            logger.error("TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET are required for token refresh.")
            return None

        url = f"{self.api_url}/oauth/token"
        payload = {
            "refresh_token": tokens["refresh_token"],
            "client_id": cid,
            "client_secret": csec,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        }
        client = self.get_client()
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                new_tokens = res.json()
                if "created_at" not in new_tokens:
                    new_tokens["created_at"] = int(time.time())
                self.save_tokens(new_tokens)
                return new_tokens
            else:
                logger.error(f"Failed to refresh Trakt token ({res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"Network error refreshing Trakt token: {e}")
            return None

    # ------------------ Scrobbling & History Endpoints ------------------

    def _prepare_scrobble_payload(
        self,
        media_payload: Any,
        progress: Optional[float] = None,
    ) -> dict[str, Any]:
        """Convert ParsedMedia or dict to Trakt scrobble payload format."""
        if hasattr(media_payload, "to_trakt_scrobble_payload"):
            payload = media_payload.to_trakt_scrobble_payload()
            if progress is not None:
                payload["progress"] = round(float(progress), 1)
            return payload
        if isinstance(media_payload, dict):
            payload = dict(media_payload)
            if progress is not None:
                payload["progress"] = round(float(progress), 1)
            return payload
        return {}

    async def scrobble_start(self, media_payload: Any, progress: Optional[float] = None) -> dict[str, Any]:
        """POST /scrobble/start - Notify Trakt playback started."""
        url = f"{self.api_url}/scrobble/start"
        payload = self._prepare_scrobble_payload(media_payload, progress)
        return await self._post_authenticated(url, payload)

    async def scrobble_pause(self, media_payload: Any, progress: Optional[float] = None) -> dict[str, Any]:
        """POST /scrobble/pause - Notify Trakt playback paused."""
        url = f"{self.api_url}/scrobble/pause"
        payload = self._prepare_scrobble_payload(media_payload, progress)
        return await self._post_authenticated(url, payload)

    async def scrobble_stop(self, media_payload: Any, progress: Optional[float] = None) -> dict[str, Any]:
        """POST /scrobble/stop - Notify Trakt playback stopped.

        Marks as watched if progress >= 80%.
        """
        url = f"{self.api_url}/scrobble/stop"
        payload = self._prepare_scrobble_payload(media_payload, progress)
        return await self._post_authenticated(url, payload)

    async def sync_history(self, sync_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/history - Directly mark episodes/movies as watched in Trakt history."""
        url = f"{self.api_url}/sync/history"
        return await self._post_authenticated(url, sync_payload)

    async def remove_history(self, remove_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/history/remove - Remove episodes/movies from Trakt watched history."""
        url = f"{self.api_url}/sync/history/remove"
        return await self._post_authenticated(url, remove_payload)

    async def sync_ratings(self, rating_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/ratings - Rate movies, shows, or episodes on Trakt."""
        url = f"{self.api_url}/sync/ratings"
        return await self._post_authenticated(url, rating_payload)

    async def sync_collection(self, collection_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/collection - Add movies, shows, or episodes to user's Trakt collection."""
        url = f"{self.api_url}/sync/collection"
        return await self._post_authenticated(url, collection_payload)

    async def sync_watchlist(self, watchlist_payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/watchlist - Add movies, shows, or seasons/episodes to user's Trakt watchlist."""
        url = f"{self.api_url}/sync/watchlist"
        return await self._post_authenticated(url, watchlist_payload)

    async def get_watched_movies(self) -> list[dict[str, Any]]:
        """GET /sync/watched/movies - Fetch user's entire watched movies history from Trakt."""
        url = f"{self.api_url}/sync/watched/movies"
        res = await self._get_authenticated(url)
        return res if isinstance(res, list) else []

    async def get_watched_shows(self) -> list[dict[str, Any]]:
        """GET /sync/watched/shows - Fetch user's entire watched TV shows history from Trakt."""
        url = f"{self.api_url}/sync/watched/shows"
        res = await self._get_authenticated(url)
        return res if isinstance(res, list) else []

    async def get_ratings(self, media_type: Optional[str] = None) -> list[dict[str, Any]]:
        """GET /sync/ratings/{type} - Fetch user's star ratings from Trakt."""
        subpath = f"/{media_type}" if media_type in ("movies", "shows", "seasons", "episodes") else ""
        url = f"{self.api_url}/sync/ratings{subpath}"
        res = await self._get_authenticated(url)
        return res if isinstance(res, list) else []

    async def get_watchlist(self, media_type: str = "movies") -> list[dict[str, Any]]:
        """GET /sync/watchlist/{type} - Fetch user's Trakt watchlist items (movies or shows)."""
        subpath = f"/{media_type}" if media_type in ("movies", "shows", "seasons", "episodes") else "/movies"
        url = f"{self.api_url}/sync/watchlist{subpath}"
        res = await self._get_authenticated(url)
        return res if isinstance(res, list) else []


    async def get_user_settings(self) -> Optional[dict[str, Any]]:
        """GET /users/settings - Retrieve authenticated user profile information."""
        if not self.is_authenticated():
            return None
        url = f"{self.api_url}/users/settings"
        headers = await self._get_headers(authenticated=True)
        client = self.get_client()
        try:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 401:
                refreshed = await self.refresh_token()
                if refreshed:
                    headers = await self._get_headers(authenticated=True)
                    retry_res = await client.get(url, headers=headers)
                    if retry_res.status_code == 200:
                        return retry_res.json()
        except Exception as e:
            logger.warning(f"Failed to fetch user settings from Trakt: {e}")
        return None

    async def search_show(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a show by title/year."""
        url = f"{self.api_url}/search/show"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching show '{title}': {e}")
        return []

    async def search_movie(self, title: str, year: Optional[int] = None) -> list[dict[str, Any]]:
        """Fallback search for a movie by title/year."""
        url = f"{self.api_url}/search/movie"
        params: dict[str, Any] = {"query": title}
        if year:
            params["years"] = str(year)
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching movie '{title}': {e}")
    async def search_media(self, query: str, media_type: Optional[str] = None) -> list[dict[str, Any]]:
        """Search Trakt catalog for movies and/or shows."""
        type_path = media_type if media_type in ("movie", "show", "episode") else "movie,show"
        url = f"{self.api_url}/search/{type_path}"
        params: dict[str, Any] = {"query": query, "limit": 10}
        try:
            client = self.get_client()
            headers = await self._get_headers(authenticated=False)
            res = await client.get(url, headers=headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Error searching media '{query}': {e}")
        return []

    async def _post_authenticated(
        self, url: str, data: dict[str, Any], retry_auth: bool = True
    ) -> dict[str, Any]:
        client = self.get_client()

        for attempt in range(3):
            headers = await self._get_headers(authenticated=True)
            try:
                res = await client.post(url, headers=headers, json=data)
            except httpx.RequestError as exc:
                logger.error(f"HTTP error contacting Trakt at {url}: {exc}")
                return {"error": str(exc), "status": 503}

            if res.status_code in (200, 201):
                return res.json()
            elif res.status_code == 409:
                # Trakt returns 409 if already scrobbled or conflict
                logger.info(f"Trakt 409 Conflict/Already scrobbled for {url}")
                return {"action": "conflict", "status": 409}
            elif res.status_code == 401 and retry_auth:
                logger.warning("Trakt 401 Unauthorized encountered. Attempting token refresh...")
                refreshed = await self.refresh_token()
                if refreshed:
                    # Retry once with refreshed credentials
                    return await self._post_authenticated(url, data, retry_auth=False)
                else:
                    return {"error": "Authentication failed (token refresh failed)", "status": 401}
            elif res.status_code == 429:
                wait_time = _retry_delay(res.headers.get("Retry-After"), attempt)
                logger.warning(
                    f"Trakt rate limit (429) on {url}. Waiting {wait_time}s (attempt {attempt + 1}/3)..."
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Trakt API error ({res.status_code}) on {url}: {res.text}")
                return {"error": res.text, "status": res.status_code}

        return {"error": "Trakt rate limit exceeded after retries", "status": 429}

    async def _get_authenticated(
        self, url: str, params: Optional[dict[str, Any]] = None, retry_auth: bool = True
    ) -> Any:
        client = self.get_client()

        for attempt in range(3):
            headers = await self._get_headers(authenticated=True)
            try:
                res = await client.get(url, headers=headers, params=params)
            except httpx.RequestError as exc:
                logger.error(f"HTTP error contacting Trakt at {url}: {exc}")
                return {"error": str(exc), "status": 503}

            if res.status_code == 200:
                return res.json()
            elif res.status_code == 401 and retry_auth:
                logger.warning("Trakt 401 Unauthorized encountered on GET. Attempting token refresh...")
                refreshed = await self.refresh_token()
                if refreshed:
                    return await self._get_authenticated(url, params=params, retry_auth=False)
                else:
                    return {"error": "Authentication failed (token refresh failed)", "status": 401}
            elif res.status_code == 429:
                wait_time = _retry_delay(res.headers.get("Retry-After"), attempt)
                logger.warning(
                    f"Trakt rate limit (429) on {url}. Waiting {wait_time}s (attempt {attempt + 1}/3)..."
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Trakt API GET error ({res.status_code}) on {url}: {res.text}")
                return {"error": res.text, "status": res.status_code}

        return {"error": "Trakt rate limit exceeded after retries", "status": 429}
