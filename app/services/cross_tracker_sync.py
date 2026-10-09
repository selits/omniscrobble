"""Cross-Tracker Watched History Importer and Two-Way Sync Engine.

Coordinates bi-directional library reconciliation and bulk synchronization
between Trakt.tv and Simkl.com across Movies, TV Shows, Anime, and Ratings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.clients.simkl_client import SimklClient
from app.clients.tmdb_client import TMDbClient
from app.clients.trakt_client import TraktClient
from app.services.demo_manager import demo_mgr
from app.services.atomic_writer import atomic_write_json

logger = logging.getLogger("omniscrobble.cross_sync")


class CrossTrackerSyncManager:
    """Reconciles Trakt and Simkl history, plus Trakt/TMDb ratings."""

    def __init__(
        self,
        trakt_client: Optional[TraktClient] = None,
        simkl_client: Optional[SimklClient] = None,
        tmdb_client: Optional[TMDbClient] = None,
        provenance_file: Optional[Path] = None,
    ) -> None:
        self.trakt = trakt_client
        self.simkl = simkl_client
        self.tmdb = tmdb_client
        self.provenance_file = Path(provenance_file) if provenance_file else None
        self._provenance: list[dict[str, Any]] = self._load_provenance()
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

    def _load_provenance(self) -> list[dict[str, Any]]:
        if not self.provenance_file or not self.provenance_file.exists():
            return []
        try:
            payload = json.loads(self.provenance_file.read_text(encoding="utf-8"))
            records = payload.get("records", []) if isinstance(payload, dict) else []
            return [record for record in records if isinstance(record, dict)][-500:]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load cross-sync provenance: %s", exc)
            return []

    def get_provenance(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent, credential-free records of applied rating conflicts."""
        return [dict(record) for record in self._provenance[-max(1, min(limit, 500)):]][::-1]

    def _record_provenance(self, items: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for item in items:
            if not item.get("rating_conflict_key"):
                continue
            self._provenance.append({
                "item_id": item.get("id"),
                "conflict_key": item.get("rating_conflict_key"),
                "media_type": item.get("media_type"),
                "title": item.get("title"),
                "year": item.get("year"),
                "direction": item.get("direction"),
                "source": item.get("direction", "").split("_to_", 1)[0],
                "policy": item.get("resolution_policy", "manual"),
                "rating": item.get("source_rating"),
                "applied_rating": self._trakt_rating(item["source_rating"]) if item.get("direction", "").endswith("_to_trakt") else item.get("source_rating"),
                "applied_at": now,
            })
        self._provenance = self._provenance[-500:]
        if self.provenance_file:
            atomic_write_json(self.provenance_file, {"version": 1, "records": self._provenance})

    def set_clients(self, trakt: TraktClient, simkl: SimklClient, tmdb: Optional[TMDbClient] = None) -> None:
        self.trakt = trakt
        self.simkl = simkl
        if tmdb is not None:
            self.tmdb = tmdb

    def _tmdb_ratings_ready(self) -> bool:
        return bool(self.tmdb and self.tmdb.is_authenticated())

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
                "tmdb_authenticated": True,
                "is_scanning": False,
                "is_syncing": False,
                "last_scan_time": self._last_scan_time or (time.time() - 1200),
                "last_sync_time": self._last_sync_time or (time.time() - 3600),
                "diff_count": len(demo_diff),
                "diff_by_direction": {
                    "trakt_to_simkl": len([d for d in demo_diff if d.get("direction") == "trakt_to_simkl"]),
                    "simkl_to_trakt": len([d for d in demo_diff if d.get("direction") == "simkl_to_trakt"]),
                    "trakt_to_tmdb": len([d for d in demo_diff if d.get("direction") == "trakt_to_tmdb"]),
                    "tmdb_to_trakt": len([d for d in demo_diff if d.get("direction") == "tmdb_to_trakt"]),
                },
                "sync_progress": self._sync_progress,
            }

        trakt_auth = bool(self.trakt and self.trakt.is_authenticated())
        simkl_auth = bool(self.simkl and self.simkl.is_authenticated())
        tmdb_auth = self._tmdb_ratings_ready()
        diff_count = len(self._last_diff)

        t_to_s = len([d for d in self._last_diff if d.get("direction") == "trakt_to_simkl"])
        s_to_t = len([d for d in self._last_diff if d.get("direction") == "simkl_to_trakt"])

        return {
            "configured": trakt_auth and (simkl_auth or tmdb_auth),
            "trakt_authenticated": trakt_auth,
            "simkl_authenticated": simkl_auth,
            "tmdb_authenticated": tmdb_auth,
            "is_scanning": self._is_scanning,
            "is_syncing": self._is_syncing,
            "last_scan_time": self._last_scan_time,
            "last_sync_time": self._last_sync_time,
            "diff_count": diff_count,
            "diff_by_direction": {
                "trakt_to_simkl": t_to_s,
                "simkl_to_trakt": s_to_t,
                "trakt_to_tmdb": len([d for d in self._last_diff if d.get("direction") == "trakt_to_tmdb"]),
                "tmdb_to_trakt": len([d for d in self._last_diff if d.get("direction") == "tmdb_to_trakt"]),
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

        trakt_ready = bool(self.trakt and self.trakt.is_authenticated())
        simkl_ready = bool(self.simkl and self.simkl.is_authenticated())
        if not trakt_ready:
            logger.warning("Cross-tracker scan aborted: Trakt must be authenticated.")
            return []

        if self._is_scanning and not force:
            logger.info("Cross-tracker scan already in progress; returning cached diff.")
            return self._last_diff

        # The TMDb ratings path can run independently of Simkl. The original
        # Trakt–Simkl scan remains intact when both clients are available.
        if not simkl_ready:
            if not self._tmdb_ratings_ready():
                logger.warning("Cross-tracker scan needs Simkl or an authenticated TMDb account.")
                return []
            async with self._lock:
                self._is_scanning = True
                try:
                    diff = await self._scan_trakt_tmdb_ratings()
                    self._last_diff = diff
                    self._last_scan_time = time.time()
                    return diff
                except Exception as e:
                    logger.error("Trakt-to-TMDb ratings scan failed: %s", e)
                    return self._last_diff
                finally:
                    self._is_scanning = False

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
                trakt_movie_rated_at_by_guid: dict[str, str] = {}
                for r in trakt_movie_ratings:
                    m_obj = r.get("movie") or {}
                    score = int(r.get("rating") or 0)
                    for key in self._get_guid_keys(m_obj.get("ids", {}), m_obj.get("title"), m_obj.get("year")):
                        trakt_movie_ratings_by_guid[key] = score
                        if r.get("rated_at"):
                            trakt_movie_rated_at_by_guid[key] = str(r["rated_at"])

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
                simkl_movie_rated_at_by_guid: dict[str, str] = {}
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
                            rated_at = sm.get("rated_at") or m_obj.get("rated_at")
                            if rated_at:
                                simkl_movie_rated_at_by_guid[key] = str(rated_at)

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
                    rating_match_key = None
                    for k in guids:
                        if k in simkl_movie_ratings_by_guid:
                            simkl_rating = simkl_movie_ratings_by_guid[k]
                            rating_match_key = k
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
                            "rating_conflict_key": f"movie:{rating_match_key}" if simkl_rating is not None and rating_match_key else None,
                            "trakt_rated_at": next((trakt_movie_rated_at_by_guid[k] for k in guids if k in trakt_movie_rated_at_by_guid), None),
                            "simkl_rated_at": simkl_movie_rated_at_by_guid.get(rating_match_key) if rating_match_key else None,
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
                        rating_match_key = None
                        for k in guids:
                            if k in trakt_movie_ratings_by_guid:
                                trakt_rating = trakt_movie_ratings_by_guid[k]
                                rating_match_key = k
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
                                "rating_conflict_key": f"movie:{rating_match_key}" if trakt_rating is not None and rating_match_key else None,
                                "trakt_rated_at": trakt_movie_rated_at_by_guid.get(rating_match_key) if rating_match_key else None,
                                "simkl_rated_at": sm.get("rated_at") or m_obj.get("rated_at"),
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

                if self._tmdb_ratings_ready():
                    try:
                        diff.extend(await self._scan_trakt_tmdb_ratings())
                    except Exception as e:
                        logger.warning("TMDb ratings scan failed; retaining Trakt–Simkl results: %s", e)

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

    async def _scan_trakt_tmdb_ratings(self) -> list[dict[str, Any]]:
        """Build two-way Trakt/TMDb rating actions using TMDb IDs only."""
        if not (self.trakt and self.tmdb and self._tmdb_ratings_ready()):
            return []
        trakt_ratings = await self.trakt.get_ratings("movies") + await self.trakt.get_ratings("shows")
        tmdb_movies = await self.tmdb.get_rated_items("movie")
        tmdb_shows = await self.tmdb.get_rated_items("tv")
        tmdb_ratings: dict[tuple[str, str], float] = {}
        tmdb_items: dict[tuple[str, str], dict[str, Any]] = {}
        for media_type, entries in (("movie", tmdb_movies), ("show", tmdb_shows)):
            for entry in entries:
                tmdb_id = str(entry.get("id") or "").strip()
                rating = entry.get("rating")
                if tmdb_id and rating is not None:
                    key = (media_type, tmdb_id)
                    tmdb_ratings[key] = float(rating)
                    tmdb_items[key] = entry

        discrepancies: list[dict[str, Any]] = []
        trakt_keys: set[tuple[str, str]] = set()
        for entry in trakt_ratings:
            media = entry.get("movie") or entry.get("show") or {}
            ids = media.get("ids") or {}
            tmdb_id = str(ids.get("tmdb") or "").strip()
            rating = entry.get("rating")
            media_type = "movie" if entry.get("movie") else "show" if entry.get("show") else ""
            if not tmdb_id or media_type not in {"movie", "show"} or rating is None:
                continue
            key = (media_type, tmdb_id)
            trakt_keys.add(key)
            source_rating = float(rating)
            target_rating = tmdb_ratings.get(key)
            if target_rating is not None and self._trakt_rating(target_rating) == self._trakt_rating(source_rating):
                continue
            title = str(media.get("title") or "Unknown title")
            year = media.get("year")
            display = tmdb_items.get((media_type, tmdb_id), {})
            conflict_key = f"{media_type}:tmdb:{tmdb_id}" if target_rating is not None else None
            common = {
                "media_type": media_type,
                "title": title,
                "year": year,
                "sync_type": "rating",
                "ids": {**ids, "tmdb": tmdb_id},
                "match_confidence": "exact_tmdb_id",
                "match_reason": "Matched by shared TMDb ID",
                "tmdb_title": display.get("title") or display.get("name"),
                "rating_conflict_key": conflict_key,
                "trakt_rated_at": entry.get("rated_at"),
                "tmdb_rated_at": display.get("rated_at") or display.get("created_at"),
            }
            discrepancies.append({
                **common,
                "id": f"diff_rating_trakt_to_tmdb_{media_type}_{tmdb_id}",
                "direction": "trakt_to_tmdb",
                "source_status": f"{source_rating:g}/10",
                "target_status": f"{target_rating:g}/10" if target_rating is not None else "unrated",
                "source_rating": source_rating,
                "target_rating": target_rating,
                "source_of_truth": "trakt",
            })
            if target_rating is not None:
                discrepancies.append({
                    **common,
                    "id": f"diff_rating_tmdb_to_trakt_{media_type}_{tmdb_id}",
                    "direction": "tmdb_to_trakt",
                    "source_status": f"{target_rating:g}/10",
                    "target_status": f"{source_rating:g}/10",
                    "source_rating": target_rating,
                    "target_rating": source_rating,
                    "source_of_truth": "tmdb",
                })

        for (media_type, tmdb_id), source_rating in tmdb_ratings.items():
            if (media_type, tmdb_id) in trakt_keys:
                continue
            display = tmdb_items[(media_type, tmdb_id)]
            title = str(display.get("title") or display.get("name") or "Unknown title")
            discrepancies.append({
                "id": f"diff_rating_tmdb_to_trakt_{media_type}_{tmdb_id}",
                "media_type": media_type,
                "title": title,
                "year": None,
                "direction": "tmdb_to_trakt",
                "sync_type": "rating",
                "source_status": f"{source_rating:g}/10",
                "target_status": "unrated",
                "source_rating": source_rating,
                "target_rating": None,
                "ids": {"tmdb": tmdb_id},
                "match_confidence": "exact_tmdb_id",
                "match_reason": "Matched by shared TMDb ID",
                "source_of_truth": "tmdb",
                "tmdb_title": title,
                "tmdb_rated_at": display.get("rated_at") or display.get("created_at"),
            })
        return discrepancies

    @staticmethod
    def _rating_timestamp(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except (TypeError, ValueError):
            return None

    def _resolve_rating_conflicts(
        self,
        candidates: list[dict[str, Any]],
        policy: str,
        *,
        allow_manual_skip: bool,
    ) -> tuple[list[dict[str, Any]], int, Optional[str]]:
        if policy not in {"manual", "trakt", "simkl", "tmdb", "newest"}:
            return candidates, 0, "Unknown rating conflict policy."

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            key = item.get("rating_conflict_key")
            if key and item.get("sync_type") == "rating":
                grouped.setdefault(str(key), []).append(item)

        excluded_ids: set[str] = set()
        resolved: list[dict[str, Any]] = []
        skipped_groups = 0
        for key, group in grouped.items():
            directions = {str(item.get("direction")) for item in group}
            if policy == "manual":
                if len(directions) < 2:
                    continue
                if allow_manual_skip:
                    excluded_ids.update(str(item["id"]) for item in group)
                    skipped_groups += 1
                    continue
                return candidates, 0, "Both directions of a rating conflict were selected. Choose one row or select a conflict policy."

            if policy == "newest":
                detail = group[0]
                trakt_time = self._rating_timestamp(detail.get("trakt_rated_at"))
                other_tracker = "tmdb" if "tmdb" in key else "simkl"
                other_time = self._rating_timestamp(detail.get(f"{other_tracker}_rated_at"))
                if trakt_time is None or other_time is None:
                    return candidates, 0, "Newest rating policy needs rated_at timestamps from both trackers for every selected conflict."
                if trakt_time == other_time:
                    return candidates, 0, f"A rating conflict has equal timestamps; choose Trakt, {other_tracker.title()}, or manual selection."
                source = "trakt" if trakt_time > other_time else other_tracker
            else:
                source = policy

            other_tracker = "tmdb" if "tmdb" in key else "simkl"
            chosen_direction = f"{source}_to_{other_tracker}" if source == "trakt" else f"{source}_to_trakt"
            full_group = [item for item in self._last_diff if item.get("rating_conflict_key") == key and item.get("sync_type") == "rating"]
            chosen = next((item for item in full_group if item.get("direction") == chosen_direction), None)
            if chosen is None:
                return candidates, 0, f"Could not find the {source}-sourced action for rating conflict {key}. Rescan and try again."
            resolved.append(chosen)

        passthrough = [item for item in candidates if str(item.get("id")) not in excluded_ids and not item.get("rating_conflict_key")]
        if policy == "manual":
            passthrough.extend(item for item in candidates if item.get("rating_conflict_key") and str(item.get("id")) not in excluded_ids)
        else:
            passthrough.extend(item for item in candidates if not item.get("rating_conflict_key"))
            passthrough.extend(resolved)
        deduped = {str(item["id"]): item for item in passthrough}
        return list(deduped.values()), skipped_groups, None

    async def execute_sync(
        self,
        item_ids: Optional[list[str]] = None,
        direction: Optional[str] = "both",
        conflict_policy: str = "manual",
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

        candidates, skipped_conflicts, conflict_error = self._resolve_rating_conflicts(
            candidates,
            conflict_policy,
            allow_manual_skip=item_ids is None,
        )
        if conflict_error:
            return {"status": "error", "message": conflict_error}

        for item in candidates:
            if item.get("rating_conflict_key"):
                item["resolution_policy"] = conflict_policy if conflict_policy != "manual" else "manual"

        if not candidates:
            return {
                "status": "completed",
                "message": "No matching discrepancies to synchronize.",
                "synced_count": 0,
            }

        has_tmdb_actions = any(item.get("direction") in {"trakt_to_tmdb", "tmdb_to_trakt"} for item in candidates)
        has_simkl_actions = any(item.get("direction") in {"trakt_to_simkl", "simkl_to_trakt"} for item in candidates)
        if not (self.trakt and self.trakt.is_authenticated()) or (has_tmdb_actions and not self._tmdb_ratings_ready()) or (has_simkl_actions and not self.is_configured()):
            return {"status": "error", "message": "The source and destination trackers for these actions must be authenticated."}

        total_items = len(candidates)
        self._is_syncing = True
        self._sync_progress = {
            "total": total_items,
            "current": 0,
            "success": 0,
            "failed": 0,
            "in_progress": True,
            "status": "running",
            "manual_conflicts_skipped": skipped_conflicts,
            "message": f"Starting cross-tracker synchronization for {total_items} items...",
        }

        # Run synchronization as background task
        asyncio.create_task(self._run_sync_worker(candidates))

        return {
            "status": "started",
            "message": f"Cross-tracker synchronization started for {total_items} items.",
            "manual_conflicts_skipped": skipped_conflicts,
            "progress": self._sync_progress,
        }

    async def _run_sync_worker(self, items: list[dict[str, Any]]) -> None:
        """Background worker executing batch synchronization between trackers."""
        success_ids: set[str] = set()
        failed_count = 0
        provenance_warning = False

        try:
            # Group items by direction and sync_type
            t2s_watched = [i for i in items if i["direction"] == "trakt_to_simkl" and i["sync_type"] == "watched"]
            t2s_ratings = [i for i in items if i["direction"] == "trakt_to_simkl" and i["sync_type"] == "rating"]
            s2t_watched = [i for i in items if i["direction"] == "simkl_to_trakt" and i["sync_type"] == "watched"]
            s2t_ratings = [i for i in items if i["direction"] == "simkl_to_trakt" and i["sync_type"] == "rating"]
            t2tmdb_ratings = [i for i in items if i["direction"] == "trakt_to_tmdb" and i["sync_type"] == "rating"]
            tmdb2t_ratings = [i for i in items if i["direction"] == "tmdb_to_trakt" and i["sync_type"] == "rating"]

            # TMDb rating writes are intentionally sequential and limited to
            # exact TMDb-ID matches. This keeps rate-limit behavior predictable.
            for item in t2tmdb_ratings:
                if self._sync_progress["current"]:
                    await asyncio.sleep(0.05)
                ids = item.get("ids") or {}
                response = await self.tmdb.sync_rating(
                    media_type="tv" if item.get("media_type") == "show" else "movie",
                    tmdb_id=ids.get("tmdb"),
                    rating=item.get("source_rating"),
                )
                if response.get("status") == "success":
                    success_ids.add(item["id"])
                else:
                    failed_count += 1
                self._update_progress(1, len(success_ids), failed_count)

            if tmdb2t_ratings:
                trakt_payload = self._build_trakt_ratings_payload(tmdb2t_ratings)
                resp = await self.trakt.sync_ratings(trakt_payload)
                if isinstance(resp, dict) and ("added" in resp or resp.get("status") in {200, 201, "success"}):
                    success_ids.update(it["id"] for it in tmdb2t_ratings)
                else:
                    failed_count += len(tmdb2t_ratings)
                self._update_progress(len(tmdb2t_ratings), len(success_ids), failed_count)

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
            resolved_conflicts = {
                item["rating_conflict_key"] for item in items
                if item.get("id") in success_ids and item.get("rating_conflict_key")
            }
            self._last_diff = [
                d for d in self._last_diff
                if d.get("id") not in success_ids and d.get("rating_conflict_key") not in resolved_conflicts
            ]
            try:
                self._record_provenance([item for item in items if item.get("id") in success_ids])
            except Exception as exc:
                provenance_warning = True
                logger.error("Could not persist cross-sync conflict provenance: %s", exc)
            self._last_sync_time = time.time()

            self._sync_progress["status"] = "completed"
            self._sync_progress["in_progress"] = False
            skipped_conflicts = int(self._sync_progress.get("manual_conflicts_skipped", 0))
            manual_note = f" {skipped_conflicts} rating conflicts were left for manual selection." if skipped_conflicts else ""
            provenance_note = " Conflict provenance could not be saved." if provenance_warning else ""
            self._sync_progress["message"] = f"Synchronization completed: {len(success_ids)} succeeded, {failed_count} failed.{manual_note}{provenance_note}"
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

    @staticmethod
    def _trakt_rating(value: Any) -> int:
        """Round half upward and clamp to Trakt's integer scale of 1–10."""
        from decimal import Decimal, ROUND_HALF_UP
        return max(1, min(10, int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))))

    def _build_trakt_ratings_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert discrepancy items into a Trakt /sync/ratings payload."""
        movies: list[dict[str, Any]] = []
        shows: list[dict[str, Any]] = []

        for item in items:
            rating = self._trakt_rating(item.get("source_rating") or 10)
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
