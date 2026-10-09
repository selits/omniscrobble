"""Two-Way Library Reconciliation and Reverse Sync Engine for Omniscrobble.

Matches watched history and ratings between media servers (Plex, Jellyfin, Emby) and Trakt,
detects discrepancies, and executes selective or automated bi-directional synchronization
while suppressing webhook echo loops.
"""

import asyncio
import logging
import time
from typing import Any, Optional

from app.clients.emby_api_client import EmbyApiClient
from app.clients.jellyfin_api_client import JellyfinApiClient
from app.clients.plex_api_client import PlexApiClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.services.demo_manager import demo_mgr
from app.services.loop_prevention import LoopPreventionManager, loop_prevention
from app.services.settings_manager import settings_mgr

logger = logging.getLogger("omniscrobble.reverse_sync")


class ReverseSyncManager:
    """Manages two-way watched status and ratings reconciliation across media servers."""

    def __init__(
        self,
        plex_client: Optional[PlexApiClient] = None,
        jellyfin_client: Optional[JellyfinApiClient] = None,
        emby_client: Optional[EmbyApiClient] = None,
        trakt_client: Optional[TraktClient] = None,
        loop_prevention_mgr: Optional[LoopPreventionManager] = None,
    ):
        recon = settings_mgr.get_reconciliation_settings(mask_token=False)

        self.plex = plex_client or PlexApiClient(
            base_url=recon.get("plex_url") or Config.PLEX_URL,
            token=recon.get("plex_token") or Config.PLEX_TOKEN,
        )
        self.jellyfin = jellyfin_client or JellyfinApiClient(
            base_url=recon.get("jellyfin_url") or Config.JELLYFIN_URL,
            token=recon.get("jellyfin_token") or Config.JELLYFIN_TOKEN,
            user_id=recon.get("jellyfin_user_id") or Config.JELLYFIN_USER_ID,
        )
        self.emby = emby_client or EmbyApiClient(
            base_url=recon.get("emby_url") or Config.EMBY_URL,
            token=recon.get("emby_token") or Config.EMBY_TOKEN,
            user_id=recon.get("emby_user_id") or Config.EMBY_USER_ID,
        )

        self.trakt = trakt_client
        self.loop_prevention = loop_prevention_mgr or loop_prevention

        self._lock = asyncio.Lock()
        self._is_scanning: bool = False
        self._is_syncing: bool = False
        self._last_scan_time: Optional[float] = None
        self._last_sync_time: Optional[float] = None
        self._last_diff: list[dict[str, Any]] = []
        self._sync_progress: dict[str, Any] = {
            "total": 0,
            "current": 0,
            "success": 0,
            "failed": 0,
            "in_progress": False,
            "status": "idle",
            "message": "",
        }

    def get_server_client(self, server: Optional[str] = None):
        """Return the API client corresponding to the specified server name."""
        if server:
            target = server.lower().strip()
            if target == "jellyfin":
                return self.jellyfin
            elif target == "emby":
                return self.emby
            return self.plex

        recon = settings_mgr.get_reconciliation_settings(mask_token=False)
        target = (recon.get("server_type", "plex") or "plex").lower().strip()
        candidate = self.jellyfin if target == "jellyfin" else self.emby if target == "emby" else self.plex
        if candidate.is_configured():
            return candidate

        # Fallback to configured client if candidate is not configured
        if self.plex.is_configured():
            return self.plex
        if self.jellyfin.is_configured():
            return self.jellyfin
        if self.emby.is_configured():
            return self.emby

        return candidate

    def update_config(
        self,
        server: Optional[str] = None,
        plex_url: Optional[str] = None,
        plex_token: Optional[str] = None,
        jellyfin_url: Optional[str] = None,
        jellyfin_token: Optional[str] = None,
        jellyfin_user_id: Optional[str] = None,
        emby_url: Optional[str] = None,
        emby_token: Optional[str] = None,
        emby_user_id: Optional[str] = None,
    ) -> None:
        """Dynamically reconfigure media server credentials in-memory."""
        if plex_url is not None:
            self.plex.base_url = plex_url.rstrip("/")
        if plex_token is not None:
            self.plex.token = plex_token

        if jellyfin_url is not None:
            self.jellyfin.base_url = jellyfin_url.rstrip("/")
        if jellyfin_token is not None:
            self.jellyfin.token = jellyfin_token
        if jellyfin_user_id is not None:
            self.jellyfin.user_id = jellyfin_user_id

        if emby_url is not None:
            self.emby.base_url = emby_url.rstrip("/")
        if emby_token is not None:
            self.emby.token = emby_token
        if emby_user_id is not None:
            self.emby.user_id = emby_user_id

        logger.info(
            "ReverseSyncManager reconfigured in-memory: plex=%s, jellyfin=%s, emby=%s",
            self.plex.is_configured(),
            self.jellyfin.is_configured(),
            self.emby.is_configured(),
        )

    async def test_connection(
        self,
        server: str = "plex",
        url: Optional[str] = None,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Test connection to specified media server with provided or active credentials."""
        srv = (server or "plex").lower().strip()
        if srv == "jellyfin":
            target_url = (url if url is not None else self.jellyfin.base_url or "").rstrip("/")
            target_token = token if token is not None else (self.jellyfin.token or "")
            target_user = user_id if user_id is not None else self.jellyfin.user_id
            if not target_url or not target_token:
                return {"status": "unconfigured", "message": "Jellyfin URL and token/API key are required"}
            test_client = JellyfinApiClient(base_url=target_url, token=target_token, user_id=target_user)
        elif srv == "emby":
            target_url = (url if url is not None else self.emby.base_url or "").rstrip("/")
            target_token = token if token is not None else (self.emby.token or "")
            target_user = user_id if user_id is not None else self.emby.user_id
            if not target_url or not target_token:
                return {"status": "unconfigured", "message": "Emby URL and token/API key are required"}
            test_client = EmbyApiClient(base_url=target_url, token=target_token, user_id=target_user)
        else:
            target_url = (url if url is not None else self.plex.base_url or "").rstrip("/")
            target_token = token if token is not None else (self.plex.token or "")
            if not target_url or not target_token:
                return {"status": "unconfigured", "message": "Plex URL and token are required"}
            test_client = PlexApiClient(base_url=target_url, token=target_token)

        if target_url and not target_url.startswith(("http://", "https://")):
            # Defense-in-depth guard for direct service callers;
            # the HTTP endpoint (main.py) also validates and raises HTTP 400 first.
            return {"status": "error", "message": "Server URL must start with http:// or https://"}

        try:
            return await test_client.check_connection()
        finally:
            await test_client.close()

    def set_trakt_client(self, client: TraktClient) -> None:
        self.trakt = client

    def get_trakt(self) -> TraktClient:
        if self.trakt is None:
            from app.main import trakt
            self.trakt = trakt
        return self.trakt

    def is_configured(self, demo: bool = False, server: Optional[str] = None) -> bool:
        """Return True if media server direct connection and Trakt are ready."""
        if demo:
            return True
        if server:
            client = self.get_server_client(server)
            return client.is_configured() and self.get_trakt().is_authenticated()
        any_server_cfg = self.plex.is_configured() or self.jellyfin.is_configured() or self.emby.is_configured()
        return any_server_cfg and self.get_trakt().is_authenticated()

    async def get_status(self, demo: bool = False, server: Optional[str] = None) -> dict[str, Any]:
        """Return real-time operational status and diagnostics."""
        recon = settings_mgr.get_reconciliation_settings(mask_token=True)
        active_srv = (server or recon.get("server_type", "plex") or "plex").lower().strip()

        if demo:
            return {
                "configured": True,
                "server_type": active_srv,
                "active_server": active_srv,
                "active_server_configured": True,
                "active_server_connected": True,
                "plex_configured": True,
                "plex_connected": True,
                "jellyfin_configured": True,
                "jellyfin_connected": True,
                "emby_configured": True,
                "emby_connected": True,
                "servers": {
                    "plex": {"configured": True, "connected": True},
                    "jellyfin": {"configured": True, "connected": True},
                    "emby": {"configured": True, "connected": True},
                },
                "trakt_authenticated": True,
                "is_scanning": False,
                "is_syncing": False,
                "last_scan_time": self._last_scan_time or (time.time() - 3600),
                "last_sync_time": self._last_sync_time or (time.time() - 1800),
                "diff_count": len(demo_mgr.get_demo_reconciliation()),
                "sync_progress": self._sync_progress,
                "interval_minutes": recon.get("interval_minutes", 60),
                "sync_on_startup": recon.get("sync_on_startup", False),
                "sync_ratings": recon.get("sync_ratings", True),
                "direction_default": recon.get("direction_default", "all"),
            }

        plex_cfg = self.plex.is_configured()
        plex_conn = False
        if plex_cfg:
            p_res = await self.plex.check_connection()
            plex_conn = p_res.get("status") == "connected"

        jf_cfg = self.jellyfin.is_configured()
        jf_conn = False
        if jf_cfg:
            jf_res = await self.jellyfin.check_connection()
            jf_conn = jf_res.get("status") == "connected"

        emby_cfg = self.emby.is_configured()
        emby_conn = False
        if emby_cfg:
            emby_res = await self.emby.check_connection()
            emby_conn = emby_res.get("status") == "connected"

        trakt_auth = self.get_trakt().is_authenticated()

        active_cfg = jf_cfg if active_srv == "jellyfin" else emby_cfg if active_srv == "emby" else plex_cfg
        active_conn = jf_conn if active_srv == "jellyfin" else emby_conn if active_srv == "emby" else plex_conn

        return {
            "configured": (plex_cfg or jf_cfg or emby_cfg) and trakt_auth,
            "server_type": active_srv,
            "active_server": active_srv,
            "active_server_configured": active_cfg,
            "active_server_connected": active_conn,
            "plex_configured": plex_cfg,
            "plex_connected": plex_conn,
            "jellyfin_configured": jf_cfg,
            "jellyfin_connected": jf_conn,
            "emby_configured": emby_cfg,
            "emby_connected": emby_conn,
            "servers": {
                "plex": {"configured": plex_cfg, "connected": plex_conn},
                "jellyfin": {"configured": jf_cfg, "connected": jf_conn},
                "emby": {"configured": emby_cfg, "connected": emby_conn},
            },
            "trakt_authenticated": trakt_auth,
            "is_scanning": self._is_scanning,
            "is_syncing": self._is_syncing,
            "last_scan_time": self._last_scan_time,
            "last_sync_time": self._last_sync_time,
            "diff_count": len(self._last_diff),
            "sync_progress": self._sync_progress,
            "interval_minutes": recon.get("interval_minutes", Config.REVERSE_SYNC_INTERVAL),
            "sync_on_startup": recon.get("sync_on_startup", Config.REVERSE_SYNC_ON_STARTUP),
            "sync_ratings": recon.get("sync_ratings", Config.REVERSE_SYNC_RATINGS),
            "direction_default": recon.get("direction_default", "all"),
        }

    async def scan_discrepancies(
        self,
        force: bool = False,
        demo: bool = False,
        server: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Scan media server and Trakt to identify watched and rating discrepancies."""
        if demo:
            demo_diff = demo_mgr.get_demo_reconciliation()
            self._last_diff = demo_diff
            self._last_scan_time = time.time()
            return demo_diff

        client = self.get_server_client(server)
        server_name = getattr(client, "server_type", "plex") if hasattr(client, "server_type") else "plex"

        if not client.is_configured():
            logger.warning("Reverse sync scan aborted: %s API not configured.", server_name.capitalize())
            return []

        trakt_client = self.get_trakt()
        if not trakt_client.is_authenticated():
            logger.warning("Reverse sync scan aborted: Trakt not authenticated.")
            return []

        if self._is_scanning:
            logger.info("Reverse sync scan already in progress; returning cached diff.")
            return self._last_diff

        async with self._lock:
            self._is_scanning = True
            try:
                logger.info("Starting library reconciliation scan between %s and Trakt...", server_name.capitalize())
                diff: list[dict[str, Any]] = []

                # 1. Fetch Trakt watched movies & build lookup indices
                watched_movies = await trakt_client.get_watched_movies()
                trakt_movies_by_imdb: dict[str, dict] = {}
                trakt_movies_by_tmdb: dict[str, dict] = {}
                trakt_movies_by_title_year: dict[tuple[str, Optional[int]], dict] = {}

                for m in watched_movies:
                    movie_obj = m.get("movie") or {}
                    ids = movie_obj.get("ids") or {}
                    imdb_id = (ids.get("imdb") or "").strip().lower()
                    tmdb_id = str(ids.get("tmdb") or "").strip()
                    title = (movie_obj.get("title") or "").strip().lower()
                    year = movie_obj.get("year")

                    if imdb_id:
                        trakt_movies_by_imdb[imdb_id] = m
                    if tmdb_id:
                        trakt_movies_by_tmdb[tmdb_id] = m
                    if title:
                        trakt_movies_by_title_year[(title, year)] = m

                # 2. Fetch Trakt watched shows & build episode lookup indices
                watched_shows = await trakt_client.get_watched_shows()
                trakt_eps_by_show_imdb: dict[tuple[str, int, int], dict] = {}
                trakt_eps_by_show_tmdb: dict[tuple[str, int, int], dict] = {}
                trakt_eps_by_show_tvdb: dict[tuple[str, int, int], dict] = {}
                trakt_eps_by_show_title: dict[tuple[str, int, int], dict] = {}

                for s in watched_shows:
                    show_obj = s.get("show") or {}
                    s_ids = show_obj.get("ids") or {}
                    s_imdb = (s_ids.get("imdb") or "").strip().lower()
                    s_tmdb = str(s_ids.get("tmdb") or "").strip()
                    s_tvdb = str(s_ids.get("tvdb") or "").strip()
                    s_title = (show_obj.get("title") or "").strip().lower()

                    for season in s.get("seasons", []):
                        season_num = season.get("number", 0)
                        for ep in season.get("episodes", []):
                            ep_num = ep.get("number", 0)
                            if s_imdb:
                                trakt_eps_by_show_imdb[(s_imdb, season_num, ep_num)] = ep
                            if s_tmdb:
                                trakt_eps_by_show_tmdb[(s_tmdb, season_num, ep_num)] = ep
                            if s_tvdb:
                                trakt_eps_by_show_tvdb[(s_tvdb, season_num, ep_num)] = ep
                            if s_title:
                                trakt_eps_by_show_title[(s_title, season_num, ep_num)] = ep

                # 3. Optional: Fetch Trakt ratings
                movie_ratings_by_imdb: dict[str, int] = {}
                movie_ratings_by_tmdb: dict[str, int] = {}
                movie_ratings_by_title_year: dict[tuple[str, Optional[int]], int] = {}
                ep_ratings_by_show_imdb: dict[tuple[str, int, int], int] = {}
                ep_ratings_by_show_tvdb: dict[tuple[str, int, int], int] = {}
                ep_ratings_by_show_title: dict[tuple[str, int, int], int] = {}

                if Config.REVERSE_SYNC_RATINGS:
                    try:
                        movie_ratings = await trakt_client.get_ratings("movies")
                        for r in movie_ratings:
                            val = int(r.get("rating", 0))
                            m_obj = r.get("movie") or {}
                            m_ids = m_obj.get("ids") or {}
                            imdb_id = (m_ids.get("imdb") or "").strip().lower()
                            tmdb_id = str(m_ids.get("tmdb") or "").strip()
                            title = (m_obj.get("title") or "").strip().lower()
                            year = m_obj.get("year")
                            if imdb_id:
                                movie_ratings_by_imdb[imdb_id] = val
                            if tmdb_id:
                                movie_ratings_by_tmdb[tmdb_id] = val
                            if title:
                                movie_ratings_by_title_year[(title, year)] = val

                        ep_ratings = await trakt_client.get_ratings("episodes")
                        for r in ep_ratings:
                            val = int(r.get("rating", 0))
                            e_obj = r.get("episode") or {}
                            s_obj = r.get("show") or {}
                            s_ids = s_obj.get("ids") or {}
                            s_imdb = (s_ids.get("imdb") or "").strip().lower()
                            s_tvdb = str(s_ids.get("tvdb") or "").strip()
                            s_title = (s_obj.get("title") or "").strip().lower()
                            season_num = e_obj.get("season", 0)
                            ep_num = e_obj.get("number", 0)

                            if s_imdb:
                                ep_ratings_by_show_imdb[(s_imdb, season_num, ep_num)] = val
                            if s_tvdb:
                                ep_ratings_by_show_tvdb[(s_tvdb, season_num, ep_num)] = val
                            if s_title:
                                ep_ratings_by_show_title[(s_title, season_num, ep_num)] = val
                    except Exception as exc:
                        logger.warning("Failed to fetch Trakt ratings during reverse sync scan: %s", exc)

                # 4. Fetch sections from the media server
                sections = await client.get_library_sections()

                for sec in sections:
                    sec_key = sec["key"]
                    sec_type = sec["type"]

                    if sec_type == "movie":
                        server_movies = await client.get_movies(sec_key)
                        for sm in server_movies:
                            rating_key = sm["rating_key"]
                            title = sm["title"]
                            year = sm.get("year")
                            sm_ids = sm.get("ids") or {}
                            sm_imdb = (sm_ids.get("imdb") or "").strip().lower()
                            sm_tmdb = str(sm_ids.get("tmdb") or "").strip()
                            is_watched_server = sm.get("is_watched", False)
                            server_rating = sm.get("user_rating")

                            # Match against Trakt watched
                            trakt_match = None
                            if sm_imdb and sm_imdb in trakt_movies_by_imdb:
                                trakt_match = trakt_movies_by_imdb[sm_imdb]
                            elif sm_tmdb and sm_tmdb in trakt_movies_by_tmdb:
                                trakt_match = trakt_movies_by_tmdb[sm_tmdb]
                            elif (title.lower().strip(), year) in trakt_movies_by_title_year:
                                trakt_match = trakt_movies_by_title_year[(title.lower().strip(), year)]

                            is_watched_trakt = trakt_match is not None and int(trakt_match.get("plays", 0)) > 0

                            # Match against Trakt rating
                            trakt_rating = None
                            if sm_imdb and sm_imdb in movie_ratings_by_imdb:
                                trakt_rating = movie_ratings_by_imdb[sm_imdb]
                            elif sm_tmdb and sm_tmdb in movie_ratings_by_tmdb:
                                trakt_rating = movie_ratings_by_tmdb[sm_tmdb]
                            elif (title.lower().strip(), year) in movie_ratings_by_title_year:
                                trakt_rating = movie_ratings_by_title_year[(title.lower().strip(), year)]

                            # Check for Watched Discrepancy
                            if is_watched_trakt and not is_watched_server:
                                diff.append({
                                    "id": f"{server_name}:movie:{rating_key}",
                                    "server": server_name,
                                    "type": "movie",
                                    "title": title,
                                    "series_title": None,
                                    "season": None,
                                    "episode": None,
                                    "year": year,
                                    "rating_key": rating_key,
                                    "ids": sm_ids,
                                    "status": "trakt_only",
                                    "server_watched": False,
                                    "plex_watched": False,
                                    "trakt_watched": True,
                                    "server_rating": server_rating,
                                    "plex_rating": server_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": f"mark_{server_name}_watched",
                                })
                            elif is_watched_server and not is_watched_trakt:
                                diff.append({
                                    "id": f"{server_name}:movie:{rating_key}",
                                    "server": server_name,
                                    "type": "movie",
                                    "title": title,
                                    "series_title": None,
                                    "season": None,
                                    "episode": None,
                                    "year": year,
                                    "rating_key": rating_key,
                                    "ids": sm_ids,
                                    "status": f"{server_name}_only",
                                    "server_watched": True,
                                    "plex_watched": True,
                                    "trakt_watched": False,
                                    "server_rating": server_rating,
                                    "plex_rating": server_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "sync_to_trakt",
                                })
                            elif Config.REVERSE_SYNC_RATINGS and trakt_rating is not None:
                                s_r_int = round(server_rating) if server_rating is not None else None
                                if s_r_int is None or s_r_int != trakt_rating:
                                    diff.append({
                                        "id": f"{server_name}:movie:{rating_key}",
                                        "server": server_name,
                                        "type": "movie",
                                        "title": title,
                                        "series_title": None,
                                        "season": None,
                                        "episode": None,
                                        "year": year,
                                        "rating_key": rating_key,
                                        "ids": sm_ids,
                                        "status": "rating_mismatch",
                                        "server_watched": is_watched_server,
                                        "plex_watched": is_watched_server,
                                        "trakt_watched": is_watched_trakt,
                                        "server_rating": server_rating,
                                        "plex_rating": server_rating,
                                        "trakt_rating": trakt_rating,
                                        "action_recommended": f"sync_rating_to_{server_name}",
                                    })

                    elif sec_type == "show":
                        server_episodes = await client.get_episodes(sec_key)
                        for se in server_episodes:
                            rating_key = se["rating_key"]
                            series_title = se.get("series_title", "")
                            ep_title = se.get("title", "")
                            season_num = se.get("season")
                            ep_num = se.get("episode")
                            if season_num is None or ep_num is None:
                                continue

                            se_ids = se.get("ids") or {}
                            se_imdb = (se_ids.get("imdb") or "").strip().lower()
                            se_tmdb = str(se_ids.get("tmdb") or "").strip()
                            se_tvdb = str(se_ids.get("tvdb") or "").strip()
                            is_watched_server = se.get("is_watched", False)
                            server_rating = se.get("user_rating")

                            # Match against Trakt show episodes
                            trakt_ep = None
                            if se_imdb and (se_imdb, season_num, ep_num) in trakt_eps_by_show_imdb:
                                trakt_ep = trakt_eps_by_show_imdb[(se_imdb, season_num, ep_num)]
                            elif se_tmdb and (se_tmdb, season_num, ep_num) in trakt_eps_by_show_tmdb:
                                trakt_ep = trakt_eps_by_show_tmdb[(se_tmdb, season_num, ep_num)]
                            elif se_tvdb and (se_tvdb, season_num, ep_num) in trakt_eps_by_show_tvdb:
                                trakt_ep = trakt_eps_by_show_tvdb[(se_tvdb, season_num, ep_num)]
                            elif (series_title.lower().strip(), season_num, ep_num) in trakt_eps_by_show_title:
                                trakt_ep = trakt_eps_by_show_title[(series_title.lower().strip(), season_num, ep_num)]

                            is_watched_trakt = trakt_ep is not None and int(trakt_ep.get("plays", 0)) > 0

                            # Match against Trakt rating
                            trakt_rating = None
                            if se_imdb and (se_imdb, season_num, ep_num) in ep_ratings_by_show_imdb:
                                trakt_rating = ep_ratings_by_show_imdb[(se_imdb, season_num, ep_num)]
                            elif se_tvdb and (se_tvdb, season_num, ep_num) in ep_ratings_by_show_tvdb:
                                trakt_rating = ep_ratings_by_show_tvdb[(se_tvdb, season_num, ep_num)]
                            elif (series_title.lower().strip(), season_num, ep_num) in ep_ratings_by_show_title:
                                trakt_rating = ep_ratings_by_show_title[(series_title.lower().strip(), season_num, ep_num)]

                            # Check for Watched Discrepancy
                            if is_watched_trakt and not is_watched_server:
                                diff.append({
                                    "id": f"{server_name}:episode:{rating_key}",
                                    "server": server_name,
                                    "type": "episode",
                                    "title": ep_title,
                                    "series_title": series_title,
                                    "season": season_num,
                                    "episode": ep_num,
                                    "year": se.get("year"),
                                    "rating_key": rating_key,
                                    "ids": se_ids,
                                    "status": "trakt_only",
                                    "server_watched": False,
                                    "plex_watched": False,
                                    "trakt_watched": True,
                                    "server_rating": server_rating,
                                    "plex_rating": server_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": f"mark_{server_name}_watched",
                                })
                            elif is_watched_server and not is_watched_trakt:
                                diff.append({
                                    "id": f"{server_name}:episode:{rating_key}",
                                    "server": server_name,
                                    "type": "episode",
                                    "title": ep_title,
                                    "series_title": series_title,
                                    "season": season_num,
                                    "episode": ep_num,
                                    "year": se.get("year"),
                                    "rating_key": rating_key,
                                    "ids": se_ids,
                                    "status": f"{server_name}_only",
                                    "server_watched": True,
                                    "plex_watched": True,
                                    "trakt_watched": False,
                                    "server_rating": server_rating,
                                    "plex_rating": server_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "sync_to_trakt",
                                })
                            elif Config.REVERSE_SYNC_RATINGS and trakt_rating is not None:
                                s_r_int = round(server_rating) if server_rating is not None else None
                                if s_r_int is None or s_r_int != trakt_rating:
                                    diff.append({
                                        "id": f"{server_name}:episode:{rating_key}",
                                        "server": server_name,
                                        "type": "episode",
                                        "title": ep_title,
                                        "series_title": series_title,
                                        "season": season_num,
                                        "episode": ep_num,
                                        "year": se.get("year"),
                                        "rating_key": rating_key,
                                        "ids": se_ids,
                                        "status": "rating_mismatch",
                                        "server_watched": is_watched_server,
                                        "plex_watched": is_watched_server,
                                        "trakt_watched": is_watched_trakt,
                                        "server_rating": server_rating,
                                        "plex_rating": server_rating,
                                        "trakt_rating": trakt_rating,
                                        "action_recommended": f"sync_rating_to_{server_name}",
                                    })

                self._last_diff = diff
                self._last_scan_time = time.time()
                logger.info("Library reconciliation scan complete: discovered %d discrepancies.", len(diff))
                return diff

            finally:
                self._is_scanning = False

    def get_chunked_diff(self, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        """Return a cursor-paginated slice of cached discrepancies to preserve low memory overhead."""
        diff = self._last_diff or []
        total = len(diff)
        c = max(0, cursor)
        lim = max(1, limit)
        chunk = diff[c : c + lim]
        next_cursor = (c + lim) if (c + lim) < total else None
        return {
            "status": "ok",
            "diff": chunk,
            "count": len(chunk),
            "total": total,
            "cursor": next_cursor,
            "has_more": next_cursor is not None,
        }

    async def execute_reconciliation(
        self,
        item_ids: Optional[list[str]] = None,
        direction: str = "all",
        demo: bool = False,
        server: Optional[str] = None,
        preview_items: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Execute reconciliation actions for selected or all discrepancies."""
        if demo:
            self._sync_progress = {
                "total": 4,
                "current": 4,
                "success": 4,
                "failed": 0,
                "in_progress": False,
                "status": "completed",
                "message": "Demo library reconciliation simulated successfully (4 items).",
            }
            self._last_sync_time = time.time()
            self._last_diff = []
            return {
                "status": "success",
                "total": 4,
                "reconciled": 4,
                "failed": 0,
                "items": [
                    {"id": "movie:101", "action": "mark_plex_watched", "status": "success"},
                    {"id": "movie:102", "action": "sync_rating_to_plex", "status": "success"},
                    {"id": "episode:201", "action": "sync_to_trakt", "status": "success"},
                    {"id": "episode:202", "action": "mark_plex_watched", "status": "success"},
                ],
            }

        if self._is_syncing:
            return {"status": "in_progress", "message": "Reconciliation already in progress"}

        target_items = []
        if preview_items is not None:
            target_items = list(preview_items)
        elif item_ids:
            target_set = set(item_ids)
            target_items = [item for item in self._last_diff if item["id"] in target_set]
        else:
            target_items = list(self._last_diff)

        # Apply direction filter if specified
        if direction in ("trakt_to_plex", "trakt_to_server"):
            target_items = [
                i for i in target_items
                if i["action_recommended"].startswith("mark_") or i["action_recommended"].startswith("sync_rating_to_")
            ]
        elif direction in ("plex_to_trakt", "server_to_trakt"):
            target_items = [
                i for i in target_items
                if i["action_recommended"] in ("sync_to_trakt", "sync_rating_to_trakt")
            ]

        if not target_items:
            return {"status": "success", "total": 0, "reconciled": 0, "failed": 0, "items": []}

        self._is_syncing = True
        self._sync_progress = {
            "total": len(target_items),
            "current": 0,
            "success": 0,
            "failed": 0,
            "in_progress": True,
            "status": "syncing",
            "message": f"Reconciling {len(target_items)} items...",
        }

        reconciled_ids: set[str] = set()
        item_results: list[dict[str, Any]] = []

        try:
            trakt_client = self.get_trakt()
            for item in target_items:
                self._sync_progress["current"] += 1
                action = item.get("action_recommended", "")
                rating_key = item.get("rating_key", "")
                item_server = item.get("server") or server or "plex"
                client = self.get_server_client(item_server)
                success = False

                # Suppress loop prevention on ratingKey and all IDs before touching server
                if rating_key:
                    self.loop_prevention.ignore(rating_key, ttl=180.0)
                for id_val in (item.get("ids") or {}).values():
                    if id_val:
                        self.loop_prevention.ignore(str(id_val), ttl=180.0)

                try:
                    if action.startswith("mark_") and action.endswith("_watched"):
                        success = await client.mark_as_watched(rating_key)
                    elif action.startswith("sync_rating_to_"):
                        val = item.get("trakt_rating")
                        if val is not None:
                            success = await client.set_user_rating(rating_key, float(val))
                        else:
                            success = False
                    elif action == "sync_to_trakt":
                        # Push media server watched item to Trakt history
                        if item["type"] == "movie":
                            payload = {"movies": [{"title": item["title"], "year": item.get("year"), "ids": item.get("ids")}]}
                        else:
                            if item.get("ids"):
                                payload = {"episodes": [{"ids": item.get("ids")}]}
                            else:
                                payload = {
                                    "shows": [{
                                        "title": item.get("series_title") or item["title"],
                                        "seasons": [{
                                            "number": item.get("season", 1),
                                            "episodes": [{"number": item.get("episode", 1)}],
                                        }],
                                    }]
                                }
                        res = await trakt_client.sync_history(payload)
                        success = not bool(res.get("error"))
                    elif action == "sync_rating_to_trakt":
                        # Push media server rating to Trakt
                        s_r = item.get("server_rating") or item.get("plex_rating")
                        if s_r is not None:
                            val = int(round(s_r))
                            if item["type"] == "movie":
                                payload = {"movies": [{"title": item["title"], "year": item.get("year"), "rating": val, "ids": item.get("ids")}]}
                            else:
                                payload = {"episodes": [{"rating": val, "ids": item.get("ids")}]}
                            res = await trakt_client.sync_ratings(payload)
                            success = not bool(res.get("error"))
                        else:
                            success = False

                except Exception as exc:
                    logger.error("Error reconciling item %s (%s) on %s: %s", item.get("id"), action, item_server, exc)
                    success = False

                if success:
                    self._sync_progress["success"] += 1
                    reconciled_ids.add(item["id"])
                    item_results.append({"id": item["id"], "action": action, "status": "success"})
                else:
                    self._sync_progress["failed"] += 1
                    item_results.append({"id": item["id"], "action": action, "status": "failed"})

            # Remove reconciled items from in-memory discrepancy cache
            self._last_diff = [i for i in self._last_diff if i["id"] not in reconciled_ids]
            self._last_sync_time = time.time()
            self._sync_progress["in_progress"] = False
            self._sync_progress["status"] = "completed"
            self._sync_progress["message"] = f"Reconciliation finished. {self._sync_progress['success']} succeeded, {self._sync_progress['failed']} failed."

            return {
                "status": "success",
                "total": len(target_items),
                "reconciled": self._sync_progress["success"],
                "failed": self._sync_progress["failed"],
                "items": item_results,
            }

        finally:
            self._is_syncing = False

    async def mirror_watched_status(
        self,
        media: Any,
        source_server: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mirrors a watched item from source_server to all other active/configured media servers in real-time."""
        if not settings_mgr.is_multi_server_mirroring_enabled():
            return {"status": "skipped", "reason": "multi_server_mirroring_disabled"}

        src = (source_server or "").strip().lower()
        results: dict[str, Any] = {"source": src, "mirrored": [], "failed": [], "skipped": []}

        # Determine target servers: configured and enabled, excluding source
        potential_targets: list[tuple[str, Any]] = []
        if src != "plex" and self.plex.is_configured() and settings_mgr.is_server_enabled("plex"):
            potential_targets.append(("plex", self.plex))
        if src != "jellyfin" and self.jellyfin.is_configured() and settings_mgr.is_server_enabled("jellyfin"):
            potential_targets.append(("jellyfin", self.jellyfin))
        if src != "emby" and self.emby.is_configured() and settings_mgr.is_server_enabled("emby"):
            potential_targets.append(("emby", self.emby))

        if not potential_targets:
            return {"status": "noop", "reason": "no_other_active_servers", "results": results}

        for srv_name, client in potential_targets:
            try:
                item = await client.find_item(media)
                if not item or not item.get("rating_key"):
                    logger.debug(f"Mirroring: Item '{getattr(media, 'title', '')}' not found on {srv_name}")
                    results["skipped"].append({"server": srv_name, "reason": "not_found"})
                    continue

                rating_key = str(item["rating_key"])
                # Suppress bounce-back webhook loop for this item on the target server
                self.loop_prevention.ignore(rating_key, ttl=180.0)
                media_ids = getattr(media, "ids", {}) or {}
                for v in media_ids.values():
                    if v:
                        self.loop_prevention.ignore(str(v), ttl=180.0)

                success = await client.mark_as_watched(rating_key)
                if success:
                    logger.info(
                        f"Multi-Server Mirroring: Marked '{getattr(media, 'title', '')}' as watched on {srv_name} (key: {rating_key})"
                    )
                    results["mirrored"].append({"server": srv_name, "rating_key": rating_key})
                else:
                    logger.warning(
                        f"Multi-Server Mirroring: Failed to mark '{getattr(media, 'title', '')}' as watched on {srv_name}"
                    )
                    results["failed"].append({"server": srv_name, "rating_key": rating_key, "error": "api_call_failed"})
            except Exception as e:
                logger.error(f"Error mirroring '{getattr(media, 'title', '')}' to {srv_name}: {e}")
                results["failed"].append({"server": srv_name, "error": str(e)})

        return {"status": "completed", "results": results}

    async def mirror_rating(
        self,
        media: Any,
        rating_10: float,
        source_server: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mirrors a user rating from source_server to all other active/configured media servers in real-time."""
        if not settings_mgr.is_multi_server_mirroring_enabled():
            return {"status": "skipped", "reason": "multi_server_mirroring_disabled"}

        src = (source_server or "").strip().lower()
        results: dict[str, Any] = {"source": src, "mirrored": [], "failed": [], "skipped": []}

        potential_targets: list[tuple[str, Any]] = []
        if src != "plex" and self.plex.is_configured() and settings_mgr.is_server_enabled("plex"):
            potential_targets.append(("plex", self.plex))
        if src != "jellyfin" and self.jellyfin.is_configured() and settings_mgr.is_server_enabled("jellyfin"):
            potential_targets.append(("jellyfin", self.jellyfin))
        if src != "emby" and self.emby.is_configured() and settings_mgr.is_server_enabled("emby"):
            potential_targets.append(("emby", self.emby))

        if not potential_targets:
            return {"status": "noop", "reason": "no_other_active_servers", "results": results}

        for srv_name, client in potential_targets:
            try:
                item = await client.find_item(media)
                if not item or not item.get("rating_key"):
                    results["skipped"].append({"server": srv_name, "reason": "not_found"})
                    continue

                rating_key = str(item["rating_key"])
                self.loop_prevention.ignore(rating_key, ttl=180.0)
                media_ids = getattr(media, "ids", {}) or {}
                for v in media_ids.values():
                    if v:
                        self.loop_prevention.ignore(str(v), ttl=180.0)

                success = await client.set_user_rating(rating_key, rating_10)
                if success:
                    logger.info(
                        f"Multi-Server Mirroring: Set rating {rating_10}/10 for '{getattr(media, 'title', '')}' on {srv_name} (key: {rating_key})"
                    )
                    results["mirrored"].append({"server": srv_name, "rating_key": rating_key})
                else:
                    results["failed"].append({"server": srv_name, "rating_key": rating_key, "error": "api_call_failed"})
            except Exception as e:
                logger.error(f"Error mirroring rating to {srv_name}: {e}")
                results["failed"].append({"server": srv_name, "error": str(e)})

        return {"status": "completed", "results": results}

    async def run_startup_sync(self) -> None:
        """Run scan and reconciliation on startup in the background if configured."""
        recon = settings_mgr.get_reconciliation_settings(mask_token=False)
        sync_startup = recon.get("sync_on_startup", Config.REVERSE_SYNC_ON_STARTUP)
        if not sync_startup or not self.is_configured():
            return

        logger.info("Reverse Sync: Startup scan scheduled (sync_on_startup=true). Waiting 15s for server warmup...")
        await asyncio.sleep(15.0)
        try:
            diff = await self.scan_discrepancies()
            if diff:
                logger.info("Reverse Sync: Startup scan found %d items to reconcile. Executing auto-reconcile...", len(diff))
                await self.execute_reconciliation(direction="trakt_to_server")
        except Exception as exc:
            logger.error("Error during startup reverse sync: %s", exc)

    async def register_webhook(self, server: str, webhook_url: Optional[str] = None) -> dict[str, Any]:
        """Automatically register the Omniscrobble webhook endpoint with the specified media server."""
        target = (server or "plex").lower().strip()
        client = self.get_server_client(target)
        if not client.is_configured():
            return {
                "success": False,
                "server": target,
                "error": f"{target.capitalize()} server URL or token is not configured.",
            }

        target_url = (webhook_url or "").strip()
        if not target_url:
            path_map = {
                "plex": "/webhook",
                "jellyfin": "/webhook/jellyfin",
                "emby": "/webhook/emby",
            }
            target_url = path_map.get(target, "/webhook")

        return await client.register_webhook(target_url)


reverse_sync_mgr = ReverseSyncManager()

