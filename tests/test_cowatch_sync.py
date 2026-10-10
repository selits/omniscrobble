"""Focused tests for Co-Watch tracker delivery and partner configuration."""

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")

@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_trackers_endpoints():
    """Verify /api/cowatch/trackers status and demo endpoints."""
    from app.main import app
    from app.config import Config

    client = make_async_test_client(app)

    # Demo mode
    res_demo = await client.get("/api/cowatch/trackers?demo=true")
    assert res_demo.status_code == 200
    demo_data = res_demo.json()
    assert demo_data["configured"] is True
    assert demo_data["user"] == "demo_partner"
    assert "trakt" in demo_data["trackers"]
    assert "simkl" in demo_data["trackers"]

    # Real mode without partner configured
    with patch.object(Config, "CO_WATCH_USER", None):
        res_none = await client.get("/api/cowatch/trackers")
        assert res_none.status_code == 200
        assert res_none.json()["configured"] is False

    # Real mode with partner configured
    with patch.object(Config, "CO_WATCH_USER", "partner_bob"):
        res = await client.get("/api/cowatch/trackers")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert data["user"] == "partner_bob"
        assert "trackers" in data


@pytest.mark.asyncio(loop_scope="module")
async def test_partner_secondary_tracker_endpoints(tmp_path, monkeypatch):
    """Verify partner user authentication endpoints for Simkl, AniList, and MAL."""
    from app.main import app
    from app.services.user_manager import user_mgr
    from app.config import Config

    client = make_async_test_client(app)
    monkeypatch.setattr(user_mgr, "tokens_dir", tmp_path)

    # 1. Partner Simkl endpoints
    with patch("app.clients.simkl_client.SimklClient.get_device_pin", new_callable=AsyncMock) as mock_pin:
        mock_pin.return_value = {"user_code": "ABCD-1234", "verification_url": "https://simkl.com/pin"}
        res = await client.post("/api/simkl/pin?user=partner_test")
        assert res.status_code == 200
        assert res.json()["user_code"] == "ABCD-1234"

    with patch("app.clients.simkl_client.SimklClient.poll_device_pin", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"result": "OK", "access_token": "simkl_partner_token"}
        res = await client.post("/api/simkl/poll", json={"user_code": "ABCD-1234", "user": "partner_test"})
        assert res.status_code == 200
        assert res.json()["result"] == "OK"

    # Status for partner
    with patch("app.clients.simkl_client.SimklClient.check_connection", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = {"status": "connected", "user": "partner_test"}
        res = await client.get("/api/simkl/status?user=partner_test")
        assert res.status_code == 200
        assert res.json()["user"] == "partner_test"

    # Disconnect partner Simkl
    res = await client.post("/api/simkl/disconnect?user=partner_test")
    assert res.status_code == 200
    assert "partner_test" in res.json()["message"]

    # 2. Partner AniList endpoints
    with patch("app.clients.anilist_client.AniListClient.check_connection", new_callable=AsyncMock) as mock_ani_conn:
        mock_ani_conn.return_value = {"status": "connected", "user": "partner_ani_user", "id": 12345}
        res = await client.post("/api/anilist/token", json={"token": "partner_ani_jwt", "user": "partner_test"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        assert res.json()["user"] == "partner_ani_user"

    res = await client.post("/api/anilist/disconnect?user=partner_test")
    assert res.status_code == 200

    # 3. Partner MAL endpoints
    with patch("app.clients.mal_client.MyAnimeListClient.check_connection", new_callable=AsyncMock) as mock_mal_conn:
        mock_mal_conn.return_value = {"status": "connected", "user": "partner_mal_user", "id": 67890}
        res = await client.post("/api/mal/token", json={"token": "partner_mal_bearer", "user": "partner_test"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"
        assert res.json()["user"] == "partner_mal_user"

    res = await client.post("/api/mal/disconnect?user=partner_test")
    assert res.status_code == 200


@pytest.mark.asyncio(loop_scope="module")
async def test_execute_cowatch_sync_multi_tracker_dual_dispatch_and_failure_isolation():
    """Verify co-watch dual dispatch sends to all authenticated partner trackers and isolates failures."""
    from app.main import execute_cowatch_sync
    from app.plex_parser import ParsedMedia
    from app.services.user_manager import user_mgr
    from app.config import Config

    parsed = ParsedMedia(
        username="selits",
        media_type="episode",
        action="scrobble",
        event="media.scrobble",
        title="Severance S02E01",
        show_title="Severance",
        season=2,
        episode=1,
        year=2025,
        progress=100.0,
    )

    partner_trakt_mock = MagicMock()
    partner_trakt_mock.is_authenticated.return_value = True
    partner_trakt_mock.sync_history = AsyncMock(side_effect=Exception("Trakt 500 Server Error"))

    partner_simkl_mock = MagicMock()
    partner_simkl_mock.is_authenticated.return_value = True
    partner_simkl_mock.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})

    partner_ani_mock = MagicMock()
    partner_ani_mock.is_authenticated.return_value = True

    with patch.object(Config, "CO_WATCH_USER", "partner_charlie"), \
         patch.object(user_mgr, "get_client", return_value=partner_trakt_mock), \
         patch.object(user_mgr, "get_tracker_client", side_effect=lambda u, t: partner_simkl_mock if t == "simkl" else partner_ani_mock):

        # Even though Trakt raises an Exception, execute_cowatch_sync should not crash and Simkl should still execute!
        await execute_cowatch_sync(parsed, "scrobble_stop")

        partner_trakt_mock.sync_history.assert_called_once()
        partner_simkl_mock.scrobble_stop.assert_called_once()


@pytest.mark.asyncio(loop_scope="module")
async def test_execute_cowatch_sync_queues_history_for_unauthenticated_partner():
    from app.main import execute_cowatch_sync, queue_mgr
    from app.plex_parser import ParsedMedia
    from app.services.user_manager import user_mgr
    from app.config import Config

    parsed = ParsedMedia(
        event="media.scrobble", username="viewer", media_type="episode",
        title="Episode 2", show_title="Carrie", season=1, episode=2,
        progress=100,
    )
    disconnected = MagicMock()
    disconnected.is_authenticated.return_value = False
    other_tracker = MagicMock()
    other_tracker.is_authenticated.return_value = False

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(user_mgr, "get_client", return_value=disconnected), \
         patch.object(user_mgr, "get_tracker_client", return_value=other_tracker), \
         patch.object(queue_mgr, "enqueue") as enqueue:
        await execute_cowatch_sync(parsed, "mark_watched", targets=["partner"])

    enqueue.assert_called_once()
    args, kwargs = enqueue.call_args
    assert args[0] == "sync_history"
    assert kwargs["username"] == "partner"
    assert "not authenticated" in kwargs["error"]


@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_trakt_retries_unmatched_episode_id_with_show_coordinates():
    from app.main import execute_cowatch_sync
    from app.plex_parser import ParsedMedia
    from app.services.user_manager import user_mgr
    from app.config import Config

    parsed = ParsedMedia(
        event="media.scrobble", username="viewer", media_type="episode",
        title="Carrie S01E02", show_title="Carrie", season=1, episode=2,
        show_year=1976, ids={"tvdb": "episode-id"}, progress=100,
    )
    trakt = MagicMock()
    trakt.is_authenticated.return_value = True
    trakt.sync_history = AsyncMock(side_effect=[
        {"added": {"episodes": 0}, "not_found": {"episodes": [{"ids": {"tvdb": "episode-id"}}]}},
        {"added": {"episodes": 1}, "not_found": {"episodes": []}},
    ])
    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(user_mgr, "get_client", return_value=trakt), \
         patch.object(user_mgr, "get_tracker_client", return_value=None):
        result = await execute_cowatch_sync(parsed, "mark_watched", targets=["partner"], selected_trackers=["trakt"])

    assert result == "success"
    assert trakt.sync_history.await_count == 2
    fallback_payload = trakt.sync_history.await_args_list[1].args[0]
    assert fallback_payload["shows"][0]["title"] == "Carrie"
    assert fallback_payload["shows"][0]["seasons"][0]["episodes"][0]["number"] == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_trakt_reports_unmatched_episode_instead_of_success():
    from app.main import execute_cowatch_sync
    from app.plex_parser import ParsedMedia
    from app.services.user_manager import user_mgr
    from app.config import Config

    parsed = ParsedMedia(
        event="media.scrobble", username="viewer", media_type="episode",
        title="Carrie S01E02", show_title="Carrie", season=1, episode=2,
        ids={"tvdb": "episode-id"}, progress=100,
    )
    trakt = MagicMock()
    trakt.is_authenticated.return_value = True
    unmatched = {"added": {"episodes": 0}, "not_found": {"episodes": [{"ids": {"tvdb": "episode-id"}}]}}
    trakt.sync_history = AsyncMock(return_value=unmatched)
    details = []
    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(user_mgr, "get_client", return_value=trakt), \
         patch.object(user_mgr, "get_tracker_client", return_value=None):
        result = await execute_cowatch_sync(
            parsed, "mark_watched", targets=["partner"], selected_trackers=["trakt"], outcome_details=details,
        )

    assert result == "failed"
    assert trakt.sync_history.await_count == 2
    assert "could not match" in details[0]


def test_cowatch_failed_delivery_reason_is_saved_for_activity_details():
    from app import main as main_module

    event = {"event_id": "cowatch-failed-event"}
    with patch.object(main_module.activity_delivery_mgr, "events", [event]), \
         patch.object(main_module.activity_delivery_mgr, "save_events"):
        main_module.update_event_cowatch_status(
            "cowatch-failed-event", "failed", "Trakt could not match this episode."
        )

    assert event["cowatch_status"]["status"] == "failed"
    assert event["cowatch_status"]["synced"] is False
    assert event["cowatch_status"]["reason"] == "Trakt could not match this episode."
    household_details = event["tracker_delivery_details"]["household"]
    assert household_details["state"] == "failed"
    assert household_details["reason"] == "Trakt could not match this episode."


@pytest.mark.asyncio(loop_scope="module")
async def test_execute_cowatch_sync_uses_anilist_client_for_anilist_progress():
    from app.main import execute_cowatch_sync
    from app.plex_parser import ParsedMedia
    from app.services.user_manager import user_mgr
    from app.config import Config

    parsed = ParsedMedia(
        event="media.scrobble", username="viewer", media_type="episode",
        title="Episode 3", show_title="Example Anime", season=1, episode=3,
        progress=100,
    )
    trakt_client = MagicMock()
    trakt_client.is_authenticated.return_value = False
    anilist_client = MagicMock()
    anilist_client.is_authenticated.return_value = True
    anilist_client.update_progress = AsyncMock()
    mal_client = MagicMock()
    mal_client.is_authenticated.return_value = False
    mal_client.update_progress = AsyncMock()
    anime_match = {"is_anime": True, "anilist_id": 987, "mal_id": None,
                   "episode_number": 3, "episodes": 12}

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(user_mgr, "get_client", return_value=trakt_client), \
         patch.object(user_mgr, "get_tracker_client", side_effect=lambda _u, tracker: anilist_client if tracker == "anilist" else mal_client), \
         patch("app.main.anime_resolver.resolve", new_callable=AsyncMock, return_value=anime_match), \
         patch("app.main.queue_mgr.enqueue"):
        status = await execute_cowatch_sync(parsed, "mark_watched", targets=["partner"])

    assert status == "partial"
    anilist_client.update_progress.assert_awaited_once_with(987, 3, 12)
    mal_client.update_progress.assert_not_awaited()


