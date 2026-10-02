"""Content Bridge and *Arr Acquisition Automation Engine for Omniscrobble.

Automatically monitors Trakt Watchlists, searches for missing movies and TV shows,
and adds them to Radarr and Sonarr with automatic quality profile and root folder assignment.
Also provides unified multi-server ecosystem observability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.plex_api_client import PlexApiClient
from app.clients.radarr_client import RadarrClient
from app.clients.simkl_client import SimklClient
from app.clients.sonarr_client import SonarrClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.plex_parser import ParsedMedia
from app.services.notifier import Notifier, notifier
from app.services.settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.arr_bridge")


class ArrBridgeManager:
    """Manages Trakt Watchlist -> Sonarr & Radarr automation and multi-server status."""

    def __init__(
        self,
        sonarr_client: Optional[SonarrClient] = None,
        radarr_client: Optional[RadarrClient] = None,
        trakt_client: Optional[TraktClient] = None,
        notifier_service: Optional[Notifier] = None,
    ):
        try:
            arr_cfg = settings_mgr.get_arr_settings(mask=False)
            s_url = arr_cfg.get("sonarr_url") or Config.SONARR_URL
            s_key = arr_cfg.get("sonarr_api_key") or Config.SONARR_API_KEY
            r_url = arr_cfg.get("radarr_url") or Config.RADARR_URL
            r_key = arr_cfg.get("radarr_api_key") or Config.RADARR_API_KEY
        except Exception:
            s_url = Config.SONARR_URL
            s_key = Config.SONARR_API_KEY
            r_url = Config.RADARR_URL
            r_key = Config.RADARR_API_KEY

        self.sonarr = sonarr_client or SonarrClient(base_url=s_url, api_key=s_key)
        self.radarr = radarr_client or RadarrClient(base_url=r_url, api_key=r_key)
        self.trakt = trakt_client
        self.notifier = notifier_service or notifier

        self._lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._last_sync_time: Optional[float] = None
        self._last_sync_result: Optional[dict[str, Any]] = None

    def update_config(
        self,
        sonarr_url: Optional[str] = None,
        sonarr_api_key: Optional[str] = None,
        radarr_url: Optional[str] = None,
        radarr_api_key: Optional[str] = None,
    ) -> None:
        """Update Sonarr and Radarr connection parameters dynamically in-memory."""
        if sonarr_url is not None:
            self.sonarr.base_url = sonarr_url.rstrip("/")
        if sonarr_api_key is not None:
            self.sonarr.api_key = sonarr_api_key
        if radarr_url is not None:
            self.radarr.base_url = radarr_url.rstrip("/")
        if radarr_api_key is not None:
            self.radarr.api_key = radarr_api_key

    def set_trakt_client(self, client: TraktClient) -> None:
        self.trakt = client

    def get_trakt(self) -> TraktClient:
        if self.trakt is None:
            from app.main import trakt
            self.trakt = trakt
        return self.trakt

    async def get_status(self, demo: bool = False) -> dict[str, Any]:
        """Return status for Sonarr, Radarr, and watchlist automation."""
        if demo:
            return {
                "configured": True,
                "sonarr_configured": True,
                "sonarr_connected": True,
                "sonarr_version": "4.0.9",
                "sonarr_series_count": 48,
                "radarr_configured": True,
                "radarr_connected": True,
                "radarr_version": "5.9.1",
                "radarr_movies_count": 215,
                "auto_add_enabled": Config.AUTO_ADD_FROM_WATCHLIST or True,
                "search_on_add": Config.SEARCH_ON_ADD,
                "interval_seconds": Config.ARR_WATCHLIST_INTERVAL or 1800,
                "last_sync_time": self._last_sync_time or (time.time() - 900),
                "is_syncing": False,
                "last_result": self._last_sync_result or {
                    "added": {"movies": 1, "shows": 1},
                    "skipped": {"movies": 3, "shows": 2},
                    "items": [
                        {"title": "Dune: Part Two", "year": 2024, "type": "movie", "status": "added", "app": "Radarr"},
                        {"title": "Severance", "year": 2022, "type": "show", "status": "skipped", "reason": "Already in Sonarr", "app": "Sonarr"},
                    ],
                },
            }

        sonarr_cfg = self.sonarr.is_configured
        sonarr_conn = False
        sonarr_ver = "unknown"
        sonarr_count = 0
        if sonarr_cfg:
            s_stat = await self.sonarr.check_connection()
            sonarr_conn = s_stat.get("status") == "connected"
            sonarr_ver = s_stat.get("version", "unknown")
            if sonarr_conn:
                s_list = await self.sonarr.get_series()
                sonarr_count = len(s_list)

        radarr_cfg = self.radarr.is_configured
        radarr_conn = False
        radarr_ver = "unknown"
        radarr_count = 0
        if radarr_cfg:
            r_stat = await self.radarr.check_connection()
            radarr_conn = r_stat.get("status") == "connected"
            radarr_ver = r_stat.get("version", "unknown")
            if radarr_conn:
                r_list = await self.radarr.get_movies()
                radarr_count = len(r_list)

        return {
            "configured": sonarr_cfg or radarr_cfg,
            "sonarr_configured": sonarr_cfg,
            "sonarr_connected": sonarr_conn,
            "sonarr_version": sonarr_ver,
            "sonarr_series_count": sonarr_count,
            "radarr_configured": radarr_cfg,
            "radarr_connected": radarr_conn,
            "radarr_version": radarr_ver,
            "radarr_movies_count": radarr_count,
            "auto_add_enabled": Config.AUTO_ADD_FROM_WATCHLIST,
            "search_on_add": Config.SEARCH_ON_ADD,
            "interval_seconds": Config.ARR_WATCHLIST_INTERVAL,
            "last_sync_time": self._last_sync_time,
            "is_syncing": self._is_syncing,
            "last_result": self._last_sync_result,
        }

    async def get_ecosystem_status(
        self,
        demo: bool = False,
        plex_client: Optional[PlexApiClient] = None,
        jellyfin_client: Optional[Any] = None,
        emby_client: Optional[Any] = None,
        simkl_client: Optional[SimklClient] = None,
        anilist_client: Optional[AniListClient] = None,
        mal_client: Optional[MyAnimeListClient] = None,
    ) -> dict[str, Any]:
        """Return unified ecosystem health matrix for all media and tracker integrations."""
        if demo:
            servers = [
                {
                    "id": "plex",
                    "name": "Plex Media Server",
                    "category": "Media Server",
                    "status": "connected",
                    "badge": "Online",
                    "version": "1.40.5",
                    "details": "Local Server (Port 32400)",
                    "icon": "plex",
                },
                {
                    "id": "jellyfin",
                    "name": "Jellyfin",
                    "category": "Media Server",
                    "status": "available",
                    "badge": "Ready",
                    "version": "10.9.11",
                    "details": "Webhook Ingestion Active",
                    "icon": "jellyfin",
                },
                {
                    "id": "emby",
                    "name": "Emby Server",
                    "category": "Media Server",
                    "status": "available",
                    "badge": "Ready",
                    "version": "4.8.8",
                    "details": "Webhook Ingestion Active",
                    "icon": "emby",
                },
                {
                    "id": "sonarr",
                    "name": "Sonarr",
                    "category": "Acquisition",
                    "status": "connected",
                    "badge": "Online",
                    "version": "4.0.9",
                    "details": "48 Series Monitored",
                    "icon": "sonarr",
                },
                {
                    "id": "radarr",
                    "name": "Radarr",
                    "category": "Acquisition",
                    "status": "connected",
                    "badge": "Online",
                    "version": "5.9.1",
                    "details": "215 Movies Monitored",
                    "icon": "radarr",
                },
                {
                    "id": "trakt",
                    "name": "Trakt.tv",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Authenticated",
                    "version": "API v2",
                    "details": "Connected as @demo_viewer (84 days left)",
                    "icon": "trakt",
                },
                {
                    "id": "simkl",
                    "name": "Simkl",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "API v2",
                    "details": "Connected as @demo_viewer (Dual-Scrobbler)",
                    "icon": "simkl",
                },
                {
                    "id": "anilist",
                    "name": "AniList",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "GraphQL",
                    "details": "Connected as @demo_otaku (Anime Scrobbler)",
                    "icon": "anilist",
                },
                {
                    "id": "myanimelist",
                    "name": "MyAnimeList",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "API v2",
                    "details": "Connected as @demo_otaku (Anime Scrobbler)",
                    "icon": "myanimelist",
                },
            ]
            for s in servers:
                s["enabled"] = True

            service_order = ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]
            servers.sort(
                key=lambda s: (
                    1 if not s.get("enabled", True) or s.get("status") == "disabled" or s.get("badge") in ("Disabled", "Paused") else 0,
                    service_order.index(s["id"]) if s.get("id") in service_order else 99,
                )
            )

            healthy = sum(1 for s in servers if s["status"] in ("connected", "available"))
            return {
                "servers": servers,
                "healthy_count": healthy,
                "total_count": len(servers),
            }

        # Live Ecosystem check
        servers = []

        # 1. Plex
        plex = plex_client or PlexApiClient()
        if plex.is_configured():
            p_conn = await plex.check_connection()
            if p_conn.get("status") == "connected":
                servers.append({
                    "id": "plex",
                    "name": "Plex Media Server",
                    "category": "Media Server",
                    "status": "connected",
                    "badge": "Online",
                    "version": p_conn.get("version", "Active"),
                    "details": f"Server: {p_conn.get('friendly_name', 'Plex')}",
                    "icon": "plex",
                })
            else:
                servers.append({
                    "id": "plex",
                    "name": "Plex Media Server",
                    "category": "Media Server",
                    "status": "error",
                    "badge": "Offline",
                    "version": "Configured",
                    "details": p_conn.get("message", "Connection failed"),
                    "icon": "plex",
                })
        else:
            servers.append({
                "id": "plex",
                "name": "Plex Webhook",
                "category": "Media Server",
                "status": "available",
                "badge": "Listener Active",
                "version": "Endpoint Ready",
                "details": "/webhook (Direct API not configured)",
                "icon": "plex",
            })

        # 2. Jellyfin
        if jellyfin_client and jellyfin_client.is_configured():
            jf_conn = await jellyfin_client.check_connection()
            if jf_conn.get("status") == "connected":
                servers.append({
                    "id": "jellyfin",
                    "name": "Jellyfin",
                    "category": "Media Server",
                    "status": "connected",
                    "badge": "Online",
                    "version": jf_conn.get("version", "Active"),
                    "details": f"Server: {jf_conn.get('server_name', 'Jellyfin')}",
                    "icon": "jellyfin",
                })
            else:
                servers.append({
                    "id": "jellyfin",
                    "name": "Jellyfin",
                    "category": "Media Server",
                    "status": "error",
                    "badge": "Offline",
                    "version": "Configured",
                    "details": jf_conn.get("message", "Connection failed"),
                    "icon": "jellyfin",
                })
        else:
            servers.append({
                "id": "jellyfin",
                "name": "Jellyfin",
                "category": "Media Server",
                "status": "available",
                "badge": "Listener Active",
                "version": "Endpoint Ready",
                "details": "/webhook/jellyfin (Direct API not configured)",
                "icon": "jellyfin",
            })

        # 3. Emby
        if emby_client and emby_client.is_configured():
            emby_conn = await emby_client.check_connection()
            if emby_conn.get("status") == "connected":
                servers.append({
                    "id": "emby",
                    "name": "Emby Server",
                    "category": "Media Server",
                    "status": "connected",
                    "badge": "Online",
                    "version": emby_conn.get("version", "Active"),
                    "details": f"Server: {emby_conn.get('server_name', 'Emby')}",
                    "icon": "emby",
                })
            else:
                servers.append({
                    "id": "emby",
                    "name": "Emby Server",
                    "category": "Media Server",
                    "status": "error",
                    "badge": "Offline",
                    "version": "Configured",
                    "details": emby_conn.get("message", "Connection failed"),
                    "icon": "emby",
                })
        else:
            servers.append({
                "id": "emby",
                "name": "Emby Server",
                "category": "Media Server",
                "status": "available",
                "badge": "Listener Active",
                "version": "Endpoint Ready",
                "details": "/webhook/emby (Direct API not configured)",
                "icon": "emby",
            })

        # 4. Sonarr
        if self.sonarr.is_configured:
            s_conn = await self.sonarr.check_connection()
            if s_conn.get("status") == "connected":
                s_list = await self.sonarr.get_series()
                servers.append({
                    "id": "sonarr",
                    "name": "Sonarr",
                    "category": "Acquisition",
                    "status": "connected",
                    "badge": "Online",
                    "version": s_conn.get("version", "v4"),
                    "details": f"{len(s_list)} Series Monitored",
                    "icon": "sonarr",
                })
            else:
                servers.append({
                    "id": "sonarr",
                    "name": "Sonarr",
                    "category": "Acquisition",
                    "status": "error",
                    "badge": "Offline",
                    "version": "Configured",
                    "details": s_conn.get("message", "Connection failed"),
                    "icon": "sonarr",
                })
        else:
            servers.append({
                "id": "sonarr",
                "name": "Sonarr",
                "category": "Acquisition",
                "status": "unconfigured",
                "badge": "Disabled",
                "version": "N/A",
                "details": "Set SONARR_URL and SONARR_API_KEY in .env",
                "icon": "sonarr",
            })

        # 5. Radarr
        if self.radarr.is_configured:
            r_conn = await self.radarr.check_connection()
            if r_conn.get("status") == "connected":
                r_list = await self.radarr.get_movies()
                servers.append({
                    "id": "radarr",
                    "name": "Radarr",
                    "category": "Acquisition",
                    "status": "connected",
                    "badge": "Online",
                    "version": r_conn.get("version", "v5"),
                    "details": f"{len(r_list)} Movies Monitored",
                    "icon": "radarr",
                })
            else:
                servers.append({
                    "id": "radarr",
                    "name": "Radarr",
                    "category": "Acquisition",
                    "status": "error",
                    "badge": "Offline",
                    "version": "Configured",
                    "details": r_conn.get("message", "Connection failed"),
                    "icon": "radarr",
                })
        else:
            servers.append({
                "id": "radarr",
                "name": "Radarr",
                "category": "Acquisition",
                "status": "unconfigured",
                "badge": "Disabled",
                "version": "N/A",
                "details": "Set RADARR_URL and RADARR_API_KEY in .env",
                "icon": "radarr",
            })

        # 6. Trakt
        trakt = self.get_trakt()
        if trakt.is_authenticated():
            token_info = trakt.get_token_info()
            days = token_info.get("days_remaining", 0)
            servers.append({
                "id": "trakt",
                "name": "Trakt.tv",
                "category": "Tracker",
                "status": "connected",
                "badge": "Authenticated",
                "version": "API v2",
                "details": f"Token Healthy ({days}d remaining)" if token_info.get("healthy") else "Token Expired",
                "icon": "trakt",
            })
        else:
            servers.append({
                "id": "trakt",
                "name": "Trakt.tv",
                "category": "Tracker",
                "status": "unconfigured",
                "badge": "Not Authenticated",
                "version": "API v2",
                "details": "Requires authorization at /auth",
                "icon": "trakt",
            })

        # 7. Simkl Multi-Tracker
        if simkl_client:
            simkl_conn = await simkl_client.check_connection()
            if simkl_conn.get("authenticated") and simkl_conn.get("enabled"):
                servers.append({
                    "id": "simkl",
                    "name": "Simkl",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "API v2",
                    "details": f"Connected as @{simkl_conn.get('user') or 'user'}",
                    "icon": "simkl",
                })
            elif simkl_conn.get("configured"):
                servers.append({
                    "id": "simkl",
                    "name": "Simkl",
                    "category": "Tracker",
                    "status": "error" if simkl_conn.get("status") == "expired" else "unconfigured",
                    "badge": "Auth Required" if simkl_conn.get("status") == "not_authenticated" else "Expired",
                    "version": "API v2",
                    "details": "Requires authorization at /auth/simkl",
                    "icon": "simkl",
                })
            else:
                servers.append({
                    "id": "simkl",
                    "name": "Simkl",
                    "category": "Tracker",
                    "status": "unconfigured",
                    "badge": "Disabled",
                    "version": "API v2",
                    "details": "Set SIMKL_CLIENT_ID to enable multi-tracking",
                    "icon": "simkl",
                })

        # 8. AniList Tracker
        if anilist_client:
            ani_conn = await anilist_client.check_connection()
            if ani_conn.get("authenticated") and ani_conn.get("enabled"):
                servers.append({
                    "id": "anilist",
                    "name": "AniList",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "GraphQL",
                    "details": f"Connected as @{ani_conn.get('user') or 'user'}",
                    "icon": "anilist",
                })
            elif ani_conn.get("configured"):
                servers.append({
                    "id": "anilist",
                    "name": "AniList",
                    "category": "Tracker",
                    "status": "unconfigured",
                    "badge": "Token Required",
                    "version": "GraphQL",
                    "details": "Requires authorization at /auth/anilist",
                    "icon": "anilist",
                })
            else:
                servers.append({
                    "id": "anilist",
                    "name": "AniList",
                    "category": "Tracker",
                    "status": "unconfigured",
                    "badge": "Disabled",
                    "version": "GraphQL",
                    "details": "Anime scrobbler disabled in config",
                    "icon": "anilist",
                })

        # 9. MyAnimeList Tracker
        if mal_client:
            mal_conn = await mal_client.check_connection()
            if mal_conn.get("authenticated") and mal_conn.get("enabled"):
                servers.append({
                    "id": "myanimelist",
                    "name": "MyAnimeList",
                    "category": "Tracker",
                    "status": "connected",
                    "badge": "Active",
                    "version": "API v2",
                    "details": f"Connected as @{mal_conn.get('user') or 'user'}",
                    "icon": "myanimelist",
                })
            elif mal_conn.get("configured"):
                servers.append({
                    "id": "myanimelist",
                    "name": "MyAnimeList",
                    "category": "Tracker",
                    "status": "unconfigured",
                    "badge": "Auth Required",
                    "version": "API v2",
                    "details": "Requires authorization at /auth/mal",
                    "icon": "myanimelist",
                })
            else:
                servers.append({
                    "id": "myanimelist",
                    "name": "MyAnimeList",
                    "category": "Tracker",
                    "status": "unconfigured",
                    "badge": "Disabled",
                    "version": "API v2",
                    "details": "Anime scrobbler disabled in config",
                    "icon": "myanimelist",
                })

        for s in servers:
            sid = s.get("id", "")
            if sid in ("plex", "jellyfin", "emby"):
                s["enabled"] = settings_mgr.is_server_enabled(sid)
                if not s["enabled"]:
                    s["status"] = "disabled"
                    s["badge"] = "Disabled"
                    s["details"] = "Ingestion disabled"
            elif sid in ("sonarr", "radarr"):
                is_cfg = self.sonarr.is_configured if sid == "sonarr" else self.radarr.is_configured
                s["enabled"] = is_cfg
                if not is_cfg:
                    s["status"] = "unconfigured"
                    s["badge"] = "Disabled"
            elif sid in ("trakt", "simkl", "anilist", "myanimelist"):
                trk = "mal" if sid == "myanimelist" else sid
                is_trk_en = settings_mgr.is_tracker_enabled(trk)
                if not is_trk_en:
                    s["enabled"] = False
                    s["status"] = "disabled"
                    s["badge"] = "Paused"
                    s["details"] = "Sync paused in dashboard"
                elif s.get("badge") == "Disabled":
                    s["enabled"] = False
                else:
                    s["enabled"] = True
            else:
                s["enabled"] = True

        service_order = ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]

        def _is_ecosystem_disabled(srv: dict[str, Any]) -> bool:
            return (
                not srv.get("enabled", True)
                or srv.get("status") == "disabled"
                or srv.get("badge") in ("Disabled", "Paused")
            )

        servers.sort(
            key=lambda s: (
                1 if _is_ecosystem_disabled(s) else 0,
                service_order.index(s["id"]) if s.get("id") in service_order else 99,
            )
        )

        healthy = sum(1 for s in servers if s["status"] in ("connected", "available"))
        return {
            "servers": servers,
            "healthy_count": healthy,
            "total_count": len(servers),
        }

    async def sync_watchlist(self, demo: bool = False) -> dict[str, Any]:
        """Poll user's Trakt Watchlist and add missing items to Sonarr and Radarr."""
        if demo:
            await asyncio.sleep(0.05)
            self._last_sync_time = time.time()
            self._last_sync_result = {
                "timestamp": self._last_sync_time,
                "added": {"movies": 1, "shows": 1},
                "skipped": {"movies": 2, "shows": 2},
                "errors": [],
                "items": [
                    {
                        "title": "Gladiator II",
                        "year": 2024,
                        "type": "movie",
                        "status": "added",
                        "app": "Radarr",
                        "search_triggered": True,
                    },
                    {
                        "title": "Dune: Part Two",
                        "year": 2024,
                        "type": "movie",
                        "status": "skipped",
                        "reason": "Already in Radarr",
                        "app": "Radarr",
                    },
                    {
                        "title": "Alien: Earth",
                        "year": 2025,
                        "type": "show",
                        "status": "added",
                        "app": "Sonarr",
                        "search_triggered": True,
                    },
                    {
                        "title": "Severance",
                        "year": 2022,
                        "type": "show",
                        "status": "skipped",
                        "reason": "Already in Sonarr",
                        "app": "Sonarr",
                    },
                ],
            }
            return self._last_sync_result

        trakt = self.get_trakt()
        if not trakt.is_authenticated():
            return {
                "success": False,
                "error": "Trakt is not authenticated. Please authorize via /auth.",
            }

        async with self._lock:
            self._is_syncing = True
            added_movies: list[dict[str, Any]] = []
            skipped_movies: list[dict[str, Any]] = []
            added_shows: list[dict[str, Any]] = []
            skipped_shows: list[dict[str, Any]] = []
            errors: list[str] = []
            items_summary: list[dict[str, Any]] = []

            try:
                # ------------------- 1. Movies -> Radarr -------------------
                if self.radarr.is_configured:
                    try:
                        watchlist_movies = await trakt.get_watchlist("movies")
                        for item in watchlist_movies:
                            movie_data = item.get("movie", {})
                            title = movie_data.get("title", "")
                            year = movie_data.get("year")
                            ids = movie_data.get("ids", {})
                            tmdb_id = ids.get("tmdb")
                            imdb_id = ids.get("imdb")

                            if not title:
                                continue

                            # Check if already exists in Radarr
                            exists = await self.radarr.has_movie(
                                tmdb_id=tmdb_id, imdb_id=imdb_id, title=title
                            )
                            if exists:
                                skipped_movies.append({"title": title, "year": year})
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "movie",
                                    "status": "skipped",
                                    "reason": "Already in Radarr",
                                    "app": "Radarr",
                                })
                                continue

                            # Lookup in Radarr to get full metadata structure
                            lookup_term = f"tmdb:{tmdb_id}" if tmdb_id else (f"imdb:{imdb_id}" if imdb_id else title)
                            candidates = await self.radarr.lookup_movie(lookup_term)
                            if not candidates and title:
                                candidates = await self.radarr.lookup_movie(title)

                            if not candidates:
                                errors.append(f"Radarr lookup found no results for movie: {title}")
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "movie",
                                    "status": "error",
                                    "reason": "Lookup failed in Radarr",
                                    "app": "Radarr",
                                })
                                continue

                            # Pick best candidate matching tmdbId or title
                            selected = candidates[0]
                            if tmdb_id:
                                for c in candidates:
                                    if c.get("tmdbId") and int(c["tmdbId"]) == int(tmdb_id):
                                        selected = c
                                        break

                            # Add to Radarr
                            add_res = await self.radarr.add_movie(
                                selected,
                                search_for_movie=Config.SEARCH_ON_ADD,
                            )
                            if add_res.get("success"):
                                added_movies.append({"title": title, "year": year})
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "movie",
                                    "status": "added",
                                    "app": "Radarr",
                                    "search_triggered": Config.SEARCH_ON_ADD,
                                })
                                logger.info(f"Added movie '{title}' ({year}) to Radarr from Trakt Watchlist")

                                # Send push alert if enabled
                                if Config.ARR_NOTIFY_ON_ADD:
                                    try:
                                        p_media = ParsedMedia(
                                            event="radarr.add",
                                            username="Radarr",
                                            media_type="movie",
                                            title=title,
                                            year=year,
                                            ids=ids,
                                        )
                                        await self.notifier.dispatch(p_media, "arr_add")
                                    except Exception as notify_err:
                                        logger.warning(f"Error sending add notification: {notify_err}")
                            else:
                                err_msg = add_res.get("error", "Unknown add error")
                                errors.append(f"Failed to add '{title}' to Radarr: {err_msg}")
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "movie",
                                    "status": "error",
                                    "reason": err_msg,
                                    "app": "Radarr",
                                })
                    except Exception as e:
                        logger.error(f"Error processing movies watchlist in Radarr: {e}")
                        errors.append(f"Movies Watchlist sync error: {e}")

                # ------------------- 2. TV Shows -> Sonarr -------------------
                if self.sonarr.is_configured:
                    try:
                        watchlist_shows = await trakt.get_watchlist("shows")
                        for item in watchlist_shows:
                            show_data = item.get("show", {})
                            title = show_data.get("title", "")
                            year = show_data.get("year")
                            ids = show_data.get("ids", {})
                            tvdb_id = ids.get("tvdb")
                            imdb_id = ids.get("imdb")

                            if not title:
                                continue

                            # Check if already exists in Sonarr
                            exists = await self.sonarr.has_series(
                                tvdb_id=tvdb_id, imdb_id=imdb_id, title=title
                            )
                            if exists:
                                skipped_shows.append({"title": title, "year": year})
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "show",
                                    "status": "skipped",
                                    "reason": "Already in Sonarr",
                                    "app": "Sonarr",
                                })
                                continue

                            # Lookup in Sonarr to get full series metadata structure
                            lookup_term = f"tvdb:{tvdb_id}" if tvdb_id else (f"imdb:{imdb_id}" if imdb_id else title)
                            candidates = await self.sonarr.lookup_series(lookup_term)
                            if not candidates and title:
                                candidates = await self.sonarr.lookup_series(title)

                            if not candidates:
                                errors.append(f"Sonarr lookup found no results for show: {title}")
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "show",
                                    "status": "error",
                                    "reason": "Lookup failed in Sonarr",
                                    "app": "Sonarr",
                                })
                                continue

                            selected = candidates[0]
                            if tvdb_id:
                                for c in candidates:
                                    if c.get("tvdbId") and int(c["tvdbId"]) == int(tvdb_id):
                                        selected = c
                                        break

                            # Add to Sonarr
                            add_res = await self.sonarr.add_series(
                                selected,
                                search_for_missing_episodes=Config.SEARCH_ON_ADD,
                            )
                            if add_res.get("success"):
                                added_shows.append({"title": title, "year": year})
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "show",
                                    "status": "added",
                                    "app": "Sonarr",
                                    "search_triggered": Config.SEARCH_ON_ADD,
                                })
                                logger.info(f"Added TV show '{title}' ({year}) to Sonarr from Trakt Watchlist")

                                # Send push alert if enabled
                                if Config.ARR_NOTIFY_ON_ADD:
                                    try:
                                        p_media = ParsedMedia(
                                            event="sonarr.add",
                                            username="Sonarr",
                                            media_type="show",
                                            title=title,
                                            show_title=title,
                                            show_year=year,
                                            ids=ids,
                                        )
                                        await self.notifier.dispatch(p_media, "arr_add")
                                    except Exception as notify_err:
                                        logger.warning(f"Error sending add notification: {notify_err}")
                            else:
                                err_msg = add_res.get("error", "Unknown add error")
                                errors.append(f"Failed to add '{title}' to Sonarr: {err_msg}")
                                items_summary.append({
                                    "title": title,
                                    "year": year,
                                    "type": "show",
                                    "status": "error",
                                    "reason": err_msg,
                                    "app": "Sonarr",
                                })
                    except Exception as e:
                        logger.error(f"Error processing shows watchlist in Sonarr: {e}")
                        errors.append(f"Shows Watchlist sync error: {e}")

                self._last_sync_time = time.time()
                self._last_sync_result = {
                    "timestamp": self._last_sync_time,
                    "added": {"movies": len(added_movies), "shows": len(added_shows)},
                    "skipped": {"movies": len(skipped_movies), "shows": len(skipped_shows)},
                    "errors": errors,
                    "items": items_summary,
                }
                return self._last_sync_result
            finally:
                self._is_syncing = False


arr_bridge = ArrBridgeManager()
