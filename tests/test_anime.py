"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_anime_fastapi_endpoints():
    client = make_async_test_client(app)

    # 1. AniList Status Demo
    res_ani_demo = await client.get("/api/anilist/status?demo=true")
    assert res_ani_demo.status_code == 200
    assert res_ani_demo.json()["authenticated"] is True
    assert res_ani_demo.json()["user"] == "demo_otaku"

    # 2. MAL Status Demo
    res_mal_demo = await client.get("/api/mal/status?demo=true")
    assert res_mal_demo.status_code == 200
    assert res_mal_demo.json()["authenticated"] is True
    assert res_mal_demo.json()["user"] == "demo_otaku"

    # 3. /api/anime/resolve diagnostic endpoint
    res_resolve = await client.get("/api/anime/resolve?title=Sousou%20no%20Frieren&year=2023")
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
        res_unauth = await client.post("/api/anilist/token", json={"token": "some_token"})
        assert res_unauth.status_code == 401

        res_mal_unauth = await client.post("/api/mal/token", json={"token": "some_token"})
        assert res_mal_unauth.status_code == 401

        # Authorized with invalid token should fail connection
        client.cookies.set("admin_token", "admintoken123")
        with patch.object(anilist, "check_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = {"status": "error", "error": "Invalid token"}
            res_bad = await client.post("/api/anilist/token", json={"token": "invalid_tok"})
            assert res_bad.status_code == 400

        with patch.object(mal, "check_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = {"status": "error", "error": "Invalid token"}
            res_bad_mal = await client.post("/api/mal/token", json={"token": "invalid_tok"})
            assert res_bad_mal.status_code == 400

        # Authorized with valid token
        with patch.object(anilist, "check_connection", new_callable=AsyncMock) as mock_conn, \
             patch.object(anilist, "save_tokens") as mock_save:
            mock_conn.return_value = {"status": "connected", "user": "ShinjiIkari", "id": 1}
            res_good = await client.post("/api/anilist/token", json={"token": "good_token"})
            assert res_good.status_code == 200
            assert res_good.json()["user"] == "ShinjiIkari"
            assert mock_save.called

        # Disconnect endpoints
        with patch.object(anilist, "delete_tokens") as mock_del:
            res_disc = await client.post("/api/anilist/disconnect")
            assert res_disc.status_code == 200
            assert mock_del.called

        with patch.object(mal, "delete_tokens") as mock_del:
            res_disc_mal = await client.post("/api/mal/disconnect")
            assert res_disc_mal.status_code == 200
            assert mock_del.called

        # Auth portal pages
        client.cookies.clear()
        assert (await client.get("/auth/anilist")).status_code == 401
        assert (await client.get("/auth/mal")).status_code == 401

        client.cookies.set("admin_token", "admintoken123")
        res_page_ani = await client.get("/auth/anilist")
        assert res_page_ani.status_code == 200
        assert "AniList Anime Tracker" in res_page_ani.text

        res_page_mal = await client.get("/auth/mal")
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

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_renders_anime_tracking_card_and_modals():
    """Verify that the dashboard template renders the Anime card, modal dialogs, and triggers."""
    client = make_async_test_client(app)
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text + await dashboard_asset_text(client)

    assert "Anime Tracking Engine" in html
    assert "anilist-modal" in html
    assert "mal-modal" in html
    assert "openAnilistModal" in html
    assert "openMalModal" in html
    assert "submitAnilistToken" in html
    assert "submitMalToken" in html

