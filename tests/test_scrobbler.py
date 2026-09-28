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
            # Zip slip attack attempt (must be skipped safely)
            test_zf.writestr("../evil_file.txt", "evil")

        test_zip_buf.seek(0)
        files = {"backup_file": ("backup.zip", test_zip_buf.getvalue(), "application/zip")}
        res_restore = client.post("/api/restore", files=files)
        assert res_restore.status_code == 200
        restore_data = res_restore.json()
        assert restore_data["status"] == "success"
        assert "data/cowatch_shows.json" in restore_data["restored"]
        # Malicious path was ignored
        assert "../evil_file.txt" not in restore_data["restored"]

        # 4. Unauthenticated restore request -> 401
        client.cookies.clear()
        res_restore_denied = client.post("/api/restore", files=files)
        assert res_restore_denied.status_code == 401


def test_dashboard_privacy_shield_and_script_syntax():
    """Verify that HTML dashboards have balanced scripts and robust non-admin privacy shielding."""
    client = TestClient(app)
    playback_mgr.clear()
    recent_events.clear()

    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"), \
         patch.object(Config, "CO_WATCH_USER", "bon.vivant"), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["selits's Fire TV", "Google TV"]):
        cowatch_mgr._shows = ["Secret CoWatch Show Alpha", "Secret CoWatch Show Beta"]

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

        # Admin sees full show titles and device names
        assert "Secret CoWatch Show Alpha" in html_admin
        assert "Secret CoWatch Show Beta" in html_admin
        assert "Devices: <strong>selits's Fire TV, Google TV</strong>" in html_admin
        assert "testsecret" in html_admin
        client.cookies.clear()


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
    assert "v1.6.0" in html
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
        assert 'class="webhook-row"' in html
        assert 'class="activity-header"' in html
        assert 'class="activity-actions"' in html
        assert 'class="table-container"' in html
        assert 'class="modal-dialog' in html
        assert 'class="logs-toolbar"' in html
        assert 'class="footer"' in html


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

    content = demo_file.read_text(encoding="utf-8")
    assert "Live Interactive Demo" in content
    assert APP_VERSION in content
    assert "👑 Demo Admin" in content
    assert "● Connected as @demo_viewer" in content
    assert "window.fetch = async function" in content
    assert "/api/playback" in content
    assert "/api/events" in content
    assert "/api/cowatch/shows" in content
    assert "/api/sonarr/shows" in content
    assert "/api/logs" in content
    assert "/api/search" in content
    assert "/api/test/webhook" in content

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
    """Verify dashboard HTML includes Two-Way Reconciliation card and modal dialog."""
    client = TestClient(app)

    res = client.get("/")
    assert res.status_code == 200
    html_content = res.text
    assert "Two-Way Library Reconciliation" in html_content
    assert 'id="reconcile-modal"' in html_content
    assert "reconcile-diff-badge" in html_content

    # In demo mode
    res_demo = client.get("/demo")
    assert res_demo.status_code == 200
    assert "Two-Way Library Reconciliation" in res_demo.text


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
    assert demo_eco["healthy_count"] == 6
    assert demo_eco["total_count"] == 6
    server_ids = [s["id"] for s in demo_eco["servers"]]
    assert "plex" in server_ids
    assert "jellyfin" in server_ids
    assert "emby" in server_ids
    assert "trakt" in server_ids
    assert "sonarr" in server_ids
    assert "radarr" in server_ids

    # Live ecosystem
    live_eco = await arr_bridge.get_ecosystem_status(demo=False)
    assert "servers" in live_eco
    assert len(live_eco["servers"]) >= 4


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
    assert res_eco_demo.json()["healthy_count"] == 6

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

    res_demo = client.get("/demo")
    assert res_demo.status_code == 200
    assert "Multi-Server Ecosystem" in res_demo.text
    assert "Content Bridge" in res_demo.text


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








