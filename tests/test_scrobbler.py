import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import app, get_uptime_str, mask_username, queue_mgr, recent_events, scrobble_stats, trakt, sonarr
from app.services.queue_manager import QueueManager, process_queue
from app.clients.trakt_client import TraktClient

from app.services.notifier import Notifier, format_media_title, get_trakt_url, notifier
from app.services.playback_manager import PlaybackManager, playback_mgr
from app.plex_parser import ParsedMedia, parse_plex_ids, parse_plex_webhook
from app.services.user_manager import UserClientManager, user_mgr
from app.services.cowatch_manager import CowatchManager, cowatch_mgr
from app.services.log_manager import log_mgr
from app.services.demo_manager import demo_mgr


@pytest.fixture(autouse=True)
def _ensure_servers_enabled_for_tests():
    """Ensure server ingestion is enabled during webhook pipeline tests and isolate settings."""
    import copy
    from app.services.settings_manager import settings_mgr
    from app.main import reverse_sync_mgr
    orig_settings = copy.deepcopy(settings_mgr._settings)
    orig_custom_notif = copy.deepcopy(settings_mgr._custom_notifications)
    settings_mgr.set_server_enabled("plex", True)
    settings_mgr.set_server_enabled("jellyfin", True)
    settings_mgr.set_server_enabled("emby", True)
    yield
    settings_mgr._settings = orig_settings
    settings_mgr._custom_notifications = orig_custom_notif
    settings_mgr._save_settings()
    orig_recon = orig_settings.get("reconciliation", {})
    reverse_sync_mgr.update_config(
        plex_url=orig_recon.get("plex_url", ""),
        plex_token=orig_recon.get("plex_token", ""),
        jellyfin_url=orig_recon.get("jellyfin_url", ""),
        jellyfin_token=orig_recon.get("jellyfin_token", ""),
        emby_url=orig_recon.get("emby_url", ""),
        emby_token=orig_recon.get("emby_token", ""),
    )


def test_parse_plex_ids():
    guid_list = [
        {"id": "imdb://tt1234567"},
        {"id": "tmdb://98765"},
        {"id": "tvdb://112233"},
    ]
    ids = parse_plex_ids(guid_list)
    assert ids["imdb"] == "tt1234567"
    assert ids["tmdb"] == 98765
    assert ids["tvdb"] == 112233


def test_parse_plex_ids_legacy():
    ids = parse_plex_ids([], legacy_guid="com.plexapp.agents.imdb://tt7654321?lang=en")
    assert ids["imdb"] == "tt7654321"


def test_parse_episode_scrobble():
    payload = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Ozymandias",
            "grandparentTitle": "Breaking Bad",
            "parentTitle": "Season 5",
            "index": 14,
            "parentIndex": 5,
            "year": 2013,
            "duration": 3000000,
            "viewOffset": 2900000,
            "Guid": [
                {"id": "imdb://tt2301451"},
                {"id": "tmdb://62085"},
                {"id": "tvdb://349232"},
            ],
        },
    }

    parsed = parse_plex_webhook(payload, allowed_users=["selits"])
    assert parsed is not None
    assert parsed.event == "media.scrobble"
    assert parsed.username == "selits"
    assert parsed.media_type == "episode"
    assert parsed.show_title == "Breaking Bad"
    assert parsed.season == 5
    assert parsed.episode == 14
    assert parsed.ids["imdb"] == "tt2301451"

    # Test conversion to Trakt scrobble format
    scrobble_data = parsed.to_trakt_scrobble_payload()
    assert scrobble_data["show"]["title"] == "Breaking Bad"
    assert scrobble_data["episode"]["season"] == 5
    assert scrobble_data["episode"]["number"] == 14
    assert scrobble_data["episode"]["ids"]["imdb"] == "tt2301451"

    # Test conversion to Trakt sync history format
    history_data = parsed.to_trakt_history_payload()
    assert "episodes" in history_data
    assert history_data["episodes"][0]["ids"]["imdb"] == "tt2301451"


def test_parse_movie_play():
    payload = {
        "event": "media.play",
        "Account": {"title": "selits"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Inception",
            "year": 2010,
            "duration": 7200000,
            "viewOffset": 0,
            "Guid": [{"id": "imdb://tt1375666"}],
        },
    }

    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.event == "media.play"
    assert parsed.media_type == "movie"
    assert parsed.progress == 0.0

    scrobble_data = parsed.to_trakt_scrobble_payload()
    assert scrobble_data["movie"]["title"] == "Inception"
    assert scrobble_data["movie"]["year"] == 2010
    assert scrobble_data["movie"]["ids"]["imdb"] == "tt1375666"


def test_user_filtering():
    payload = {
        "event": "media.scrobble",
        "Account": {"title": "other_user"},
        "Metadata": {
            "type": "episode",
            "title": "Pilot",
            "grandparentTitle": "Lost",
        },
    }
    # Only allow "selits"
    parsed = parse_plex_webhook(payload, allowed_users=["selits"])
    assert parsed is None


def test_webhook_endpoint_full_flow():
    client = TestClient(app)

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    # 2. Dashboard
    res_dash = client.get("/")
    assert res_dash.status_code == 200
    assert "Omniscrobble" in res_dash.text

    # 3. Webhook call with mocked Trakt responses
    plex_sample = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "episode",
            "title": "Chicanery",
            "grandparentTitle": "Better Call Saul",
            "parentIndex": 3,
            "index": 5,
            "duration": 3000000,
            "viewOffset": 2900000,
            "Guid": [{"id": "imdb://tt5869688"}],
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:

        mock_stop.return_value = {"action": "scrobble", "progress": 100}
        mock_sync.return_value = {"added": {"episodes": 1}}

        response = client.post(
            "/webhook",
            data={"payload": json.dumps(plex_sample)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["action"] == "mark_watched"
        assert "history" in data["result"]


def test_webhook_secret_authentication():
    client = TestClient(app)
    plex_sample = {
        "event": "media.play",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Interstellar",
            "year": 2014,
            "duration": 7200000,
            "viewOffset": 0,
            "Guid": [{"id": "imdb://tt0816692"}],
        },
    }

    with patch.object(Config, "WEBHOOK_SECRET", "super_secret_token"), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:

        mock_start.return_value = {"action": "start"}

        # 1. Reject without token
        res_no_token = client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res_no_token.status_code == 401

        # 2. Reject with wrong token
        res_wrong_token = client.post("/webhook?token=wrong", data={"payload": json.dumps(plex_sample)})
        assert res_wrong_token.status_code == 401

        # 3. Allow with correct query token
        res_valid_query = client.post("/webhook?token=super_secret_token", data={"payload": json.dumps(plex_sample)})
        assert res_valid_query.status_code == 200
        assert res_valid_query.json()["status"] == "success"

        # 4. Allow with correct header token
        res_valid_header = client.post(
            "/webhook",
            headers={"X-Webhook-Secret": "super_secret_token"},
            data={"payload": json.dumps(plex_sample)},
        )
        assert res_valid_header.status_code == 200
        assert res_valid_header.json()["status"] == "success"


def test_webhook_get_info():
    """Verify that visiting /webhook via browser GET returns a friendly status message."""
    client = TestClient(app)
    res = client.get("/webhook")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "Plex Webhook endpoint is active" in data["message"]


def test_tv_show_year_extraction():
    payload = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "episode",
            "title": "Pilot",
            "grandparentTitle": "Doctor Who",
            "grandparentYear": 2005,
            "parentIndex": 1,
            "index": 1,
            "duration": 2700000,
            "viewOffset": 2700000,
        },
    }
    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.show_year == 2005

    scrobble_data = parsed.to_trakt_scrobble_payload()
    assert scrobble_data["show"]["year"] == 2005

    history_data = parsed.to_trakt_history_payload()
    assert history_data["shows"][0]["year"] == 2005


@pytest.mark.asyncio
async def test_trakt_client_401_retry(tmp_path):
    class FakeConfig:
        TRAKT_CLIENT_ID = "cid"
        TRAKT_CLIENT_SECRET = "csec"
        TRAKT_API_URL = "https://api.trakt.tv"
        TRAKT_TOKENS_FILE = tmp_path / "tokens.json"

    client = TraktClient(FakeConfig)
    client.save_tokens({"access_token": "expired_token", "refresh_token": "valid_refresh", "created_at": 9999999999, "expires_in": 7200})

    call_count = 0


    def mock_handler(request: httpx.Request):
        nonlocal call_count
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fresh_token", "refresh_token": "new_refresh", "expires_in": 7200, "created_at": 200})
        elif request.url.path == "/scrobble/start":
            call_count += 1
            if request.headers.get("Authorization") == "Bearer expired_token":
                return httpx.Response(401, text="Unauthorized")
            elif request.headers.get("Authorization") == "Bearer fresh_token":
                return httpx.Response(201, json={"action": "start"})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client._http_client = httpx.AsyncClient(transport=transport)

    res = await client.scrobble_start({"movie": {"title": "Test"}})
    assert res == {"action": "start"}
    assert call_count == 2
    await client.close()


@pytest.mark.asyncio
async def test_trakt_client_429_backoff(tmp_path):
    class FakeConfig:
        TRAKT_CLIENT_ID = "cid"
        TRAKT_CLIENT_SECRET = "csec"
        TRAKT_API_URL = "https://api.trakt.tv"
        TRAKT_TOKENS_FILE = tmp_path / "tokens.json"

    client = TraktClient(FakeConfig)
    client.save_tokens({"access_token": "tok", "refresh_token": "ref", "created_at": 9999999999, "expires_in": 7200})

    call_count = 0

    def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="Rate limited")
        return httpx.Response(200, json={"action": "start"})

    transport = httpx.MockTransport(mock_handler)
    client._http_client = httpx.AsyncClient(transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await client.scrobble_start({"movie": {"title": "Test"}})
        assert res == {"action": "start"}
        assert call_count == 2
        mock_sleep.assert_awaited_once_with(1)
    await client.close()


def test_api_events_and_clear():
    client = TestClient(app)
    # Get events
    res = client.get("/api/events")
    assert res.status_code == 200
    assert "events" in res.json()

    # Clear events
    clear_res = client.post("/api/events/clear")
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "cleared"

    res_after = client.get("/api/events")
    assert res_after.json()["events"] == []


def test_auth_endpoints_and_page():
    client = TestClient(app)

    # 1. GET /auth page
    res_page = client.get("/auth")
    assert res_page.status_code == 200
    assert "Link Trakt Account" in res_page.text

    # 2. POST /api/auth/start
    with patch.object(trakt, "generate_device_code", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        res_start = client.post("/api/auth/start")
        assert res_start.status_code == 200
        assert res_start.json()["user_code"] == "ABCD1234"

    # 3. POST /api/auth/poll pending
    with patch.object(trakt, "poll_for_token", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"status": "pending"}
        res_poll = client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll.status_code == 200
        assert res_poll.json()["status"] == "pending"

    # 4. POST /api/auth/poll success
    with patch.object(trakt, "poll_for_token", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"access_token": "valid_token"}
        res_poll_ok = client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll_ok.status_code == 200
        assert res_poll_ok.json()["status"] == "success"


def test_mask_username():
    assert mask_username("selits") == "se****"
    assert mask_username("alex") == "a***"
    assert mask_username("bob") == "b**"
    assert mask_username("al") == "a*"
    assert mask_username("a") == "*"
    assert mask_username("") == ""
    assert mask_username(None) == ""


def test_get_uptime_str():
    uptime = get_uptime_str()
    assert isinstance(uptime, str)
    assert len(uptime) > 0


def test_token_info(tmp_path):
    class FakeConfig:
        TRAKT_CLIENT_ID = "cid"
        TRAKT_CLIENT_SECRET = "csec"
        TRAKT_API_URL = "https://api.trakt.tv"
        TRAKT_TOKENS_FILE = tmp_path / "tokens.json"

    client = TraktClient(FakeConfig)

    # 1. No tokens
    info_none = client.get_token_info()
    assert info_none["status"] == "none"
    assert info_none["healthy"] is False

    # 2. Healthy token
    import time
    now = int(time.time())
    client.save_tokens({"access_token": "tok123", "created_at": now, "expires_in": 7776000})
    info_healthy = client.get_token_info()
    assert info_healthy["status"] == "healthy"
    assert info_healthy["healthy"] is True
    assert info_healthy["days_remaining"] >= 89

    # 3. Expired token
    client.save_tokens({"access_token": "tok123", "created_at": now - 100000, "expires_in": 50000})
    info_expired = client.get_token_info()
    assert info_expired["status"] == "expired"
    assert info_expired["healthy"] is False
    assert info_expired["days_remaining"] == 0


def test_admin_authorization_gate():
    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "secret123"):
        # 1. Clear events without admin rights -> 401
        res_clear_denied = client.post("/api/events/clear")
        assert res_clear_denied.status_code == 401

        # 2. Clear events with valid header -> 200
        res_clear_header = client.post("/api/events/clear", headers={"x-webhook-secret": "secret123"})
        assert res_clear_header.status_code == 200

        # 3. Clear events with cookie -> 200
        client.cookies.set("admin_token", "secret123")
        res_clear_cookie = client.post("/api/events/clear")
        assert res_clear_cookie.status_code == 200
        client.cookies.clear()

        # 4. Auth endpoints without admin rights -> 401
        res_auth_denied = client.get("/auth")
        assert res_auth_denied.status_code == 401
        assert "Admin Authorization Required" in res_auth_denied.text

        res_start_denied = client.post("/api/auth/start")
        assert res_start_denied.status_code == 401

        res_poll_denied = client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll_denied.status_code == 401

        # 5. Admin unlock endpoint
        res_unlock_bad = client.post("/api/admin/unlock", json={"token": "wrong"})
        assert res_unlock_bad.status_code == 401

        res_unlock_ok = client.post("/api/admin/unlock", json={"token": "secret123"})
        assert res_unlock_ok.status_code == 200
        assert "admin_token" in res_unlock_ok.cookies

        # 6. Admin lock endpoint
        res_lock = client.post("/api/admin/lock")
        assert res_lock.status_code == 200


def test_dashboard_privacy_masking():
    client = TestClient(app)

    recent_events.appendleft({
        "timestamp": "2026-09-26 12:00:00",
        "user": "selits",
        "event": "media.scrobble",
        "action": "mark_watched",
        "title": "Severance S01E01",
        "type": "episode",
        "progress": "100.0%",
        "result_status": "ok",
        "raw_result": {},
    })

    with patch.object(Config, "WEBHOOK_SECRET", "my_super_secret"), \
         patch.object(Config, "PLEX_ALLOWED_USERS", ["selits"]):

        # 1. Unauthenticated request: masked usernames & secret
        res_locked = client.get("/")
        assert res_locked.status_code == 200
        assert "se****" in res_locked.text
        assert "my_super_secret" not in res_locked.text
        assert "●●●●●●●●" in res_locked.text
        assert "://●●●●●●●●" in res_locked.text
        assert "🔓 Unlock Admin" in res_locked.text

        # 2. Events API masked
        res_events = client.get("/api/events")
        assert res_events.status_code == 200
        assert res_events.json()["events"][0]["user"] == "se****"

        # 3. Authenticated request via cookie: reveals unmasked data
        client.cookies.set("admin_token", "my_super_secret")
        res_unlocked = client.get("/")
        assert res_unlocked.status_code == 200
        assert "my_super_secret" in res_unlocked.text
        assert "🔒 Lock Admin" in res_unlocked.text
        assert "📋 Copy URL" in res_unlocked.text

        res_events_unlocked = client.get("/api/events")
        assert res_events_unlocked.json()["events"][0]["user"] == "selits"
        client.cookies.clear()


def test_health_and_stats():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "uptime" in data
    assert "token_health" in data
    assert "stats" in data
    assert "total" in data["stats"]
    assert "movies" in data["stats"]
    assert "episodes" in data["stats"]
    assert "ratings" in data["stats"]


def test_parse_movie_rating():
    payload = {
        "event": "media.rate",
        "Account": {"title": "selits"},
        "rating": 9.0,
        "Metadata": {
            "type": "movie",
            "title": "Dune: Part Two",
            "year": 2024,
            "Guid": [{"id": "imdb://tt15239678"}],
        },
    }
    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.event == "media.rate"
    assert parsed.rating == 9
    assert parsed.media_type == "movie"

    rating_payload = parsed.to_trakt_rating_payload()
    assert "movies" in rating_payload
    assert rating_payload["movies"][0]["title"] == "Dune: Part Two"
    assert rating_payload["movies"][0]["rating"] == 9
    assert rating_payload["movies"][0]["ids"]["imdb"] == "tt15239678"


def test_parse_episode_rating():
    payload = {
        "event": "media.rate",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "episode",
            "title": "Face Off",
            "grandparentTitle": "Breaking Bad",
            "parentIndex": 4,
            "index": 13,
            "userRating": 10.0,
            "Guid": [{"id": "imdb://tt2064145"}],
        },
    }
    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.rating == 10

    rating_payload = parsed.to_trakt_rating_payload()
    assert "episodes" in rating_payload
    assert rating_payload["episodes"][0]["rating"] == 10
    assert rating_payload["episodes"][0]["ids"]["imdb"] == "tt2064145"


def test_parse_show_rating():
    payload = {
        "event": "media.rate",
        "Account": {"title": "selits"},
        "rating": 8,
        "Metadata": {
            "type": "show",
            "title": "Succession",
            "year": 2018,
            "Guid": [{"id": "imdb://tt7660850"}],
        },
    }
    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.media_type == "show"
    assert parsed.rating == 8

    rating_payload = parsed.to_trakt_rating_payload()
    assert "shows" in rating_payload
    assert rating_payload["shows"][0]["title"] == "Succession"
    assert rating_payload["shows"][0]["rating"] == 8
    assert rating_payload["shows"][0]["year"] == 2018
    assert rating_payload["shows"][0]["ids"]["imdb"] == "tt7660850"


@pytest.mark.asyncio
async def test_trakt_sync_ratings(tmp_path):
    class FakeConfig:
        TRAKT_CLIENT_ID = "cid"
        TRAKT_CLIENT_SECRET = "csec"
        TRAKT_API_URL = "https://api.trakt.tv"
        TRAKT_TOKENS_FILE = tmp_path / "tokens.json"

    client = TraktClient(FakeConfig)
    client.save_tokens({"access_token": "valid_token", "created_at": 9999999999, "expires_in": 7200})

    def mock_handler(request: httpx.Request):
        if request.url.path == "/sync/ratings":
            return httpx.Response(201, json={"added": {"movies": 1}})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client._http_client = httpx.AsyncClient(transport=transport)

    res = await client.sync_ratings({"movies": [{"rating": 9, "title": "Test"}]})
    assert res == {"added": {"movies": 1}}
    await client.close()


def test_webhook_rating_flow():
    client = TestClient(app)
    plex_rating_payload = {
        "event": "media.rate",
        "Account": {"title": "selits"},
        "rating": 9.0,
        "Metadata": {
            "type": "movie",
            "title": "Oppenheimer",
            "year": 2023,
            "Guid": [{"id": "imdb://tt15398776"}],
        },
    }

    initial_ratings_count = scrobble_stats.get("ratings", 0)

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_ratings", new_callable=AsyncMock) as mock_sync:

        mock_sync.return_value = {"added": {"movies": 1}}

        res = client.post("/webhook", data={"payload": json.dumps(plex_rating_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "rate"
        assert scrobble_stats["ratings"] == initial_ratings_count + 1
        assert "rate (9/10)" in recent_events[0]["action"]


def test_queue_manager_crud(tmp_path):
    db_file = tmp_path / "test_queue.db"
    qm = QueueManager(db_file)

    # 1. Initially empty
    assert qm.get_pending_count() == 0
    assert qm.get_pending() == []

    # 2. Enqueue items
    id1 = qm.enqueue("scrobble_stop", {"movie": {"title": "Matrix"}}, error="Timeout")
    id2 = qm.enqueue("sync_ratings", {"shows": [{"title": "Dark"}]}, error="503 Service Unavailable")
    assert id1 > 0
    assert id2 > 0
    assert qm.get_pending_count() == 2

    # 3. Retrieve pending
    items = qm.get_pending(limit=10)
    assert len(items) == 2
    assert items[0]["id"] == id1
    assert items[0]["event_type"] == "scrobble_stop"
    assert items[0]["payload"]["movie"]["title"] == "Matrix"
    assert items[1]["id"] == id2
    assert items[1]["event_type"] == "sync_ratings"

    # 4. Mark failure and max_retries
    qm.mark_failure(id1, error="Still down", max_retries=2)
    items = qm.get_pending()
    assert len(items) == 2
    assert items[0]["retry_count"] == 1
    assert items[0]["status"] == "pending"

    # Second failure triggers status="failed"
    qm.mark_failure(id1, error="Permanent fail", max_retries=2)
    assert qm.get_pending_count() == 1  # Only id2 is still pending

    # 5. Retry all failed
    reset_count = qm.retry_all_failed()
    assert reset_count == 1
    assert qm.get_pending_count() == 2

    # 6. Mark success
    qm.mark_success(id1)
    assert qm.get_pending_count() == 1

    # 7. Clear queue
    qm.clear_queue()
    assert qm.get_pending_count() == 0


@pytest.mark.asyncio
async def test_process_queue_success(tmp_path):
    db_file = tmp_path / "process_queue.db"
    qm = QueueManager(db_file)
    qm.enqueue("scrobble_stop", {"movie": {"title": "Interstellar"}})
    qm.enqueue("sync_ratings", {"movies": [{"title": "Inception", "rating": 10}]})

    mock_client = MagicMock(spec=TraktClient)
    mock_client.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})
    mock_client.sync_ratings = AsyncMock(return_value={"added": {"movies": 1}})

    stats = await process_queue(mock_client, qm)
    assert stats["processed"] == 2
    assert stats["succeeded"] == 2
    assert stats["failed"] == 0
    assert qm.get_pending_count() == 0


@pytest.mark.asyncio
async def test_process_queue_transient_failure(tmp_path):
    db_file = tmp_path / "process_queue_fail.db"
    qm = QueueManager(db_file)
    qm.enqueue("scrobble_stop", {"movie": {"title": "Tenet"}})
    qm.enqueue("sync_history", {"episodes": []})

    mock_client = MagicMock(spec=TraktClient)
    # First item encounters 503
    mock_client.scrobble_stop = AsyncMock(return_value={"status": 503, "error": "Service Unavailable"})

    stats = await process_queue(mock_client, qm)
    assert stats["succeeded"] == 0
    assert stats["failed"] == 1
    # Stops processing batch on transient failure; 2 items remain in queue
    assert qm.get_pending_count() == 2


def test_webhook_automatic_enqueue_on_failure():
    client = TestClient(app)
    plex_sample = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Gladiator",
            "year": 2000,
            "duration": 9000000,
            "viewOffset": 8500000,
            "Guid": [{"id": "imdb://tt0172495"}],
        },
    }

    initial_pending = queue_mgr.get_pending_count()

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:

        # Simulate 503 Service Unavailable from Trakt
        mock_stop.return_value = {"status": 503, "error": "Trakt API Unavailable"}
        mock_sync.return_value = {"status": 503, "error": "Trakt API Unavailable"}

        res = client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res.status_code == 200
        # Check that both stop and history events were enqueued
        assert queue_mgr.get_pending_count() >= initial_pending + 2


def test_queue_endpoints():
    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "super_secret"):
        # 1. POST /api/queue/retry without auth -> 401
        res_retry_denied = client.post("/api/queue/retry")
        assert res_retry_denied.status_code == 401

        # 2. POST /api/queue/clear without auth -> 401
        res_clear_denied = client.post("/api/queue/clear")
        assert res_clear_denied.status_code == 401

        # 3. With admin cookie -> 200
        client.cookies.set("admin_token", "super_secret")
        with patch("app.main.process_queue", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = {"processed": 0, "succeeded": 0, "failed": 0}
            res_retry_ok = client.post("/api/queue/retry")
            assert res_retry_ok.status_code == 200
            assert "pending_count" in res_retry_ok.json()

        res_clear_ok = client.post("/api/queue/clear")
        assert res_clear_ok.status_code == 200
        assert res_clear_ok.json()["pending_count"] == 0
        client.cookies.clear()


def test_notifier_title_formatting_and_trakt_url():
    # Episode with subtitle
    ep = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        ids={"imdb": "tt2301451"},
    )
    assert format_media_title(ep) == "Breaking Bad S05E14 - Ozymandias"
    assert get_trakt_url(ep) == "https://trakt.tv/search?q=Breaking+Bad"

    # Episode without subtitle or title same as show
    ep_same = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Breaking Bad",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        ids={"tmdb": 62085},
    )
    assert format_media_title(ep_same) == "Breaking Bad S05E14"
    assert get_trakt_url(ep_same) == "https://trakt.tv/search?q=Breaking+Bad"

    # Movie with year
    movie = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
        ids={"tvdb": 12345},
    )
    assert format_media_title(movie) == "Inception (2010)"
    assert get_trakt_url(movie) == "https://trakt.tv/search?q=Inception"

    # Fallback with title
    plain = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Unknown Film",
    )
    assert format_media_title(plain) == "Unknown Film"
    assert get_trakt_url(plain) == "https://trakt.tv/search?q=Unknown+Film"

    # Empty title fallback
    empty = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="",
    )
    assert get_trakt_url(empty) == "https://trakt.tv"


def test_notifier_build_payloads():
    media_scrobble = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        progress=100.0,
        ids={"imdb": "tt2301451"},
    )

    notifier_inst = Notifier(Config)

    # 1. Discord Scrobble payload
    discord_scrobble = notifier_inst.build_discord_payload(media_scrobble, "mark_watched")
    assert "embeds" in discord_scrobble
    embed = discord_scrobble["embeds"][0]
    assert embed["color"] == 0xED1C24
    assert embed["title"] == "Breaking Bad S05E14 - Ozymandias"
    assert embed["url"] == "https://trakt.tv/search?q=Breaking+Bad"
    assert any(f["name"] == "Progress" and f["value"] == "100.0%" for f in embed["fields"])

    # 2. Discord Rating payload
    media_rate = ParsedMedia(
        event="media.rate",
        username="selits",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        rating=10,
        ids={"imdb": "tt15239678"},
    )
    discord_rate = notifier_inst.build_discord_payload(media_rate, "rate")
    embed_rate = discord_rate["embeds"][0]
    assert embed_rate["color"] == 0xF5A623
    assert embed_rate["title"] == "Dune: Part Two (2024)"
    assert any(f["name"] == "Rating" and "10/10" in f["value"] for f in embed_rate["fields"])

    # 3. Telegram Scrobble payload
    with patch.object(Config, "TELEGRAM_CHAT_ID", "123456"):
        tg_scrobble = notifier_inst.build_telegram_payload(media_scrobble, "mark_watched")
        assert tg_scrobble["chat_id"] == "123456"
        assert tg_scrobble["parse_mode"] == "HTML"
        assert "Breaking Bad S05E14 - Ozymandias" in tg_scrobble["text"]
        assert "Scrobbled to Trakt" in tg_scrobble["text"]

    # 4. Telegram Rating payload
    with patch.object(Config, "TELEGRAM_CHAT_ID", "123456"):
        tg_rate = notifier_inst.build_telegram_payload(media_rate, "rate")
        assert tg_rate["chat_id"] == "123456"
        assert "Dune: Part Two (2024)" in tg_rate["text"]
        assert "10/10" in tg_rate["text"]


@pytest.mark.asyncio
async def test_notifier_send_discord_and_telegram():
    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
        progress=100.0,
    )

    notifier_inst = Notifier(Config)

    # 1. Discord unconfigured
    with patch.object(Config, "DISCORD_WEBHOOK_URL", ""):
        assert await notifier_inst.send_discord(media, "mark_watched") is False

    # 2. Discord configured & success (204 No Content)
    mock_discord_client = AsyncMock()
    mock_discord_client.post.return_value = httpx.Response(204)
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        success = await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client)
        assert success is True
        mock_discord_client.post.assert_called_once()

    # 3. Discord failure response (500)
    mock_discord_client.post.return_value = httpx.Response(500, text="Internal Server Error")
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        assert await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client) is False

    # 4. Discord network exception handled gracefully
    mock_discord_client.post.side_effect = httpx.ConnectTimeout("Discord timed out")
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        assert await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client) is False

    # 5. Telegram unconfigured
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), patch.object(Config, "TELEGRAM_CHAT_ID", ""):
        assert await notifier_inst.send_telegram(media, "mark_watched") is False

    # 6. Telegram configured & success
    mock_tg_client = AsyncMock()
    mock_tg_client.post.return_value = httpx.Response(200, json={"ok": True})
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        success = await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client)
        assert success is True
        mock_tg_client.post.assert_called_once()

    # 7. Telegram error response (400)
    mock_tg_client.post.return_value = httpx.Response(400, text="Bad Request")
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        assert await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client) is False

    # 8. Telegram network exception handled gracefully
    mock_tg_client.post.side_effect = httpx.ConnectError("Network is down")
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        assert await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client) is False


@pytest.mark.asyncio
async def test_notifier_dispatch_toggles():
    media_scrobble = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
    )
    media_rate = ParsedMedia(
        event="media.rate",
        username="selits",
        media_type="movie",
        title="Inception",
        rating=9,
    )

    notifier_inst = Notifier(Config)

    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", "bot123"), \
         patch.object(Config, "TELEGRAM_CHAT_ID", "chat123"):

        # 1. When NOTIFY_ON_SCROBBLE is disabled
        with patch.object(Config, "NOTIFY_ON_SCROBBLE", False), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_scrobble, "mark_watched")
            mock_d.assert_not_called()
            mock_t.assert_not_called()

        # 2. When NOTIFY_ON_RATE is disabled
        with patch.object(Config, "NOTIFY_ON_RATE", False), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_rate, "rate")
            mock_d.assert_not_called()
            mock_t.assert_not_called()

        # 3. When both enabled
        with patch.object(Config, "NOTIFY_ON_SCROBBLE", True), \
             patch.object(Config, "NOTIFY_ON_RATE", True), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_scrobble, "mark_watched")
            mock_d.assert_called_once()
            mock_t.assert_called_once()


def test_webhook_triggers_notification_dispatch():
    client = TestClient(app)

    plex_sample = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Interstellar",
            "year": 2014,
            "duration": 10000000,
            "viewOffset": 9900000,
            "Guid": [{"id": "imdb://tt0816692"}],
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_dispatch:

        mock_stop.return_value = {"action": "scrobble"}
        mock_sync.return_value = {"added": {"movies": 1}}

        res = client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res.status_code == 200
        # Check that dispatch was called with parsed media and action
        assert mock_dispatch.called
        args = mock_dispatch.call_args[0]
        assert args[0].title == "Interstellar"
        assert args[1] == "mark_watched"


def test_health_and_dashboard_notification_status():
    client = TestClient(app)

    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), \
         patch.object(Config, "TELEGRAM_CHAT_ID", ""):

        # 1. Check /health
        res_health = client.get("/health")
        assert res_health.status_code == 200
        data = res_health.json()
        assert "notifications" in data
        assert data["notifications"]["discord"] is True
        assert data["notifications"]["telegram"] is False

        # 2. Check dashboard
        res_dash = client.get("/")
        assert res_dash.status_code == 200
        assert "Alerts: Discord" in res_dash.text


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


def test_api_playback_endpoint():
    client = TestClient(app)
    playback_mgr.clear()

    # Empty
    res = client.get("/api/playback")
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

    res2 = client.get("/api/playback")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2["active_sessions"]) == 1
    assert data2["active_sessions"][0]["title"] == "Oppenheimer (2023)"
    playback_mgr.clear()


@pytest.mark.asyncio
async def test_trakt_search_media():
    mock_client = AsyncMock()
    mock_client.get.return_value = httpx.Response(
        200,
        json=[
            {
                "type": "movie",
                "movie": {
                    "title": "Inception",
                    "year": 2010,
                    "ids": {"imdb": "tt1375666", "trakt": 16},
                },
            }
        ],
    )

    with patch.object(trakt, "get_client", return_value=mock_client), \
         patch.object(trakt, "_get_headers", new_callable=AsyncMock) as mock_headers:
        mock_headers.return_value = {"trakt-api-key": "fake"}
        results = await trakt.search_media("Inception", "movie")
        assert len(results) == 1
        assert results[0]["movie"]["title"] == "Inception"


def test_api_search_and_manual_scrobble():
    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
        # 1. Unauthenticated search -> 401
        res_denied = client.get("/api/search?query=Inception")
        assert res_denied.status_code == 401

        # 2. Authenticated search -> 200
        client.cookies.set("admin_token", "test_secret")
        with patch.object(trakt, "search_media", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{"type": "movie", "movie": {"title": "Inception", "year": 2010}}]
            res_ok = client.get("/api/search?query=Inception")
            assert res_ok.status_code == 200
            assert len(res_ok.json()["results"]) == 1

        # 3. Unauthenticated manual scrobble -> 401
        client.cookies.clear()
        res_scrobble_denied = client.post("/api/scrobble/manual", json={"media_type": "movie", "title": "Inception"})
        assert res_scrobble_denied.status_code == 401

        # 4. Authenticated manual scrobble movie -> 200
        client.cookies.set("admin_token", "test_secret")
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {"added": {"movies": 1}}

            payload = {
                "media_type": "movie",
                "title": "Interstellar",
                "year": 2014,
                "ids": {"imdb": "tt0816692"},
            }
            res_scrobble_ok = client.post("/api/scrobble/manual", json=payload)
            assert res_scrobble_ok.status_code == 200
            assert mock_sync.called
            sync_payload = mock_sync.call_args[0][0]
            assert "movies" in sync_payload
            assert sync_payload["movies"][0]["title"] == "Interstellar"

        # 5. Authenticated manual scrobble episode -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {"added": {"episodes": 1}}

            ep_payload = {
                "media_type": "episode",
                "title": "Succession",
                "year": 2018,
                "season": 4,
                "episode": 3,
                "ids": {"tmdb": 76331},
            }
            res_ep_ok = client.post("/api/scrobble/manual", json=ep_payload)
            assert res_ep_ok.status_code == 200
            assert mock_sync.called
            sync_ep_payload = mock_sync.call_args[0][0]
            assert "shows" in sync_ep_payload
            assert sync_ep_payload["shows"][0]["title"] == "Succession"
            assert sync_ep_payload["shows"][0]["seasons"][0]["episodes"][0]["number"] == 3

        client.cookies.clear()


def test_webhook_tracks_playback():
    client = TestClient(app)
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

        res = client.post("/webhook", data={"payload": json.dumps(play_payload)})
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

        res2 = client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res2.status_code == 200

        assert len(playback_mgr.get_active_sessions()) == 0
        finished = playback_mgr.get_recently_finished()
        assert finished is not None
        assert finished["title"] == "Blade Runner 2049 (2017)"

    playback_mgr.clear()


def test_user_client_manager(tmp_path):
    mgr = UserClientManager()
    mgr.tokens_dir = tmp_path / "tokens"
    mgr.tokens_dir.mkdir(parents=True, exist_ok=True)

    # 1. Default client resolution
    c_default = mgr.get_client("default")
    assert c_default is not None
    assert mgr.get_client(None) is c_default

    # 2. Per-user client
    c_alice = mgr.get_client("alice")
    assert c_alice is not c_default
    assert c_alice.tokens_file == tmp_path / "tokens" / "alice_tokens.json"
    assert mgr.get_client("Alice") is c_alice  # Case insensitivity & caching

    # 3. List configured users & co-watch deduplication
    users = mgr.list_configured_users()
    unames = [u["username"] for u in users]
    assert "default" in unames

    # 4. Username with dots & deduplication test
    with patch.object(Config, "CO_WATCH_USER", "bon.vivant"):
        # Simulate bonvivant_tokens.json existing
        (mgr.tokens_dir / "bonvivant_tokens.json").write_text(json.dumps({"access_token": "abc"}))
        users_cowatch = mgr.list_configured_users()
        user_names_cowatch = [u["username"] for u in users_cowatch]
        # Should not have both bonvivant and bon.vivant
        assert user_names_cowatch.count("bon.vivant") == 1
        assert "bonvivant" not in user_names_cowatch
        cw_entry = next(u for u in users_cowatch if u["username"] == "bon.vivant")
        assert cw_entry["is_cowatch_target"] is True
        assert cw_entry["tokens_file"] == "bonvivant_tokens.json"


def test_cowatch_manager(tmp_path):
    data_file = tmp_path / "cowatch_shows.json"
    mgr = CowatchManager()
    mgr.data_file = data_file
    mgr._shows = []
    mgr._save_shows()

    # 1. Add and remove shows
    assert mgr.add_show("The Bear") == ["The Bear"]
    assert mgr.add_show("Severance") == ["The Bear", "Severance"]
    # Duplicate addition ignored
    assert mgr.add_show("the bear") == ["The Bear", "Severance"]
    assert len(mgr.get_shows()) == 2

    # 2. Normalized matching
    assert mgr.is_cowatch_show("The Bear") is True
    assert mgr.is_cowatch_show("The Bear (2022)") is True
    assert mgr.is_cowatch_show("the bear") is True
    assert mgr.is_cowatch_show("Severance") is True
    assert mgr.is_cowatch_show("Succession") is False

    # 3. Persistence reload
    mgr2 = CowatchManager()
    mgr2.data_file = data_file
    mgr2._load_shows()
    assert "The Bear" in mgr2.get_shows()
    assert "Severance" in mgr2.get_shows()

    # 4. Remove show
    mgr2.remove_show("The Bear")
    assert "The Bear" not in mgr2.get_shows()
    assert mgr2.is_cowatch_show("The Bear") is False


def test_cowatch_should_cowatch_rules():
    mgr = CowatchManager()
    mgr._shows = ["Severance"]

    # 1. No CO_WATCH_USER configured
    with patch.object(Config, "CO_WATCH_USER", ""):
        m = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="episode",
            title="Good News About Hell",
            show_title="Severance",
        )
        assert mgr.should_cowatch(m) is False

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "CO_WATCH_MOVIES", False), \
         patch.object(Config, "CO_WATCH_PLAYERS", []):

        # 2. Event originated from partner user -> False (no self-sync loop)
        m_partner = ParsedMedia(
            event="media.scrobble",
            username="partner",
            media_type="episode",
            title="Good News About Hell",
            show_title="Severance",
        )
        assert mgr.should_cowatch(m_partner) is False

        # 3. Matching episode from main user -> True
        m_main = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="episode",
            title="Good News About Hell",
            show_title="Severance",
        )
        assert mgr.should_cowatch(m_main) is True

        # 4. Non-matching episode -> False
        m_solo = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="episode",
            title="Ozymandias",
            show_title="Breaking Bad",
        )
        assert mgr.should_cowatch(m_solo) is False

        # 5. Movie when CO_WATCH_MOVIES is False -> False
        m_movie = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="movie",
            title="Inception",
            year=2010,
        )
        assert mgr.should_cowatch(m_movie) is False

    # 6. Movie when CO_WATCH_MOVIES is True -> True
    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "CO_WATCH_MOVIES", True), \
         patch.object(Config, "CO_WATCH_PLAYERS", []):
        assert mgr.should_cowatch(m_movie) is True

    # 7. Player whitelist filtering
    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "CO_WATCH_MOVIES", True), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["Living Room TV"]):
        m_living_room = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="movie",
            title="Inception",
            player="Living Room TV",
        )
        m_phone = ParsedMedia(
            event="media.scrobble",
            username="selits",
            media_type="movie",
            title="Inception",
            player="iPhone",
        )
        assert mgr.should_cowatch(m_living_room) is True
        assert mgr.should_cowatch(m_phone) is False


def test_cowatch_api_endpoints():
    client = TestClient(app)

    # 1. GET /api/cowatch (public / masked vs admin)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"), \
         patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["selits's Fire TV", "Shield TV"]):
        cowatch_mgr._shows = ["Severance", "The Bear"]
        res_pub = client.get("/api/cowatch")
        assert res_pub.status_code == 200
        pub_data = res_pub.json()
        assert pub_data["status"]["shows"] == []
        assert pub_data["status"]["shows_count"] == 2
        assert pub_data["status"]["co_watch_user"] == "pa*****"
        assert pub_data["status"]["co_watch_players"] == []

        # Admin access reveals shows and actual device names
        client.cookies.set("admin_token", "testsecret")
        res_admin = client.get("/api/cowatch")
        assert res_admin.status_code == 200
        admin_data = res_admin.json()
        assert admin_data["status"]["shows"] == ["Severance", "The Bear"]
        assert admin_data["status"]["co_watch_players"] == ["selits's Fire TV", "Shield TV"]
        client.cookies.clear()

    # 2. POST /api/cowatch/shows (auth check)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # Unauthorized without token
        res_unauth = client.post("/api/cowatch/shows", json={"show": "The Bear"})
        assert res_unauth.status_code == 401

        # Authorized with token
        res_auth = client.post("/api/cowatch/shows?token=testsecret", json={"show": "The Bear"})
        assert res_auth.status_code == 200
        assert "The Bear" in res_auth.json()["shows"]

        # 3. DELETE /api/cowatch/shows
        res_del = client.delete("/api/cowatch/shows?token=testsecret&show=The+Bear")
        assert res_del.status_code == 200
        assert "The Bear" not in res_del.json()["shows"]

        # 4. POST /api/cowatch/devices (auth check & empty validation)
        res_dev_unauth = client.post("/api/cowatch/devices", json={"device": "Apple TV 4K"})
        assert res_dev_unauth.status_code == 401

        res_dev_empty = client.post("/api/cowatch/devices?token=testsecret", json={"device": "   "})
        assert res_dev_empty.status_code == 400

        res_dev_auth = client.post("/api/cowatch/devices?token=testsecret", json={"device": "Apple TV 4K"})
        assert res_dev_auth.status_code == 200
        assert "Apple TV 4K" in res_dev_auth.json()["devices"]

        # 5. DELETE /api/cowatch/devices
        res_del_dev_unauth = client.delete("/api/cowatch/devices?device=Apple+TV+4K")
        assert res_del_dev_unauth.status_code == 401

        res_del_dev = client.delete("/api/cowatch/devices?token=testsecret&device=Apple+TV+4K")
        assert res_del_dev.status_code == 200
        assert "Apple TV 4K" not in res_del_dev.json()["devices"]

        # 6. Demo mode for /api/cowatch/devices
        res_demo_add = client.post("/api/cowatch/devices?demo=true", json={"device": "Demo Shield"})
        assert res_demo_add.status_code == 200
        assert "Demo Shield" in res_demo_add.json()["devices"]

        res_demo_del = client.delete("/api/cowatch/devices?demo=true&device=Demo+Shield")
        assert res_demo_del.status_code == 200
        assert "Demo Shield" not in res_demo_del.json()["devices"]

        # Clean up test devices
        if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
            Config.CO_WATCH_DEVICES_DATA_FILE.unlink()
        cowatch_mgr._devices.clear()


def test_cowatch_manual_sync_endpoint():
    client = TestClient(app)

    # Mock partner client
    mock_partner_client = MagicMock()
    mock_partner_client.is_authenticated.return_value = True
    mock_partner_client.sync_history = AsyncMock(return_value={"added": {"movies": 1}})

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(user_mgr, "get_client", return_value=mock_partner_client):

        payload = {
            "media_type": "movie",
            "title": "Dune: Part Two",
            "year": 2024,
            "ids": {"imdb": "tt15239678"},
        }
        res = client.post("/api/cowatch/sync", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["target_user"] == "partner"
        mock_partner_client.sync_history.assert_called_once()


def test_multi_user_auth_flow():
    client = TestClient(app)

    mock_alice_client = MagicMock()
    mock_alice_client.generate_device_code = AsyncMock(return_value={
        "device_code": "alice_dev",
        "user_code": "ALICE123",
        "verification_url": "https://trakt.tv/activate",
        "expires_in": 600,
        "interval": 5,
    })
    mock_alice_client.poll_for_token = AsyncMock(return_value={"access_token": "alice_token"})

    with patch.object(user_mgr, "get_client", return_value=mock_alice_client):
        # 1. Start auth for alice
        res_start = client.post("/api/auth/start?user=alice")
        assert res_start.status_code == 200
        assert res_start.json()["user_code"] == "ALICE123"

        # 2. Poll for alice
        res_poll = client.post("/api/auth/poll", json={"device_code": "alice_dev", "user": "alice"})
        assert res_poll.status_code == 200
        assert res_poll.json()["status"] == "success"
        assert res_poll.json()["user"] == "alice"

        # 3. GET /auth?user=alice
        res_page = client.get("/auth?user=alice")
        assert res_page.status_code == 200
        assert "@alice" in res_page.text


@pytest.mark.asyncio
async def test_webhook_triggers_cowatch_sync():
    client = TestClient(app)

    mock_partner_client = MagicMock()
    mock_partner_client.is_authenticated.return_value = True
    mock_partner_client.sync_history = AsyncMock(return_value={"added": {"episodes": 1}})

    payload = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Forks",
            "grandparentTitle": "The Bear",
            "parentTitle": "Season 2",
            "index": 7,
            "parentIndex": 2,
            "year": 2023,
            "duration": 2000000,
            "viewOffset": 1950000,
        },
    }

    def fake_get_client(uname):
        if uname == "partner":
            return mock_partner_client
        return trakt

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_hist, \
         patch.object(user_mgr, "get_client", side_effect=fake_get_client):

        mock_stop.return_value = {"action": "scrobble"}
        mock_hist.return_value = {"added": {"episodes": 1}}

        res = client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res.status_code == 200

        # Allow background create_task(execute_cowatch_sync) to execute
        import asyncio
        await asyncio.sleep(0.05)

        mock_partner_client.sync_history.assert_called_once()


def test_library_filtering():
    """Verify that events from excluded or non-allowed Plex libraries are filtered out."""
    base_payload = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Family Trip 2024",
            "librarySectionTitle": "Home Videos",
            "duration": 60000,
            "viewOffset": 60000,
        },
    }

    # 1. Excluded library matches -> ignored
    res_excluded = parse_plex_webhook(base_payload, excluded_libraries=["Home Videos", "Fitness"])
    assert res_excluded is None

    # 2. Case-insensitive excluded match -> ignored
    res_excluded_case = parse_plex_webhook(base_payload, excluded_libraries=["home videos"])
    assert res_excluded_case is None

    # 3. Allowed library check: item not in allowed -> ignored
    res_not_allowed = parse_plex_webhook(base_payload, allowed_libraries=["Movies", "TV Shows"])
    assert res_not_allowed is None

    # 4. Item matches allowed library -> parsed successfully
    movie_payload = {
        "event": "media.scrobble",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Dune: Part Two",
            "librarySectionTitle": "Movies",
            "duration": 60000,
            "viewOffset": 60000,
        },
    }
    res_allowed = parse_plex_webhook(
        movie_payload,
        allowed_libraries=["Movies", "TV Shows"],
        excluded_libraries=["Home Videos"],
    )
    assert res_allowed is not None
    assert res_allowed.title == "Dune: Part Two"
    assert res_allowed.library_section_title == "Movies"


def test_parse_collection_media_specs():
    """Verify that video resolution, audio codec, and channels are extracted for Trakt collection payload."""
    payload = {
        "event": "library.new",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Oppenheimer",
            "year": 2023,
            "librarySectionTitle": "4K Movies",
            "Guid": [{"id": "imdb://tt15398776"}],
            "Media": [
                {
                    "videoResolution": "4k",
                    "audioCodec": "truehd",
                    "audioChannels": 8,
                }
            ],
        },
    }
    parsed = parse_plex_webhook(payload)
    assert parsed is not None
    assert parsed.video_resolution == "4k"
    assert parsed.audio_codec == "truehd"
    assert parsed.audio_channels == "7.1"

    col_payload = parsed.to_trakt_collection_payload()
    assert "movies" in col_payload
    movie = col_payload["movies"][0]
    assert movie["title"] == "Oppenheimer"
    assert movie["resolution"] == "uhd_4k"
    assert movie["audio"] == "dolby_truehd"
    assert movie["audio_channels"] == "7.1"
    assert movie["media_type"] == "digital"
    assert movie["ids"]["imdb"] == "tt15398776"


@pytest.mark.asyncio
async def test_trakt_client_sync_collection():
    """Verify TraktClient.sync_collection delegates to POST /sync/collection."""
    with patch.object(trakt, "_post_authenticated", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"added": {"movies": 1, "episodes": 0}}
        payload = {"movies": [{"title": "Oppenheimer", "year": 2023}]}
        res = await trakt.sync_collection(payload)
        assert res["added"]["movies"] == 1
        mock_post.assert_called_once()
        assert "/sync/collection" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_webhook_library_new_collection_flow():
    """Verify that library.new webhooks trigger Trakt collection synchronization."""
    client = TestClient(app)
    payload = {
        "event": "library.new",
        "Account": {"title": "selits"},
        "Metadata": {
            "type": "movie",
            "title": "Alien: Romulus",
            "year": 2024,
            "Guid": [{"id": "imdb://tt18412256"}],
            "Media": [{"videoResolution": "1080", "audioCodec": "eac3", "audioChannels": 6}],
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_collection", new_callable=AsyncMock) as mock_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_notif, \
         patch.object(Config, "SYNC_COLLECTION", True):

        mock_sync.return_value = {"added": {"movies": 1}}

        res = client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "collection"
        assert mock_sync.called
        sent_payload = mock_sync.call_args[0][0]
        assert sent_payload["movies"][0]["resolution"] == "hd_1080p"
        assert sent_payload["movies"][0]["audio"] == "dolby_digital_plus"
        assert sent_payload["movies"][0]["audio_channels"] == "5.1"

    # Verify when SYNC_COLLECTION=False, event is ignored
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(Config, "SYNC_COLLECTION", False):
        res_disabled = client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res_disabled.status_code == 200
        assert res_disabled.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_queue_collection_retry():
    """Verify that queued sync_collection events are successfully processed by background worker."""
    col_payload = {"movies": [{"title": "Gladiator II", "year": 2024}]}
    item_id = queue_mgr.enqueue("sync_collection", col_payload, username="default")

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_collection", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"movies": 1}}

        stats = await process_queue(trakt, queue_mgr)
        assert stats["succeeded"] >= 1
        assert queue_mgr.get_pending_count() == 0


def test_prometheus_metrics_endpoint():
    """Verify that /metrics exports valid Prometheus text exposition format."""
    client = TestClient(app)
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    body = res.text

    # Check for Prometheus metric definitions
    assert "# HELP plex_trakt_uptime_seconds" in body
    assert "# TYPE plex_trakt_uptime_seconds gauge" in body
    assert "# HELP plex_trakt_requests_total" in body
    assert "# HELP plex_trakt_scrobbles_total" in body
    assert "# HELP plex_trakt_collections_total" in body
    assert "# HELP plex_trakt_active_streams" in body
    assert "plex_trakt_uptime_seconds" in body


@pytest.mark.asyncio
async def test_ntfy_and_pushover_notifications():
    """Verify Ntfy and Pushover notification builders and dispatch."""
    media = ParsedMedia(
        event="library.new",
        username="selits",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        video_resolution="4k",
        audio_codec="truehd",
        ids={"imdb": "tt15239678"},
    )

    mock_http = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_http.post = AsyncMock(return_value=mock_resp)

    # 1. Ntfy delivery
    with patch.object(Config, "NTFY_URL", "https://ntfy.sh/my_test_topic"), \
         patch.object(Config, "NTFY_AUTH_TOKEN", "tk_test"):
        res_ntfy = await notifier.send_ntfy(media, "collection", client=mock_http)
        assert res_ntfy is True
        assert mock_http.post.called
        call_headers = mock_http.post.call_args[1]["headers"]
        assert "Trakt: Dune: Part Two" in call_headers["Title"]
        assert call_headers["Authorization"] == "Bearer tk_test"

    # 2. Pushover delivery
    mock_http.post.reset_mock()
    with patch.object(Config, "PUSHOVER_USER_KEY", "u_key"), \
         patch.object(Config, "PUSHOVER_API_TOKEN", "t_tok"):
        res_push = await notifier.send_pushover(media, "collection", client=mock_http)
        assert res_push is True
        assert mock_http.post.called
        post_data = mock_http.post.call_args[1]["data"]
        assert post_data["user"] == "u_key"
        assert "Dune: Part Two" in post_data["title"]


def test_backup_and_restore_endpoints():
    """Verify zip backup export, safe restoration, and security controls."""
    client = TestClient(app)
    # 1. Unauthenticated backup request -> 401
    client.cookies.clear()
    with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
        res_unauth = client.get("/api/backup")
        assert res_unauth.status_code == 401

        # 2. Authenticated backup request -> 200 with zip content
        client.cookies.set("admin_token", "test_secret")
        res_backup = client.get("/api/backup")
        assert res_backup.status_code == 200
        assert "application/zip" in res_backup.headers["content-type"]
        assert "attachment; filename=" in res_backup.headers["content-disposition"]

        # Verify zip content structure
        import io
        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(res_backup.content))
        namelist = zf.namelist()
        assert isinstance(namelist, list)

        # 3. Restore test: create a zip and upload
        test_zip_buf = io.BytesIO()
        with zipfile.ZipFile(test_zip_buf, "w") as test_zf:
            test_zf.writestr("data/cowatch_shows.json", json.dumps(["Severance", "Succession"]))
            test_zf.writestr("data/cowatch_devices.json", json.dumps(["Living Room Apple TV"]))
            # Zip slip attack attempt (must be skipped safely)
            test_zf.writestr("../evil_file.txt", "evil")

        test_zip_buf.seek(0)
        files = {"backup_file": ("backup.zip", test_zip_buf.getvalue(), "application/zip")}
        res_restore = client.post("/api/restore", files=files)
        assert res_restore.status_code == 200
        restore_data = res_restore.json()
        assert restore_data["status"] == "success"
        assert "data/cowatch_shows.json" in restore_data["restored"]
        assert "data/cowatch_devices.json" in restore_data["restored"]
        # Malicious path was ignored
        assert "../evil_file.txt" not in restore_data["restored"]

        # 4. Unauthenticated restore request -> 401
        client.cookies.clear()
        res_restore_denied = client.post("/api/restore", files=files)
        assert res_restore_denied.status_code == 401

        # Cleanup restored test devices
        if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
            Config.CO_WATCH_DEVICES_DATA_FILE.unlink()
        cowatch_mgr._devices.clear()


def test_dashboard_privacy_shield_and_script_syntax():
    """Verify that HTML dashboards have balanced scripts and robust non-admin privacy shielding."""
    client = TestClient(app)
    playback_mgr.clear()
    recent_events.clear()

    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"), \
         patch.object(Config, "CO_WATCH_USER", "bon.vivant"), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["selits's Fire TV", "Google TV"]):
        cowatch_mgr._shows = ["Secret CoWatch Show Alpha", "Secret CoWatch Show Beta"]
        cowatch_mgr._devices = ["selits's Fire TV", "Google TV"]

        try:
            # 1. Unauthenticated / Non-Admin Dashboard Request
            client.cookies.clear()
            res = client.get("/")
            assert res.status_code == 200
            html = res.text

            # Validate that scripts have perfectly balanced curly braces (no JavaScript syntax errors)
            import re
            scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
            assert len(scripts) >= 1
            for idx, s in enumerate(scripts):
                open_count = s.count('{')
                close_count = s.count('}')
                assert open_count == close_count, f"Script {idx} in dashboard has unbalanced braces: open={open_count}, close={close_count}"

            # Privacy checks for non-admin viewers:
            # Show titles must be hidden
            assert "Secret CoWatch Show Alpha" not in html
            assert "Secret CoWatch Show Beta" not in html
            assert "2 shared shows configured" in html
            assert "Unlock admin access to view titles" in html

            # Personal device names and Devices rule must be completely hidden
            assert "selits's Fire TV" not in html
            assert "Google TV" not in html
            assert "Devices:" not in html

            # Partner username completely masked
            assert "@bon.vivant" not in html
            assert "@bo********" not in html
            assert "@●●●●●●●●" in html

            # Webhook secret masked
            assert "testsecret" not in html

            # 2. Authenticated Admin Dashboard Request
            client.cookies.set("admin_token", "testsecret")
            res_admin = client.get("/")
            assert res_admin.status_code == 200
            html_admin = res_admin.text

            # Admin sees full show titles and device names in allowed devices whitelist
            assert "Secret CoWatch Show Alpha" in html_admin
            assert "Secret CoWatch Show Beta" in html_admin
            assert "selits&#x27;s Fire TV" in html_admin
            assert "Google TV" in html_admin
            assert "Allowed Devices Whitelist" in html_admin
            assert "testsecret" in html_admin
        finally:
            client.cookies.clear()
            cowatch_mgr._shows.clear()
            cowatch_mgr._devices.clear()


def test_auth_page_script_syntax():
    """Verify that /auth page scripts (both locked and unlocked) have balanced braces."""
    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # 1. Unauthenticated locked /auth
        client.cookies.clear()
        res_locked = client.get("/auth")
        assert res_locked.status_code == 401
        import re
        scripts_locked = re.findall(r'<script>(.*?)</script>', res_locked.text, re.DOTALL)
        assert len(scripts_locked) >= 1
        for idx, s in enumerate(scripts_locked):
            assert s.count('{') == s.count('}'), f"Locked auth script {idx} unbalanced"

        # 2. Authenticated /auth
        client.cookies.set("admin_token", "testsecret")
        res = client.get("/auth")
        assert res.status_code == 200
        scripts = re.findall(r'<script>(.*?)</script>', res.text, re.DOTALL)
        assert len(scripts) >= 1
        for idx, s in enumerate(scripts):
            assert s.count('{') == s.count('}'), f"Auth script {idx} unbalanced"
        client.cookies.clear()


@pytest.mark.asyncio
async def test_sonarr_client_search_and_cache():
    from app.clients.sonarr_client import SonarrClient
    sc = SonarrClient(base_url="http://sonarr.local:8989", api_key="secretkey")
    assert sc.is_configured is True

    mock_series_data = [
        {"title": "Severance", "year": 2022, "tvdbId": 371980, "imdbId": "tt11280740", "status": "continuing"},
        {"title": "The Bear", "year": 2022, "tvdbId": 412497, "imdbId": "tt14452776", "status": "continuing"},
        {"title": "House of the Dragon", "year": 2022, "tvdbId": 371572, "imdbId": "tt11198330", "status": "continuing"},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_series_data

    mock_http = AsyncMock()
    mock_http.get.return_value = mock_resp

    # First search
    res = await sc.search_series("sev", client=mock_http)
    assert len(res) == 1
    assert res[0]["title"] == "Severance"
    assert mock_http.get.call_count == 1

    # Second search should hit memory cache
    res2 = await sc.search_series("bear", client=mock_http)
    assert len(res2) == 1
    assert res2[0]["title"] == "The Bear"
    assert mock_http.get.call_count == 1  # Cache hit, no second network request

    # Search with exclude parameter
    res3 = await sc.search_series("", client=mock_http, exclude=["The Bear", "Severance"])
    assert len(res3) == 1
    assert res3[0]["title"] == "House of the Dragon"


def test_sonarr_webhook_parsing():
    from app.clients.sonarr_client import parse_sonarr_webhook

    # Test event
    ev_type, trakt_p, parsed = parse_sonarr_webhook({"eventType": "Test"})
    assert ev_type == "test"
    assert trakt_p is None

    # Download event
    dl_payload = {
        "eventType": "Download",
        "series": {
            "title": "Severance",
            "year": 2022,
            "tvdbId": 371980,
            "imdbId": "tt11280740",
        },
        "episodes": [
            {
                "episodeNumber": 1,
                "seasonNumber": 1,
                "title": "Good News About Hell",
            }
        ],
        "episodeFile": {
            "quality": "WEBDL-1080p",
            "releaseGroup": "FLUX",
        },
    }
    ev_type, trakt_p, parsed = parse_sonarr_webhook(dl_payload)
    assert ev_type == "download"
    assert trakt_p is not None
    assert trakt_p["shows"][0]["title"] == "Severance"
    assert trakt_p["shows"][0]["ids"]["tvdb"] == 371980
    assert trakt_p["shows"][0]["seasons"][0]["episodes"][0]["resolution"] == "hd_1080p"
    assert parsed.show_title == "Severance"
    assert parsed.season == 1
    assert parsed.episode == 1


def test_radarr_webhook_parsing():
    from app.clients.sonarr_client import parse_radarr_webhook

    ev_type, trakt_p, parsed = parse_radarr_webhook({"eventType": "Test"})
    assert ev_type == "test"

    dl_payload = {
        "eventType": "Download",
        "movie": {
            "title": "Dune: Part Two",
            "year": 2024,
            "tmdbId": 693134,
            "imdbId": "tt15239678",
        },
        "movieFile": {
            "quality": "WEBDL-2160p",
        },
    }
    ev_type, trakt_p, parsed = parse_radarr_webhook(dl_payload)
    assert ev_type == "download"
    assert trakt_p["movies"][0]["title"] == "Dune: Part Two"
    assert trakt_p["movies"][0]["ids"]["tmdb"] == 693134
    assert trakt_p["movies"][0]["resolution"] == "uhd_4k"
    assert parsed.title == "Dune: Part Two"


def test_sonarr_api_routes():
    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # 1. /api/sonarr/shows requires admin
        client.cookies.clear()
        res_unauth = client.get("/api/sonarr/shows")
        assert res_unauth.status_code == 401

        # 2. /api/sonarr/shows with admin
        client.cookies.set("admin_token", "testsecret")
        res_auth = client.get("/api/sonarr/shows")
        assert res_auth.status_code == 200
        data = res_auth.json()
        assert "configured" in data
        assert "shows" in data

        # 2b. /api/sonarr/shows passes exclude from cowatch_mgr to search_series
        with patch.object(sonarr, "base_url", "http://sonarr:8989"), \
             patch.object(sonarr, "api_key", "secretkey"), \
             patch.object(sonarr, "search_series", new_callable=AsyncMock) as mock_search, \
             patch.object(cowatch_mgr, "get_shows", return_value=["The Bear"]):
            mock_search.return_value = [{"title": "Severance"}]
            res_filtered = client.get("/api/sonarr/shows?q=sev")
            assert res_filtered.status_code == 200
            mock_search.assert_called_once_with(query="sev", limit=15, exclude=["The Bear"])

        # 3. GET /sonarr and GET /radarr info
        assert client.get("/sonarr").status_code == 200
        assert client.get("/radarr").status_code == 200

        # 4. POST /sonarr unauthorized
        res_sonarr_unauth = client.post("/sonarr", json={"eventType": "Test"})
        assert res_sonarr_unauth.status_code == 401

        # 5. POST /sonarr authorized test event
        res_sonarr_test = client.post("/sonarr?token=testsecret", json={"eventType": "Test"})
        assert res_sonarr_test.status_code == 200
        assert res_sonarr_test.json()["status"] == "success"

        # 6. POST /radarr authorized test event
        res_radarr_test = client.post("/radarr?token=testsecret", json={"eventType": "Test"})
        assert res_radarr_test.status_code == 200
        assert res_radarr_test.json()["status"] == "success"

        # 7. Health check includes sonarr
        res_health = client.get("/health")
        assert res_health.status_code == 200
        assert "sonarr" in res_health.json()

        client.cookies.clear()


def test_sonarr_webhook_collection_sync():
    dl_payload = {
        "eventType": "Download",
        "series": {
            "title": "Severance",
            "year": 2022,
            "tvdbId": 371980,
        },
        "episodes": [
            {
                "episodeNumber": 1,
                "seasonNumber": 1,
                "title": "Good News About Hell",
            }
        ],
        "episodeFile": {
            "quality": "WEBDL-1080p",
        },
    }
    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", ""):
        with patch("app.main.user_mgr.get_client") as mock_get_client:
            mock_trakt = AsyncMock()
            mock_trakt.sync_collection.return_value = {"added": {"episodes": 1}}
            mock_get_client.return_value = mock_trakt

            res = client.post("/sonarr", json=dl_payload)
            assert res.status_code == 200
            assert res.json()["status"] == "success"
            assert mock_trakt.sync_collection.called


def test_720p_resolution_mapping():
    from app.plex_parser import map_plex_resolution
    from app.clients.sonarr_client import map_arr_resolution

    # Plex mappings
    assert map_plex_resolution("720") == "hd_720p"
    assert map_plex_resolution("720p") == "hd_720p"
    assert map_plex_resolution("1080") == "hd_1080p"
    assert map_plex_resolution("1080p") == "hd_1080p"
    assert map_plex_resolution("4k") == "uhd_4k"

    # Sonarr / Radarr quality mappings
    assert map_arr_resolution("HDTV-720p") == "hd_720p"
    assert map_arr_resolution("WEBDL-720p") == "hd_720p"
    assert map_arr_resolution("Bluray-720p") == "hd_720p"
    assert map_arr_resolution("720p") == "hd_720p"
    assert map_arr_resolution({"quality": {"name": "HDTV-720p", "resolution": 720}}) == "hd_720p"


def test_modular_package_structure():
    """Verify clean modular app package structure, template loading, and base directory resolution."""
    import main as root_main
    import app.main as app_main
    import app.config as app_config
    import app.services.cowatch_manager as app_cw
    import app.services.user_manager as app_um
    import app.services.notifier as app_notif
    import app.services.playback_manager as app_pm
    import app.services.queue_manager as app_qm
    import app.clients.trakt_client as app_tc
    import app.clients.sonarr_client as app_sc
    import app.plex_parser as app_pp
    import app.metrics as app_m
    import os

    # 1. Main entrypoint re-exports app
    assert root_main.app is app_main.app

    # 2. Template verification
    templates_dir = app_main.TEMPLATES_DIR
    assert templates_dir.is_dir()
    assert (templates_dir / "dashboard.html").is_file()
    assert (templates_dir / "auth.html").is_file()
    assert (templates_dir / "auth_locked.html").is_file()

    # 3. Base directory is project root
    assert (app_config.Config.BASE_DIR / "requirements.txt").is_file()
    assert (app_config.Config.BASE_DIR / "main.py").is_file()
    assert (app_config.Config.BASE_DIR / "auth.py").is_file()

    # 4. Verify no old root shim files exist in base folder
    root_files = set(os.listdir(app_config.Config.BASE_DIR))
    for deprecated in [
        "config.py",
        "cowatch_manager.py",
        "metrics.py",
        "notifier.py",
        "playback_manager.py",
        "plex_parser.py",
        "queue_manager.py",
        "sonarr_client.py",
        "trakt_client.py",
        "user_manager.py",
    ]:
        assert deprecated not in root_files, f"{deprecated} should no longer exist in root directory"


def test_dashboard_footer_and_repo_link():
    """Verify that the dashboard renders the version badge and GitHub repository links."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "https://github.com/selits/omniscrobble" in html
    assert "v2.5.0" in html
    assert "https://github.com/selits/omniscrobble/releases" in html
    assert "https://github.com/selits/omniscrobble#readme" in html
    assert "Auto-refresh (30s)" in html
    assert '<input type="checkbox" id="auto-refresh-toggle" onchange="toggleAutoRefresh(this)">' in html


def test_media_stop_below_threshold():
    client = TestClient(app)
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

        res = client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "playback_stopped"
        assert scrobble_stats["total"] == initial_total
        mock_stop.assert_called_once()
        mock_dispatch.assert_not_called()


def test_media_stop_below_one_percent():
    client = TestClient(app)
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

        res = client.post("/webhook", data={"payload": json.dumps(stop_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "playback_stopped"
        assert data["result"]["status"] == "ignored"
        assert scrobble_stats["total"] == initial_total
        mock_stop.assert_not_called()
        mock_dispatch.assert_not_called()


def test_notifier_zero_and_custom_progress():
    notifier_inst = Notifier(Config)
    media_zero = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Critical Failure",
        show="The Ark",
        season=3,
        episode=9,
        progress=0.0,
    )
    discord_payload = notifier_inst.build_discord_payload(media_zero, "mark_watched")
    embed = discord_payload["embeds"][0]
    assert any(f["name"] == "Progress" and f["value"] == "0.0%" for f in embed["fields"])
    assert "0.0% watched" in embed["description"]

    media_partial = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Critical Failure",
        show="The Ark",
        season=3,
        episode=9,
        progress=85.5,
    )
    discord_payload_partial = notifier_inst.build_discord_payload(media_partial, "mark_watched")
    embed_partial = discord_payload_partial["embeds"][0]
    assert any(f["name"] == "Progress" and f["value"] == "85.5%" for f in embed_partial["fields"])
    assert "85.5% watched" in embed_partial["description"]


def test_demo_dashboard_page():
    client = TestClient(app)
    res = client.get("/demo")
    assert res.status_code == 200
    html = res.text
    assert "Demo Mode Active" in html
    assert "Severance" in html
    assert "demo_viewer" in html
    assert "demo_partner" in html
    assert "View Logs" in html
    assert "Exit Demo" in html
    assert "✕ Exit Demo" in html
    assert "1,428" in html or "1428" in html


def test_demo_query_param_on_root():
    client = TestClient(app)
    res_demo = client.get("/?demo=true")
    assert res_demo.status_code == 200
    assert "Demo Mode Active" in res_demo.text
    assert "✕ Exit Demo" in res_demo.text

    # Without ?demo=true, demo banner should not be present, but header button and modal link are
    res_normal = client.get("/")
    assert res_normal.status_code == 200
    assert "Demo Mode Active" not in res_normal.text
    assert "🎭 Demo" in res_normal.text
    assert "Try Demo Mode" in res_normal.text


def test_demo_api_endpoints():
    client = TestClient(app)

    # 1. Playback
    res = client.get("/api/playback?demo=true")
    assert res.status_code == 200
    data = res.json()
    assert len(data["active_sessions"]) == 1
    assert "Severance" in data["active_sessions"][0]["title"]
    assert data["recently_finished"] is None

    # 2. Events
    res = client.get("/api/events?demo=true")
    assert res.status_code == 200
    events = res.json()["events"]
    assert len(events) >= 5
    assert any("Severance" in e["title"] for e in events)

    # 3. Clear events
    res = client.post("/api/events/clear?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "cleared"

    # 4. Queue retry & clear
    res = client.post("/api/queue/retry?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = client.post("/api/queue/clear?demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 5. Search
    res = client.get("/api/search?demo=true&query=Severance")
    assert res.status_code == 200
    results = res.json()["results"]
    assert any("Severance" in r.get("show", {}).get("title", "") for r in results)

    # 6. Manual scrobble
    res = client.post("/api/scrobble/manual?demo=true", json={"media_type": "movie", "title": "Inception"})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # 7. Cowatch shows
    res = client.post("/api/cowatch/shows?demo=true", json={"show": "Ted Lasso"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = client.delete("/api/cowatch/shows?show=Ted%20Lasso&demo=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 8. Sonarr shows
    res = client.get("/api/sonarr/shows?demo=true")
    assert res.status_code == 200
    shows = res.json()["shows"]
    assert len(shows) >= 5

    res_q = client.get("/api/sonarr/shows?demo=true&q=Severance")
    assert res_q.status_code == 200
    assert any(s["title"] == "Severance" for s in res_q.json()["shows"])

    # 9. Cowatch sync
    res = client.post("/api/cowatch/sync?demo=true", json={"media_type": "episode", "title": "Severance"})
    assert res.status_code == 200
    assert res.json()["status"] == "success"


def test_api_logs_access_control():
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "supersecret_test_token"
        client = TestClient(app)

        # Non-admin request should receive 401
        res = client.get("/api/logs")
        assert res.status_code == 401

        # Demo mode allows log viewing without admin auth
        res_demo = client.get("/api/logs?demo=true")
        assert res_demo.status_code == 200
        demo_data = res_demo.json()
        assert "simulated journal" in demo_data["source"]
        assert len(demo_data["lines"]) > 0

        # Admin unlocked request succeeds
        client.cookies.set("admin_token", "supersecret_test_token")
        res_admin = client.get("/api/logs?lines=20")
        assert res_admin.status_code == 200
        admin_data = res_admin.json()
        assert "source" in admin_data
        assert isinstance(admin_data["lines"], list)
    finally:
        Config.WEBHOOK_SECRET = orig_secret


def test_log_sanitization_and_buffer():
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "my_webhook_secret_value"

        # Test token masking in query param
        line1 = "GET /webhook?token=my_webhook_secret_value HTTP/1.1 200"
        sanitized1 = log_mgr.sanitize_line(line1)
        assert "my_webhook_secret_value" not in sanitized1
        assert "token=●●●●" in sanitized1 or "●●●●" in sanitized1

        # Test Bearer token masking
        line2 = "Sending auth header: Bearer abc123secrettoken456"
        sanitized2 = log_mgr.sanitize_line(line2)
        assert "abc123secrettoken456" not in sanitized2
        assert "Bearer ●●●●" in sanitized2

        # Test header masking
        line3 = "Header received x-webhook-secret: raw_secret_pass"
        sanitized3 = log_mgr.sanitize_line(line3)
        assert "raw_secret_pass" not in sanitized3
        assert "x-webhook-secret: ●●●●" in sanitized3

        # Test buffer capture
        import logging
        test_log = logging.getLogger("test_buffer_logger")
        test_log.setLevel(logging.DEBUG)
        test_msg = "Scrobble event test unique buffer verify 987654"
        test_log.warning(test_msg)

        buffer_lines = log_mgr.get_buffer_logs(lines=50)
        assert any(test_msg in l for l in buffer_lines)
    finally:
        Config.WEBHOOK_SECRET = orig_secret


def test_dashboard_mobile_responsiveness():
    """Verify that the dashboard and demo pages contain mobile responsive viewport and CSS structures."""
    client = TestClient(app)

    for path in ["/", "/demo"]:
        resp = client.get(path)
        assert resp.status_code == 200
        html = resp.text

        # 1. Viewport meta tag
        assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in html

        # 2. CSS Media query for mobile viewports
        assert "@media (max-width: 640px)" in html
        assert "-webkit-overflow-scrolling: touch" in html

        # 3. Mobile layout classes
        assert 'class="title-brand"' in html
        assert 'class="title-sub"' in html
        assert 'class="cowatch-grid"' in html
        assert 'class="cowatch-account-row"' in html
        assert 'class="cowatch-form-row"' in html
        assert 'class="webhook-row"' in html
        assert 'class="activity-header"' in html
        assert 'class="activity-actions"' in html
        assert 'class="table-container"' in html
        assert 'class="modal-dialog' in html
        assert 'class="logs-toolbar"' in html
        assert 'class="footer"' in html
        assert '.cowatch-account-row' in html
        assert '.cowatch-grid > div + div' in html
        assert 'Universal Media Scrobbler &amp; Multi-Tracker Hub' in html
        assert 'Media Server Webhook Endpoints' in html
        assert 'Tracker Only &bull; Mark' in html
        assert 'omniscrobble_auto_refresh' in html
        assert 'grid-template-columns: 1fr !important' in html
        assert 'word-break: break-word' in html
        assert 'settings-trk-cat-btn' in html
        assert 'scrobble-trk-kitsu' in html


@pytest.mark.asyncio
async def test_trakt_sync_watchlist():
    """Verify TraktClient.sync_watchlist posts payload to /sync/watchlist."""
    with patch.object(trakt, "_post_authenticated", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"added": {"movies": 1, "shows": 0}}
        res = await trakt.sync_watchlist({"movies": [{"title": "Dune", "year": 2021}]})
        mock_post.assert_awaited_once_with(f"{trakt.api_url}/sync/watchlist", {"movies": [{"title": "Dune", "year": 2021}]})
        assert res["added"]["movies"] == 1


def test_watchlist_api_endpoints():
    """Test POST /api/watchlist for adding movies and shows to Trakt watchlist."""
    client = TestClient(app)

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"shows": 1, "movies": 1}}

        # 1. Demo mode
        resp_demo = client.post("/api/watchlist?demo=true", json={"media_type": "movie", "title": "Inception"})
        assert resp_demo.status_code == 200
        assert resp_demo.json()["status"] == "success"

        # 2. Authenticated admin request
        resp = client.post("/api/watchlist", json={"media_type": "show", "title": "Severance", "year": 2022})
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_sync.assert_awaited_once()

        # 3. Unauthorized access check when WEBHOOK_SECRET is active
        orig_secret = Config.WEBHOOK_SECRET
        try:
            Config.WEBHOOK_SECRET = "secret_pass_123"
            resp_unauth = client.post("/api/watchlist", json={"media_type": "movie", "title": "Dune"})
            assert resp_unauth.status_code == 401

            resp_auth = client.post("/api/watchlist?token=secret_pass_123", json={"media_type": "movie", "title": "Dune"})
            assert resp_auth.status_code == 200
        finally:
            Config.WEBHOOK_SECRET = orig_secret


def test_cowatch_settings_and_movie_toggle():
    """Test POST /api/cowatch/settings to dynamically toggle movie co-watching."""
    client = TestClient(app)
    orig_movies = cowatch_mgr.config.CO_WATCH_MOVIES
    try:
        # Enable movies
        resp_enable = client.post("/api/cowatch/settings", json={"co_watch_movies": True})
        assert resp_enable.status_code == 200
        assert resp_enable.json()["co_watch_movies"] is True
        assert cowatch_mgr.config.CO_WATCH_MOVIES is True

        # Disable movies
        resp_disable = client.post("/api/cowatch/settings", json={"co_watch_movies": False})
        assert resp_disable.status_code == 200
        assert resp_disable.json()["co_watch_movies"] is False
        assert cowatch_mgr.config.CO_WATCH_MOVIES is False
    finally:
        cowatch_mgr.set_cowatch_movies(orig_movies)


def test_cowatch_env_merging(tmp_path):
    """Verify CowatchManager merges .env CO_WATCH_SHOWS into existing JSON file."""
    shows_file = tmp_path / "cowatch_shows.json"
    shows_file.write_text(json.dumps(["Existing Show A", "Existing Show B"]))

    cfg = MagicMock()
    cfg.CO_WATCH_DATA_FILE = shows_file
    cfg.CO_WATCH_SHOWS = ["Env Show 1", "Existing Show A"]
    cfg.CO_WATCH_USER = "partner"
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = []

    mgr = CowatchManager(config=cfg)
    shows = mgr.get_shows()
    assert "Existing Show A" in shows
    assert "Existing Show B" in shows
    assert "Env Show 1" in shows
    assert len(shows) == 3


def test_cowatch_devices_env_merging_and_crud(tmp_path):
    """Verify CowatchManager manages devices dynamically and merges .env CO_WATCH_PLAYERS."""
    devices_file = tmp_path / "cowatch_devices.json"
    devices_file.write_text(json.dumps(["Living Room Apple TV", "Shield TV"]))

    cfg = MagicMock()
    cfg.CO_WATCH_DATA_FILE = tmp_path / "cowatch_shows.json"
    cfg.CO_WATCH_DEVICES_DATA_FILE = devices_file
    cfg.CO_WATCH_SHOWS = []
    cfg.CO_WATCH_USER = "partner"
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = ["Env Device 1", "Living Room Apple TV"]

    mgr = CowatchManager(config=cfg)
    devices = mgr.get_devices()
    assert "Living Room Apple TV" in devices
    assert "Shield TV" in devices
    assert "Env Device 1" in devices
    assert len(devices) == 3

    # Add device
    mgr.add_device("Bedroom TV")
    assert "Bedroom TV" in mgr.get_devices()

    # Re-reading from file should contain added device
    mgr2 = CowatchManager(config=cfg)
    assert "Bedroom TV" in mgr2.get_devices()

    # Remove device
    mgr2.remove_device("Shield TV")
    assert "Shield TV" not in mgr2.get_devices()


def test_cowatch_eligibility_reasons():
    """Verify check_cowatch_eligibility returns accurate boolean and informative reason strings."""
    cfg = MagicMock()
    cfg.CO_WATCH_USER = "jane"
    cfg.CO_WATCH_SHOWS = ["Lanterns", "Animal Control"]
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = ["Apple TV", "Living Room"]

    mgr = CowatchManager(config=cfg)

    # 1. Partner self playback
    media_self = ParsedMedia(
        event="media.scrobble", username="jane", media_type="episode",
        title="Ep 1", show_title="Lanterns", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_self)
    assert not eligible
    assert "partner" in reason.lower()

    # 2. Player not allowed
    media_wrong_player = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Lanterns", progress=100.0, player="Bedroom Phone"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_wrong_player)
    assert not eligible
    assert "CO_WATCH_PLAYERS" in reason

    # 3. Eligible show
    media_ok_show = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Lanterns (2025)", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_ok_show)
    assert eligible
    assert "Shared show" in reason

    # 4. Solo show not in list
    media_solo_show = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Breaking Bad", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_solo_show)
    assert not eligible
    assert "not in shared whitelist" in reason

    # 5. Movie disabled vs enabled
    media_movie = ParsedMedia(
        event="media.scrobble", username="selits", media_type="movie",
        title="Inception", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_movie)
    assert not eligible
    assert "disabled" in reason.lower()

    mgr.set_cowatch_movies(True)
    eligible, reason = mgr.check_cowatch_eligibility(media_movie)
    assert eligible
    assert "enabled" in reason.lower()


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


def test_synthetic_test_webhook():
    """Test POST /api/test/webhook for dry-run simulation and recent event generation."""
    client = TestClient(app)

    payload = {
        "event": "media.scrobble",
        "media_type": "episode",
        "title": "Synthetic Episode",
        "show_title": "Lanterns",
        "season": 1,
        "episode": 7,
        "progress": 100.0,
        "execute_trakt": False,
    }
    resp = client.post("/api/test/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["parsed"]["show_title"] == "Lanterns"
    assert data["parsed"]["duration_ms"] == 3600000
    assert "eligible" in data["cowatch"]

    # Verify event was recorded in recent_events
    events_resp = client.get("/api/events")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    assert any("Lanterns S01E07" in e["title"] for e in events)


def test_health_includes_radarr():
    """Verify /health reports Radarr integration info alongside Sonarr."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "radarr" in data
    assert data["radarr"]["endpoint"] == "/radarr"
    assert "sonarr" in data


@pytest.mark.asyncio
async def test_process_queue_sync_watchlist():
    """Verify that queued sync_watchlist events are properly dequeued and sent to Trakt."""
    queue_mgr.clear_queue()
    watchlist_payload = {"movies": [{"title": "Inception", "year": 2010}]}
    queue_mgr.enqueue("sync_watchlist", watchlist_payload, username="default")
    assert queue_mgr.get_pending_count() == 1

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"movies": 1}}

        stats = await process_queue(trakt, queue_mgr)
        assert stats["succeeded"] >= 1
        assert queue_mgr.get_pending_count() == 0
        mock_sync.assert_awaited_once_with(watchlist_payload)


def test_watchlist_transient_error_enqueues():
    """Verify POST /api/watchlist enqueues the payload if Trakt returns a transient HTTP error."""
    client = TestClient(app)
    queue_mgr.clear_queue()

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"error": "Service Unavailable", "status_code": 503}

        res = client.post("/api/watchlist", json={"media_type": "movie", "title": "Interstellar", "year": 2014})
        assert res.status_code == 200
        assert queue_mgr.get_pending_count() == 1
        pending = queue_mgr.get_pending()
        assert pending[0]["event_type"] == "sync_watchlist"
        assert pending[0]["payload"]["movies"][0]["title"] == "Interstellar"

    queue_mgr.clear_queue()


@pytest.mark.asyncio
async def test_synthetic_test_webhook_execute_trakt():
    """Verify POST /api/test/webhook with execute_trakt=True syncs history and triggers co-watch."""
    client = TestClient(app)

    payload = {
        "event": "media.scrobble",
        "media_type": "episode",
        "title": "Lanterns S01E07",
        "show_title": "Lanterns",
        "season": 1,
        "episode": 7,
        "progress": 100.0,
        "execute_trakt": True,
    }

    mock_partner_client = MagicMock()
    mock_partner_client.is_authenticated.return_value = True
    mock_partner_client.sync_history = AsyncMock(return_value={"added": {"episodes": 1}})

    def fake_get_client(uname=None):
        if uname == "partner":
            return mock_partner_client
        return trakt

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync, \
         patch.object(user_mgr, "get_client", side_effect=fake_get_client):
        mock_sync.return_value = {"added": {"episodes": 1}}

        resp = client.post("/api/test/webhook", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["cowatch"]["eligible"] is True
        assert mock_sync.called
        assert mock_partner_client.sync_history.called


def test_synthetic_test_webhook_unauthorized():
    """Verify POST /api/test/webhook requires admin authentication when WEBHOOK_SECRET is set."""
    client = TestClient(app)
    payload = {"event": "media.scrobble", "media_type": "movie", "title": "Test Movie"}

    with patch.object(Config, "WEBHOOK_SECRET", "secret_admin_key"):
        # Unauthenticated -> 401
        res_unauth = client.post("/api/test/webhook", json=payload)
        assert res_unauth.status_code == 401

        # Query param ?token= -> 200
        res_token = client.post("/api/test/webhook?token=secret_admin_key", json=payload)
        assert res_token.status_code == 200

        # Admin cookie -> 200
        client.cookies.set("admin_token", "secret_admin_key")
        res_cookie = client.post("/api/test/webhook", json=payload)
        assert res_cookie.status_code == 200
        client.cookies.clear()


def test_radarr_webhook_collection_sync():
    """Verify Radarr download webhooks sync movie to Trakt collection and increment stats."""
    client = TestClient(app)
    dl_payload = {
        "eventType": "Download",
        "movie": {
            "title": "Dune: Part Two",
            "year": 2024,
            "tmdbId": 693134,
            "imdbId": "tt15239678",
        },
        "movieFile": {
            "quality": "WEBDL-2160p",
        },
    }

    initial_collections = scrobble_stats.get("collections", 0)

    with patch.object(Config, "WEBHOOK_SECRET", ""), \
         patch.object(Config, "SYNC_COLLECTION", True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_collection", new_callable=AsyncMock) as mock_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):

        mock_sync.return_value = {"added": {"movies": 1}}

        res = client.post("/radarr", json=dl_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "collection"
        assert mock_sync.called
        call_arg = mock_sync.call_args[0][0]
        assert call_arg["movies"][0]["title"] == "Dune: Part Two"
        assert call_arg["movies"][0]["resolution"] == "uhd_4k"
        assert scrobble_stats["collections"] == initial_collections + 1


def test_webhook_records_cowatch_status_and_privacy():
    """Verify live webhooks record cowatch_status in events and respect privacy masking."""
    client = TestClient(app)
    recent_events.clear()

    payload = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Forks",
            "grandparentTitle": "The Bear",
            "parentIndex": 2,
            "index": 7,
            "duration": 2000000,
            "viewOffset": 1950000,
        },
    }

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "WEBHOOK_SECRET", "privacy_token"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop:

        mock_stop.return_value = {"action": "scrobble"}

        res = client.post("/webhook?token=privacy_token", data={"payload": json.dumps(payload)})
        assert res.status_code == 200
        assert len(recent_events) >= 1
        ev = recent_events[0]
        assert ev.get("cowatch_status") is not None
        assert ev["cowatch_status"]["synced"] is True
        assert ev["cowatch_status"]["target"] == "partner"

        # 1. Non-admin request to /api/events should have cowatch_status masked (None)
        client.cookies.clear()
        res_unauth_events = client.get("/api/events")
        assert res_unauth_events.status_code == 200
        unauth_ev = res_unauth_events.json()["events"][0]
        assert unauth_ev["cowatch_status"] is None
        assert unauth_ev["show_title"] is None
        assert unauth_ev["user"] == "se****"

        # 2. Admin request to /api/events reveals cowatch_status and show_title
        client.cookies.set("admin_token", "privacy_token")
        res_admin_events = client.get("/api/events")
        assert res_admin_events.status_code == 200
        admin_ev = res_admin_events.json()["events"][0]
        assert admin_ev["cowatch_status"] is not None
        assert admin_ev["cowatch_status"]["synced"] is True
        assert admin_ev["is_cowatch_show"] is True
        client.cookies.clear()


def test_cowatch_settings_unauthorized():
    """Verify POST /api/cowatch/settings enforces admin authorization when WEBHOOK_SECRET is active."""
    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # Without credentials -> 401
        res_denied = client.post("/api/cowatch/settings", json={"co_watch_movies": True})
        assert res_denied.status_code == 401

        # With credentials -> 200
        res_ok = client.post("/api/cowatch/settings?token=admin_secret", json={"co_watch_movies": True})
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "ok"


def test_static_github_pages_demo_generation(tmp_path):
    """Verify scripts/generate_static_demo.py generates a valid standalone GitHub Pages demo."""
    import re
    from pathlib import Path
    from app.main import APP_VERSION
    from scripts.generate_static_demo import generate_static_demo

    # 1. Test generation to temporary directory
    demo_file = generate_static_demo(output_dir=tmp_path)
    assert demo_file.is_file()
    assert (tmp_path / ".nojekyll").is_file()
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "static" / "icons" / "icon-192.svg").is_file()
    assert (tmp_path / "static" / "icons" / "icon-512.svg").is_file()

    content = demo_file.read_text(encoding="utf-8")
    assert "Live Interactive Demo" in content
    assert APP_VERSION in content
    assert "👑 Demo Admin" in content
    assert "● Connected as @demo_viewer" in content
    assert "window.fetch = async function" in content
    assert "/api/playback" in content
    assert "/api/events" in content
    assert "/api/cowatch/shows" in content
    assert "/api/cowatch/devices" in content
    assert "/api/sonarr/shows" in content
    assert "/api/logs" in content
    assert "/api/search" in content
    assert "/api/test/webhook" in content
    assert "updateDemoSettingUI" in content
    assert "!isDemo && 'serviceWorker'" in content

    # Verify asset links use relative paths for GitHub Pages subpath compatibility
    assert 'href="manifest.json"' in content
    assert 'href="/manifest.json"' not in content
    assert 'href="assets/icon-192.png"' in content
    assert 'href="assets/icon.svg"' in content
    assert 'href="/static/icons/icon-192.svg"' not in content

    # Verify no unreplaced template placeholders
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", content)
    assert not unreplaced, f"Found unreplaced template variables in static demo: {unreplaced}"

    # Verify balanced curly braces in scripts to ensure no syntax errors
    scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", content, re.DOTALL)
    assert len(scripts) >= 2
    for idx, s in enumerate(scripts):
        open_count = s.count("{")
        close_count = s.count("}")
        assert open_count == close_count, f"Script {idx} in static demo has unbalanced braces: open={open_count}, close={close_count}"

    # 2. Verify docs/index.html in repo exists and is valid
    repo_docs_index = Path(__file__).resolve().parent.parent / "docs" / "index.html"
    assert repo_docs_index.is_file(), "docs/index.html does not exist in repository"
    repo_content = repo_docs_index.read_text(encoding="utf-8")
    assert "Live Interactive Demo" in repo_content
    assert APP_VERSION in repo_content
    assert not re.findall(r"\{\{[A-Z_]+\}\}", repo_content)
    assert 'href="/static/icons/icon-192.svg"' not in repo_content


def test_granular_scrobble_thresholds_logic():
    """Verify separate episode vs movie scrobble thresholds and Config.get_threshold fallback."""
    # Defaults
    assert Config.EPISODE_SCROBBLE_THRESHOLD == 80.0
    assert Config.MOVIE_SCROBBLE_THRESHOLD == 90.0
    assert Config.get_threshold("episode") == 80.0
    assert Config.get_threshold("movie") == 90.0
    assert Config.get_threshold("other") == Config.SCROBBLE_THRESHOLD

    client = TestClient(app)

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

        res = client.post("/webhook", data={"payload": json.dumps(ep_stop_82)})
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

        res = client.post("/webhook", data={"payload": json.dumps(movie_stop_82)})
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

        res = client.post("/webhook", data={"payload": json.dumps(movie_stop_92)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"
        assert scrobble_stats["total"] == initial_total + 1
        assert scrobble_stats["movies"] == initial_movies + 1


def test_jellyfin_parser_and_webhook():
    from app.jellyfin_parser import parse_jellyfin_webhook
    client = TestClient(app)

    # Info GET endpoint
    info_res = client.get("/webhook/jellyfin")
    assert info_res.status_code == 200
    assert "Jellyfin" in info_res.json()["instructions"]

    # 1. Movie playback start
    jf_movie_payload = {
        "NotificationType": "PlaybackStart",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Oppenheimer",
        "Year": 2023,
        "RunTimeTicks": 108000000000,  # 180 min
        "PlaybackPositionTicks": 10800000000,  # 10%
        "Provider_Ids": {"Imdb": "tt15398776", "Tmdb": "872585"},
        "DeviceName": "Living Room Shield",
        "Client": "Jellyfin AndroidTV",
    }

    parsed = parse_jellyfin_webhook(jf_movie_payload)
    assert parsed is not None
    assert parsed.event == "media.play"
    assert parsed.server_type == "jellyfin"
    assert parsed.media_type == "movie"
    assert parsed.title == "Oppenheimer"
    assert parsed.year == 2023
    assert parsed.ids["imdb"] == "tt15398776"
    assert parsed.ids["tmdb"] == 872585
    assert abs(parsed.progress - 10.0) < 0.1

    # Post to /webhook/jellyfin
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {"action": "start"}
        res = client.post("/webhook/jellyfin", json=jf_movie_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_start"

    # 2. Episode playback stop at 85% (scrobble_stop)
    jf_ep_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Episode",
        "SeriesName": "Succession",
        "Name": "Connor's Wedding",
        "SeasonNumber": 4,
        "EpisodeNumber": 3,
        "Year": 2023,
        "RunTimeTicks": 36000000000,
        "PlaybackPositionTicks": 30600000000,  # 85%
        "Provider_Ids": {"Imdb": "tt22216852", "Tvdb": "80349"},
        "DeviceName": "Bedroom Roku",
    }

    parsed_ep = parse_jellyfin_webhook(jf_ep_payload)
    assert parsed_ep is not None
    assert parsed_ep.event == "media.stop"
    assert parsed_ep.server_type == "jellyfin"
    assert parsed_ep.media_type == "episode"
    assert parsed_ep.show_title == "Succession"
    assert parsed_ep.season == 4
    assert parsed_ep.episode == 3
    assert abs(parsed_ep.progress - 85.0) < 0.1

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = client.post("/webhook/jellyfin", json=jf_ep_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

    # 3. UserDataSaved rating event
    jf_rate_payload = {
        "NotificationType": "UserDataSaved",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Inception",
        "Year": 2010,
        "UserData": {"IsFavorite": True},
        "Provider_Ids": {"Imdb": "tt1375666"},
    }
    parsed_rate = parse_jellyfin_webhook(jf_rate_payload)
    assert parsed_rate is not None
    assert parsed_rate.event == "media.rate"
    assert parsed_rate.rating == 10

    # 4. User filtering
    parsed_ignored = parse_jellyfin_webhook(jf_ep_payload, allowed_users=["other_user"])
    assert parsed_ignored is None


def test_emby_parser_and_webhook():
    from app.emby_parser import parse_emby_webhook
    client = TestClient(app)

    # Info GET endpoint
    info_res = client.get("/webhook/emby")
    assert info_res.status_code == 200
    assert "Emby" in info_res.json()["instructions"]

    # 1. Movie playback pause
    emby_payload = {
        "Event": "playback.pause",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Movie",
            "Name": "Interstellar",
            "ProductionYear": 2014,
            "RunTimeTicks": 101400000000,
            "ProviderIds": {"Imdb": "tt0816692", "Tmdb": "157336"},
        },
        "PlaybackInfo": {
            "PositionTicks": 20280000000,  # 20%
            "DeviceName": "Living Room TV",
        },
    }

    parsed = parse_emby_webhook(emby_payload)
    assert parsed is not None
    assert parsed.event == "media.pause"
    assert parsed.server_type == "emby"
    assert parsed.media_type == "movie"
    assert parsed.title == "Interstellar"
    assert parsed.year == 2014
    assert parsed.ids["imdb"] == "tt0816692"
    assert parsed.ids["tmdb"] == 157336
    assert abs(parsed.progress - 20.0) < 0.1

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_pause", new_callable=AsyncMock) as mock_pause:
        mock_pause.return_value = {"action": "pause"}
        res = client.post("/webhook/emby", json=emby_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_pause"

    # 2. Episode playback stop at 95%
    emby_ep_payload = {
        "Event": "playback.stop",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Episode",
            "Name": "Pilot",
            "SeriesName": "Breaking Bad",
            "ParentIndexNumber": 1,
            "IndexNumber": 1,
            "ProductionYear": 2008,
            "RunTimeTicks": 34800000000,
            "ProviderIds": {"Tvdb": "81189"},
        },
        "PlaybackInfo": {
            "PositionTicks": 33060000000,  # 95%
            "DeviceName": "iPad",
        },
    }

    parsed_ep = parse_emby_webhook(emby_ep_payload)
    assert parsed_ep is not None
    assert parsed_ep.event == "media.stop"
    assert parsed_ep.server_type == "emby"
    assert parsed_ep.season == 1
    assert parsed_ep.episode == 1
    assert parsed_ep.show_title == "Breaking Bad"

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = client.post("/webhook/emby", json=emby_ep_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

    # 3. Rating event: item.rate
    emby_rate_payload = {
        "Event": "item.rate",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Movie",
            "Name": "Arrival",
            "ProductionYear": 2016,
            "ProviderIds": {"Imdb": "tt2543164"},
        },
        "UserRating": 9,
    }
    parsed_rate = parse_emby_webhook(emby_rate_payload)
    assert parsed_rate is not None
    assert parsed_rate.event == "media.rate"
    assert parsed_rate.rating == 9


def test_pwa_and_static_assets():
    client = TestClient(app)

    # Manifest
    res_manifest = client.get("/manifest.json")
    assert res_manifest.status_code == 200
    assert "application/manifest+json" in res_manifest.headers.get("content-type", "")
    data = res_manifest.json()
    assert data["name"] == "Omniscrobble"
    assert data["short_name"] == "Omniscrobble"
    assert data["display"] == "standalone"
    assert len(data["icons"]) >= 2

    # Service Worker
    res_sw = client.get("/sw.js")
    assert res_sw.status_code == 200
    assert "javascript" in res_sw.headers.get("content-type", "")
    assert "omniscrobble" in res_sw.text

    # Icons
    res_icon192 = client.get("/static/icons/icon-192.svg")
    assert res_icon192.status_code == 200
    assert "image/svg+xml" in res_icon192.headers.get("content-type", "")
    assert "<svg" in res_icon192.text

    res_icon512 = client.get("/static/icons/icon-512.svg")
    assert res_icon512.status_code == 200
    assert "image/svg+xml" in res_icon512.headers.get("content-type", "")
    assert "<svg" in res_icon512.text


def test_dashboard_multi_server_tabs_and_oled_theme():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Multi-server tabs
    assert "switchWebhookTab('plex')" in html
    assert "switchWebhookTab('jellyfin')" in html
    assert "switchWebhookTab('emby')" in html
    assert "/webhook/jellyfin" in html
    assert "/webhook/emby" in html

    # OLED theme toggle
    assert "toggleTheme()" in html
    assert "theme-toggle" in html
    assert "theme-oled" in html or "omniscrobble_theme" in html


def test_jellyfin_and_emby_webhook_security():
    """Verify webhook token enforcement on Jellyfin and Emby endpoints."""
    client = TestClient(app)
    sample_payload = {
        "NotificationType": "PlaybackStart",
        "ItemType": "Movie",
        "Name": "Inception",
        "Year": 2010,
    }

    with patch.object(Config, "WEBHOOK_SECRET", "super_secret_token_123"):
        # 1. Jellyfin without token -> 401
        res = client.post("/webhook/jellyfin", json=sample_payload)
        assert res.status_code == 401

        # 2. Jellyfin with wrong token -> 401
        res = client.post("/webhook/jellyfin?token=wrong_token", json=sample_payload)
        assert res.status_code == 401

        # 3. Jellyfin with valid query token -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = client.post("/webhook/jellyfin?token=super_secret_token_123", json=sample_payload)
            assert res.status_code == 200

        # 4. Jellyfin alias route /jellyfin with valid header -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = client.post("/jellyfin", json=sample_payload, headers={"x-webhook-secret": "super_secret_token_123"})
            assert res.status_code == 200

        # 5. Emby without token -> 401
        emby_sample = {"Event": "playback.start", "Item": {"Type": "Movie", "Name": "Inception"}}
        res = client.post("/webhook/emby", json=emby_sample)
        assert res.status_code == 401

        # 6. Emby with valid query token -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = client.post("/webhook/emby?token=super_secret_token_123", json=emby_sample)
            assert res.status_code == 200

        # 7. Emby alias route /emby with valid header -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = client.post("/emby", json=emby_sample, headers={"x-webhook-secret": "super_secret_token_123"})
            assert res.status_code == 200


def test_granular_thresholds_pause_behavior():
    """Verify smart pause past threshold respects granular episode (80%) vs movie (90%) thresholds."""
    client = TestClient(app)

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
        res = client.post("/webhook", data={"payload": json.dumps(ep_pause_82)})
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
        res = client.post("/webhook", data={"payload": json.dumps(ep_pause_75)})
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
        res = client.post("/webhook", data={"payload": json.dumps(movie_pause_85)})
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
        res = client.post("/webhook", data={"payload": json.dumps(movie_pause_92)})
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"


def test_jellyfin_and_emby_filtering_and_edge_cases():
    """Verify library filtering, non-media events, and safe ticks division in Jellyfin/Emby parsers."""
    from app.jellyfin_parser import parse_jellyfin_webhook
    from app.emby_parser import parse_emby_webhook

    # 1. Jellyfin library exclusion
    jf_payload = {
        "NotificationType": "PlaybackStart",
        "ItemType": "Movie",
        "Name": "Family Vacation",
        "LibraryName": "Home Videos",
    }
    assert parse_jellyfin_webhook(jf_payload, excluded_libraries=["Home Videos"]) is None
    assert parse_jellyfin_webhook(jf_payload, allowed_libraries=["Movies"]) is None

    # 2. Unsupported Jellyfin media types
    assert parse_jellyfin_webhook({"NotificationType": "PlaybackStart", "ItemType": "Audio"}) is None
    assert parse_jellyfin_webhook({"NotificationType": "PlaybackStart", "ItemType": "Book"}) is None
    assert parse_jellyfin_webhook({"NotificationType": "ServerRestarting"}) is None

    # 3. Missing/zero ticks
    jf_zero_ticks = {
        "NotificationType": "PlaybackStart",
        "ItemType": "Movie",
        "Name": "Sample Movie",
        "RunTimeTicks": 0,
        "PlaybackPositionTicks": 0,
    }
    parsed_zero = parse_jellyfin_webhook(jf_zero_ticks)
    assert parsed_zero is not None
    assert parsed_zero.progress == 0.0

    # 4. Emby library exclusion
    emby_payload = {
        "Event": "playback.start",
        "Item": {"Type": "Movie", "Name": "Private Clip", "LibraryName": "Private"},
    }
    assert parse_emby_webhook(emby_payload, excluded_libraries=["Private"]) is None
    assert parse_emby_webhook(emby_payload, allowed_libraries=["Main"]) is None

    # 5. Unsupported Emby item types
    assert parse_emby_webhook({"Event": "playback.start", "Item": {"Type": "Audio"}}) is None
    assert parse_emby_webhook({"Event": "playback.start", "Item": {"Type": "Photo"}}) is None
    assert parse_emby_webhook({"Event": "system.restart"}) is None


def test_jellyfin_and_emby_offline_queue_on_trakt_temporary_error():
    """Verify offline retry queue persists failed Jellyfin/Emby scrobbles during Trakt outages."""
    client = TestClient(app)
    jf_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Oppenheimer",
        "Year": 2023,
        "RunTimeTicks": 100000000000,
        "PlaybackPositionTicks": 95000000000,  # 95% -> scrobble_stop
        "ProviderIds": {"Imdb": "tt15398776"},
    }

    initial_queue_count = queue_mgr.get_pending_count()

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop:
        # Simulate Trakt 503 outage
        mock_stop.return_value = {"error": "503 Service Unavailable: Trakt maintenance"}

        res = client.post("/webhook/jellyfin", json=jf_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "scrobble_stop"

        # Verify offline queue preserved the event
        assert queue_mgr.get_pending_count() == initial_queue_count + 1
        pending = queue_mgr.get_pending()
        assert any(item["event_type"] == "scrobble_stop" and item.get("username") == "selits" for item in pending)


def test_jellyfin_and_emby_cowatch_integration():
    """Verify Watch Together co-watch dual-sync evaluates properly for Jellyfin & Emby events."""
    client = TestClient(app)
    jf_cowatch_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Episode",
        "SeriesName": "Severance",
        "Name": "Good News About Hell",
        "SeasonNumber": 1,
        "EpisodeNumber": 1,
        "Year": 2022,
        "RunTimeTicks": 36000000000,
        "PlaybackPositionTicks": 34000000000,  # >80%
    }

    with patch.object(Config, "CO_WATCH_USER", "partner_trakt_user"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch("app.main.execute_cowatch_sync", new_callable=AsyncMock) as mock_cowatch_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}

        res = client.post("/webhook/jellyfin", json=jf_cowatch_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"
        mock_cowatch_sync.assert_called_once()


def test_opengraph_and_social_metadata():
    """Verify that the dashboard serves complete OpenGraph and Twitter card social preview meta tags."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert '<meta property="og:title" content="Omniscrobble' in html
    assert '<meta property="og:description" content="Watch anywhere. Track everywhere.' in html
    assert '<meta property="og:image" content="https://selits.github.io/omniscrobble/assets/social-preview.png">' in html
    assert '<meta property="twitter:card" content="summary_large_image">' in html


def test_brand_assets_integrity():
    """Verify that all generated brand assets exist, are non-empty, and contain valid image headers."""
    assets_dir = Path(__file__).resolve().parent.parent / "docs" / "assets"
    expected_assets = [
        "banner.png",
        "banner.svg",
        "icon-512.png",
        "icon-192.png",
        "icon.svg",
        "favicon.png",
        "favicon.ico",
        "social-preview.png",
        "social-preview.svg",
    ]
    for asset_name in expected_assets:
        asset_path = assets_dir / asset_name
        assert asset_path.exists(), f"Asset {asset_name} is missing from docs/assets/"
        assert asset_path.stat().st_size > 0, f"Asset {asset_name} is empty"

    # SVG validation
    icon_svg = (assets_dir / "icon.svg").read_text(encoding="utf-8")
    assert "<svg" in icon_svg and "</svg>" in icon_svg
    assert "arrowGradTop" in icon_svg

    # PNG magic bytes check (\x89PNG)
    for png_name in ["banner.png", "icon-512.png", "icon-192.png", "favicon.png", "social-preview.png"]:
        header = (assets_dir / png_name).read_bytes()[:8]
        assert header.startswith(b"\x89PNG"), f"{png_name} does not have valid PNG header"


def test_systemd_service_file_consistency():
    """Verify that the systemd user unit file contains the updated service description."""
    service_path = Path(__file__).resolve().parent.parent / "plex-trakt.service"
    assert service_path.exists(), "plex-trakt.service missing"
    content = service_path.read_text(encoding="utf-8")
    assert "Omniscrobble" in content
    assert "ExecStart=%h/plex-trakt-webhook/.venv/bin/python main.py" in content


@pytest.mark.asyncio
async def test_plex_api_client_operations():
    """Verify PlexApiClient configuration, connection checks, library parsing, and scrobbling."""
    from app.clients.plex_api_client import PlexApiClient

    # 1. Unconfigured client
    unconf = PlexApiClient(base_url="", token="")
    assert not unconf.is_configured()
    conn = await unconf.check_connection()
    assert conn["status"] == "unconfigured"
    assert await unconf.get_library_sections() == []
    assert await unconf.get_movies("1") == []
    assert await unconf.get_episodes("2") == []
    assert not await unconf.mark_as_watched("123")
    assert not await unconf.set_user_rating("123", 8.0)

    # 2. Mock HTTP transport for configured client
    async def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "/identity" in url_str:
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "test-uuid-123", "version": "1.40.1"}})
        elif "/library/sections/1/all" in url_str:
            return httpx.Response(200, json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "ratingKey": "1001",
                            "title": "Inception",
                            "year": 2010,
                            "viewCount": 0,
                            "Guid": [{"id": "imdb://tt1375666"}, {"id": "tmdb://27205"}],
                            "userRating": 9.0,
                        },
                        {
                            "ratingKey": "1002",
                            "title": "Interstellar",
                            "year": 2014,
                            "viewCount": 2,
                            "Guid": [{"id": "imdb://tt0816692"}],
                        }
                    ]
                }
            })
        elif "/library/sections/2/all" in url_str:
            return httpx.Response(200, json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "ratingKey": "2001",
                            "grandparentTitle": "Severance",
                            "grandparentRatingKey": "2000",
                            "parentIndex": 1,
                            "index": 1,
                            "title": "Good News About Hell",
                            "year": 2022,
                            "viewCount": 1,
                            "Guid": [{"id": "imdb://tt11280740"}],
                        }
                    ]
                }
            })
        elif "/library/sections" in url_str:
            return httpx.Response(200, json={
                "MediaContainer": {
                    "Directory": [
                        {"key": "1", "title": "Movies", "type": "movie", "uuid": "sec-1"},
                        {"key": "2", "title": "TV Shows", "type": "show", "uuid": "sec-2"},
                        {"key": "3", "title": "Music", "type": "artist", "uuid": "sec-3"},
                    ]
                }
            })
        elif "/:/scrobble" in url_str:
            return httpx.Response(200, text="OK")
        elif "/:/unscrobble" in url_str:
            return httpx.Response(200, text="OK")
        elif "/:/rate" in url_str:
            return httpx.Response(200, text="OK")
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    plex = PlexApiClient(base_url="http://mock-plex:32400", token="mock-token-xyz", client=mock_client)
    assert plex.is_configured()

    # Connection check
    conn_info = await plex.check_connection()
    assert conn_info["status"] == "connected"
    assert conn_info["machine_identifier"] == "test-uuid-123"

    # Sections check
    sections = await plex.get_library_sections()
    assert len(sections) == 2
    assert sections[0]["title"] == "Movies" and sections[0]["type"] == "movie"
    assert sections[1]["title"] == "TV Shows" and sections[1]["type"] == "show"

    # Movies check
    movies = await plex.get_movies("1")
    assert len(movies) == 2
    assert movies[0]["rating_key"] == "1001"
    assert movies[0]["title"] == "Inception"
    assert not movies[0]["is_watched"]
    assert movies[0]["ids"]["imdb"] == "tt1375666"
    assert movies[1]["rating_key"] == "1002"
    assert movies[1]["is_watched"]

    # Episodes check
    episodes = await plex.get_episodes("2")
    assert len(episodes) == 1
    assert episodes[0]["rating_key"] == "2001"
    assert episodes[0]["series_title"] == "Severance"
    assert episodes[0]["season"] == 1
    assert episodes[0]["episode"] == 1
    assert episodes[0]["is_watched"]

    # Actions check
    assert await plex.mark_as_watched("1001")
    assert await plex.mark_as_unwatched("1002")
    assert await plex.set_user_rating("1001", 9.5)


@pytest.mark.asyncio
async def test_trakt_reverse_sync_fetch_methods():
    """Verify TraktClient reverse sync methods (watched movies, watched shows, ratings)."""
    from app.clients.trakt_client import TraktClient

    async def mock_trakt_handler(request: httpx.Request):
        url = str(request.url)
        if "/sync/watched/movies" in url:
            return httpx.Response(200, json=[
                {
                    "plays": 1,
                    "last_watched_at": "2025-01-01T00:00:00.000Z",
                    "movie": {"title": "Inception", "year": 2010, "ids": {"imdb": "tt1375666", "tmdb": 27205}},
                }
            ])
        elif "/sync/watched/shows" in url:
            return httpx.Response(200, json=[
                {
                    "plays": 1,
                    "show": {"title": "Severance", "year": 2022, "ids": {"imdb": "tt11280740"}},
                    "seasons": [
                        {
                            "number": 1,
                            "episodes": [
                                {"number": 1, "plays": 1, "last_watched_at": "2025-01-01T00:00:00.000Z"}
                            ]
                        }
                    ]
                }
            ])
        elif "/sync/ratings/movies" in url:
            return httpx.Response(200, json=[
                {
                    "rating": 10,
                    "movie": {"title": "Inception", "year": 2010, "ids": {"imdb": "tt1375666"}},
                }
            ])
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_trakt_handler))
    t = TraktClient(Config, client=mock_client)
    t.access_token = "mock-token"

    watched_movies = await t.get_watched_movies()
    assert len(watched_movies) == 1
    assert watched_movies[0]["movie"]["title"] == "Inception"

    watched_shows = await t.get_watched_shows()
    assert len(watched_shows) == 1
    assert watched_shows[0]["show"]["title"] == "Severance"

    ratings = await t.get_ratings("movies")
    assert len(ratings) == 1
    assert ratings[0]["rating"] == 10


def test_loop_prevention_manager():
    """Verify thread-safe LoopPreventionManager suppression, TTL expiration, and clearing."""
    from app.services.loop_prevention import LoopPreventionManager
    import time

    lp = LoopPreventionManager(default_ttl=1.0)
    assert not lp.is_ignored("1234")
    assert not lp.is_ignored("")
    assert lp.get_active_count() == 0

    lp.ignore("1234", ttl=0.2)
    assert lp.is_ignored("1234")
    assert lp.get_active_count() == 1

    # Wait for expiration
    time.sleep(0.25)
    assert not lp.is_ignored("1234")
    assert lp.get_active_count() == 0

    # Clear functionality
    lp.ignore("5678", ttl=60.0)
    assert lp.is_ignored("5678")
    lp.clear()
    assert not lp.is_ignored("5678")


@pytest.mark.asyncio
async def test_webhook_echo_loop_suppression():
    """Verify that incoming webhooks containing suppressed keys or GUIDs are ignored by loop prevention."""
    from app.services.loop_prevention import loop_prevention
    from app.plex_parser import ParsedMedia
    from app.main import process_media_event

    loop_prevention.clear()

    # Case 1: Unsuppressed event proceeds
    unsuppressed = ParsedMedia(
        event="media.scrobble",
        username="test_user",
        media_type="movie",
        title="Test Movie",
        year=2024,
        rating_key="99901",
        ids={"imdb": "tt999001"},
        progress=100.0,
    )
    # Temporarily mark Trakt unauthenticated so it returns not authenticated rather than loop prevention
    # But when we suppress:
    loop_prevention.ignore("99901", ttl=60.0)
    res = await process_media_event(unsuppressed, endpoint_name="webhook")
    assert res["status"] == "ignored"
    assert res["reason"] == "loop_prevention"
    assert res["key"] == "99901"

    # Case 2: Suppressed by external GUID
    loop_prevention.clear()
    loop_prevention.ignore("tt999001", ttl=60.0)
    res2 = await process_media_event(unsuppressed, endpoint_name="webhook")
    assert res2["status"] == "ignored"
    assert res2["reason"] == "loop_prevention"

    loop_prevention.clear()


@pytest.mark.asyncio
async def test_reverse_sync_manager_scan_and_reconciliation():
    """Verify ReverseSyncManager discrepancy detection and selective sync execution."""
    from app.services.reverse_sync_manager import ReverseSyncManager
    from app.clients.plex_api_client import PlexApiClient
    from app.clients.trakt_client import TraktClient
    from app.services.loop_prevention import LoopPreventionManager

    # Mock Plex client
    scrobbled_keys = []
    rated_keys = []

    async def mock_plex_handler(request: httpx.Request):
        url = str(request.url)
        if "/identity" in url:
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "test-uuid"}})
        elif "/library/sections/1/all" in url:
            return httpx.Response(200, json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "ratingKey": "101",
                            "title": "Dune",
                            "year": 2021,
                            "viewCount": 0,
                            "Guid": [{"id": "imdb://tt1160419"}],
                            "userRating": None,
                        },
                        {
                            "ratingKey": "102",
                            "title": "Blade Runner 2049",
                            "year": 2017,
                            "viewCount": 1,
                            "Guid": [{"id": "imdb://tt1856101"}],
                            "userRating": 9.0,
                        }
                    ]
                }
            })
        elif "/library/sections" in url:
            return httpx.Response(200, json={
                "MediaContainer": {
                    "Directory": [{"key": "1", "title": "Movies", "type": "movie"}]
                }
            })
        elif "/:/scrobble" in url:
            key = request.url.params.get("key")
            scrobbled_keys.append(key)
            return httpx.Response(200, text="OK")
        elif "/:/rate" in url:
            key = request.url.params.get("key")
            rated_keys.append(key)
            return httpx.Response(200, text="OK")
        return httpx.Response(404)

    # Mock Trakt client
    history_synced = []

    async def mock_trakt_handler(request: httpx.Request):
        url = str(request.url)
        if "/sync/watched/movies" in url:
            # Trakt has watched "Dune" (which Plex has unwatched)
            # Trakt does NOT have watched "Blade Runner 2049" (which Plex has watched)
            return httpx.Response(200, json=[
                {
                    "plays": 1,
                    "movie": {"title": "Dune", "year": 2021, "ids": {"imdb": "tt1160419"}},
                }
            ])
        elif "/sync/watched/shows" in url:
            return httpx.Response(200, json=[])
        elif "/sync/ratings/movies" in url:
            return httpx.Response(200, json=[])
        elif "/sync/history" in url:
            history_synced.append(json.loads(request.content))
            return httpx.Response(201, json={"added": {"movies": 1}})
        return httpx.Response(200, json=[])

    plex_client = PlexApiClient(
        base_url="http://mock-plex:32400",
        token="token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(mock_plex_handler))
    )
    trakt_client = TraktClient(
        Config,
        client=httpx.AsyncClient(transport=httpx.MockTransport(mock_trakt_handler))
    )
    trakt_client.access_token = "mock-trakt-token"
    lp = LoopPreventionManager()

    mgr = ReverseSyncManager(plex_client=plex_client, trakt_client=trakt_client, loop_prevention_mgr=lp)

    # 1. Status check
    status = await mgr.get_status()
    assert status["configured"]
    assert status["plex_connected"]
    assert status["trakt_authenticated"]

    # 2. Scan discrepancies
    diff = await mgr.scan_discrepancies(force=True)
    assert len(diff) == 2

    # Check "Dune" discrepancy (watched on Trakt, unwatched on Plex -> trakt_only)
    dune_diff = next(d for d in diff if d["title"] == "Dune")
    assert dune_diff["status"] == "trakt_only"
    assert dune_diff["action_recommended"] == "mark_plex_watched"
    assert dune_diff["rating_key"] == "101"

    # Check "Blade Runner 2049" discrepancy (watched on Plex, unwatched on Trakt -> plex_only)
    br_diff = next(d for d in diff if d["title"] == "Blade Runner 2049")
    assert br_diff["status"] == "plex_only"
    assert br_diff["action_recommended"] == "sync_to_trakt"
    assert br_diff["rating_key"] == "102"

    # 3. Execute reconciliation (trakt_to_plex: mark Dune as watched on Plex)
    res = await mgr.execute_reconciliation(direction="trakt_to_plex")
    assert res["status"] == "success"
    assert res["reconciled"] == 1
    assert "101" in scrobbled_keys
    # Verify loop prevention suppressed key 101 during the call
    assert lp.is_ignored("101")

    # 4. Execute remaining reconciliation (plex_to_trakt: push Blade Runner 2049 to Trakt)
    res2 = await mgr.execute_reconciliation(direction="plex_to_trakt")
    assert res2["status"] == "success"
    assert res2["reconciled"] == 1
    assert len(history_synced) == 1
    assert history_synced[0]["movies"][0]["title"] == "Blade Runner 2049"


def test_sync_api_endpoints_and_admin_security():
    """Verify /api/sync/* endpoints with admin authentication gating and demo mode simulation."""
    client = TestClient(app)

    # 1. Unauthenticated diff request is rejected
    res = client.get("/api/sync/diff")
    if Config.WEBHOOK_SECRET:
        assert res.status_code == 401

    # 2. Authenticated status endpoint
    res_status = client.get("/api/sync/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "configured" in data
    assert "interval_minutes" in data

    # 3. Demo mode diff returns simulated discrepancies without admin token
    res_demo = client.get("/api/sync/diff?demo=true")
    assert res_demo.status_code == 200
    demo_diff = res_demo.json()
    assert demo_diff["status"] == "ok"
    assert len(demo_diff["diff"]) == 4

    # 4. Admin authenticated diff request
    headers = {"x-webhook-secret": Config.WEBHOOK_SECRET} if Config.WEBHOOK_SECRET else {}
    res_diff = client.get("/api/sync/diff", headers=headers)
    assert res_diff.status_code == 200
    assert "diff" in res_diff.json()

    # 5. Demo reconcile execution
    res_rec = client.post("/api/sync/reconcile?demo=true", json={"direction": "all"})
    assert res_rec.status_code == 200
    assert res_rec.json()["status"] == "success"

    # 6. Progress endpoint
    res_prog = client.get("/api/sync/progress")
    assert res_prog.status_code == 200
    assert "in_progress" in res_prog.json()


def test_dashboard_reconciliation_elements():
    """Verify dashboard HTML includes Two-Way Reconciliation card and modal dialogs."""
    client = TestClient(app)

    res = client.get("/")
    assert res.status_code == 200
    html_content = res.text
    assert "Two-Way Library Reconciliation" in html_content
    assert 'id="reconcile-modal"' in html_content
    assert 'id="reconcile-settings-modal"' in html_content
    assert 'id="recon-plex-url"' in html_content
    assert 'id="recon-plex-token"' in html_content
    assert 'id="recon-tab-btn-jellyfin"' in html_content
    assert 'id="recon-tab-btn-emby"' in html_content
    assert 'id="recon-srv-btn-jellyfin"' in html_content
    assert 'id="recon-srv-btn-emby"' in html_content
    assert 'id="recon-jellyfin-url"' in html_content
    assert 'id="recon-emby-url"' in html_content
    assert 'id="scrobble-disambig-container"' in html_content
    assert "reconcile-diff-badge" in html_content

    # In demo mode
    res_demo = client.get("/demo")
    assert res_demo.status_code == 200
    assert "Two-Way Library Reconciliation" in res_demo.text
    assert 'id="reconcile-settings-modal"' in res_demo.text
    assert 'id="recon-tab-btn-jellyfin"' in res_demo.text
    assert 'id="recon-srv-btn-jellyfin"' in res_demo.text


def test_settings_manager_reconciliation(tmp_path):
    """Verify SettingsManager reconciliation getter, token masking, and updates."""
    from app.services.settings_manager import SettingsManager

    settings_file = tmp_path / "settings.json"
    sm = SettingsManager(settings_file=settings_file)

    # 1. Defaults
    rec = sm.get_reconciliation_settings(mask_token=True)
    assert "plex_url" in rec
    assert "interval_minutes" in rec
    assert "sync_ratings" in rec
    assert "sync_on_startup" in rec

    # 2. Update settings with a secret token
    sm.update_reconciliation_settings({
        "plex_url": "http://plex.local:32400",
        "plex_token": "super-secret-token-1234",
        "interval_minutes": 15,
        "sync_ratings": False,
        "sync_on_startup": False,
    })

    # Masked
    masked = sm.get_reconciliation_settings(mask_token=True)
    assert masked["plex_url"] == "http://plex.local:32400"
    assert masked["plex_token"] == "••••••••1234"
    assert masked["has_token"] is True
    assert masked["interval_minutes"] == 15
    assert masked["sync_ratings"] is False
    assert masked["sync_on_startup"] is False

    # Unmasked
    unmasked = sm.get_reconciliation_settings(mask_token=False)
    assert unmasked["plex_token"] == "super-secret-token-1234"

    # 3. Update without modifying token (passing back masked token placeholder)
    sm.update_reconciliation_settings({
        "plex_token": "••••••••1234",
        "interval_minutes": 60,
    })
    # Token should remain unchanged
    assert sm.get_reconciliation_settings(mask_token=False)["plex_token"] == "super-secret-token-1234"
    assert sm.get_reconciliation_settings(mask_token=False)["interval_minutes"] == 60

    # 4. Clear token explicitly
    sm.update_reconciliation_settings({
        "clear_token": True,
    })
    assert sm.get_reconciliation_settings(mask_token=False)["plex_token"] == ""
    assert sm.get_reconciliation_settings(mask_token=True)["has_token"] is False


@pytest.mark.asyncio
async def test_sync_settings_api_and_connection_test():
    """Verify /api/sync/settings and /api/sync/test-connection endpoints."""
    from app.services.settings_manager import settings_mgr
    from app.main import reverse_sync_mgr
    client = TestClient(app)

    orig_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    try:
        with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
            # 1. Unauthenticated -> 401
            res = client.get("/api/sync/settings")
            assert res.status_code == 401

            res_post = client.post("/api/sync/settings", json={"interval_minutes": 45})
            assert res_post.status_code == 401

            res_test = client.post("/api/sync/test-connection", json={"plex_url": "http://mock-plex:32400"})
            assert res_test.status_code == 401

            headers = {"x-webhook-secret": "test_secret"}

            # 2. Connection test missing params when unconfigured -> status unconfigured
            res_bad_test = client.post("/api/sync/test-connection", headers=headers, json={"url": "", "token": ""})
            assert res_bad_test.status_code == 200
            assert res_bad_test.json().get("status") in ("unconfigured", "error", "unreachable")

            # 3. Authenticated GET /api/sync/settings
            res_get = client.get("/api/sync/settings", headers=headers)
            assert res_get.status_code == 200
            data = res_get.json()
            assert "interval_minutes" in data
            assert "plex_url" in data

            # 4. Authenticated POST /api/sync/settings
            res_update = client.post("/api/sync/settings", headers=headers, json={
                "plex_url": "http://plex.local:32400",
                "plex_token": "test-new-token-9999",
                "interval_minutes": 45,
                "sync_ratings": True,
                "sync_on_startup": False
            })
            assert res_update.status_code == 200
            saved = res_update.json()["settings"]
            assert saved["interval_minutes"] == 45
            assert saved["sync_on_startup"] is False
            assert saved["plex_token"] == "••••••••9999"

            # 5. Connection test successful mock
            with patch.object(reverse_sync_mgr, "test_connection", new_callable=AsyncMock) as mock_test:
                mock_test.return_value = {
                    "connected": True,
                    "server_name": "Living Room Plex",
                    "version": "1.41.0.8992",
                    "message": "Successfully connected to Living Room Plex (v1.41.0.8992)",
                }
                res_conn = client.post("/api/sync/test-connection", headers=headers, json={
                    "plex_url": "http://plex.local:32400",
                    "plex_token": "••••••••9999"
                })
                assert res_conn.status_code == 200
                assert res_conn.json()["connected"] is True
                assert res_conn.json()["server_name"] == "Living Room Plex"
                # Verify test_connection used the actual unmasked token
                mock_test.assert_called_once()
                assert mock_test.call_args.kwargs.get("url") == "http://plex.local:32400"
                assert mock_test.call_args.kwargs.get("token") == "test-new-token-9999"
    finally:
        settings_mgr.update_reconciliation_settings(orig_recon)
        reverse_sync_mgr.update_config(
            plex_url=orig_recon.get("plex_url", ""),
            plex_token=orig_recon.get("plex_token", ""),
            jellyfin_url=orig_recon.get("jellyfin_url", ""),
            jellyfin_token=orig_recon.get("jellyfin_token", ""),
            emby_url=orig_recon.get("emby_url", ""),
            emby_token=orig_recon.get("emby_token", ""),
        )


@pytest.mark.asyncio
async def test_mediabrowser_api_client_operations():
    """Verify JellyfinApiClient and EmbyApiClient connection checks, user resolution, library views, and played status."""
    from app.clients.jellyfin_api_client import JellyfinApiClient
    from app.clients.emby_api_client import EmbyApiClient

    # 1. Unconfigured checks
    unconf_jf = JellyfinApiClient(base_url="", token="")
    assert not unconf_jf.is_configured()
    res = await unconf_jf.check_connection()
    assert res["status"] == "unconfigured"
    assert await unconf_jf.get_library_sections() == []
    assert await unconf_jf.get_movies("v1") == []
    assert await unconf_jf.get_episodes("v2") == []
    assert not await unconf_jf.mark_as_watched("item-1")
    assert not await unconf_jf.set_user_rating("item-1", 8.0)

    # 2. Mock handler for Jellyfin / Emby REST API
    async def mock_jf_handler(request: httpx.Request):
        url_str = str(request.url)
        # Auth check
        token_hdr = request.headers.get("x-emby-token") or request.headers.get("authorization")
        if not token_hdr or "bad-token" in str(token_hdr):
            return httpx.Response(401, text="Unauthorized")

        if "/System/Info" in url_str:
            return httpx.Response(200, json={"ServerName": "My Jellyfin", "Version": "10.9.11", "Id": "jf-sys-id"})
        elif "/Users" in url_str and "/Items" not in url_str and "/Views" not in url_str:
            return httpx.Response(200, json=[
                {"Id": "uid-guest", "Name": "guest", "Policy": {"IsAdministrator": False}},
                {"Id": "uid-admin", "Name": "admin", "Policy": {"IsAdministrator": True}},
            ])
        elif "/Users/uid-admin/Views" in url_str:
            return httpx.Response(200, json={
                "Items": [
                    {"Id": "view-movies", "Name": "Movies", "CollectionType": "movies"},
                    {"Id": "view-shows", "Name": "TV Shows", "CollectionType": "tvshows"},
                    {"Id": "view-music", "Name": "Music", "CollectionType": "music"},
                ]
            })
        elif "/Rating" in url_str:
            return httpx.Response(200, json={"Rating": 8.0})
        elif "/PlayedItems/m-102" in url_str:
            if request.method == "POST":
                return httpx.Response(200, json={"Played": True})
            elif request.method == "DELETE":
                return httpx.Response(200, json={"Played": False})
        elif "/Users/uid-admin/Items" in url_str:
            if "IncludeItemTypes=Movie" in url_str:
                return httpx.Response(200, json={
                    "Items": [
                        {
                            "Id": "m-101",
                            "Name": "Inception",
                            "ProductionYear": 2010,
                            "ProviderIds": {"Imdb": "tt1375666", "Tmdb": "27205"},
                            "UserData": {"Played": True, "PlayCount": 2, "Rating": 9.0},
                        },
                        {
                            "Id": "m-102",
                            "Name": "Interstellar",
                            "ProductionYear": 2014,
                            "ProviderIds": {"Imdb": "tt0816692"},
                            "UserData": {"Played": False, "PlayCount": 0},
                        }
                    ]
                })
            elif "IncludeItemTypes=Episode" in url_str:
                return httpx.Response(200, json={
                    "Items": [
                        {
                            "Id": "ep-201",
                            "Name": "Good News About Hell",
                            "SeriesName": "Severance",
                            "ParentIndexNumber": 1,
                            "IndexNumber": 1,
                            "ProductionYear": 2022,
                            "ProviderIds": {"Imdb": "tt11280740"},
                            "UserData": {"Played": True, "PlayCount": 1},
                        }
                    ]
                })

        return httpx.Response(404, text="Not Found")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_jf_handler))

    # Test Jellyfin client
    jf = JellyfinApiClient(base_url="http://mock-jf:8096", token="valid-jf-token", user_id=None, client=mock_client)
    assert jf.is_configured()
    conn = await jf.check_connection()
    assert conn["status"] == "connected"
    assert conn["server_name"] == "My Jellyfin"
    assert conn["version"] == "10.9.11"

    # User auto-detection finds administrator
    uid = await jf.get_default_user_id()
    assert uid == "uid-admin"

    # Library sections
    sections = await jf.get_library_sections()
    assert len(sections) == 2
    assert sections[0]["type"] == "movie"
    assert sections[1]["type"] == "show"

    # Movies
    movies = await jf.get_movies("view-movies")
    assert len(movies) == 2
    assert movies[0]["rating_key"] == "m-101"
    assert movies[0]["title"] == "Inception"
    assert movies[0]["is_watched"] is True
    assert movies[0]["ids"]["imdb"] == "tt1375666"
    assert movies[0]["rating"] == 9.0
    assert movies[1]["is_watched"] is False

    # Episodes
    episodes = await jf.get_episodes("view-shows")
    assert len(episodes) == 1
    assert episodes[0]["series_title"] == "Severance"
    assert episodes[0]["season"] == 1
    assert episodes[0]["episode"] == 1
    assert episodes[0]["is_watched"] is True

    # Mark watched / unwatched / rating
    assert await jf.mark_as_watched("m-102")
    assert await jf.mark_as_unwatched("m-102")
    assert await jf.set_user_rating("m-101", 8.0)

    # Test Emby client subclass
    emby = EmbyApiClient(base_url="http://mock-emby:8096", token="valid-emby-token", user_id="uid-admin", client=mock_client)
    assert emby.is_configured()
    assert emby.server_type == "emby"
    emby_conn = await emby.check_connection()
    assert emby_conn["status"] == "connected"
    assert await emby.mark_as_watched("m-102")


@pytest.mark.asyncio
async def test_multi_server_reverse_sync_manager():
    """Verify ReverseSyncManager multi-server dispatch, scanning, and reconciliation against Jellyfin."""
    from app.services.reverse_sync_manager import ReverseSyncManager
    from app.clients.jellyfin_api_client import JellyfinApiClient
    from app.clients.trakt_client import TraktClient
    from app.services.loop_prevention import LoopPreventionManager

    loop_prev = LoopPreventionManager()

    # Mock Jellyfin client
    mock_jf = AsyncMock(spec=JellyfinApiClient)
    mock_jf.server_type = "jellyfin"
    mock_jf.is_configured.return_value = True
    mock_jf.get_library_sections.return_value = [{"key": "sec-m", "type": "movie", "title": "Movies"}]
    mock_jf.get_movies.return_value = [
        {
            "rating_key": "jf-m-1",
            "title": "Inception",
            "year": 2010,
            "is_watched": False,
            "rating": None,
            "ids": {"imdb": "tt1375666", "tmdb": 27205},
        },
        {
            "rating_key": "jf-m-2",
            "title": "Blade Runner 2049",
            "year": 2017,
            "is_watched": True,
            "rating": None,
            "ids": {"imdb": "tt1856101"},
        }
    ]
    mock_jf.get_episodes.return_value = []
    mock_jf.mark_as_watched.return_value = True

    # Mock Trakt
    mock_trakt = AsyncMock(spec=TraktClient)
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.get_watched_movies.return_value = [
        {"plays": 1, "movie": {"title": "Inception", "year": 2010, "ids": {"imdb": "tt1375666"}}}
    ]
    mock_trakt.get_watched_shows.return_value = []
    mock_trakt.get_ratings.return_value = []
    mock_trakt.sync_history.return_value = {"added": {"movies": 1}}

    sync_mgr = ReverseSyncManager(jellyfin_client=mock_jf, loop_prevention_mgr=loop_prev)
    sync_mgr.get_trakt = lambda: mock_trakt

    # 1. Scan Jellyfin discrepancies
    diff = await sync_mgr.scan_discrepancies(force=True, server="jellyfin")
    assert len(diff) == 2
    trakt_only = next(i for i in diff if i["status"] == "trakt_only")
    assert trakt_only["action_recommended"] == "mark_jellyfin_watched"
    assert trakt_only["server"] == "jellyfin"

    jf_only = next(i for i in diff if i["status"] == "jellyfin_only")
    assert jf_only["action_recommended"] == "sync_to_trakt"
    assert jf_only["server"] == "jellyfin"

    # 2. Execute reconciliation for Jellyfin item
    res = await sync_mgr.execute_reconciliation(item_ids=[trakt_only["id"]], server="jellyfin")
    assert res["status"] == "success"
    assert res["reconciled"] == 1
    mock_jf.mark_as_watched.assert_called_with("jf-m-1")
    assert loop_prev.is_ignored("jf-m-1")


@pytest.mark.asyncio
async def test_multi_server_sync_settings_and_test_connection_api():
    """Verify settings persistence and test connection for Jellyfin and Emby."""
    from app.services.settings_manager import settings_mgr
    from app.main import reverse_sync_mgr
    client = TestClient(app)

    orig_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    try:
        with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
            headers = {"x-webhook-secret": "test_secret"}

            # 1. Update Jellyfin & Emby settings
            res = client.post("/api/sync/settings", headers=headers, json={
                "server_type": "jellyfin",
                "jellyfin_url": "http://jellyfin.local:8096",
                "jellyfin_token": "jf-secret-token-5555",
                "jellyfin_user_id": "jf-user-admin",
                "emby_url": "http://emby.local:8096",
                "emby_token": "emby-secret-token-7777",
                "emby_user_id": "emby-user-admin",
            })
            assert res.status_code == 200
            settings_data = res.json()["settings"]
            assert settings_data["server_type"] == "jellyfin"
            assert settings_data["jellyfin_url"] == "http://jellyfin.local:8096"
            assert settings_data["jellyfin_token"] == "••••••••5555"
            assert settings_data["emby_token"] == "••••••••7777"
            assert settings_data["is_jellyfin_token_set"] is True
            assert settings_data["is_emby_token_set"] is True

            # 2. Test Connection for Jellyfin
            with patch.object(reverse_sync_mgr, "test_connection", new_callable=AsyncMock) as mock_test:
                mock_test.return_value = {
                    "status": "connected",
                    "connected": True,
                    "server_name": "Living Room Jellyfin",
                    "version": "10.9.11",
                }
                res_jf_test = client.post("/api/sync/test-connection", headers=headers, json={
                    "server": "jellyfin",
                    "url": "http://jellyfin.local:8096",
                    "token": "••••••••5555"
                })
                assert res_jf_test.status_code == 200
                assert res_jf_test.json()["connected"] is True
                mock_test.assert_called_once()
                assert mock_test.call_args.kwargs.get("server") == "jellyfin"
                assert mock_test.call_args.kwargs.get("token") == "jf-secret-token-5555"

            # 3. Test Connection for Emby
            with patch.object(reverse_sync_mgr, "test_connection", new_callable=AsyncMock) as mock_test_emby:
                mock_test_emby.return_value = {
                    "status": "connected",
                    "connected": True,
                    "server_name": "Living Room Emby",
                    "version": "4.8.8",
                }
                res_emby_test = client.post("/api/sync/test-connection", headers=headers, json={
                    "server": "emby",
                    "url": "http://emby.local:8096",
                    "token": "••••••••7777"
                })
                assert res_emby_test.status_code == 200
                assert res_emby_test.json()["connected"] is True
                mock_test_emby.assert_called_once()
                assert mock_test_emby.call_args.kwargs.get("server") == "emby"
                assert mock_test_emby.call_args.kwargs.get("token") == "emby-secret-token-7777"

            # 4. Clear tokens and restore server_type to plex
            res_clear = client.post("/api/sync/settings", headers=headers, json={
                "server_type": "plex",
                "jellyfin_url": "",
                "jellyfin_user_id": "",
                "emby_url": "",
                "emby_user_id": "",
                "clear_jellyfin_token": True,
                "clear_emby_token": True,
            })
            assert res_clear.status_code == 200
            cleared = res_clear.json()["settings"]
            assert cleared["server_type"] == "plex"
            assert cleared["jellyfin_token"] == ""
            assert cleared["emby_token"] == ""
            assert cleared["is_jellyfin_token_set"] is False
            assert cleared["is_emby_token_set"] is False
    finally:
        settings_mgr.update_reconciliation_settings(orig_recon)
        reverse_sync_mgr.update_config(
            plex_url=orig_recon.get("plex_url", ""),
            plex_token=orig_recon.get("plex_token", ""),
            jellyfin_url=orig_recon.get("jellyfin_url", ""),
            jellyfin_token=orig_recon.get("jellyfin_token", ""),
            emby_url=orig_recon.get("emby_url", ""),
            emby_token=orig_recon.get("emby_token", ""),
        )


def test_manual_scrobble_action_start_and_playback():
    """Verify manual scrobble supports action='start' (Now Playing session & playback start scrobble) and action='watched'."""
    client = TestClient(app)
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
            res_start = client.post("/api/scrobble/manual", json=payload_start)
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
                res_watched = client.post("/api/scrobble/manual", json=payload_watched)
                assert res_watched.status_code == 200
                data_watched = res_watched.json()
                assert data_watched["status"] == "success"
                assert data_watched["action"] == "watched"
                assert mock_sync.called

        client.cookies.clear()


@pytest.mark.asyncio
async def test_manual_scrobble_simkl_override_when_auto_sync_paused():
    """Verify that when Simkl is paused in settings (stopping automatic media server webhook sync),
    explicit manual scrobble to Simkl via selected_trackers still succeeds without double-scrobbling incoming webhooks."""
    from app.services.settings_manager import settings_mgr
    from app.main import multi_tracker, simkl

    # 1. Pause Simkl in settings (as user does to prevent webhook double-scrobbles)
    orig_simkl_setting = settings_mgr.is_tracker_enabled("simkl")
    try:
        settings_mgr.set_tracker_enabled("simkl", False)
        assert settings_mgr.is_tracker_enabled("simkl") is False

        # 2. Verify incoming webhook would not trigger Simkl auto-sync
        from app.main import execute_multi_tracker_dispatch
        parsed_webhook = ParsedMedia(
            event="media.scrobble",
            username="testuser",
            media_type="movie",
            title="Dune: Part Two",
            year=2024,
            progress=100.0,
        )
        with patch("app.main.execute_simkl_scrobble", new_callable=AsyncMock) as mock_simkl_auto:
            await execute_multi_tracker_dispatch(parsed_webhook, "mark_watched", "media.scrobble", 100.0)
            mock_simkl_auto.assert_not_called()

        # 3. Verify manual scrobble with explicit trackers=['simkl'] DOES dispatch to Simkl
        with patch.object(simkl, "is_enabled", return_value=True), \
             patch.object(simkl, "is_authenticated", return_value=True), \
             patch.object(simkl, "sync_history", new_callable=AsyncMock) as mock_simkl_manual, \
             patch.object(simkl, "scrobble_start", new_callable=AsyncMock) as mock_simkl_start:

            mock_simkl_manual.return_value = {"added": {"movies": 1}}
            mock_simkl_start.return_value = {"action": "start"}

            # A. Manual watched to Simkl
            res_manual = await multi_tracker.dispatch_manual_scrobble(
                media=parsed_webhook,
                trakt_client=trakt,
                selected_trackers=["simkl"],
            )
            assert "simkl" in res_manual["synced_trackers"]
            mock_simkl_manual.assert_called_once()

            # B. Manual start to Simkl
            res_start = await multi_tracker.dispatch_scrobble(
                action="start",
                media=parsed_webhook,
                trakt_client=trakt,
                progress=1.0,
                selected_trackers=["simkl"],
            )
            assert "simkl" in res_start["trackers"]
            mock_simkl_start.assert_called_once()

    finally:
        settings_mgr.set_tracker_enabled("simkl", orig_simkl_setting)


@pytest.mark.asyncio
async def test_sonarr_client_unit():
    """Unit test SonarrClient methods with MockTransport."""
    from app.clients.sonarr_client import SonarrClient

    def handler(request: httpx.Request):
        url = str(request.url)
        if "/api/v3/system/status" in url:
            return httpx.Response(200, json={"version": "4.0.0.1234"})
        elif "/api/v3/rootfolder" in url:
            return httpx.Response(200, json=[{"path": "/tv", "freeSpace": 1000000000}])
        elif "/api/v3/qualityprofile" in url:
            return httpx.Response(200, json=[{"id": 1, "name": "HD-1080p"}])
        elif "/api/v3/series/lookup" in url:
            return httpx.Response(200, json=[{"title": "Severance", "year": 2022, "tvdbId": 12345}])
        elif request.method == "GET" and "/api/v3/series" in url:
            return httpx.Response(200, json=[{"title": "Severance", "tvdbId": 12345, "id": 1}])
        elif request.method == "POST" and "/api/v3/series" in url:
            return httpx.Response(201, json={"title": "The Bear", "tvdbId": 67890, "id": 2})
        return httpx.Response(404, json={"error": "Not Found"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sonarr.local")
    sc = SonarrClient(base_url="http://sonarr.local", api_key="test_key", client=mock_client)

    assert sc.is_configured is True

    # 1. check_connection
    conn = await sc.check_connection()
    assert conn["status"] == "connected"
    assert conn["version"] == "4.0.0.1234"

    # 2. get_root_folders
    folders = await sc.get_root_folders()
    assert len(folders) == 1
    assert folders[0]["path"] == "/tv"

    # 3. get_quality_profiles
    profiles = await sc.get_quality_profiles()
    assert len(profiles) == 1
    assert profiles[0]["id"] == 1

    # 4. lookup_series
    lookup = await sc.lookup_series("tvdb:12345")
    assert len(lookup) == 1
    assert lookup[0]["title"] == "Severance"

    # 5. has_series
    has_by_id = await sc.has_series(tvdb_id=12345)
    assert has_by_id is True
    has_by_title = await sc.has_series(title="Severance")
    assert has_by_title is True
    has_missing = await sc.has_series(tvdb_id=99999, title="Unknown Show")
    assert has_missing is False

    # 6. add_series
    added = await sc.add_series({"title": "The Bear", "tvdbId": 67890}, quality_profile_id=1, root_folder_path="/tv")
    assert added is not None
    assert added["success"] is True
    assert added["data"]["id"] == 2

    await sc.close()


@pytest.mark.asyncio
async def test_radarr_client_unit():
    """Unit test RadarrClient methods with MockTransport."""
    from app.clients.radarr_client import RadarrClient

    def handler(request: httpx.Request):
        url = str(request.url)
        if "/api/v3/system/status" in url:
            return httpx.Response(200, json={"version": "5.0.0.8000"})
        elif "/api/v3/rootfolder" in url:
            return httpx.Response(200, json=[{"path": "/movies", "freeSpace": 5000000000}])
        elif "/api/v3/qualityprofile" in url:
            return httpx.Response(200, json=[{"id": 1, "name": "HD-1080p"}])
        elif "/api/v3/movie/lookup" in url:
            return httpx.Response(200, json=[{"title": "Dune: Part Two", "year": 2024, "tmdbId": 693134}])
        elif request.method == "GET" and "/api/v3/movie" in url:
            return httpx.Response(200, json=[{"title": "Dune: Part Two", "tmdbId": 693134, "id": 1}])
        elif request.method == "POST" and "/api/v3/movie" in url:
            return httpx.Response(201, json={"title": "Gladiator II", "tmdbId": 558449, "id": 2})
        return httpx.Response(404, json={"error": "Not Found"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://radarr.local")
    rc = RadarrClient(base_url="http://radarr.local", api_key="test_key", client=mock_client)

    assert rc.is_configured is True

    # 1. check_connection
    conn = await rc.check_connection()
    assert conn["status"] == "connected"
    assert conn["version"] == "5.0.0.8000"

    # 2. get_movies
    movies = await rc.get_movies()
    assert len(movies) == 1
    assert movies[0]["title"] == "Dune: Part Two"

    # 3. get_root_folders
    folders = await rc.get_root_folders()
    assert len(folders) == 1
    assert folders[0]["path"] == "/movies"

    # 4. get_quality_profiles
    profiles = await rc.get_quality_profiles()
    assert len(profiles) == 1
    assert profiles[0]["id"] == 1

    # 5. lookup_movie
    lookup = await rc.lookup_movie("tmdb:693134")
    assert len(lookup) == 1
    assert lookup[0]["title"] == "Dune: Part Two"

    # 6. has_movie
    has_by_id = await rc.has_movie(tmdb_id=693134)
    assert has_by_id is True
    has_by_title = await rc.has_movie(title="Dune: Part Two")
    assert has_by_title is True
    has_missing = await rc.has_movie(tmdb_id=999999, title="Unknown Movie")
    assert has_missing is False

    # 7. add_movie
    added = await rc.add_movie({"title": "Gladiator II", "tmdbId": 558449}, quality_profile_id=1, root_folder_path="/movies")
    assert added is not None
    assert added["success"] is True
    assert added["data"]["id"] == 2

    await rc.close()


@pytest.mark.asyncio
async def test_arr_bridge_watchlist_sync_full():
    """Unit test ArrBridgeManager.sync_watchlist() with mocked Trakt, Sonarr, and Radarr clients."""
    from app.services.arr_bridge import ArrBridgeManager
    from app.clients.sonarr_client import SonarrClient
    from app.clients.radarr_client import RadarrClient

    # Mock Sonarr: has "Severance" (tvdb: 12345), missing "The Bear" (tvdb: 67890)
    def sonarr_handler(request: httpx.Request):
        url = str(request.url)
        if "/api/v3/system/status" in url:
            return httpx.Response(200, json={"version": "4.0.9"})
        elif "/api/v3/series/lookup" in url:
            return httpx.Response(200, json=[{"title": "The Bear", "year": 2022, "tvdbId": 67890, "seasons": []}])
        elif request.method == "GET" and "/api/v3/series" in url:
            return httpx.Response(200, json=[{"title": "Severance", "tvdbId": 12345}])
        elif "/api/v3/rootfolder" in url:
            return httpx.Response(200, json=[{"path": "/data/tv"}])
        elif "/api/v3/qualityprofile" in url:
            return httpx.Response(200, json=[{"id": 1, "name": "HD-1080p"}])
        elif request.method == "POST" and "/api/v3/series" in url:
            return httpx.Response(201, json={"title": "The Bear", "tvdbId": 67890, "id": 10})
        return httpx.Response(404, json={"error": "Not Found"})

    # Mock Radarr: has "Dune: Part Two" (tmdb: 693134), missing "Gladiator II" (tmdb: 558449)
    def radarr_handler(request: httpx.Request):
        url = str(request.url)
        if "/api/v3/system/status" in url:
            return httpx.Response(200, json={"version": "5.9.1"})
        elif "/api/v3/movie/lookup" in url:
            return httpx.Response(200, json=[{"title": "Gladiator II", "year": 2024, "tmdbId": 558449}])
        elif request.method == "GET" and "/api/v3/movie" in url:
            return httpx.Response(200, json=[{"title": "Dune: Part Two", "tmdbId": 693134}])
        elif "/api/v3/rootfolder" in url:
            return httpx.Response(200, json=[{"path": "/data/movies"}])
        elif "/api/v3/qualityprofile" in url:
            return httpx.Response(200, json=[{"id": 1, "name": "HD-1080p"}])
        elif request.method == "POST" and "/api/v3/movie" in url:
            return httpx.Response(201, json={"title": "Gladiator II", "tmdbId": 558449, "id": 20})
        return httpx.Response(404, json={"error": "Not Found"})

    sonarr_c = SonarrClient(
        base_url="http://sonarr.local", api_key="k",
        client=httpx.AsyncClient(transport=httpx.MockTransport(sonarr_handler), base_url="http://sonarr.local")
    )
    radarr_c = RadarrClient(
        base_url="http://radarr.local", api_key="k",
        client=httpx.AsyncClient(transport=httpx.MockTransport(radarr_handler), base_url="http://radarr.local")
    )

    # Mock Trakt
    class MockTraktClient:
        def is_authenticated(self):
            return True

        async def get_watchlist(self, media_type: str = "movies"):
            if media_type == "movies":
                return [
                    {"movie": {"title": "Dune: Part Two", "year": 2024, "ids": {"tmdb": 693134}}},
                    {"movie": {"title": "Gladiator II", "year": 2024, "ids": {"tmdb": 558449}}},
                ]
            else:
                return [
                    {"show": {"title": "Severance", "year": 2022, "ids": {"tvdb": 12345}}},
                    {"show": {"title": "The Bear", "year": 2022, "ids": {"tvdb": 67890}}},
                ]

    bridge = ArrBridgeManager(
        sonarr_client=sonarr_c,
        radarr_client=radarr_c,
        trakt_client=MockTraktClient(),
    )

    res = await bridge.sync_watchlist()
    assert res["added"]["movies"] == 1
    assert res["skipped"]["movies"] == 1
    assert res["added"]["shows"] == 1
    assert res["skipped"]["shows"] == 1
    assert len(res["errors"]) == 0

    # Test unauthenticated Trakt
    class UnauthTraktClient:
        def is_authenticated(self):
            return False

    bridge.set_trakt_client(UnauthTraktClient())
    unauth_res = await bridge.sync_watchlist()
    assert unauth_res["success"] is False
    assert "not authenticated" in unauth_res["error"]

    # Test demo mode
    demo_res = await bridge.sync_watchlist(demo=True)
    assert demo_res["added"]["movies"] >= 1
    assert demo_res["added"]["shows"] >= 1


@pytest.mark.asyncio
async def test_arr_bridge_ecosystem_and_status():
    """Verify get_status and get_ecosystem_status reporting."""
    from app.services.arr_bridge import arr_bridge

    # Demo status
    demo_status = await arr_bridge.get_status(demo=True)
    assert demo_status["configured"] is True
    assert demo_status["sonarr_connected"] is True
    assert demo_status["radarr_connected"] is True
    assert demo_status["sonarr_series_count"] == 48
    assert demo_status["radarr_movies_count"] == 215

    # Demo ecosystem
    demo_eco = await arr_bridge.get_ecosystem_status(demo=True)
    assert demo_eco["healthy_count"] == 9
    assert demo_eco["total_count"] == 9
    server_ids = [s["id"] for s in demo_eco["servers"]]
    # Reordered: Servers (Plex, Jellyfin, Emby) -> Arr (Sonarr, Radarr) -> Trackers (Trakt, Simkl, AniList, MAL)
    assert server_ids == ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]

    # Live ecosystem
    live_eco = await arr_bridge.get_ecosystem_status(demo=False)
    assert "servers" in live_eco
    assert len(live_eco["servers"]) >= 4

    # Verify that enabled items precede disabled items in live ecosystem
    disabled_seen = False
    for s in live_eco["servers"]:
        is_dis = not s.get("enabled", True) or s.get("status") == "disabled" or s.get("badge") in ("Disabled", "Paused")
        if is_dis:
            disabled_seen = True
        else:
            assert not disabled_seen, f"Active server {s['id']} appeared after disabled servers"


@pytest.mark.asyncio
async def test_ecosystem_reordering_and_disabled_sorting():
    """Verify ecosystem reordering (Servers -> Arr -> Trackers) and disabled items moving to end."""
    from app.services.arr_bridge import arr_bridge
    from app.services.settings_manager import settings_mgr

    # 1. Verify demo mode canonical order
    demo_res = await arr_bridge.get_ecosystem_status(demo=True)
    demo_ids = [s["id"] for s in demo_res["servers"]]
    assert demo_ids == ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]

    # 2. Verify live mode ordering with toggled settings
    orig_jellyfin = settings_mgr.is_server_enabled("jellyfin")
    orig_simkl = settings_mgr.is_tracker_enabled("simkl")
    try:
        settings_mgr.set_server_enabled("jellyfin", False)
        settings_mgr.set_tracker_enabled("simkl", False)

        from app.main import simkl, anilist, mal, reverse_sync_mgr
        live_res = await arr_bridge.get_ecosystem_status(
            demo=False,
            plex_client=reverse_sync_mgr.plex,
            simkl_client=simkl,
            anilist_client=anilist,
            mal_client=mal,
        )
        servers = live_res["servers"]

        active_ids = [
            s["id"] for s in servers
            if s.get("enabled", True) and s.get("status") != "disabled" and s.get("badge") not in ("Disabled", "Paused")
        ]
        disabled_ids = [
            s["id"] for s in servers
            if (not s.get("enabled", True)) or s.get("status") == "disabled" or s.get("badge") in ("Disabled", "Paused")
        ]

        # Jellyfin and Simkl must be in disabled_ids
        assert "jellyfin" in disabled_ids
        assert "simkl" in disabled_ids

        # All active items must precede all disabled items in the overall server list
        full_ids = [s["id"] for s in servers]
        assert full_ids == active_ids + disabled_ids

        # Within disabled items, ordering should remain Servers -> Arr -> Trackers
        service_order = ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]
        disabled_indices = [service_order.index(sid) for sid in disabled_ids if sid in service_order]
        assert disabled_indices == sorted(disabled_indices)
    finally:
        settings_mgr.set_server_enabled("jellyfin", orig_jellyfin)
        settings_mgr.set_tracker_enabled("simkl", orig_simkl)


def test_arr_api_endpoints_and_auth():
    """Test /api/arr/status, /api/arr/sync, and /api/ecosystem routes."""
    client = TestClient(app)

    # 1. /api/arr/status
    res = client.get("/api/arr/status")
    assert res.status_code == 200
    assert "configured" in res.json()

    res_demo = client.get("/api/arr/status?demo=true")
    assert res_demo.status_code == 200
    assert res_demo.json()["sonarr_series_count"] == 48

    # 2. /api/ecosystem
    res_eco = client.get("/api/ecosystem")
    assert res_eco.status_code == 200
    assert "servers" in res_eco.json()

    res_eco_demo = client.get("/api/ecosystem?demo=true")
    assert res_eco_demo.status_code == 200
    assert res_eco_demo.json()["healthy_count"] == 9
    demo_api_ids = [s["id"] for s in res_eco_demo.json()["servers"]]
    assert demo_api_ids == ["plex", "jellyfin", "emby", "sonarr", "radarr", "trakt", "simkl", "anilist", "myanimelist"]

    # 3. /api/arr/sync
    # Demo execution allowed without auth
    res_sync_demo = client.post("/api/arr/sync?demo=true")
    assert res_sync_demo.status_code == 200
    assert "added" in res_sync_demo.json()

    # Non-demo requires admin
    if Config.WEBHOOK_SECRET:
        res_sync_unauth = client.post("/api/arr/sync")
        assert res_sync_unauth.status_code == 401

        # Admin authorized
        headers = {"x-webhook-secret": Config.WEBHOOK_SECRET}
        res_sync_auth = client.post("/api/arr/sync", headers=headers)
        assert res_sync_auth.status_code == 200


def test_dashboard_arr_and_ecosystem_cards():
    """Verify Multi-Server Ecosystem and Arr Bridge cards appear on dashboard."""
    client = TestClient(app)

    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "Multi-Server Ecosystem" in html
    assert "Content Bridge" in html
    assert 'id="arr-modal"' in html
    assert "eco-card" in html

    res_demo = client.get("/demo")
    assert res_demo.status_code == 200
    assert "Multi-Server Ecosystem" in res_demo.text
    assert "Content Bridge" in res_demo.text
    assert "eco-card" in res_demo.text


def test_notifier_arr_add_action():
    """Verify Notifier formats and handles arr_add action."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier_inst = Notifier(Config)

    media = ParsedMedia(
        raw_payload={},
        event="arr.add",
        media_type="movie",
        title="Gladiator II",
        year=2024,
        username="Radarr",
    )

    payload = notifier_inst.build_discord_payload(media, "arr_add")
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Gladiator II (2024)" in embed["title"]
    assert embed["color"] == 0x2ECC71
    assert "Added to **Radarr** from Trakt Watchlist" in embed["description"]

    # Also test show
    show_media = ParsedMedia(
        raw_payload={},
        event="arr.add",
        media_type="show",
        title="Severance",
        year=2022,
        username="Sonarr",
    )
    show_payload = notifier_inst.build_discord_payload(show_media, "arr_add")
    assert "Added to **Sonarr** from Trakt Watchlist" in show_payload["embeds"][0]["description"]


# =====================================================================
# SIMKL & MULTI-TRACKER ARCHITECTURE TESTS (v1.7.0)
# =====================================================================

@pytest.mark.asyncio
async def test_simkl_client_token_lifecycle(tmp_path):
    """Verify Simkl token file persistence, loading, and deletion."""
    from app.clients.simkl_client import SimklClient
    from app.config import Config

    test_tokens_file = tmp_path / "simkl_tokens.json"
    client = SimklClient(
        client_id="test_simkl_id",
        client_secret="test_simkl_secret",
        tokens_file=test_tokens_file,
    )

    assert client.is_authenticated() is False
    assert client.get_access_token() is None

    # Save tokens
    client.save_tokens({"access_token": "simkl_secret_token_123", "token_type": "Bearer"})
    assert test_tokens_file.exists()
    assert client.is_authenticated() is True
    assert client.get_access_token() == "simkl_secret_token_123"

    # Reload from disk in a fresh client
    client2 = SimklClient(
        client_id="test_simkl_id",
        tokens_file=test_tokens_file,
    )
    assert client2.is_authenticated() is True
    assert client2.get_access_token() == "simkl_secret_token_123"

    # Delete tokens
    client2.delete_tokens()
    assert not test_tokens_file.exists()
    assert client2.is_authenticated() is False
    await client.close()
    await client2.close()


def test_simkl_client_payload_builder():
    """Verify Simkl JSON payload formatting for movies and TV episodes."""
    from app.clients.simkl_client import SimklClient
    from app.plex_parser import ParsedMedia

    client = SimklClient(client_id="test_id")

    # 1. Movie payload
    movie = ParsedMedia(
        raw_payload={},
        event="media.scrobble",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        username="test_user",
        ids={"imdb": "tt15239678", "tmdb": "693134"},
    )
    m_payload = client.build_media_payload(movie)
    assert "movie" in m_payload
    assert m_payload["movie"]["title"] == "Dune: Part Two"
    assert m_payload["movie"]["year"] == 2024
    assert m_payload["movie"]["ids"]["imdb"] == "tt15239678"
    assert m_payload["movie"]["ids"]["tmdb"] == "693134"

    # 2. Episode payload
    ep = ParsedMedia(
        raw_payload={},
        event="media.scrobble",
        media_type="episode",
        title="Severance",
        show_title="Severance",
        season=1,
        episode=9,
        year=2022,
        username="test_user",
        ids={"tvdb": "371980", "imdb": "tt11280740"},
    )
    e_payload = client.build_media_payload(ep)
    assert "show" in e_payload
    assert e_payload["show"]["title"] == "Severance"
    assert e_payload["show"]["year"] == 2022
    assert e_payload["show"]["ids"]["tvdb"] == "371980"
    assert "episode" in e_payload
    assert e_payload["episode"]["season"] == 1
    assert e_payload["episode"]["number"] == 9


@pytest.mark.asyncio
async def test_simkl_client_device_pin_and_poll(tmp_path):
    """Verify Simkl OAuth Device PIN flow request and polling."""
    from app.clients.simkl_client import SimklClient
    import httpx

    tokens_file = tmp_path / "simkl_test_tokens.json"

    def mock_pin_handler(request: httpx.Request):
        if "oauth/pin" in str(request.url) and "DEMO-PIN" not in str(request.url):
            return httpx.Response(
                200,
                json={"user_code": "DEMO-PIN", "verification_url": "https://simkl.com/pin/DEMO-PIN", "expires_in": 900},
            )
        elif "oauth/pin/DEMO-PIN" in str(request.url):
            return httpx.Response(
                200,
                json={"result": "OK", "access_token": "simkl_access_abc"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_pin_handler)
    mock_http = httpx.AsyncClient(transport=transport)

    client = SimklClient(client_id="test_client_id", tokens_file=tokens_file, client=mock_http)
    pin_data = await client.get_device_pin()
    assert pin_data["user_code"] == "DEMO-PIN"
    assert "https://simkl.com/pin/DEMO-PIN" in pin_data["verification_url"]

    poll_data = await client.poll_device_pin("DEMO-PIN")
    assert poll_data["result"] == "OK"
    assert poll_data["access_token"] == "simkl_access_abc"
    assert client.is_authenticated() is True
    assert client.get_access_token() == "simkl_access_abc"
    await client.close()


@pytest.mark.asyncio
async def test_simkl_client_scrobble_actions(tmp_path):
    """Verify Simkl scrobble_start, scrobble_pause, and scrobble_stop."""
    from app.clients.simkl_client import SimklClient
    from app.plex_parser import ParsedMedia
    import httpx

    tokens_file = tmp_path / "simkl_tokens.json"
    client = SimklClient(client_id="test_id", tokens_file=tokens_file)
    client.save_tokens({"access_token": "valid_token"})

    scrobble_requests = []

    def mock_scrobble_handler(request: httpx.Request):
        scrobble_requests.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": json.loads(request.content.decode("utf-8")),
        })
        return httpx.Response(200, json={"result": "ok"})

    transport = httpx.MockTransport(mock_scrobble_handler)
    client._client = httpx.AsyncClient(transport=transport)

    media = ParsedMedia(
        raw_payload={},
        event="media.play",
        media_type="movie",
        title="Gladiator II",
        year=2024,
        username="test_user",
        progress=10.0,
    )

    # 1. Start
    res1 = await client.scrobble_start(media, progress=10.0)
    assert res1.get("status") == "success"
    assert res1.get("data", {}).get("result") == "ok"
    assert "scrobble/start" in scrobble_requests[-1]["url"]

    # 2. Pause
    res2 = await client.scrobble_pause(media, progress=50.0)
    assert res2.get("status") == "success"
    assert res2.get("data", {}).get("result") == "ok"
    assert "scrobble/pause" in scrobble_requests[-1]["url"]

    # 3. Stop
    res3 = await client.scrobble_stop(media, progress=95.0)
    assert res3.get("status") == "success"
    assert res3.get("data", {}).get("result") == "ok"
    assert "scrobble/stop" in scrobble_requests[-1]["url"]

    await client.close()


@pytest.mark.asyncio
async def test_simkl_client_sync_history_and_ratings(tmp_path):
    """Verify Simkl sync_history and sync_ratings with ParsedMedia objects."""
    from app.clients.simkl_client import SimklClient
    from app.plex_parser import ParsedMedia
    import httpx

    tokens_file = tmp_path / "simkl_tokens.json"
    client = SimklClient(client_id="test_id", tokens_file=tokens_file)
    client.save_tokens({"access_token": "valid_token"})

    posted_requests = []

    def mock_post_handler(request: httpx.Request):
        posted_requests.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode("utf-8")),
        })
        return httpx.Response(200, json={"result": "ok"})

    transport = httpx.MockTransport(mock_post_handler)
    client._client = httpx.AsyncClient(transport=transport)

    # 1. Movie sync_history with ids dict
    movie_media = ParsedMedia(
        raw_payload={},
        event="manual.scrobble",
        media_type="movie",
        title="Digger",
        year=2026,
        username="selits",
        ids={"imdb": "tt1234567", "tmdb": 98765},
    )
    res_movie = await client.sync_history(movie_media)
    assert res_movie.get("status") == "success"
    assert "/sync/history" in posted_requests[-1]["url"]
    movies_payload = posted_requests[-1]["body"].get("movies", [])
    assert len(movies_payload) == 1
    assert movies_payload[0]["title"] == "Digger"
    assert movies_payload[0]["year"] == 2026
    assert movies_payload[0]["ids"] == {"imdb": "tt1234567", "tmdb": "98765"}

    # 2. Show / Episode sync_history
    ep_media = ParsedMedia(
        raw_payload={},
        event="manual.scrobble",
        media_type="episode",
        title="Pilot",
        show_title="Test Series",
        show_year=2025,
        season=1,
        episode=1,
        username="selits",
        ids={"tvdb": 55555},
    )
    res_ep = await client.sync_history(ep_media)
    assert res_ep.get("status") == "success"
    shows_payload = posted_requests[-1]["body"].get("shows", [])
    assert len(shows_payload) == 1
    assert shows_payload[0]["title"] == "Test Series"
    assert shows_payload[0]["year"] == 2025
    assert shows_payload[0]["ids"] == {"tvdb": "55555"}
    assert shows_payload[0]["seasons"][0]["number"] == 1
    assert shows_payload[0]["seasons"][0]["episodes"][0]["number"] == 1

    # 3. Movie sync_ratings
    res_rate_movie = await client.sync_ratings(movie_media, rating=9)
    assert res_rate_movie.get("status") == "success"
    assert "/sync/ratings" in posted_requests[-1]["url"]
    rate_movies = posted_requests[-1]["body"].get("movies", [])
    assert len(rate_movies) == 1
    assert rate_movies[0]["title"] == "Digger"
    assert rate_movies[0]["rating"] == 9

    # 4. Show sync_ratings
    res_rate_show = await client.sync_ratings(ep_media, rating=10)
    assert res_rate_show.get("status") == "success"
    rate_shows = posted_requests[-1]["body"].get("shows", [])
    assert len(rate_shows) == 1
    assert rate_shows[0]["title"] == "Test Series"
    assert rate_shows[0]["rating"] == 10

    await client.close()


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


@pytest.mark.asyncio
async def test_multi_tracker_manual_scrobble_simkl_live_dispatch(tmp_path):
    """Verify dispatch_manual_scrobble works end-to-end with Simkl without AttributeError."""
    from app.services.multi_tracker import MultiTrackerManager
    from app.clients.simkl_client import SimklClient
    from app.clients.trakt_client import TraktClient
    from app.plex_parser import ParsedMedia
    from unittest.mock import AsyncMock
    import httpx

    tokens_file = tmp_path / "simkl_tokens.json"
    simkl = SimklClient(client_id="test_id", tokens_file=tokens_file)
    simkl.save_tokens({"access_token": "valid_token"})

    simkl_requests = []

    def mock_simkl_handler(request: httpx.Request):
        simkl_requests.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode("utf-8")),
        })
        return httpx.Response(200, json={"result": "ok"})

    simkl._client = httpx.AsyncClient(transport=httpx.MockTransport(mock_simkl_handler))

    mock_trakt = AsyncMock(spec=TraktClient)
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.sync_history.return_value = {"added": {"movies": 1}}

    mt_mgr = MultiTrackerManager(simkl_client=simkl)

    media = ParsedMedia(
        raw_payload={},
        event="manual.scrobble",
        media_type="movie",
        title="Digger",
        year=2026,
        username="selits",
        ids={"imdb": "tt1234567"},
    )

    res = await mt_mgr.dispatch_manual_scrobble(
        media=media,
        trakt_client=mock_trakt,
        selected_trackers=["simkl"],
    )

    assert res["status"] == "success"
    assert "simkl" in res["synced_trackers"]
    assert "simkl" not in res["errors"]
    assert len(simkl_requests) == 1
    assert "/sync/history" in simkl_requests[0]["url"]
    assert simkl_requests[0]["body"]["movies"][0]["ids"]["imdb"] == "tt1234567"

    await simkl.close()


@pytest.mark.asyncio
async def test_multi_tracker_manager_dispatch():
    """Verify MultiTrackerManager dual dispatch to Trakt and Simkl."""
    from app.services.multi_tracker import MultiTrackerManager
    from app.clients.simkl_client import SimklClient
    from app.clients.trakt_client import TraktClient
    from app.config import Config
    from app.plex_parser import ParsedMedia
    from unittest.mock import AsyncMock

    mock_trakt = AsyncMock(spec=TraktClient)
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.scrobble_stop.return_value = {"action": "scrobble", "trakt_id": 1234}
    mock_trakt.sync_ratings.return_value = {"added": {"movies": 1}}

    mock_simkl = AsyncMock(spec=SimklClient)
    mock_simkl.is_authenticated.return_value = True
    mock_simkl.is_enabled.return_value = True
    mock_simkl.check_connection.return_value = {"authenticated": True, "enabled": True}
    mock_simkl.scrobble_stop.return_value = {"result": "ok"}
    mock_simkl.sync_ratings.return_value = {"result": "ok"}

    mt_mgr = MultiTrackerManager(simkl_client=mock_simkl)

    status = await mt_mgr.get_status()
    assert "trakt" in status["active_trackers"]
    assert "simkl" in status["active_trackers"]

    media = ParsedMedia(
        raw_payload={},
        event="media.scrobble",
        media_type="movie",
        title="Severance",
        year=2022,
        username="test_user",
        progress=95.0,
    )

    # Scrobble dispatch
    res = await mt_mgr.dispatch_scrobble(action="stop", media=media, trakt_client=mock_trakt, progress=95.0)
    assert "trakt" in res
    assert "simkl" in res
    assert "simkl" in res["trackers"]
    mock_trakt.scrobble_stop.assert_awaited_once_with(media, progress=95.0)
    mock_simkl.scrobble_stop.assert_awaited_once_with(media, progress=95.0)

    # Rating dispatch
    rate_res = await mt_mgr.dispatch_rating(media=media, trakt_client=mock_trakt, rating=10)
    assert "trakt" in rate_res
    assert "simkl" in rate_res
    assert "simkl" in rate_res["trackers"]
    mock_trakt.sync_ratings.assert_awaited_once()
    mock_simkl.sync_ratings.assert_awaited_once_with(media, rating=10)


def test_simkl_api_endpoints_and_views():
    """Verify /api/simkl/status, /api/simkl/pin, /api/simkl/poll, and /auth/simkl routes."""
    client = TestClient(app)

    # 1. Demo Status
    demo_resp = client.get("/api/simkl/status?demo=true")
    assert demo_resp.status_code == 200
    d_data = demo_resp.json()
    assert d_data["enabled"] is True
    assert d_data["configured"] is True
    assert d_data["authenticated"] is True
    assert d_data["user"] == "demo_viewer"

    # 2. Live Status (public read)
    live_resp = client.get("/api/simkl/status")
    assert live_resp.status_code == 200

    # 3. Auth Simkl page (Admin protected)
    if Config.WEBHOOK_SECRET:
        unauth_page = client.get("/auth/simkl")
        assert unauth_page.status_code == 401

        auth_page = client.get(f"/auth/simkl?token={Config.WEBHOOK_SECRET}")
        assert auth_page.status_code == 200
        assert "Simkl" in auth_page.text
        assert "Device PIN Authorization" in auth_page.text
    else:
        auth_page = client.get("/auth/simkl")
        assert auth_page.status_code == 200
        assert "Simkl" in auth_page.text

    # 4. Disconnect Simkl route
    if Config.WEBHOOK_SECRET:
        unauth_disc = client.post("/api/simkl/disconnect")
        assert unauth_disc.status_code == 401

        auth_disc = client.post(f"/api/simkl/disconnect?token={Config.WEBHOOK_SECRET}")
        assert auth_disc.status_code == 200
        assert auth_disc.json()["status"] == "ok"
    else:
        auth_disc = client.post("/api/simkl/disconnect")
        assert auth_disc.status_code == 200
        assert auth_disc.json()["status"] == "ok"

    # 5. Simkl PIN endpoint
    from app.main import simkl
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        unauth_pin = client.post("/api/simkl/pin")
        assert unauth_pin.status_code == 401

        with patch.object(simkl, "get_device_pin", return_value={"error": "SIMKL_CLIENT_ID not configured"}):
            err_pin = client.post("/api/simkl/pin?token=testsecret")
            assert err_pin.status_code == 400
            assert "SIMKL_CLIENT_ID not configured" in err_pin.json()["detail"]

        with patch.object(simkl, "get_device_pin", return_value={"user_code": "ABCD-1234", "device_code": "dev_123", "verification_url": "https://simkl.com/pin?user_code=ABCD-1234"}):
            ok_pin = client.post("/api/simkl/pin?token=testsecret")
            assert ok_pin.status_code == 200
            assert ok_pin.json()["user_code"] == "ABCD-1234"
            assert ok_pin.json()["device_code"] == "dev_123"

        with patch.object(simkl, "poll_device_pin", return_value={"status": "success", "result": "OK", "access_token": "tok123"}) as mock_poll:
            poll_resp = client.post("/api/simkl/poll?token=testsecret", json={"user_code": "ABCD-1234", "device_code": "dev_123"})
            assert poll_resp.status_code == 200
            assert poll_resp.json()["status"] == "success"
            mock_poll.assert_called_once_with("ABCD-1234", device_code="dev_123")


@pytest.mark.asyncio
async def test_simkl_client_auth_v2_device_flow(tmp_path):
    """Verify Simkl AUTH V2 Device PIN request, polling, and token refresh."""
    from app.clients.simkl_client import SimklClient
    import httpx

    tokens_file = tmp_path / "simkl_v2_tokens.json"

    def mock_v2_handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/device" in url:
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "device_code": "v2_device_xyz",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://simkl.com/pin",
                    "verification_uri_complete": "https://simkl.com/pin?user_code=WXYZ-1234",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        elif "oauth2/token" in url:
            body = request.content.decode("utf-8")
            if "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in body:
                if "pending" in body:
                    return httpx.Response(400, json={"error": "authorization_pending"})
                if "unauth_test" in body:
                    return httpx.Response(401, json={"error": "invalid_client", "error_description": "Missing client_secret"})
                if "with_secret" in body:
                    assert "client_secret=my_secret_123" in body
                return httpx.Response(
                    200,
                    json={
                        "access_token": "v2_access_token_123",
                        "token_type": "Bearer",
                        "expires_in": 604800,
                        "refresh_token": "v2_refresh_token_456",
                        "scope": "media:read media:write",
                    },
                )
            elif "grant_type=refresh_token" in body:
                if "client_secret" in body:
                    assert "client_secret=my_secret_123" in body
                return httpx.Response(
                    200,
                    json={
                        "access_token": "v2_access_token_refreshed",
                        "token_type": "Bearer",
                        "expires_in": 604800,
                        "refresh_token": "v2_refresh_token_456",
                    },
                )
        elif "users/settings" in url:
            return httpx.Response(200, json={"user": {"name": "simkl_user_test"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_v2_handler)
    mock_http = httpx.AsyncClient(transport=transport)

    client = SimklClient(client_id="test_v2_client_id", client_secret="my_secret_123", tokens_file=tokens_file, client=mock_http)
    assert client.effective_client_secret == "my_secret_123"

    pin_data = await client.get_device_pin()
    assert pin_data["auth_version"] == "v2"
    assert pin_data["device_code"] == "v2_device_xyz"
    assert pin_data["user_code"] == "WXYZ-1234"
    assert "https://simkl.com/pin?user_code=WXYZ-1234" in pin_data["verification_url"]

    # Poll 401 without secret guidance test
    client_no_secret = SimklClient(client_id="test_v2_client_id", tokens_file=tmp_path / "nosec.json", client=mock_http)
    assert client_no_secret.effective_client_secret == ""
    err_401_data = await client_no_secret.poll_device_pin("WXYZ-1234", device_code="unauth_test")
    assert err_401_data["status"] == "error"
    assert "Server apps & services" in err_401_data["error"]
    assert "Client Secret" in err_401_data["error"]

    # Poll pending
    pending_data = await client.poll_device_pin("WXYZ-1234", device_code="v2_device_xyz_pending")
    assert pending_data["status"] == "pending"

    # Poll success with client_secret passed
    success_data = await client.poll_device_pin("WXYZ-1234", device_code="with_secret")
    assert success_data["status"] == "success"
    assert client.access_token == "v2_access_token_123"
    assert client.refresh_token == "v2_refresh_token_456"
    assert client.user_name == "simkl_user_test"

    # Refresh token with client_secret passed
    refreshed = await client.refresh_access_token()
    assert refreshed is True
    assert client.access_token == "v2_access_token_refreshed"
    await client.close()
    await client_no_secret.close()


def test_dashboard_renders_simkl_card():
    """Verify that the dashboard template renders the Simkl Multi-Tracker card and modal."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert "Multi-Tracker Architecture" in html
    assert "Simkl Integration" in html
    assert "simkl-modal" in html
    assert "openSimklModal" in html
    assert "disconnectSimkl" in html


@pytest.mark.asyncio
async def test_simkl_client_all_items_and_bulk_sync(tmp_path):
    """Verify SimklClient get_all_items, get_activities, bulk_sync_history, and bulk_sync_ratings."""
    from app.clients.simkl_client import SimklClient

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/sync/all-items/movies" in url_str:
            return httpx.Response(200, json={"movies": [{"movie": {"title": "Inception", "year": 2010, "ids": {"imdb": "tt1375666"}}, "status": "completed"}]})
        elif "/sync/activities" in url_str:
            return httpx.Response(200, json={"all": "2026-09-28T12:00:00Z", "movies": {"completed": "2026-09-28T12:00:00Z"}})
        elif "/sync/history" in url_str and request.method == "POST":
            return httpx.Response(200, json={"status": "success", "added": {"movies": 1}})
        elif "/sync/ratings" in url_str and request.method == "POST":
            return httpx.Response(200, json={"status": "success", "rated": {"movies": 1}})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as async_client:
        tokens_file = tmp_path / "simkl_tokens.json"
        client = SimklClient(client=async_client, tokens_file=tokens_file, client_id="test_client_id")
        client.access_token = "valid_token"

        # 1. get_all_items
        items = await client.get_all_items("movies")
        assert "movies" in items
        assert items["movies"][0]["movie"]["title"] == "Inception"

        # 2. get_activities
        act = await client.get_activities()
        assert "all" in act

        # 3. bulk_sync_history
        hist_res = await client.bulk_sync_history({"movies": [{"title": "Dune", "year": 2021}]})
        assert hist_res.get("status") == "success"

        # 4. bulk_sync_ratings
        rate_res = await client.bulk_sync_ratings({"movies": [{"title": "Dune", "rating": 10}]})
        assert rate_res.get("status") == "success"


@pytest.mark.asyncio
async def test_cross_tracker_sync_manager_scan_and_execution():
    """Verify CrossTrackerSyncManager discrepancy detection and bi-directional reconciliation."""
    import asyncio
    from app.services.cross_tracker_sync import CrossTrackerSyncManager
    from unittest.mock import AsyncMock, MagicMock

    mock_trakt = MagicMock()
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.get_watched_movies = AsyncMock(return_value=[
        {"movie": {"title": "Trakt Movie Only", "year": 2024, "ids": {"imdb": "tt9999001"}}, "last_watched_at": "2026-09-20T00:00:00Z"},
        {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "last_watched_at": "2026-09-21T00:00:00Z"},
    ])
    mock_trakt.get_watched_shows = AsyncMock(return_value=[
        {
            "show": {"title": "Trakt Show", "year": 2023, "ids": {"imdb": "tt8888001"}},
            "seasons": [{"number": 1, "episodes": [{"number": 1, "last_watched_at": "2026-09-22T00:00:00Z"}]}]
        }
    ])
    mock_trakt.get_ratings = AsyncMock(side_effect=lambda media_type: [
        {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "rating": 10}
    ] if media_type == "movies" else [])
    mock_trakt.sync_history = AsyncMock(return_value={"status": 200, "added": {"movies": 1}})
    mock_trakt.sync_ratings = AsyncMock(return_value={"status": 200, "added": {"movies": 1}})

    mock_simkl = MagicMock()
    mock_simkl.is_authenticated.return_value = True
    mock_simkl.get_all_items = AsyncMock(side_effect=lambda media_type: {
        "movies": [
            {"movie": {"title": "Shared Movie", "year": 2020, "ids": {"imdb": "tt9999002"}}, "status": "completed", "user_rating": 8},
            {"movie": {"title": "Simkl Movie Only", "year": 2022, "ids": {"imdb": "tt7777001"}}, "status": "completed", "last_watched_at": "2026-09-23T00:00:00Z"}
        ]
    } if media_type == "movies" else {"shows": [], "anime": []})
    mock_simkl.bulk_sync_history = AsyncMock(return_value={"status": "success"})
    mock_simkl.bulk_sync_ratings = AsyncMock(return_value={"status": "success"})

    manager = CrossTrackerSyncManager(trakt_client=mock_trakt, simkl_client=mock_simkl)
    assert manager.is_configured() is True

    # 1. Scan discrepancies
    diff = await manager.scan_discrepancies(force=True)
    assert len(diff) >= 4

    # Trakt Movie Only -> Trakt to Simkl
    t2s_movies = [d for d in diff if d["direction"] == "trakt_to_simkl" and d["sync_type"] == "watched" and d["title"] == "Trakt Movie Only"]
    assert len(t2s_movies) == 1
    assert t2s_movies[0]["ids"]["imdb"] == "tt9999001"

    # Shared Movie Rating mismatch -> Trakt rating 10 vs Simkl rating 8
    rating_diffs = [d for d in diff if d["sync_type"] == "rating" and d["title"] == "Shared Movie"]
    assert len(rating_diffs) == 2
    t2s_ratings = [d for d in rating_diffs if d["direction"] == "trakt_to_simkl"]
    s2t_ratings = [d for d in rating_diffs if d["direction"] == "simkl_to_trakt"]
    assert len(t2s_ratings) == 1
    assert t2s_ratings[0]["source_rating"] == 10
    assert t2s_ratings[0]["target_rating"] == 8
    assert len(s2t_ratings) == 1
    assert s2t_ratings[0]["source_rating"] == 8
    assert s2t_ratings[0]["target_rating"] == 10

    # Simkl Movie Only -> Simkl to Trakt
    s2t_movies = [d for d in diff if d["direction"] == "simkl_to_trakt" and d["sync_type"] == "watched" and d["title"] == "Simkl Movie Only"]
    assert len(s2t_movies) == 1
    assert s2t_movies[0]["ids"]["imdb"] == "tt7777001"

    # 2. Execute reconciliation
    sync_res = await manager.execute_sync(direction="both")
    assert sync_res["status"] == "started"

    # Allow background sync task to complete
    await asyncio.sleep(0.1)

    assert mock_simkl.bulk_sync_history.called
    assert mock_trakt.sync_history.called


def test_cross_sync_api_endpoints():
    """Verify FastAPI route handlers for cross-tracker synchronization."""
    client = TestClient(app)

    # 1. GET /api/cross-sync/status
    res = client.get("/api/cross-sync/status?demo=true")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert "diff_count" in data

    # 2. GET /api/cross-sync/diff
    demo_diff_res = client.get("/api/cross-sync/diff?demo=true")
    assert demo_diff_res.status_code == 200
    assert len(demo_diff_res.json()["diff"]) > 0

    if Config.WEBHOOK_SECRET:
        unauth_diff = client.get("/api/cross-sync/diff")
        assert unauth_diff.status_code == 401

        auth_diff = client.get(f"/api/cross-sync/diff?token={Config.WEBHOOK_SECRET}")
        assert auth_diff.status_code == 200
    else:
        auth_diff = client.get("/api/cross-sync/diff")
        assert auth_diff.status_code == 200

    # 3. POST /api/cross-sync/scan
    demo_scan = client.post("/api/cross-sync/scan?demo=true")
    assert demo_scan.status_code == 200
    assert "diff" in demo_scan.json()

    # 4. POST /api/cross-sync/execute
    demo_exec = client.post("/api/cross-sync/execute?demo=true", json={"direction": "both"})
    assert demo_exec.status_code == 200
    assert demo_exec.json()["status"] == "completed"

    # 5. GET /api/cross-sync/progress
    prog = client.get("/api/cross-sync/progress")
    assert prog.status_code == 200
    assert "status" in prog.json()


def test_dashboard_renders_cross_sync_modal():
    """Verify that the dashboard renders the Cross-Tracker Reconciliation modal and trigger buttons."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert "cross-sync-modal" in html
    assert "openCrossSyncModal" in html
    assert "closeCrossSyncModal" in html
    assert "Cross-Tracker Reconciliation" in html


# =====================================================================
# Milestone 6: Anime Tracking Engine (v1.9.0) Tests
# =====================================================================
from app.clients.anilist_client import AniListClient
from app.clients.mal_client import MyAnimeListClient
from app.services.anime_resolver import AnimeResolver
from app.services.multi_tracker import MultiTrackerManager
from app.main import anilist, mal, anime_resolver, multi_tracker


def test_anilist_client_auth_and_persistence(tmp_path):
    token_file = tmp_path / "anilist_tokens.json"
    client = AniListClient(tokens_file=token_file)

    assert not client.is_authenticated()
    client.save_tokens({"access_token": "ani_token_123", "user_name": "otaku_king", "user_id": 9999})
    assert client.is_authenticated()
    assert client.user_name == "otaku_king"
    assert client.user_id == 9999
    assert token_file.exists()

    # Re-instantiate from file
    client2 = AniListClient(tokens_file=token_file)
    assert client2.is_authenticated()
    assert client2.user_name == "otaku_king"

    client2.delete_tokens()
    assert not client2.is_authenticated()
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_anilist_client_check_connection():
    client = AniListClient(access_token="test_token")

    mock_resp_data = {
        "data": {
            "Viewer": {
                "id": 12345,
                "name": "SpikeSpiegel",
                "avatar": {"medium": "https://s4.anilist.co/avatar.png"}
            }
        }
    }

    with patch.object(client, "execute_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_resp_data
        status = await client.check_connection()
        assert status["status"] == "connected"
        assert status["authenticated"] is True
        assert status["user"] == "SpikeSpiegel"
        assert status["id"] == 12345


@pytest.mark.asyncio
async def test_anilist_client_search_anime():
    client = AniListClient()
    mock_data = {
        "data": {
            "Media": {
                "id": 16498,
                "idMal": 16498,
                "title": {
                    "romaji": "Shingeki no Kyojin",
                    "english": "Attack on Titan",
                    "native": "進撃の巨人",
                    "userPreferred": "Attack on Titan"
                },
                "format": "TV",
                "episodes": 25,
                "status": "FINISHED",
                "seasonYear": 2013,
                "coverImage": {"large": "https://s4.anilist.co/cover.png"}
            }
        }
    }

    with patch.object(client, "execute_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_data
        res = await client.search_anime("Attack on Titan", year=2013)
        assert res is not None
        assert res["id"] == 16498
        assert res["idMal"] == 16498
        assert res["title_preferred"] == "Attack on Titan"


@pytest.mark.asyncio
async def test_anilist_client_update_progress_and_rating():
    client = AniListClient(access_token="valid_token")

    # 1. Update Progress
    mock_progress_data = {
        "data": {
            "SaveMediaListEntry": {
                "id": 8888,
                "mediaId": 16498,
                "status": "CURRENT",
                "progress": 5,
            }
        }
    }
    with patch.object(client, "execute_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_progress_data
        res = await client.update_progress(media_id=16498, episode=5)
        assert res["status"] == "success"
        assert res["data"]["progress"] == 5

        # 2. Update Rating (10-point scale)
        mock_rating_data = {
            "data": {
                "SaveMediaListEntry": {
                    "id": 8888,
                    "mediaId": 16498,
                    "score": 9.0
                }
            }
        }
        mock_query.return_value = mock_rating_data
        res_r = await client.update_rating(media_id=16498, rating=9.0)
        assert res_r["status"] == "success"
        assert res_r["data"]["score"] == 9.0


def test_mal_client_auth_and_persistence(tmp_path):
    token_file = tmp_path / "mal_tokens.json"
    client = MyAnimeListClient(client_id="mal_cid", client_secret="mal_sec", tokens_file=token_file)

    assert not client.is_authenticated()
    client.save_tokens({"access_token": "mal_token_xyz", "user_name": "L_Lawliet", "user_id": 777})
    assert client.is_authenticated()
    assert client.user_name == "L_Lawliet"
    assert token_file.exists()

    # Re-instantiate
    client2 = MyAnimeListClient(tokens_file=token_file)
    assert client2.is_authenticated()
    assert client2.user_name == "L_Lawliet"

    client2.delete_tokens()
    assert not client2.is_authenticated()
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_mal_client_check_connection_and_search():
    client = MyAnimeListClient(access_token="test_mal_tok")

    # 1. Connection check
    mock_user_resp = MagicMock()
    mock_user_resp.status_code = 200
    mock_user_resp.json.return_value = {
        "id": 777,
        "name": "LightYagami",
        "picture": "https://myanimelist.net/avatar.jpg"
    }

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "data": [
            {
                "node": {
                    "id": 16498,
                    "title": "Shingeki no Kyojin",
                    "num_episodes": 25,
                }
            }
        ]
    }

    mock_http = AsyncMock()
    mock_http.get.side_effect = [mock_user_resp, mock_search_resp]

    with patch.object(client, "get_client", return_value=mock_http):
        conn = await client.check_connection()
        assert conn["status"] == "connected"
        assert conn["user"] == "LightYagami"

        results = await client.search_anime("Attack on Titan")
        assert results is not None
        assert results["id"] == 16498
        assert results["title"] == "Shingeki no Kyojin"


@pytest.mark.asyncio
async def test_mal_client_update_progress_and_rating():
    client = MyAnimeListClient(access_token="valid_mal_tok")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "watching",
        "score": 9,
        "num_episodes_watched": 4,
    }

    mock_http = AsyncMock()
    mock_http.patch.return_value = mock_resp

    with patch.object(client, "get_client", return_value=mock_http):
        # Progress
        res = await client.update_progress(anime_id=16498, episode=4)
        assert res["status"] == "success"
        assert res["data"]["num_episodes_watched"] == 4

        # Rating
        res_r = await client.update_rating(anime_id=16498, rating=9)
        assert res_r["status"] == "success"
        assert res_r["data"]["score"] == 9


def test_anime_resolver_title_normalization():
    resolver = AnimeResolver()

    # Brackets and parentheses cleaning
    clean1 = resolver.clean_title("[SubsPlease] Sousou no Frieren [1080p]")
    assert clean1 == "Sousou no Frieren"

    clean2 = resolver.clean_title("Attack on Titan (2024) (TV)")
    assert clean2 == "Attack on Titan"

    clean3 = resolver.clean_title("[Erai-raws] Jujutsu Kaisen [Dual Audio]")
    assert clean3 == "Jujutsu Kaisen"


@pytest.mark.asyncio
async def test_anime_resolver_direct_ids(tmp_path):
    mock_ani = MagicMock()
    mock_ani.get_media_by_id = AsyncMock(return_value={
        "id": 16498,
        "idMal": 16498,
        "title_preferred": "Attack on Titan",
        "format": "TV",
        "episodes": 25,
    })
    resolver = AnimeResolver(anilist_client=mock_ani, cache_file=tmp_path / "direct_cache.json")
    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="To You, in 2000 Years",
        show_title="Shingeki no Kyojin",
        season=1,
        episode=1,
        progress=100.0,
        ids={"anilist": 16498, "mal": 16498}
    )

    info = await resolver.resolve(media)
    assert info is not None
    assert info["is_anime"] is True
    assert info["anilist_id"] == 16498
    assert info["mal_id"] == 16498
    assert info["source"] == "direct_id"


@pytest.mark.asyncio
async def test_anime_resolver_caching_and_negative_cache(tmp_path):
    cache_file = tmp_path / "anime_cache.json"
    mock_anilist = MagicMock()
    resolver = AnimeResolver(anilist_client=mock_anilist, cache_file=cache_file)

    # 1. Non-anime show (negative caching)
    media_non_anime = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        progress=100.0,
    )
    mock_anilist.search_anime = AsyncMock(return_value=None)

    info_non = await resolver.resolve(media_non_anime)
    assert info_non is None
    assert mock_anilist.search_anime.call_count == 1
    assert cache_file.exists()

    # Second check should hit negative disk cache without calling search_anime
    mock_anilist.search_anime.reset_mock()
    info_non_cached = await resolver.resolve(media_non_anime)
    assert info_non_cached is None
    assert mock_anilist.search_anime.call_count == 0

    # 2. Anime show (positive caching)
    media_anime = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="The End of the Journey",
        show_title="Frieren: Beyond Journey's End",
        season=1,
        episode=1,
        progress=100.0,
    )
    mock_anilist.search_anime = AsyncMock(return_value={
        "id": 154587,
        "idMal": 52991,
        "title_preferred": "Frieren: Beyond Journey's End",
        "format": "TV",
        "episodes": 28,
    })

    info_anime = await resolver.resolve(media_anime)
    assert info_anime is not None
    assert info_anime["is_anime"] is True
    assert info_anime["anilist_id"] == 154587
    assert info_anime["mal_id"] == 52991
    assert mock_anilist.search_anime.call_count == 1

    # Second check hits memory/disk cache
    mock_anilist.search_anime.reset_mock()
    info_anime_cached = await resolver.resolve(media_anime)
    assert info_anime_cached is not None
    assert info_anime_cached["is_anime"] is True
    assert info_anime_cached["anilist_id"] == 154587
    assert mock_anilist.search_anime.call_count == 0


@pytest.mark.asyncio
async def test_multi_tracker_4way_dispatch():
    mock_trakt = MagicMock()
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.scrobble_start = AsyncMock(return_value={"action": "start"})
    mock_trakt.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})
    mock_trakt.sync_ratings = AsyncMock(return_value={"added": {"episodes": 1}})

    mock_simkl = MagicMock()
    mock_simkl.is_enabled.return_value = True
    mock_simkl.is_authenticated.return_value = True
    mock_simkl.scrobble_start = AsyncMock(return_value={"result": "OK"})
    mock_simkl.scrobble_stop = AsyncMock(return_value={"result": "OK"})
    mock_simkl.sync_ratings = AsyncMock(return_value={"result": "OK"})

    mock_anilist = MagicMock()
    mock_anilist.is_enabled.return_value = True
    mock_anilist.is_authenticated.return_value = True
    mock_anilist.update_progress = AsyncMock(return_value={"status": "success"})
    mock_anilist.update_rating = AsyncMock(return_value={"status": "success"})

    mock_mal = MagicMock()
    mock_mal.is_enabled.return_value = True
    mock_mal.is_authenticated.return_value = True
    mock_mal.update_progress = AsyncMock(return_value={"status": "success"})
    mock_mal.update_rating = AsyncMock(return_value={"status": "success"})

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(return_value={
        "is_anime": True,
        "anilist_id": 16498,
        "mal_id": 16498,
        "episode_number": 1,
        "title": "Attack on Titan"
    })

    manager = MultiTrackerManager(
        simkl_client=mock_simkl,
        anilist_client=mock_anilist,
        mal_client=mock_mal,
        anime_resolver=mock_resolver,
    )

    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="To You, in 2000 Years",
        show_title="Attack on Titan",
        season=1,
        episode=1,
        progress=100.0,
    )

    # 1. 100% Scrobble dispatch (hits all 4 platforms)
    results = await manager.dispatch_scrobble(
        action="stop",
        media=media,
        trakt_client=mock_trakt,
        progress=100.0,
    )
    assert results["trakt"] is not None
    assert results["simkl"] is not None
    assert results["anilist"] is not None
    assert results["myanimelist"] is not None
    assert mock_trakt.scrobble_stop.called
    assert mock_simkl.scrobble_stop.called
    assert mock_anilist.update_progress.called
    assert mock_mal.update_progress.called

    # 2. Start playback dispatch: Trakt & Simkl get start, AniList & MAL are skipped (progress < threshold)
    mock_anilist.update_progress.reset_mock()
    mock_mal.update_progress.reset_mock()
    media_start = media.model_copy(update={"event": "media.play", "progress": 5.0})
    start_results = await manager.dispatch_scrobble(
        action="start",
        media=media_start,
        trakt_client=mock_trakt,
        progress=5.0,
    )
    assert mock_trakt.scrobble_start.called
    assert mock_simkl.scrobble_start.called
    assert start_results["anilist"] is None
    assert start_results["myanimelist"] is None
    assert not mock_anilist.update_progress.called
    assert not mock_mal.update_progress.called

    # 3. Rating dispatch (hits all 4 platforms)
    rating_results = await manager.dispatch_rating(
        media=media,
        rating=10,
        trakt_client=mock_trakt,
    )
    assert rating_results["trakt"] is not None
    assert rating_results["simkl"] is not None
    assert rating_results["anilist"] is not None
    assert rating_results["myanimelist"] is not None
    assert mock_anilist.update_rating.called
    assert mock_mal.update_rating.called


@pytest.mark.asyncio
async def test_multi_tracker_non_anime_skips_anime_trackers():
    mock_trakt = MagicMock()
    mock_trakt.is_authenticated.return_value = True
    mock_trakt.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})

    mock_simkl = MagicMock()
    mock_simkl.is_enabled.return_value = True
    mock_simkl.is_authenticated.return_value = True
    mock_simkl.scrobble_stop = AsyncMock(return_value={"result": "OK"})

    mock_anilist = MagicMock()
    mock_anilist.is_enabled.return_value = True
    mock_anilist.is_authenticated.return_value = True
    mock_anilist.update_progress = AsyncMock()

    mock_mal = MagicMock()
    mock_mal.is_enabled.return_value = True
    mock_mal.is_authenticated.return_value = True
    mock_mal.update_progress = AsyncMock()

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(return_value=None)

    manager = MultiTrackerManager(
        simkl_client=mock_simkl,
        anilist_client=mock_anilist,
        mal_client=mock_mal,
        anime_resolver=mock_resolver,
    )

    media_movie = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        progress=100.0,
    )

    results = await manager.dispatch_scrobble(
        action="stop",
        media=media_movie,
        trakt_client=mock_trakt,
        progress=100.0,
    )
    assert results["trakt"] is not None
    assert results["simkl"] is not None
    assert results["anilist"] is None
    assert results["myanimelist"] is None
    assert not mock_anilist.update_progress.called
    assert not mock_mal.update_progress.called


def test_anime_fastapi_endpoints():
    client = TestClient(app)

    # 1. AniList Status Demo
    res_ani_demo = client.get("/api/anilist/status?demo=true")
    assert res_ani_demo.status_code == 200
    assert res_ani_demo.json()["authenticated"] is True
    assert res_ani_demo.json()["user"] == "demo_otaku"

    # 2. MAL Status Demo
    res_mal_demo = client.get("/api/mal/status?demo=true")
    assert res_mal_demo.status_code == 200
    assert res_mal_demo.json()["authenticated"] is True
    assert res_mal_demo.json()["user"] == "demo_otaku"

    # 3. /api/anime/resolve diagnostic endpoint
    res_resolve = client.get("/api/anime/resolve?title=Sousou%20no%20Frieren&year=2023")
    assert res_resolve.status_code == 200
    data = res_resolve.json()
    assert "is_anime" in data
    assert "title" in data
    assert "resolved" in data

    # 4. Token submission admin authentication
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "admintoken123"

        # Unauthorized
        client.cookies.clear()
        res_unauth = client.post("/api/anilist/token", json={"token": "some_token"})
        assert res_unauth.status_code == 401

        res_mal_unauth = client.post("/api/mal/token", json={"token": "some_token"})
        assert res_mal_unauth.status_code == 401

        # Authorized with invalid token should fail connection
        client.cookies.set("admin_token", "admintoken123")
        with patch.object(anilist, "check_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = {"status": "error", "error": "Invalid token"}
            res_bad = client.post("/api/anilist/token", json={"token": "invalid_tok"})
            assert res_bad.status_code == 400

        with patch.object(mal, "check_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = {"status": "error", "error": "Invalid token"}
            res_bad_mal = client.post("/api/mal/token", json={"token": "invalid_tok"})
            assert res_bad_mal.status_code == 400

        # Authorized with valid token
        with patch.object(anilist, "check_connection", new_callable=AsyncMock) as mock_conn, \
             patch.object(anilist, "save_tokens") as mock_save:
            mock_conn.return_value = {"status": "connected", "user": "ShinjiIkari", "id": 1}
            res_good = client.post("/api/anilist/token", json={"token": "good_token"})
            assert res_good.status_code == 200
            assert res_good.json()["user"] == "ShinjiIkari"
            assert mock_save.called

        # Disconnect endpoints
        with patch.object(anilist, "delete_tokens") as mock_del:
            res_disc = client.post("/api/anilist/disconnect")
            assert res_disc.status_code == 200
            assert mock_del.called

        with patch.object(mal, "delete_tokens") as mock_del:
            res_disc_mal = client.post("/api/mal/disconnect")
            assert res_disc_mal.status_code == 200
            assert mock_del.called

        # Auth portal pages
        client.cookies.clear()
        assert client.get("/auth/anilist").status_code == 401
        assert client.get("/auth/mal").status_code == 401

        client.cookies.set("admin_token", "admintoken123")
        res_page_ani = client.get("/auth/anilist")
        assert res_page_ani.status_code == 200
        assert "AniList Anime Tracker" in res_page_ani.text

        res_page_mal = client.get("/auth/mal")
        assert res_page_mal.status_code == 200
        assert "MyAnimeList (MAL) Integration" in res_page_mal.text

    finally:
        Config.WEBHOOK_SECRET = orig_secret
        anilist.access_token = None
        anilist.user_name = None
        anilist.user_id = None
        mal.access_token = None
        mal.user_name = None
        mal.user_id = None
        client.cookies.clear()


def test_dashboard_renders_anime_tracking_card_and_modals():
    """Verify that the dashboard template renders the Anime card, modal dialogs, and triggers."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    assert "Anime Tracking Engine" in html
    assert "anilist-modal" in html
    assert "mal-modal" in html
    assert "openAnilistModal" in html
    assert "openMalModal" in html
    assert "submitAnilistToken" in html
    assert "submitMalToken" in html


def test_settings_manager_lifecycle(tmp_path):
    """Verify SettingsManager handles persistence, defaults, and toggles."""
    from app.services.settings_manager import SettingsManager

    settings_file = tmp_path / "settings.json"
    clean_data_dir = tmp_path / "clean_data"
    clean_data_dir.mkdir()
    mgr = SettingsManager(settings_file=settings_file, data_dir=clean_data_dir)

    # Defaults: servers disabled by default out of the box for fresh install
    assert mgr.is_server_enabled("plex") is False
    assert mgr.is_server_enabled("jellyfin") is False
    assert mgr.is_server_enabled("emby") is False
    # Trackers enabled by default
    assert mgr.is_tracker_enabled("trakt") is True
    assert mgr.is_tracker_enabled("simkl") is True
    assert mgr.is_tracker_enabled("anilist") is True
    assert mgr.is_tracker_enabled("mal") is True

    # Upgrade scenario: existing install with previous data preserves Plex enabled, Jellyfin/Emby disabled
    upgrade_data_dir = tmp_path / "upgrade_data"
    upgrade_data_dir.mkdir()
    (upgrade_data_dir / "cowatch_shows.json").write_text("[]")
    upgrade_mgr = SettingsManager(settings_file=tmp_path / "upgrade_settings.json", data_dir=upgrade_data_dir)
    assert upgrade_mgr.is_server_enabled("plex") is True
    assert upgrade_mgr.is_server_enabled("jellyfin") is False
    assert upgrade_mgr.is_server_enabled("emby") is False

    # Toggle server on
    mgr.set_server_enabled("plex", True)
    assert mgr.is_server_enabled("plex") is True
    assert mgr.is_server_enabled("jellyfin") is False

    # Toggle tracker off
    mgr.set_tracker_enabled("simkl", False)
    assert mgr.is_tracker_enabled("simkl") is False
    assert mgr.is_tracker_enabled("trakt") is True

    # Verify persistence
    mgr2 = SettingsManager(settings_file=settings_file, data_dir=clean_data_dir)
    assert mgr2.is_server_enabled("plex") is True
    assert mgr2.is_server_enabled("jellyfin") is False
    assert mgr2.is_tracker_enabled("simkl") is False
    assert mgr2.is_tracker_enabled("trakt") is True


def test_settings_api_and_toggle_endpoint():
    """Verify GET /api/settings and POST /api/settings/toggle endpoints."""
    from app.services.settings_manager import settings_mgr

    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "secret123"
        client = TestClient(app)

        # GET is public status (returns 200)
        res_get = client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()
        assert "settings" in data
        assert "servers" in data["settings"]
        assert "trackers" in data["settings"]

        # POST /api/settings/toggle unauthorized -> 401
        res_unauth = client.post("/api/settings/toggle", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": False
        })
        assert res_unauth.status_code == 401

        # Toggle via POST authorized
        res_toggle = client.post("/api/settings/toggle?token=secret123", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": False
        })
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False
        assert settings_mgr.is_server_enabled("jellyfin") is False

        # Reset back
        res_reset = client.post("/api/settings/toggle?token=secret123", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": True
        })
        assert res_reset.status_code == 200
        assert settings_mgr.is_server_enabled("jellyfin") is True
    finally:
        Config.WEBHOOK_SECRET = orig_secret


def test_webhook_ingestion_paused_bypass():
    """Verify incoming webhooks are paused/bypassed when server ingestion is disabled in settings."""
    from app.services.settings_manager import settings_mgr

    client = TestClient(app)
    try:
        settings_mgr.set_server_enabled("plex", False)
        settings_mgr.set_server_enabled("jellyfin", False)
        settings_mgr.set_server_enabled("emby", False)

        # Plex webhook
        res_plex = client.post("/webhook", data={"payload": "{}"})
        assert res_plex.status_code == 200
        assert res_plex.json()["status"] == "ignored"
        assert "Plex ingestion is paused" in res_plex.json()["reason"]

        # Jellyfin webhook
        res_jf = client.post("/webhook/jellyfin", json={"NotificationType": "PlaybackStart"})
        assert res_jf.status_code == 200
        assert res_jf.json()["status"] == "ignored"
        assert "Jellyfin ingestion is paused" in res_jf.json()["reason"]

        # Emby webhook
        res_emby = client.post("/webhook/emby", data={"data": "{}"})
        assert res_emby.status_code == 200
        assert res_emby.json()["status"] == "ignored"
        assert "Emby ingestion is paused" in res_emby.json()["reason"]
    finally:
        settings_mgr.set_server_enabled("plex", True)
        settings_mgr.set_server_enabled("jellyfin", True)
        settings_mgr.set_server_enabled("emby", True)


def test_api_history_remove_endpoint():
    """Verify POST /api/history/remove unscrobbles items across trackers."""
    client = TestClient(app)
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "admintoken"

        # Unauthorized
        res_unauth = client.post("/api/history/remove", json={
            "media_type": "movie", "title": "Dune", "year": 2021
        })
        assert res_unauth.status_code == 401

        # Authorized call with mock
        with patch("app.main.multi_tracker.dispatch_unscrobble", new_callable=AsyncMock) as mock_unscrobble:
            mock_unscrobble.return_value = {
                "trakt": {"status": "removed"},
                "simkl": {"status": "removed"}
            }

            res_ok = client.post("/api/history/remove?token=admintoken", json={
                "media_type": "movie", "title": "Dune", "year": 2021,
                "trackers": ["trakt", "simkl"],
                "cowatch": True
            })
            assert res_ok.status_code == 200
            assert res_ok.json()["status"] == "success"
            assert "result" in res_ok.json()
            mock_unscrobble.assert_awaited_once()
    finally:
        Config.WEBHOOK_SECRET = orig_secret


@pytest.mark.asyncio
async def test_notifier_failure_alert_deduplication():
    """Verify Notifier sends failure alert and deduplicates subsequent identical alerts within TTL."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier = Notifier()
    notifier.config.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/test"
    notifier._failure_cache.clear()

    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        title="Severance S02E01",
        show_title="Severance",
        media_type="episode",
        season=2,
        episode=1
    )

    mock_resp = MagicMock(status_code=204)
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    # First alert -> sent
    sent1 = await notifier.send_failure_alert(media, "Simkl", "401 Unauthorized", "selits", client=mock_http)
    assert sent1 is True
    assert mock_http.post.call_count == 1

    # Second identical alert immediately -> deduplicated / skipped
    sent2 = await notifier.send_failure_alert(media, "Simkl", "401 Unauthorized", "selits", client=mock_http)
    assert sent2 is False
    assert mock_http.post.call_count == 1

    # Different failure -> sent
    sent3 = await notifier.send_failure_alert(media, "AniList", "500 Server Error", "selits", client=mock_http)
    assert sent3 is True
    assert mock_http.post.call_count == 2


def test_notifier_discord_payload_includes_cowatch_partner_and_trackers():
    """Verify Discord payload includes unmasked co-watch partner username and tracker list."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier = Notifier()
    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        title="Severance S02E04",
        show_title="Severance",
        media_type="episode",
        season=2,
        episode=4
    )

    payload = notifier.build_discord_payload(
        media,
        "scrobble",
        cowatch_partner="alice_partner",
        trackers=["Trakt", "Simkl"]
    )
    fields = payload["embeds"][0]["fields"]
    field_names = [f["name"] for f in fields]
    field_vals = [f["value"] for f in fields]

    assert "👥 Co-Watched With" in field_names
    idx = field_names.index("👥 Co-Watched With")
    assert "@alice_partner" in field_vals[idx]

    assert "📡 Synced Trackers" in field_names
    idx_trk = field_names.index("📡 Synced Trackers")
    assert "Trakt, Simkl" in field_vals[idx_trk]


def test_playback_activity_and_events_persistence(tmp_path, monkeypatch):
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
    client = TestClient(main_mod.app)
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "secret123"
        res = client.post("/api/events/clear?token=secret123")
        assert res.status_code == 200
        main_mod.reload_recent_events_in_place()
        assert len(main_mod.recent_events) == 0

        # Test stats reset endpoint
        res_reset = client.post("/api/stats/reset?token=secret123")
        assert res_reset.status_code == 200
        assert res_reset.json()["stats"]["total"] == 0
        cleared_stats = main_mod.load_scrobble_stats()
        assert cleared_stats["total"] == 0
    finally:
        Config.WEBHOOK_SECRET = orig_secret


def test_quick_scrobble_modal_defaults_and_simkl_button():
    """Verify that the quick scrobble modal only defaults to configured trackers and co-watch is unchecked."""
    client = TestClient(app)

    # 1. Test live dashboard (unauthenticated trackers by default in mock env)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Co-watch partner must be unchecked by default
    assert '<input type="checkbox" id="scrobble-cowatch-check">' in html
    assert '<input type="checkbox" id="scrobble-cowatch-check" checked>' not in html

    # Reset JS logic must be present in openManualScrobbleModal
    assert "cowatchChk.checked = false" in html
    assert "'scrobble-trk-trakt':" in html

    # 2. Test demo dashboard (all trackers active in demo)
    resp_demo = client.get("/demo")
    assert resp_demo.status_code == 200
    demo_html = resp_demo.text

    # Co-watch partner must be unchecked by default even in demo
    assert '<input type="checkbox" id="scrobble-cowatch-check">' in demo_html
    assert '<input type="checkbox" id="scrobble-cowatch-check" checked>' not in demo_html

    # Configured trackers are checked in demo
    assert '<input type="checkbox" id="scrobble-trk-trakt" checked>' in demo_html
    assert '<input type="checkbox" id="scrobble-trk-simkl" checked>' in demo_html
    assert '<input type="checkbox" id="scrobble-trk-anilist" checked>' in demo_html
    assert '<input type="checkbox" id="scrobble-trk-mal" checked>' in demo_html

    # Simkl card renders Quick Scrobble button
    assert "🍿 Quick Scrobble" in demo_html
    assert 'onclick="openManualScrobbleModal()"' in demo_html


def test_settings_hub_endpoints_and_masking(tmp_path, monkeypatch):
    """Verify GET and POST /api/settings with credential masking, auth protection, and live reload."""
    from app.services.settings_manager import settings_mgr
    from app.main import simkl, arr_bridge
    test_settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mgr, "settings_file", test_settings_file)
    settings_mgr._settings = {
        "servers": {"plex": True, "jellyfin": False, "emby": False},
        "trackers": {"trakt": True, "simkl": False, "anilist": False, "mal": False},
        "reconciliation": settings_mgr._detect_default_reconciliation(),
        "credentials": settings_mgr._detect_default_credentials(),
        "arr": settings_mgr._detect_default_arr(),
    }
    settings_mgr._save_settings()

    client = TestClient(app)

    # 1. GET /api/settings returns masked settings
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "servers" in data
    assert "trackers" in data
    assert "credentials" in data
    assert "reconciliation" in data
    assert "arr" in data

    # 2. Auth Protection: Require admin when secret configured
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "supersecret123")
    unauth_res = client.post("/api/settings", json={"servers": {"jellyfin": True}})
    assert unauth_res.status_code == 401
    assert "Unauthorized" in unauth_res.json()["detail"]

    # 3. Successful update as Admin
    client.cookies.set("admin_token", "supersecret123")

    payload = {
        "servers": {"jellyfin": True},
        "trackers": {"simkl": True},
        "credentials": {
            "simkl": {
                "client_id": "test_simkl_client_id_999",
                "client_secret": "simkl_secret_raw_pass_8888",
            }
        },
        "reconciliation": {
            "server_type": "plex",
            "plex_url": "http://127.0.0.1:32400",
            "plex_token": "my_super_secret_plex_token_1111",
        },
        "arr": {
            "sonarr_url": "http://127.0.0.1:8989",
            "sonarr_api_key": "sonarr_key_raw_2222",
            "radarr_url": "http://127.0.0.1:7878",
            "radarr_api_key": "radarr_key_raw_3333",
            "auto_add_watchlist": True,
            "search_on_add": True,
        },
    }
    update_res = client.post("/api/settings", json=payload)
    assert update_res.status_code == 200
    up_data = update_res.json()
    assert up_data["status"] == "success"
    assert up_data["settings"]["servers"]["jellyfin"] is True
    assert up_data["settings"]["trackers"]["simkl"] is True

    # Verify live in-memory reload
    assert simkl.effective_client_id == "test_simkl_client_id_999"
    assert arr_bridge.sonarr.base_url == "http://127.0.0.1:8989"
    assert arr_bridge.sonarr.api_key == "sonarr_key_raw_2222"
    assert arr_bridge.radarr.base_url == "http://127.0.0.1:7878"
    assert arr_bridge.radarr.api_key == "radarr_key_raw_3333"

    # 4. Mask preservation test: Submitting masked string does NOT overwrite real secret
    mask_payload = {
        "credentials": {
            "simkl": {
                "client_id": "test_simkl_client_id_999",
                "client_secret": "••••••••8888",
            }
        },
        "arr": {
            "sonarr_api_key": "••••••••2222",
        },
    }
    mask_res = client.post("/api/settings", json=mask_payload)
    assert mask_res.status_code == 200

    # Unmasked check in settings manager
    unmasked_simkl = settings_mgr.get_tracker_credentials("simkl", mask=False)
    assert unmasked_simkl["client_secret"] == "simkl_secret_raw_pass_8888"

    unmasked_arr = settings_mgr.get_arr_settings(mask=False)
    assert unmasked_arr["sonarr_api_key"] == "sonarr_key_raw_2222"


def test_arr_test_connection_endpoint(monkeypatch):
    """Verify POST /api/arr/test-connection for Sonarr and Radarr with demo and mocked connectivity."""
    from app.clients.sonarr_client import SonarrClient
    from app.clients.radarr_client import RadarrClient
    client = TestClient(app)

    # 1. Demo mode returns simulated success
    demo_res = client.post("/api/arr/test-connection?demo=true", json={"app": "sonarr"})
    assert demo_res.status_code == 200
    assert demo_res.json()["status"] == "connected"
    assert demo_res.json()["app"] == "sonarr"

    demo_radarr = client.post("/api/arr/test-connection?demo=true", json={"app": "radarr"})
    assert demo_radarr.status_code == 200
    assert demo_radarr.json()["status"] == "connected"
    assert demo_radarr.json()["app"] == "radarr"

    # 2. Auth protection
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "pwd12345")
    unauth_res = client.post("/api/arr/test-connection", json={"app": "sonarr"})
    assert unauth_res.status_code == 401

    # 3. As admin with mock
    client.cookies.set("admin_token", "pwd12345")

    with patch.object(SonarrClient, "check_connection", new_callable=AsyncMock) as mock_sonarr:
        mock_sonarr.return_value = {"status": "connected", "version": "4.0.9"}
        s_res = client.post("/api/arr/test-connection", json={"app": "sonarr", "url": "http://127.0.0.1:8989", "api_key": "testkey"})
        assert s_res.status_code == 200
        assert s_res.json()["status"] == "connected"

    with patch.object(RadarrClient, "check_connection", new_callable=AsyncMock) as mock_radarr:
        mock_radarr.return_value = {"status": "connected", "version": "5.9.1"}
        r_res = client.post("/api/arr/test-connection", json={"app": "radarr", "url": "http://127.0.0.1:7878", "api_key": "testkey"})
        assert r_res.status_code == 200
        assert r_res.json()["status"] == "connected"

    # 4. Invalid app type returns 400
    bad_res = client.post("/api/arr/test-connection", json={"app": "lidarr"})
    assert bad_res.status_code == 400
    assert "Invalid app" in bad_res.json()["detail"]


def test_settings_modal_dashboard_rendering():
    """Verify that #settings-modal, header button, and contextual deep links render on dashboard."""
    client = TestClient(app)

    # 1. Main dashboard renders #settings-modal and Settings Hub header button
    res = client.get("/")
    assert res.status_code == 200
    html_content = res.text

    assert 'id="settings-modal"' in html_content
    assert "Settings Hub" in html_content
    assert "{{SETTINGS_HEADER_BTN}}" not in html_content
    assert '<button onclick="openSettingsModal()"' in html_content
    assert ", #settings-modal {" in html_content
    assert "openSettingsModal" in html_content
    assert "saveAllSettingsFromModal" in html_content
    assert "testSettingsArrConnection" in html_content

    # Contextual deep link calls
    assert 'openSettingsModal(\'servers\')' in html_content or "openSettingsModal('servers')" in html_content
    assert "openSettingsModal('trackers', 'simkl')" in html_content
    assert "openSettingsModal('trackers', 'anilist')" in html_content
    assert "openSettingsModal('automation')" in html_content

    # 2. Demo dashboard also includes Settings Hub
    demo_res = client.get("/demo")
    assert demo_res.status_code == 200
    assert 'id="settings-modal"' in demo_res.text
    assert "Settings Hub" in demo_res.text
    assert "{{SETTINGS_HEADER_BTN}}" not in demo_res.text
    assert '<button onclick="openSettingsModal()"' in demo_res.text


def test_activity_table_show_cowatch_alignment():
    """Verify that events with media_type=='show' resolve show_title, detect co-watching, and render aligned action buttons."""
    from app.services.cowatch_manager import cowatch_mgr
    from app.main import recent_events, log_event
    from app.plex_parser import ParsedMedia

    # Ensure Ted Lasso is in co-watch shows
    cowatch_mgr.add_show("Ted Lasso")

    # 1. Log a show event
    parsed = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="show",
        title="Ted Lasso (2020)",
        year=2020,
        show_title="Ted Lasso (2020)",
        progress=100.0,
    )
    log_event(parsed, "scrobble", {"status": "ok"})

    # 2. Check recent_events entry
    latest = recent_events[0]
    assert latest["type"] == "show"
    assert latest["show_title"] in ("Ted Lasso (2020)", "Ted Lasso")

    client = TestClient(app)

    # 3. Test /api/events endpoint calculates is_cowatch_show=True
    res = client.get("/api/events")
    assert res.status_code == 200
    events = res.json()["events"]
    show_ev = next((e for e in events if e.get("type") == "show"), None)
    assert show_ev is not None
    assert show_ev.get("is_cowatch_show") is True

    # 4. Test SSR dashboard renders clean flex layout without inert badge or redundant + Co-Watch button for whitelisted show
    client.cookies.set("admin_token", "unlocked")
    dash_res = client.get("/")
    assert dash_res.status_code == 200
    html = dash_res.text
    assert "✓ Co-Watching" not in html
    assert 'data-show="Ted%20Lasso%20%282020%29"' not in html
    assert '<div style="display:inline-flex;flex-wrap:nowrap;gap:6px;align-items:center;">' in html


def test_cache_control_headers_and_sw_invalidation():
    """Verify that dashboard, API endpoints, and sw.js have no-cache headers and sw.js is dynamically versioned."""
    from app.main import APP_VERSION
    client = TestClient(app)

    # 1. Root dashboard returns no-cache headers
    dash_res = client.get("/")
    assert dash_res.status_code == 200
    cache_ctrl = dash_res.headers.get("cache-control", "")
    assert "no-cache" in cache_ctrl
    assert "no-store" in cache_ctrl

    # 2. Service worker script returns no-cache headers and dynamic versioning
    sw_res = client.get("/sw.js")
    assert sw_res.status_code == 200
    sw_cache = sw_res.headers.get("cache-control", "")
    assert "no-cache" in sw_cache
    assert f"omniscrobble-v{APP_VERSION}" in sw_res.text
    # Verify '/' is not cached in STATIC_ASSETS
    static_assets_block = sw_res.text.split("STATIC_ASSETS = [")[1].split("]")[0]
    assert "'/'" not in static_assets_block and '"/"' not in static_assets_block

    # 3. Dynamic API endpoint returns no-cache headers
    api_res = client.get("/api/events")
    assert api_res.status_code == 200
    assert "no-cache" in api_res.headers.get("cache-control", "")


def test_http_security_headers():
    """Verify security headers are present on dashboard and API responses."""
    client = TestClient(app)
    for path in ["/", "/api/events", "/sw.js"]:
        res = client.get(path)
        assert res.headers.get("x-frame-options") == "SAMEORIGIN"
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_admin_unlock_rate_limiting():
    """Verify admin unlock enforces rate limiting (429) after multiple failed attempts."""
    from app.main import _failed_unlock_attempts
    _failed_unlock_attempts.clear()

    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", "correct_secret_phrase"):
        # First 5 failed attempts return 401
        for i in range(5):
            res = client.post("/api/admin/unlock", json={"token": f"wrong_{i}"})
            assert res.status_code == 401
            assert "Invalid admin secret" in res.json().get("detail", "")

        # 6th attempt triggers 429 rate limit
        res_blocked = client.post("/api/admin/unlock", json={"token": "wrong_6"})
        assert res_blocked.status_code == 429
        assert "Too many failed unlock attempts" in res_blocked.json().get("detail", "")
        assert "Retry-After" in res_blocked.headers

    _failed_unlock_attempts.clear()


def test_ssr_html_escaping():
    """Verify stored strings in SSR events table are safely HTML-escaped to prevent Stored XSS."""
    from app.main import log_event, ParsedMedia
    client = TestClient(app)

    malicious_title = "<script>alert('xss_title')</script>"
    malicious_user = "<img src=x onerror=alert('xss_user')>"

    parsed = ParsedMedia(
        event="media.scrobble",
        media_type="movie",
        title=malicious_title,
        year=2024,
        username=malicious_user,
        progress=100.0,
    )
    log_event(
        media=parsed,
        action="scrobble",
        result={"status": "ok"},
    )

    res = client.get("/")
    assert res.status_code == 200
    assert "<script>alert('xss_title')</script>" not in res.text
    assert "<img src=x onerror=alert('xss_user')>" not in res.text
    assert "&lt;script&gt;alert(&#x27;xss_title&#x27;)&lt;/script&gt;" in res.text or "&lt;script&gt;alert('xss_title')&lt;/script&gt;" in res.text


def test_connection_endpoint_url_validation():
    """Verify test-connection endpoints reject non-http/https URL schemes."""
    client = TestClient(app)
    with patch.object(Config, "WEBHOOK_SECRET", "super_admin_pass"):
        client.cookies.set("admin_token", "super_admin_pass")

        # Test sync connection with invalid scheme
        res_sync = client.post("/api/sync/test-connection", json={"server": "plex", "url": "file:///etc/passwd", "token": "tok"})
        assert res_sync.status_code == 400
        assert "must start with http:// or https://" in res_sync.json().get("detail", "")

        res_jf = client.post("/api/sync/test-connection", json={"server": "jellyfin", "url": "gopher://127.0.0.1", "token": "tok"})
        assert res_jf.status_code == 400
        assert "must start with http:// or https://" in res_jf.json().get("detail", "")

        # Test Arr connection with invalid scheme
        res_sonarr = client.post("/api/arr/test-connection", json={"app": "sonarr", "url": "ftp://malicious.host", "api_key": "key"})
        assert res_sonarr.status_code == 400
        assert "must start with http:// or https://" in res_sonarr.json().get("detail", "")

        res_radarr = client.post("/api/arr/test-connection", json={"app": "radarr", "url": "data:text/html,boom", "api_key": "key"})
        assert res_radarr.status_code == 400
        assert "must start with http:// or https://" in res_radarr.json().get("detail", "")


def test_api_events_pagination_and_dashboard_controls():
    client = TestClient(app)
    recent_events.clear()

    # Seed 25 test events
    for i in range(25):
        recent_events.appendleft({
            "timestamp": f"2026-10-02 12:{i:02d}:00",
            "user": "selits",
            "server": "plex",
            "event": "media.scrobble",
            "action": "scrobble",
            "title": f"Show S01E{i+1:02d}",
            "type": "episode",
            "show_title": "Show",
            "media_payload": {"media_type": "episode", "title": f"Episode {i+1}", "season": 1, "episode": i + 1},
            "progress": "100.0%",
            "result_status": "ok",
            "raw_result": {"status": "ok"},
            "cowatch_status": None,
        })

    try:
        # 1. /api/events returns all 25 items and total
        res = client.get("/api/events")
        assert res.status_code == 200
        data = res.json()
        assert len(data["events"]) == 25
        assert data["total"] == 25

        # 2. /api/events with limit & offset
        res_page1 = client.get("/api/events?limit=10&offset=0")
        assert res_page1.status_code == 200
        data1 = res_page1.json()
        assert len(data1["events"]) == 10
        assert data1["total"] == 25
        assert data1["events"][0]["title"] == "Show S01E25"

        res_page2 = client.get("/api/events?limit=10&offset=10")
        assert res_page2.status_code == 200
        data2 = res_page2.json()
        assert len(data2["events"]) == 10
        assert data2["events"][0]["title"] == "Show S01E15"

        res_page3 = client.get("/api/events?limit=10&offset=20")
        assert res_page3.status_code == 200
        data3 = res_page3.json()
        assert len(data3["events"]) == 5

        # 3. /api/events in demo mode
        res_demo = client.get("/api/events?demo=true&limit=2&offset=0")
        assert res_demo.status_code == 200
        assert len(res_demo.json()["events"]) == 2
        assert res_demo.json()["total"] > 2

        # 4. Dashboard HTML includes pagination controls and info
        dash_res = client.get("/")
        assert dash_res.status_code == 200
        assert "events-pagination" in dash_res.text
        assert "events-page-info" in dash_res.text
        assert "events-page-size" in dash_res.text
        assert "events-prev-btn" in dash_res.text
        assert "events-next-btn" in dash_res.text
        assert "Showing 1–10 of 25 events" in dash_res.text
        assert "Page 1 of 3" in dash_res.text

        # 5. Config default verification
        assert Config.MAX_EVENT_HISTORY >= 10
    finally:
        recent_events.clear()


@pytest.mark.asyncio
async def test_activity_table_ui_polish():
    """Verify action label formatting, co-watch badge filtering, unhandled webhook skipping, and SSR styling."""
    from app.main import (
        format_action_label,
        should_display_cowatch_badge,
        render_status_badge,
        recent_events,
        log_event,
        process_media_event,
    )
    from app.plex_parser import ParsedMedia
    from app.config import Config

    # 1. Action label formatting
    assert format_action_label("scrobble_start") == "play"
    assert format_action_label("scrobble_pause") == "pause"
    assert format_action_label("scrobble_stop") == "scrobble"
    assert format_action_label("mark_watched") == "scrobble"
    assert format_action_label("playback_stopped") == "stop"
    assert format_action_label("test_webhook") == "test"
    assert format_action_label("none") == "ignored"
    assert format_action_label("custom_action") == "custom_action"

    # 2. Co-watch badge display eligibility
    # False on ignored / error / 0% progress
    assert should_display_cowatch_badge("scrobble_stop", "ignored", "100.0%") is False
    assert should_display_cowatch_badge("mark_watched", "error", "100.0%") is False
    assert should_display_cowatch_badge("scrobble_stop", "ok", "0.0%") is False
    # False on interim playback states
    assert should_display_cowatch_badge("scrobble_start", "ok", "10.0%") is False
    assert should_display_cowatch_badge("scrobble_pause", "ok", "45.0%") is False
    assert should_display_cowatch_badge("playback_stopped", "ok", "50.0%") is False
    # True on valid completed scrobbles
    assert should_display_cowatch_badge("scrobble_stop", "ok", "92.0%") is True
    assert should_display_cowatch_badge("mark_watched", "ok", "100.0%") is True
    assert should_display_cowatch_badge("scrobble", "200", "90.0%") is True

    # 3. Status badge rendering helper
    cw_synced = render_status_badge("mark_watched", "ok", "100.0%", {"synced": True, "target": "partner"})
    assert "👥 Co-Watched" in cw_synced
    assert "Synced to @partner" in cw_synced
    assert "ok" not in cw_synced.lower() or "ok" not in cw_synced  # No awkward 'ok' prepended

    cw_solo = render_status_badge("scrobble_stop", "ok", "95.0%", {"synced": False, "reason": "Not in whitelist"})
    assert "✓ Scrobbled" in cw_solo
    assert "Solo: Not in whitelist" in cw_solo
    assert "👥 Solo" not in cw_solo  # Replaced noisy badge with clean tooltip

    assert "✓ Added" in render_status_badge("collection", "ok")
    assert "✓ Rated" in render_status_badge("rate", "ok")
    assert "✓ OK" in render_status_badge("playback_stopped", "ok", "0.0%")
    assert "Ignored" in render_status_badge("scrobble_start", "ignored")
    assert "⏳ Queued" in render_status_badge("scrobble_stop", "queued")
    assert "✕ Failed" in render_status_badge("scrobble_stop", "error")

    # 4. Unhandled webhook events in scrobble mode do NOT log "none"
    parsed_unhandled = ParsedMedia(
        event="library.on.deck",
        username="selits",
        media_type="show",
        title="Unknown Deck Item",
        progress=0.0,
    )
    recent_events.clear()
    with patch.object(trakt, "is_authenticated", return_value=True):
        res = await process_media_event(parsed_unhandled, endpoint_name="webhook")
    assert res["status"] == "ignored"
    assert "library.on.deck" in res["reason"]
    # Verify nothing was added to recent_events with action "none"
    assert len([e for e in recent_events if e.get("action") == "none"]) == 0

    # 5. SSR Dashboard Table Verification
    client = TestClient(app)
    client.cookies.set("admin_token", "unlocked")

    # Log a 0% stopped event (should have clean ✓ OK badge, NOT Solo or raw ok)
    parsed_zero = ParsedMedia(
        event="media.stop",
        username="selits",
        media_type="movie",
        title="Quick Sample Test Movie",
        progress=0.0,
    )
    log_event(
        parsed_zero,
        "playback_stopped",
        {"status": "ok"},
        cowatch_status={"synced": False, "reason": "Not in shared co-watch list"},
    )

    # Log a 100% completed scrobble
    parsed_completed = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Finished Blockbuster Movie",
        progress=100.0,
    )
    log_event(
        parsed_completed,
        "mark_watched",
        {"status": "ok"},
        cowatch_status={"synced": True, "target": "partner", "reason": "Shared show whitelist match"},
    )

    # Log an unsynced 100% completed event (should still offer + Sync Partner)
    parsed_unsynced = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Solo Watched Indie Movie",
        progress=100.0,
    )
    log_event(
        parsed_unsynced,
        "mark_watched",
        {"status": "ok"},
        cowatch_status={"synced": False, "reason": "Not in shared co-watch list"},
    )

    dash_res = client.get("/")
    assert dash_res.status_code == 200
    html_text = dash_res.text

    # Header styling: renamed to clean universal "Status"
    assert '<th style="white-space:nowrap;">Status</th>' in html_text
    assert '<th style="min-width:220px;white-space:nowrap;">Actions</th>' in html_text

    # Verify Co-Watched badge has white-space:nowrap and NO awkward 'ok' prepended
    assert '👥 Co-Watched</span>' in html_text
    assert 'white-space:nowrap;" title="Synced to @partner' in html_text

    # Verify 0% stop gets clean ✓ OK badge and does NOT have Solo badge
    assert '✓ OK</span>' in html_text
    assert 'title="Co-watch skipped: Not in shared co-watch list"' not in html_text

    # Action buttons: inert ✓ Co-Watching is omitted completely
    assert '✓ Co-Watching' not in html_text

    # Action buttons: unsynced completed event gets + Sync Partner and Unscrobble
    assert '+ Sync Partner' in html_text
    assert '🗑️ Unscrobble' in html_text

    # Action buttons flex styling
    assert 'display:inline-flex;flex-wrap:nowrap;gap:6px;align-items:center;' in html_text


def test_settings_api_notifications():
    """Verify GET and POST /api/settings handles notifications configuration and masking."""
    from app.services.settings_manager import settings_mgr
    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        headers = {"x-webhook-secret": "supersecret"}

        # 1. GET settings includes notifications
        res = client.get("/api/settings", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "notifications" in data
        notifs = data["notifications"]
        assert "discord_webhook_url" in notifs
        assert "ntfy_auth_token" in notifs
        assert "notify_on_scrobble" in notifs
        assert "notify_on_rate" in notifs
        assert "notify_on_collection" in notifs
        assert "notify_on_failure" in notifs

        # 2. POST updates notifications
        new_payload = {
            "notifications": {
                "discord_webhook_url": "https://discord.com/api/webhooks/12345/secret_token",
                "telegram_bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
                "telegram_chat_id": "-100987654321",
                "ntfy_url": "https://ntfy.sh/my-secret-test-topic",
                "ntfy_auth_token": "secret_ntfy_tk_9999",
                "pushover_user_key": "user_key_9999",
                "pushover_api_token": "app_token_8888",
                "notify_on_scrobble": False,
                "notify_on_rate": True,
                "notify_on_collection": False,
                "notify_on_failure": True,
            }
        }
        res_post = client.post("/api/settings", headers=headers, json=new_payload)
        assert res_post.status_code == 200
        saved = res_post.json()["settings"]["notifications"]

        # Sensitive values should be masked in API responses
        assert "••••" in saved["discord_webhook_url"]
        assert "••••" in saved["telegram_bot_token"]
        assert "••••" in saved["ntfy_auth_token"]
        assert "••••" in saved["pushover_user_key"]
        assert "••••" in saved["pushover_api_token"]
        assert saved["telegram_chat_id"] == "-100987654321"
        assert saved["ntfy_url"] == "https://ntfy.sh/my-secret-test-topic"
        assert saved["notify_on_scrobble"] is False
        assert saved["notify_on_collection"] is False

        # Raw values should be retained in settings_mgr
        raw = settings_mgr.get_notifications(mask=False)
        assert raw["discord_webhook_url"] == "https://discord.com/api/webhooks/12345/secret_token"
        assert raw["telegram_bot_token"] == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        assert raw["ntfy_auth_token"] == "secret_ntfy_tk_9999"

        # Submitting masked value should preserve existing secret
        res_masked = client.post(
            "/api/settings",
            headers=headers,
            json={"notifications": {"discord_webhook_url": saved["discord_webhook_url"]}}
        )
        assert res_masked.status_code == 200
        assert settings_mgr.get_notifications(mask=False)["discord_webhook_url"] == "https://discord.com/api/webhooks/12345/secret_token"

        # Cleanup settings for subsequent tests
        settings_mgr.update_notifications({
            "discord_webhook_url": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "ntfy_url": "",
            "ntfy_auth_token": "",
            "pushover_user_key": "",
            "pushover_api_token": "",
        })


def test_notifications_test_endpoint_auth():
    """Verify authentication and validation on POST /api/notifications/test."""
    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        # 1. Unauthorized request
        res_unauth = client.post("/api/notifications/test", json={"channel": "discord"})
        assert res_unauth.status_code == 401

        # 2. Authorized but invalid channel
        headers = {"x-webhook-secret": "supersecret"}
        res_invalid = client.post(
            "/api/notifications/test",
            headers=headers,
            json={"channel": "unsupported_channel"}
        )
        assert res_invalid.status_code == 400
        assert "Unknown notification channel" in res_invalid.json()["message"]


@pytest.mark.asyncio
async def test_notifications_test_endpoint_channels():
    """Verify test notification dispatch across all supported channels."""
    from app.services.settings_manager import settings_mgr
    client = TestClient(app)

    settings_mgr.update_notifications({
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "ntfy_url": "",
        "pushover_user_key": "",
        "pushover_api_token": "",
    })

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"), \
         patch.object(Config, "DISCORD_WEBHOOK_URL", ""), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), \
         patch.object(Config, "TELEGRAM_CHAT_ID", ""), \
         patch.object(Config, "NTFY_URL", ""), \
         patch.object(Config, "PUSHOVER_USER_KEY", ""), \
         patch.object(Config, "PUSHOVER_API_TOKEN", ""):
        headers = {"x-webhook-secret": "supersecret"}

        # 1. Discord unconfigured
        res_disc_none = client.post("/api/notifications/test", headers=headers, json={"channel": "discord"})
        assert res_disc_none.status_code == 400
        assert res_disc_none.json()["status"] == "error"
        assert "not configured" in res_disc_none.json()["message"]

        # Discord success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")
            res_disc = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "https://discord.com/api/webhooks/test/123"}
            )
            assert res_disc.status_code == 200
            assert res_disc.json()["status"] == "success"

        # Discord failure
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
            res_disc_fail = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "https://discord.com/api/webhooks/test/123"}
            )
            assert res_disc_fail.status_code == 400
            assert res_disc_fail.json()["status"] == "error"
            assert "HTTP 400" in res_disc_fail.json()["message"]

        # 2. Telegram unconfigured
        res_tg_none = client.post("/api/notifications/test", headers=headers, json={"channel": "telegram"})
        assert res_tg_none.status_code == 400
        assert res_tg_none.json()["status"] == "error"

        # Telegram success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="{}")
            res_tg = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "telegram", "telegram_bot_token": "bot123", "telegram_chat_id": "chat123"}
            )
            assert res_tg.status_code == 200
            assert res_tg.json()["status"] == "success"

        # 3. Ntfy success (without auth and with auth token)
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            res_ntfy = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "ntfy", "ntfy_url": "https://ntfy.sh/my-topic"}
            )
            assert res_ntfy.status_code == 200
            assert res_ntfy.json()["status"] == "success"
            assert "Authorization" not in mock_post.call_args[1]["headers"]

        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            res_ntfy_auth = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "ntfy", "ntfy_url": "https://ntfy.sh/my-topic", "ntfy_auth_token": "secret_token_123"}
            )
            assert res_ntfy_auth.status_code == 200
            assert res_ntfy_auth.json()["status"] == "success"
            assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret_token_123"

        # 4. Pushover success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text='{"status": 1}')
            res_push = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "pushover", "pushover_user_key": "ukey", "pushover_api_token": "atoken"}
            )
            assert res_push.status_code == 200
            assert res_push.json()["status"] == "success"

        # 5. Masked credentials fallback to saved settings
        settings_mgr.update_notifications({
            "discord_webhook_url": "https://discord.com/api/webhooks/saved/real_token",
            "telegram_bot_token": "bot_saved_real_token",
            "telegram_chat_id": "chat_12345",
        })
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")
            res_masked = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "••••••••dcrd"}
            )
            assert res_masked.status_code == 200
            assert res_masked.json()["status"] == "success"
            assert mock_post.call_args[0][0] == "https://discord.com/api/webhooks/saved/real_token"

        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="{}")
            res_masked_tg = client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "telegram", "telegram_bot_token": "••••••••tele", "telegram_chat_id": "chat_12345"}
            )
            assert res_masked_tg.status_code == 200
            assert res_masked_tg.json()["status"] == "success"
            assert "bot_saved_real_token" in mock_post.call_args[0][0]

        # Reset cleanup
        settings_mgr.update_notifications({
            "discord_webhook_url": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        })


def test_notifier_dynamic_settings_resolution():
    """Verify Notifier respects SettingsManager overrides with Config fallback."""
    from app.services.settings_manager import settings_mgr
    test_notif = Notifier(Config)

    # When no custom override exists, falls back to Config
    with patch.object(Config, "NOTIFY_ON_RATE", True):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is True
    with patch.object(Config, "NOTIFY_ON_RATE", False):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is False

    # When custom override is set in settings_mgr, override takes precedence
    settings_mgr.update_notifications({"notify_on_rate": False})
    with patch.object(Config, "NOTIFY_ON_RATE", True):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is False

    # Check _get_ntfy_auth_token resolution
    with patch.object(Config, "NTFY_AUTH_TOKEN", "config_token"):
        settings_mgr.update_notifications({"ntfy_auth_token": ""})
        assert test_notif._get_ntfy_auth_token() == "config_token"

        settings_mgr.update_notifications({"ntfy_auth_token": "custom_token"})
        assert test_notif._get_ntfy_auth_token() == "custom_token"

    # Cleanup
    settings_mgr.update_notifications({"notify_on_rate": True, "ntfy_auth_token": ""})


@pytest.mark.asyncio
async def test_trakt_dynamic_credentials_and_token_management(tmp_path):
    """Verify TraktClient dynamic credential resolution, update_credentials, is_enabled, and delete_tokens."""
    from app.clients.trakt_client import TraktClient
    from app.services.settings_manager import settings_mgr

    tokens_file = tmp_path / "trakt_tokens.json"
    tokens_file.write_text(json.dumps({"access_token": "valid_token", "refresh_token": "refr_123"}))

    client = TraktClient(config=Config, tokens_file=tokens_file)
    assert client.is_authenticated() is True

    # Update in settings_mgr
    settings_mgr.update_tracker_credentials("trakt", {"client_id": "dynamic_trakt_id", "client_secret": "dynamic_trakt_secret"})
    client.update_credentials("dynamic_trakt_id", "dynamic_trakt_secret")
    assert client.effective_client_id == "dynamic_trakt_id"
    assert client.effective_client_secret == "dynamic_trakt_secret"

    # Enabled status check
    settings_mgr.set_tracker_enabled("trakt", False)
    assert client.is_enabled() is False
    settings_mgr.set_tracker_enabled("trakt", True)
    assert client.is_enabled() is True

    # delete_tokens
    client.delete_tokens()
    assert client.is_authenticated() is False
    assert not tokens_file.exists()


@pytest.mark.asyncio
async def test_trakt_poll_for_token_client_credentials_and_errors(tmp_path):
    """Verify TraktClient.poll_for_token forwards client_secret and raises friendly error on 401."""
    from app.clients.trakt_client import TraktClient

    tokens_file = tmp_path / "trakt_tokens.json"

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        assert data.get("client_id") == "test_cid"
        assert data.get("client_secret") == "test_csec"
        assert data.get("code") == "dev_123"
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Bad client credentials"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    trakt = TraktClient(config=Config, tokens_file=tokens_file, client=mock_client, client_id="test_cid", client_secret="test_csec")

    with pytest.raises(PermissionError) as exc_info:
        await trakt.poll_for_token("dev_123")
    assert "Invalid client credentials (401)" in str(exc_info.value)
    assert "Settings Hub" in str(exc_info.value)


def test_trakt_api_status_and_disconnect_endpoints(tmp_path):
    """Verify /api/trakt/status and /api/trakt/disconnect API endpoints."""
    from starlette.testclient import TestClient
    from app.main import app, user_mgr, trakt

    client = TestClient(app)

    # 1. Demo mode status
    resp = client.get("/api/trakt/status?demo=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["configured"] is True
    assert data["authenticated"] is True
    assert data["user"] == "demo_viewer"

    # 2. Live status
    resp = client.get("/api/trakt/status")
    assert resp.status_code == 200
    live_data = resp.json()
    assert "enabled" in live_data
    assert "configured" in live_data
    assert "authenticated" in live_data

    # 3. Disconnect requires admin (or open mode if WEBHOOK_SECRET is empty)
    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        unauth = client.post("/api/trakt/disconnect")
        assert unauth.status_code == 401

        auth = client.post("/api/trakt/disconnect", headers={"x-webhook-secret": "supersecret"})
        assert auth.status_code == 200
        assert auth.json()["status"] == "ok"


def test_settings_hub_trakt_credential_sync_and_tracker_statuses():
    """Verify PUT /api/settings syncs Trakt credentials to both trakt and user_mgr dynamically."""
    from starlette.testclient import TestClient
    from app.main import app, trakt, user_mgr, anilist, mal
    from app.services.settings_manager import settings_mgr

    client = TestClient(app)

    with patch.object(Config, "WEBHOOK_SECRET", "adminkey"):
        payload = {
            "credentials": {
                "trakt": {
                    "client_id": "synced_trakt_id",
                    "client_secret": "synced_trakt_secret",
                },
                "simkl": {
                    "client_id": "synced_simkl_id",
                    "client_secret": "synced_simkl_secret",
                },
                "mal": {
                    "client_id": "synced_mal_id",
                    "client_secret": "synced_mal_secret",
                },
            }
        }
        res = client.put("/api/settings", json=payload, headers={"x-webhook-secret": "adminkey"})
        assert res.status_code == 200

        # Verify trakt and user_mgr dynamically updated
        assert trakt.client_id == "synced_trakt_id"
        assert trakt.client_secret == "synced_trakt_secret"
        default_client = user_mgr.get_client("default")
        assert default_client.client_id == "synced_trakt_id"
        assert default_client.client_secret == "synced_trakt_secret"

        # Verify /api/anilist/status and /api/mal/status respect dynamic is_enabled
        settings_mgr.set_tracker_enabled("anilist", False)
        ani_res = client.get("/api/anilist/status")
        assert ani_res.status_code == 200
        assert ani_res.json()["enabled"] is False

        settings_mgr.set_tracker_enabled("anilist", True)
        ani_res2 = client.get("/api/anilist/status")
        assert ani_res2.status_code == 200
        assert ani_res2.json()["enabled"] is True

        settings_mgr.set_tracker_enabled("mal", False)
        mal_res = client.get("/api/mal/status")
        assert mal_res.status_code == 200
        assert mal_res.json()["enabled"] is False

        settings_mgr.set_tracker_enabled("mal", True)
        mal_res2 = client.get("/api/mal/status")
        assert mal_res2.status_code == 200
        assert mal_res2.json()["enabled"] is True


def test_webhook_simulator_payload_generation_and_parsing():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import simulate_webhook
    from app.plex_parser import parse_plex_webhook
    from app.jellyfin_parser import parse_jellyfin_webhook
    from app.emby_parser import parse_emby_webhook
    from app.clients.sonarr_client import parse_sonarr_webhook
    from app.clients.radarr_client import parse_radarr_webhook

    # 1. Plex Movie Finish
    plex_payload = simulate_webhook.build_plex_payload(
        scenario="movie-finish",
        title="Dune: Part Two",
        year=2024,
        show=None,
        season=1,
        episode=1,
        rating=10,
        progress=100.0,
        user="selits",
    )
    parsed_plex = parse_plex_webhook(plex_payload)
    assert parsed_plex is not None
    assert parsed_plex.media_type == "movie"
    assert parsed_plex.title == "Dune: Part Two"
    assert parsed_plex.year == 2024
    assert parsed_plex.event == "media.scrobble"

    # 2. Jellyfin Episode Start
    jf_payload = simulate_webhook.build_jellyfin_payload(
        scenario="episode-start",
        title="Good News About Hell",
        year=2022,
        show="Severance",
        season=1,
        episode=1,
        rating=10,
        progress=0.0,
        user="testuser",
    )
    parsed_jf = parse_jellyfin_webhook(jf_payload)
    assert parsed_jf is not None
    assert parsed_jf.media_type == "episode"
    assert parsed_jf.show_title == "Severance"
    assert parsed_jf.season == 1
    assert parsed_jf.episode == 1
    assert parsed_jf.event == "media.play"

    # 3. Emby Rating
    emby_payload = simulate_webhook.build_emby_payload(
        scenario="rate-movie",
        title="Inception",
        year=2010,
        show=None,
        season=1,
        episode=1,
        rating=9,
        progress=100.0,
        user="testuser",
    )
    parsed_emby = parse_emby_webhook(emby_payload)
    assert parsed_emby is not None
    assert parsed_emby.media_type == "movie"
    assert parsed_emby.rating == 9
    assert parsed_emby.event == "media.rate"

    # 4. Sonarr Download
    sonarr_payload = simulate_webhook.build_sonarr_payload(
        scenario="download",
        title="Pilot",
        year=2022,
        show="Severance",
        season=1,
        episode=1,
    )
    ev_type, trakt_p, parsed_sonarr = parse_sonarr_webhook(sonarr_payload)
    assert ev_type == "download"
    assert parsed_sonarr is not None
    assert parsed_sonarr.show_title == "Severance"
    assert parsed_sonarr.season == 1

    # 5. Radarr Download
    radarr_payload = simulate_webhook.build_radarr_payload(
        scenario="download",
        title="Inception",
        year=2010,
    )
    ev_r_type, trakt_r_p, parsed_radarr = parse_radarr_webhook(radarr_payload)
    assert ev_r_type == "download"
    assert parsed_radarr is not None
    assert parsed_radarr.title == "Inception"
    assert parsed_radarr.year == 2010


# =====================================================================
# Pillar 3 & 4: Cloud Trackers, Categorization & Endpoints Unit Tests
# =====================================================================

@pytest.mark.asyncio
async def test_tmdb_client_operations(monkeypatch):
    """Verify TMDbClient watchlist sync, ratings, and connection check."""
    from app.clients.tmdb_client import TMDbClient
    from app.config import Config

    monkeypatch.setattr(Config, "TMDB_API_KEY", "test_tmdb_key")
    monkeypatch.setattr(Config, "TMDB_READ_ACCESS_TOKEN", "test_token")
    monkeypatch.setattr(Config, "TMDB_ACCOUNT_ID", "12345")
    monkeypatch.setattr(Config, "TMDB_SESSION_ID", "test_session")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/account/12345/watchlist" in url:
            return httpx.Response(200, json={"status_code": 1, "status_message": "Success."})
        elif "/movie/27205/rating" in url:
            return httpx.Response(200, json={"status_code": 1, "status_message": "Success."})
        elif "/tv/95396/season/1/episode/1/rating" in url:
            return httpx.Response(200, json={"status_code": 1, "status_message": "Success."})
        elif "/account" in url:
            return httpx.Response(200, json={"id": 12345, "username": "cinephile_test"})
        return httpx.Response(404, json={"status_message": "Not found"})

    transport = httpx.MockTransport(handler)
    client = TMDbClient(Config, transport=transport)

    assert client.is_configured() is True
    conn = await client.check_connection()
    assert conn["status"] == "connected"
    assert conn["authenticated"] is True

    # Watchlist sync
    res_w = await client.sync_watchlist(media_type="movie", tmdb_id=27205, watchlist=True)
    assert res_w["status"] == "success"
    assert res_w["action"] == "add"

    # Movie Rating sync (1-10 scale)
    res_r_m = await client.sync_rating(media_type="movie", tmdb_id=27205, rating=8)
    assert res_r_m["status"] == "success"
    assert res_r_m["rating"] == 8.0

    # TV Episode Rating sync
    res_r_e = await client.sync_rating(media_type="episode", tmdb_id=95396, season=1, episode=1, rating=10)
    assert res_r_e["status"] == "success"
    assert res_r_e["rating"] == 10.0

    await client.close()


@pytest.mark.asyncio
async def test_kitsu_client_operations(monkeypatch):
    """Verify KitsuClient anime search, progress updates, ratings, and delete progress."""
    from app.clients.kitsu_client import KitsuClient
    from app.config import Config

    monkeypatch.setattr(Config, "KITSU_API_KEY", "test_kitsu_token")
    monkeypatch.setattr(Config, "KITSU_USER_ID", "999")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/anime" in url and "filter%5Btext%5D=Attack" in url:
            return httpx.Response(200, json={
                "data": [{
                    "id": "7442",
                    "type": "anime",
                    "attributes": {
                        "canonicalTitle": "Attack on Titan",
                        "startDate": "2013-04-07",
                        "episodeCount": 25,
                    }
                }]
            })
        elif "/library-entries" in url and request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "entry_1", "attributes": {"progress": 4, "ratingTwenty": 18}}]})
        elif "/library-entries" in url and request.method == "PATCH":
            return httpx.Response(200, json={"data": {"id": "entry_1", "attributes": {"progress": 5}}})
        elif "/library-entries/entry_1" in url and request.method == "DELETE":
            return httpx.Response(204)
        elif "/users" in url:
            return httpx.Response(200, json={"data": [{"id": "999", "attributes": {"name": "kitsu_otaku"}}]})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    client = KitsuClient(Config, transport=transport)

    assert client.is_configured() is True
    conn = await client.check_connection()
    assert conn["status"] == "connected"
    assert conn["user"] == "kitsu_otaku"

    # Search anime
    search_res = await client.search_anime("Attack on Titan", 2013)
    assert search_res is not None
    assert search_res["id"] == "7442"
    assert search_res["title"] == "Attack on Titan"
    assert search_res["episode_count"] == 25

    # Update progress
    prog_res = await client.update_progress(anime_id="7442", episode_number=5, total_episodes=25)
    assert prog_res["status"] == "success"
    assert prog_res["progress"] == 5

    # Update rating (converted to ratingTwenty)
    rate_res = await client.update_rating(anime_id="7442", rating=9)
    assert rate_res["status"] == "success"
    assert rate_res["ratingTwenty"] == 18

    # Delete progress
    del_res = await client.delete_progress(anime_id="7442")
    assert del_res["status"] == "deleted"

    await client.close()


@pytest.mark.asyncio
async def test_letterboxd_client_diary_and_csv(tmp_path, monkeypatch):
    """Verify LetterboxdClient persistent diary store and RFC-4180 CSV export."""
    from app.clients.letterboxd_client import LetterboxdClient
    from app.config import Config

    diary_file = tmp_path / "test_diary.json"
    monkeypatch.setattr(Config, "LETTERBOXD_USERNAME", "cinephile_test")

    client = LetterboxdClient(Config, diary_file=diary_file)
    assert client.is_configured() is True
    conn = await client.check_connection()
    assert conn["status"] == "connected"
    assert conn["user"] == "cinephile_test"

    # Log entry 1
    log1 = await client.log_movie_entry(
        title="Inception",
        year=2010,
        rating=9,
        imdb_id="tt1375666",
        tmdb_id=27205,
    )
    assert log1["status"] == "logged"
    assert log1["stars"] == 4.5
    assert log1["rating10"] == 9

    # Log entry 2
    log2 = await client.log_movie_entry(
        title="Dune: Part Two",
        year=2024,
        rating=10,
        imdb_id="tt15239678",
        tmdb_id=693134,
        tags=["imax", "sci-fi"],
    )
    assert log2["status"] == "logged"
    assert log2["stars"] == 5.0

    # Get diary entries
    diary = client.get_diary_entries()
    assert len(diary) == 2
    assert diary[0]["title"] == "Inception"

    # Generate CSV export
    csv_content = client.generate_csv_export()
    assert "Title,Year,WatchedDate,Rating10,Rating,Rewatch,Tags,Review,imdbID,tmdbID" in csv_content
    assert "Inception,2010" in csv_content
    assert "4.5" in csv_content
    assert "tt1375666" in csv_content
    assert "Dune: Part Two,2024" in csv_content

    await client.close()


@pytest.mark.asyncio
async def test_serializd_client_operations(monkeypatch):
    """Verify SerializdClient TV episode diary logging and ratings."""
    from app.clients.serializd_client import SerializdClient
    from app.config import Config

    monkeypatch.setattr(Config, "SERIALIZD_USERNAME", "binger_test")
    monkeypatch.setattr(Config, "SERIALIZD_TOKEN", "serializd_jwt_token")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/me" in url or "/user/binger_test" in url:
            return httpx.Response(200, json={"username": "binger_test", "total_episodes": 340})
        elif "/log" in url:
            return httpx.Response(200, json={"success": True, "id": "log_123"})
        elif "/review" in url or "/rating" in url:
            return httpx.Response(200, json={"success": True, "rating": 4.5})
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    client = SerializdClient(Config, transport=transport)

    assert client.is_configured() is True
    conn = await client.check_connection()
    assert conn["status"] == "connected"
    assert conn["user"] == "binger_test"

    # Log TV episode
    res_ep = await client.log_episode(
        show_title="Severance",
        season=1,
        episode=1,
        tmdb_id=95396,
        rating=10,
    )
    assert res_ep["status"] == "success"
    assert res_ep["show"] == "Severance"
    assert res_ep["rating"] == 5.0

    # Sync show rating
    res_r = await client.sync_rating(show_title="Severance", tmdb_id=95396, rating=9)
    assert res_r["status"] == "success"
    assert res_r["rating"] == 4.5

    await client.close()


@pytest.mark.asyncio
async def test_mdblist_client_operations(monkeypatch):
    """Verify MDBListClient external score enrichment and watchlist ingestion."""
    from app.clients.mdblist_client import MDBListClient
    from app.config import Config

    monkeypatch.setattr(Config, "MDBLIST_API_KEY", "test_mdblist_key")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/user" in url:
            return httpx.Response(200, json={"user": "collector_test", "valid": True})
        elif "/watchlist" in url:
            return httpx.Response(200, json={"status": "added", "title": "Inception"})
        else:
            return httpx.Response(200, json={
                "id": 27205,
                "title": "Inception",
                "year": 2010,
                "score": 87,
                "ratings": [
                    {"source": "imdb", "value": 8.8, "score": 88},
                    {"source": "tomatoes", "value": 87, "score": 87},
                    {"source": "metacritic", "value": 74, "score": 74},
                    {"source": "letterboxd", "value": 4.2, "score": 84},
                ]
            })

    transport = httpx.MockTransport(handler)
    client = MDBListClient(Config, transport=transport)

    assert client.is_configured() is True
    conn = await client.check_connection()
    assert conn["status"] == "connected"

    # Get aggregated ratings
    ratings = await client.get_item_ratings(media_type="movie", imdb_id="tt1375666", tmdb_id=27205)
    assert ratings is not None
    assert ratings["score"] == 87
    assert ratings["imdb"] == 8.8
    assert ratings["tomatoes"] == 87
    assert ratings["letterboxd"] == 4.2

    # Watchlist ingestion
    watch_res = await client.add_to_watchlist(media_type="movie", imdb_id="tt1375666", tmdb_id=27205)
    assert watch_res["status"] == "success"

    await client.close()


@pytest.mark.asyncio
async def test_multi_tracker_categorized_status_and_capabilities(tmp_path):
    """Verify MultiTrackerManager 4-category taxonomy and registry."""
    from app.services.multi_tracker import MultiTrackerManager
    from app.clients import SimklClient, AniListClient, MyAnimeListClient, KitsuClient, TMDbClient, LetterboxdClient, SerializdClient, MDBListClient
    from app.config import Config

    mt = MultiTrackerManager(
        Config,
        simkl_client=SimklClient(Config),
        anilist_client=AniListClient(Config),
        mal_client=MyAnimeListClient(Config),
        kitsu_client=KitsuClient(Config),
        tmdb_client=TMDbClient(Config),
        letterboxd_client=LetterboxdClient(Config, diary_file=tmp_path / "diary.json"),
        serializd_client=SerializdClient(Config),
        mdblist_client=MDBListClient(Config),
    )

    reg = mt.get_registered_trackers()
    assert len(reg) == 9
    assert reg["trakt"]["category"] == "universal"
    assert reg["simkl"]["category"] == "universal"
    assert reg["tmdb"]["category"] == "universal"
    assert reg["anilist"]["category"] == "anime"
    assert reg["myanimelist"]["category"] == "anime"
    assert reg["kitsu"]["category"] == "anime"
    assert reg["letterboxd"]["category"] == "social_diary"
    assert reg["serializd"]["category"] == "social_diary"
    assert reg["mdblist"]["category"] == "lists_ratings"

    status = await mt.get_status()
    assert "categories" in status
    assert status["categories"]["universal"] == ["trakt", "simkl", "tmdb"]
    assert status["categories"]["anime"] == ["anilist", "myanimelist", "kitsu"]
    assert status["categories"]["social_diary"] == ["letterboxd", "serializd"]
    assert status["categories"]["lists_ratings"] == ["mdblist"]
    assert "trackers" in status
    assert len(status["trackers"]) == 9

    await mt.close()


def test_cloud_tracker_api_endpoints():
    """Verify HTTP API endpoints for all cloud trackers, Letterboxd CSV export, and relay."""
    client = TestClient(app)

    # 1. Structured Multi-Tracker status
    res = client.get("/api/trackers/status")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "universal" in data["categories"]
    assert "anime" in data["categories"]
    assert "social_diary" in data["categories"]
    assert "lists_ratings" in data["categories"]
    assert "trackers" in data

    # 2. TMDb status
    res_tmdb = client.get("/api/tmdb/status")
    assert res_tmdb.status_code == 200
    assert "status" in res_tmdb.json()

    # 3. Kitsu status
    res_kitsu = client.get("/api/kitsu/status")
    assert res_kitsu.status_code == 200
    assert "status" in res_kitsu.json()

    # 4. Letterboxd status & diary
    res_lb = client.get("/api/letterboxd/status")
    assert res_lb.status_code == 200

    res_diary = client.get("/api/letterboxd/diary")
    assert res_diary.status_code == 200
    assert isinstance(res_diary.json(), list)

    res_export = client.get("/api/letterboxd/export")
    assert res_export.status_code == 200
    assert "text/csv" in res_export.headers.get("content-type", "")
    assert "Title,Year,WatchedDate" in res_export.text

    # 5. Serializd status
    res_ser = client.get("/api/serializd/status")
    assert res_ser.status_code == 200

    # 6. MDBList status & ratings endpoint (demo and unconfigured behaviors)
    res_mdb = client.get("/api/mdblist/status")
    assert res_mdb.status_code == 200

    res_mdb_r = client.get("/api/mdblist/ratings?demo=true")
    assert res_mdb_r.status_code == 200
    assert res_mdb_r.json().get("title") == "Dune: Part Two"

    # 7. Relay status
    res_relay = client.get("/api/relay/status")
    assert res_relay.status_code == 200
    relay_data = res_relay.json()
    assert "SeriesGuide" in relay_data.get("supported_apps", [])
    assert "Showly" in relay_data.get("supported_apps", [])


def test_cloud_tracker_security_and_privacy(monkeypatch, tmp_path):
    """Verify admin authorization, privacy masking, and backup inclusion for cloud trackers."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import Config
    import app.main as main_mod

    client = TestClient(app)

    # 1. When WEBHOOK_SECRET is set, non-admin requests to /api/letterboxd/export and /api/letterboxd/diary return 401
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "super_secret_webhook_key_123")

    # Unauthenticated export & diary
    res_export_unauth = client.get("/api/letterboxd/export")
    assert res_export_unauth.status_code == 401

    res_diary_unauth = client.get("/api/letterboxd/diary")
    assert res_diary_unauth.status_code == 401

    # Authenticated export & diary with ?token=
    res_export_auth = client.get("/api/letterboxd/export?token=super_secret_webhook_key_123")
    assert res_export_auth.status_code == 200
    assert "text/csv" in res_export_auth.headers.get("content-type", "")

    res_diary_auth = client.get("/api/letterboxd/diary?token=super_secret_webhook_key_123")
    assert res_diary_auth.status_code == 200

    # Demo bypass for diary
    res_diary_demo = client.get("/api/letterboxd/diary?demo=true")
    assert res_diary_demo.status_code == 200
    assert len(res_diary_demo.json()) >= 1

    # 2. Non-admin privacy masking for usernames across tracker status endpoints
    main_mod.kitsu.user_name = "kitsu_otaku_master"
    main_mod.letterboxd.username = "letterboxd_cinephile"
    main_mod.serializd.username = "serializd_binger"

    async def mock_kitsu_check():
        return {
            "name": "Kitsu",
            "configured": True,
            "authenticated": True,
            "enabled": True,
            "status": "connected",
            "user": "kitsu_otaku_master",
            "username": "kitsu_otaku_master",
            "message": "Connected as @kitsu_otaku_master",
        }
    monkeypatch.setattr(main_mod.kitsu, "check_connection", mock_kitsu_check)

    # Non-admin status view masks usernames
    res_kitsu_masked = client.get("/api/kitsu/status")
    assert res_kitsu_masked.status_code == 200
    assert res_kitsu_masked.json().get("user") != "kitsu_otaku_master"

    res_lb_masked = client.get("/api/letterboxd/status")
    assert res_lb_masked.status_code == 200
    assert res_lb_masked.json().get("user") != "letterboxd_cinephile"

    res_ser_masked = client.get("/api/serializd/status")
    assert res_ser_masked.status_code == 200
    assert res_ser_masked.json().get("user") != "serializd_binger"

    # Admin view with header x-webhook-secret reveals full username
    res_kitsu_admin = client.get("/api/kitsu/status", headers={"x-webhook-secret": "super_secret_webhook_key_123"})
    assert res_kitsu_admin.status_code == 200
    assert res_kitsu_admin.json().get("user") == "kitsu_otaku_master"

    # 3. Parameter validation on /api/mdblist/ratings: 400 when missing both ids
    monkeypatch.setattr(main_mod.mdblist, "is_configured", lambda: True)
    res_mdb_bad = client.get("/api/mdblist/ratings?token=super_secret_webhook_key_123")
    assert res_mdb_bad.status_code == 400
    assert "Either imdb_id or tmdb_id" in res_mdb_bad.json().get("detail", "")

    # 4. Backup zip includes letterboxd_diary.json when present
    diary_file = tmp_path / "letterboxd_diary.json"
    diary_file.write_text('[{"title": "Inception", "year": 2010}]', encoding="utf-8")
    monkeypatch.setattr(Config, "LETTERBOXD_DIARY_FILE", diary_file)
    monkeypatch.setattr(Config, "LETTERBOXD_DATA_FILE", diary_file)

    res_backup = client.get("/api/backup?token=super_secret_webhook_key_123")
    assert res_backup.status_code == 200
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(res_backup.content), "r") as zf:
        names = zf.namelist()
        assert "data/letterboxd_diary.json" in names


@pytest.mark.asyncio
async def test_cloud_tracker_resilience_and_errors(monkeypatch):
    """Verify error handling, HTTP error tolerances, and non-blocking resilience across trackers."""
    from app.clients.tmdb_client import TMDbClient
    from app.clients.kitsu_client import KitsuClient
    from app.clients.serializd_client import SerializdClient
    from app.clients.mdblist_client import MDBListClient
    from app.config import Config

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal upstream error"})

    transport = httpx.MockTransport(error_handler)

    monkeypatch.setattr(Config, "TMDB_API_KEY", "key")
    monkeypatch.setattr(Config, "KITSU_API_KEY", "token")
    monkeypatch.setattr(Config, "SERIALIZD_USERNAME", "user")
    monkeypatch.setattr(Config, "SERIALIZD_TOKEN", "token")
    monkeypatch.setattr(Config, "MDBLIST_API_KEY", "key")

    tmdb_c = TMDbClient(Config, transport=transport)
    kitsu_c = KitsuClient(Config, transport=transport)
    ser_c = SerializdClient(Config, transport=transport)
    mdb_c = MDBListClient(Config, transport=transport)

    res_t_rate = await tmdb_c.sync_rating(media_type="movie", tmdb_id=123, rating=8)
    assert res_t_rate["status"] == "error"

    res_t_watch = await tmdb_c.sync_watchlist(media_type="movie", tmdb_id=123, watchlist=True)
    assert res_t_watch["status"] == "error"

    res_k_search = await kitsu_c.search_anime("NonExistentShow", 2025)
    assert res_k_search is None

    res_k_prog = await kitsu_c.update_progress(anime_id=999, episode_number=1)
    assert res_k_prog["status"] == "error"

    res_k_rate = await kitsu_c.update_rating(anime_id=999, rating=8)
    assert res_k_rate["status"] == "error"

    res_k_del = await kitsu_c.delete_progress(anime_id=999)
    assert res_k_del["status"] in ("ignored", "error")

    res_s_log = await ser_c.log_episode(show_title="TestShow", season=1, episode=1)
    assert res_s_log["status"] == "error"

    res_m_ratings = await mdb_c.get_item_ratings(imdb_id="tt0000000")
    assert res_m_ratings is None

    res_m_watchlist = await mdb_c.add_to_watchlist(media_type="movie", imdb_id="tt0000000")
    assert res_m_watchlist["status"] == "error"

    await tmdb_c.close()
    await kitsu_c.close()
    await ser_c.close()
    await mdb_c.close()


def test_https_and_proxy_headers_readiness(monkeypatch):
    """Verify proxy-headers middleware, conditional HSTS header, and EXTERNAL_URL support."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import Config

    client = TestClient(app)

    # 1. Plain HTTP request: no HSTS header emitted
    res_http = client.get("/")
    assert res_http.status_code == 200
    assert "Strict-Transport-Security" not in res_http.headers

    # 2. HTTPS request via X-Forwarded-Proto header: HSTS header emitted
    res_https = client.get("/", headers={"x-forwarded-proto": "https"})
    assert res_https.status_code == 200
    assert "Strict-Transport-Security" in res_https.headers
    assert "max-age=31536000" in res_https.headers["Strict-Transport-Security"]

    # 3. EXTERNAL_URL overrides dashboard webhook URLs
    monkeypatch.setattr(Config, "EXTERNAL_URL", "https://omniscrobble.securehomelab.net")
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test_secret_abc")
    res_dash = client.get("/?token=test_secret_abc")
    assert res_dash.status_code == 200
    assert "https://omniscrobble.securehomelab.net/webhook?token=test_secret_abc" in res_dash.text



