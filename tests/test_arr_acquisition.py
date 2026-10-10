"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_sonarr_api_routes():
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # 1. /api/sonarr/shows requires admin
        client.cookies.clear()
        res_unauth = await client.get("/api/sonarr/shows")
        assert res_unauth.status_code == 401

        # 2. /api/sonarr/shows with admin
        client.cookies.set("admin_token", "testsecret")
        res_auth = await client.get("/api/sonarr/shows")
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
            res_filtered = await client.get("/api/sonarr/shows?q=sev")
            assert res_filtered.status_code == 200
            mock_search.assert_called_once_with(query="sev", limit=15, exclude=["The Bear"])

        # 3. GET /sonarr and GET /radarr info
        assert (await client.get("/sonarr")).status_code == 200
        assert (await client.get("/radarr")).status_code == 200

        # 4. POST /sonarr unauthorized
        res_sonarr_unauth = await client.post("/sonarr", json={"eventType": "Test"})
        assert res_sonarr_unauth.status_code == 401

        # 5. POST /sonarr authorized test event
        res_sonarr_test = await client.post("/sonarr?token=testsecret", json={"eventType": "Test"})
        assert res_sonarr_test.status_code == 200
        assert res_sonarr_test.json()["status"] == "success"

        # 6. POST /radarr authorized test event
        res_radarr_test = await client.post("/radarr?token=testsecret", json={"eventType": "Test"})
        assert res_radarr_test.status_code == 200
        assert res_radarr_test.json()["status"] == "success"

        # 7. Health check includes sonarr
        res_health = await client.get("/health")
        assert res_health.status_code == 200
        assert "sonarr" in res_health.json()

        client.cookies.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_sonarr_webhook_collection_sync():
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
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", ""):
        with patch("app.main.user_mgr.get_client") as mock_get_client:
            mock_trakt = AsyncMock()
            mock_trakt.sync_collection.return_value = {"added": {"episodes": 1}}
            mock_get_client.return_value = mock_trakt

            res = await client.post("/sonarr", json=dl_payload)
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

@pytest.mark.asyncio(loop_scope="module")
async def test_health_includes_radarr():
    """Verify /health reports Radarr integration info alongside Sonarr."""
    client = make_async_test_client(app)
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "radarr" in data
    assert data["radarr"]["endpoint"] == "/radarr"
    assert "sonarr" in data

@pytest.mark.asyncio(loop_scope="module")
async def test_radarr_webhook_collection_sync():
    """Verify Radarr download webhooks sync movie to Trakt collection and increment stats."""
    client = make_async_test_client(app)
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

        res = await client.post("/radarr", json=dl_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "collection"
        assert mock_sync.called
        call_arg = mock_sync.call_args[0][0]
        assert call_arg["movies"][0]["title"] == "Dune: Part Two"
        assert call_arg["movies"][0]["resolution"] == "uhd_4k"
        assert scrobble_stats["collections"] == initial_collections + 1

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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
    # Reordered: Media Servers (Plex, Jellyfin, Emby) -> Requests (Overseerr) -> Acquisition Engines (Sonarr, Radarr)
    assert server_ids == ["plex", "jellyfin", "emby", "overseerr", "sonarr", "radarr"]

    # Live ecosystem
    live_eco = await arr_bridge.get_ecosystem_status(demo=False)
    assert "servers" in live_eco
    assert len(live_eco["servers"]) >= 3

    # Verify that enabled items precede disabled items in live ecosystem
    disabled_seen = False
    for s in live_eco["servers"]:
        is_dis = not s.get("enabled", True) or s.get("status") == "disabled" or s.get("badge") in ("Disabled", "Paused")
        if is_dis:
            disabled_seen = True
        else:
            assert not disabled_seen, f"Active server {s['id']} appeared after disabled servers"

@pytest.mark.asyncio(loop_scope="module")
async def test_ecosystem_reordering_and_disabled_sorting():
    """Verify ecosystem reordering (Servers -> Arr) and disabled items moving to end."""
    from app.services.arr_bridge import arr_bridge
    from app.services.settings_manager import settings_mgr

    # 1. Verify demo mode canonical order
    demo_res = await arr_bridge.get_ecosystem_status(demo=True)
    demo_ids = [s["id"] for s in demo_res["servers"]]
    assert demo_ids == ["plex", "jellyfin", "emby", "overseerr", "sonarr", "radarr"]

    # 2. Verify live mode ordering with toggled settings
    orig_jellyfin = settings_mgr.is_server_enabled("jellyfin")
    try:
        settings_mgr.set_server_enabled("jellyfin", False)

        from app.main import reverse_sync_mgr
        live_res = await arr_bridge.get_ecosystem_status(
            demo=False,
            plex_client=reverse_sync_mgr.plex,
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

        # Jellyfin must be in disabled_ids
        assert "jellyfin" in disabled_ids

        # All active items must precede all disabled items in the overall server list
        full_ids = [s["id"] for s in servers]
        assert full_ids == active_ids + disabled_ids

        # Within disabled items, ordering should remain Servers -> Requests -> Arr
        service_order = ["plex", "jellyfin", "emby", "overseerr", "sonarr", "radarr"]
        disabled_indices = [service_order.index(sid) for sid in disabled_ids if sid in service_order]
        assert disabled_indices == sorted(disabled_indices)
    finally:
        settings_mgr.set_server_enabled("jellyfin", orig_jellyfin)

@pytest.mark.asyncio(loop_scope="module")
async def test_arr_api_endpoints_and_auth():
    """Test /api/arr/status, /api/arr/sync, and /api/ecosystem routes."""
    client = make_async_test_client(app)

    # 1. /api/arr/status
    res = await client.get("/api/arr/status")
    assert res.status_code == 200
    assert "configured" in res.json()

    res_demo = await client.get("/api/arr/status?demo=true")
    assert res_demo.status_code == 200
    assert res_demo.json()["sonarr_series_count"] == 48

    # 2. /api/ecosystem
    res_eco = await client.get("/api/ecosystem")
    assert res_eco.status_code == 200
    assert "servers" in res_eco.json()

    res_eco_demo = await client.get("/api/ecosystem?demo=true")
    assert res_eco_demo.status_code == 200
    assert res_eco_demo.json()["healthy_count"] == 6
    demo_api_ids = [s["id"] for s in res_eco_demo.json()["servers"]]
    assert demo_api_ids == ["plex", "jellyfin", "emby", "overseerr", "sonarr", "radarr"]

    # 3. /api/arr/sync
    # Demo execution allowed without auth
    res_sync_demo = await client.post("/api/arr/sync?demo=true")
    assert res_sync_demo.status_code == 200
    assert "added" in res_sync_demo.json()

    # Non-demo requires admin
    if Config.WEBHOOK_SECRET:
        res_sync_unauth = await client.post("/api/arr/sync")
        assert res_sync_unauth.status_code == 401

        # Admin authorized
        headers = {"x-webhook-secret": Config.WEBHOOK_SECRET}
        res_sync_auth = await client.post("/api/arr/sync", headers=headers)
        assert res_sync_auth.status_code == 200

@pytest.mark.asyncio(loop_scope="module")
async def test_interactive_arr_acquisition_endpoints(monkeypatch):
    """Interactive acquisition is admin-only, protected by CSRF, and co-watch defaults off."""
    import app.main as main_module
    client = make_async_test_client(app)
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "arr-admin-secret")
    monkeypatch.setattr(Config, "CO_WATCH_USER", "partner_user")
    monkeypatch.setitem(main_module._arr_acquisition_config_cache, "expires_at", 0.0)
    monkeypatch.setitem(main_module._arr_acquisition_config_cache, "data", None)
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "base_url", "http://sonarr.test")
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "api_key", "test-key")
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "lookup_series", AsyncMock(return_value=[{
        "title": "Example Series", "year": 2024, "tvdbId": 123, "imdbId": "tt123",
        "overview": "A sample series", "images": [],
    }]))
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "has_series", AsyncMock(return_value=False))
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "get_root_folders", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "get_quality_profiles", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.arr_bridge.radarr, "base_url", "")
    monkeypatch.setattr(main_module.arr_bridge.radarr, "api_key", "")

    denied = await client.get("/api/arr/lookup?type=series&term=Example")
    assert denied.status_code == 401
    auth = {"x-webhook-secret": "arr-admin-secret"}
    found = await client.get("/api/arr/lookup?type=series&term=Example", headers=auth)
    assert found.status_code == 200
    assert found.json()["results"][0]["title"] == "Example Series"
    assert found.json()["results"][0]["in_library"] is False
    assert (await client.get("/api/arr/lookup?type=other&term=x", headers=auth)).status_code == 400

    # Even an authenticated admin must supply a matching double-submit CSRF token.
    body = {"type": "series", "item_data": {"title": "Example Series"}}
    no_csrf = await client.post("/api/arr/add", json=body, headers=auth)
    assert no_csrf.status_code == 403
    client.cookies.set("csrf_token", "csrf-test")
    monkeypatch.setattr(main_module.arr_bridge.sonarr, "add_series", AsyncMock(return_value={"success": True, "data": {"id": 42}}))
    add = await client.post("/api/arr/add", json=body, headers={**auth, "x-csrf-token": "csrf-test"})
    assert add.status_code == 200
    assert add.json()["cowatch_enrolled"] is False
    main_module.arr_bridge.sonarr.add_series.assert_awaited_once()
    assert not add.json().get("enable_cowatch", False)

    with patch.object(main_module.cowatch_mgr, "add_show", return_value=["Example Series"]) as enroll:
        opted_in = await client.post("/api/arr/add", json={**body, "enable_cowatch": True},
                                     headers={**auth, "x-csrf-token": "csrf-test"})
        assert opted_in.status_code == 200
        assert opted_in.json()["cowatch_enrolled"] is True
        enroll.assert_called_once_with("Example Series")

    monkeypatch.setattr(Config, "CO_WATCH_USER", "")
    no_partner = await client.post("/api/arr/add", json={**body, "enable_cowatch": True},
                                   headers={**auth, "x-csrf-token": "csrf-test"})
    assert no_partner.status_code == 400
    assert "configured partner" in no_partner.json()["detail"]

    config = await client.get("/api/arr/config", headers=auth)
    assert config.status_code == 200
    assert "sonarr" in config.json() and "radarr" in config.json()
    assert "co_watch_user" in config.json()
    await client.get("/api/arr/config", headers=auth)
    main_module.arr_bridge.sonarr.get_root_folders.assert_awaited_once()
    main_module.arr_bridge.sonarr.get_quality_profiles.assert_awaited_once()

    # Radarr lookup/add is supported; the co-watch flag has no effect on movies.
    monkeypatch.setattr(main_module.arr_bridge.radarr, "base_url", "http://radarr.test")
    monkeypatch.setattr(main_module.arr_bridge.radarr, "api_key", "test-key")
    monkeypatch.setattr(main_module.arr_bridge.radarr, "lookup_movie", AsyncMock(return_value=[{"title": "Example Movie", "year": 2025, "tmdbId": 456}]))
    monkeypatch.setattr(main_module.arr_bridge.radarr, "has_movie", AsyncMock(return_value=False))
    movie_lookup = await client.get("/api/arr/lookup?type=movie&term=Example", headers=auth)
    assert movie_lookup.status_code == 200
    assert movie_lookup.json()["results"][0]["title"] == "Example Movie"
    monkeypatch.setattr(main_module.arr_bridge.radarr, "add_movie", AsyncMock(return_value={"success": True, "data": {"id": 8}}))
    movie_body = {"type": "movie", "item_data": {"title": "Example Movie"}, "enable_cowatch": True}
    with patch.object(main_module.cowatch_mgr, "add_show") as enroll_movie:
        movie_add = await client.post("/api/arr/add", json=movie_body,
                                      headers={**auth, "x-csrf-token": "csrf-test"})
        assert movie_add.status_code == 200
        assert movie_add.json()["cowatch_enrolled"] is False
        enroll_movie.assert_not_called()
    await client.aclose()

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_arr_and_ecosystem_cards():
    """Verify Multi-Server Ecosystem and Arr Bridge cards appear on dashboard."""
    client = make_async_test_client(app)

    res = await client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "Multi-Server Ecosystem" in html
    assert "Content Bridge" in html
    assert 'id="arr-modal"' in html
    assert "eco-card" in html

    res_demo = await client.get("/demo")
    assert res_demo.status_code == 200
    assert "Multi-Server Ecosystem" in res_demo.text
    assert "Content Bridge" in res_demo.text
    assert "eco-card" in res_demo.text

@pytest.mark.asyncio(loop_scope="module")
async def test_arr_test_connection_endpoint(monkeypatch):
    """Verify POST /api/arr/test-connection for Sonarr and Radarr with demo and mocked connectivity."""
    from app.clients.sonarr_client import SonarrClient
    from app.clients.radarr_client import RadarrClient
    client = make_async_test_client(app)

    # 1. Demo mode returns simulated success
    demo_res = await client.post("/api/arr/test-connection?demo=true", json={"app": "sonarr"})
    assert demo_res.status_code == 200
    assert demo_res.json()["status"] == "connected"
    assert demo_res.json()["app"] == "sonarr"

    demo_radarr = await client.post("/api/arr/test-connection?demo=true", json={"app": "radarr"})
    assert demo_radarr.status_code == 200
    assert demo_radarr.json()["status"] == "connected"
    assert demo_radarr.json()["app"] == "radarr"

    # 2. Auth protection
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "pwd12345")
    unauth_res = await client.post("/api/arr/test-connection", json={"app": "sonarr"})
    assert unauth_res.status_code == 401

    # 3. As admin with mock
    client.cookies.set("admin_token", "pwd12345")

    with patch.object(SonarrClient, "check_connection", new_callable=AsyncMock) as mock_sonarr:
        mock_sonarr.return_value = {"status": "connected", "version": "4.0.9"}
        s_res = await client.post("/api/arr/test-connection", json={"app": "sonarr", "url": "http://127.0.0.1:8989", "api_key": "testkey"})
        assert s_res.status_code == 200
        assert s_res.json()["status"] == "connected"

    with patch.object(RadarrClient, "check_connection", new_callable=AsyncMock) as mock_radarr:
        mock_radarr.return_value = {"status": "connected", "version": "5.9.1"}
        r_res = await client.post("/api/arr/test-connection", json={"app": "radarr", "url": "http://127.0.0.1:7878", "api_key": "testkey"})
        assert r_res.status_code == 200
        assert r_res.json()["status"] == "connected"

    # 4. Invalid app type returns 400
    bad_res = await client.post("/api/arr/test-connection", json={"app": "lidarr"})
    assert bad_res.status_code == 400
    assert "Invalid app" in bad_res.json()["detail"]

@pytest.mark.asyncio(loop_scope="module")
async def test_overseerr_client_full():
    """Verify OverseerrClient methods: check_connection, get_request_counts, search, has_media, request_media."""
    from app.clients.overseerr_client import OverseerrClient

    client = OverseerrClient(base_url="http://mock-overseerr:5055", api_key="valid_api_key")
    assert client.is_configured is True

    # 1. check_connection success
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json={"version": "1.33.2"})
        res = await client.check_connection()
        assert res["status"] == "connected"
        assert res["version"] == "1.33.2"

        # check_connection 401
        mock_get.return_value = httpx.Response(401, json={"message": "Unauthorized"})
        res_unauth = await client.check_connection()
        assert res_unauth["status"] == "error"

        # check_connection exception
        mock_get.side_effect = httpx.ConnectError("Connection refused")
        res_err = await client.check_connection()
        assert res_err["status"] == "error"
        assert "Connection refused" in res_err["message"]

    # 2. get_request_counts
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json={
            "total": 15, "movie": 7, "tv": 8, "pending": 3, "approved": 10, "available": 2
        })
        counts = await client.get_request_counts()
        assert counts["total"] == 15
        assert counts["pending"] == 3
        assert counts["available"] == 2

    # 3. search
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json={
            "page": 1, "totalPages": 1, "totalResults": 1,
            "results": [{"id": 693134, "mediaType": "movie", "title": "Dune: Part Two"}]
        })
        results = await client.search("Dune")
        assert len(results) == 1
        assert results[0]["id"] == 693134

    # 4. has_media
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        # Status 5: available
        mock_get.return_value = httpx.Response(200, json={"id": 693134, "mediaInfo": {"status": 5}})
        exists = await client.has_media("movie", 693134)
        assert exists is True

        # Status 3: processing
        mock_get.return_value = httpx.Response(200, json={"id": 693134, "mediaInfo": {"status": 3}})
        exists = await client.has_media("movie", 693134)
        assert exists is True

        # Not requested
        mock_get.return_value = httpx.Response(200, json={"id": 693134, "mediaInfo": None})
        exists = await client.has_media("movie", 693134)
        assert exists is False

        # 404 not found
        mock_get.return_value = httpx.Response(404, json={"message": "Not found"})
        exists = await client.has_media("movie", 999999)
        assert exists is False

    # 5. request_media
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(201, json={"id": 42, "status": 1})
        # Movie request
        req_res = await client.request_media("movie", 693134)
        assert req_res.get("success") is True
        assert req_res.get("request", {})["id"] == 42
        mock_post.assert_awaited_with(
            "http://mock-overseerr:5055/api/v1/request",
            json={"mediaType": "movie", "mediaId": 693134, "is4k": False},
            headers={"X-Api-Key": "valid_api_key", "Accept": "application/json", "Content-Type": "application/json"}
        )

        # TV request with specific seasons
        req_res_tv = await client.request_media("tv", 97951, seasons=[1, 2])
        assert req_res_tv.get("success") is True
        mock_post.assert_awaited_with(
            "http://mock-overseerr:5055/api/v1/request",
            json={"mediaType": "tv", "mediaId": 97951, "is4k": False, "seasons": [1, 2]},
            headers={"X-Api-Key": "valid_api_key", "Accept": "application/json", "Content-Type": "application/json"}
        )

    await client.close()

@pytest.mark.asyncio(loop_scope="module")
async def test_arr_bridge_watchlist_sync_with_overseerr():
    """Verify that when Overseerr is enabled in ArrBridgeManager, Watchlist items route to Overseerr."""
    from app.services.arr_bridge import ArrBridgeManager
    from app.clients.sonarr_client import SonarrClient
    from app.clients.radarr_client import RadarrClient
    from app.clients.overseerr_client import OverseerrClient
    from app.services.settings_manager import settings_mgr

    sonarr_c = SonarrClient(base_url="http://mock-sonarr:8989", api_key="sonarr_key")
    radarr_c = RadarrClient(base_url="http://mock-radarr:7878", api_key="radarr_key")
    overseerr_c = OverseerrClient(base_url="http://mock-overseerr:5055", api_key="overseerr_key")

    class MockTraktClient:
        def is_authenticated(self):
            return True

        async def get_watchlist(self, media_type: str = "movies"):
            if media_type == "movies":
                return [
                    {
                        "type": "movie",
                        "movie": {
                            "title": "Dune: Part Two",
                            "year": 2024,
                            "ids": {"tmdb": 693134, "imdb": "tt15239678"},
                        },
                    }
                ]
            else:
                return [
                    {
                        "type": "show",
                        "show": {
                            "title": "Severance",
                            "year": 2022,
                            "ids": {"tmdb": 97951, "tvdb": 371980},
                        },
                    }
                ]

    bridge = ArrBridgeManager(
        sonarr_client=sonarr_c,
        radarr_client=radarr_c,
        overseerr_client=overseerr_c,
        trakt_client=MockTraktClient(),
    )

    # Enable Overseerr via settings_mgr
    with patch.object(settings_mgr, "is_overseerr_enabled", return_value=True), \
         patch.object(bridge.overseerr, "has_media", new_callable=AsyncMock) as mock_has, \
         patch.object(bridge.overseerr, "request_media", new_callable=AsyncMock) as mock_req, \
         patch.object(bridge.radarr, "has_movie", new_callable=AsyncMock) as mock_radarr_has, \
         patch.object(bridge.sonarr, "has_series", new_callable=AsyncMock) as mock_sonarr_has:

        mock_has.return_value = False
        mock_req.return_value = {"success": True, "request": {"id": 101}}

        stats = await bridge.sync_watchlist(demo=False)

        assert stats["added"]["movies"] == 1
        assert stats["added"]["shows"] == 1
        assert len(stats["items"]) == 2
        assert stats["items"][0]["app"] == "Overseerr"
        assert stats["items"][1]["app"] == "Overseerr"

        # Overseerr was invoked
        assert mock_req.await_count == 2
        # Direct Radarr and Sonarr were NOT invoked because Overseerr handled both
        mock_radarr_has.assert_not_awaited()
        mock_sonarr_has.assert_not_awaited()
    await sonarr_c.close()
    await radarr_c.close()
    await overseerr_c.close()

@pytest.mark.asyncio(loop_scope="module")
async def test_arr_test_connection_endpoint_overseerr():
    """Verify POST /api/arr/test-connection supports 'overseerr'."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "test_admin_secret"):
        client.cookies.set("admin_token", "test_admin_secret")

        # Demo mode
        res_demo = await client.post(
            "/api/arr/test-connection?demo=true",
            json={"app": "overseerr", "url": "http://mock-overseerr:5055", "api_key": "any_key"}
        )
        assert res_demo.status_code == 200
        assert res_demo.json()["status"] == "connected"
        assert res_demo.json()["app"] == "overseerr"
        assert res_demo.json()["version"] == "1.33.2"

        # Live mode with mock
        with patch("app.clients.overseerr_client.OverseerrClient.check_connection", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = {"status": "connected", "version": "1.33.2", "app_name": "Overseerr"}
            res_live = await client.post(
                "/api/arr/test-connection",
                json={"app": "overseerr", "url": "http://127.0.0.1:5055", "api_key": "valid_key"}
            )
            assert res_live.status_code == 200
            assert res_live.json()["status"] == "connected"
            assert res_live.json()["version"] == "1.33.2"

