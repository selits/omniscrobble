import json
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
    assert "Plex &rarr; Trakt Scrobbler" in res_dash.text

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
    assert get_trakt_url(ep) == "https://trakt.tv/search/imdb/tt2301451"

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
    assert get_trakt_url(ep_same) == "https://trakt.tv/search/tmdb/62085"

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
    assert get_trakt_url(movie) == "https://trakt.tv/search/tvdb/12345"

    # Fallback without IDs
    plain = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Unknown Film",
    )
    assert format_media_title(plain) == "Unknown Film"
    assert get_trakt_url(plain) == "https://trakt.tv"


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
    assert embed["url"] == "https://trakt.tv/search/imdb/tt2301451"
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
    assert "https://github.com/selits/plex-trakt-webhook" in html
    assert "v1.2.0" in html
    assert "https://github.com/selits/plex-trakt-webhook/releases" in html
    assert "https://github.com/selits/plex-trakt-webhook#readme" in html











