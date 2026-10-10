"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_simkl_api_endpoints_and_views():
    """Verify /api/simkl/status, /api/simkl/pin, /api/simkl/poll, and /auth/simkl routes."""
    client = make_async_test_client(app)

    # 1. Demo Status
    demo_resp = await client.get("/api/simkl/status?demo=true")
    assert demo_resp.status_code == 200
    d_data = demo_resp.json()
    assert d_data["enabled"] is True
    assert d_data["configured"] is True
    assert d_data["authenticated"] is True
    assert d_data["user"] == "demo_viewer"

    # 2. Live Status (public read)
    live_resp = await client.get("/api/simkl/status")
    assert live_resp.status_code == 200

    # 3. Auth Simkl page (Admin protected)
    if Config.WEBHOOK_SECRET:
        unauth_page = await client.get("/auth/simkl")
        assert unauth_page.status_code == 401

        auth_page = await client.get(f"/auth/simkl?token={Config.WEBHOOK_SECRET}")
        assert auth_page.status_code == 200
        assert "Simkl" in auth_page.text
        assert "Device PIN Authorization" in auth_page.text
    else:
        auth_page = await client.get("/auth/simkl")
        assert auth_page.status_code == 200
        assert "Simkl" in auth_page.text

    # 4. Disconnect Simkl route
    if Config.WEBHOOK_SECRET:
        unauth_disc = await client.post("/api/simkl/disconnect")
        assert unauth_disc.status_code == 401

        auth_disc = await client.post(f"/api/simkl/disconnect?token={Config.WEBHOOK_SECRET}")
        assert auth_disc.status_code == 200
        assert auth_disc.json()["status"] == "ok"
    else:
        auth_disc = await client.post("/api/simkl/disconnect")
        assert auth_disc.status_code == 200
        assert auth_disc.json()["status"] == "ok"

    # 5. Simkl PIN endpoint
    from app.main import simkl
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        unauth_pin = await client.post("/api/simkl/pin")
        assert unauth_pin.status_code == 401

        with patch.object(simkl, "get_device_pin", return_value={"error": "SIMKL_CLIENT_ID not configured"}):
            err_pin = await client.post("/api/simkl/pin?token=testsecret")
            assert err_pin.status_code == 400
            assert "SIMKL_CLIENT_ID not configured" in err_pin.json()["detail"]

        with patch.object(simkl, "get_device_pin", return_value={"user_code": "ABCD-1234", "device_code": "dev_123", "verification_url": "https://simkl.com/pin?user_code=ABCD-1234"}):
            ok_pin = await client.post("/api/simkl/pin?token=testsecret")
            assert ok_pin.status_code == 200
            assert ok_pin.json()["user_code"] == "ABCD-1234"
            assert ok_pin.json()["device_code"] == "dev_123"

        with patch.object(simkl, "poll_device_pin", return_value={"status": "success", "result": "OK", "access_token": "tok123"}) as mock_poll:
            poll_resp = await client.post("/api/simkl/poll?token=testsecret", json={"user_code": "ABCD-1234", "device_code": "dev_123"})
            assert poll_resp.status_code == 200
            assert poll_resp.json()["status"] == "success"
            mock_poll.assert_called_once_with("ABCD-1234", device_code="dev_123")

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_renders_simkl_card():
    """Verify that the dashboard template renders the Simkl Multi-Tracker card and modal."""
    client = make_async_test_client(app)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text + await dashboard_asset_text(client)

    assert "Multi-Tracker Hub" in html
    assert "Cloud Synchronization" in html
    assert "simkl-modal" in html
    assert "openSimklModal" in html
    assert "disconnectSimkl" in html

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_quick_scrobble_modal_defaults_and_simkl_button():
    """Verify that the quick scrobble modal only defaults to configured trackers and co-watch is unchecked."""
    client = make_async_test_client(app)

    # 1. Test live dashboard (unauthenticated trackers by default in mock env)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text + await dashboard_asset_text(client)

    # Co-watch partner must be unchecked by default
    assert '<input type="checkbox" id="scrobble-cowatch-check">' in html
    assert '<input type="checkbox" id="scrobble-cowatch-check" checked>' not in html

    # Reset JS logic must be present in openManualScrobbleModal
    assert "cowatchChk.checked = false" in html
    assert "'scrobble-trk-trakt':" in html

    # 2. Test demo dashboard (all trackers active in demo)
    resp_demo = await client.get("/demo")
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

