"""Focused tests for playback session tracking and completion behavior."""

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")

def test_playback_completion_marker_survives_pause_resume():
    pm = PlaybackManager()
    media = ParsedMedia(event="media.play", username="viewer", media_type="movie", title="Example")
    pm.update_playback(media, state="playing")
    pm.mark_scrobbled(media)
    assert pm.is_scrobbled(media)

    resumed = media.model_copy(update={"event": "media.resume"})
    pm.update_playback(resumed, state="playing")
    assert pm.is_scrobbled(resumed)
    finished = pm.stop_playback(resumed)
    assert finished["scrobbled"] is True

def test_playback_interpolation_and_remaining():
    """Verify PlaybackManager real-time progress interpolation and remaining time calculation."""
    pm = PlaybackManager()
    media = ParsedMedia(
        event="media.play",
        username="selits",
        media_type="episode",
        show_title="Lanterns",
        season=1,
        episode=7,
        title="Episode 7",
        duration_ms=3600000,     # 60 minutes
        view_offset_ms=1800000,  # 30 minutes in (50%)
        progress=50.0,
    )

    # 1. Start playback
    pm.update_playback(media, state="playing")
    sessions = pm.get_active_sessions(is_admin=True)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["state"] == "playing"
    assert s["progress"] >= 50.0
    assert "left" in s["remaining_str"]

    # 2. Pause playback
    media_paused = media.model_copy(update={"event": "media.pause", "view_offset_ms": 2700000, "progress": 75.0})
    pm.update_playback(media_paused, state="paused")
    sessions_paused = pm.get_active_sessions(is_admin=True)
    assert sessions_paused[0]["state"] == "paused"
    assert "(paused)" in sessions_paused[0]["remaining_str"]

    # 3. Stop playback
    pm.stop_playback(media_paused)
    assert pm.get_active_count() == 0
    recent = pm.get_recently_finished(is_admin=True)
    assert recent is not None
    assert recent["title"] == "Lanterns S01E07 - Episode 7"
    assert recent["remaining_str"] == "Finished"


@pytest.mark.asyncio(loop_scope="module")
async def test_pause_then_stop_counts_a_playback_once(monkeypatch):
    import copy
    import app.main as main_mod

    original_stats = dict(main_mod.scrobble_stats)
    original_events = list(main_mod.recent_events)
    original_sessions = copy.deepcopy(main_mod.playback_mgr.sessions)
    original_finished = copy.deepcopy(main_mod.playback_mgr.recently_finished)
    main_mod.playback_mgr.clear()
    monkeypatch.setattr(main_mod, "save_recent_events", lambda: None)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "save_events", lambda: None)
    monkeypatch.setattr(main_mod, "save_scrobble_stats", lambda: None)
    media = ParsedMedia(
        event="media.play", username="review-test", media_type="movie", title="Single Count",
        progress=10, duration_ms=7_200_000, view_offset_ms=720_000, player="Review Player",
    )
    try:
        with patch.object(main_mod.trakt, "is_authenticated", return_value=True), \
             patch.object(main_mod.trakt, "scrobble_start", new_callable=AsyncMock, return_value={"status": "ok"}), \
             patch.object(main_mod.trakt, "scrobble_stop", new_callable=AsyncMock, return_value={"status": "ok"}) as stop, \
             patch("app.main.execute_multi_tracker_dispatch", new_callable=AsyncMock):
            await main_mod.process_media_event(media)
            paused = media.model_copy(update={"event": "media.pause", "progress": 95, "view_offset_ms": 6_840_000})
            await main_mod.process_media_event(paused)
            stopped = paused.model_copy(update={"event": "media.stop"})
            result = await main_mod.process_media_event(stopped)
            assert result["status"] == "ignored"
            assert stop.await_count == 1
    finally:
        main_mod.playback_mgr.sessions.clear()
        main_mod.playback_mgr.sessions.update(original_sessions)
        main_mod.playback_mgr.recently_finished = original_finished
        main_mod.scrobble_stats.clear()
        main_mod.scrobble_stats.update(original_stats)
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(original_events)


@pytest.mark.asyncio(loop_scope="module")
async def test_duplicate_pause_webhook_does_not_duplicate_completed_watch(monkeypatch):
    """A repeated completion webhook for one active playback session records only one watch."""
    import copy
    import app.main as main_mod

    original_stats = dict(main_mod.scrobble_stats)
    original_events = list(main_mod.recent_events)
    original_sessions = copy.deepcopy(main_mod.playback_mgr.sessions)
    original_finished = copy.deepcopy(main_mod.playback_mgr.recently_finished)
    main_mod.playback_mgr.clear()
    monkeypatch.setattr(main_mod, "save_recent_events", lambda: None)
    monkeypatch.setattr(main_mod.activity_delivery_mgr, "save_events", lambda: None)
    monkeypatch.setattr(main_mod, "save_scrobble_stats", lambda: None)
    monkeypatch.setattr(main_mod, "execute_multi_tracker_dispatch", AsyncMock())
    client = make_async_test_client(app)
    play_payload = {
        "event": "media.play", "user": True,
        "Account": {"id": 809, "title": "duplicate-test"},
        "Player": {"title": "Duplicate Test Player"},
        "Metadata": {
            "librarySectionType": "movie", "type": "movie", "title": "One Session Only",
            "year": 2025, "duration": 7_200_000, "viewOffset": 1_440_000,
        },
    }
    pause_payload = {
        **play_payload,
        "event": "media.pause",
        "Metadata": {**play_payload["Metadata"], "viewOffset": 6_840_000},
    }

    try:
        with patch.object(main_mod.trakt, "is_authenticated", return_value=True), \
             patch.object(main_mod.trakt, "scrobble_start", new_callable=AsyncMock, return_value={"status": "ok"}), \
             patch.object(main_mod.trakt, "scrobble_stop", new_callable=AsyncMock, return_value={"status": "ok"}) as stop:
            started = await client.post("/webhook", data={"payload": json.dumps(play_payload)})
            first_pause = await client.post("/webhook", data={"payload": json.dumps(pause_payload)})
            duplicate_pause = await client.post("/webhook", data={"payload": json.dumps(pause_payload)})

        assert started.status_code == 200
        assert first_pause.json()["status"] == "success"
        assert duplicate_pause.json() == {
            "status": "ignored", "reason": "Watch already recorded for this playback session"
        }
        assert stop.await_count == 1
        assert sum(event.get("title") == "One Session Only (2025)" for event in main_mod.recent_events) == 2
    finally:
        main_mod.playback_mgr.sessions.clear()
        main_mod.playback_mgr.sessions.update(original_sessions)
        main_mod.playback_mgr.recently_finished = original_finished
        main_mod.scrobble_stats.clear()
        main_mod.scrobble_stats.update(original_stats)
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(original_events)


def test_playback_manager_zero_offset_progress_estimation():
    """Verify that when playback starts at 0 offset (or view_offset_ms is None/0),
    real-time progress estimation advances as time elapses rather than staying stalled at 0%."""
    from app.services.playback_manager import PlaybackManager
    import time

    pm = PlaybackManager()
    media = ParsedMedia(
        event="media.play",
        username="selits",
        media_type="movie",
        title="The Social Network",
        year=2010,
        duration_ms=7200000,     # 120 minutes (2 hours)
        view_offset_ms=0,
        progress=0.0,
    )
    pm.update_playback(media, state="playing")

    # Simulate 38 minutes (2280 seconds) elapsed since playback began
    key = pm._get_key(media)
    pm.sessions[key]["updated_at"] = time.time() - (38 * 60)

    sessions = pm.get_active_sessions(is_admin=True)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["state"] == "playing"
    # 38 min / 120 min = 31.7%
    assert 31.0 <= s["progress"] <= 33.0
    assert any(f"{m}m left" in s["remaining_str"] for m in (81, 82, 83))

    # Also test when view_offset_ms is None in the stored session dict
    pm.sessions[key]["view_offset_ms"] = None
    sessions_none = pm.get_active_sessions(is_admin=True)
    assert len(sessions_none) == 1
    assert 31.0 <= sessions_none[0]["progress"] <= 33.0
    assert any(f"{m}m left" in sessions_none[0]["remaining_str"] for m in (81, 82, 83))

    # Shared estimator (also used by the Trakt keep-alive heartbeat) handles None offsets
    current_sec, dur_sec = PlaybackManager.estimate_position(pm.sessions[key])
    assert dur_sec == 7200.0
    assert 38 * 60 <= current_sec <= 38 * 60 + 5
    assert PlaybackManager.estimate_position({"duration_ms": None}) is None


def test_playback_manager_stop_preserves_last_known_progress():
    from app.services.playback_manager import PlaybackManager

    manager = PlaybackManager()
    playing = ParsedMedia(
        event="media.play", username="review-test", media_type="episode",
        title="Episode 2", show_title="Carrie", progress=42,
        duration_ms=2_400_000, view_offset_ms=1_008_000,
        player="Review Player",
    )
    manager.update_playback(playing, state="playing")
    stopped = playing.model_copy(update={"event": "media.stop", "progress": 0, "view_offset_ms": 0})

    finished = manager.stop_playback(stopped)

    assert finished["progress"] >= 42


def test_plex_parser_omitted_view_offset_remains_unknown():
    """Verify a missing Plex viewOffset is distinct from an explicit zero offset."""
    payload = {
        "event": "media.play",
        "user": True,
        "Account": {"title": "selits"},
        "Server": {"title": "Home-Server"},
        "Player": {"title": "Plex Web", "uuid": "client-1"},
        "Metadata": {
            "type": "movie",
            "title": "The Social Network",
            "year": 2010,
            "duration": 7200000,
        },
    }
    parsed = parse_plex_webhook(payload, allowed_users=["selits"])
    assert parsed is not None
    assert parsed.view_offset_ms is None
    assert parsed.progress == 0.0


def test_playback_manager_preserves_estimate_when_update_omits_offset():
    from app.services.playback_manager import PlaybackManager
    import time

    manager = PlaybackManager()
    media = ParsedMedia(
        event="media.play", username="review-test", media_type="movie",
        title="Progress Test", duration_ms=3_600_000, view_offset_ms=600_000,
        progress=100 / 6, player="Review Player",
    )
    manager.update_playback(media, state="playing")
    key = manager._get_key(media)
    manager.sessions[key]["updated_at"] = time.time() - 120

    update = media.model_copy(update={"event": "media.resume", "view_offset_ms": None, "progress": 0})
    session = manager.update_playback(update, state="playing")

    assert session["view_offset_ms"] >= 720_000
    assert session["progress"] >= 20


