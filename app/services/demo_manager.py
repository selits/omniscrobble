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
            "poster_url": "https://images.metahub.space/poster/medium/tt11280740/img",
            "backdrop_url": "https://images.metahub.space/background/medium/tt11280740/img",
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

    DEMO_HOUSEHOLD_RULES = [
        {
            "id": "rule_living_room",
            "name": "Family Living Room",
            "targets": ["demo_partner", "demo_kids"],
            "devices": ["Living Room Apple TV"],
            "shows": ["*"],
            "media_types": ["movie", "episode"],
            "enabled": True,
        },
        {
            "id": "rule_kids_room",
            "name": "Kids Playroom",
            "targets": ["demo_kids"],
            "devices": ["Playroom Shield"],
            "shows": ["*"],
            "media_types": ["episode"],
            "enabled": True,
        },
    ]

    def get_demo_household_rules(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.DEMO_HOUSEHOLD_RULES]

    def add_demo_household_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        new_rule = {
            "id": rule.get("id") or f"rule_{len(self.DEMO_HOUSEHOLD_RULES) + 1}",
            "name": str(rule.get("name", "New Rule")).strip(),
            "targets": [str(t).strip() for t in (rule.get("targets") or []) if str(t).strip()],
            "devices": [str(d).strip() for d in (rule.get("devices") or []) if str(d).strip()],
            "shows": [str(s).strip() for s in (rule.get("shows") or []) if str(s).strip()],
            "media_types": [str(m).strip().lower() for m in (rule.get("media_types") or []) if str(m).strip()],
            "enabled": bool(rule.get("enabled", True)),
        }
        self.DEMO_HOUSEHOLD_RULES.append(new_rule)
        return dict(new_rule)

    def delete_demo_household_rule(self, rule_id: str) -> bool:
        init_len = len(self.DEMO_HOUSEHOLD_RULES)
        self.DEMO_HOUSEHOLD_RULES = [r for r in self.DEMO_HOUSEHOLD_RULES if r["id"] != rule_id]
        return len(self.DEMO_HOUSEHOLD_RULES) < init_len

    def toggle_demo_household_rule(self, rule_id: str) -> Optional[bool]:
        for r in self.DEMO_HOUSEHOLD_RULES:
            if r["id"] == rule_id:
                r["enabled"] = not r["enabled"]
                return r["enabled"]
        return None

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
            {
                "username": "demo_kids",
                "is_default": False,
                "is_cowatch_target": False,
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

    def get_demo_tmdb_status(self) -> dict[str, Any]:
        """Return simulated TMDb connectivity status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_cinephile",
            "account_id": 9876543,
            "avatar": "https://secure.gravatar.com/avatar/demo.jpg",
        }

    def get_demo_kitsu_status(self) -> dict[str, Any]:
        """Return simulated Kitsu connectivity status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_otaku",
            "user_id": "129841",
            "avatar": "https://media.kitsu.io/users/avatars/129841/large.jpeg",
        }

    def get_demo_letterboxd_status(self) -> dict[str, Any]:
        """Return simulated Letterboxd diary status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_critic",
            "diary_count": 48,
            "last_export": "2026-10-02T18:00:00Z",
        }

    def get_demo_serializd_status(self) -> dict[str, Any]:
        """Return simulated Serializd status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_binger",
            "logged_episodes": 312,
        }

    def get_demo_mdblist_status(self) -> dict[str, Any]:
        """Return simulated MDBList status for demo previews."""
        return {
            "configured": True,
            "enabled": True,
            "authenticated": True,
            "status": "connected",
            "user": "demo_collector",
            "api_tier": "Supporter",
        }

    def get_demo_trackers_status(self) -> dict[str, Any]:
        """Return complete simulated multi-tracker diagnostics across all 4 categories."""
        trackers = {
            "trakt": {
                "id": "trakt",
                "name": "Trakt.tv",
                "category": "universal",
                "media_types": ["movie", "episode", "show"],
                "supports_realtime_scrobble": True,
                "supports_ratings": True,
                "supports_watchlist": True,
                "badge_color": "#ed1c24",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_viewer",
            },
            "simkl": {
                "id": "simkl",
                "name": "Simkl",
                "category": "universal",
                "media_types": ["movie", "episode", "show"],
                "supports_realtime_scrobble": True,
                "supports_ratings": True,
                "supports_watchlist": True,
                "badge_color": "#00aaff",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_viewer",
            },
            "tmdb": {
                "id": "tmdb",
                "name": "TMDb",
                "category": "universal",
                "media_types": ["movie", "episode", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": True,
                "badge_color": "#01d277",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_cinephile",
            },
            "anilist": {
                "id": "anilist",
                "name": "AniList",
                "category": "anime",
                "media_types": ["episode", "movie", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": False,
                "badge_color": "#02a9ff",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_otaku",
            },
            "myanimelist": {
                "id": "myanimelist",
                "name": "MyAnimeList",
                "category": "anime",
                "media_types": ["episode", "movie", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": False,
                "badge_color": "#2e51a2",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_otaku",
            },
            "kitsu": {
                "id": "kitsu",
                "name": "Kitsu",
                "category": "anime",
                "media_types": ["episode", "movie", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": False,
                "badge_color": "#fd755c",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_otaku",
            },
            "letterboxd": {
                "id": "letterboxd",
                "name": "Letterboxd",
                "category": "social_diary",
                "media_types": ["movie"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": False,
                "badge_color": "#00e054",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_critic",
            },
            "serializd": {
                "id": "serializd",
                "name": "Serializd",
                "category": "social_diary",
                "media_types": ["episode", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": False,
                "badge_color": "#ffbe1a",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_binger",
            },
            "mdblist": {
                "id": "mdblist",
                "name": "MDBList",
                "category": "lists_ratings",
                "media_types": ["movie", "episode", "show"],
                "supports_realtime_scrobble": False,
                "supports_ratings": True,
                "supports_watchlist": True,
                "badge_color": "#8b5cf6",
                "configured": True,
                "authenticated": True,
                "enabled": True,
                "status": "connected",
                "user": "demo_collector",
            },
        }
        return {
            "categories": {
                "universal": ["trakt", "simkl", "tmdb"],
                "anime": ["anilist", "myanimelist", "kitsu"],
                "social_diary": ["letterboxd", "serializd"],
                "lists_ratings": ["mdblist"],
            },
            "active_trackers": list(trackers.keys()),
            "trackers": trackers,
            **trackers,
        }

    def get_demo_cowatch_trackers(self) -> dict[str, Any]:
        return {
            "configured": True,
            "user": "demo_partner",
            "masked_user": "de******",
            "trackers": {
                "trakt": {
                    "authenticated": True,
                    "healthy": True,
                    "days_remaining": 88,
                    "status": "healthy",
                    "username": "demo_partner",
                },
                "simkl": {
                    "authenticated": True,
                    "user": "demo_partner",
                    "account_id": 654321,
                },
                "anilist": {
                    "authenticated": True,
                    "user": "demo_partner",
                    "avatar": None,
                },
                "mal": {
                    "authenticated": False,
                    "user": None,
                },
            },
        }

    def get_demo_background_sync_status(self) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.timezone.utc)
        return {
            "last_run_timestamp": (now - datetime.timedelta(hours=2)).isoformat(),
            "last_run_status": "success",
            "duration_seconds": 4.12,
            "items_reconciled": 5,
            "next_scheduled_run": (now + datetime.timedelta(hours=22)).isoformat(),
            "trigger": "scheduled",
            "errors": [],
            "is_running": False,
            "tasks": {
                "letterboxd_export": {
                    "status": "success",
                    "file": "letterboxd_diary.csv",
                    "bytes": 14280,
                },
                "server_reconciliation": {
                    "status": "success",
                    "server": "plex",
                    "items_reconciled": 3,
                },
                "cross_tracker_sync": {
                    "status": "success",
                    "items_reconciled": 2,
                },
                "arr_watchlist": {
                    "status": "success",
                    "result": {"added": 0, "checked": 12},
                },
            },
        }

    def get_demo_webhook_debug_history(self) -> list[dict[str, Any]]:
        now = datetime.datetime.now()
        return [
            {
                "id": "wh_demo001",
                "timestamp": (now - datetime.timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "iso_timestamp": (now - datetime.timedelta(minutes=3)).isoformat(),
                "source": "plex",
                "endpoint": "/webhook",
                "headers": {"host": "omniscrobble.local:8080", "user-agent": "PlexMediaServer/1.40.2"},
                "payload": {
                    "event": "media.scrobble",
                    "Account": {"title": "demo_viewer"},
                    "Server": {"title": "HomeLab-Plex"},
                    "Player": {"title": "Living Room Apple TV"},
                    "Metadata": {
                        "type": "episode",
                        "grandparentTitle": "Severance",
                        "title": "Good News About Hell",
                        "year": 2022,
                        "duration": 3600000,
                        "viewOffset": 3600000,
                    },
                },
                "status": "processed",
                "reason": "Scrobbled to Trakt + Co-Watch dual-sync (@demo_partner)",
                "event": "media.scrobble",
                "media_title": "Severance - Good News About Hell",
            },
            {
                "id": "wh_demo002",
                "timestamp": (now - datetime.timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "iso_timestamp": (now - datetime.timedelta(minutes=45)).isoformat(),
                "source": "jellyfin",
                "endpoint": "/webhook/jellyfin",
                "headers": {"host": "omniscrobble.local:8080", "user-agent": "Jellyfin-Webhook/10.9"},
                "payload": {
                    "NotificationType": "PlaybackStop",
                    "ItemType": "Movie",
                    "Name": "Dune: Part Two",
                    "Year": 2024,
                    "PlayedToCompletion": True,
                    "PlaybackPositionTicks": 99600000000,
                },
                "status": "processed",
                "reason": "Scrobbled to Trakt & Simkl (100%)",
                "event": "PlaybackStop",
                "media_title": "Dune: Part Two (2024)",
            },
            {
                "id": "wh_demo003",
                "timestamp": (now - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "iso_timestamp": (now - datetime.timedelta(hours=2)).isoformat(),
                "source": "standalone",
                "endpoint": "/api/scrobble",
                "headers": {"host": "omniscrobble.local:8080", "user-agent": "Infuse/7.7"},
                "payload": {
                    "action": "scrobble",
                    "media_type": "movie",
                    "title": "Gladiator II",
                    "year": 2024,
                    "progress": 92.5,
                    "player": "Infuse Apple TV",
                },
                "status": "processed",
                "reason": "Scrobbled to Trakt (92.5%)",
                "event": "scrobble",
                "media_title": "Gladiator II (2024)",
            },
        ]

    def get_demo_analytics_summary(self, period: str = "all") -> dict[str, Any]:
        from app.services.analytics_manager import analytics_mgr
        return analytics_mgr.get_summary(period=period, demo=True)

    def get_demo_omniwrapped(self, year: int = 2026) -> dict[str, Any]:
        from app.services.analytics_manager import analytics_mgr
        return analytics_mgr.get_omniwrapped(year=year, demo=True)


demo_mgr = DemoManager()

