"""Multi-tracker coordination service for Omniscrobble.

Dispatches scrobble and rating events across primary (Trakt) and secondary
(Simkl) tracking platforms with decoupled resilience and unified logging.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.clients.simkl_client import SimklClient
from app.clients.trakt_client import TraktClient
from app.config import Config
from app.plex_parser import ParsedMedia
from app.services.anime_resolver import AnimeResolver

logger = logging.getLogger("omniscrobble.multi_tracker")


class MultiTrackerManager:
    """Coordinates scrobbles and ratings across Trakt, Simkl, AniList, and MyAnimeList."""

    def __init__(
        self,
        config: type[Config] = Config,
        simkl_client: Optional[SimklClient] = None,
        anilist_client: Optional[AniListClient] = None,
        mal_client: Optional[MyAnimeListClient] = None,
        anime_resolver: Optional[AnimeResolver] = None,
    ) -> None:
        self.config = config
        self.simkl_client = simkl_client or SimklClient(config=config)
        self.anilist_client = anilist_client or AniListClient(config=config)
        self.mal_client = mal_client or MyAnimeListClient(config=config)
        self.anime_resolver = anime_resolver or AnimeResolver(
            config=config,
            anilist_client=self.anilist_client,
            mal_client=self.mal_client,
        )

    async def dispatch_scrobble(
        self,
        action: str,
        media: ParsedMedia,
        trakt_client: TraktClient,
        progress: float,
    ) -> dict[str, Any]:
        """Dispatch playback scrobble action (start, pause, stop) to all active trackers.
        
        Args:
            action: 'start' | 'pause' | 'stop' | 'scrobble'
            media: Standardized ParsedMedia instance
            trakt_client: Authenticated TraktClient for current user
            progress: Playback completion percentage (0.0 - 100.0)
            
        Returns:
            dict containing per-tracker dispatch statuses and list of active trackers.
        """
        results: dict[str, Any] = {
            "action": action,
            "media": f"{media.show_title or media.title} ({media.year or 'N/A'})",
            "progress": round(progress, 1),
            "trackers": ["trakt"],
            "trakt": None,
            "simkl": None,
            "anilist": None,
            "myanimelist": None,
            "is_anime": False,
        }

        # 1. Dispatch to Trakt (Primary)
        try:
            if action == "start":
                trakt_res = await trakt_client.scrobble_start(media, progress=progress)
            elif action == "pause":
                trakt_res = await trakt_client.scrobble_pause(media, progress=progress)
            else:
                trakt_res = await trakt_client.scrobble_stop(media, progress=progress)
            results["trakt"] = trakt_res
        except Exception as e:
            logger.error("Trakt scrobble dispatch failed: %s", e)
            results["trakt"] = {"status": "error", "error": str(e)}

        # 2. Dispatch to Simkl (Secondary, if configured and authenticated)
        if self.simkl_client.is_enabled() and self.simkl_client.is_authenticated():
            results["trackers"].append("simkl")
            try:
                if action == "start":
                    simkl_res = await self.simkl_client.scrobble_start(media, progress=progress)
                elif action == "pause":
                    simkl_res = await self.simkl_client.scrobble_pause(media, progress=progress)
                else:
                    simkl_res = await self.simkl_client.scrobble_stop(media, progress=progress)
                results["simkl"] = simkl_res
            except Exception as e:
                logger.error("Simkl scrobble dispatch failed: %s", e)
                results["simkl"] = {"status": "error", "error": str(e)}

        # 3. Check Anime Resolution & Dispatch to Anime Trackers (AniList & MAL)
        try:
            resolved_anime = await self.anime_resolver.resolve(media)
            if resolved_anime and resolved_anime.get("is_anime"):
                results["is_anime"] = True
                results["anime_info"] = resolved_anime

                # Only scrobble progress when completing playback or meeting threshold
                threshold = self.config.get_threshold(media.media_type)
                should_scrobble_anime = action in ("stop", "scrobble") and (progress >= threshold or action == "scrobble")

                if should_scrobble_anime:
                    # AniList Dispatch
                    if self.anilist_client.is_enabled() and self.anilist_client.is_authenticated():
                        results["trackers"].append("anilist")
                        try:
                            ani_res = await self.anilist_client.update_progress(
                                media_id=resolved_anime["anilist_id"],
                                episode=resolved_anime["episode_number"],
                                total_episodes=resolved_anime.get("episodes"),
                            )
                            results["anilist"] = ani_res
                        except Exception as e:
                            logger.error("AniList progress dispatch failed: %s", e)
                            results["anilist"] = {"status": "error", "error": str(e)}

                    # MyAnimeList Dispatch
                    if (
                        self.mal_client.is_enabled()
                        and self.mal_client.is_authenticated()
                        and resolved_anime.get("mal_id")
                    ):
                        results["trackers"].append("myanimelist")
                        try:
                            mal_res = await self.mal_client.update_progress(
                                anime_id=resolved_anime["mal_id"],
                                episode=resolved_anime["episode_number"],
                                total_episodes=resolved_anime.get("episodes"),
                            )
                            results["myanimelist"] = mal_res
                        except Exception as e:
                            logger.error("MAL progress dispatch failed: %s", e)
                            results["myanimelist"] = {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error("Anime tracking resolution/dispatch exception: %s", e)

        return results

    async def dispatch_rating(
        self,
        media: ParsedMedia,
        trakt_client: TraktClient,
        rating: int,
    ) -> dict[str, Any]:
        """Dispatch a user rating to all active trackers."""
        results: dict[str, Any] = {
            "action": "rate",
            "rating": rating,
            "trackers": ["trakt"],
            "trakt": None,
            "simkl": None,
            "anilist": None,
            "myanimelist": None,
            "is_anime": False,
        }

        # 1. Trakt Rating
        try:
            rating_payload = media.to_trakt_rating_payload(rating)
            trakt_res = await trakt_client.sync_ratings(rating_payload)
            results["trakt"] = trakt_res
        except Exception as e:
            logger.error("Trakt rating dispatch failed: %s", e)
            results["trakt"] = {"status": "error", "error": str(e)}

        # 2. Simkl Rating
        if self.simkl_client.is_enabled() and self.simkl_client.is_authenticated():
            results["trackers"].append("simkl")
            try:
                simkl_res = await self.simkl_client.sync_ratings(media, rating=rating)
                results["simkl"] = simkl_res
            except Exception as e:
                logger.error("Simkl rating dispatch failed: %s", e)
                results["simkl"] = {"status": "error", "error": str(e)}

        # 3. Anime Rating Dispatch
        try:
            resolved_anime = await self.anime_resolver.resolve(media)
            if resolved_anime and resolved_anime.get("is_anime"):
                results["is_anime"] = True
                results["anime_info"] = resolved_anime

                # AniList Rating
                if self.anilist_client.is_enabled() and self.anilist_client.is_authenticated():
                    results["trackers"].append("anilist")
                    try:
                        ani_res = await self.anilist_client.update_rating(
                            media_id=resolved_anime["anilist_id"],
                            rating=float(rating),
                        )
                        results["anilist"] = ani_res
                    except Exception as e:
                        logger.error("AniList rating dispatch failed: %s", e)
                        results["anilist"] = {"status": "error", "error": str(e)}

                # MyAnimeList Rating
                if (
                    self.mal_client.is_enabled()
                    and self.mal_client.is_authenticated()
                    and resolved_anime.get("mal_id")
                ):
                    results["trackers"].append("myanimelist")
                    try:
                        mal_res = await self.mal_client.update_rating(
                            anime_id=resolved_anime["mal_id"],
                            rating=rating,
                        )
                        results["myanimelist"] = mal_res
                    except Exception as e:
                        logger.error("MAL rating dispatch failed: %s", e)
                        results["myanimelist"] = {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error("Anime rating dispatch exception: %s", e)

        return results

    async def get_status(self) -> dict[str, Any]:
        """Return connectivity and authentication diagnostics for all supported trackers."""
        simkl_status = await self.simkl_client.check_connection()
        anilist_status = await self.anilist_client.check_connection()
        mal_status = await self.mal_client.check_connection()

        active = ["trakt"]
        if simkl_status.get("authenticated") and simkl_status.get("enabled"):
            active.append("simkl")
        if anilist_status.get("authenticated") and anilist_status.get("enabled"):
            active.append("anilist")
        if mal_status.get("authenticated") and mal_status.get("enabled"):
            active.append("myanimelist")

        return {
            "active_trackers": active,
            "trakt": {
                "configured": bool(self.config.TRAKT_CLIENT_ID),
                "name": "Trakt",
            },
            "simkl": simkl_status,
            "anilist": anilist_status,
            "myanimelist": mal_status,
        }

    async def close(self) -> None:
        """Close underlying clients."""
        await self.simkl_client.close()
        await self.anilist_client.close()
        await self.mal_client.close()

