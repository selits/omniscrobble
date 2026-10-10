"""Sonarr/Radarr acquisition handlers and route registration."""

import asyncio
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class ArrTestConnectionRequest(BaseModel):
    app: Optional[str] = "sonarr"
    url: Optional[str] = None
    api_key: Optional[str] = None


class ArrAddRequest(BaseModel):
    type: str
    item_data: dict[str, Any]
    root_folder_path: Optional[str] = None
    quality_profile_id: Optional[int] = None
    monitored: bool = True
    monitor_option: str = "all"
    search_now: bool = True
    enable_cowatch: bool = False

    model_config = {"extra": "ignore"}


class AcquisitionHandlers:
    """Acquisition endpoint behavior with application dependencies supplied explicitly."""

    def __init__(
        self, *, config: Any, arr_bridge: Any, settings_mgr: Any, cowatch_mgr: Any,
        is_admin_request: Callable[[Request], bool], watch_list_actor: Callable[[Request], Any],
        sonarr_client: Any, radarr_client: Any, overseerr_client: Any,
        ecosystem_clients: Mapping[str, Any], logger: Any,
    ) -> None:
        self.config = config
        self.arr_bridge = arr_bridge
        self.settings_mgr = settings_mgr
        self.cowatch_mgr = cowatch_mgr
        self.is_admin_request = is_admin_request
        self.watch_list_actor = watch_list_actor
        self.sonarr_client = sonarr_client
        self.radarr_client = radarr_client
        self.overseerr_client = overseerr_client
        self.ecosystem_clients = ecosystem_clients
        self.logger = logger
        self._config_cache: dict[str, Any] = {"expires_at": 0.0, "data": None}

    def invalidate_config_cache(self) -> None:
        self._config_cache.update({"expires_at": 0.0, "data": None})

    async def lookup_media(self, request: Request, type: str, term: str):
        """Search the Sonarr/Radarr catalog and flag items already in the library."""
        if request.query_params.get("demo") == "true":
            return {"results": []}
        self.watch_list_actor(request)
        kind, search = type.strip().lower(), term.strip()[:120]
        if kind not in {"series", "movie"}:
            raise HTTPException(status_code=400, detail="type must be 'series' or 'movie'")
        if not search:
            return {"results": []}
        client = self.arr_bridge.sonarr if kind == "series" else self.arr_bridge.radarr
        if not client.is_configured:
            return {"results": [], "configured": False}
        candidates = await self.arr_bridge.lookup_media(kind, search)
        results = []
        for item in candidates[:20]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            if kind == "series":
                exists = await self.arr_bridge.sonarr.has_series(tvdb_id=item.get("tvdbId"), imdb_id=item.get("imdbId"), title=title)
                external_id, network = item.get("tvdbId"), item.get("network") or ""
            else:
                exists = await self.arr_bridge.radarr.has_movie(tmdb_id=item.get("tmdbId"), imdb_id=item.get("imdbId"), title=title)
                external_id, network = item.get("tmdbId"), item.get("studio") or ""
            images = item.get("images") or []
            poster = next((img.get("remoteUrl") or img.get("url") for img in images if img.get("coverType") == "poster"), None)
            results.append({"title": title, "year": item.get("year"), "overview": item.get("overview", ""),
                            "network": network, "poster_url": poster, "in_library": exists,
                            "payload": item, "external_id": external_id})
        return {"results": results, "configured": True}

    async def get_config(self, request: Request):
        """Return configured root folders and quality profiles for both services."""
        if request.query_params.get("demo") == "true":
            return {"sonarr": {"configured": True, "root_folders": [], "quality_profiles": []},
                    "radarr": {"configured": True, "root_folders": [], "quality_profiles": []}}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        cached = self._config_cache.get("data")
        if cached is not None and time.monotonic() < self._config_cache["expires_at"]:
            return cached
        arr_settings = self.settings_mgr.get_arr_settings(mask=False)

        async def details(client, root_default, profile_default):
            if not client.is_configured:
                return {"configured": False, "root_folders": [], "quality_profiles": [], "default_root": root_default, "default_profile": profile_default}
            folders, profiles = await asyncio.gather(client.get_root_folders(), client.get_quality_profiles())
            return {"configured": True, "root_folders": folders, "quality_profiles": profiles,
                    "default_root": root_default, "default_profile": profile_default}

        config = {
            "sonarr": await details(self.arr_bridge.sonarr, arr_settings.get("sonarr_root_folder"), arr_settings.get("sonarr_quality_profile_id")),
            "radarr": await details(self.arr_bridge.radarr, arr_settings.get("radarr_root_folder"), arr_settings.get("radarr_quality_profile_id")),
            "co_watch_user": self.config.CO_WATCH_USER or None,
        }
        self._config_cache.update({"expires_at": time.monotonic() + 300.0, "data": config})
        return config

    async def add_media(self, payload: ArrAddRequest, request: Request):
        """Add a selected catalog item and optionally enroll a show for Co-Watch."""
        if request.query_params.get("demo") == "true":
            return {"status": "ok", "success": True, "cowatch_enrolled": False}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        csrf_cookie, csrf_header = request.cookies.get("csrf_token", ""), request.headers.get("x-csrf-token", "")
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
        kind = payload.type.strip().lower()
        if kind not in {"series", "movie"}:
            raise HTTPException(status_code=400, detail="type must be 'series' or 'movie'")
        if kind == "series" and payload.enable_cowatch and not self.config.CO_WATCH_USER:
            raise HTTPException(status_code=400, detail="Co-Watch enrollment requires a configured partner account")
        item = payload.item_data
        title = str(item.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Selected item is missing a title")
        result = await self.arr_bridge.acquire_media(kind, item, root_folder_path=payload.root_folder_path,
            quality_profile_id=payload.quality_profile_id, monitored=payload.monitored,
            search_now=payload.search_now, monitor_option=payload.monitor_option)
        if not result.get("success"):
            self.logger.warning("Interactive *Arr addition failed for %s item (service=%s)", kind, "Sonarr" if kind == "series" else "Radarr")
            raise HTTPException(status_code=400, detail="The item could not be added. Check the *Arr service connection and configuration.")
        enrolled = False
        if kind == "series" and payload.enable_cowatch:
            self.cowatch_mgr.add_show(title)
            enrolled = True
        self.logger.info("Interactive media acquisition completed (type=%s, service=%s, co-watch=%s)", kind, "Sonarr" if kind == "series" else "Radarr", enrolled)
        return {"status": "ok", "success": True, "title": title,
                "service": "Sonarr" if kind == "series" else "Radarr", "cowatch_enrolled": enrolled,
                "item_id": (result.get("data") or {}).get("id")}

    async def test_connection(self, payload: ArrTestConnectionRequest, request: Request):
        """Test Sonarr, Radarr, or Overseerr/Jellyseerr using supplied or saved settings."""
        app_type = (payload.app or "sonarr").lower().strip()
        if request.query_params.get("demo") == "true":
            ver = "4.0.9" if app_type == "sonarr" else ("5.9.1" if app_type == "radarr" else "1.33.2")
            return {"status": "connected", "app": app_type, "version": ver}
        if not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        arr_cfg = self.settings_mgr.get_arr_settings(mask=False)
        if app_type == "sonarr":
            target_url = (payload.url or "").strip() or arr_cfg.get("sonarr_url", "")
            target_key = (payload.api_key or "").strip()
            if not target_key or self.settings_mgr._is_masked(target_key): target_key = arr_cfg.get("sonarr_api_key", "")
            client = self.sonarr_client(base_url=target_url, api_key=target_key)
        elif app_type == "radarr":
            target_url = (payload.url or "").strip() or arr_cfg.get("radarr_url", "")
            target_key = (payload.api_key or "").strip()
            if not target_key or self.settings_mgr._is_masked(target_key): target_key = arr_cfg.get("radarr_api_key", "")
            client = self.radarr_client(base_url=target_url, api_key=target_key)
        elif app_type in ("overseerr", "jellyseerr"):
            target_url = (payload.url or "").strip() or arr_cfg.get("overseerr_url", "")
            target_key = (payload.api_key or "").strip()
            if not target_key or self.settings_mgr._is_masked(target_key): target_key = arr_cfg.get("overseerr_api_key", "")
            client = self.overseerr_client(base_url=target_url, api_key=target_key)
        else:
            raise HTTPException(status_code=400, detail=f"Invalid app '{payload.app}'")
        if target_url and not target_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Server URL must start with http:// or https://")
        try:
            return await client.check_connection()
        finally:
            await client.close()

    async def get_status(self, request: Request):
        """Return Sonarr/Radarr and Trakt Watchlist automation status."""
        return await self.arr_bridge.get_status(demo=request.query_params.get("demo") == "true")

    async def trigger_sync(self, request: Request):
        """Run a Trakt Watchlist synchronization into Sonarr and Radarr."""
        demo = request.query_params.get("demo") == "true"
        if not demo and not self.is_admin_request(request):
            raise HTTPException(status_code=401, detail="Unauthorized: Admin access required")
        return await self.arr_bridge.sync_watchlist(demo=demo)

    async def get_ecosystem_status(self, request: Request):
        """Return current media server and tracker ecosystem status."""
        return await self.arr_bridge.get_ecosystem_status(
            demo=request.query_params.get("demo") == "true",
            **self.ecosystem_clients,
        )


_ROUTES = (
    ("/api/arr/lookup", "lookup_arr_media", ["GET"]),
    ("/api/arr/config", "get_arr_acquisition_config", ["GET"]),
    ("/api/arr/add", "add_arr_media", ["POST"]),
    ("/api/arr/test-connection", "test_arr_connection", ["POST"]),
    ("/api/arr/status", "get_arr_status", ["GET"]),
    ("/api/arr/sync", "trigger_arr_sync", ["POST"]),
    ("/api/ecosystem", "get_ecosystem_status", ["GET"]),
)


def create_router(handlers: Mapping[str, Any]):
    router = APIRouter(tags=["Acquisition"])
    for path, name, methods in _ROUTES:
        if name not in handlers:
            raise ValueError(f"Missing acquisition route handler: {name}")
        router.add_api_route(path, handlers[name], methods=methods, name=name)
    return router
