"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
async def test_trakt_sync_watchlist():
    """Verify TraktClient.sync_watchlist posts payload to /sync/watchlist."""
    with patch.object(trakt, "_post_authenticated", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"added": {"movies": 1, "shows": 0}}
        res = await trakt.sync_watchlist({"movies": [{"title": "Dune", "year": 2021}]})
        mock_post.assert_awaited_once_with(f"{trakt.api_url}/sync/watchlist", {"movies": [{"title": "Dune", "year": 2021}]})
        assert res["added"]["movies"] == 1

@pytest.mark.asyncio(loop_scope="module")
async def test_watchlist_api_endpoints():
    """Test POST /api/watchlist for adding movies and shows to Trakt watchlist."""
    client = make_async_test_client(app)

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "sync_watchlist", new_callable=AsyncMock) as mock_sync:
        mock_sync.return_value = {"added": {"shows": 1, "movies": 1}}

        # 1. Demo mode
        resp_demo = await client.post("/api/watchlist?demo=true", json={"media_type": "movie", "title": "Inception"})
        assert resp_demo.status_code == 200
        assert resp_demo.json()["status"] == "success"

        # 2. Authenticated admin request
        resp = await client.post("/api/watchlist", json={"media_type": "show", "title": "Severance", "year": 2022})
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_sync.assert_awaited_once()

        # 3. Unauthorized access check when WEBHOOK_SECRET is active
        orig_secret = Config.WEBHOOK_SECRET
        try:
            Config.WEBHOOK_SECRET = "secret_pass_123"
            resp_unauth = await client.post("/api/watchlist", json={"media_type": "movie", "title": "Dune"})
            assert resp_unauth.status_code == 401

            resp_auth = await client.post("/api/watchlist?token=secret_pass_123", json={"media_type": "movie", "title": "Dune"})
            assert resp_auth.status_code == 200
        finally:
            Config.WEBHOOK_SECRET = orig_secret

