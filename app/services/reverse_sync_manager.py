"""Two-Way Library Reconciliation and Reverse Sync Engine for Omniscrobble.

Matches watched history and ratings between media servers (Plex) and Trakt,
detects discrepancies, and executes selective or automated bi-directional synchronization
while suppressing webhook echo loops.
"""

import asyncio
import logging
import time
from typing import Any, Optional

from app.clients.plex_api_client import PlexApiClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.services.demo_manager import demo_mgr
from app.services.loop_prevention import LoopPreventionManager, loop_prevention

logger = logging.getLogger("omniscrobble.reverse_sync")


class ReverseSyncManager:
    """Manages two-way watched status and ratings reconciliation."""

    def __init__(
        self,
        plex_client: Optional[PlexApiClient] = None,
        trakt_client: Optional[TraktClient] = None,
        loop_prevention_mgr: Optional[LoopPreventionManager] = None,
    ):
        self.plex = plex_client or PlexApiClient()
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

    def set_trakt_client(self, client: TraktClient) -> None:
        self.trakt = client

    def get_trakt(self) -> TraktClient:
        if self.trakt is None:
            # Fallback import if not injected
            from app.main import trakt
            self.trakt = trakt
        return self.trakt

    def is_configured(self, demo: bool = False) -> bool:
        """Return True if media server direct connection and Trakt are ready."""
        if demo:
            return True
        return self.plex.is_configured() and self.get_trakt().is_authenticated()

    async def get_status(self, demo: bool = False) -> dict[str, Any]:
        """Return real-time operational status and diagnostics."""
        if demo:
            return {
                "configured": True,
                "plex_configured": True,
                "plex_connected": True,
                "trakt_authenticated": True,
                "is_scanning": False,
                "is_syncing": False,
                "last_scan_time": self._last_scan_time or (time.time() - 3600),
                "last_sync_time": self._last_sync_time or (time.time() - 1800),
                "diff_count": len(demo_mgr.get_demo_reconciliation()),
                "sync_progress": self._sync_progress,
                "interval_minutes": Config.REVERSE_SYNC_INTERVAL,
                "sync_on_startup": Config.REVERSE_SYNC_ON_STARTUP,
                "sync_ratings": Config.REVERSE_SYNC_RATINGS,
            }

        plex_configured = self.plex.is_configured()
        plex_connected = False
        if plex_configured:
            conn_info = await self.plex.check_connection()
            plex_connected = conn_info.get("status") == "connected"

        trakt_auth = self.get_trakt().is_authenticated()

        return {
            "configured": plex_configured and trakt_auth,
            "plex_configured": plex_configured,
            "plex_connected": plex_connected,
            "trakt_authenticated": trakt_auth,
            "is_scanning": self._is_scanning,
            "is_syncing": self._is_syncing,
            "last_scan_time": self._last_scan_time,
            "last_sync_time": self._last_sync_time,
            "diff_count": len(self._last_diff),
            "sync_progress": self._sync_progress,
            "interval_minutes": Config.REVERSE_SYNC_INTERVAL,
            "sync_on_startup": Config.REVERSE_SYNC_ON_STARTUP,
            "sync_ratings": Config.REVERSE_SYNC_RATINGS,
        }

    async def scan_discrepancies(self, force: bool = False, demo: bool = False) -> list[dict[str, Any]]:
        """Scan media server and Trakt to identify watched and rating discrepancies."""
        if demo:
            demo_diff = demo_mgr.get_demo_reconciliation()
            self._last_diff = demo_diff
            self._last_scan_time = time.time()
            return demo_diff

        if not self.plex.is_configured():
            logger.warning("Reverse sync scan aborted: Plex API not configured (PLEX_URL or PLEX_TOKEN missing).")
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
                logger.info("Starting library reconciliation scan between Plex and Trakt...")
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

                # 4. Fetch Plex sections
                sections = await self.plex.get_library_sections()

                for sec in sections:
                    sec_key = sec["key"]
                    sec_type = sec["type"]

                    if sec_type == "movie":
                        plex_movies = await self.plex.get_movies(sec_key)
                        for pm in plex_movies:
                            rating_key = pm["rating_key"]
                            title = pm["title"]
                            year = pm.get("year")
                            plex_ids = pm.get("ids") or {}
                            plex_imdb = (plex_ids.get("imdb") or "").strip().lower()
                            plex_tmdb = str(plex_ids.get("tmdb") or "").strip()
                            is_watched_plex = pm.get("is_watched", False)
                            plex_rating = pm.get("user_rating")

                            # Match against Trakt watched
                            trakt_match = None
                            if plex_imdb and plex_imdb in trakt_movies_by_imdb:
                                trakt_match = trakt_movies_by_imdb[plex_imdb]
                            elif plex_tmdb and plex_tmdb in trakt_movies_by_tmdb:
                                trakt_match = trakt_movies_by_tmdb[plex_tmdb]
                            elif (title.lower().strip(), year) in trakt_movies_by_title_year:
                                trakt_match = trakt_movies_by_title_year[(title.lower().strip(), year)]

                            is_watched_trakt = trakt_match is not None and int(trakt_match.get("plays", 0)) > 0

                            # Match against Trakt rating
                            trakt_rating = None
                            if plex_imdb and plex_imdb in movie_ratings_by_imdb:
                                trakt_rating = movie_ratings_by_imdb[plex_imdb]
                            elif plex_tmdb and plex_tmdb in movie_ratings_by_tmdb:
                                trakt_rating = movie_ratings_by_tmdb[plex_tmdb]
                            elif (title.lower().strip(), year) in movie_ratings_by_title_year:
                                trakt_rating = movie_ratings_by_title_year[(title.lower().strip(), year)]

                            # Check for Watched Discrepancy
                            if is_watched_trakt and not is_watched_plex:
                                diff.append({
                                    "id": f"movie:{rating_key}",
                                    "type": "movie",
                                    "title": title,
                                    "series_title": None,
                                    "season": None,
                                    "episode": None,
                                    "year": year,
                                    "rating_key": rating_key,
                                    "ids": plex_ids,
                                    "status": "trakt_only",
                                    "plex_watched": False,
                                    "trakt_watched": True,
                                    "plex_rating": plex_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "mark_plex_watched",
                                })
                            elif is_watched_plex and not is_watched_trakt:
                                diff.append({
                                    "id": f"movie:{rating_key}",
                                    "type": "movie",
                                    "title": title,
                                    "series_title": None,
                                    "season": None,
                                    "episode": None,
                                    "year": year,
                                    "rating_key": rating_key,
                                    "ids": plex_ids,
                                    "status": "plex_only",
                                    "plex_watched": True,
                                    "trakt_watched": False,
                                    "plex_rating": plex_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "sync_to_trakt",
                                })
                            elif Config.REVERSE_SYNC_RATINGS and trakt_rating is not None:
                                # Watched matches, but check rating mismatch
                                plex_r_int = round(plex_rating) if plex_rating is not None else None
                                if plex_r_int is None or plex_r_int != trakt_rating:
                                    diff.append({
                                        "id": f"movie:{rating_key}",
                                        "type": "movie",
                                        "title": title,
                                        "series_title": None,
                                        "season": None,
                                        "episode": None,
                                        "year": year,
                                        "rating_key": rating_key,
                                        "ids": plex_ids,
                                        "status": "rating_mismatch",
                                        "plex_watched": is_watched_plex,
                                        "trakt_watched": is_watched_trakt,
                                        "plex_rating": plex_rating,
                                        "trakt_rating": trakt_rating,
                                        "action_recommended": "sync_rating_to_plex",
                                    })

                    elif sec_type == "show":
                        plex_episodes = await self.plex.get_episodes(sec_key)
                        for pe in plex_episodes:
                            rating_key = pe["rating_key"]
                            series_title = pe.get("series_title", "")
                            ep_title = pe.get("title", "")
                            season_num = pe.get("season")
                            ep_num = pe.get("episode")
                            if season_num is None or ep_num is None:
                                continue

                            plex_ids = pe.get("ids") or {}
                            plex_imdb = (plex_ids.get("imdb") or "").strip().lower()
                            plex_tmdb = str(plex_ids.get("tmdb") or "").strip()
                            plex_tvdb = str(plex_ids.get("tvdb") or "").strip()
                            is_watched_plex = pe.get("is_watched", False)
                            plex_rating = pe.get("user_rating")

                            # Match against Trakt show episodes
                            trakt_ep = None
                            if plex_imdb and (plex_imdb, season_num, ep_num) in trakt_eps_by_show_imdb:
                                trakt_ep = trakt_eps_by_show_imdb[(plex_imdb, season_num, ep_num)]
                            elif plex_tmdb and (plex_tmdb, season_num, ep_num) in trakt_eps_by_show_tmdb:
                                trakt_ep = trakt_eps_by_show_tmdb[(plex_tmdb, season_num, ep_num)]
                            elif plex_tvdb and (plex_tvdb, season_num, ep_num) in trakt_eps_by_show_tvdb:
                                trakt_ep = trakt_eps_by_show_tvdb[(plex_tvdb, season_num, ep_num)]
                            elif (series_title.lower().strip(), season_num, ep_num) in trakt_eps_by_show_title:
                                trakt_ep = trakt_eps_by_show_title[(series_title.lower().strip(), season_num, ep_num)]

                            is_watched_trakt = trakt_ep is not None and int(trakt_ep.get("plays", 0)) > 0

                            # Match against Trakt rating
                            trakt_rating = None
                            if plex_imdb and (plex_imdb, season_num, ep_num) in ep_ratings_by_show_imdb:
                                trakt_rating = ep_ratings_by_show_imdb[(plex_imdb, season_num, ep_num)]
                            elif plex_tvdb and (plex_tvdb, season_num, ep_num) in ep_ratings_by_show_tvdb:
                                trakt_rating = ep_ratings_by_show_tvdb[(plex_tvdb, season_num, ep_num)]
                            elif (series_title.lower().strip(), season_num, ep_num) in ep_ratings_by_show_title:
                                trakt_rating = ep_ratings_by_show_title[(series_title.lower().strip(), season_num, ep_num)]

                            # Check for Watched Discrepancy
                            if is_watched_trakt and not is_watched_plex:
                                diff.append({
                                    "id": f"episode:{rating_key}",
                                    "type": "episode",
                                    "title": ep_title,
                                    "series_title": series_title,
                                    "season": season_num,
                                    "episode": ep_num,
                                    "year": pe.get("year"),
                                    "rating_key": rating_key,
                                    "ids": plex_ids,
                                    "status": "trakt_only",
                                    "plex_watched": False,
                                    "trakt_watched": True,
                                    "plex_rating": plex_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "mark_plex_watched",
                                })
                            elif is_watched_plex and not is_watched_trakt:
                                diff.append({
                                    "id": f"episode:{rating_key}",
                                    "type": "episode",
                                    "title": ep_title,
                                    "series_title": series_title,
                                    "season": season_num,
                                    "episode": ep_num,
                                    "year": pe.get("year"),
                                    "rating_key": rating_key,
                                    "ids": plex_ids,
                                    "status": "plex_only",
                                    "plex_watched": True,
                                    "trakt_watched": False,
                                    "plex_rating": plex_rating,
                                    "trakt_rating": trakt_rating,
                                    "action_recommended": "sync_to_trakt",
                                })
                            elif Config.REVERSE_SYNC_RATINGS and trakt_rating is not None:
                                plex_r_int = round(plex_rating) if plex_rating is not None else None
                                if plex_r_int is None or plex_r_int != trakt_rating:
                                    diff.append({
                                        "id": f"episode:{rating_key}",
                                        "type": "episode",
                                        "title": ep_title,
                                        "series_title": series_title,
                                        "season": season_num,
                                        "episode": ep_num,
                                        "year": pe.get("year"),
                                        "rating_key": rating_key,
                                        "ids": plex_ids,
                                        "status": "rating_mismatch",
                                        "plex_watched": is_watched_plex,
                                        "trakt_watched": is_watched_trakt,
                                        "plex_rating": plex_rating,
                                        "trakt_rating": trakt_rating,
                                        "action_recommended": "sync_rating_to_plex",
                                    })

                self._last_diff = diff
                self._last_scan_time = time.time()
                logger.info("Library reconciliation scan complete: discovered %d discrepancies.", len(diff))
                return diff

            finally:
                self._is_scanning = False

    async def execute_reconciliation(
        self,
        item_ids: Optional[list[str]] = None,
        direction: str = "all",
        demo: bool = False,
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
        if item_ids:
            target_set = set(item_ids)
            target_items = [item for item in self._last_diff if item["id"] in target_set]
        else:
            target_items = list(self._last_diff)

        # Apply direction filter if specified
        if direction == "trakt_to_plex":
            target_items = [i for i in target_items if i["action_recommended"] in ("mark_plex_watched", "sync_rating_to_plex")]
        elif direction == "plex_to_trakt":
            target_items = [i for i in target_items if i["action_recommended"] in ("sync_to_trakt", "sync_rating_to_trakt")]

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
                action = item.get("action_recommended")
                rating_key = item.get("rating_key", "")
                success = False

                # Suppress loop prevention on ratingKey and all IDs before touching Plex
                if rating_key:
                    self.loop_prevention.ignore(rating_key, ttl=180.0)
                for id_val in (item.get("ids") or {}).values():
                    if id_val:
                        self.loop_prevention.ignore(str(id_val), ttl=180.0)

                try:
                    if action == "mark_plex_watched":
                        success = await self.plex.mark_as_watched(rating_key)
                    elif action == "sync_rating_to_plex":
                        val = item.get("trakt_rating")
                        if val is not None:
                            success = await self.plex.set_user_rating(rating_key, float(val))
                        else:
                            success = False
                    elif action == "sync_to_trakt":
                        # Push Plex watched item to Trakt history
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
                        # Push Plex rating to Trakt
                        plex_r = item.get("plex_rating")
                        if plex_r is not None:
                            val = int(round(plex_r))
                            if item["type"] == "movie":
                                payload = {"movies": [{"title": item["title"], "year": item.get("year"), "rating": val, "ids": item.get("ids")}]}
                            else:
                                payload = {"episodes": [{"rating": val, "ids": item.get("ids")}]}
                            res = await trakt_client.sync_ratings(payload)
                            success = not bool(res.get("error"))
                        else:
                            success = False

                except Exception as exc:
                    logger.error("Error reconciling item %s (%s): %s", item.get("id"), action, exc)
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

    async def run_startup_sync(self) -> None:
        """Run scan and reconciliation on startup in the background if configured."""
        if not Config.REVERSE_SYNC_ON_STARTUP or not self.is_configured():
            return

        logger.info("Reverse Sync: Startup scan scheduled (REVERSE_SYNC_ON_STARTUP=true). Waiting 15s for server warmup...")
        await asyncio.sleep(15.0)
        try:
            diff = await self.scan_discrepancies()
            if diff:
                logger.info("Reverse Sync: Startup scan found %d items to reconcile. Executing auto-reconcile...", len(diff))
                await self.execute_reconciliation(direction="trakt_to_plex")
        except Exception as exc:
            logger.error("Error during startup reverse sync: %s", exc)


reverse_sync_mgr = ReverseSyncManager()
