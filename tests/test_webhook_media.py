"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


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

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_endpoint_full_flow():
    client = make_async_test_client(app)

    # 1. Health check
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    # 2. Dashboard
    # Demo rendering avoids real-time media-server probes in this UI smoke check.
    res_dash = await client.get("/?demo=true")
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

        response = await client.post(
            "/webhook",
            data={"payload": json.dumps(plex_sample)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["action"] == "mark_watched"
        assert "history" in data["result"]

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_get_info():
    """Verify that visiting /webhook via browser GET returns a friendly status message."""
    client = make_async_test_client(app)
    res = await client.get("/webhook")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "Plex Webhook endpoint is active" in data["message"]
    await client.aclose()

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

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_rating_flow():
    client = make_async_test_client(app)
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

        res = await client.post("/webhook", data={"payload": json.dumps(plex_rating_payload)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["action"] == "rate"
        assert scrobble_stats["ratings"] == initial_ratings_count + 1
        assert "rate (9/10)" in recent_events[0]["action"]

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

@pytest.mark.asyncio(loop_scope="module")
async def test_trakt_client_sync_collection():
    """Verify TraktClient.sync_collection delegates to POST /sync/collection."""
    with patch.object(trakt, "_post_authenticated", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"added": {"movies": 1, "episodes": 0}}
        payload = {"movies": [{"title": "Oppenheimer", "year": 2023}]}
        res = await trakt.sync_collection(payload)
        assert res["added"]["movies"] == 1
        mock_post.assert_called_once()
        assert "/sync/collection" in mock_post.call_args[0][0]

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_library_new_collection_flow():
    """Verify that library.new webhooks trigger Trakt collection synchronization."""
    client = make_async_test_client(app)
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

        res = await client.post("/webhook", data={"payload": json.dumps(payload)})
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
        res_disabled = await client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res_disabled.status_code == 200
        assert res_disabled.json()["status"] == "ignored"

@pytest.mark.asyncio(loop_scope="module")
async def test_synthetic_test_webhook():
    """Test POST /api/test/webhook for dry-run simulation and recent event generation."""
    client = make_async_test_client(app)

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
    resp = await client.post("/api/test/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["parsed"]["show_title"] == "Lanterns"
    assert data["parsed"]["duration_ms"] == 3600000
    assert "eligible" in data["cowatch"]

    # Verify event was recorded in recent_events
    events_resp = await client.get("/api/events")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    assert any("Lanterns S01E07" in e["title"] for e in events)

@pytest.mark.asyncio(loop_scope="module")
async def test_synthetic_test_webhook_execute_trakt():
    """Verify POST /api/test/webhook with execute_trakt=True syncs history and triggers co-watch."""
    client = make_async_test_client(app)

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

        resp = await client.post("/api/test/webhook", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["cowatch"]["eligible"] is True
        assert mock_sync.called
        assert mock_partner_client.sync_history.called

@pytest.mark.asyncio(loop_scope="module")
async def test_jellyfin_and_emby_webhook_security():
    """Verify webhook token enforcement on Jellyfin and Emby endpoints."""
    client = make_async_test_client(app)
    sample_payload = {
        "NotificationType": "PlaybackStart",
        "ItemType": "Movie",
        "Name": "Inception",
        "Year": 2010,
    }

    with patch.object(Config, "WEBHOOK_SECRET", "super_secret_token_123"):
        # 1. Jellyfin without token -> 401
        res = await client.post("/webhook/jellyfin", json=sample_payload)
        assert res.status_code == 401

        # 2. Jellyfin with wrong token -> 401
        res = await client.post("/webhook/jellyfin?token=wrong_token", json=sample_payload)
        assert res.status_code == 401

        # 3. Jellyfin with valid query token -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = await client.post("/webhook/jellyfin?token=super_secret_token_123", json=sample_payload)
            assert res.status_code == 200

        # 4. Jellyfin alias route /jellyfin with valid header -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = await client.post("/jellyfin", json=sample_payload, headers={"x-webhook-secret": "super_secret_token_123"})
            assert res.status_code == 200

        # 5. Emby without token -> 401
        emby_sample = {"Event": "playback.start", "Item": {"Type": "Movie", "Name": "Inception"}}
        res = await client.post("/webhook/emby", json=emby_sample)
        assert res.status_code == 401

        # 6. Emby with valid query token -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = await client.post("/webhook/emby?token=super_secret_token_123", json=emby_sample)
            assert res.status_code == 200

        # 7. Emby alias route /emby with valid header -> 200
        with patch.object(trakt, "is_authenticated", return_value=True), \
             patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {"action": "start"}
            res = await client.post("/emby", json=emby_sample, headers={"x-webhook-secret": "super_secret_token_123"})
            assert res.status_code == 200

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

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_ingestion_paused_bypass():
    """Verify incoming webhooks are paused/bypassed when server ingestion is disabled in settings."""
    from app.services.settings_manager import settings_mgr

    client = make_async_test_client(app)
    try:
        settings_mgr.set_server_enabled("plex", False)
        settings_mgr.set_server_enabled("jellyfin", False)
        settings_mgr.set_server_enabled("emby", False)

        # Plex webhook
        res_plex = await client.post("/webhook", data={"payload": "{}"})
        assert res_plex.status_code == 200
        assert res_plex.json()["status"] == "ignored"
        assert "Plex ingestion is paused" in res_plex.json()["reason"]

        # Jellyfin webhook
        res_jf = await client.post("/webhook/jellyfin", json={"NotificationType": "PlaybackStart"})
        assert res_jf.status_code == 200
        assert res_jf.json()["status"] == "ignored"
        assert "Jellyfin ingestion is paused" in res_jf.json()["reason"]

        # Emby webhook
        res_emby = await client.post("/webhook/emby", data={"data": "{}"})
        assert res_emby.status_code == 200
        assert res_emby.json()["status"] == "ignored"
        assert "Emby ingestion is paused" in res_emby.json()["reason"]
    finally:
        settings_mgr.set_server_enabled("plex", True)
        settings_mgr.set_server_enabled("jellyfin", True)
        settings_mgr.set_server_enabled("emby", True)

def test_webhook_simulator_payload_generation_and_parsing():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import simulate_webhook
    from app.plex_parser import parse_plex_webhook
    from app.jellyfin_parser import parse_jellyfin_webhook
    from app.emby_parser import parse_emby_webhook
    from app.clients.sonarr_client import parse_sonarr_webhook
    from app.clients.radarr_client import parse_radarr_webhook

    # 1. Plex Movie Finish
    plex_payload = simulate_webhook.build_plex_payload(
        scenario="movie-finish",
        title="Dune: Part Two",
        year=2024,
        show=None,
        season=1,
        episode=1,
        rating=10,
        progress=100.0,
        user="selits",
    )
    parsed_plex = parse_plex_webhook(plex_payload)
    assert parsed_plex is not None
    assert parsed_plex.media_type == "movie"
    assert parsed_plex.title == "Dune: Part Two"
    assert parsed_plex.year == 2024
    assert parsed_plex.event == "media.scrobble"

    # 2. Jellyfin Episode Start
    jf_payload = simulate_webhook.build_jellyfin_payload(
        scenario="episode-start",
        title="Good News About Hell",
        year=2022,
        show="Severance",
        season=1,
        episode=1,
        rating=10,
        progress=0.0,
        user="testuser",
    )
    parsed_jf = parse_jellyfin_webhook(jf_payload)
    assert parsed_jf is not None
    assert parsed_jf.media_type == "episode"
    assert parsed_jf.show_title == "Severance"
    assert parsed_jf.season == 1
    assert parsed_jf.episode == 1
    assert parsed_jf.event == "media.play"

    # 3. Emby Rating
    emby_payload = simulate_webhook.build_emby_payload(
        scenario="rate-movie",
        title="Inception",
        year=2010,
        show=None,
        season=1,
        episode=1,
        rating=9,
        progress=100.0,
        user="testuser",
    )
    parsed_emby = parse_emby_webhook(emby_payload)
    assert parsed_emby is not None
    assert parsed_emby.media_type == "movie"
    assert parsed_emby.rating == 9
    assert parsed_emby.event == "media.rate"

    # 4. Sonarr Download
    sonarr_payload = simulate_webhook.build_sonarr_payload(
        scenario="download",
        title="Pilot",
        year=2022,
        show="Severance",
        season=1,
        episode=1,
    )
    ev_type, trakt_p, parsed_sonarr = parse_sonarr_webhook(sonarr_payload)
    assert ev_type == "download"
    assert parsed_sonarr is not None
    assert parsed_sonarr.show_title == "Severance"
    assert parsed_sonarr.season == 1

    # 5. Radarr Download
    radarr_payload = simulate_webhook.build_radarr_payload(
        scenario="download",
        title="Inception",
        year=2010,
    )
    ev_r_type, trakt_r_p, parsed_radarr = parse_radarr_webhook(radarr_payload)
    assert ev_r_type == "download"
    assert parsed_radarr is not None
    assert parsed_radarr.title == "Inception"
    assert parsed_radarr.year == 2010

@pytest.mark.asyncio(loop_scope="module")
async def test_api_sync_register_webhook_endpoint():
    """Verify POST /api/sync/register-webhook endpoint security, demo, and execution."""
    client = make_async_test_client(app)

    # 1. Unauthenticated -> 401
    with patch.object(Config, "WEBHOOK_SECRET", "super_secret"):
        res_unauth = await client.post("/api/sync/register-webhook", json={"server": "plex"})
        assert res_unauth.status_code == 401

        # 2. Demo mode -> 200 simulation
        res_demo = await client.post("/api/sync/register-webhook?demo=true", json={"server": "jellyfin"})
        assert res_demo.status_code == 200
        assert res_demo.json()["status"] == "success"
        assert "Demo Mode" in res_demo.json()["message"]

        # 3. Admin authorized live call
        client.cookies.set("admin_token", "super_secret")
        with patch("app.services.reverse_sync_manager.reverse_sync_mgr.register_webhook", new_callable=AsyncMock) as mock_reg:
            mock_reg.return_value = {"success": True, "message": "Webhook configured in Emby"}
            res_live = await client.post("/api/sync/register-webhook", json={"server": "emby"})
            assert res_live.status_code == 200
            assert res_live.json()["success"] is True
            assert res_live.json()["message"] == "Webhook configured in Emby"

