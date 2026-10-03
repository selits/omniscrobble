"""Simkl REST API client for Omniscrobble multi-tracker dispatch.

Handles OAuth Device PIN flow, token persistence, connection validation,
and real-time scrobble (start, pause, stop) and history/ratings sync.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import Config
from app.plex_parser import ParsedMedia

logger = logging.getLogger("omniscrobble.simkl")


class SimklClient:
    """Asynchronous client for interacting with the Simkl API."""

    def __init__(
        self,
        config: type[Config] = Config,
        client: Optional[httpx.AsyncClient] = None,
        tokens_file: Optional[Path] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> None:
        self.config = config
        try:
            from app.services.settings_manager import settings_mgr
            stored_creds = settings_mgr.get_tracker_credentials("simkl", mask=False)
        except Exception:
            stored_creds = {}
        self.client_id = client_id or stored_creds.get("client_id") or config.SIMKL_CLIENT_ID
        self.client_secret = client_secret or stored_creds.get("client_secret") or config.SIMKL_CLIENT_SECRET
        self.base_url = config.SIMKL_API_URL.rstrip("/")
        self.tokens_file = tokens_file or config.SIMKL_TOKENS_FILE
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(timeout=15.0)

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.created_at: Optional[int] = None
        self.expires_in: Optional[int] = None
        self.user_name: Optional[str] = None
        self.load_tokens()

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
            return settings_mgr.get_tracker_credentials("simkl", mask=False).get("client_id", "")
        except Exception:
            return getattr(self.config, "SIMKL_CLIENT_ID", "")

    @property
    def effective_client_secret(self) -> str:
        if self.client_secret:
            return self.client_secret
        try:
            from app.services.settings_manager import settings_mgr
            return settings_mgr.get_tracker_credentials("simkl", mask=False).get("client_secret", "")
        except Exception:
            return getattr(self.config, "SIMKL_CLIENT_SECRET", "")

    def load_tokens(self) -> None:
        """Load stored access token, refresh token, and user info from disk."""
        if not self.tokens_file.exists():
            return
        try:
            with open(self.tokens_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.created_at = data.get("created_at")
                self.expires_in = data.get("expires_in")
                self.user_name = data.get("user_name")
        except Exception as e:
            logger.error("Failed to load Simkl tokens from %s: %s", self.tokens_file, e)

    def save_tokens(self, token_data: dict[str, Any]) -> None:
        """Atomically persist token data to disk."""
        try:
            self.tokens_file.parent.mkdir(parents=True, exist_ok=True)
            self.access_token = token_data.get("access_token")
            if "refresh_token" in token_data:
                self.refresh_token = token_data.get("refresh_token")
            if "created_at" in token_data:
                self.created_at = token_data.get("created_at")
            if "expires_in" in token_data:
                self.expires_in = token_data.get("expires_in")
            if "user_name" in token_data:
                self.user_name = token_data.get("user_name")

            save_payload = {
                "access_token": self.access_token,
                "refresh_token": getattr(self, "refresh_token", None),
                "created_at": getattr(self, "created_at", None),
                "expires_in": getattr(self, "expires_in", None),
                "user_name": self.user_name,
            }
            with open(self.tokens_file, "w", encoding="utf-8") as f:
                json.dump(save_payload, f, indent=2)
            logger.info("Saved Simkl token for user: %s", self.user_name or "unknown")
        except Exception as e:
            logger.error("Failed to save Simkl tokens: %s", e)

    def delete_tokens(self) -> None:
        """Delete stored tokens from disk and memory."""
        self.access_token = None
        self.refresh_token = None
        self.created_at = None
        self.expires_in = None
        self.user_name = None
        if self.tokens_file.exists():
            try:
                self.tokens_file.unlink()
                logger.info("Deleted Simkl tokens file: %s", self.tokens_file)
            except Exception as e:
                logger.error("Failed to delete Simkl tokens file: %s", e)

    def get_access_token(self) -> Optional[str]:
        """Return the current access token if present."""
        return self.access_token

    def is_authenticated(self) -> bool:
        """Check if an access token is available."""
        return bool(self.access_token)

    def is_enabled(self) -> bool:
        """Check if Simkl tracking is configured and enabled."""
        try:
            from app.services.settings_manager import settings_mgr
            tracker_active = settings_mgr.is_tracker_enabled("simkl")
        except Exception:
            tracker_active = self.config.SIMKL_ENABLED
        return bool(self.effective_client_id) and tracker_active

    def _get_headers(self, auth: bool = True) -> dict[str, str]:
        """Construct HTTP headers required for Simkl API."""
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.effective_client_id,
        }
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def get_device_pin(self) -> dict[str, Any]:
        """Initiate OAuth Device PIN flow.
        
        Attempts modern OAuth 2.0 Device Flow (AUTH V2) first; falls back to
        legacy PIN flow (AUTH V1) if the client ID was registered on V1.

        Returns:
            dict containing user_code, device_code, verification_url, expires_in, interval.
        """
        cid = self.effective_client_id
        if not cid:
            return {"error": "SIMKL_CLIENT_ID not configured"}

        # 1. Try modern OAuth 2.0 Device Flow (AUTH V2)
        v2_url = f"{self.base_url}/oauth2/device"
        v2_data = {
            "client_id": cid,
            "scope": "media:read media:write",
        }
        try:
            resp = await self._client.post(
                v2_url,
                data=v2_data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Omniscrobble/2.5"},
            )
            if resp.status_code == 200:
                data = resp.json()
                user_code = data.get("user_code", "")
                verification_url = (
                    data.get("verification_uri_complete")
                    or data.get("verification_uri", "https://simkl.com/pin")
                )
                if verification_url == "https://simkl.com/pin" and user_code:
                    verification_url = f"https://simkl.com/pin?user_code={user_code}"
                return {
                    "auth_version": "v2",
                    "device_code": data.get("device_code"),
                    "user_code": user_code,
                    "verification_url": verification_url,
                    "expires_in": data.get("expires_in", 900),
                    "interval": data.get("interval", 5),
                }
            v2_status = resp.status_code
            logger.info("Simkl AUTH V2 device request returned %s; attempting legacy V1 fallback...", v2_status)
        except httpx.RequestError as e:
            logger.warning("Simkl AUTH V2 request failed (%s); trying V1...", e)

        # 2. Fall back to legacy AUTH V1 PIN flow
        v1_url = f"{self.base_url}/oauth/pin"
        params = {"client_id": cid}
        try:
            resp = await self._client.get(
                v1_url,
                params=params,
                headers={"Content-Type": "application/json", "User-Agent": "Omniscrobble/2.5"},
            )
            if resp.status_code == 200:
                data = resp.json()
                user_code = data.get("user_code", "")
                return {
                    "auth_version": "v1",
                    "device_code": "DEVICE_CODE",
                    "user_code": user_code,
                    "verification_url": data.get("verification_url", f"https://simkl.com/pin?code={user_code}"),
                    "expires_in": data.get("expires_in", 900),
                    "interval": data.get("interval", 5),
                }
            logger.warning("Simkl PIN request failed (%s): %s", resp.status_code, resp.text)
            detail = ""
            try:
                err_json = resp.json()
                detail = err_json.get("message") or err_json.get("error_description") or err_json.get("error") or resp.text
            except Exception:
                detail = resp.text
            return {"error": f"Simkl API error: {resp.status_code}", "detail": detail}
        except httpx.RequestError as e:
            logger.error("Network error during Simkl PIN request: %s", e)
            return {"error": "Network error", "detail": str(e)}

    async def poll_device_pin(self, user_code: str, device_code: Optional[str] = None) -> dict[str, Any]:
        """Poll the status of an issued device PIN (supports AUTH V2 and legacy V1).
        
        Returns:
            dict with 'status': 'success' | 'pending' | 'error'
        """
        cid = self.effective_client_id
        if not cid:
            return {"status": "error", "error": "SIMKL_CLIENT_ID not configured"}

        # AUTH V2 flow when real device_code is provided
        if device_code and device_code != "DEVICE_CODE":
            token_url = f"{self.base_url}/oauth2/token"
            data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": cid,
                "device_code": device_code,
            }
            c_secret = self.effective_client_secret
            if c_secret:
                data["client_secret"] = c_secret
            try:
                resp = await self._client.post(
                    token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Omniscrobble/2.5"},
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                    self.access_token = token_data.get("access_token")
                    self.refresh_token = token_data.get("refresh_token")
                    import time
                    self.created_at = int(time.time())
                    self.expires_in = token_data.get("expires_in", 604800)

                    profile = await self.get_user_profile()
                    user_name = profile.get("user", {}).get("name") if isinstance(profile.get("user"), dict) else None
                    if user_name:
                        self.user_name = user_name

                    self.save_tokens({
                        "access_token": self.access_token,
                        "refresh_token": self.refresh_token,
                        "created_at": self.created_at,
                        "expires_in": self.expires_in,
                        "user_name": self.user_name,
                    })
                    return {
                        "status": "success",
                        "result": "OK",
                        "access_token": self.access_token,
                        "user_name": self.user_name,
                    }
                elif resp.status_code == 400:
                    try:
                        err_json = resp.json()
                    except Exception:
                        err_json = {}
                    err = err_json.get("error", "")
                    if err in ("authorization_pending", "slow_down"):
                        return {"status": "pending", "message": err}
                    elif err == "expired_token":
                        return {"status": "error", "error": "Activation code expired", "message": "The PIN has expired. Please generate a new code."}
                    return {"status": "error", "error": err or "Bad Request", "detail": err_json.get("error_description", resp.text)}
                elif resp.status_code == 401:
                    try:
                        err_json = resp.json()
                    except Exception:
                        err_json = {}
                    desc = err_json.get("error_description") or err_json.get("message") or ""
                    if not self.effective_client_secret:
                        err_msg = "Invalid client credentials. If your Simkl app was registered as 'Server apps & services', please configure your Client Secret in Settings Hub."
                    else:
                        err_msg = "Invalid client credentials. Please check your Simkl Client ID and Client Secret in Settings Hub."
                    if desc and desc not in err_msg:
                        err_msg += f" ({desc})"
                    return {"status": "error", "error": err_msg, "detail": desc or resp.text}
                return {"status": "error", "error": f"HTTP {resp.status_code}", "detail": resp.text}
            except httpx.RequestError as e:
                return {"status": "error", "error": "Network error", "detail": str(e)}

        # Legacy AUTH V1 polling fallback
        url = f"{self.base_url}/oauth/pin/{user_code}"
        params = {"client_id": cid}
        try:
            resp = await self._client.get(url, params=params, headers={"Content-Type": "application/json", "User-Agent": "Omniscrobble/2.5"})
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result")
                if result == "OK" and "access_token" in data:
                    self.access_token = data["access_token"]
                    # Fetch user settings to capture username
                    profile = await self.get_user_profile()
                    user_name = profile.get("user", {}).get("name") if isinstance(profile.get("user"), dict) else None
                    if user_name:
                        self.user_name = user_name

                    self.save_tokens({
                        "access_token": self.access_token,
                        "user_name": self.user_name,
                    })
                    return {
                        "status": "success",
                        "result": "OK",
                        "access_token": self.access_token,
                        "user_name": self.user_name,
                    }
                elif result == "KO" or data.get("message") == "authorization_pending":
                    return {"status": "pending", "message": "authorization_pending"}
                else:
                    return {"status": "pending", "message": data.get("message", "pending")}
            elif resp.status_code == 400:
                # 400 with authorization_pending is standard for Simkl PIN polling
                try:
                    data = resp.json()
                    if data.get("result") == "KO" or "pending" in data.get("message", "").lower():
                        return {"status": "pending", "message": data.get("message", "authorization_pending")}
                except Exception:
                    pass
                return {"status": "pending", "message": "authorization_pending"}
            return {"status": "error", "error": f"HTTP {resp.status_code}", "detail": resp.text}
        except httpx.RequestError as e:
            return {"status": "error", "error": "Network error", "detail": str(e)}

    async def refresh_access_token(self) -> bool:
        """Refresh expired AUTH V2 access token if refresh_token is present."""
        if not getattr(self, "refresh_token", None):
            return False
        cid = self.effective_client_id
        if not cid:
            return False

        token_url = f"{self.base_url}/oauth2/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": cid,
            "refresh_token": self.refresh_token,
        }
        c_secret = self.effective_client_secret
        if c_secret:
            data["client_secret"] = c_secret
        try:
            resp = await self._client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Omniscrobble/2.5"},
            )
            if resp.status_code == 200:
                token_data = resp.json()
                self.access_token = token_data.get("access_token")
                if token_data.get("refresh_token"):
                    self.refresh_token = token_data.get("refresh_token")
                import time
                self.created_at = int(time.time())
                self.expires_in = token_data.get("expires_in", 604800)
                self.save_tokens({
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "created_at": self.created_at,
                    "expires_in": self.expires_in,
                    "user_name": self.user_name,
                })
                logger.info("Successfully refreshed Simkl access token")
                return True
            logger.warning("Simkl token refresh failed (%s): %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("Error refreshing Simkl access token: %s", e)
            return False

    async def get_user_profile(self) -> dict[str, Any]:
        """Fetch authenticated user profile settings."""
        if not self.is_authenticated():
            return {"error": "Not authenticated"}

        url = f"{self.base_url}/users/settings"
        try:
            resp = await self._client.get(url, headers=self._get_headers(auth=True))
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        except httpx.RequestError as e:
            return {"error": str(e)}

    async def check_connection(self) -> dict[str, Any]:
        """Check live connection status and token validity."""
        configured = bool(self.effective_client_id)
        if not configured:
            return {
                "configured": False,
                "enabled": False,
                "authenticated": False,
                "user": None,
                "status": "unconfigured",
            }

        if not self.is_authenticated():
            return {
                "configured": True,
                "enabled": self.is_enabled(),
                "authenticated": False,
                "user": None,
                "status": "not_authenticated",
            }

        profile = await self.get_user_profile()
        if "error" not in profile:
            user_data = profile.get("user", {})
            name = user_data.get("name") if isinstance(user_data, dict) else self.user_name
            if name:
                self.user_name = name
            return {
                "configured": True,
                "enabled": self.config.SIMKL_ENABLED,
                "authenticated": True,
                "user": self.user_name or name,
                "status": "connected",
            }
        return {
            "configured": True,
            "enabled": self.config.SIMKL_ENABLED,
            "authenticated": False,
            "user": self.user_name,
            "status": "expired",
            "error": profile.get("error"),
        }

    def build_media_payload(self, media: ParsedMedia, progress: Optional[float] = None) -> dict[str, Any]:
        """Convert a ParsedMedia object into a Simkl-compliant scrobble payload."""
        ids: dict[str, str] = {}
        if getattr(media, "ids", None):
            for k, v in media.ids.items():
                if v:
                    ids[str(k).lower()] = str(v)
        if getattr(media, "imdb_id", None) and "imdb" not in ids:
            ids["imdb"] = str(media.imdb_id)
        if getattr(media, "tmdb_id", None) and "tmdb" not in ids:
            ids["tmdb"] = str(media.tmdb_id)
        if getattr(media, "tvdb_id", None) and "tvdb" not in ids:
            ids["tvdb"] = str(media.tvdb_id)

        payload: dict[str, Any] = {}
        if media.media_type == "movie":
            movie_data: dict[str, Any] = {
                "title": media.title,
                "ids": ids,
            }
            if media.year:
                movie_data["year"] = media.year
            payload["movie"] = movie_data
        else:
            show_title = getattr(media, "show_title", None) or getattr(media, "grandparent_title", None) or media.title
            show_year = getattr(media, "show_year", None) or getattr(media, "year", None)
            show_data: dict[str, Any] = {
                "title": show_title,
                "ids": ids,
            }
            if show_year:
                show_data["year"] = show_year

            episode_data: dict[str, Any] = {}
            season_num = getattr(media, "season", None)
            if season_num is None:
                season_num = getattr(media, "parent_index", None)
            ep_num = getattr(media, "episode", None)
            if ep_num is None:
                ep_num = getattr(media, "index", None)

            if season_num is not None:
                episode_data["season"] = season_num
            if ep_num is not None:
                episode_data["number"] = ep_num
            if media.title and show_title and media.title != show_title:
                episode_data["title"] = media.title

            payload["show"] = show_data
            payload["episode"] = episode_data

        if progress is not None:
            payload["progress"] = max(0.0, min(100.0, round(float(progress), 1)))

        return payload

    async def scrobble_start(self, media: ParsedMedia, progress: float = 0.0) -> dict[str, Any]:
        """Send a playback start event to Simkl."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        payload = self.build_media_payload(media, progress=progress)
        return await self._post("/scrobble/start", payload)

    async def scrobble_pause(self, media: ParsedMedia, progress: float = 0.0) -> dict[str, Any]:
        """Send a playback pause event to Simkl."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        payload = self.build_media_payload(media, progress=progress)
        return await self._post("/scrobble/pause", payload)

    async def scrobble_stop(self, media: ParsedMedia, progress: float = 100.0) -> dict[str, Any]:
        """Send a playback stop / finish scrobble event to Simkl."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        payload = self.build_media_payload(media, progress=progress)
        return await self._post("/scrobble/stop", payload)

    async def sync_history(self, media: ParsedMedia) -> dict[str, Any]:
        """Sync a completed watched media item directly to Simkl history."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        ids: dict[str, str] = {}
        if getattr(media, "ids", None):
            for k, v in media.ids.items():
                if v:
                    ids[str(k).lower()] = str(v)
        if getattr(media, "imdb_id", None) and "imdb" not in ids:
            ids["imdb"] = str(media.imdb_id)
        if getattr(media, "tmdb_id", None) and "tmdb" not in ids:
            ids["tmdb"] = str(media.tmdb_id)
        if getattr(media, "tvdb_id", None) and "tvdb" not in ids:
            ids["tvdb"] = str(media.tvdb_id)

        if media.media_type == "movie":
            item = {"title": media.title, "ids": ids}
            if media.year:
                item["year"] = media.year
            payload = {"movies": [item]}
        else:
            show_title = getattr(media, "show_title", None) or getattr(media, "grandparent_title", None) or media.title
            show_year = getattr(media, "show_year", None) or getattr(media, "year", None)
            ep_num = getattr(media, "episode", None)
            if ep_num is None:
                ep_num = getattr(media, "index", None) or 1
            season_num = getattr(media, "season", None)
            if season_num is None:
                season_num = getattr(media, "parent_index", None) or 1

            ep_item = {"number": ep_num}
            season_item = {"number": season_num, "episodes": [ep_item]}
            show_item = {"title": show_title, "ids": ids, "seasons": [season_item]}
            if show_year:
                show_item["year"] = show_year
            payload = {"shows": [show_item]}

        return await self._post("/sync/history", payload)

    async def sync_ratings(self, media: ParsedMedia, rating: int) -> dict[str, Any]:
        """Sync a user rating (1-10) to Simkl."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}

        ids: dict[str, str] = {}
        if getattr(media, "ids", None):
            for k, v in media.ids.items():
                if v:
                    ids[str(k).lower()] = str(v)
        if getattr(media, "imdb_id", None) and "imdb" not in ids:
            ids["imdb"] = str(media.imdb_id)
        if getattr(media, "tmdb_id", None) and "tmdb" not in ids:
            ids["tmdb"] = str(media.tmdb_id)
        if getattr(media, "tvdb_id", None) and "tvdb" not in ids:
            ids["tvdb"] = str(media.tvdb_id)

        score = max(1, min(10, int(round(rating))))
        if media.media_type == "movie":
            item = {"title": media.title, "ids": ids, "rating": score}
            if media.year:
                item["year"] = media.year
            payload = {"movies": [item]}
        else:
            show_title = getattr(media, "show_title", None) or getattr(media, "grandparent_title", None) or media.title
            show_year = getattr(media, "show_year", None) or getattr(media, "year", None)
            item = {"title": show_title, "ids": ids, "rating": score}
            if show_year:
                item["year"] = show_year
            payload = {"shows": [item]}

        return await self._post("/sync/ratings", payload)

    async def get_all_items(
        self, media_type: str = "movies", date_from: Optional[str] = None
    ) -> dict[str, Any]:
        """Fetch user's library items (completed, watching, plan_to_watch).

        Args:
            media_type: 'movies', 'shows', or 'anime'
            date_from: Optional ISO timestamp or date for incremental changes
        """
        if not self.is_authenticated():
            return {"error": "Not authenticated"}

        endpoint = f"/sync/all-items/{media_type}"
        params: dict[str, Any] = {"extended": "full"}
        if date_from:
            params["date_from"] = date_from

        res = await self._get(endpoint, params=params)
        return res if isinstance(res, dict) else {}

    async def get_activities(self) -> dict[str, Any]:
        """Fetch last activity timestamps for user's library."""
        if not self.is_authenticated():
            return {"error": "Not authenticated"}
        res = await self._get("/sync/activities")
        return res if isinstance(res, dict) else {}

    async def bulk_sync_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bulk add movies/shows/episodes to Simkl watched history."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        return await self._post("/sync/history", payload)

    async def remove_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /sync/history/remove - Remove movies/shows/episodes from Simkl watched history."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        return await self._post("/sync/history/remove", payload)

    async def bulk_sync_ratings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bulk add movie/show ratings to Simkl."""
        if not self.is_authenticated():
            return {"status": "skipped", "reason": "not_authenticated"}
        return await self._post("/sync/ratings", payload)

    async def _get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Internal helper to execute authorized GET requests."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(auth=True)
        try:
            resp = await self._client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return {}
            elif resp.status_code == 401:
                logger.warning("Simkl authentication rejected (401 Unauthorized)")
                if getattr(self, "refresh_token", None):
                    refreshed = await self.refresh_access_token()
                    if refreshed:
                        retry_headers = self._get_headers(auth=True)
                        retry_resp = await self._client.get(url, params=params, headers=retry_headers)
                        if retry_resp.status_code == 200:
                            try:
                                return retry_resp.json()
                            except Exception:
                                return {}
                return {"error": "unauthorized", "code": 401}
            elif resp.status_code == 429:
                logger.warning("Simkl rate limit reached (429 Too Many Requests)")
                return {"error": "rate_limited", "code": 429}
            else:
                logger.warning("Simkl API error (%s) on %s: %s", resp.status_code, endpoint, resp.text)
                return {"error": f"HTTP {resp.status_code}", "code": resp.status_code, "detail": resp.text}
        except httpx.RequestError as e:
            logger.error("Network error communicating with Simkl on %s: %s", endpoint, e)
            return {"error": "network_error", "detail": str(e)}

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Internal helper to execute authorized POST requests."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(auth=True)
        try:
            resp = await self._client.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                try:
                    return {"status": "success", "data": resp.json()}
                except Exception:
                    return {"status": "success", "data": resp.text}
            elif resp.status_code == 401:
                logger.warning("Simkl authentication rejected (401 Unauthorized)")
                if getattr(self, "refresh_token", None):
                    refreshed = await self.refresh_access_token()
                    if refreshed:
                        retry_headers = self._get_headers(auth=True)
                        retry_resp = await self._client.post(url, json=payload, headers=retry_headers)
                        if retry_resp.status_code in (200, 201):
                            try:
                                return {"status": "success", "data": retry_resp.json()}
                            except Exception:
                                return {"status": "success", "data": retry_resp.text}
                return {"status": "error", "error": "unauthorized", "code": 401}
            elif resp.status_code == 429:
                logger.warning("Simkl rate limit reached (429 Too Many Requests)")
                return {"status": "error", "error": "rate_limited", "code": 429}
            else:
                logger.warning("Simkl API error (%s) on %s: %s", resp.status_code, endpoint, resp.text)
                return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except httpx.RequestError as e:
            logger.error("Network error communicating with Simkl on %s: %s", endpoint, e)
            return {"status": "error", "error": "network_error", "detail": str(e)}

    async def close(self) -> None:
        """Close the underlying HTTP client if owned."""
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()
