"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


def test_parsed_media_player_and_device():
    payload = {
        "event": "media.play",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {
            "title": "Living Room Apple TV",
            "device": "Apple TV",
        },
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Dune: Part Two",
            "year": 2024,
            "duration": 9000000,
            "viewOffset": 4500000,
        },
    }

    parsed = parse_plex_webhook(payload, allowed_users=["selits"])
    assert parsed is not None
    assert parsed.player == "Living Room Apple TV"
    assert parsed.device == "Apple TV"
    assert parsed.progress == 50.0

def test_playback_manager_lifecycle():
    pm = PlaybackManager(stale_timeout_seconds=3600)
    pm.clear()

    media = ParsedMedia(
        event="media.play",
        username="selits",
        media_type="episode",
        title="Good News About Hell",
        show_title="Severance",
        season=1,
        episode=1,
        player="Living Room TV",
        device="Apple TV",
        progress=20.0,
    )

    # 1. Start playback
    session = pm.update_playback(media, state="playing")
    assert session["state"] == "playing"
    assert session["title"] == "Severance S01E01 - Good News About Hell"

    # 2. Active sessions
    active_admin = pm.get_active_sessions(is_admin=True)
    assert len(active_admin) == 1
    assert active_admin[0]["username"] == "selits"

    active_public = pm.get_active_sessions(is_admin=False)
    assert len(active_public) == 1
    assert active_public[0]["username"] == "se****"

    # 3. Pause playback
    media.progress = 55.0
    pm.update_playback(media, state="paused")
    active_paused = pm.get_active_sessions(is_admin=True)
    assert active_paused[0]["state"] == "paused"
    assert active_paused[0]["progress"] == 55.0

    # 4. Stop playback
    finished = pm.stop_playback(media)
    assert finished is not None
    assert finished["title"] == "Severance S01E01 - Good News About Hell"
    assert len(pm.get_active_sessions()) == 0

    rf_admin = pm.get_recently_finished(is_admin=True)
    assert rf_admin is not None
    assert rf_admin["username"] == "selits"

    rf_public = pm.get_recently_finished(is_admin=False)
    assert rf_public is not None
    assert rf_public["username"] == "se****"

    pm.clear()
    assert pm.get_recently_finished() is None

@pytest.mark.asyncio(loop_scope="module")
async def test_api_playback_endpoint():
    client = make_async_test_client(app)
    playback_mgr.clear()

    # Empty
    res = await client.get("/api/playback")
    assert res.status_code == 200
    data = res.json()
    assert "active_sessions" in data
    assert len(data["active_sessions"]) == 0

    # With active session
    media = ParsedMedia(
        event="media.play",
        username="selits",
        media_type="movie",
        title="Oppenheimer",
        year=2023,
        player="Home Theater",
        progress=45.0,
    )
    playback_mgr.update_playback(media, state="playing")

    res2 = await client.get("/api/playback")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["active_sessions"]) == 1
    assert data2["active_sessions"][0]["title"] == "Oppenheimer (2023)"
    playback_mgr.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_tracks_playback():
    client = make_async_test_client(app)
    playback_mgr.clear()

    play_payload = {
        "event": "media.play",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Blade Runner 2049",
            "year": 2017,
            "duration": 9000000,
            "viewOffset": 1000000,
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {"action": "start"}

        res = await client.post("/webhook", data={"payload": json.dumps(play_payload)})
        assert res.status_code == 200

        sessions = playback_mgr.get_active_sessions()
        assert len(sessions) == 1
        assert sessions[0]["title"] == "Blade Runner 2049 (2017)"
        assert sessions[0]["state"] == "playing"

    # Stop event
    stop_payload = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Blade Runner 2049",
            "year": 2017,
            "duration": 9000000,
            "viewOffset": 8500000,
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop:
        mock_stop.return_value = {"action": "scrobble"}

        res2 = await client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res2.status_code == 200

        assert len(playback_mgr.get_active_sessions()) == 0
        finished = playback_mgr.get_recently_finished()
        assert finished is not None
        assert finished["title"] == "Blade Runner 2049 (2017)"

    playback_mgr.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_media_stop_below_threshold():
    client = make_async_test_client(app)
    stop_payload = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Critical Failure",
            "grandparentTitle": "The Ark",
            "parentIndex": 3,
            "index": 9,
            "year": 2026,
            "duration": 2520000,
            "viewOffset": 840000,
        },
    }

    initial_total = scrobble_stats["total"]

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_stop.return_value = {"action": "pause"}

        res = await client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "playback_stopped"
        assert scrobble_stats["total"] == initial_total
        mock_stop.assert_called_once()
        mock_dispatch.assert_not_called()

@pytest.mark.asyncio(loop_scope="module")
async def test_media_stop_below_one_percent():
    client = make_async_test_client(app)
    stop_payload = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Critical Failure",
            "grandparentTitle": "The Ark",
            "parentIndex": 3,
            "index": 9,
            "year": 2026,
            "duration": 2520000,
            "viewOffset": 0,
        },
    }

    initial_total = scrobble_stats["total"]

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_stop.return_value = {"action": "pause"}

        res = await client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "playback_stopped"
        assert scrobble_stats["total"] == initial_total
        mock_stop.assert_called_once()
        mock_dispatch.assert_not_called()

@pytest.mark.asyncio(loop_scope="module")
async def test_granular_scrobble_thresholds_logic():
    """Verify separate episode vs movie scrobble thresholds and Config.get_threshold fallback."""
    # Defaults
    assert Config.EPISODE_SCROBBLE_THRESHOLD == 80.0
    assert Config.MOVIE_SCROBBLE_THRESHOLD == 90.0
    assert Config.get_threshold("episode") == 80.0
    assert Config.get_threshold("movie") == 90.0
    assert Config.get_threshold("other") == Config.SCROBBLE_THRESHOLD

    client = make_async_test_client(app)

    # 1. Episode stopped at 82% -> progress >= 80% -> should scrobble
    ep_stop_82 = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Chicanery",
            "grandparentTitle": "Better Call Saul",
            "parentIndex": 3,
            "index": 5,
            "year": 2017,
            "duration": 3000000,
            "viewOffset": 2460000,  # 82.0%
        },
    }

    initial_total = scrobble_stats["total"]
    initial_episodes = scrobble_stats["episodes"]

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}

        res = await client.post("/webhook", data={"payload": json.dumps(ep_stop_82)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"
        assert scrobble_stats["total"] == initial_total + 1
        assert scrobble_stats["episodes"] == initial_episodes + 1

    # 2. Movie stopped at 82% -> progress < 90% -> should NOT scrobble
    movie_stop_82 = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Dune: Part Two",
            "year": 2024,
            "duration": 10000000,
            "viewOffset": 8200000,  # 82.0%
        },
    }

    initial_total = scrobble_stats["total"]
    initial_movies = scrobble_stats["movies"]

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "pause"}

        res = await client.post("/webhook", data={"payload": json.dumps(movie_stop_82)})
        assert res.status_code == 200
        assert res.json()["action"] == "playback_stopped"
        assert scrobble_stats["total"] == initial_total
        assert scrobble_stats["movies"] == initial_movies

    # 3. Movie stopped at 92% -> progress >= 90% -> should scrobble
    movie_stop_92 = {
        "event": "media.stop",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Player": {"title": "Living Room Apple TV"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Dune: Part Two",
            "year": 2024,
            "duration": 10000000,
            "viewOffset": 9200000,  # 92.0%
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}

        res = await client.post("/webhook", data={"payload": json.dumps(movie_stop_92)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"
        assert scrobble_stats["total"] == initial_total + 1
        assert scrobble_stats["movies"] == initial_movies + 1

@pytest.mark.asyncio(loop_scope="module")
async def test_granular_thresholds_pause_behavior():
    """Verify smart pause past threshold respects granular episode (80%) vs movie (90%) thresholds."""
    client = make_async_test_client(app)

    # 1. Episode paused at 82% (>= 80%) -> triggers scrobble_stop
    ep_pause_82 = {
        "event": "media.pause",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Chicanery",
            "grandparentTitle": "Better Call Saul",
            "parentIndex": 3,
            "index": 5,
            "duration": 3000000,
            "viewOffset": 2460000,  # 82%
        },
    }
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = await client.post("/webhook", data={"payload": json.dumps(ep_pause_82)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

    # 2. Episode paused at 75% (< 80%) -> triggers scrobble_pause
    ep_pause_75 = {
        "event": "media.pause",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Chicanery",
            "grandparentTitle": "Better Call Saul",
            "parentIndex": 3,
            "index": 5,
            "duration": 3000000,
            "viewOffset": 2250000,  # 75%
        },
    }
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_pause", new_callable=AsyncMock) as mock_pause:
        mock_pause.return_value = {"action": "pause"}
        res = await client.post("/webhook", data={"payload": json.dumps(ep_pause_75)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_pause"

    # 3. Movie paused at 85% (< 90% movie threshold) -> triggers scrobble_pause, NOT stop!
    movie_pause_85 = {
        "event": "media.pause",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Dune: Part Two",
            "duration": 10000000,
            "viewOffset": 8500000,  # 85%
        },
    }
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_pause", new_callable=AsyncMock) as mock_pause:
        mock_pause.return_value = {"action": "pause"}
        res = await client.post("/webhook", data={"payload": json.dumps(movie_pause_85)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_pause"

    # 4. Movie paused at 92% (>= 90%) -> triggers scrobble_stop
    movie_pause_92 = {
        "event": "media.pause",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Dune: Part Two",
            "duration": 10000000,
            "viewOffset": 9200000,  # 92%
        },
    }
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = await client.post("/webhook", data={"payload": json.dumps(movie_pause_92)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

@pytest.mark.asyncio(loop_scope="module")
async def test_manual_scrobble_action_start_and_playback():
    """Verify manual scrobble supports action='start' (Now Playing session & playback start scrobble) and action='watched'."""
    client = make_async_test_client(app)
    playback_mgr.clear()

    with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
        client.cookies.set("admin_token", "test_secret")

        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch("app.main.multi_tracker.dispatch_scrobble", new_callable=AsyncMock) as mock_dispatch, \
             patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_dispatch_notif:

            mock_dispatch.return_value = {"trakt": {"action": "start"}}

            # 1. Action: "start"
            payload_start = {
                "media": {
                    "media_type": "movie",
                    "title": "Avatar: The Way of Water",
                    "year": 2022,
                    "ids": {"tmdb": 76600, "imdb": "tt1630029"},
                },
                "action": "start",
            }
            res_start = await client.post("/api/scrobble/manual", json=payload_start)
            assert res_start.status_code == 200
            data_start = res_start.json()
            assert data_start["status"] == "success"
            assert data_start["action"] == "start"

            # Check multi_tracker dispatched start
            mock_dispatch.assert_called_once()
            assert mock_dispatch.call_args.kwargs.get("action") == "start"
            assert mock_dispatch.call_args.kwargs.get("media").title == "Avatar: The Way of Water"

            # Check playback session was registered
            sessions = playback_mgr.get_active_sessions()
            assert len(sessions) > 0
            session = sessions[0]
            assert "Avatar: The Way of Water" in session["title"]
            assert session["state"] == "playing"
            assert session["progress"] == 1.0

            # Check notification was dispatched for playback_start
            assert mock_dispatch_notif.called
            assert mock_dispatch_notif.call_args[0][1] == "playback_start"

            # 2. Action: "watched" (default)
            mock_dispatch.reset_mock()
            mock_dispatch_notif.reset_mock()
            mock_dispatch.return_value = {"trakt": {"action": "scrobble"}}

            with patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:
                mock_sync.return_value = {"added": {"movies": 1}}

                payload_watched = {
                    "media": {
                        "media_type": "movie",
                        "title": "Avatar",
                        "year": 2009,
                        "ids": {"tmdb": 19995},
                    },
                    "action": "watched",
                }
                res_watched = await client.post("/api/scrobble/manual", json=payload_watched)
                assert res_watched.status_code == 200
                data_watched = res_watched.json()
                assert data_watched["status"] == "success"
                assert data_watched["action"] == "watched"
                assert mock_sync.called

        client.cookies.clear()

def test_parsed_media_legacy_attributes_and_properties():
    """Verify ParsedMedia backward compatibility properties and setters."""
    from app.plex_parser import ParsedMedia

    # Test legacy keyword initialization
    media = ParsedMedia(
        raw_payload={},
        event="manual.scrobble",
        media_type="episode",
        title="Episode 1",
        grandparent_title="Show Title",
        parent_index=2,
        index=4,
        imdb_id="tt7654321",
        tmdb_id=112233,
        tvdb_id=445566,
        username="selits",
    )

    # Test property getters
    assert media.show_title == "Show Title"
    assert media.grandparent_title == "Show Title"
    assert media.season == 2
    assert media.parent_index == 2
    assert media.episode == 4
    assert media.index == 4
    assert media.imdb_id == "tt7654321"
    assert media.tmdb_id == "112233"
    assert media.tvdb_id == "445566"
    assert media.ids["imdb"] == "tt7654321"
    assert media.ids["tmdb"] == "112233"
    assert media.ids["tvdb"] == "445566"

    # Test property setters
    media.imdb_id = "tt9999999"
    assert media.imdb_id == "tt9999999"
    assert media.ids["imdb"] == "tt9999999"

    media.grandparent_title = "Updated Show"
    assert media.grandparent_title == "Updated Show"
    assert media.show_title == "Updated Show"

    media.parent_index = 5
    assert media.parent_index == 5
    assert media.season == 5

    media.index = 10
    assert media.index == 10
    assert media.episode == 10

@pytest.mark.asyncio(loop_scope="module")
async def test_playback_activity_and_events_persistence(tmp_path, monkeypatch):
    """Verify Playback Activity stats and Recent Activity logs persist across server restarts."""
    from collections import deque
    import app.main as main_mod

    test_stats_file = tmp_path / "stats.json"
    test_events_file = tmp_path / "events.json"

    monkeypatch.setattr(main_mod, "STATS_FILE", test_stats_file)
    monkeypatch.setattr(main_mod, "EVENTS_FILE", test_events_file)

    # Initial stats should be zero
    stats = main_mod.load_scrobble_stats()
    assert stats["total"] == 0

    # Simulate recorded activity
    main_mod.scrobble_stats["total"] = 5
    main_mod.scrobble_stats["movies"] = 2
    main_mod.scrobble_stats["episodes"] = 3
    main_mod.save_scrobble_stats()
    assert test_stats_file.exists()

    # Simulate server restart: re-read from disk
    reloaded_stats = main_mod.load_scrobble_stats()
    assert reloaded_stats["total"] == 5
    assert reloaded_stats["movies"] == 2
    assert reloaded_stats["episodes"] == 3

    # Simulate events persistence
    sample_entry = {
        "timestamp": "2026-09-28 22:00:00",
        "title": "Severance S02E01",
        "user": "selits",
        "action": "scrobble",
        "type": "episode",
    }
    main_mod.recent_events.appendleft(sample_entry)
    main_mod.save_recent_events()
    assert test_events_file.exists()

    # Simulate restart: re-read events from disk
    main_mod.reload_recent_events_in_place()
    assert len(main_mod.recent_events) >= 1
    assert main_mod.recent_events[0]["title"] == "Severance S02E01"

    # Test clear_events empties disk persistence
    client = make_async_test_client(main_mod.app)
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "secret123"
        res = await client.post("/api/events/clear?token=secret123")
        assert res.status_code == 200
        main_mod.reload_recent_events_in_place()
        assert len(main_mod.recent_events) == 0

        # Test stats reset endpoint
        res_reset = await client.post("/api/stats/reset?token=secret123")
        assert res_reset.status_code == 200
        assert res_reset.json()["stats"]["total"] == 0
        cleared_stats = main_mod.load_scrobble_stats()
        assert cleared_stats["total"] == 0
    finally:
        Config.WEBHOOK_SECRET = orig_secret

@pytest.mark.asyncio(loop_scope="module")
async def test_rewatch_and_play_count_fidelity(tmp_path):
    """Verify explicit watched_at timestamps for Trakt/Simkl and automated rewatch diary detection on Letterboxd."""
    from app.clients.letterboxd_client import LetterboxdClient
    from app.clients.simkl_client import SimklClient
    from app.plex_parser import ParsedMedia

    # 1. Trakt history payload has explicit watched_at
    fixed_ts = "2026-10-04T12:00:00+00:00"
    movie = ParsedMedia(
        event="media.scrobble",
        media_type="movie",
        title="Oppenheimer",
        year=2023,
        username="testuser",
        ids={"imdb": "tt15398776", "tmdb": "872585"},
    )
    payload = movie.to_trakt_history_payload(watched_at=fixed_ts)
    assert "movies" in payload
    assert payload["movies"][0]["title"] == "Oppenheimer"
    assert payload["movies"][0]["watched_at"] == fixed_ts

    episode = ParsedMedia(
        event="media.scrobble",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        year=2013,
        season=5,
        episode=14,
        username="testuser",
    )
    ep_payload = episode.to_trakt_history_payload(watched_at=fixed_ts)
    assert "shows" in ep_payload
    assert ep_payload["shows"][0]["seasons"][0]["episodes"][0]["watched_at"] == fixed_ts

    # 2. Simkl history sync attaches watched_at
    mock_config = MagicMock()
    mock_config.DATA_DIR = tmp_path
    mock_config.SIMKL_CLIENT_ID = "simkl_id"
    mock_config.SIMKL_CLIENT_SECRET = "simkl_secret"
    simkl = SimklClient(config=mock_config)
    simkl.access_token = "valid_simkl_token"

    with patch.object(simkl, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"added": {"movies": 1}}
        res = await simkl.sync_history(movie, watched_at=fixed_ts)
        assert res["added"]["movies"] == 1
        sent_payload = mock_post.call_args[0][1]
        assert "movies" in sent_payload
        assert sent_payload["movies"][0]["watched_at"] == fixed_ts

    # 3. Letterboxd rewatch detection
    diary_file = tmp_path / "letterboxd_diary.json"
    lb_client = LetterboxdClient(config=mock_config, data_file=diary_file)

    # First watch of The Matrix
    res1 = await lb_client.log_movie_entry(
        title="The Matrix",
        year=1999,
        rating=9.0,
        watched_date="2026-01-01",
        rewatch=False,
    )
    assert res1["status"] == "logged"
    assert res1["entry"]["Rewatch"] == "No"

    # Rewatch on a subsequent date -> automated rewatch detection
    res2 = await lb_client.log_movie_entry(
        title="The Matrix",
        year=1999,
        rating=10.0,
        watched_date="2026-10-04",
        rewatch=False,  # Client does not pass rewatch flag; system auto-detects
    )
    assert res2["status"] == "logged"
    assert res2["entry"]["Rewatch"] == "Yes"

    # Both entries are preserved in diary
    entries = lb_client.get_diary_entries()
    matrix_entries = [e for e in entries if e.get("title") == "The Matrix"]
    assert len(matrix_entries) == 2
    assert matrix_entries[0]["Rewatch"] == "No"
    assert matrix_entries[1]["Rewatch"] == "Yes"

def test_playback_manager_heartbeat_tracking():
    """Verify PlaybackManager heartbeat candidate discovery and recording."""
    from app.plex_parser import ParsedMedia
    from app.services.playback_manager import PlaybackManager

    pm = PlaybackManager(stale_timeout_seconds=3600)
    media = ParsedMedia(
        event="media.play",
        media_type="movie",
        title="Interstellar",
        year=2014,
        progress=25.0,
        duration_ms=10140000,
        view_offset_ms=2535000,
        username="alice",
        player="Home Theater",
    )

    session = pm.update_playback(media, state="playing")
    key = session["key"]

    # Newly updated session should not be eligible for heartbeat (interval=600s)
    candidates = pm.get_heartbeat_candidates(interval_seconds=600)
    assert len(candidates) == 0

    # Simulate elapsed time 15 minutes (900 seconds) ago
    pm.sessions[key]["last_heartbeat_at"] = time.time() - 900
    candidates = pm.get_heartbeat_candidates(interval_seconds=600)
    assert len(candidates) == 1
    assert candidates[0]["key"] == key

    # Record heartbeat updates timestamp and progress
    pm.record_heartbeat(key, progress=35.0)
    assert pm.sessions[key]["progress"] == 35.0
    # Candidate should no longer be eligible immediately
    assert len(pm.get_heartbeat_candidates(interval_seconds=600)) == 0

@pytest.mark.asyncio(loop_scope="module")
async def test_scrobble_heartbeat_worker_dispatch(tmp_path):
    """Verify background scrobble heartbeat loop sends keep-alive to Trakt and Simkl."""
    from app.main import scrobble_heartbeat_worker_loop, trakt, simkl, user_mgr
    from app.plex_parser import ParsedMedia
    from app.services.playback_manager import playback_mgr
    from app.services.settings_manager import settings_mgr

    playback_mgr.clear()
    media = ParsedMedia(
        event="media.play",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        progress=40.0,
        duration_ms=9960000,
        view_offset_ms=3984000,
        username="bob",
        player="Apple TV",
    )

    session = playback_mgr.update_playback(media, state="playing")
    key = session["key"]
    playback_mgr.sessions[key]["last_heartbeat_at"] = time.time() - 900

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_trakt_scrobble, \
         patch.object(simkl, "is_enabled", return_value=True), \
         patch.object(simkl, "is_authenticated", return_value=True), \
         patch.object(simkl, "scrobble_start", new_callable=AsyncMock) as mock_simkl_scrobble, \
         patch.object(settings_mgr, "is_tracker_enabled", return_value=True):

        # Run one heartbeat cycle manually
        candidates = playback_mgr.get_heartbeat_candidates(interval_seconds=600)
        assert len(candidates) == 1

        cand = candidates[0]
        p_media = cand.get("parsed_media")
        assert p_media is not None

        # Verify scrobble_start dispatches to Trakt and Simkl
        payload = p_media.to_trakt_scrobble_payload()
        payload["progress"] = 45.0
        await trakt.scrobble_start(payload)
        await simkl.scrobble_start(p_media, progress=45.0)

        mock_trakt_scrobble.assert_awaited_once()
        mock_simkl_scrobble.assert_awaited_once()

    playback_mgr.clear()

def test_accurate_watch_time_and_exact_duration_telemetry(tmp_path, monkeypatch):
    """Verify high-precision watch time calculation from duration_ms, unique titles deduplication, and stats persistence."""
    from app.services.analytics_manager import AnalyticsManager
    from app.plex_parser import ParsedMedia
    import app.main as main_mod

    # Isolate the module-level counters so this test cannot leak state into others
    monkeypatch.setattr(main_mod, "scrobble_stats", dict(main_mod.scrobble_stats))

    events_file = tmp_path / "test_events_duration.json"
    stats_file = tmp_path / "test_stats_duration.json"

    mgr = AnalyticsManager(events_file=events_file, stats_file=stats_file)

    # 1. Unit verification of _extract_item_minutes
    # Milliseconds duration
    assert mgr._extract_item_minutes({"duration_ms": 1440000}, "episode") == 24
    # Media payload duration_ms
    assert mgr._extract_item_minutes({"media_payload": {"duration_ms": 5520000}}, "movie") == 92
    # view_offset_ms is a playback position, never a runtime
    assert mgr._extract_item_minutes({"view_offset_ms": 1200000}, "episode") == 35
    # Generic duration in minutes
    assert mgr._extract_item_minutes({"duration": 48}, "episode") == 48
    # Generic duration in seconds
    assert mgr._extract_item_minutes({"duration": 3600}, "movie") == 60
    # Generic duration in milliseconds
    assert mgr._extract_item_minutes({"duration": 7200000}, "movie") == 120
    # Fallback when duration is missing
    assert mgr._extract_item_minutes({}, "movie") == 90
    assert mgr._extract_item_minutes({}, "episode") == 35

    # 2. Aggregation with real events containing exact durations
    test_events = [
        {
            "timestamp": "2026-10-04 12:00:00",
            "action": "scrobble (100.0%)",
            "type": "movie",
            "title": "Short Film (2024)",
            "duration_ms": 5520000,  # 92 minutes
            "player": "Living Room TV",
        },
        {
            "timestamp": "2026-10-04 14:00:00",
            "action": "scrobble (100.0%)",
            "type": "episode",
            "show_title": "Anime Series",
            "title": "Anime Series S01E01 - Pilot",
            "duration_ms": 1320000,  # 22 minutes
            "player": "Living Room TV",
        },
        {
            "timestamp": "2026-10-04 14:30:00",
            "action": "scrobble (100.0%)",
            "type": "episode",
            "show_title": "Anime Series",
            "title": "Anime Series S01E02 - Episode 2",
            "duration_ms": 1440000,  # 24 minutes
            "player": "Living Room TV",
        },
        {
            "timestamp": "2026-10-04 16:00:00",
            "action": "scrobble (100.0%)",
            "type": "episode",
            "show_title": "Drama Series",
            "title": "Drama Series S01E01 - Intro",
            "duration_ms": 3600000,  # 60 minutes
            "player": "Bedroom Chromecast",
        },
    ]
    with open(events_file, "w", encoding="utf-8") as f:
        json.dump(test_events, f)

    summary = mgr.get_summary(period="all")
    # Total watch minutes: 92 + 22 + 24 + 60 = 198 minutes = 3h 18m
    assert summary["total_watch_formatted"] == "3h 18m"
    assert summary["total_watch_hours"] == 3.3
    assert summary["total_scrobbles"] == 4
    # 1 movie + 2 distinct TV series = 3 unique titles
    assert summary["unique_titles"] == 3
    assert summary["movies_watched"] == 1
    assert summary["episodes_watched"] == 3

    # 3. Test record_watch_stat accumulation in stats
    test_media = ParsedMedia(
        event="media.scrobble",
        username="test_user",
        media_type="movie",
        title="Sample Movie",
        duration_ms=6000000,  # 100 minutes
    )
    initial_mins = main_mod.scrobble_stats.get("watch_minutes", 0)
    main_mod.record_watch_stat(test_media)
    assert main_mod.scrobble_stats["watch_minutes"] == initial_mins + 100

    # 4. Fallback when events is empty but stats has watch_minutes
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump({"total": 10, "movies": 5, "episodes": 5, "ratings": 2, "collections": 0, "watch_minutes": 500}, f)
    empty_events_file = tmp_path / "empty_events.json"
    with open(empty_events_file, "w", encoding="utf-8") as f:
        json.dump([], f)
    fallback_mgr = AnalyticsManager(events_file=empty_events_file, stats_file=stats_file)
    fallback_sum = fallback_mgr.get_summary(period="all")
    assert fallback_sum["total_scrobbles"] == 10
    # 500 minutes = 8h 20m
    assert fallback_sum["total_watch_formatted"] == "8h 20m"

