import datetime
from typing import Any


class DemoManager:
    """Provides realistic simulated data for Demo Mode previews."""

    DEMO_SHOWS = [
        "A Knight of the Seven Kingdoms",
        "Fallout",
        "Lanterns",
        "Rick and Morty",
        "Severance",
        "Shōgun",
        "Succession",
        "The Expanse",
        "The Last of Us",
        "The Office",
        "The White Lotus",
        "Widow's Bay",
    ]

    SONARR_CATALOG = [
        {"title": "Severance", "year": 2022, "status": "continuing"},
        {"title": "Lanterns", "year": 2026, "status": "continuing"},
        {"title": "Succession", "year": 2018, "status": "ended"},
        {"title": "Shōgun", "year": 2024, "status": "continuing"},
        {"title": "Widow's Bay", "year": 2025, "status": "continuing"},
        {"title": "Fallout", "year": 2024, "status": "continuing"},
        {"title": "Rick and Morty", "year": 2013, "status": "continuing"},
        {"title": "The Last of Us", "year": 2023, "status": "continuing"},
        {"title": "The White Lotus", "year": 2021, "status": "continuing"},
        {"title": "The Office", "year": 2005, "status": "ended"},
        {"title": "The Expanse", "year": 2015, "status": "ended"},
        {"title": "A Knight of the Seven Kingdoms", "year": 2025, "status": "continuing"},
        {"title": "Yellowstone", "year": 2018, "status": "continuing"},
        {"title": "True Detective", "year": 2014, "status": "continuing"},
        {"title": "Better Call Saul", "year": 2015, "status": "ended"},
    ]

    def get_demo_playback(self) -> dict[str, Any]:
        return {
            "state": "playing",
            "title": "Severance S02E01 - Hello, Kier",
            "username": "demo_viewer",
            "player": "Living Room Apple TV",
            "device": "tvOS 18.2",
            "progress": 68.4,
            "trakt_url": "https://trakt.tv/search?q=Severance",
        }

    def get_demo_stats(self) -> dict[str, int]:
        return {
            "total": 1428,
            "movies": 312,
            "episodes": 1064,
            "ratings": 52,
            "collections": 148,
        }

    DEMO_DEVICES = [
        "Living Room Apple TV",
    ]

    def get_demo_cowatch_shows(self) -> list[str]:
        return list(self.DEMO_SHOWS)

    def get_demo_cowatch_devices(self) -> list[str]:
        return list(self.DEMO_DEVICES)

    def add_demo_cowatch_device(self, device: str) -> list[str]:
        clean = (device or "").strip()
        if clean and not any(d.lower() == clean.lower() for d in self.DEMO_DEVICES):
            self.DEMO_DEVICES.append(clean)
            self.DEMO_DEVICES.sort(key=lambda x: x.lower())
        return list(self.DEMO_DEVICES)

    def remove_demo_cowatch_device(self, device: str) -> list[str]:
        clean = (device or "").strip().lower()
        self.DEMO_DEVICES = [d for d in self.DEMO_DEVICES if d.strip().lower() != clean]
        return list(self.DEMO_DEVICES)

    def get_demo_users(self) -> list[dict[str, Any]]:
        return [
            {
                "username": "demo_viewer",
                "is_default": True,
                "is_cowatch_target": False,
                "authenticated": True,
            },
            {
                "username": "demo_partner",
                "is_default": False,
                "is_cowatch_target": True,
                "authenticated": True,
            },
        ]

    def get_demo_events(self) -> list[dict[str, Any]]:
        now = datetime.datetime.now()
        fmt = "%Y-%m-%d %H:%M:%S"

        def _time_ago(minutes: int) -> str:
            return (now - datetime.timedelta(minutes=minutes)).strftime(fmt)

        return [
            {
                "timestamp": _time_ago(4),
                "user": "demo_viewer",
                "event": "media.play",
                "action": "play",
                "title": "Severance S02E01 - Hello, Kier",
                "type": "episode",
                "show_title": "Severance",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Hello, Kier",
                    "year": 2025,
                    "season": 2,
                    "episode": 1,
                    "ids": {"imdb": "tt11280740"},
                },
                "progress": "68.4%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(38),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Lanterns S01E01 - Pilot",
                "type": "episode",
                "show_title": "Lanterns",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Pilot",
                    "year": 2026,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt20885210"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": True, "target": "demo_partner", "reason": "Shared show whitelist match"},
            },
            {
                "timestamp": _time_ago(85),
                "user": "demo_viewer",
                "event": "media.rate",
                "action": "rate",
                "title": "Dune: Part Two (2024)",
                "type": "movie",
                "show_title": None,
                "media_payload": {
                    "media_type": "movie",
                    "title": "Dune: Part Two",
                    "year": 2024,
                    "ids": {"imdb": "tt15239678"},
                },
                "progress": "9/10",
                "result_status": "ok",
                "cowatch_status": {"synced": False, "reason": "Movie co-watching is disabled"},
            },
            {
                "timestamp": _time_ago(190),
                "user": "demo_partner",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Succession S04E03 - Connor's Wedding",
                "type": "episode",
                "show_title": "Succession",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Connor's Wedding",
                    "year": 2023,
                    "season": 4,
                    "episode": 3,
                    "ids": {"imdb": "tt19363162"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": True, "target": "demo_viewer", "reason": "Shared show whitelist match"},
            },
            {
                "timestamp": _time_ago(320),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Widow's Bay S01E01 - Pilot",
                "type": "episode",
                "show_title": "Widow's Bay",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Pilot",
                    "year": 2025,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt21876404"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": True, "target": "demo_partner", "reason": "Shared show whitelist match"},
            },
            {
                "timestamp": _time_ago(480),
                "user": "demo_viewer",
                "event": "media.pause",
                "action": "pause",
                "title": "Shōgun S01E01 - Anjin",
                "type": "episode",
                "show_title": "Shōgun",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Anjin",
                    "year": 2024,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt8888322"},
                },
                "progress": "42.1%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(600),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "The Bear S03E01 - Tomorrow",
                "type": "episode",
                "show_title": "The Bear",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Tomorrow",
                    "year": 2024,
                    "season": 3,
                    "episode": 1,
                    "ids": {"imdb": "tt27993074"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": False, "reason": "Not in shared co-watch list"},
            },
            {
                "timestamp": _time_ago(720),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Fallout S01E01 - The End",
                "type": "episode",
                "show_title": "Fallout",
                "media_payload": {
                    "media_type": "episode",
                    "title": "The End",
                    "year": 2024,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt12637874"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": True, "target": "demo_partner", "reason": "Shared show whitelist match"},
            },
            {
                "timestamp": _time_ago(850),
                "user": "demo_viewer",
                "event": "media.rate",
                "action": "rate",
                "title": "Oppenheimer (2023)",
                "type": "movie",
                "show_title": None,
                "media_payload": {
                    "media_type": "movie",
                    "title": "Oppenheimer",
                    "year": 2023,
                    "ids": {"imdb": "tt15398776"},
                },
                "progress": "10/10",
                "result_status": "ok",
                "cowatch_status": {"synced": False, "reason": "Movie co-watching is disabled"},
            },
            {
                "timestamp": _time_ago(1000),
                "user": "demo_partner",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "The Last of Us S01E01 - When You're Lost in the Darkness",
                "type": "episode",
                "show_title": "The Last of Us",
                "media_payload": {
                    "media_type": "episode",
                    "title": "When You're Lost in the Darkness",
                    "year": 2023,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt14500888"},
                },
                "progress": "100.0%",
                "result_status": "ok",
                "cowatch_status": {"synced": True, "target": "demo_viewer", "reason": "Shared show whitelist match"},
            },
            {
                "timestamp": _time_ago(1200),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "The White Lotus S02E01 - Ciao",
                "type": "episode",
                "show_title": "The White Lotus",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Ciao",
                    "year": 2022,
                    "season": 2,
                    "episode": 1,
                    "ids": {"imdb": "tt15214040"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(1440),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "The Expanse S01E01 - Dulcinea",
                "type": "episode",
                "show_title": "The Expanse",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Dulcinea",
                    "year": 2015,
                    "season": 1,
                    "episode": 1,
                    "ids": {"imdb": "tt3463140"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(1800),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Rick and Morty S07E01 - How Poopy Got His Poop Back",
                "type": "episode",
                "show_title": "Rick and Morty",
                "media_payload": {
                    "media_type": "episode",
                    "title": "How Poopy Got His Poop Back",
                    "year": 2023,
                    "season": 7,
                    "episode": 1,
                    "ids": {"imdb": "tt29297298"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
            {
                "timestamp": _time_ago(2100),
                "user": "demo_viewer",
                "event": "media.scrobble",
                "action": "scrobble",
                "title": "Better Call Saul S06E01 - Wine and Roses",
                "type": "episode",
                "show_title": "Better Call Saul",
                "media_payload": {
                    "media_type": "episode",
                    "title": "Wine and Roses",
                    "year": 2022,
                    "season": 6,
                    "episode": 1,
                    "ids": {"imdb": "tt12154866"},
                },
                "progress": "100.0%",
                "result_status": "ok",
            },
        ]

    def get_demo_sonarr_shows(self, query: str = "") -> list[dict[str, Any]]:
        q = (query or "").lower().strip()
        if not q:
            return self.SONARR_CATALOG[:8]
        return [s for s in self.SONARR_CATALOG if q in s["title"].lower()]

    def get_demo_logs(self, lines: int = 40) -> list[str]:
        now = datetime.datetime.now()
        date_str = now.strftime("%b %d")
        logs = [
            f"{date_str} 08:00:01 mediaserver systemd[1]: Started Plex to Trakt Webhook Scrobbler.",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Token health verified (valid for 84 days).",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Multi-user co-watching enabled for partner @demo_partner.",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] plex_trakt_scrobbler: Startup Diagnostics: Sonarr integration enabled (https://sonarr.demo.internal).",
            f"{date_str} 08:00:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:00:02 [INFO] uvicorn.error: Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)",
            f"{date_str} 08:15:32 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:32 [INFO] plex_trakt_scrobbler: Webhook received: media.play for 'Lanterns S01E01 - Pilot' by user 'demo_viewer'",
            f"{date_str} 08:15:33 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:33 [INFO] plex_trakt_scrobbler: Trakt scrobble: start payload dispatched for Lanterns S01E01",
            f"{date_str} 08:15:33 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:15:33 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 08:48:12 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:12 [INFO] plex_trakt_scrobbler: Webhook received: media.scrobble for 'Lanterns S01E01 - Pilot' (progress: 100.0%)",
            f"{date_str} 08:48:12 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:12 [INFO] plex_trakt_scrobbler: Co-watching dual-sync triggering for partner @demo_partner: Lanterns",
            f"{date_str} 08:48:13 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:13 [INFO] plex_trakt_scrobbler: Co-watch dual-sync succeeded for partner @demo_partner: Lanterns",
            f"{date_str} 08:48:13 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:48:13 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 08:55:01 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:55:01 [INFO] plex_trakt_scrobbler: Webhook received: media.rate for 'Dune: Part Two (2024)' (rating: 9/10)",
            f"{date_str} 08:55:02 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 08:55:02 [INFO] plex_trakt_scrobbler: Trakt rating successfully submitted: 201 Created",
            f"{date_str} 09:02:45 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:45 [INFO] plex_trakt_scrobbler: Webhook received: media.play for 'Severance S02E01 - Hello, Kier' by user 'demo_viewer'",
            f"{date_str} 09:02:46 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:46 [INFO] plex_trakt_scrobbler: Trakt scrobble: start dispatched for Severance S02E01",
            f"{date_str} 09:02:46 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:02:46 [INFO] plex_trakt_scrobbler: Trakt scrobble response: 201 Created",
            f"{date_str} 09:18:20 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:18:20 [INFO] plex_trakt_scrobbler: Webhook received: media.pause for 'Severance S02E01 - Hello, Kier' (progress: 68.4%)",
            f"{date_str} 09:18:21 mediaserver python[5678]: {now.strftime('%Y-%m-%d')} 09:18:21 [INFO] plex_trakt_scrobbler: Trakt scrobble: pause dispatched for Severance S02E01",
        ]
        return logs[-lines:]

    def get_demo_reconciliation(self) -> list[dict[str, Any]]:
        """Return simulated library discrepancies for demo mode."""
        return [
            {
                "id": "movie:101",
                "type": "movie",
                "title": "Interstellar",
                "series_title": None,
                "season": None,
                "episode": None,
                "year": 2014,
                "rating_key": "101",
                "ids": {"imdb": "tt0816692", "tmdb": "157336"},
                "status": "trakt_only",
                "plex_watched": False,
                "trakt_watched": True,
                "plex_rating": None,
                "trakt_rating": 10,
                "action_recommended": "mark_plex_watched",
            },
            {
                "id": "movie:102",
                "type": "movie",
                "title": "Dune: Part Two",
                "series_title": None,
                "season": None,
                "episode": None,
                "year": 2024,
                "rating_key": "102",
                "ids": {"imdb": "tt15239678", "tmdb": "693134"},
                "status": "rating_mismatch",
                "plex_watched": True,
                "trakt_watched": True,
                "plex_rating": 8.0,
                "trakt_rating": 9,
                "action_recommended": "sync_rating_to_plex",
            },
            {
                "id": "episode:201",
                "type": "episode",
                "title": "Pilot",
                "series_title": "Lanterns",
                "season": 1,
                "episode": 1,
                "year": 2026,
                "rating_key": "201",
                "ids": {"imdb": "tt20885210"},
                "status": "plex_only",
                "plex_watched": True,
                "trakt_watched": False,
                "plex_rating": None,
                "trakt_rating": None,
                "action_recommended": "sync_to_trakt",
            },
            {
                "id": "episode:202",
                "type": "episode",
                "title": "Felina",
                "series_title": "Breaking Bad",
                "season": 5,
                "episode": 16,
                "year": 2013,
                "rating_key": "202",
                "ids": {"imdb": "tt2301451"},
                "status": "trakt_only",
                "plex_watched": False,
                "trakt_watched": True,
                "plex_rating": None,
                "trakt_rating": 10,
                "action_recommended": "mark_plex_watched",
            },
        ]

    def get_demo_cross_tracker_diff(self) -> list[dict[str, Any]]:
        """Return simulated cross-tracker discrepancies between Trakt and Simkl."""
        return [
            {
                "id": "diff_movie_t2s_tt15239678",
                "media_type": "movie",
                "title": "Dune: Part Two",
                "year": 2024,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "trakt_to_simkl",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt15239678", "tmdb": "693134"},
                "watched_at": "2026-09-27T21:40:00Z",
            },
            {
                "id": "diff_ep_t2s_tt11280740_s2e1",
                "media_type": "episode",
                "title": "S02E01",
                "year": 2025,
                "season": 2,
                "episode": 1,
                "show_title": "Severance",
                "direction": "trakt_to_simkl",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt11280740", "tvdb": "371980"},
                "watched_at": "2026-09-28T02:15:00Z",
            },
            {
                "id": "diff_movie_s2t_tt0245429",
                "media_type": "movie",
                "title": "Spirited Away",
                "year": 2001,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "simkl_to_trakt",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt0245429", "tmdb": "129", "simkl": "2608"},
                "watched_at": "2026-09-26T18:30:00Z",
            },
            {
                "id": "diff_ep_s2t_tt29930773_s1e1",
                "media_type": "episode",
                "title": "S01E01",
                "year": 2023,
                "season": 1,
                "episode": 1,
                "show_title": "Frieren: Beyond Journey's End",
                "direction": "simkl_to_trakt",
                "sync_type": "watched",
                "source_status": "watched",
                "target_status": "unwatched",
                "source_rating": None,
                "target_rating": None,
                "ids": {"imdb": "tt29930773", "tvdb": "424759", "simkl": "2029530"},
                "watched_at": "2026-09-25T14:10:00Z",
            },
            {
                "id": "diff_movie_rating_t2s_tt15398776",
                "media_type": "movie",
                "title": "Oppenheimer",
                "year": 2023,
                "season": None,
                "episode": None,
                "show_title": None,
                "direction": "trakt_to_simkl",
                "sync_type": "rating",
                "source_status": "10/10",
                "target_status": "unrated",
                "source_rating": 10,
                "target_rating": None,
                "ids": {"imdb": "tt15398776", "tmdb": "872585"},
                "watched_at": None,
            },
            {
                "id": "diff_ep_rating_s2t_tt2560140_s4e28",
                "media_type": "episode",
                "title": "S04E28",
                "year": 2022,
                "season": 4,
                "episode": 28,
                "show_title": "Attack on Titan",
                "direction": "simkl_to_trakt",
                "sync_type": "rating",
                "source_status": "9/10",
                "target_status": "unrated",
                "source_rating": 9,
                "target_rating": None,
                "ids": {"imdb": "tt2560140", "tvdb": "267440", "simkl": "40316"},
                "watched_at": None,
            },
        ]

    def get_demo_anilist_status(self) -> dict[str, Any]:
        """Return simulated AniList connectivity status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_otaku",
            "avatar": "https://s4.anilist.co/file/anilistcdn/user/avatar/large/default.png",
            "id": 842105,
        }

    def get_demo_mal_status(self) -> dict[str, Any]:
        """Return simulated MyAnimeList connectivity status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_otaku",
            "avatar": "https://cdn.myanimelist.net/images/userimages/default.jpg",
            "id": 1492084,
        }


demo_mgr = DemoManager()
