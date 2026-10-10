"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
async def test_api_search_and_manual_scrobble():
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
        # 1. Unauthenticated search -> 401
        res_denied = await client.get("/api/search?query=Inception")
        assert res_denied.status_code == 401

        # 2. Authenticated search -> 200
        client.cookies.set("admin_token", "test_secret")
        with patch.object(trakt, "search_media", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{"type": "movie", "movie": {"title": "Inception", "year": 2010}}]
            res_ok = await client.get("/api/search?query=Inception")
            assert res_ok.status_code == 200
            assert len(res_ok.json()["results"]) == 1

        # 3. Unauthenticated manual scrobble -> 401
        client.cookies.clear()
        res_scrobble_denied = await client.post("/api/scrobble/manual", json={"media_type": "movie", "title": "Inception"})
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
            res_scrobble_ok = await client.post("/api/scrobble/manual", json=payload)
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
            res_ep_ok = await client.post("/api/scrobble/manual", json=ep_payload)
            assert res_ep_ok.status_code == 200
            assert mock_sync.called
            sync_ep_payload = mock_sync.call_args[0][0]
            assert "shows" in sync_ep_payload
            assert sync_ep_payload["shows"][0]["title"] == "Succession"
            assert sync_ep_payload["shows"][0]["seasons"][0]["episodes"][0]["number"] == 3

        client.cookies.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_api_history_remove_endpoint():
    """Verify POST /api/history/remove unscrobbles items across trackers."""
    client = make_async_test_client(app)
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "admintoken"

        # Unauthorized
        res_unauth = await client.post("/api/history/remove", json={
            "media_type": "movie", "title": "Dune", "year": 2021
        })
        assert res_unauth.status_code == 401

        # Authorized call with mock
        with patch("app.main.multi_tracker.dispatch_unscrobble", new_callable=AsyncMock) as mock_unscrobble:
            mock_unscrobble.return_value = {
                "trakt": {"status": "removed"},
                "simkl": {"status": "removed"}
            }

            res_ok = await client.post("/api/history/remove?token=admintoken", json={
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

@pytest.mark.asyncio(loop_scope="module")
async def test_standalone_scrobble_rest_bridge():
    """Verify Standalone Player Direct Webhook / REST Bridge (POST /api/scrobble)."""
    from app.main import app, trakt
    client = make_async_test_client(app)

    # 1. Info endpoint (GET /api/scrobble)
    res_info = await client.get("/api/scrobble")
    assert res_info.status_code == 200
    assert res_info.json()["service"] == "Omniscrobble Standalone Player REST Bridge"
    assert "payload_schema" in res_info.json()

    # 2. POST /api/scrobble with webhook secret protection
    with patch.object(Config, "WEBHOOK_SECRET", "super_secret_token"):
        # Without token -> 401
        res_unauth = await client.post(
            "/api/scrobble",
            json={"title": "Spirited Away", "media_type": "movie", "action": "play"},
        )
        assert res_unauth.status_code == 401

        # With query parameter token -> authenticated
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}

            res_play = await client.post(
                "/api/scrobble?token=super_secret_token",
                json={
                    "title": "Spirited Away",
                    "year": 2001,
                    "progress": 5.0,
                    "media_type": "movie",
                    "action": "play",
                    "player": "Infuse",
                    "ids": {"imdb": "tt0245429", "tmdb": "129"},
                },
            )
            assert res_play.status_code == 200
            assert res_play.json()["action"] == "scrobble_start"
            mock_start.assert_awaited_once()

        # With header x-webhook-secret and scrobble completion action
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
             patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_hist:
            mock_stop.return_value = {"action": "stop"}
            mock_hist.return_value = {"added": {"movies": 1}}

            res_scrobble = await client.post(
                "/api/scrobble",
                headers={"x-webhook-secret": "super_secret_token"},
                json={
                    "title": "Spirited Away",
                    "year": 2001,
                    "progress": 100.0,
                    "media_type": "movie",
                    "action": "scrobble",
                    "player": "Kodi",
                    "ids": {"imdb": "tt0245429"},
                },
            )
            assert res_scrobble.status_code == 200
            assert res_scrobble.json()["action"] == "mark_watched"
            mock_stop.assert_awaited_once()
            mock_hist.assert_awaited_once()
            # Verify explicit watched_at in history payload
            hist_call_arg = mock_hist.call_args[0][0]
            assert "movies" in hist_call_arg
            assert "watched_at" in hist_call_arg["movies"][0]

        # Episode scrobble with show and season/episode numbers
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
             patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_hist:
            mock_stop.return_value = {"action": "stop"}
            mock_hist.return_value = {"added": {"episodes": 1}}

            res_ep = await client.post(
                "/api/scrobble?token=super_secret_token",
                json={
                    "title": "Severance",
                    "season": 1,
                    "episode": 9,
                    "episode_title": "The We We Are",
                    "progress": 98.0,
                    "media_type": "episode",
                    "action": "scrobble",
                    "player": "Stremio",
                    "ids": {"tmdb": "97951"},
                },
            )
            assert res_ep.status_code == 200
            assert res_ep.json()["action"] == "mark_watched"
            mock_stop.assert_awaited_once()
            mock_hist.assert_awaited_once()

