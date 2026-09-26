import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from config import Config
from main import app, get_uptime_str, mask_username, queue_mgr, recent_events, scrobble_stats, trakt
from queue_manager import QueueManager, process_queue
from trakt_client import TraktClient

from notifier import Notifier, format_media_title, get_trakt_url, notifier
from plex_parser import ParsedMedia, parse_plex_ids, parse_plex_webhook


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
        with patch("main.process_queue", new_callable=AsyncMock) as mock_proc:
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





