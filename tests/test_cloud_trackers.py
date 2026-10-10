"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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
    assert reg["trakt"]["capabilities"] == ["realtime_playback", "history", "progress", "ratings", "watchlist", "collection", "search"]
    assert reg["simkl"]["supports_watchlist"] is False
    assert reg["mdblist"]["supports_ratings"] is False
    assert reg["anilist"]["supports_progress"] is True
    reg["trakt"]["capabilities"].clear()
    assert "history" in mt.get_registered_trackers()["trakt"]["capabilities"]

    status = await mt.get_status()
    assert "categories" in status
    assert status["categories"]["universal"] == ["trakt", "simkl", "tmdb"]
    assert status["categories"]["anime"] == ["anilist", "myanimelist", "kitsu"]
    assert status["categories"]["social_diary"] == ["letterboxd", "serializd"]
    assert status["categories"]["lists_ratings"] == ["mdblist"]
    assert "trackers" in status
    assert len(status["trackers"]) == 9
    assert "collection" in status["trackers"]["trakt"]["capabilities"]
    assert status["trackers"]["simkl"]["supports_watchlist"] is False
    assert status["trackers"]["mdblist"]["supports_ratings"] is False

    await mt.close()

@pytest.mark.asyncio(loop_scope="module")
async def test_tracker_capability_skip_reason_is_recorded_in_delivery_details():
    from app.main import _delivery_category, _initial_delivery_details, _tracker_delivery_from_result
    from app.services.multi_tracker import MultiTrackerManager
    manager = MultiTrackerManager()
    media = ParsedMedia(event="media.play", media_type="movie", title="Capability Test", username="tester", progress=1)
    result = await manager.dispatch_scrobble(
        action="start", media=media, trakt_client=MagicMock(), progress=1,
        selected_trackers=["letterboxd"],
    )
    assert result["skipped"]["letterboxd"]["reason"] == "This tracker does not support live playback updates."
    delivery = _tracker_delivery_from_result(result, "manual_start")
    assert delivery["letterboxd"] == "skipped"
    details = _initial_delivery_details(result, delivery)
    assert details["letterboxd"]["reason"] == result["skipped"]["letterboxd"]["reason"]
    assert _delivery_category("skipped", result["skipped"]["letterboxd"]) == "unsupported"
    await manager.close()

@pytest.mark.asyncio(loop_scope="module")
async def test_cloud_tracker_api_endpoints():
    """Verify HTTP API endpoints for all cloud trackers, Letterboxd CSV export, and relay."""
    client = make_async_test_client(app)

    # 1. Structured Multi-Tracker status
    res = await client.get("/api/trackers/status")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "universal" in data["categories"]
    assert "anime" in data["categories"]
    assert "social_diary" in data["categories"]
    assert "lists_ratings" in data["categories"]
    assert "trackers" in data

    # 2. TMDb status
    res_tmdb = await client.get("/api/tmdb/status")
    assert res_tmdb.status_code == 200
    assert "status" in res_tmdb.json()

    # 3. Kitsu status
    res_kitsu = await client.get("/api/kitsu/status")
    assert res_kitsu.status_code == 200
    assert "status" in res_kitsu.json()

    # 4. Letterboxd status & diary
    res_lb = await client.get("/api/letterboxd/status")
    assert res_lb.status_code == 200

    res_diary = await client.get("/api/letterboxd/diary")
    assert res_diary.status_code == 200
    assert isinstance(res_diary.json(), list)

    res_export = await client.get("/api/letterboxd/export")
    assert res_export.status_code == 200
    assert "text/csv" in res_export.headers.get("content-type", "")
    assert "Title,Year,WatchedDate" in res_export.text

    # 5. Serializd status
    res_ser = await client.get("/api/serializd/status")
    assert res_ser.status_code == 200

    # 6. MDBList status & ratings endpoint (demo and unconfigured behaviors)
    res_mdb = await client.get("/api/mdblist/status")
    assert res_mdb.status_code == 200

    res_mdb_r = await client.get("/api/mdblist/ratings?demo=true")
    assert res_mdb_r.status_code == 200
    assert res_mdb_r.json().get("title") == "Dune: Part Two"

    # 7. Relay status
    res_relay = await client.get("/api/relay/status")
    assert res_relay.status_code == 200
    relay_data = res_relay.json()
    assert "SeriesGuide" in relay_data.get("supported_apps", [])
    assert "Showly" in relay_data.get("supported_apps", [])

@pytest.mark.asyncio(loop_scope="module")
async def test_cloud_tracker_security_and_privacy(monkeypatch, tmp_path):
    """Verify admin authorization, privacy masking, and backup inclusion for cloud trackers."""
    from app.main import app
    from app.config import Config
    import app.main as main_mod

    client = make_async_test_client(app)

    # 1. When WEBHOOK_SECRET is set, non-admin requests to /api/letterboxd/export and /api/letterboxd/diary return 401
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "super_secret_webhook_key_123")

    # Unauthenticated export & diary
    res_export_unauth = await client.get("/api/letterboxd/export")
    assert res_export_unauth.status_code == 401

    res_diary_unauth = await client.get("/api/letterboxd/diary")
    assert res_diary_unauth.status_code == 401

    # Authenticated export & diary with ?token=
    res_export_auth = await client.get("/api/letterboxd/export?token=super_secret_webhook_key_123")
    assert res_export_auth.status_code == 200
    assert "text/csv" in res_export_auth.headers.get("content-type", "")

    res_diary_auth = await client.get("/api/letterboxd/diary?token=super_secret_webhook_key_123")
    assert res_diary_auth.status_code == 200

    # Demo bypass for diary
    res_diary_demo = await client.get("/api/letterboxd/diary?demo=true")
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
    res_kitsu_masked = await client.get("/api/kitsu/status")
    assert res_kitsu_masked.status_code == 200
    assert res_kitsu_masked.json().get("user") != "kitsu_otaku_master"

    res_lb_masked = await client.get("/api/letterboxd/status")
    assert res_lb_masked.status_code == 200
    assert res_lb_masked.json().get("user") != "letterboxd_cinephile"

    res_ser_masked = await client.get("/api/serializd/status")
    assert res_ser_masked.status_code == 200
    assert res_ser_masked.json().get("user") != "serializd_binger"

    # Admin view with header x-webhook-secret reveals full username
    res_kitsu_admin = await client.get("/api/kitsu/status", headers={"x-webhook-secret": "super_secret_webhook_key_123"})
    assert res_kitsu_admin.status_code == 200
    assert res_kitsu_admin.json().get("user") == "kitsu_otaku_master"

    # 3. Parameter validation on /api/mdblist/ratings: 400 when missing both ids
    monkeypatch.setattr(main_mod.mdblist, "is_configured", lambda: True)
    res_mdb_bad = await client.get("/api/mdblist/ratings?token=super_secret_webhook_key_123")
    assert res_mdb_bad.status_code == 400
    assert "Either imdb_id or tmdb_id" in res_mdb_bad.json().get("detail", "")

    # 4. Backup zip includes letterboxd_diary.json when present
    diary_file = tmp_path / "letterboxd_diary.json"
    diary_file.write_text('[{"title": "Inception", "year": 2010}]', encoding="utf-8")
    monkeypatch.setattr(Config, "LETTERBOXD_DIARY_FILE", diary_file)
    monkeypatch.setattr(Config, "LETTERBOXD_DATA_FILE", diary_file)

    backup_data_dir = tmp_path / "backup-data"
    backup_data_dir.mkdir()
    rules_file = backup_data_dir / "automation_rules.json"
    rules_file.write_text('[{"id":"rule-1","name":"Pause anime","action":"suppress"}]', encoding="utf-8")
    provenance_file = backup_data_dir / "cross_sync_provenance.json"
    provenance_file.write_text('{"version":1,"records":[]}', encoding="utf-8")
    monkeypatch.setattr(Config, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    rules_file.rename(tmp_path / "data" / "automation_rules.json")
    provenance_file.rename(tmp_path / "data" / "cross_sync_provenance.json")

    res_backup = await client.get("/api/backup?token=super_secret_webhook_key_123")
    assert res_backup.status_code == 200
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(res_backup.content), "r") as zf:
        names = zf.namelist()
        assert "data/letterboxd_diary.json" in names
        assert "data/automation_rules.json" in names
        assert "data/cross_sync_provenance.json" in names

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_cloud_sync_manager_run_cycle_and_letterboxd_export(tmp_path):
    """Verify CloudSyncManager executes full sync cycle, exports Letterboxd CSV, and records telemetry."""
    from app.services.cloud_sync_manager import CloudSyncManager

    class DummyConfig:
        BASE_DIR = tmp_path
        BACKGROUND_CLOUD_SYNC_INTERVAL_HOURS = 12
        AUTO_ADD_FROM_WATCHLIST = True

    mgr = CloudSyncManager(config=DummyConfig)

    # Mock clients
    mock_lb = MagicMock()
    mock_lb.generate_csv.return_value = "Date,Name,Year,Letterboxd URI,Rating,Rewatch\n2025-01-01,Dune: Part Two,2024,,5,\n"

    mock_reverse = MagicMock()
    mock_reverse.plex = MagicMock()
    mock_reverse.plex.is_configured.return_value = True
    mock_reverse.scan_discrepancies = AsyncMock(return_value={"discrepancies": [{"id": "1", "title": "Dune"}]})
    mock_reverse.execute_reconciliation = AsyncMock(return_value={"reconciled_count": 1})

    mock_cross = MagicMock()
    mock_cross.simkl = MagicMock()
    mock_cross.simkl.is_authenticated.return_value = True
    mock_cross.scan_discrepancies = AsyncMock(return_value={"discrepancies": [{"id": "2", "title": "Severance"}]})
    mock_cross.execute_sync = AsyncMock(return_value={"synced_count": 1})

    mock_arr = MagicMock()
    mock_arr.sonarr = MagicMock()
    mock_arr.sonarr.is_configured = True
    mock_arr.radarr = MagicMock()
    mock_arr.radarr.is_configured = False
    mock_arr.sync_watchlist = AsyncMock(return_value={"added": 1, "checked": 5})

    with patch("app.services.settings_manager.settings_mgr.get_reconciliation_settings", return_value={"server_type": "plex"}), \
         patch("app.services.settings_manager.settings_mgr.is_tracker_enabled", return_value=True):

        res = await mgr.run_sync_cycle(
            trigger="test_runner",
            letterboxd_client=mock_lb,
            reverse_sync_mgr=mock_reverse,
            cross_tracker_sync=mock_cross,
            arr_bridge=mock_arr,
        )

        # Verify export file was written
        export_file = tmp_path / "data" / "exports" / "letterboxd_diary.csv"
        assert export_file.exists()
        content = export_file.read_text(encoding="utf-8")
        assert "Dune: Part Two" in content

        # Verify state
        assert res["last_run_status"] == "success"
        assert res["items_reconciled"] == 2
        assert res["trigger"] == "test_runner"
        assert "next_scheduled_run" in res
        assert mgr.state_file.exists()

        status = mgr.get_status()
        assert status["last_run_status"] == "success"
        assert status["is_running"] is False

@pytest.mark.asyncio(loop_scope="module")
async def test_cloud_sync_mutex_lock_protection():
    """Verify concurrency mutex lock returns HTTP 409 Conflict when a sync is active."""
    from app.main import app
    from app.services.cloud_sync_manager import cloud_sync_mgr

    client = make_async_test_client(app)

    # Acquire the mutex manually to simulate an active sync
    await cloud_sync_mgr.sync_mutex.acquire()
    try:
        assert cloud_sync_mgr.sync_mutex.locked() is True

        # 1. Trigger background sync returns 409
        res_bg = await client.post("/api/sync/background/run")
        assert res_bg.status_code == 409

        # 2. Trigger reconciliation returns 409
        res_recon = await client.post("/api/sync/reconcile", json={"direction": "all"})
        assert res_recon.status_code == 409

        # 3. Trigger cross-sync returns 409
        res_cross = await client.post("/api/cross-sync/execute", json={"direction": "all"})
        assert res_cross.status_code == 409

        # 4. Direct run_sync_cycle returns conflict
        direct_res = await cloud_sync_mgr.run_sync_cycle()
        assert direct_res["status"] == "conflict"
    finally:
        cloud_sync_mgr.sync_mutex.release()

@pytest.mark.asyncio(loop_scope="module")
async def test_background_sync_endpoints():
    """Verify /api/sync/background/status and /api/sync/background/run endpoints."""
    from app.main import app
    from app.services.cloud_sync_manager import cloud_sync_mgr

    client = make_async_test_client(app)

    # Demo status
    res_demo = await client.get("/api/sync/background/status?demo=true")
    assert res_demo.status_code == 200
    demo_data = res_demo.json()
    assert demo_data["last_run_status"] == "success"
    assert "tasks" in demo_data

    # Demo run
    res_run_demo = await client.post("/api/sync/background/run?demo=true")
    assert res_run_demo.status_code == 200
    assert res_run_demo.json()["status"] == "success"

    # Real status
    res_real = await client.get("/api/sync/background/status")
    assert res_real.status_code == 200
    assert "is_running" in res_real.json()

