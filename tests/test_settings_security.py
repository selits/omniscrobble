"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


def test_public_settings_endpoint_omits_private_configuration():
    from starlette.requests import Request
    import app.main as main_mod

    request = Request({
        "type": "http", "method": "GET", "path": "/api/settings", "headers": [],
        "query_string": b"", "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345), "root_path": "",
    })
    with patch.object(Config, "WEBHOOK_SECRET", "settings-secret"):
        response = main_mod.get_settings_endpoint(request)
    settings = response["settings"]
    assert set(settings) == {"servers", "trackers", "multi_server_mirroring"}

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_secret_authentication():
    client = make_async_test_client(app)
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
        res_no_token = await client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res_no_token.status_code == 401

        # 2. Reject with wrong token
        res_wrong_token = await client.post("/webhook?token=wrong", data={"payload": json.dumps(plex_sample)})
        assert res_wrong_token.status_code == 401

        # 3. Allow with correct query token
        res_valid_query = await client.post("/webhook?token=super_secret_token", data={"payload": json.dumps(plex_sample)})
        assert res_valid_query.status_code == 200
        assert res_valid_query.json()["status"] == "success"

        # 4. Allow with correct header token
        res_valid_header = await client.post(
            "/webhook",
            headers={"X-Webhook-Secret": "super_secret_token"},
            data={"payload": json.dumps(plex_sample)},
        )
        assert res_valid_header.status_code == 200
        assert res_valid_header.json()["status"] == "success"

@pytest.mark.asyncio(loop_scope="module")
async def test_auth_endpoints_and_page():
    client = make_async_test_client(app)

    # 1. GET /auth page
    res_page = await client.get("/auth")
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
        res_start = await client.post("/api/auth/start")
        assert res_start.status_code == 200
        assert res_start.json()["user_code"] == "ABCD1234"

    # 3. POST /api/auth/poll pending
    with patch.object(trakt, "poll_for_token", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"status": "pending"}
        res_poll = await client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll.status_code == 200
        assert res_poll.json()["status"] == "pending"

    # 4. POST /api/auth/poll success
    with patch.object(trakt, "poll_for_token", new_callable=AsyncMock) as mock_poll:
        mock_poll.return_value = {"access_token": "valid_token"}
        res_poll_ok = await client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll_ok.status_code == 200
        assert res_poll_ok.json()["status"] == "success"

@pytest.mark.asyncio(loop_scope="function")
async def test_trakt_auth_endpoints_enforce_admin_cookie_csrf():
    """Require a matching CSRF header for cookie-authenticated Trakt auth POSTs."""
    client = make_async_test_client(app)
    secret = "test_admin_secret"
    csrf_token = "test_csrf_token"
    fake_client = MagicMock()
    fake_client.generate_device_code = AsyncMock(return_value={
        "device_code": "device-123",
        "user_code": "ABCD1234",
        "verification_url": "https://trakt.tv/activate",
        "interval": 5,
    })
    fake_client.poll_for_token = AsyncMock(return_value={"status": "pending"})

    with patch.object(Config, "WEBHOOK_SECRET", secret), \
            patch("app.main.user_mgr.get_client", return_value=fake_client):
        client.cookies.set("admin_token", secret)
        client.cookies.set("csrf_token", csrf_token)

        assert (await client.post("/api/auth/start")).status_code == 401
        assert (await client.post("/api/auth/poll", json={"device_code": "device-123"})).status_code == 401

        headers = {"x-csrf-token": csrf_token}
        start = await client.post("/api/auth/start", headers=headers)
        assert start.status_code == 200
        assert start.json()["user_code"] == "ABCD1234"

        poll = await client.post(
            "/api/auth/poll",
            json={"device_code": "device-123"},
            headers=headers,
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "pending"

def test_mask_username():
    assert mask_username("selits") == "se****"
    assert mask_username("alex") == "a***"
    assert mask_username("bob") == "b**"
    assert mask_username("al") == "a*"
    assert mask_username("a") == "*"
    assert mask_username("") == ""
    assert mask_username(None) == ""

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

@pytest.mark.asyncio(loop_scope="module")
async def test_admin_authorization_gate():
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "secret123"):
        # 1. Clear events without admin rights -> 401
        res_clear_denied = await client.post("/api/events/clear")
        assert res_clear_denied.status_code == 401

        # 2. Clear events with valid header -> 200
        res_clear_header = await client.post("/api/events/clear", headers={"x-webhook-secret": "secret123"})
        assert res_clear_header.status_code == 200

        # 3. Clear events with cookie -> 200
        client.cookies.set("admin_token", "secret123")
        res_clear_cookie = await client.post("/api/events/clear")
        assert res_clear_cookie.status_code == 200
        client.cookies.clear()

        # 4. Auth endpoints without admin rights -> 401
        res_auth_denied = await client.get("/auth")
        assert res_auth_denied.status_code == 401
        assert "Admin Authorization Required" in res_auth_denied.text

        res_start_denied = await client.post("/api/auth/start")
        assert res_start_denied.status_code == 401

        res_poll_denied = await client.post("/api/auth/poll", json={"device_code": "dev123"})
        assert res_poll_denied.status_code == 401

        # 5. Admin unlock endpoint
        res_unlock_bad = await client.post("/api/admin/unlock", json={"token": "wrong"})
        assert res_unlock_bad.status_code == 401

        res_unlock_ok = await client.post("/api/admin/unlock", json={"token": "secret123"})
        assert res_unlock_ok.status_code == 200
        assert "admin_token" in res_unlock_ok.cookies

        # 6. Admin lock endpoint
        res_lock = await client.post("/api/admin/lock")
        assert res_lock.status_code == 200

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_privacy_masking():
    client = make_async_test_client(app)

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
        res_locked = await client.get("/")
        assert res_locked.status_code == 200
        assert "se****" in res_locked.text
        assert "my_super_secret" not in res_locked.text
        assert "●●●●●●●●" in res_locked.text
        assert "://●●●●●●●●" in res_locked.text
        assert "🔓 Unlock Admin" in res_locked.text

        # 2. Events API masked
        res_events = await client.get("/api/events")
        assert res_events.status_code == 200
        assert res_events.json()["events"][0]["user"] == "se****"

        # 3. Authenticated request via cookie: reveals unmasked data
        client.cookies.set("admin_token", "my_super_secret")
        res_unlocked = await client.get("/")
        assert res_unlocked.status_code == 200
        assert "my_super_secret" in res_unlocked.text
        assert "🔒 Lock Admin" in res_unlocked.text
        assert "📋 Copy URL" in res_unlocked.text

        res_events_unlocked = await client.get("/api/events")
        assert res_events_unlocked.json()["events"][0]["user"] == "selits"
        client.cookies.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_health_and_stats():
    client = make_async_test_client(app)
    res = await client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "uptime" in data
    assert "token_health" in data
    assert "stats" in data
    assert "total" in data["stats"]
    assert "movies" in data["stats"]
    assert "episodes" in data["stats"]
    assert "ratings" in data["stats"]

@pytest.mark.asyncio(loop_scope="module")
async def test_backup_and_restore_endpoints():
    """Verify zip backup export, safe restoration, and security controls."""
    client = make_async_test_client(app)
    # 1. Unauthenticated backup request -> 401
    client.cookies.clear()
    with patch.object(Config, "WEBHOOK_SECRET", "test_secret"):
        res_unauth = await client.get("/api/backup")
        assert res_unauth.status_code == 401

        # 2. Authenticated backup request -> 200 with zip content
        client.cookies.set("admin_token", "test_secret")
        res_backup = await client.get("/api/backup")
        assert res_backup.status_code == 200
        assert "application/zip" in res_backup.headers["content-type"]
        assert "attachment; filename=" in res_backup.headers["content-disposition"]

        # Verify zip content structure
        import io
        import zipfile
        zf = zipfile.ZipFile(io.BytesIO(res_backup.content))
        namelist = zf.namelist()
        assert isinstance(namelist, list)
        exported_preview = await client.post(
            "/api/restore/preview",
            files={"backup_file": ("exported.zip", res_backup.content, "application/zip")},
        )
        assert exported_preview.status_code == 200
        assert exported_preview.json()["format_version"] == 1

        # 3. Preview a valid archive before applying it.
        test_zip_buf = io.BytesIO()
        with zipfile.ZipFile(test_zip_buf, "w") as test_zf:
            test_zf.writestr("data/cowatch_shows.json", json.dumps(["Severance", "Succession"]))
            test_zf.writestr("data/cowatch_devices.json", json.dumps(["Living Room Apple TV"]))

        test_zip_buf.seek(0)
        files = {"backup_file": ("backup.zip", test_zip_buf.getvalue(), "application/zip")}
        prior_shows = Config.CO_WATCH_DATA_FILE.read_bytes() if Config.CO_WATCH_DATA_FILE.exists() else None
        preview = await client.post("/api/restore/preview", files=files)
        assert preview.status_code == 200
        preview_data = preview.json()
        assert preview_data["valid"] is True
        assert preview_data["compatibility"] == "legacy"
        assert "data/cowatch_shows.json" in preview_data["categories"]["household data"]["replace"]
        assert (Config.CO_WATCH_DATA_FILE.read_bytes() if Config.CO_WATCH_DATA_FILE.exists() else None) == prior_shows

        res_restore = await client.post("/api/restore", files=files)
        assert res_restore.status_code == 200
        restore_data = res_restore.json()
        assert restore_data["status"] == "success"
        assert "data/cowatch_shows.json" in restore_data["restored"]
        assert "data/cowatch_devices.json" in restore_data["restored"]

        # Unsafe paths are rejected during preview and restore without being written.
        malicious_zip = io.BytesIO()
        with zipfile.ZipFile(malicious_zip, "w") as malicious_archive:
            malicious_archive.writestr("../evil_file.txt", "evil")
        malicious_file = {"backup_file": ("unsafe.zip", malicious_zip.getvalue(), "application/zip")}
        preview_unsafe = await client.post("/api/restore/preview", files=malicious_file)
        assert preview_unsafe.status_code == 400
        restore_unsafe = await client.post("/api/restore", files=malicious_file)
        assert restore_unsafe.status_code == 400

        # 4. Unauthenticated restore request -> 401
        client.cookies.clear()
        res_restore_denied = await client.post("/api/restore", files=files)
        assert res_restore_denied.status_code == 401

        # Cleanup restored test devices
        if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
            Config.CO_WATCH_DEVICES_DATA_FILE.unlink()
        cowatch_mgr._devices.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_dashboard_privacy_shield_and_script_syntax():
    """Verify that HTML dashboards have balanced scripts and robust non-admin privacy shielding."""
    client = make_async_test_client(app)
    playback_mgr.clear()
    recent_events.clear()

    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"), \
         patch.object(Config, "CO_WATCH_USER", "bon.vivant"), \
         patch.object(Config, "CO_WATCH_PLAYERS", ["selits's Fire TV", "Google TV"]):
        cowatch_mgr._shows = ["Secret CoWatch Show Alpha", "Secret CoWatch Show Beta"]
        cowatch_mgr._devices = ["selits's Fire TV", "Google TV"]

        try:
            # 1. Unauthenticated / Non-Admin Dashboard Request
            client.cookies.clear()
            res = await client.get("/")
            assert res.status_code == 200
            html = res.text

            # Validate that scripts have perfectly balanced curly braces (no JavaScript syntax errors)
            import re
            scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
            assert len(scripts) >= 1
            for idx, s in enumerate(scripts):
                open_count = s.count('{')
                close_count = s.count('}')
                assert open_count == close_count, f"Script {idx} in dashboard has unbalanced braces: open={open_count}, close={close_count}"

            # Privacy checks for non-admin viewers:
            # Show titles must be hidden
            assert "Secret CoWatch Show Alpha" not in html
            assert "Secret CoWatch Show Beta" not in html
            assert "2 shared shows configured" in html
            assert "Unlock admin access to view titles" in html

            # Personal device names and Devices rule must be completely hidden
            assert "selits's Fire TV" not in html
            assert "Google TV" not in html
            assert "Devices:" not in html

            # Partner username completely masked
            assert "@bon.vivant" not in html
            assert "@bo********" not in html
            assert "@●●●●●●●●" in html

            # Webhook secret masked
            assert "testsecret" not in html

            # 2. Authenticated Admin Dashboard Request
            client.cookies.set("admin_token", "testsecret")
            res_admin = await client.get("/")
            assert res_admin.status_code == 200
            html_admin = res_admin.text

            # Admin sees full show titles and device names in allowed devices whitelist
            assert "Secret CoWatch Show Alpha" in html_admin
            assert "Secret CoWatch Show Beta" in html_admin
            assert "selits&#x27;s Fire TV" in html_admin
            assert "Google TV" in html_admin
            assert "Allowed Devices Whitelist" in html_admin
            assert "testsecret" in html_admin
        finally:
            client.cookies.clear()
            cowatch_mgr._shows.clear()
            cowatch_mgr._devices.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_auth_page_script_syntax():
    """Verify that /auth page scripts (both locked and unlocked) have balanced braces."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "testsecret"):
        # 1. Unauthenticated locked /auth
        client.cookies.clear()
        res_locked = await client.get("/auth")
        assert res_locked.status_code == 401
        import re
        scripts_locked = re.findall(r'<script>(.*?)</script>', res_locked.text, re.DOTALL)
        assert len(scripts_locked) >= 1
        for idx, s in enumerate(scripts_locked):
            assert s.count('{') == s.count('}'), f"Locked auth script {idx} unbalanced"

        # 2. Authenticated /auth
        client.cookies.set("admin_token", "testsecret")
        res = await client.get("/auth")
        assert res.status_code == 200
        scripts = re.findall(r'<script>(.*?)</script>', res.text, re.DOTALL)
        assert len(scripts) >= 1
        for idx, s in enumerate(scripts):
            assert s.count('{') == s.count('}'), f"Auth script {idx} unbalanced"
        client.cookies.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_api_logs_access_control():
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "supersecret_test_token"
        client = make_async_test_client(app)

        # Non-admin request should receive 401
        res = await client.get("/api/logs")
        assert res.status_code == 401

        # Demo mode allows log viewing without admin auth
        res_demo = await client.get("/api/logs?demo=true")
        assert res_demo.status_code == 200
        demo_data = res_demo.json()
        assert "simulated journal" in demo_data["source"]
        assert len(demo_data["lines"]) > 0

        # Admin unlocked request succeeds
        client.cookies.set("admin_token", "supersecret_test_token")
        res_admin = await client.get("/api/logs?lines=20")
        assert res_admin.status_code == 200
        admin_data = res_admin.json()
        assert "source" in admin_data
        assert isinstance(admin_data["lines"], list)
    finally:
        Config.WEBHOOK_SECRET = orig_secret

def test_log_sanitization_and_buffer():
    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "my_webhook_secret_value"

        # Test token masking in query param
        line1 = "GET /webhook?token=my_webhook_secret_value HTTP/1.1 200"
        sanitized1 = log_mgr.sanitize_line(line1)
        assert "my_webhook_secret_value" not in sanitized1
        assert "token=●●●●" in sanitized1 or "●●●●" in sanitized1

        # Test Bearer token masking
        line2 = "Sending auth header: Bearer abc123secrettoken456"
        sanitized2 = log_mgr.sanitize_line(line2)
        assert "abc123secrettoken456" not in sanitized2
        assert "Bearer ●●●●" in sanitized2

        # Test header masking
        line3 = "Header received x-webhook-secret: raw_secret_pass"
        sanitized3 = log_mgr.sanitize_line(line3)
        assert "raw_secret_pass" not in sanitized3
        assert "x-webhook-secret: ●●●●" in sanitized3

        # Test buffer capture
        import logging
        test_log = logging.getLogger("test_buffer_logger")
        test_log.setLevel(logging.DEBUG)
        test_msg = "Scrobble event test unique buffer verify 987654"
        test_log.warning(test_msg)

        buffer_lines = log_mgr.get_buffer_logs(lines=50)
        assert any(test_msg in l for l in buffer_lines)
    finally:
        Config.WEBHOOK_SECRET = orig_secret

@pytest.mark.asyncio(loop_scope="module")
async def test_synthetic_test_webhook_unauthorized():
    """Verify POST /api/test/webhook requires admin authentication when WEBHOOK_SECRET is set."""
    client = make_async_test_client(app)
    payload = {"event": "media.scrobble", "media_type": "movie", "title": "Test Movie"}

    with patch.object(Config, "WEBHOOK_SECRET", "secret_admin_key"):
        # Unauthenticated -> 401
        res_unauth = await client.post("/api/test/webhook", json=payload)
        assert res_unauth.status_code == 401

        # Query param ?token= -> 200
        res_token = await client.post("/api/test/webhook?token=secret_admin_key", json=payload)
        assert res_token.status_code == 200

        # Admin cookie -> 200
        client.cookies.set("admin_token", "secret_admin_key")
        res_cookie = await client.post("/api/test/webhook", json=payload)
        assert res_cookie.status_code == 200
        client.cookies.clear()

def test_settings_manager_lifecycle(tmp_path):
    """Verify SettingsManager handles persistence, defaults, and toggles."""
    from app.services.settings_manager import SettingsManager

    settings_file = tmp_path / "settings.json"
    clean_data_dir = tmp_path / "clean_data"
    clean_data_dir.mkdir()
    mgr = SettingsManager(settings_file=settings_file, data_dir=clean_data_dir)

    # Defaults: servers disabled by default out of the box for fresh install
    assert mgr.is_server_enabled("plex") is False
    assert mgr.is_server_enabled("jellyfin") is False
    assert mgr.is_server_enabled("emby") is False
    # Trackers enabled by default
    assert mgr.is_tracker_enabled("trakt") is True
    assert mgr.is_tracker_enabled("simkl") is True
    assert mgr.is_tracker_enabled("anilist") is True
    assert mgr.is_tracker_enabled("mal") is True

    # Upgrade scenario: existing install with previous data preserves Plex enabled, Jellyfin/Emby disabled
    upgrade_data_dir = tmp_path / "upgrade_data"
    upgrade_data_dir.mkdir()
    (upgrade_data_dir / "cowatch_shows.json").write_text("[]")
    upgrade_mgr = SettingsManager(settings_file=tmp_path / "upgrade_settings.json", data_dir=upgrade_data_dir)
    assert upgrade_mgr.is_server_enabled("plex") is True
    assert upgrade_mgr.is_server_enabled("jellyfin") is False
    assert upgrade_mgr.is_server_enabled("emby") is False

    # Toggle server on
    mgr.set_server_enabled("plex", True)
    assert mgr.is_server_enabled("plex") is True
    assert mgr.is_server_enabled("jellyfin") is False

    # Toggle tracker off
    mgr.set_tracker_enabled("simkl", False)
    assert mgr.is_tracker_enabled("simkl") is False
    assert mgr.is_tracker_enabled("trakt") is True

    # Verify persistence
    mgr2 = SettingsManager(settings_file=settings_file, data_dir=clean_data_dir)
    assert mgr2.is_server_enabled("plex") is True
    assert mgr2.is_server_enabled("jellyfin") is False
    assert mgr2.is_tracker_enabled("simkl") is False
    assert mgr2.is_tracker_enabled("trakt") is True

@pytest.mark.asyncio(loop_scope="module")
async def test_settings_api_and_toggle_endpoint():
    """Verify GET /api/settings and POST /api/settings/toggle endpoints."""
    from app.services.settings_manager import settings_mgr

    orig_secret = Config.WEBHOOK_SECRET
    try:
        Config.WEBHOOK_SECRET = "secret123"
        client = make_async_test_client(app)

        # GET is public status (returns 200)
        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()
        assert "settings" in data
        assert "servers" in data["settings"]
        assert "trackers" in data["settings"]

        # POST /api/settings/toggle unauthorized -> 401
        res_unauth = await client.post("/api/settings/toggle", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": False
        })
        assert res_unauth.status_code == 401

        # Toggle via POST authorized
        res_toggle = await client.post("/api/settings/toggle?token=secret123", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": False
        })
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False
        assert settings_mgr.is_server_enabled("jellyfin") is False

        # Reset back
        res_reset = await client.post("/api/settings/toggle?token=secret123", json={
            "category": "server",
            "key": "jellyfin",
            "enabled": True
        })
        assert res_reset.status_code == 200
        assert settings_mgr.is_server_enabled("jellyfin") is True
    finally:
        Config.WEBHOOK_SECRET = orig_secret

@pytest.mark.asyncio(loop_scope="module")
async def test_settings_hub_endpoints_and_masking(tmp_path, monkeypatch):
    """Verify GET and POST /api/settings with credential masking, auth protection, and live reload."""
    from app.services.settings_manager import settings_mgr
    from app.main import simkl, arr_bridge
    import app.main as main_module
    test_settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mgr, "settings_file", test_settings_file)
    settings_mgr._settings = {
        "servers": {"plex": True, "jellyfin": False, "emby": False},
        "trackers": {"trakt": True, "simkl": False, "anilist": False, "mal": False},
        "reconciliation": settings_mgr._detect_default_reconciliation(),
        "credentials": settings_mgr._detect_default_credentials(),
        "arr": settings_mgr._detect_default_arr(),
    }
    settings_mgr._save_settings()

    client = make_async_test_client(app)

    # Anonymous callers get only public enablement flags, not private config.
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "supersecret123")
    public_res = await client.get("/api/settings")
    assert public_res.status_code == 200
    public_settings = public_res.json()["settings"]
    assert set(public_settings) == {"servers", "trackers", "multi_server_mirroring"}

    # Admin GET returns full settings with credentials masked.
    res = await client.get("/api/settings", headers={"x-webhook-secret": "supersecret123"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "servers" in data
    assert "trackers" in data
    assert "credentials" in data
    assert "reconciliation" in data
    assert "arr" in data

    # 2. Auth Protection: Require admin when secret configured
    unauth_res = await client.post("/api/settings", json={"servers": {"jellyfin": True}})
    assert unauth_res.status_code == 401
    assert "Unauthorized" in unauth_res.json()["detail"]

    # 3. Successful update as Admin
    client.cookies.set("admin_token", "supersecret123")

    payload = {
        "servers": {"jellyfin": True},
        "trackers": {"simkl": True},
        "credentials": {
            "simkl": {
                "client_id": "test_simkl_client_id_999",
                "client_secret": "simkl_secret_raw_pass_8888",  # gitleaks:allow -- synthetic redaction fixture
            }
        },
        "reconciliation": {
            "server_type": "plex",
            "plex_url": "http://127.0.0.1:32400",
            "plex_token": "my_super_secret_plex_token_1111",
        },
        "arr": {
            "sonarr_url": "http://127.0.0.1:8989",
            "sonarr_api_key": "sonarr_key_raw_2222",
            "radarr_url": "http://127.0.0.1:7878",
            "radarr_api_key": "radarr_key_raw_3333",
            "auto_add_watchlist": True,
            "search_on_add": True,
        },
    }
    main_module._arr_acquisition_config_cache.update({"data": {"stale": True}, "expires_at": time.monotonic() + 300})
    update_res = await client.post("/api/settings", json=payload)
    assert update_res.status_code == 200
    assert main_module._arr_acquisition_config_cache["data"] is None
    up_data = update_res.json()
    assert up_data["status"] == "success"
    assert up_data["settings"]["servers"]["jellyfin"] is True
    assert up_data["settings"]["trackers"]["simkl"] is True

    # Verify live in-memory reload
    assert simkl.effective_client_id == "test_simkl_client_id_999"
    assert arr_bridge.sonarr.base_url == "http://127.0.0.1:8989"
    assert arr_bridge.sonarr.api_key == "sonarr_key_raw_2222"
    assert arr_bridge.radarr.base_url == "http://127.0.0.1:7878"
    assert arr_bridge.radarr.api_key == "radarr_key_raw_3333"

    # 4. Mask preservation test: Submitting masked string does NOT overwrite real secret
    mask_payload = {
        "credentials": {
            "simkl": {
                "client_id": "test_simkl_client_id_999",
                "client_secret": "••••••••8888",
            }
        },
        "arr": {
            "sonarr_api_key": "••••••••2222",
        },
    }
    mask_res = await client.post("/api/settings", json=mask_payload)
    assert mask_res.status_code == 200

    # Unmasked check in settings manager
    unmasked_simkl = settings_mgr.get_tracker_credentials("simkl", mask=False)
    assert unmasked_simkl["client_secret"] == "simkl_secret_raw_pass_8888"

    unmasked_arr = settings_mgr.get_arr_settings(mask=False)
    assert unmasked_arr["sonarr_api_key"] == "sonarr_key_raw_2222"

@pytest.mark.asyncio(loop_scope="module")
async def test_settings_modal_dashboard_rendering():
    """Verify that #settings-modal, header button, and contextual deep links render on dashboard."""
    client = make_async_test_client(app)

    # 1. Main dashboard renders #settings-modal and Settings Hub header button
    res = await client.get("/")
    assert res.status_code == 200
    html_content = res.text
    css_content = (await client.get("/static/dashboard.css")).text

    assert 'id="settings-modal"' in html_content
    assert "Settings Hub" in html_content
    assert "{{SETTINGS_HEADER_BTN}}" not in html_content
    assert '<button onclick="openSettingsModal()"' in html_content
    assert ", #settings-modal {" in css_content
    assert "openSettingsModal" in html_content
    assert "saveAllSettingsFromModal" in html_content
    assert "testSettingsArrConnection" in html_content

    # Contextual deep link calls
    assert 'openSettingsModal(\'servers\')' in html_content or "openSettingsModal('servers')" in html_content
    assert "openSettingsModal('trackers', 'simkl')" in html_content
    assert "openSettingsModal('trackers', 'anilist')" in html_content
    assert "openSettingsModal('automation')" in html_content

    # 2. Demo dashboard also includes Settings Hub
    demo_res = await client.get("/demo")
    assert demo_res.status_code == 200
    assert 'id="settings-modal"' in demo_res.text
    assert "Settings Hub" in demo_res.text
    assert "{{SETTINGS_HEADER_BTN}}" not in demo_res.text
    assert '<button onclick="openSettingsModal()"' in demo_res.text

@pytest.mark.asyncio(loop_scope="module")
async def test_admin_unlock_rate_limiting():
    """Verify admin unlock enforces rate limiting (429) after multiple failed attempts."""
    from app.main import _failed_unlock_attempts
    _failed_unlock_attempts.clear()

    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "correct_secret_phrase"):
        # First 5 failed attempts return 401
        for i in range(5):
            res = await client.post("/api/admin/unlock", json={"token": f"wrong_{i}"})
            assert res.status_code == 401
            assert "Invalid admin secret" in res.json().get("detail", "")

        # 6th attempt triggers 429 rate limit
        res_blocked = await client.post("/api/admin/unlock", json={"token": "wrong_6"})
        assert res_blocked.status_code == 429
        assert "Too many failed unlock attempts" in res_blocked.json().get("detail", "")
        assert "Retry-After" in res_blocked.headers

    _failed_unlock_attempts.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_connection_endpoint_url_validation():
    """Verify test-connection endpoints reject non-http/https URL schemes."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "super_admin_pass"):
        client.cookies.set("admin_token", "super_admin_pass")

        # Test sync connection with invalid scheme
        res_sync = await client.post("/api/sync/test-connection", json={"server": "plex", "url": "file:///etc/passwd", "token": "tok"})
        assert res_sync.status_code == 400
        assert "must start with http:// or https://" in res_sync.json().get("detail", "")

        res_jf = await client.post("/api/sync/test-connection", json={"server": "jellyfin", "url": "gopher://127.0.0.1", "token": "tok"})
        assert res_jf.status_code == 400
        assert "must start with http:// or https://" in res_jf.json().get("detail", "")

        # Test Arr connection with invalid scheme
        res_sonarr = await client.post("/api/arr/test-connection", json={"app": "sonarr", "url": "ftp://malicious.host", "api_key": "key"})
        assert res_sonarr.status_code == 400
        assert "must start with http:// or https://" in res_sonarr.json().get("detail", "")

        res_radarr = await client.post("/api/arr/test-connection", json={"app": "radarr", "url": "data:text/html,boom", "api_key": "key"})
        assert res_radarr.status_code == 400
        assert "must start with http:// or https://" in res_radarr.json().get("detail", "")

@pytest.mark.asyncio(loop_scope="module")
async def test_trakt_dynamic_credentials_and_token_management(tmp_path):
    """Verify TraktClient dynamic credential resolution, update_credentials, is_enabled, and delete_tokens."""
    from app.clients.trakt_client import TraktClient
    from app.services.settings_manager import settings_mgr

    tokens_file = tmp_path / "trakt_tokens.json"
    tokens_file.write_text(json.dumps({"access_token": "valid_token", "refresh_token": "refr_123"}))

    client = TraktClient(config=Config, tokens_file=tokens_file)
    assert client.is_authenticated() is True

    # Update in settings_mgr
    settings_mgr.update_tracker_credentials("trakt", {"client_id": "dynamic_trakt_id", "client_secret": "dynamic_trakt_secret"})
    client.update_credentials("dynamic_trakt_id", "dynamic_trakt_secret")
    assert client.effective_client_id == "dynamic_trakt_id"
    assert client.effective_client_secret == "dynamic_trakt_secret"

    # Enabled status check
    settings_mgr.set_tracker_enabled("trakt", False)
    assert client.is_enabled() is False
    settings_mgr.set_tracker_enabled("trakt", True)
    assert client.is_enabled() is True

    # delete_tokens
    client.delete_tokens()
    assert client.is_authenticated() is False
    assert not tokens_file.exists()

@pytest.mark.asyncio(loop_scope="module")
async def test_trakt_poll_for_token_client_credentials_and_errors(tmp_path):
    """Verify TraktClient.poll_for_token forwards client_secret and raises friendly error on 401."""
    from app.clients.trakt_client import TraktClient

    tokens_file = tmp_path / "trakt_tokens.json"

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        assert data.get("client_id") == "test_cid"
        assert data.get("client_secret") == "test_csec"
        assert data.get("code") == "dev_123"
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Bad client credentials"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    trakt = TraktClient(config=Config, tokens_file=tokens_file, client=mock_client, client_id="test_cid", client_secret="test_csec")

    with pytest.raises(PermissionError) as exc_info:
        await trakt.poll_for_token("dev_123")
    assert "Invalid client credentials (401)" in str(exc_info.value)
    assert "Settings Hub" in str(exc_info.value)

@pytest.mark.asyncio(loop_scope="module")
async def test_trakt_api_status_and_disconnect_endpoints(tmp_path):
    """Verify /api/trakt/status and /api/trakt/disconnect API endpoints."""
    from app.main import app, user_mgr, trakt

    client = make_async_test_client(app)

    # 1. Demo mode status
    resp = await client.get("/api/trakt/status?demo=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["configured"] is True
    assert data["authenticated"] is True
    assert data["user"] == "demo_viewer"

    # 2. Live status
    resp = await client.get("/api/trakt/status")
    assert resp.status_code == 200
    live_data = resp.json()
    assert "enabled" in live_data
    assert "configured" in live_data
    assert "authenticated" in live_data

    # 3. Disconnect requires admin (or open mode if WEBHOOK_SECRET is empty)
    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        unauth = await client.post("/api/trakt/disconnect")
        assert unauth.status_code == 401

        auth = await client.post("/api/trakt/disconnect", headers={"x-webhook-secret": "supersecret"})
        assert auth.status_code == 200
        assert auth.json()["status"] == "ok"

@pytest.mark.asyncio(loop_scope="module")
async def test_settings_hub_trakt_credential_sync_and_tracker_statuses():
    """Verify PUT /api/settings syncs Trakt credentials to both trakt and user_mgr dynamically."""
    from app.main import app, trakt, user_mgr, anilist, mal
    from app.services.settings_manager import settings_mgr

    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "adminkey"):
        payload = {
            "credentials": {
                "trakt": {
                    "client_id": "synced_trakt_id",
                    "client_secret": "synced_trakt_secret",
                },
                "simkl": {
                    "client_id": "synced_simkl_id",
                    "client_secret": "synced_simkl_secret",
                },
                "mal": {
                    "client_id": "synced_mal_id",
                    "client_secret": "synced_mal_secret",
                },
            }
        }
        res = await client.put("/api/settings", json=payload, headers={"x-webhook-secret": "adminkey"})
        assert res.status_code == 200

        # Verify trakt and user_mgr dynamically updated
        assert trakt.client_id == "synced_trakt_id"
        assert trakt.client_secret == "synced_trakt_secret"
        default_client = user_mgr.get_client("default")
        assert default_client.client_id == "synced_trakt_id"
        assert default_client.client_secret == "synced_trakt_secret"

        # Verify /api/anilist/status and /api/mal/status respect dynamic is_enabled
        settings_mgr.set_tracker_enabled("anilist", False)
        ani_res = await client.get("/api/anilist/status")
        assert ani_res.status_code == 200
        assert ani_res.json()["enabled"] is False

        settings_mgr.set_tracker_enabled("anilist", True)
        ani_res2 = await client.get("/api/anilist/status")
        assert ani_res2.status_code == 200
        assert ani_res2.json()["enabled"] is True

        settings_mgr.set_tracker_enabled("mal", False)
        mal_res = await client.get("/api/mal/status")
        assert mal_res.status_code == 200
        assert mal_res.json()["enabled"] is False

        settings_mgr.set_tracker_enabled("mal", True)
        mal_res2 = await client.get("/api/mal/status")
        assert mal_res2.status_code == 200
        assert mal_res2.json()["enabled"] is True

@pytest.mark.asyncio(loop_scope="module")
async def test_https_and_proxy_headers_readiness(monkeypatch):
    """Verify proxy-headers middleware, conditional HSTS header, and EXTERNAL_URL support."""
    from app.main import app
    from app.config import Config

    client = make_async_test_client(app)

    # 1. Plain HTTP request: no HSTS header emitted
    res_http = await client.get("/")
    assert res_http.status_code == 200
    assert "Strict-Transport-Security" not in res_http.headers

    # 2. HTTPS request via X-Forwarded-Proto header: HSTS header emitted
    res_https = await client.get("/", headers={"x-forwarded-proto": "https"})
    assert res_https.status_code == 200
    assert "Strict-Transport-Security" in res_https.headers
    assert "max-age=31536000" in res_https.headers["Strict-Transport-Security"]

    # 3. EXTERNAL_URL overrides dashboard webhook URLs
    monkeypatch.setattr(Config, "EXTERNAL_URL", "https://omniscrobble.securehomelab.net")
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test_secret_abc")
    res_dash = await client.get("/?token=test_secret_abc")
    assert res_dash.status_code == 200
    assert "https://omniscrobble.securehomelab.net/webhook?token=test_secret_abc" in res_dash.text

@pytest.mark.asyncio(loop_scope="module")
async def test_rules_settings_api_and_persistence(tmp_path, monkeypatch):
    """Verify GET and POST /api/settings/rules, clamping, auth, and persistence."""
    from app.services.settings_manager import settings_mgr
    from app.main import app
    from app.config import Config

    test_settings_file = tmp_path / "settings_rules_test.json"
    monkeypatch.setattr(settings_mgr, "settings_file", test_settings_file)
    settings_mgr._load_settings()

    client = make_async_test_client(app)

    # 1. GET /api/settings/rules returns default rules
    res = await client.get("/api/settings/rules")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    rules = data["rules"]
    assert rules["scrobble_threshold"] == 80
    assert rules["movie_scrobble_threshold"] == 90
    assert rules["min_duration_seconds"] == 300
    assert rules["apply_min_duration_to_episodes"] is False
    assert isinstance(rules["ignore_libraries"], list)

    # 2. Auth protection for POST /api/settings/rules when secret configured
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "rules_secret_999")
    unauth_res = await client.post("/api/settings/rules", json={"scrobble_threshold": 85})
    assert unauth_res.status_code == 401
    assert "Unauthorized" in unauth_res.json()["detail"]

    # 3. Successful update with admin token
    update_res = await client.post(
        "/api/settings/rules?token=rules_secret_999",
        json={
            "scrobble_threshold": 85,
            "movie_scrobble_threshold": 95,
            "min_duration_seconds": 450,
            "apply_min_duration_to_episodes": True,
            "ignore_libraries": ["Trailers", "Extras"],
            "ignore_path_patterns": [r"/extras/", r"\.sample\."],
        },
    )
    assert update_res.status_code == 200
    updated_rules = update_res.json()["rules"]
    assert updated_rules["scrobble_threshold"] == 85
    assert updated_rules["movie_scrobble_threshold"] == 95
    assert updated_rules["min_duration_seconds"] == 450
    assert updated_rules["apply_min_duration_to_episodes"] is True
    assert updated_rules["ignore_libraries"] == ["Trailers", "Extras"]
    assert updated_rules["ignore_path_patterns"] == [r"/extras/", r"\.sample\."]

    # 4. Clamping verification: thresholds clamp to [50, 95], min_duration clamps to >= 0
    clamp_res = await client.post(
        "/api/settings/rules?token=rules_secret_999",
        json={
            "scrobble_threshold": -50,
            "movie_scrobble_threshold": 200,
            "min_duration_seconds": -99,
        },
    )
    assert clamp_res.status_code == 200
    clamped_rules = clamp_res.json()["rules"]
    assert clamped_rules["scrobble_threshold"] == 50
    assert clamped_rules["movie_scrobble_threshold"] == 95
    assert clamped_rules["min_duration_seconds"] == 0

    # 5. Full POST /api/settings updates rules block
    full_settings_res = await client.post(
        "/api/settings?token=rules_secret_999",
        json={
            "rules": {
                "scrobble_threshold": 82,
                "movie_scrobble_threshold": 88,
            }
        },
    )
    assert full_settings_res.status_code == 200
    final_rules = settings_mgr.get_rules_settings()
    assert final_rules["scrobble_threshold"] == 82
    assert final_rules["movie_scrobble_threshold"] == 88

def test_rules_duration_filter_bypass():
    """Verify minimum playback duration filtering and episode exemption toggle."""
    from app.services.settings_manager import settings_mgr
    from app.plex_parser import ParsedMedia

    # Configure min duration of 300 seconds, episodes exempted by default
    settings_mgr.update_rules_settings({
        "min_duration_seconds": 300,
        "apply_min_duration_to_episodes": False,
        "ignore_libraries": [],
        "ignore_path_patterns": [],
    })

    # Short movie (180s < 300s): rejected
    short_movie = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Short Film",
        duration_ms=180000,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(short_movie)
    assert allowed is False
    assert "below minimum threshold" in reason

    # Standard movie (600s >= 300s): allowed
    std_movie = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Feature Film",
        duration_ms=600000,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(std_movie)
    assert allowed is True

    # Short episode (180s): allowed because apply_min_duration_to_episodes is False
    short_episode = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        show_title="Mini Series",
        season=1,
        episode=1,
        title="Intro Clip",
        duration_ms=180000,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(short_episode)
    assert allowed is True

    # Media with no duration specified: allowed
    no_dur_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Unknown Duration Movie",
        duration_ms=None,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(no_dur_media)
    assert allowed is True

    # Enable apply_min_duration_to_episodes -> short episode now rejected
    settings_mgr.update_rules_settings({"apply_min_duration_to_episodes": True})
    allowed, reason = settings_mgr.is_media_allowed(short_episode)
    assert allowed is False
    assert "below minimum threshold" in reason

def test_rules_library_ignore():
    """Verify library section title filtering against configured ignore list."""
    from app.services.settings_manager import settings_mgr
    from app.plex_parser import ParsedMedia

    settings_mgr.update_rules_settings({
        "ignore_libraries": ["Home Videos", "Trailers", "Fitness"],
        "min_duration_seconds": 0,
        "ignore_path_patterns": [],
    })

    # Ignored library (exact match)
    trailer_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Official Trailer",
        library_section_title="Trailers",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(trailer_media)
    assert allowed is False
    assert "Trailers" in reason
    assert "ignored in rules" in reason

    # Ignored library (case-insensitive match)
    case_trailer = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Home Recording",
        library_section_title="home videos",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(case_trailer)
    assert allowed is False
    assert "ignored in rules" in reason

    # Allowed library
    movie_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Interstellar",
        library_section_title="Movies",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(movie_media)
    assert allowed is True

    # Media with no library title
    no_lib_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Independent Film",
        library_section_title=None,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(no_lib_media)
    assert allowed is True

def test_rules_path_regex_ignore():
    """Verify file path regex pattern matching and error-resilient evaluation."""
    from app.services.settings_manager import settings_mgr
    from app.plex_parser import ParsedMedia

    settings_mgr.update_rules_settings({
        "min_duration_seconds": 0,
        "ignore_libraries": [],
        "ignore_path_patterns": [r"/extras/", r"\.sample\.", r"\[invalid_regex"],
    })

    # File path matches /extras/
    extra_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Behind the Scenes",
        file_path="/mnt/storage/movies/Inception (2010)/extras/featurette.mkv",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(extra_media)
    assert allowed is False
    assert "matched ignore pattern" in reason

    # File path matches .sample.
    sample_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Sample Clip",
        file_path="/mnt/storage/downloads/film.sample.mkv",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(sample_media)
    assert allowed is False
    assert "matched ignore pattern" in reason

    # Normal file path
    main_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        file_path="/mnt/storage/movies/Inception (2010)/Inception (2010).mkv",
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(main_media)
    assert allowed is True

    # Media without file_path
    no_path_media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Streaming Movie",
        file_path=None,
        progress=100.0,
    )
    allowed, reason = settings_mgr.is_media_allowed(no_path_media)
    assert allowed is True

@pytest.mark.asyncio(loop_scope="module")
async def test_rules_effective_thresholds_and_test_webhook(monkeypatch):
    """Verify granular thresholds resolution and test webhook filtering."""
    from app.services.settings_manager import settings_mgr
    from app.main import app, get_effective_excluded_libraries
    from app.config import Config

    # Custom thresholds
    settings_mgr.update_rules_settings({
        "scrobble_threshold": 75,
        "movie_scrobble_threshold": 88,
        "ignore_libraries": ["Trailers"],
    })

    assert settings_mgr.get_effective_threshold("episode") == 75.0
    assert settings_mgr.get_effective_threshold("movie") == 88.0
    assert settings_mgr.get_effective_threshold("show") == 75.0

    # Test effective excluded libraries merging
    monkeypatch.setattr(Config, "EXCLUDED_LIBRARIES", ["Home Videos"])
    effective_libs = get_effective_excluded_libraries()
    assert "Home Videos" in effective_libs
    assert "Trailers" in effective_libs

    # Test synthetic webhook with ignored library
    client = make_async_test_client(app)
    res_test = await client.post(
        "/api/test/webhook",
        json={
            "source": "plex",
            "event_type": "scrobble",
            "media_type": "movie",
            "title": "Blocked Trailer",
            "library_section_title": "Trailers",
        },
    )
    assert res_test.status_code == 200
    data = res_test.json()
    assert data["status"] == "ignored"
    assert "reason" in data

def test_user_manager_multi_tracker_tokens_and_clients(tmp_path):
    """Verify UserClientManager secondary tracker token resolution, caching, and discovery."""
    from app.services.user_manager import UserClientManager

    mgr = UserClientManager(tokens_dir=tmp_path)
    # Check token paths
    assert mgr.get_tokens_file("partner_alice", "trakt") == tmp_path / "partner_alice_tokens.json"
    assert mgr.get_tokens_file("partner_alice", "simkl") == tmp_path / "partner_alice_simkl_tokens.json"
    assert mgr.get_tokens_file("partner_alice", "anilist") == tmp_path / "partner_alice_anilist_tokens.json"
    assert mgr.get_tokens_file("partner_alice", "mal") == tmp_path / "partner_alice_mal_tokens.json"

    # Write dummy tokens for Simkl and AniList
    simkl_file = mgr.get_tokens_file("partner_alice", "simkl")
    simkl_file.write_text(json.dumps({"access_token": "simkl_secret_tok", "user": "alice_simkl", "account_id": 999}))

    ani_file = mgr.get_tokens_file("partner_alice", "anilist")
    ani_file.write_text(json.dumps({"access_token": "ani_secret_tok", "user_name": "alice_ani"}))

    # Verify clients loaded
    simkl_client = mgr.get_tracker_client("partner_alice", "simkl")
    assert simkl_client is not None
    assert simkl_client.is_authenticated() is True
    assert simkl_client.access_token == "simkl_secret_tok"

    # Check client caching returns the same instance
    assert mgr.get_tracker_client("partner_alice", "simkl") is simkl_client

    ani_client = mgr.get_tracker_client("partner_alice", "anilist")
    assert ani_client is not None
    assert ani_client.is_authenticated() is True

    mal_client = mgr.get_tracker_client("partner_alice", "mal")
    assert mal_client is not None
    assert mal_client.is_authenticated() is False

    # Check tracker status matrix
    status = mgr.get_user_trackers_status("partner_alice")
    assert status["simkl"]["authenticated"] is True
    assert status["simkl"]["user"] == "alice_simkl"
    assert status["anilist"]["authenticated"] is True
    assert status["anilist"]["user"] == "alice_ani"
    assert status["mal"]["authenticated"] is False

    # Check discovery in list_configured_users
    users = mgr.list_configured_users()
    alice_entry = next((u for u in users if u["username"] == "partner_alice"), None)
    assert alice_entry is not None
    assert "trackers" in alice_entry
    assert alice_entry["trackers"]["simkl"]["authenticated"] is True

    # Test disconnect tracker
    mgr.disconnect_user_tracker("partner_alice", "simkl")
    assert not simkl_file.exists()
    assert mgr.is_tracker_authenticated("partner_alice", "simkl") is False

@pytest.mark.asyncio(loop_scope="module")
async def test_admin_unlock_rate_limiting_and_sanitized_logs():
    """Verify admin unlock rate limiter logs sanitized warnings and blocks repeated attacks."""
    from app.main import _failed_unlock_attempts
    _failed_unlock_attempts.clear()

    try:
        client = make_async_test_client(app)
        with patch.object(Config, "WEBHOOK_SECRET", "super_secret_webhook_pass"):
            with patch("app.main.logger.warning") as mock_warn:
                for i in range(5):
                    res = await client.post("/api/admin/unlock", json={"token": f"bad_token_{i}"})
                    assert res.status_code == 401
                assert mock_warn.call_count >= 5

                res_429 = await client.post("/api/admin/unlock", json={"token": "bad_token_6"})
                assert res_429.status_code == 429
                assert "Retry-After" in res_429.headers
                assert any("Admin unlock rate limit exceeded" in str(c) for c in mock_warn.call_args_list)

            # Successful unlock clears rate limiter for IP
            _failed_unlock_attempts["testclient"] = []
            res_ok = await client.post("/api/admin/unlock", json={"token": "super_secret_webhook_pass"})
            assert res_ok.status_code == 200
            assert "csrf_token" in res_ok.cookies
            assert "admin_token" in res_ok.cookies
    finally:
        _failed_unlock_attempts.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_csrf_double_submit_protection():
    """Verify double-submit CSRF cookie protection for state-mutating admin requests."""
    from app.main import _failed_unlock_attempts
    _failed_unlock_attempts.clear()
    client = make_async_test_client(app)
    try:
        with patch.object(Config, "WEBHOOK_SECRET", "my_secure_secret"):
            # 1. Unlock admin to obtain cookies
            unlock_res = await client.post("/api/admin/unlock", json={"token": "my_secure_secret"})
            assert unlock_res.status_code == 200
            admin_cookie = unlock_res.cookies.get("admin_token")
            csrf_cookie = unlock_res.cookies.get("csrf_token")
            assert admin_cookie is not None
            assert csrf_cookie is not None

            # 2. Mutating request (POST /api/settings) with admin & csrf cookies but NO x-csrf-token header -> 401
            client.cookies.clear()
            client.cookies.set("admin_token", admin_cookie)
            client.cookies.set("csrf_token", csrf_cookie)
            bad_req = await client.post("/api/settings", json={"plex_enabled": True})
            assert bad_req.status_code == 401

            # 3. Mutating request with admin cookie and mismatched x-csrf-token header -> 401
            client.cookies.set("csrf_token", csrf_cookie)
            bad_csrf = await client.post(
                "/api/settings",
                json={"plex_enabled": True},
                headers={"x-csrf-token": "wrong_csrf_token"},
            )
            assert bad_csrf.status_code == 401

            # 4. Mutating request with admin cookie and valid matching x-csrf-token header -> 200
            good_req = await client.post(
                "/api/settings",
                json={"plex_enabled": True},
                headers={"x-csrf-token": csrf_cookie},
            )
            assert good_req.status_code == 200

            # 5. Non-mutating request (GET /api/logs) with admin cookie does NOT require CSRF header
            get_logs = await client.get("/api/logs")
            assert get_logs.status_code == 200

            # 6. Webhook / direct API key auth via header or query param does NOT require CSRF header
            client.cookies.clear()
            token_req = await client.post("/api/settings?token=my_secure_secret", json={"plex_enabled": True})
            assert token_req.status_code == 200

            # 7. Visiting GET / with admin_token but missing csrf_token issues csrf_token cookie
            client.cookies.set("admin_token", admin_cookie)
            visit_res = await client.get("/")
            assert visit_res.status_code == 200
            assert "csrf_token" in visit_res.cookies

            # 8. Admin lock deletes both cookies
            client.cookies.set("csrf_token", csrf_cookie)
            lock_res = await client.post(
                "/api/admin/lock",
                headers={"x-csrf-token": csrf_cookie},
            )
            assert lock_res.status_code == 200
            set_cookie_header = lock_res.headers.get("set-cookie", "")
            assert "admin_token" in set_cookie_header
            assert "csrf_token" in set_cookie_header
    finally:
        _failed_unlock_attempts.clear()

def test_crypto_manager_at_rest_encryption(tmp_path):
    """Verify AES-256-GCM symmetric encryption, PBKDF2 derivation, and secure JSON roundtripping."""
    from app.services.crypto_manager import CryptoManager, derive_key, encrypt_payload, decrypt_payload, is_encrypted_payload

    passphrase = "test_super_encryption_passphrase_123"
    payload = {"client_id": "trakt_id_abc", "tokens": {"access": "xyz", "expires": 86400}}

    # 1. Direct payload encryption / decryption
    envelope = encrypt_payload(payload, passphrase)
    assert is_encrypted_payload(envelope) is True
    assert envelope["encrypted"] is True
    assert envelope["algo"] == "aes-256-gcm"
    assert "salt" in envelope
    assert "nonce" in envelope
    assert "ciphertext" in envelope

    decrypted = decrypt_payload(envelope, passphrase)
    assert decrypted == payload

    # Decrypt with wrong passphrase raises ValueError
    with pytest.raises(ValueError):
        decrypt_payload(envelope, "wrong_passphrase")

    # 2. CryptoManager file I/O with CONFIG_ENCRYPTION_KEY
    cm = CryptoManager(default_key=passphrase)
    test_file = tmp_path / "secure_tokens.json"

    cm.write_secure_json(test_file, payload)
    raw_disk_data = json.loads(test_file.read_text(encoding="utf-8"))
    assert is_encrypted_payload(raw_disk_data) is True

    loaded = cm.read_secure_json(test_file)
    assert loaded == payload

    # 3. Backward compatibility: reading unencrypted plain JSON file when encryption key is enabled
    plain_file = tmp_path / "plain_tokens.json"
    plain_data = {"plain_key": "plain_value"}
    plain_file.write_text(json.dumps(plain_data), encoding="utf-8")

    loaded_plain = cm.read_secure_json(plain_file)
    assert loaded_plain == plain_data

    # 4. Reading encrypted file without key raises ValueError
    cm_no_key = CryptoManager(default_key="")
    with patch.object(Config, "CONFIG_ENCRYPTION_KEY", ""):
        with pytest.raises(ValueError) as exc:
            cm_no_key.read_secure_json(test_file)
        assert "encrypted at rest" in str(exc.value)

@pytest.mark.asyncio(loop_scope="module")
async def test_encrypted_backup_and_restore_workflow(tmp_path):
    """Verify passphrase-protected encrypted backup archive generation and restore."""
    client = make_async_test_client(app)
    passphrase = "archive_super_secret_123"

    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # 1. Create encrypted backup via passphrase query param
        res = await client.get(f"/api/backup?token=admin_secret&passphrase={passphrase}")
        assert res.status_code == 200
        assert res.headers.get("content-type") == "application/json"
        assert "omniscrobble-backup-encrypted-" in res.headers.get("content-disposition", "")

        backup_envelope = res.json()
        from app.services.crypto_manager import is_encrypted_payload
        assert is_encrypted_payload(backup_envelope) is True

        # 2. Restore with correct passphrase
        import io
        import zipfile
        mock_zip = io.BytesIO()
        with zipfile.ZipFile(mock_zip, "w") as zf:
            zf.writestr("data/cowatch_devices.json", json.dumps(["Test Encrypted Device"]))
        mock_zip.seek(0)

        from app.services.crypto_manager import encrypt_bytes
        enc_dict = encrypt_bytes(mock_zip.getvalue(), passphrase)
        enc_payload_bytes = json.dumps(enc_dict).encode("utf-8")

        files = {"backup_file": ("backup.enc.json", enc_payload_bytes, "application/json")}
        data = {"passphrase": passphrase}
        preview_res = await client.post("/api/restore/preview?token=admin_secret", files=files, data=data)
        assert preview_res.status_code == 200
        assert preview_res.json()["valid"] is True
        restore_res = await client.post("/api/restore?token=admin_secret", files=files, data=data)
        assert restore_res.status_code == 200
        assert restore_res.json().get("status") == "success"
        assert "data/cowatch_devices.json" in restore_res.json().get("restored", [])

        # 3. Restore with incorrect passphrase returns 400
        files_bad = {"backup_file": ("backup.enc.json", enc_payload_bytes, "application/json")}
        data_bad = {"passphrase": "wrong_archive_passphrase"}
        restore_bad = await client.post("/api/restore?token=admin_secret", files=files_bad, data=data_bad)
        assert restore_bad.status_code == 400
        assert "Decryption failed" in restore_bad.json().get("detail", "")
        preview_bad = await client.post("/api/restore/preview?token=admin_secret", files=files_bad, data=data_bad)
        assert preview_bad.status_code == 400

        # 4. Standard unencrypted backup when no passphrase is given
        with patch.object(Config, "CONFIG_ENCRYPTION_KEY", ""):
            res_plain = await client.get("/api/backup?token=admin_secret")
            assert res_plain.status_code == 200
            assert res_plain.headers.get("content-type") == "application/zip"
            assert res_plain.content.startswith(b"PK")

    # Cleanup restored test devices
    if Config.CO_WATCH_DEVICES_DATA_FILE.exists():
        Config.CO_WATCH_DEVICES_DATA_FILE.unlink()
    cowatch_mgr._devices.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_local_backup_snapshot_retention(tmp_path):
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"), \
         patch.object(Config, "BASE_DIR", tmp_path), \
         patch.object(Config, "BACKUP_RETENTION_COUNT", 2), \
         patch.object(Config, "CONFIG_ENCRYPTION_KEY", ""):
        denied = await client.post("/api/backup/snapshots")
        assert denied.status_code == 401
        client.cookies.set("admin_token", "admin_secret")
        for _ in range(3):
            created = await client.post("/api/backup/snapshots")
            assert created.status_code == 200
        listed = await client.get("/api/backup/snapshots")
        assert listed.status_code == 200
        data = listed.json()
        assert data["retention_count"] == 2
        assert len(data["snapshots"]) == 2
        assert all(item["filename"].endswith(".zip") for item in data["snapshots"])
        assert all(not {"contents", "token", "passphrase"}.intersection(item) for item in data["snapshots"])
        assert all((tmp_path / "data" / "backups" / item["filename"]).stat().st_mode & 0o777 == 0o600
                   for item in data["snapshots"])

@pytest.mark.asyncio(loop_scope="module")
async def test_token_health_monitor_and_alerts():
    """Verify TokenHealthMonitor proactive refresh, alert dispatching, and /api/health/tokens endpoint."""
    from app.services.token_health_monitor import TokenHealthMonitor, token_health_mgr
    import time

    monitor = TokenHealthMonitor()

    # 1. Healthy Trakt token (>24 hours)
    mock_trakt = MagicMock()
    mock_trakt.client_id = "test_id"
    mock_trakt.is_authenticated.return_value = True
    now = time.time()
    mock_trakt.load_tokens.return_value = {
        "access_token": "valid_token",
        "created_at": now - 3600,
        "expires_in": 86400 * 30,
    }
    trakt_health = await monitor.evaluate_trakt(mock_trakt)
    assert trakt_health["status"] == "healthy"
    assert trakt_health["needs_reauth"] is False

    # 2. Trakt token near expiry (<= 24 hours), proactive refresh succeeds
    mock_trakt.load_tokens.return_value = {
        "access_token": "valid_token",
        "created_at": now - 3600,
        "expires_in": 7200,
    }
    mock_trakt.refresh_token = AsyncMock(return_value={"access_token": "refreshed_access_token"})
    trakt_refreshed = await monitor.evaluate_trakt(mock_trakt)
    assert trakt_refreshed["status"] == "healthy"
    assert trakt_refreshed["needs_reauth"] is False
    mock_trakt.refresh_token.assert_awaited_once()

    # 3. Trakt token near expiry, proactive refresh fails -> near_expiry & needs_reauth
    mock_trakt.refresh_token = AsyncMock(side_effect=Exception("Trakt API down"))
    trakt_failing = await monitor.evaluate_trakt(mock_trakt)
    assert trakt_failing["status"] == "near_expiry"
    assert trakt_failing["needs_reauth"] is True

    # 4. Full check cycle dispatches notification via notifier
    with patch("app.services.notifier.notifier.send_token_expiry_alert", new_callable=AsyncMock) as mock_alert:
        results = await monitor.run_check_cycle(trakt_client=mock_trakt)
        assert "trakt" in results
        assert results["trakt"]["needs_reauth"] is True
        mock_alert.assert_awaited_once()

    # 5. GET /api/health/tokens endpoint
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        # Unauthenticated returns 401
        res_unauth = await client.get("/api/health/tokens")
        assert res_unauth.status_code == 401

        # Authenticated returns structured health dictionary
        res_auth = await client.get("/api/health/tokens?token=admin_secret")
        assert res_auth.status_code == 200
        data = res_auth.json()
        assert "trakt" in data
        assert "service" in data["trakt"]
        assert "mal" in data

@pytest.mark.asyncio(loop_scope="module")


@pytest.mark.asyncio(loop_scope="function")




@pytest.mark.asyncio(loop_scope="module")


@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_auth_rejections_are_counted_without_retaining_request_data():
    from app.services.webhook_debugger import webhook_debugger

    client = make_async_test_client(app)
    webhook_debugger.clear()
    paths = ("/webhook", "/webhook/jellyfin", "/webhook/emby", "/api/scrobble", "/sonarr", "/radarr")
    supplied_secret = "untrusted-request-secret"
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        for path in paths:
            request_options = {"headers": {"x-webhook-secret": supplied_secret}}
            if path == "/api/scrobble":
                request_options["json"] = {"title": "Synthetic diagnostic"}
            response = await client.post(path, **request_options)
            assert response.status_code == 401

        health = await client.get("/api/health/recovery?token=admin_secret")
        assert health.status_code == 200
        webhook_check = next(check for check in health.json()["checks"] if check["id"] == "webhook_auth")
        assert webhook_check["auth_rejections"]["count"] == len(paths)
        assert set(webhook_check["auth_rejections"]["by_endpoint"]) == set(paths)
        assert supplied_secret not in health.text

    assert webhook_debugger.get_history() == []
    for _ in range(105):
        webhook_debugger.record_auth_rejection("plex", "/webhook")
    assert webhook_debugger.get_auth_rejection_summary()["count"] == 100
    webhook_debugger.clear()

@pytest.mark.asyncio(loop_scope="module")
async def test_csrf_cross_site_protection():
    """Verify Sec-Fetch-Site: cross-site is rejected for cookie-authenticated admin mutating requests."""
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        client.cookies.set("admin_token", "admin_secret")

        # GET request with cross-site is allowed
        res_get = await client.get("/api/debug/webhooks", headers={"sec-fetch-site": "cross-site"})
        assert res_get.status_code == 200

        # Mutating DELETE request with cross-site is blocked
        res_del = await client.delete("/api/debug/webhooks", headers={"sec-fetch-site": "cross-site"})
        assert res_del.status_code == 403

        # Mutating DELETE request with same-origin is allowed
        res_del_ok = await client.delete("/api/debug/webhooks", headers={"sec-fetch-site": "same-origin"})
        assert res_del_ok.status_code == 200

