"""Cross-Tracker Watched History Importer and Two-Way Sync Engine.

Coordinates bi-directional library reconciliation and bulk synchronization
between Trakt.tv and Simkl.com across Movies, TV Shows, Anime, and Ratings.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.clients.simkl_client import SimklClient
from app.clients.trakt_client import TraktClient
from app.services.demo_manager import demo_mgr

logger = logging.getLogger("omniscrobble.cross_sync")


class CrossTrackerSyncManager:
    """Manages cross-tracker reconciliation and synchronization between Trakt and Simkl."""

    def __init__(
        self,
        trakt_client: Optional[TraktClient] = None,
        simkl_client: Optional[SimklClient] = None,
    ) -> None:
        self.trakt = trakt_client
        self.simkl = simkl_client
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

    def set_clients(self, trakt: TraktClient, simkl: SimklClient) -> None:
        self.trakt = trakt
        self.simkl = simkl

    def is_configured(self, demo: bool = False) -> bool:
        """Check if both Trakt and Simkl are authenticated and ready."""
        if demo:
            return True
        trakt_ready = self.trakt is not None and self.trakt.is_authenticated()
        simkl_ready = self.simkl is not None and self.simkl.is_authenticated()
        return trakt_ready and simkl_ready

    async def get_status(self, demo: bool = False) -> dict[str, Any]:
        """Return operational status and reconciliation metrics."""
        if demo:
            demo_diff = demo_mgr.get_demo_cross_tracker_diff()
            return {
                "configured": True,
                "trakt_authenticated": True,
                "simkl_authenticated": True,
                "is_scanning": False,
                "is_syncing": False,
                "last_scan_time": self._last_scan_time or (time.time() - 1200),
                "last_sync_time": self._last_sync_time or (time.time() - 3600),
                "diff_count": len(demo_diff),
                "diff_by_direction": {
                    "trakt_to_simkl": len([d for d in demo_diff if d.get("direction") == "trakt_to_simkl"]),
                    "simkl_to_trakt": len([d for d in demo_diff if d.get("direction") == "simkl_to_trakt"]),
                },
                "sync_progress": self._sync_progress,
            }

        trakt_auth = bool(self.trakt and self.trakt.is_authenticated())
        simkl_auth = bool(self.simkl and self.simkl.is_authenticated())
        diff_count = len(self._last_diff)

        t_to_s = len([d for d in self._last_diff if d.get("direction") == "trakt_to_simkl"])
        s_to_t = len([d for d in self._last_diff if d.get("direction") == "simkl_to_trakt"])

        return {
            "configured": trakt_auth and simkl_auth,
            "trakt_authenticated": trakt_auth,
            "simkl_authenticated": simkl_auth,
            "is_scanning": self._is_scanning,
            "is_syncing": self._is_syncing,
            "last_scan_time": self._last_scan_time,
            "last_sync_time": self._last_sync_time,
            "diff_count": diff_count,
            "diff_by_direction": {
                "trakt_to_simkl": t_to_s,
                "simkl_to_trakt": s_to_t,
            },
            "sync_progress": self._sync_progress,
        }

    async def scan_discrepancies(self, force: bool = False, demo: bool = False) -> list[dict[str, Any]]:
        """Scan both trackers, compare watched states & ratings, and compute discrepancies."""
        if demo:
            demo_diff = demo_mgr.get_demo_cross_tracker_diff()
            self._last_diff = demo_diff
            self._last_scan_time = time.time()
            return demo_diff

        if not self.is_configured():
            logger.warning("Cross-tracker scan aborted: Both Trakt and Simkl must be authenticated.")
            return []

        if self._is_scanning and not force:
            logger.info("Cross-tracker scan already in progress; returning cached diff.")
            return self._last_diff

        async with self._lock:
            self._is_scanning = True
            try:
                logger.info("Starting cross-tracker library reconciliation scan (Trakt <-> Simkl)...")
                diff: list[dict[str, Any]] = []

                # 1. Fetch Trakt Watched Movies & Shows
                trakt_movies = await self.trakt.get_watched_movies()
                trakt_shows = await self.trakt.get_watched_shows()

                # Optional Trakt Ratings
                trakt_movie_ratings = await self.trakt.get_ratings("movies")
                trakt_ep_ratings = await self.trakt.get_ratings("episodes")

                # 2. Fetch Simkl Movies, Shows, Anime
                simkl_movies_resp = await self.simkl.get_all_items("movies")
                simkl_shows_resp = await self.simkl.get_all_items("shows")
                simkl_anime_resp = await self.simkl.get_all_items("anime")

                # Extract Simkl items lists
                simkl_movies = self._extract_simkl_items(simkl_movies_resp, "movies")
                simkl_shows = self._extract_simkl_items(simkl_shows_resp, "shows")
                simkl_anime = self._extract_simkl_items(simkl_anime_resp, "anime")
                all_simkl_shows = simkl_shows + simkl_anime

                # 3. Build Lookup Maps for Trakt
                trakt_movies_by_guid: dict[str, dict[str, Any]] = {}
                for m in trakt_movies:
                    m_obj = m.get("movie") or {}
                    for key in self._get_guid_keys(m_obj.get("ids", {}), m_obj.get("title"), m_obj.get("year")):
                        trakt_movies_by_guid[key] = m

                trakt_movie_ratings_by_guid: dict[str, int] = {}
                for r in trakt_movie_ratings:
                    m_obj = r.get("movie") or {}
                    score = int(r.get("rating") or 0)
                    for key in self._get_guid_keys(m_obj.get("ids", {}), m_obj.get("title"), m_obj.get("year")):
                        trakt_movie_ratings_by_guid[key] = score

                trakt_eps_by_guid: dict[str, dict[str, Any]] = {}
                for s in trakt_shows:
                    s_obj = s.get("show") or {}
                    s_guids = self._get_guid_keys(s_obj.get("ids", {}), s_obj.get("title"), s_obj.get("year"))
                    for season in s.get("seasons", []):
                        s_num = season.get("number", 0)
                        for ep in season.get("episodes", []):
                            ep_num = ep.get("number", 0)
                            for s_guid in s_guids:
                                trakt_eps_by_guid[f"{s_guid}:s{s_num}e{ep_num}"] = ep

                trakt_ep_ratings_by_guid: dict[str, int] = {}
                for r in trakt_ep_ratings:
                    s_obj = r.get("show") or {}
                    e_obj = r.get("episode") or {}
                    score = int(r.get("rating") or 0)
                    s_guids = self._get_guid_keys(s_obj.get("ids", {}), s_obj.get("title"), s_obj.get("year"))
                    s_num = e_obj.get("season", 0)
                    ep_num = e_obj.get("number", 0)
                    for s_guid in s_guids:
                        trakt_ep_ratings_by_guid[f"{s_guid}:s{s_num}e{ep_num}"] = score

                # 4. Build Lookup Maps for Simkl
                simkl_movies_by_guid: dict[str, dict[str, Any]] = {}
                simkl_movie_ratings_by_guid: dict[str, int] = {}
                for sm in simkl_movies:
                    m_obj = sm.get("movie") or sm
                    ids = m_obj.get("ids", {})
                    guids = self._get_guid_keys(ids, m_obj.get("title"), m_obj.get("year"))
                    status = sm.get("status", "completed")
                    # Consider watched if status is completed or watched_at exists
                    if status == "completed" or sm.get("last_watched_at"):
                        for key in guids:
                            simkl_movies_by_guid[key] = sm
                    user_rating = sm.get("user_rating") or m_obj.get("user_rating")
                    if user_rating:
                        for key in guids:
                            simkl_movie_ratings_by_guid[key] = int(user_rating)

                simkl_eps_by_guid: dict[str, dict[str, Any]] = {}
                simkl_ep_ratings_by_guid: dict[str, int] = {}
                for ss in all_simkl_shows:
                    s_obj = ss.get("show") or ss.get("anime") or ss
                    ids = s_obj.get("ids", {})
                    s_guids = self._get_guid_keys(ids, s_obj.get("title"), s_obj.get("year"))

                    # Parse seasons and episodes if present
                    seasons = ss.get("seasons", [])
                    if isinstance(seasons, list):
                        for season in seasons:
                            s_num = season.get("number", 0)
                            for ep in season.get("episodes", []):
                                ep_num = ep.get("number", 0)
                                if ep.get("watched") or ep.get("completed") or ep.get("watched_at"):
                                    for s_guid in s_guids:
                                        simkl_eps_by_guid[f"{s_guid}:s{s_num}e{ep_num}"] = ep
                                ep_rating = ep.get("user_rating")
                                if ep_rating:
                                    for s_guid in s_guids:
                                        simkl_ep_ratings_by_guid[f"{s_guid}:s{s_num}e{ep_num}"] = int(ep_rating)

                    # If Simkl reports total completed show without granular episode list
                    if ss.get("status") == "completed" and not seasons:
                        for s_guid in s_guids:
                            simkl_eps_by_guid[f"{s_guid}:show_completed"] = ss

                # -------------------------------------------------------------
                # 5. Compare Trakt -> Simkl (Watched on Trakt, missing on Simkl)
                # -------------------------------------------------------------
                matched_simkl_movie_guids: set[str] = set()

                for m in trakt_movies:
                    m_obj = m.get("movie") or {}
                    ids = m_obj.get("ids", {})
                    title = m_obj.get("title") or "Unknown Movie"
                    year = m_obj.get("year")
                    guids = self._get_guid_keys(ids, title, year)

                    found = any(k in simkl_movies_by_guid for k in guids)
                    if not found:
                        primary_id = ids.get("imdb") or ids.get("tmdb") or f"{title}_{year}"
                        diff.append({
                            "id": f"diff_movie_t2s_{primary_id}",
                            "media_type": "movie",
                            "title": title,
                            "year": year,
                            "season": None,
                            "episode": None,
                            "show_title": None,
                            "direction": "trakt_to_simkl",
                            "sync_type": "watched",
                            "source_status": "watched",
                            "target_status": "unwatched",
                            "source_rating": None,
                            "target_rating": None,
                            "ids": ids,
                            "watched_at": m.get("last_watched_at"),
                        })
                    else:
                        for k in guids:
                            matched_simkl_movie_guids.add(k)

                    # Check movie ratings Trakt -> Simkl
                    trakt_rating = None
                    for k in guids:
                        if k in trakt_movie_ratings_by_guid:
                            trakt_rating = trakt_movie_ratings_by_guid[k]
                            break
                    simkl_rating = None
                    for k in guids:
                        if k in simkl_movie_ratings_by_guid:
                            simkl_rating = simkl_movie_ratings_by_guid[k]
                            break

                    if trakt_rating and (not simkl_rating or trakt_rating != simkl_rating):
                        primary_id = ids.get("imdb") or ids.get("tmdb") or f"{title}_{year}"
                        diff.append({
                            "id": f"diff_movie_rating_t2s_{primary_id}",
                            "media_type": "movie",
                            "title": title,
                            "year": year,
                            "season": None,
                            "episode": None,
                            "show_title": None,
                            "direction": "trakt_to_simkl",
                            "sync_type": "rating",
                            "source_status": f"{trakt_rating}/10",
                            "target_status": f"{simkl_rating}/10" if simkl_rating else "unrated",
                            "source_rating": trakt_rating,
                            "target_rating": simkl_rating,
                            "ids": ids,
                            "watched_at": None,
                        })

                # Trakt Shows -> Simkl
                matched_simkl_ep_guids: set[str] = set()

                for s in trakt_shows:
                    s_obj = s.get("show") or {}
                    s_ids = s_obj.get("ids", {})
                    s_title = s_obj.get("title") or "Unknown Show"
                    s_year = s_obj.get("year")
                    s_guids = self._get_guid_keys(s_ids, s_title, s_year)

                    for season in s.get("seasons", []):
                        s_num = season.get("number", 0)
                        for ep in season.get("episodes", []):
                            ep_num = ep.get("number", 0)
                            ep_keys = [f"{g}:s{s_num}e{ep_num}" for g in s_guids]
                            show_keys = [f"{g}:show_completed" for g in s_guids]

                            found = any(k in simkl_eps_by_guid for k in ep_keys + show_keys)
                            if not found:
                                primary_id = s_ids.get("imdb") or s_ids.get("tvdb") or f"{s_title}_{s_year}"
                                diff.append({
                                    "id": f"diff_ep_t2s_{primary_id}_s{s_num}e{ep_num}",
                                    "media_type": "episode",
                                    "title": f"S{s_num:02d}E{ep_num:02d}",
                                    "year": s_year,
                                    "season": s_num,
                                    "episode": ep_num,
                                    "show_title": s_title,
                                    "direction": "trakt_to_simkl",
                                    "sync_type": "watched",
                                    "source_status": "watched",
                                    "target_status": "unwatched",
                                    "source_rating": None,
                                    "target_rating": None,
                                    "ids": s_ids,
                                    "watched_at": ep.get("last_watched_at"),
                                })
                            else:
                                for k in ep_keys:
                                    matched_simkl_ep_guids.add(k)

                # -------------------------------------------------------------
                # 6. Compare Simkl -> Trakt (Watched on Simkl, missing on Trakt)
                # -------------------------------------------------------------
                for sm in simkl_movies:
                    m_obj = sm.get("movie") or sm
                    ids = m_obj.get("ids", {})
                    title = m_obj.get("title") or "Unknown Movie"
                    year = m_obj.get("year")
                    guids = self._get_guid_keys(ids, title, year)

                    status = sm.get("status", "completed")
                    if status != "completed" and not sm.get("last_watched_at"):
                        continue

                    found = any(k in trakt_movies_by_guid for k in guids)
                    if not found and not any(k in matched_simkl_movie_guids for k in guids):
                        primary_id = ids.get("imdb") or ids.get("tmdb") or f"{title}_{year}"
                        diff.append({
                            "id": f"diff_movie_s2t_{primary_id}",
                            "media_type": "movie",
                            "title": title,
                            "year": year,
                            "season": None,
                            "episode": None,
                            "show_title": None,
                            "direction": "simkl_to_trakt",
                            "sync_type": "watched",
                            "source_status": "watched",
                            "target_status": "unwatched",
                            "source_rating": None,
                            "target_rating": None,
                            "ids": ids,
                            "watched_at": sm.get("last_watched_at"),
                        })

                    # Check ratings Simkl -> Trakt
                    simkl_rating = sm.get("user_rating") or m_obj.get("user_rating")
                    if simkl_rating:
                        simkl_rating = int(simkl_rating)
                        trakt_rating = None
                        for k in guids:
                            if k in trakt_movie_ratings_by_guid:
                                trakt_rating = trakt_movie_ratings_by_guid[k]
                                break
                        if not trakt_rating or trakt_rating != simkl_rating:
                            primary_id = ids.get("imdb") or ids.get("tmdb") or f"{title}_{year}"
                            diff.append({
                                "id": f"diff_movie_rating_s2t_{primary_id}",
                                "media_type": "movie",
                                "title": title,
                                "year": year,
                                "season": None,
                                "episode": None,
                                "show_title": None,
                                "direction": "simkl_to_trakt",
                                "sync_type": "rating",
                                "source_status": f"{simkl_rating}/10",
                                "target_status": f"{trakt_rating}/10" if trakt_rating else "unrated",
                                "source_rating": simkl_rating,
                                "target_rating": trakt_rating,
                                "ids": ids,
                                "watched_at": None,
                            })

                # Simkl Shows / Anime -> Trakt
                for ss in all_simkl_shows:
                    s_obj = ss.get("show") or ss.get("anime") or ss
                    ids = s_obj.get("ids", {})
                    s_title = s_obj.get("title") or "Unknown Show"
                    s_year = s_obj.get("year")
                    s_guids = self._get_guid_keys(ids, s_title, s_year)

                    seasons = ss.get("seasons", [])
                    if isinstance(seasons, list) and seasons:
                        for season in seasons:
                            s_num = season.get("number", 0)
                            for ep in season.get("episodes", []):
                                ep_num = ep.get("number", 0)
                                if not (ep.get("watched") or ep.get("completed") or ep.get("watched_at")):
                                    continue
                                ep_keys = [f"{g}:s{s_num}e{ep_num}" for g in s_guids]
                                found = any(k in trakt_eps_by_guid for k in ep_keys)
                                if not found and not any(k in matched_simkl_ep_guids for k in ep_keys):
                                    primary_id = ids.get("imdb") or ids.get("tvdb") or f"{s_title}_{s_year}"
                                    diff.append({
                                        "id": f"diff_ep_s2t_{primary_id}_s{s_num}e{ep_num}",
                                        "media_type": "episode",
                                        "title": f"S{s_num:02d}E{ep_num:02d}",
                                        "year": s_year,
                                        "season": s_num,
                                        "episode": ep_num,
                                        "show_title": s_title,
                                        "direction": "simkl_to_trakt",
                                        "sync_type": "watched",
                                        "source_status": "watched",
                                        "target_status": "unwatched",
                                        "source_rating": None,
                                        "target_rating": None,
                                        "ids": ids,
                                        "watched_at": ep.get("watched_at"),
                                    })

                self._last_diff = diff
                self._last_scan_time = time.time()
                logger.info(
                    "Cross-tracker reconciliation completed: %d total discrepancies found (%d Trakt->Simkl, %d Simkl->Trakt).",
                    len(diff),
                    len([d for d in diff if d["direction"] == "trakt_to_simkl"]),
                    len([d for d in diff if d["direction"] == "simkl_to_trakt"]),
                )
                return diff
            except Exception as e:
                logger.error("Error during cross-tracker reconciliation scan: %s", e, exc_info=True)
                return self._last_diff
            finally:
                self._is_scanning = False

    async def execute_sync(
        self,
        item_ids: Optional[list[str]] = None,
        direction: Optional[str] = "both",
        demo: bool = False,
    ) -> dict[str, Any]:
        """Execute synchronization for specified items or by direction."""
        if demo:
            target_diff = demo_mgr.get_demo_cross_tracker_diff()
            if item_ids:
                target_diff = [d for d in target_diff if d.get("id") in item_ids]
            elif direction and direction != "both":
                target_diff = [d for d in target_diff if d.get("direction") == direction]

            count = len(target_diff)
            self._sync_progress = {
                "total": count,
                "current": count,
                "success": count,
                "failed": 0,
                "in_progress": False,
                "status": "completed",
                "message": f"Successfully synchronized {count} cross-tracker items (Demo Mode).",
            }
            self._last_sync_time = time.time()
            return self._sync_progress

        if not self.is_configured():
            return {
                "status": "error",
                "message": "Both Trakt and Simkl must be authenticated to execute sync.",
            }

        if self._is_syncing:
            return {
                "status": "in_progress",
                "message": "Cross-tracker sync is currently running.",
                "progress": self._sync_progress,
            }

        # Filter candidate items from last scan
        candidates = list(self._last_diff)
        if item_ids:
            candidates = [c for c in candidates if c.get("id") in item_ids]
        elif direction and direction != "both":
            candidates = [c for c in candidates if c.get("direction") == direction]

        if not candidates:
            return {
                "status": "completed",
                "message": "No matching discrepancies to synchronize.",
                "synced_count": 0,
            }

        total_items = len(candidates)
        self._is_syncing = True
        self._sync_progress = {
            "total": total_items,
            "current": 0,
            "success": 0,
            "failed": 0,
            "in_progress": True,
            "status": "running",
            "message": f"Starting cross-tracker synchronization for {total_items} items...",
        }

        # Run synchronization as background task
        asyncio.create_task(self._run_sync_worker(candidates))

        return {
            "status": "started",
            "message": f"Cross-tracker synchronization started for {total_items} items.",
            "progress": self._sync_progress,
        }

    async def _run_sync_worker(self, items: list[dict[str, Any]]) -> None:
        """Background worker executing batch synchronization between trackers."""
        success_ids: set[str] = set()
        failed_count = 0

        try:
            # Group items by direction and sync_type
            t2s_watched = [i for i in items if i["direction"] == "trakt_to_simkl" and i["sync_type"] == "watched"]
            t2s_ratings = [i for i in items if i["direction"] == "trakt_to_simkl" and i["sync_type"] == "rating"]
            s2t_watched = [i for i in items if i["direction"] == "simkl_to_trakt" and i["sync_type"] == "watched"]
            s2t_ratings = [i for i in items if i["direction"] == "simkl_to_trakt" and i["sync_type"] == "rating"]

            # 1. Trakt -> Simkl Watched Items
            if t2s_watched:
                simkl_payload = self._build_simkl_history_payload(t2s_watched)
                resp = await self.simkl.bulk_sync_history(simkl_payload)
                if resp.get("status") == "success":
                    for it in t2s_watched:
                        success_ids.add(it["id"])
                else:
                    failed_count += len(t2s_watched)
                self._update_progress(len(t2s_watched), len(success_ids), failed_count)

            # 2. Trakt -> Simkl Ratings
            if t2s_ratings:
                simkl_ratings_payload = self._build_simkl_ratings_payload(t2s_ratings)
                resp = await self.simkl.bulk_sync_ratings(simkl_ratings_payload)
                if resp.get("status") == "success":
                    for it in t2s_ratings:
                        success_ids.add(it["id"])
                else:
                    failed_count += len(t2s_ratings)
                self._update_progress(len(t2s_ratings), len(success_ids), failed_count)

            # 3. Simkl -> Trakt Watched Items
            if s2t_watched:
                trakt_payload = self._build_trakt_history_payload(s2t_watched)
                resp = await self.trakt.sync_history(trakt_payload)
                if isinstance(resp, dict) and "added" in resp:
                    for it in s2t_watched:
                        success_ids.add(it["id"])
                elif resp.get("status") == 200 or resp.get("status") == 201:
                    for it in s2t_watched:
                        success_ids.add(it["id"])
                else:
                    failed_count += len(s2t_watched)
                self._update_progress(len(s2t_watched), len(success_ids), failed_count)

            # 4. Simkl -> Trakt Ratings
            if s2t_ratings:
                trakt_ratings_payload = self._build_trakt_ratings_payload(s2t_ratings)
                resp = await self.trakt.sync_ratings(trakt_ratings_payload)
                if isinstance(resp, dict) and "added" in resp:
                    for it in s2t_ratings:
                        success_ids.add(it["id"])
                elif resp.get("status") == 200 or resp.get("status") == 201:
                    for it in s2t_ratings:
                        success_ids.add(it["id"])
                else:
                    failed_count += len(s2t_ratings)
                self._update_progress(len(s2t_ratings), len(success_ids), failed_count)

            # Remove resolved items from cached diff
            self._last_diff = [d for d in self._last_diff if d.get("id") not in success_ids]
            self._last_sync_time = time.time()

            self._sync_progress["status"] = "completed"
            self._sync_progress["in_progress"] = False
            self._sync_progress["message"] = (
                f"Synchronization completed: {len(success_ids)} succeeded, {failed_count} failed."
            )
            logger.info("Cross-tracker sync finished. %s", self._sync_progress["message"])

        except Exception as e:
            logger.error("Error executing cross-tracker synchronization: %s", e, exc_info=True)
            self._sync_progress["status"] = "error"
            self._sync_progress["in_progress"] = False
            self._sync_progress["message"] = f"Sync failed with error: {e}"
        finally:
            self._is_syncing = False

    def _update_progress(self, batch_size: int, success: int, failed: int) -> None:
        self._sync_progress["current"] += batch_size
        self._sync_progress["success"] = success
        self._sync_progress["failed"] = failed
        self._sync_progress["message"] = (
            f"Synchronizing items ({self._sync_progress['current']}/{self._sync_progress['total']})..."
        )

    # -------------------------------------------------------------------------
    # Payload Builders
    # -------------------------------------------------------------------------

    def _build_simkl_history_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert discrepancy items into a Simkl /sync/history payload."""
        movies: list[dict[str, Any]] = []
        shows: list[dict[str, Any]] = []

        # Group show episodes by show title and IDs
        shows_map: dict[str, dict[str, Any]] = {}

        for item in items:
            if item["media_type"] == "movie":
                m: dict[str, Any] = {
                    "title": item["title"],
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    m["year"] = item["year"]
                if item.get("watched_at"):
                    m["watched_at"] = item["watched_at"]
                movies.append(m)
            else:
                s_title = item.get("show_title") or item["title"]
                if s_title not in shows_map:
                    s_data: dict[str, Any] = {
                        "title": s_title,
                        "ids": item.get("ids", {}),
                        "seasons": [],
                    }
                    if item.get("year"):
                        s_data["year"] = item["year"]
                    shows_map[s_title] = s_data

                s_entry = shows_map[s_title]
                s_num = item.get("season", 1)
                ep_num = item.get("episode", 1)

                season_entry = next((s for s in s_entry["seasons"] if s["number"] == s_num), None)
                if not season_entry:
                    season_entry = {"number": s_num, "episodes": []}
                    s_entry["seasons"].append(season_entry)

                ep_data: dict[str, Any] = {"number": ep_num}
                if item.get("watched_at"):
                    ep_data["watched_at"] = item["watched_at"]
                season_entry["episodes"].append(ep_data)

        shows.extend(shows_map.values())
        payload: dict[str, Any] = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows
        return payload

    def _build_simkl_ratings_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert discrepancy items into a Simkl /sync/ratings payload."""
        movies: list[dict[str, Any]] = []
        shows: list[dict[str, Any]] = []

        for item in items:
            rating = item.get("source_rating") or 10
            if item["media_type"] == "movie":
                m: dict[str, Any] = {
                    "title": item["title"],
                    "rating": rating,
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    m["year"] = item["year"]
                movies.append(m)
            else:
                s_title = item.get("show_title") or item["title"]
                s: dict[str, Any] = {
                    "title": s_title,
                    "rating": rating,
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    s["year"] = item["year"]
                shows.append(s)

        payload: dict[str, Any] = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows
        return payload

    def _build_trakt_history_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert discrepancy items into a Trakt /sync/history payload."""
        movies: list[dict[str, Any]] = []
        shows_map: dict[str, dict[str, Any]] = {}

        for item in items:
            if item["media_type"] == "movie":
                m: dict[str, Any] = {
                    "title": item["title"],
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    m["year"] = item["year"]
                if item.get("watched_at"):
                    m["watched_at"] = item["watched_at"]
                movies.append(m)
            else:
                s_title = item.get("show_title") or item["title"]
                if s_title not in shows_map:
                    s_data: dict[str, Any] = {
                        "title": s_title,
                        "ids": item.get("ids", {}),
                        "seasons": [],
                    }
                    if item.get("year"):
                        s_data["year"] = item["year"]
                    shows_map[s_title] = s_data

                s_entry = shows_map[s_title]
                s_num = item.get("season", 1)
                ep_num = item.get("episode", 1)

                season_entry = next((s for s in s_entry["seasons"] if s["number"] == s_num), None)
                if not season_entry:
                    season_entry = {"number": s_num, "episodes": []}
                    s_entry["seasons"].append(season_entry)

                ep_data: dict[str, Any] = {"number": ep_num}
                if item.get("watched_at"):
                    ep_data["watched_at"] = item["watched_at"]
                season_entry["episodes"].append(ep_data)

        payload: dict[str, Any] = {}
        if movies:
            payload["movies"] = movies
        if shows_map:
            payload["shows"] = list(shows_map.values())
        return payload

    def _build_trakt_ratings_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert discrepancy items into a Trakt /sync/ratings payload."""
        movies: list[dict[str, Any]] = []
        shows: list[dict[str, Any]] = []

        for item in items:
            rating = item.get("source_rating") or 10
            if item["media_type"] == "movie":
                m: dict[str, Any] = {
                    "title": item["title"],
                    "rating": rating,
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    m["year"] = item["year"]
                movies.append(m)
            else:
                s_title = item.get("show_title") or item["title"]
                s: dict[str, Any] = {
                    "title": s_title,
                    "rating": rating,
                    "ids": item.get("ids", {}),
                }
                if item.get("year"):
                    s["year"] = item["year"]
                shows.append(s)

        payload: dict[str, Any] = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows
        return payload

    # -------------------------------------------------------------------------
    # Helper Utilities
    # -------------------------------------------------------------------------

    def _extract_simkl_items(self, resp: dict[str, Any], key: str) -> list[dict[str, Any]]:
        """Extract lists of media items from varied Simkl API responses."""
        if not isinstance(resp, dict):
            return []
        if key in resp and isinstance(resp[key], list):
            return resp[key]
        # In some endpoints, Simkl groups by status: 'completed', 'watching', etc.
        combined: list[dict[str, Any]] = []
        for status_group in ("completed", "watching", "hold", "dropped", "plantowatch", "plan_to_watch"):
            if status_group in resp and isinstance(resp[status_group], list):
                for item in resp[status_group]:
                    if isinstance(item, dict):
                        item_copy = dict(item)
                        item_copy.setdefault("status", status_group)
                        combined.append(item_copy)
        return combined

    def _get_guid_keys(
        self,
        ids: dict[str, Any],
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> list[str]:
        """Generate normalized identifier keys for matching items across trackers."""
        keys: list[str] = []
        if not isinstance(ids, dict):
            ids = {}

        imdb_id = str(ids.get("imdb") or "").strip().lower()
        if imdb_id:
            keys.append(f"imdb:{imdb_id}")

        tmdb_id = str(ids.get("tmdb") or "").strip()
        if tmdb_id:
            keys.append(f"tmdb:{tmdb_id}")

        tvdb_id = str(ids.get("tvdb") or "").strip()
        if tvdb_id:
            keys.append(f"tvdb:{tvdb_id}")

        simkl_id = str(ids.get("simkl") or "").strip()
        if simkl_id:
            keys.append(f"simkl:{simkl_id}")

        if title:
            norm_title = str(title).strip().lower()
            if year:
                keys.append(f"ty:{norm_title}:{year}")
            else:
                keys.append(f"title:{norm_title}")

        return keys
