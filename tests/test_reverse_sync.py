"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_sync_api_endpoints_and_admin_security():
    """Verify /api/sync/* endpoints with admin authentication gating and demo mode simulation."""
    client = make_async_test_client(app)

    # 1. Unauthenticated diff request is rejected
    res = await client.get("/api/sync/diff")
    if Config.WEBHOOK_SECRET:
        assert res.status_code == 401

    # 2. Authenticated status endpoint
    res_status = await client.get("/api/sync/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "configured" in data
    assert "interval_minutes" in data

    # 3. Demo mode diff returns simulated discrepancies without admin token
    res_demo = await client.get("/api/sync/diff?demo=true")
    assert res_demo.status_code == 200
    demo_diff = res_demo.json()
    assert demo_diff["status"] == "ok"
    assert len(demo_diff["diff"]) == 4

    # 4. Admin authenticated diff request
    headers = {"x-webhook-secret": Config.WEBHOOK_SECRET} if Config.WEBHOOK_SECRET else {}
    res_diff = await client.get("/api/sync/diff", headers=headers)
    assert res_diff.status_code == 200
    assert "diff" in res_diff.json()

    # 5. Demo reconcile execution
    res_rec = await client.post("/api/sync/reconcile?demo=true", json={"direction": "all"})
    assert res_rec.status_code == 200
    assert res_rec.json()["status"] == "success"

    # 6. Progress endpoint
    res_prog = await client.get("/api/sync/progress")
    assert res_prog.status_code == 200
    assert "in_progress" in res_prog.json()

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_reconciliation_elements():
    """Verify dashboard HTML includes Two-Way Reconciliation card and modal dialogs."""
    client = make_async_test_client(app)

    res = await client.get("/")
    assert res.status_code == 200
    html_content = res.text + await dashboard_asset_text(client)
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
    res_demo = await client.get("/demo")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_sync_settings_api_and_connection_test():
    """Verify /api/sync/settings and /api/sync/test-connection endpoints."""
    from app.services.settings_manager import settings_mgr
    from app.main import reverse_sync_mgr
    client = make_async_test_client(app)

    orig_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    try:
        with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
            # 1. Unauthenticated -> 401
            res = await client.get("/api/sync/settings")
            assert res.status_code == 401

            res_post = await client.post("/api/sync/settings", json={"interval_minutes": 45})
            assert res_post.status_code == 401

            res_test = await client.post("/api/sync/test-connection", json={"plex_url": "http://mock-plex:32400"})
            assert res_test.status_code == 401

            headers = {"x-webhook-secret": "test_secret"}

            # 2. Connection test missing params when unconfigured -> status unconfigured
            res_bad_test = await client.post("/api/sync/test-connection", headers=headers, json={"url": "", "token": ""})
            assert res_bad_test.status_code == 200
            assert res_bad_test.json().get("status") in ("unconfigured", "error", "unreachable")

            # 3. Authenticated GET /api/sync/settings
            res_get = await client.get("/api/sync/settings", headers=headers)
            assert res_get.status_code == 200
            data = res_get.json()
            assert "interval_minutes" in data
            assert "plex_url" in data

            # 4. Authenticated POST /api/sync/settings
            res_update = await client.post("/api/sync/settings", headers=headers, json={
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
                res_conn = await client.post("/api/sync/test-connection", headers=headers, json={
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_multi_server_sync_settings_and_test_connection_api():
    """Verify settings persistence and test connection for Jellyfin and Emby."""
    from app.services.settings_manager import settings_mgr
    from app.main import reverse_sync_mgr
    client = make_async_test_client(app)

    orig_recon = settings_mgr.get_reconciliation_settings(mask_token=False)
    try:
        with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
            headers = {"x-webhook-secret": "test_secret"}

            # 1. Update Jellyfin & Emby settings
            res = await client.post("/api/sync/settings", headers=headers, json={
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
                res_jf_test = await client.post("/api/sync/test-connection", headers=headers, json={
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
                res_emby_test = await client.post("/api/sync/test-connection", headers=headers, json={
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
            res_clear = await client.post("/api/sync/settings", headers=headers, json={
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

@pytest.mark.asyncio(loop_scope="module")
async def test_cross_sync_api_endpoints():
    """Verify FastAPI route handlers for cross-tracker synchronization."""
    client = make_async_test_client(app)

    # 1. GET /api/cross-sync/status
    res = await client.get("/api/cross-sync/status?demo=true")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert "diff_count" in data

    # 2. GET /api/cross-sync/diff
    demo_diff_res = await client.get("/api/cross-sync/diff?demo=true")
    assert demo_diff_res.status_code == 200
    assert len(demo_diff_res.json()["diff"]) > 0

    if Config.WEBHOOK_SECRET:
        unauth_diff = await client.get("/api/cross-sync/diff")
        assert unauth_diff.status_code == 401

        auth_diff = await client.get(f"/api/cross-sync/diff?token={Config.WEBHOOK_SECRET}")
        assert auth_diff.status_code == 200
    else:
        auth_diff = await client.get("/api/cross-sync/diff")
        assert auth_diff.status_code == 200

    # 3. POST /api/cross-sync/scan
    demo_scan = await client.post("/api/cross-sync/scan?demo=true")
    assert demo_scan.status_code == 200
    assert "diff" in demo_scan.json()

    # 4. POST /api/cross-sync/execute
    demo_exec = await client.post("/api/cross-sync/execute?demo=true", json={"direction": "both"})
    assert demo_exec.status_code == 200
    assert demo_exec.json()["status"] == "completed"

    # 5. GET /api/cross-sync/progress
    prog = await client.get("/api/cross-sync/progress")
    assert prog.status_code == 200
    assert "status" in prog.json()

    provenance = await client.get("/api/cross-sync/provenance?demo=true")
    assert provenance.status_code == 200
    assert provenance.json() == {"records": []}
    if Config.WEBHOOK_SECRET:
        private_provenance = await client.get("/api/cross-sync/provenance")
        assert private_provenance.status_code == 401

@pytest.mark.asyncio(loop_scope="module")
async def test_reconciliation_preview_is_read_only_and_apply_uses_preview():
    from app.main import reverse_sync_mgr
    client = make_async_test_client(app)
    discrepancy = {
        "id": "plex:movie:42", "server": "plex", "type": "movie", "title": "Example",
        "year": 2020, "ids": {}, "status": "plex_only", "action_recommended": "sync_to_trakt",
        "server_watched": True, "trakt_watched": False, "server_rating": None,
    }
    with patch.object(Config, "WEBHOOK_SECRET", "preview_secret"), \
         patch.object(reverse_sync_mgr, "scan_discrepancies", new_callable=AsyncMock, return_value=[discrepancy]) as scan, \
         patch.object(reverse_sync_mgr, "execute_reconciliation", new_callable=AsyncMock,
                      return_value={"status": "success", "reconciled": 1, "failed": 0}) as execute:
        denied = await client.post("/api/sync/reconcile/preview", json={"direction": "all"})
        assert denied.status_code == 401
        client.cookies.set("admin_token", "preview_secret")
        preview_response = await client.post("/api/sync/reconcile/preview", json={"direction": "all", "server": "plex"})
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["total"] == 1
        assert preview["items"][0]["match_confidence"] == "title-based"
        assert len(preview["warnings"]) == 1
        execute.assert_not_awaited()
        assert scan.await_args.kwargs["force"] is True
        applied = await client.post("/api/sync/reconcile", json={"preview_id": preview["preview_id"], "server": "plex"})
        assert applied.status_code == 200
        assert execute.await_args.kwargs["preview_items"] == preview["items"]

@pytest.mark.asyncio(loop_scope="module")
async def test_multi_server_mirroring_and_loop_prevention(tmp_path):
    """Verify multi-server watched status mirroring and loop suppression."""
    from app.clients.plex_api_client import PlexApiClient
    from app.clients.jellyfin_api_client import JellyfinApiClient
    from app.clients.emby_api_client import EmbyApiClient
    from app.plex_parser import ParsedMedia
    from app.services.loop_prevention import LoopPreventionManager
    from app.services.reverse_sync_manager import ReverseSyncManager
    from app.services.settings_manager import SettingsManager

    sm_file = tmp_path / "settings_mirror.json"
    sm = SettingsManager(settings_file=sm_file)
    sm.set_server_enabled("plex", True)
    sm.set_server_enabled("jellyfin", True)
    lp = LoopPreventionManager()

    mock_plex = MagicMock(spec=PlexApiClient)
    mock_plex.is_configured.return_value = True
    mock_plex.find_item = AsyncMock(return_value={"rating_key": "12345", "title": "Inception"})
    mock_plex.mark_as_watched = AsyncMock(return_value=True)

    mock_jf = MagicMock(spec=JellyfinApiClient)
    mock_jf.is_configured.return_value = True
    mock_jf.find_item = AsyncMock(return_value={"rating_key": "jf-9999", "title": "Inception"})
    mock_jf.mark_as_watched = AsyncMock(return_value=True)

    mock_emby = MagicMock(spec=EmbyApiClient)
    mock_emby.is_configured.return_value = False

    rsm = ReverseSyncManager(
        plex_client=mock_plex,
        jellyfin_client=mock_jf,
        emby_client=mock_emby,
        loop_prevention_mgr=lp,
    )

    media = ParsedMedia(
        event="media.scrobble",
        media_type="movie",
        title="Inception",
        year=2010,
        progress=100.0,
        rating_key="plex-item-1",
        username="testuser",
        ids={"imdb": "tt1375666", "tmdb": "27205"},
    )

    # 1. Disabled mirroring -> skipped
    sm.set_multi_server_mirroring(False)
    with patch("app.services.reverse_sync_manager.settings_mgr", sm):
        res_disabled = await rsm.mirror_watched_status(media, source_server="plex")
        assert res_disabled["status"] == "skipped"

    # 2. Enabled mirroring -> mirrors from Plex to Jellyfin
    sm.set_multi_server_mirroring(True)
    with patch("app.services.reverse_sync_manager.settings_mgr", sm):
        res_enabled = await rsm.mirror_watched_status(media, source_server="plex")
        assert res_enabled["status"] == "completed"
        assert len(res_enabled["results"]["mirrored"]) == 1
        assert res_enabled["results"]["mirrored"][0]["server"] == "jellyfin"
        assert res_enabled["results"]["mirrored"][0]["rating_key"] == "jf-9999"

        # Verify loop prevention suppressed echo keys
        assert lp.is_ignored("jf-9999") is True
        assert lp.is_ignored("tt1375666") is True
        assert lp.is_ignored("27205") is True

        mock_jf.find_item.assert_awaited_once()
        mock_jf.mark_as_watched.assert_awaited_once_with("jf-9999")
        # Plex should NOT be mirrored since it is the source server
        mock_plex.mark_as_watched.assert_not_called()

@pytest.mark.asyncio(loop_scope="module")
async def test_find_item_plex_and_mediabrowser():
    """Verify find_item API searches across Plex and Jellyfin/Emby clients."""
    from app.clients.plex_api_client import PlexApiClient
    from app.clients.mediabrowser_api_client import BaseMediaBrowserClient
    from app.plex_parser import ParsedMedia
    import httpx

    media_movie = ParsedMedia(
        event="media.scrobble",
        media_type="movie",
        title="The Dark Knight",
        year=2008,
        username="testuser",
        ids={"imdb": "tt0468569"},
    )

    # 1. Plex find_item
    plex_client = PlexApiClient(base_url="http://mock-plex:32400", token="mock-token")
    mock_plex_resp = MagicMock(spec=httpx.Response)
    mock_plex_resp.status_code = 200
    mock_plex_resp.json.return_value = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "8888",
                    "title": "The Dark Knight",
                    "year": 2008,
                    "Guid": [{"id": "imdb://tt0468569"}],
                }
            ]
        }
    }
    with patch.object(plex_client, "get_client") as mock_http:
        mock_client_inst = AsyncMock()
        mock_client_inst.get = AsyncMock(return_value=mock_plex_resp)
        mock_http.return_value = mock_client_inst

        found = await plex_client.find_item(media_movie)
        assert found is not None
        assert found["rating_key"] == "8888"
        assert found["title"] == "The Dark Knight"

    # 2. Jellyfin find_item
    jf_client = BaseMediaBrowserClient(base_url="http://mock-jf:8096", token="mock-token", user_id="user1")
    mock_jf_resp = MagicMock(spec=httpx.Response)
    mock_jf_resp.status_code = 200
    mock_jf_resp.json.return_value = {
        "Items": [
            {
                "Id": "jf-item-777",
                "Name": "The Dark Knight",
                "ProductionYear": 2008,
                "ProviderIds": {"Imdb": "tt0468569"},
            }
        ]
    }
    with patch.object(jf_client, "get_client") as mock_http:
        mock_client_inst = AsyncMock()
        mock_client_inst.get = AsyncMock(return_value=mock_jf_resp)
        mock_http.return_value = mock_client_inst

        found_jf = await jf_client.find_item(media_movie)
        assert found_jf is not None
        assert found_jf["rating_key"] == "jf-item-777"
        assert found_jf["title"] == "The Dark Knight"

@pytest.mark.asyncio(loop_scope="module")
async def test_media_server_webhook_auto_registration():
    """Verify 1-click webhook registration for Plex, Jellyfin, and Emby."""
    from app.clients.plex_api_client import PlexApiClient
    from app.clients.jellyfin_api_client import JellyfinApiClient
    from app.clients.emby_api_client import EmbyApiClient
    from app.services.reverse_sync_manager import ReverseSyncManager

    # 1. Plex webhook registration
    plex = PlexApiClient(base_url="http://mock-plex:32400", token="valid_plex_token")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_plex_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_plex_post:
        mock_plex_get.return_value = httpx.Response(200, json=[])
        mock_plex_post.return_value = httpx.Response(201, json={"status": "created"})
        res_plex = await plex.register_webhook("http://omniscrobble:8000/webhook/plex")
        assert res_plex["success"] is True
        assert "Successfully registered" in res_plex["message"]
        mock_plex_post.assert_awaited_once()
        call_args, call_kwargs = mock_plex_post.await_args
        assert call_args[0] == "https://plex.tv/api/v2/user/webhooks"
        assert call_kwargs["json"] == {"url": "http://omniscrobble:8000/webhook/plex"}
        assert call_kwargs["headers"]["X-Plex-Token"] == "valid_plex_token"
    await plex.close()

    # 2. Jellyfin webhook registration (Plugin config update)
    jf = JellyfinApiClient(base_url="http://mock-jellyfin:8096", token="jf_token", user_id="jf_user")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_jf_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_jf_post:
        # Mock Plugins list
        mock_jf_get.side_effect = [
            httpx.Response(200, json=[{"Name": "Webhook", "Id": "plugin-webhook-guid-123"}]),
            httpx.Response(200, json={"Webhooks": []}),
        ]
        mock_jf_post.return_value = httpx.Response(204)

        res_jf = await jf.register_webhook("http://omniscrobble:8000/webhook/jellyfin")
        assert res_jf["success"] is True
        assert "Successfully registered" in res_jf["message"]
        assert mock_jf_post.await_count == 1
    await jf.close()

    # 3. Emby webhook registration (/Webhooks endpoint)
    emby = EmbyApiClient(base_url="http://mock-emby:8096", token="emby_token", user_id="emby_user")
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_emby_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_emby_post:
        mock_emby_get.return_value = httpx.Response(200, json=[])
        mock_emby_post.return_value = httpx.Response(200, json={"Id": "dest-123"})
        res_emby = await emby.register_webhook("http://omniscrobble:8000/webhook/emby")
        assert res_emby["success"] is True
        assert "Successfully registered" in res_emby["message"]
        mock_emby_post.assert_awaited_once()
    await emby.close()

    # 4. ReverseSyncManager delegation
    mgr = ReverseSyncManager()
    with patch.object(mgr.plex, "is_configured", return_value=True), \
         patch.object(mgr.plex, "register_webhook", new_callable=AsyncMock) as mock_reg:
        mock_reg.return_value = {"success": True, "message": "Plex success"}
        res = await mgr.register_webhook("plex", "http://test-webhook")
        assert res.get("success") is True
        assert res.get("message") == "Plex success"

