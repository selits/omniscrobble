"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


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

@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_api_endpoints():
    client = make_async_test_client(app)

    # 1. GET /api/cowatch (public / masked vs admin)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"), \
         patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["selits's Fire TV", "Shield TV"]):
        cowatch_mgr._shows = ["Severance", "The Bear"]
        res_pub = await client.get("/api/cowatch")
        assert res_pub.status_code == 200
        pub_data = res_pub.json()
        assert pub_data["status"]["shows"] == []
        assert pub_data["status"]["shows_count"] == 2
        assert pub_data["status"]["co_watch_user"] == "pa*****"
        assert pub_data["status"]["co_watch_players"] == []

        # Admin access reveals shows and actual device names
        client.cookies.set("admin_token", "testsecret")
        res_admin = await client.get("/api/cowatch")
        assert res_admin.status_code == 200
        admin_data = res_admin.json()
        assert admin_data["status"]["shows"] == ["Severance", "The Bear"]
        assert admin_data["status"]["co_watch_players"] == ["selits's Fire TV", "Shield TV"]
        client.cookies.clear()

    # 2. POST /api/cowatch/shows (auth check)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # Unauthorized without token
        res_unauth = await client.post("/api/cowatch/shows", json={"show": "The Bear"})
        assert res_unauth.status_code == 401

        # Authorized with token
        res_auth = await client.post("/api/cowatch/shows?token=testsecret", json={"show": "The Bear"})
        assert res_auth.status_code == 200
        assert "The Bear" in res_auth.json()["shows"]

        # 3. DELETE /api/cowatch/shows
        res_del = await client.delete("/api/cowatch/shows?token=testsecret&show=The+Bear")
        assert res_del.status_code == 200
        assert "The Bear" not in res_del.json()["shows"]

        # 4. POST /api/cowatch/devices (auth check & empty validation)
        res_dev_unauth = await client.post("/api/cowatch/devices", json={"device": "Apple TV 4K"})
        assert res_dev_unauth.status_code == 401

        res_dev_empty = await client.post("/api/cowatch/devices?token=testsecret", json={"device": "   "})
        assert res_dev_empty.status_code == 400

        res_dev_auth = await client.post("/api/cowatch/devices?token=testsecret", json={"device": "Apple TV 4K"})
        assert res_dev_auth.status_code == 200
        assert "Apple TV 4K" in res_dev_auth.json()["devices"]

        # 5. DELETE /api/cowatch/devices
        res_del_dev_unauth = await client.delete("/api/cowatch/devices?device=Apple+TV+4K")
        assert res_del_dev_unauth.status_code == 401

        res_del_dev = await client.delete("/api/cowatch/devices?token=testsecret&device=Apple+TV+4K")
        assert res_del_dev.status_code == 200
        assert "Apple TV 4K" not in res_del_dev.json()["devices"]

        # 6. Demo mode for /api/cowatch/devices
        res_demo_add = await client.post("/api/cowatch/devices?demo=true", json={"device": "Demo Shield"})
        assert res_demo_add.status_code == 200
        assert "Demo Shield" in res_demo_add.json()["devices"]

        res_demo_del = await client.delete("/api/cowatch/devices?demo=true&device=Demo+Shield")
        assert res_demo_del.status_code == 200
        assert "Demo Shield" not in res_demo_del.json()["devices"]

        # Clean up test devices
        if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
            Config.CO_WATCH_DEVICES_DATA_FILE.unlink()
        cowatch_mgr._devices.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_manual_sync_endpoint():
    client = make_async_test_client(app)

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
        res = await client.post("/api/cowatch/sync", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["target_user"] == "partner"
        mock_partner_client.sync_history.assert_called_once()

@pytest.mark.asyncio(loop_scope="module")
async def test_multi_user_auth_flow():
    client = make_async_test_client(app)

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
        res_start = await client.post("/api/auth/start?user=alice")
        assert res_start.status_code == 200
        assert res_start.json()["user_code"] == "ALICE123"

        # 2. Poll for alice
        res_poll = await client.post("/api/auth/poll", json={"device_code": "alice_dev", "user": "alice"})
        assert res_poll.status_code == 200
        assert res_poll.json()["status"] == "success"
        assert res_poll.json()["user"] == "alice"

        # 3. GET /auth?user=alice
        res_page = await client.get("/auth?user=alice")
        assert res_page.status_code == 200
        assert "@alice" in res_page.text

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_triggers_cowatch_sync():
    client = make_async_test_client(app)

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

        res = await client.post("/webhook", data={"payload": json.dumps(payload)})
        assert res.status_code == 200

        # Allow background create_task(execute_cowatch_sync) to execute
        import asyncio
        await asyncio.sleep(0.05)

        mock_partner_client.sync_history.assert_called_once()

@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_settings_and_movie_toggle():
    """Test POST /api/cowatch/settings to dynamically toggle movie co-watching."""
    client = make_async_test_client(app)
    orig_movies = cowatch_mgr.config.CO_WATCH_MOVIES
    try:
        # Enable movies
        resp_enable = await client.post("/api/cowatch/settings", json={"co_watch_movies": True})
        assert resp_enable.status_code == 200
        assert resp_enable.json()["co_watch_movies"] is True
        assert cowatch_mgr.config.CO_WATCH_MOVIES is True

        # Disable movies
        resp_disable = await client.post("/api/cowatch/settings", json={"co_watch_movies": False})
        assert resp_disable.status_code == 200
        assert resp_disable.json()["co_watch_movies"] is False
        assert cowatch_mgr.config.CO_WATCH_MOVIES is False
    finally:
        cowatch_mgr.set_cowatch_movies(orig_movies)

def test_cowatch_env_merging(tmp_path):
    """Verify CowatchManager merges .env CO_WATCH_SHOWS into existing JSON file."""
    shows_file = tmp_path / "cowatch_shows.json"
    shows_file.write_text(json.dumps(["Existing Show A", "Existing Show B"]))

    cfg = MagicMock()
    cfg.CO_WATCH_DATA_FILE = shows_file
    cfg.CO_WATCH_SHOWS = ["Env Show 1", "Existing Show A"]
    cfg.CO_WATCH_USER = "partner"
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = []

    mgr = CowatchManager(config=cfg)
    shows = mgr.get_shows()
    assert "Existing Show A" in shows
    assert "Existing Show B" in shows
    assert "Env Show 1" in shows
    assert len(shows) == 3

def test_cowatch_devices_env_merging_and_crud(tmp_path):
    """Verify CowatchManager manages devices dynamically and merges .env CO_WATCH_PLAYERS."""
    devices_file = tmp_path / "cowatch_devices.json"
    devices_file.write_text(json.dumps(["Living Room Apple TV", "Shield TV"]))

    cfg = MagicMock()
    cfg.CO_WATCH_DATA_FILE = tmp_path / "cowatch_shows.json"
    cfg.CO_WATCH_DEVICES_DATA_FILE = devices_file
    cfg.CO_WATCH_SHOWS = []
    cfg.CO_WATCH_USER = "partner"
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = ["Env Device 1", "Living Room Apple TV"]

    mgr = CowatchManager(config=cfg)
    devices = mgr.get_devices()
    assert "Living Room Apple TV" in devices
    assert "Shield TV" in devices
    assert "Env Device 1" in devices
    assert len(devices) == 3

    # Add device
    mgr.add_device("Bedroom TV")
    assert "Bedroom TV" in mgr.get_devices()

    # Re-reading from file should contain added device
    mgr2 = CowatchManager(config=cfg)
    assert "Bedroom TV" in mgr2.get_devices()

    # Remove device
    mgr2.remove_device("Shield TV")
    assert "Shield TV" not in mgr2.get_devices()

def test_cowatch_eligibility_reasons():
    """Verify check_cowatch_eligibility returns accurate boolean and informative reason strings."""
    cfg = MagicMock()
    cfg.CO_WATCH_USER = "jane"
    cfg.CO_WATCH_SHOWS = ["Lanterns", "Animal Control"]
    cfg.CO_WATCH_MOVIES = False
    cfg.CO_WATCH_PLAYERS = ["Apple TV", "Living Room"]

    mgr = CowatchManager(config=cfg)

    # 1. Partner self playback
    media_self = ParsedMedia(
        event="media.scrobble", username="jane", media_type="episode",
        title="Ep 1", show_title="Lanterns", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_self)
    assert not eligible
    assert "partner" in reason.lower()

    # 2. Player not allowed
    media_wrong_player = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Lanterns", progress=100.0, player="Bedroom Phone"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_wrong_player)
    assert not eligible
    assert "CO_WATCH_PLAYERS" in reason

    # 3. Eligible show
    media_ok_show = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Lanterns (2025)", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_ok_show)
    assert eligible
    assert "Shared show" in reason

    # 4. Solo show not in list
    media_solo_show = ParsedMedia(
        event="media.scrobble", username="selits", media_type="episode",
        title="Ep 1", show_title="Breaking Bad", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_solo_show)
    assert not eligible
    assert "not in shared whitelist" in reason

    # 5. Movie disabled vs enabled
    media_movie = ParsedMedia(
        event="media.scrobble", username="selits", media_type="movie",
        title="Inception", progress=100.0, player="Apple TV"
    )
    eligible, reason = mgr.check_cowatch_eligibility(media_movie)
    assert not eligible
    assert "disabled" in reason.lower()

    mgr.set_cowatch_movies(True)
    eligible, reason = mgr.check_cowatch_eligibility(media_movie)
    assert eligible
    assert "enabled" in reason.lower()

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_records_cowatch_status_and_privacy():
    """Verify live webhooks record cowatch_status in events and respect privacy masking."""
    import asyncio

    client = make_async_test_client(app)
    recent_events.clear()

    payload = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "show",
            "type": "episode",
            "title": "Forks",
            "grandparentTitle": "The Bear",
            "parentIndex": 2,
            "index": 7,
            "duration": 2000000,
            "viewOffset": 1950000,
        },
    }

    with patch.object(Config, "CO_WATCH_USER", "partner"), \
         patch.object(Config, "WEBHOOK_SECRET", "privacy_token"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch("app.main.execute_cowatch_sync", new_callable=AsyncMock, return_value="success"):

        mock_stop.return_value = {"action": "scrobble"}

        res = await client.post("/webhook?token=privacy_token", data={"payload": json.dumps(payload)})
        assert res.status_code == 200
        await asyncio.sleep(0.01)
        assert len(recent_events) >= 1
        ev = recent_events[0]
        assert ev.get("cowatch_status") is not None
        assert ev["cowatch_status"]["status"] == "success"
        assert ev["cowatch_status"]["synced"] is True
        assert ev["cowatch_status"]["target"] == "partner"

        # 1. Non-admin request to /api/events should have cowatch_status masked (None)
        client.cookies.clear()
        res_unauth_events = await client.get("/api/events")
        assert res_unauth_events.status_code == 200
        unauth_ev = res_unauth_events.json()["events"][0]
        assert unauth_ev["cowatch_status"] is None
        assert unauth_ev["show_title"] is None
        assert unauth_ev["user"] == "se****"
        assert "tracker_delivery_details" not in unauth_ev
        assert "operation_id" not in unauth_ev

        # 2. Admin request to /api/events reveals cowatch_status and show_title
        client.cookies.set("admin_token", "privacy_token")
        res_admin_events = await client.get("/api/events")
        assert res_admin_events.status_code == 200
        admin_ev = res_admin_events.json()["events"][0]
        assert admin_ev["cowatch_status"] is not None
        assert admin_ev["cowatch_status"]["synced"] is True
        assert admin_ev["is_cowatch_show"] is True
        assert admin_ev["operation_id"] == admin_ev["event_id"]
        assert isinstance(admin_ev["tracker_delivery_details"], dict)
        client.cookies.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_cowatch_settings_unauthorized():
    """Verify POST /api/cowatch/settings enforces admin authorization when WEBHOOK_SECRET is active."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # Without credentials -> 401
        res_denied = await client.post("/api/cowatch/settings", json={"co_watch_movies": True})
        assert res_denied.status_code == 401

        # With credentials -> 200
        res_ok = await client.post("/api/cowatch/settings?token=admin_secret", json={"co_watch_movies": True})
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "ok"

@pytest.mark.asyncio(loop_scope="module")
async def test_jellyfin_and_emby_cowatch_integration():
    """Verify Watch Together co-watch dual-sync evaluates properly for Jellyfin & Emby events."""
    client = make_async_test_client(app)
    jf_cowatch_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Episode",
        "SeriesName": "Severance",
        "Name": "Good News About Hell",
        "SeasonNumber": 1,
        "EpisodeNumber": 1,
        "Year": 2022,
        "RunTimeTicks": 36000000000,
        "PlaybackPositionTicks": 34000000000,  # >80%
    }

    with patch.object(Config, "CO_WATCH_USER", "partner_trakt_user"), \
         patch.object(cowatch_mgr, "is_cowatch_show", return_value=True), \
         patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch("app.main.execute_cowatch_sync", new_callable=AsyncMock) as mock_cowatch_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        mock_cowatch_sync.return_value = "success"

        res = await client.post("/webhook/jellyfin", json=jf_cowatch_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"
        mock_cowatch_sync.assert_called_once()

def test_notifier_discord_payload_includes_cowatch_partner_and_trackers():
    """Verify Discord payload includes unmasked co-watch partner username and tracker list."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier = Notifier()
    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        title="Severance S02E04",
        show_title="Severance",
        media_type="episode",
        season=2,
        episode=4
    )

    payload = notifier.build_discord_payload(
        media,
        "scrobble",
        cowatch_partner="alice_partner",
        trackers=["Trakt", "Simkl"]
    )
    fields = payload["embeds"][0]["fields"]
    field_names = [f["name"] for f in fields]
    field_vals = [f["value"] for f in fields]

    assert "👥 Co-Watched With" in field_names
    idx = field_names.index("👥 Co-Watched With")
    assert "@alice_partner" in field_vals[idx]

    assert "📡 Synced Trackers" in field_names
    idx_trk = field_names.index("📡 Synced Trackers")
    assert "Trakt, Simkl" in field_vals[idx_trk]

@pytest.mark.asyncio(loop_scope="module")
async def test_activity_table_show_cowatch_alignment():
    """Verify that events with media_type=='show' resolve show_title, detect co-watching, and render aligned action buttons."""
    from app.services.cowatch_manager import cowatch_mgr
    from app.main import recent_events, log_event
    from app.plex_parser import ParsedMedia

    # Ensure Ted Lasso is in co-watch shows
    cowatch_mgr.add_show("Ted Lasso")

    # 1. Log a show event
    parsed = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="show",
        title="Ted Lasso (2020)",
        year=2020,
        show_title="Ted Lasso (2020)",
        progress=100.0,
    )
    log_event(parsed, "scrobble", {"status": "ok"})

    # 2. Check recent_events entry
    latest = recent_events[0]
    assert latest["type"] == "show"
    assert latest["show_title"] in ("Ted Lasso (2020)", "Ted Lasso")

    client = make_async_test_client(app)

    # 3. Test /api/events endpoint calculates is_cowatch_show=True
    res = await client.get("/api/events")
    assert res.status_code == 200
    events = res.json()["events"]
    show_ev = next((e for e in events if e.get("type") == "show"), None)
    assert show_ev is not None
    assert show_ev.get("is_cowatch_show") is True

    # 4. Test SSR dashboard renders clean flex layout without inert badge or redundant + Co-Watch button for whitelisted show
    client.cookies.set("admin_token", "unlocked")
    dash_res = await client.get("/")
    assert dash_res.status_code == 200
    html = dash_res.text + await dashboard_asset_text(client)
    assert "✓ Co-Watching" not in html
    assert 'data-show="Ted%20Lasso%20%282020%29"' not in html
    assert 'class="activity-row-actions"' in html

def test_household_manager_crud_and_persistence(tmp_path):
    """Verify HouseholdManager rule CRUD operations, toggle, and atomic JSON persistence."""
    from app.services.household_manager import HouseholdManager
    from app.config import Config

    rules_file = tmp_path / "household_rules.json"

    class CustomConfig(Config):
        HOUSEHOLD_RULES_DATA_FILE = rules_file
        CO_WATCH_DATA_FILE = tmp_path / "cowatch_shows.json"
        CO_WATCH_DEVICES_DATA_FILE = tmp_path / "cowatch_devices.json"

    mgr = HouseholdManager(config=CustomConfig)
    assert mgr.get_rules() == []

    # 1. Add rule
    r1 = mgr.add_rule(
        name="Living Room Family",
        targets=["alice", "kids"],
        devices=["Living Room Apple TV", "Shield Pro"],
        shows=["*"],
        media_types=["movie", "episode"],
        enabled=True,
    )
    assert r1["name"] == "Living Room Family"
    assert r1["targets"] == ["alice", "kids"]
    assert len(mgr.get_rules()) == 1

    # 2. Add second rule
    r2 = mgr.add_rule(
        name="Kids Playroom",
        targets=["kids"],
        devices=["Playroom TV"],
        shows=["Bluey"],
        media_types=["episode"],
        enabled=True,
    )
    assert len(mgr.get_rules()) == 2

    # 3. Update rule
    up = mgr.update_rule(r1["id"], {"name": "Living Room All", "targets": ["alice", "kids", "bob"]})
    assert up is not None
    assert up["name"] == "Living Room All"
    assert "bob" in up["targets"]

    # 4. Toggle rule
    new_state = mgr.toggle_rule(r2["id"])
    assert new_state is False
    assert next(r for r in mgr.get_rules() if r["id"] == r2["id"])["enabled"] is False

    # 5. Persistence across reloads
    mgr_reloaded = HouseholdManager(config=CustomConfig)
    rules_reloaded = mgr_reloaded.get_rules()
    assert len(rules_reloaded) == 2
    r1_reloaded = next(r for r in rules_reloaded if r["id"] == r1["id"])
    assert r1_reloaded["name"] == "Living Room All"
    assert r1_reloaded["targets"] == ["alice", "kids", "bob"]

    # 6. Delete rule
    deleted = mgr.delete_rule(r2["id"])
    assert deleted is True
    assert len(mgr.get_rules()) == 1
    assert mgr.delete_rule("non_existent_id") is False

def test_household_manager_resolve_targets(tmp_path):
    """Verify HouseholdManager resolves multi-tenant targets based on device, type, and show filters."""
    from app.services.household_manager import HouseholdManager
    from app.config import Config
    from app.plex_parser import ParsedMedia

    class CustomConfig(Config):
        CO_WATCH_USER = "partner_jane"
        CO_WATCH_SHOWS = ["Severance"]
        CO_WATCH_PLAYERS = []
        CO_WATCH_MOVIES = True
        HOUSEHOLD_RULES_DATA_FILE = tmp_path / "household_rules.json"
        CO_WATCH_DATA_FILE = tmp_path / "cowatch_shows.json"
        CO_WATCH_DEVICES_DATA_FILE = tmp_path / "cowatch_devices.json"

    mgr = HouseholdManager(config=CustomConfig)

    # Add household rule for Living Room TV
    mgr.add_rule(
        name="Living Room Family",
        targets=["kids", "partner_jane"],
        devices=["Living Room Apple TV"],
        shows=["*"],
        media_types=["movie", "episode"],
        enabled=True,
    )
    # Add household rule for Bedroom TV
    mgr.add_rule(
        name="Bedroom TV Solo",
        targets=["alice"],
        devices=["Bedroom Chromecast"],
        shows=["*"],
        media_types=["episode"],
        enabled=True,
    )

    # 1. Severance playing on Living Room Apple TV by "selits"
    # Matches CO_WATCH_USER (Severance is in CO_WATCH_SHOWS) + rule targets ("kids", "partner_jane")
    # Result should include partner_jane and kids (deduplicated)
    m1 = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        show_title="Severance",
        title="Severance S02E01",
        player="Living Room Apple TV",
    )
    targets1 = mgr.resolve_targets(m1)
    assert "partner_jane" in targets1
    assert "kids" in targets1
    assert "selits" not in targets1  # Playing user must never self-scrobble

    # 2. Movie playing on Bedroom Chromecast
    # Rule for Bedroom TV only allows "episode", so bedroom rule does NOT match.
    # But CO_WATCH_MOVIES is True, so partner_jane is eligible.
    m2 = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        player="Bedroom Chromecast",
    )
    targets2 = mgr.resolve_targets(m2)
    assert targets2 == ["partner_jane"]

    # 3. Episode playing on Bedroom Chromecast by "partner_jane"
    # Traditional co-watch partner is playing, so traditional co-watch is NOT eligible (self playback by partner).
    # But Bedroom TV rule targets "alice" and media is episode!
    m3 = ParsedMedia(
        event="media.scrobble",
        username="partner_jane",
        media_type="episode",
        show_title="Ted Lasso",
        title="Ted Lasso S01E01",
        player="Bedroom Chromecast",
    )
    targets3 = mgr.resolve_targets(m3)
    assert targets3 == ["alice"]
    assert "partner_jane" not in targets3

@pytest.mark.asyncio(loop_scope="module")
async def test_household_rules_api_endpoints():
    """Verify REST API endpoints for household multi-tenant rules management."""
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "admin_pass"):
        # 1. GET /api/household/rules unauthenticated -> masks targets
        res_unauth = await client.get("/api/household/rules")
        assert res_unauth.status_code == 200
        data_unauth = res_unauth.json()
        assert data_unauth["status"] == "ok"
        assert isinstance(data_unauth["rules"], list)

        # 2. POST /api/household/rules without auth -> 401
        res_fail = await client.post("/api/household/rules", json={"name": "Test", "targets": ["alice"]})
        assert res_fail.status_code == 401

        # 3. POST /api/household/rules with auth but empty targets -> 400
        client.cookies.set("admin_token", "admin_pass")
        res_empty = await client.post("/api/household/rules", json={"name": "Test", "targets": []})
        assert res_empty.status_code == 400

        # 4. POST /api/household/rules with auth -> creates rule
        rule_payload = {
            "name": "Basement Shield",
            "targets": ["alice", "bob"],
            "devices": ["Basement Shield"],
            "shows": ["*"],
            "media_types": ["movie", "episode"],
            "enabled": True,
        }
        res_create = await client.post("/api/household/rules", json=rule_payload)
        assert res_create.status_code == 200
        created_rule = res_create.json()["rule"]
        assert created_rule["name"] == "Basement Shield"
        assert created_rule["targets"] == ["alice", "bob"]
        rule_id = created_rule["id"]

        # 5. POST /api/household/rules with existing id -> updates rule
        update_payload = dict(rule_payload)
        update_payload["id"] = rule_id
        update_payload["name"] = "Basement Shield Pro"
        res_update = await client.post("/api/household/rules", json=update_payload)
        assert res_update.status_code == 200
        assert res_update.json()["rule"]["name"] == "Basement Shield Pro"

        # 6. POST /api/household/rules/{id}/toggle -> toggles enabled
        res_toggle = await client.post(f"/api/household/rules/{rule_id}/toggle")
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False

        # 7. DELETE /api/household/rules/{id} -> deletes rule
        res_del = await client.delete(f"/api/household/rules/{rule_id}")
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

        # 8. DELETE non-existent rule -> 404
        res_del_404 = await client.delete("/api/household/rules/non_existent_123")
        assert res_del_404.status_code == 404

        # 9. Demo mode for all endpoints
        res_demo_get = await client.get("/api/household/rules?demo=true")
        assert res_demo_get.status_code == 200
        assert len(res_demo_get.json()["rules"]) >= 1

        res_demo_post = await client.post("/api/household/rules?demo=true", json={"name": "Demo Rule", "targets": ["demo_partner"]})
        assert res_demo_post.status_code == 200

        res_demo_toggle = await client.post("/api/household/rules/rule_living_room/toggle?demo=true")
        assert res_demo_toggle.status_code == 200

        res_demo_del = await client.delete("/api/household/rules/rule_living_room?demo=true")
        assert res_demo_del.status_code == 200
        assert res_demo_del.json()["deleted"] is True

@pytest.mark.asyncio(loop_scope="module")
async def test_household_multi_tenant_webhook_dual_sync():
    """Verify live webhook dispatches dual-sync across multiple resolved household target profiles."""
    import asyncio
    from app.services.household_manager import household_mgr
    from app.services.user_manager import user_mgr
    from app.main import recent_events
    from app.config import Config

    client = make_async_test_client(app)

    # Add a household rule targeting 'kids_user' and 'partner_user' on Living Room TV
    rule = household_mgr.add_rule(
        name="Living Room Family Sync",
        targets=["kids_user", "partner_user"],
        devices=["Living Room Apple TV"],
        shows=["*"],
        media_types=["episode"],
        enabled=True,
    )

    try:
        mock_partner = MagicMock()
        mock_partner.is_authenticated.return_value = True
        mock_partner.sync_history = AsyncMock(return_value={"action": "scrobble"})

        mock_kids = MagicMock()
        mock_kids.is_authenticated.return_value = True
        mock_kids.sync_history = AsyncMock(return_value={"action": "scrobble"})

        mock_selits = MagicMock()
        mock_selits.is_authenticated.return_value = True
        mock_selits.scrobble_stop = AsyncMock(return_value={"action": "scrobble"})
        mock_selits.sync_history = AsyncMock(return_value={"added": {"episodes": 1}})

        def mock_get_client(user):
            if user == "partner_user":
                return mock_partner
            elif user == "kids_user":
                return mock_kids
            elif user == "selits":
                return mock_selits
            return MagicMock()

        webhook_payload = {
            "event": "media.scrobble",
            "Account": {"title": "selits"},
            "Player": {"title": "Living Room Apple TV"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Severance",
                "title": "Good News About Hell",
                "year": 2022,
                "parentIndex": 1,
                "index": 1,
                "viewOffset": 3600000,
                "duration": 3600000,
            }
        }

        with patch.object(Config, "CO_WATCH_USER", "partner_user"), \
             patch.object(user_mgr, "get_client", side_effect=mock_get_client), \
             patch("app.main.trakt.is_authenticated", return_value=True), \
             patch("app.main.trakt.scrobble_stop", new_callable=AsyncMock) as mock_trakt_stop, \
             patch("app.main.trakt.sync_history", new_callable=AsyncMock) as mock_trakt_hist, \
             patch("app.main.notifier.dispatch", new_callable=AsyncMock):
            mock_trakt_stop.return_value = {"action": "scrobble"}
            mock_trakt_hist.return_value = {"added": {"episodes": 1}}

            res = await client.post("/webhook", json=webhook_payload)
            assert res.status_code == 200

            # Yield control to event loop to allow asyncio.create_task(execute_cowatch_sync(...)) to complete
            await asyncio.sleep(0.05)

            # Both partner_user and kids_user should have had sync_history called!
            mock_partner.sync_history.assert_called_once()
            mock_kids.sync_history.assert_called_once()

            # Verify cowatch_status recorded in recent_events contains targets
            ev = recent_events[0]
            assert ev.get("cowatch_status") is not None
            assert ev["cowatch_status"]["status"] == "success"
            assert ev["cowatch_status"]["synced"] is True
            assert "partner_user" in ev["cowatch_status"]["targets"]
            assert "kids_user" in ev["cowatch_status"]["targets"]

    finally:
        household_mgr.delete_rule(rule["id"])

