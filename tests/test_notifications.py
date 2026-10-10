"""Focused tests migrated from the former test_scrobbler monolith."""

import pytest

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")


def test_notifier_title_formatting_and_trakt_url():
    # Episode with subtitle
    ep = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        ids={"imdb": "tt2301451"},
    )
    assert format_media_title(ep) == "Breaking Bad S05E14 - Ozymandias"
    assert get_trakt_url(ep) == "https://trakt.tv/search?q=Breaking+Bad"

    # Episode without subtitle or title same as show
    ep_same = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Breaking Bad",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        ids={"tmdb": 62085},
    )
    assert format_media_title(ep_same) == "Breaking Bad S05E14"
    assert get_trakt_url(ep_same) == "https://trakt.tv/search?q=Breaking+Bad"

    # Movie with year
    movie = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
        ids={"tvdb": 12345},
    )
    assert format_media_title(movie) == "Inception (2010)"
    assert get_trakt_url(movie) == "https://trakt.tv/search?q=Inception"

    # Fallback with title
    plain = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Unknown Film",
    )
    assert format_media_title(plain) == "Unknown Film"
    assert get_trakt_url(plain) == "https://trakt.tv/search?q=Unknown+Film"

    # Empty title fallback
    empty = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="",
    )
    assert get_trakt_url(empty) == "https://trakt.tv"

def test_notifier_build_payloads():
    media_scrobble = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Ozymandias",
        show_title="Breaking Bad",
        season=5,
        episode=14,
        progress=100.0,
        ids={"imdb": "tt2301451"},
    )

    notifier_inst = Notifier(Config)

    # 1. Discord Scrobble payload
    discord_scrobble = notifier_inst.build_discord_payload(media_scrobble, "mark_watched")
    assert "embeds" in discord_scrobble
    embed = discord_scrobble["embeds"][0]
    assert embed["color"] == 0xED1C24
    assert embed["title"] == "Breaking Bad S05E14 - Ozymandias"
    assert embed["url"] == "https://trakt.tv/search?q=Breaking+Bad"
    assert any(f["name"] == "Progress" and f["value"] == "100.0%" for f in embed["fields"])

    # 2. Discord Rating payload
    media_rate = ParsedMedia(
        event="media.rate",
        username="selits",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        rating=10,
        ids={"imdb": "tt15239678"},
    )
    discord_rate = notifier_inst.build_discord_payload(media_rate, "rate")
    embed_rate = discord_rate["embeds"][0]
    assert embed_rate["color"] == 0xF5A623
    assert embed_rate["title"] == "Dune: Part Two (2024)"
    assert any(f["name"] == "Rating" and "10/10" in f["value"] for f in embed_rate["fields"])

    # 3. Telegram Scrobble payload
    with patch.object(Config, "TELEGRAM_CHAT_ID", "123456"):
        tg_scrobble = notifier_inst.build_telegram_payload(media_scrobble, "mark_watched")
        assert tg_scrobble["chat_id"] == "123456"
        assert tg_scrobble["parse_mode"] == "HTML"
        assert "Breaking Bad S05E14 - Ozymandias" in tg_scrobble["text"]
        assert "Scrobbled to Trakt" in tg_scrobble["text"]

    # 4. Telegram Rating payload
    with patch.object(Config, "TELEGRAM_CHAT_ID", "123456"):
        tg_rate = notifier_inst.build_telegram_payload(media_rate, "rate")
        assert tg_rate["chat_id"] == "123456"
        assert "Dune: Part Two (2024)" in tg_rate["text"]
        assert "10/10" in tg_rate["text"]

@pytest.mark.asyncio(loop_scope="module")
async def test_notifier_send_discord_and_telegram():
    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
        progress=100.0,
    )

    notifier_inst = Notifier(Config)

    # 1. Discord unconfigured
    with patch.object(Config, "DISCORD_WEBHOOK_URL", ""):
        assert await notifier_inst.send_discord(media, "mark_watched") is False

    # 2. Discord configured & success (204 No Content)
    mock_discord_client = AsyncMock()
    mock_discord_client.post.return_value = httpx.Response(204)
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        success = await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client)
        assert success is True
        mock_discord_client.post.assert_called_once()

    # 3. Discord failure response (500)
    mock_discord_client.post.return_value = httpx.Response(500, text="Internal Server Error")
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        assert await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client) is False

    # 4. Discord network exception handled gracefully
    mock_discord_client.post.side_effect = httpx.ConnectTimeout("Discord timed out")
    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        assert await notifier_inst.send_discord(media, "mark_watched", client=mock_discord_client) is False

    # 5. Telegram unconfigured
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), patch.object(Config, "TELEGRAM_CHAT_ID", ""):
        assert await notifier_inst.send_telegram(media, "mark_watched") is False

    # 6. Telegram configured & success
    mock_tg_client = AsyncMock()
    mock_tg_client.post.return_value = httpx.Response(200, json={"ok": True})
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        success = await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client)
        assert success is True
        mock_tg_client.post.assert_called_once()

    # 7. Telegram error response (400)
    mock_tg_client.post.return_value = httpx.Response(400, text="Bad Request")
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        assert await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client) is False

    # 8. Telegram network exception handled gracefully
    mock_tg_client.post.side_effect = httpx.ConnectError("Network is down")
    with patch.object(Config, "TELEGRAM_BOT_TOKEN", "fake_bot_token"), patch.object(Config, "TELEGRAM_CHAT_ID", "12345"):
        assert await notifier_inst.send_telegram(media, "mark_watched", client=mock_tg_client) is False

@pytest.mark.asyncio(loop_scope="module")
async def test_notifier_dispatch_toggles():
    media_scrobble = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Inception",
        year=2010,
    )
    media_rate = ParsedMedia(
        event="media.rate",
        username="selits",
        media_type="movie",
        title="Inception",
        rating=9,
    )

    notifier_inst = Notifier(Config)

    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", "bot123"), \
         patch.object(Config, "TELEGRAM_CHAT_ID", "chat123"):

        # 1. When NOTIFY_ON_SCROBBLE is disabled
        with patch.object(Config, "NOTIFY_ON_SCROBBLE", False), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_scrobble, "mark_watched")
            mock_d.assert_not_called()
            mock_t.assert_not_called()

        # 2. When NOTIFY_ON_RATE is disabled
        with patch.object(Config, "NOTIFY_ON_RATE", False), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_rate, "rate")
            mock_d.assert_not_called()
            mock_t.assert_not_called()

        # 3. When both enabled
        with patch.object(Config, "NOTIFY_ON_SCROBBLE", True), \
             patch.object(Config, "NOTIFY_ON_RATE", True), \
             patch.object(notifier_inst, "send_discord", new_callable=AsyncMock) as mock_d, \
             patch.object(notifier_inst, "send_telegram", new_callable=AsyncMock) as mock_t:
            await notifier_inst.dispatch(media_scrobble, "mark_watched")
            mock_d.assert_called_once()
            mock_t.assert_called_once()

@pytest.mark.asyncio(loop_scope="module")
async def test_webhook_triggers_notification_dispatch():
    client = make_async_test_client(app)

    plex_sample = {
        "event": "media.scrobble",
        "user": True,
        "Account": {"id": 1, "title": "selits"},
        "Metadata": {
            "librarySectionType": "movie",
            "type": "movie",
            "title": "Interstellar",
            "year": 2014,
            "duration": 10000000,
            "viewOffset": 9900000,
            "Guid": [{"id": "imdb://tt0816692"}],
        },
    }

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(trakt, "sync_history", new_callable=AsyncMock) as mock_sync, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock) as mock_dispatch:

        mock_stop.return_value = {"action": "scrobble"}
        mock_sync.return_value = {"added": {"movies": 1}}

        res = await client.post("/webhook", data={"payload": json.dumps(plex_sample)})
        assert res.status_code == 200
        # Check that dispatch was called with parsed media and action
        assert mock_dispatch.called
        args = mock_dispatch.call_args[0]
        assert args[0].title == "Interstellar"
        assert args[1] == "mark_watched"

@pytest.mark.asyncio(loop_scope="module")
async def test_health_and_dashboard_notification_status():
    client = make_async_test_client(app)

    with patch.object(Config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), \
         patch.object(Config, "TELEGRAM_CHAT_ID", ""):

        # 1. Check /health
        res_health = await client.get("/health")
        assert res_health.status_code == 200
        data = res_health.json()
        assert "notifications" in data
        assert data["notifications"]["discord"] is True
        assert data["notifications"]["telegram"] is False

        # 2. Check dashboard
        res_dash = await client.get("/")
        assert res_dash.status_code == 200
        assert "Alerts: Discord" in res_dash.text

@pytest.mark.asyncio(loop_scope="module")
async def test_ntfy_and_pushover_notifications():
    """Verify Ntfy and Pushover notification builders and dispatch."""
    media = ParsedMedia(
        event="library.new",
        username="selits",
        media_type="movie",
        title="Dune: Part Two",
        year=2024,
        video_resolution="4k",
        audio_codec="truehd",
        ids={"imdb": "tt15239678"},
    )

    mock_http = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_http.post = AsyncMock(return_value=mock_resp)

    # 1. Ntfy delivery
    with patch.object(Config, "NTFY_URL", "https://ntfy.sh/my_test_topic"), \
         patch.object(Config, "NTFY_AUTH_TOKEN", "tk_test"):
        res_ntfy = await notifier.send_ntfy(media, "collection", client=mock_http)
        assert res_ntfy is True
        assert mock_http.post.called
        call_headers = mock_http.post.call_args[1]["headers"]
        assert "Trakt: Dune: Part Two" in call_headers["Title"]
        assert call_headers["Authorization"] == "Bearer tk_test"

    # 2. Pushover delivery
    mock_http.post.reset_mock()
    with patch.object(Config, "PUSHOVER_USER_KEY", "u_key"), \
         patch.object(Config, "PUSHOVER_API_TOKEN", "t_tok"):
        res_push = await notifier.send_pushover(media, "collection", client=mock_http)
        assert res_push is True
        assert mock_http.post.called
        post_data = mock_http.post.call_args[1]["data"]
        assert post_data["user"] == "u_key"
        assert "Dune: Part Two" in post_data["title"]

def test_notifier_zero_and_custom_progress():
    notifier_inst = Notifier(Config)
    media_zero = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Critical Failure",
        show="The Ark",
        season=3,
        episode=9,
        progress=0.0,
    )
    discord_payload = notifier_inst.build_discord_payload(media_zero, "mark_watched")
    embed = discord_payload["embeds"][0]
    assert any(f["name"] == "Progress" and f["value"] == "0.0%" for f in embed["fields"])
    assert "0.0% watched" in embed["description"]

    media_partial = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="episode",
        title="Critical Failure",
        show="The Ark",
        season=3,
        episode=9,
        progress=85.5,
    )
    discord_payload_partial = notifier_inst.build_discord_payload(media_partial, "mark_watched")
    embed_partial = discord_payload_partial["embeds"][0]
    assert any(f["name"] == "Progress" and f["value"] == "85.5%" for f in embed_partial["fields"])
    assert "85.5% watched" in embed_partial["description"]

def test_notifier_arr_add_action():
    """Verify Notifier formats and handles arr_add action."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier_inst = Notifier(Config)

    media = ParsedMedia(
        raw_payload={},
        event="arr.add",
        media_type="movie",
        title="Gladiator II",
        year=2024,
        username="Radarr",
    )

    payload = notifier_inst.build_discord_payload(media, "arr_add")
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "Gladiator II (2024)" in embed["title"]
    assert embed["color"] == 0x2ECC71
    assert "Added to **Radarr** from Trakt Watchlist" in embed["description"]

    # Also test show
    show_media = ParsedMedia(
        raw_payload={},
        event="arr.add",
        media_type="show",
        title="Severance",
        year=2022,
        username="Sonarr",
    )
    show_payload = notifier_inst.build_discord_payload(show_media, "arr_add")
    assert "Added to **Sonarr** from Trakt Watchlist" in show_payload["embeds"][0]["description"]

@pytest.mark.asyncio(loop_scope="module")
async def test_notifier_failure_alert_deduplication():
    """Verify Notifier sends failure alert and deduplicates subsequent identical alerts within TTL."""
    from app.services.notifier import Notifier
    from app.plex_parser import ParsedMedia

    notifier = Notifier()
    notifier.config.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/test"
    notifier._failure_cache.clear()

    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        title="Severance S02E01",
        show_title="Severance",
        media_type="episode",
        season=2,
        episode=1
    )

    mock_resp = MagicMock(status_code=204)
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    # First alert -> sent
    sent1 = await notifier.send_failure_alert(media, "Simkl", "401 Unauthorized", "selits", client=mock_http)
    assert sent1 is True
    assert mock_http.post.call_count == 1

    # Second identical alert immediately -> deduplicated / skipped
    sent2 = await notifier.send_failure_alert(media, "Simkl", "401 Unauthorized", "selits", client=mock_http)
    assert sent2 is False
    assert mock_http.post.call_count == 1

    # Different failure -> sent
    sent3 = await notifier.send_failure_alert(media, "AniList", "500 Server Error", "selits", client=mock_http)
    assert sent3 is True
    assert mock_http.post.call_count == 2

@pytest.mark.asyncio(loop_scope="module")
async def test_settings_api_notifications():
    """Verify GET and POST /api/settings handles notifications configuration and masking."""
    from app.services.settings_manager import settings_mgr
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        headers = {"x-webhook-secret": "supersecret"}

        # 1. GET settings includes notifications
        res = await client.get("/api/settings", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "notifications" in data
        notifs = data["notifications"]
        assert "discord_webhook_url" in notifs
        assert "ntfy_auth_token" in notifs
        assert "notify_on_scrobble" in notifs
        assert "notify_on_rate" in notifs
        assert "notify_on_collection" in notifs
        assert "notify_on_failure" in notifs

        # 2. POST updates notifications
        new_payload = {
            "notifications": {
                "discord_webhook_url": "https://discord.com/api/webhooks/12345/secret_token",
                "telegram_bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
                "telegram_chat_id": "-100987654321",
                "ntfy_url": "https://ntfy.sh/my-secret-test-topic",
                "ntfy_auth_token": "secret_ntfy_tk_9999",
                "pushover_user_key": "user_key_9999",
                "pushover_api_token": "app_token_8888",
                "notify_on_scrobble": False,
                "notify_on_rate": True,
                "notify_on_collection": False,
                "notify_on_failure": True,
            }
        }
        res_post = await client.post("/api/settings", headers=headers, json=new_payload)
        assert res_post.status_code == 200
        saved = res_post.json()["settings"]["notifications"]

        # Sensitive values should be masked in API responses
        assert "••••" in saved["discord_webhook_url"]
        assert "••••" in saved["telegram_bot_token"]
        assert "••••" in saved["ntfy_auth_token"]
        assert "••••" in saved["pushover_user_key"]
        assert "••••" in saved["pushover_api_token"]
        assert saved["telegram_chat_id"] == "-100987654321"
        assert saved["ntfy_url"] == "https://ntfy.sh/my-secret-test-topic"
        assert saved["notify_on_scrobble"] is False
        assert saved["notify_on_collection"] is False

        # Raw values should be retained in settings_mgr
        raw = settings_mgr.get_notifications(mask=False)
        assert raw["discord_webhook_url"] == "https://discord.com/api/webhooks/12345/secret_token"
        assert raw["telegram_bot_token"] == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        assert raw["ntfy_auth_token"] == "secret_ntfy_tk_9999"

        # Submitting masked value should preserve existing secret
        res_masked = await client.post(
            "/api/settings",
            headers=headers,
            json={"notifications": {"discord_webhook_url": saved["discord_webhook_url"]}}
        )
        assert res_masked.status_code == 200
        assert settings_mgr.get_notifications(mask=False)["discord_webhook_url"] == "https://discord.com/api/webhooks/12345/secret_token"

        # Cleanup settings for subsequent tests
        settings_mgr.update_notifications({
            "discord_webhook_url": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "ntfy_url": "",
            "ntfy_auth_token": "",
            "pushover_user_key": "",
            "pushover_api_token": "",
        })

@pytest.mark.asyncio(loop_scope="module")
async def test_notifications_test_endpoint_auth():
    """Verify authentication and validation on POST /api/notifications/test."""
    client = make_async_test_client(app)

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"):
        # 1. Unauthorized request
        res_unauth = await client.post("/api/notifications/test", json={"channel": "discord"})
        assert res_unauth.status_code == 401

        # 2. Authorized but invalid channel
        headers = {"x-webhook-secret": "supersecret"}
        res_invalid = await client.post(
            "/api/notifications/test",
            headers=headers,
            json={"channel": "unsupported_channel"}
        )
        assert res_invalid.status_code == 400
        assert "Unknown notification channel" in res_invalid.json()["message"]

@pytest.mark.asyncio(loop_scope="module")
async def test_notifications_test_endpoint_channels():
    """Verify test notification dispatch across all supported channels."""
    from app.services.settings_manager import settings_mgr
    client = make_async_test_client(app)

    settings_mgr.update_notifications({
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "ntfy_url": "",
        "pushover_user_key": "",
        "pushover_api_token": "",
    })

    with patch.object(Config, "WEBHOOK_SECRET", "supersecret"), \
         patch.object(Config, "DISCORD_WEBHOOK_URL", ""), \
         patch.object(Config, "TELEGRAM_BOT_TOKEN", ""), \
         patch.object(Config, "TELEGRAM_CHAT_ID", ""), \
         patch.object(Config, "NTFY_URL", ""), \
         patch.object(Config, "PUSHOVER_USER_KEY", ""), \
         patch.object(Config, "PUSHOVER_API_TOKEN", ""):
        headers = {"x-webhook-secret": "supersecret"}

        # 1. Discord unconfigured
        res_disc_none = await client.post("/api/notifications/test", headers=headers, json={"channel": "discord"})
        assert res_disc_none.status_code == 400
        assert res_disc_none.json()["status"] == "error"
        assert "not configured" in res_disc_none.json()["message"]

        # Discord success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")
            res_disc = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "https://discord.com/api/webhooks/test/123"}
            )
            assert res_disc.status_code == 200
            assert res_disc.json()["status"] == "success"

        # Discord failure
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
            res_disc_fail = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "https://discord.com/api/webhooks/test/123"}
            )
            assert res_disc_fail.status_code == 400
            assert res_disc_fail.json()["status"] == "error"
            assert "HTTP 400" in res_disc_fail.json()["message"]

        # 2. Telegram unconfigured
        res_tg_none = await client.post("/api/notifications/test", headers=headers, json={"channel": "telegram"})
        assert res_tg_none.status_code == 400
        assert res_tg_none.json()["status"] == "error"

        # Telegram success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="{}")
            res_tg = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "telegram", "telegram_bot_token": "bot123", "telegram_chat_id": "chat123"}
            )
            assert res_tg.status_code == 200
            assert res_tg.json()["status"] == "success"

        # 3. Ntfy success (without auth and with auth token)
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            res_ntfy = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "ntfy", "ntfy_url": "https://ntfy.sh/my-topic"}
            )
            assert res_ntfy.status_code == 200
            assert res_ntfy.json()["status"] == "success"
            assert "Authorization" not in mock_post.call_args[1]["headers"]

        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="ok")
            res_ntfy_auth = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "ntfy", "ntfy_url": "https://ntfy.sh/my-topic", "ntfy_auth_token": "secret_token_123"}
            )
            assert res_ntfy_auth.status_code == 200
            assert res_ntfy_auth.json()["status"] == "success"
            assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret_token_123"

        # 4. Pushover success
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text='{"status": 1}')
            res_push = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "pushover", "pushover_user_key": "ukey", "pushover_api_token": "atoken"}
            )
            assert res_push.status_code == 200
            assert res_push.json()["status"] == "success"

        # 5. Masked credentials fallback to saved settings
        settings_mgr.update_notifications({
            "discord_webhook_url": "https://discord.com/api/webhooks/saved/real_token",
            "telegram_bot_token": "bot_saved_real_token",
            "telegram_chat_id": "chat_12345",
        })
        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")
            res_masked = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "discord", "discord_webhook_url": "••••••••dcrd"}
            )
            assert res_masked.status_code == 200
            assert res_masked.json()["status"] == "success"
            assert mock_post.call_args[0][0] == "https://discord.com/api/webhooks/saved/real_token"

        with patch.object(httpx.AsyncClient, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, text="{}")
            res_masked_tg = await client.post(
                "/api/notifications/test",
                headers=headers,
                json={"channel": "telegram", "telegram_bot_token": "••••••••tele", "telegram_chat_id": "chat_12345"}
            )
            assert res_masked_tg.status_code == 200
            assert res_masked_tg.json()["status"] == "success"
            assert "bot_saved_real_token" in mock_post.call_args[0][0]

        # Reset cleanup
        settings_mgr.update_notifications({
            "discord_webhook_url": "",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        })

def test_notifier_dynamic_settings_resolution():
    """Verify Notifier respects SettingsManager overrides with Config fallback."""
    from app.services.settings_manager import settings_mgr
    test_notif = Notifier(Config)

    # When no custom override exists, falls back to Config
    with patch.object(Config, "NOTIFY_ON_RATE", True):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is True
    with patch.object(Config, "NOTIFY_ON_RATE", False):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is False

    # When custom override is set in settings_mgr, override takes precedence
    settings_mgr.update_notifications({"notify_on_rate": False})
    with patch.object(Config, "NOTIFY_ON_RATE", True):
        assert test_notif._is_event_enabled("notify_on_rate", "NOTIFY_ON_RATE") is False

    # Check _get_ntfy_auth_token resolution
    with patch.object(Config, "NTFY_AUTH_TOKEN", "config_token"):
        settings_mgr.update_notifications({"ntfy_auth_token": ""})
        assert test_notif._get_ntfy_auth_token() == "config_token"

        settings_mgr.update_notifications({"ntfy_auth_token": "custom_token"})
        assert test_notif._get_ntfy_auth_token() == "custom_token"

    # Cleanup
    settings_mgr.update_notifications({"notify_on_rate": True, "ntfy_auth_token": ""})

@pytest.mark.asyncio(loop_scope="module")
async def test_notification_channels_gotify_and_matrix():
    """Verify Discord action buttons, Gotify dispatch, and Matrix dispatch."""
    from app.services.notifier import notifier
    from app.plex_parser import ParsedMedia

    media = ParsedMedia(
        event="media.scrobble",
        username="selits",
        media_type="movie",
        title="Spirited Away",
        year=2001,
        ids={"imdb": "tt0245429", "tmdb": 129},
        playback_progress=100.0,
    )

    # 1. Discord payload includes action buttons
    discord_payload = notifier.build_discord_payload(media, action="scrobble")
    assert "components" in discord_payload
    components = discord_payload["components"]
    assert len(components) == 1
    assert components[0]["type"] == 1  # Action Row
    buttons = components[0]["components"]
    assert len(buttons) == 3
    labels = [b["label"] for b in buttons]
    assert any("Trakt" in l for l in labels)
    assert any("Letterboxd" in l for l in labels)
    assert any("IMDb" in l for l in labels)

    # 2. Gotify payload and send_gotify
    gotify_payload = notifier.build_gotify_payload(media, action="scrobble")
    assert "Spirited Away (2001)" in gotify_payload["title"]
    assert "Scrobbled" in gotify_payload["message"]
    assert gotify_payload["priority"] == 5

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(200, json={"id": 1})
        ok = await notifier.send_gotify(gotify_payload, "http://mock-gotify:8080", "app_token", priority=6)
        assert ok is True
        mock_post.assert_awaited_with(
            "http://mock-gotify:8080/message",
            json={**gotify_payload, "priority": 6},
            headers={"X-Gotify-Key": "app_token"}
        )

    # 3. Matrix payload and send_matrix
    matrix_payload = notifier.build_matrix_payload(media, action="scrobble")
    assert matrix_payload["msgtype"] == "m.text"
    assert matrix_payload["format"] == "org.matrix.custom.html"
    assert "Spirited Away" in matrix_payload["formatted_body"]

    with patch("httpx.AsyncClient.put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = httpx.Response(200, json={"event_id": "$mock_event_123"})
        ok = await notifier.send_matrix(matrix_payload, "https://matrix.org", "access_tok", "!room:matrix.org")
        assert ok is True
        assert mock_put.await_count == 1
        put_url = mock_put.call_args[0][0]
        assert "https://matrix.org/_matrix/client/v3/rooms/" in put_url
        assert "/send/m.room.message/" in put_url

    # 4. Status includes Gotify and Matrix
    status = notifier.get_status()
    assert "gotify" in status
    assert "matrix" in status
    assert "weekly_digest_enabled" in status

@pytest.mark.asyncio(loop_scope="module")
async def test_api_notification_test_endpoint_gotify_and_matrix():
    """Verify POST /api/notifications/test with Gotify and Matrix channels."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "super_secret"):
        client.cookies.set("admin_token", "super_secret")

        # Demo Gotify
        res_demo_gotify = await client.post("/api/notifications/test?demo=true", json={"channel": "gotify"})
        assert res_demo_gotify.status_code == 200
        assert res_demo_gotify.json()["success"] is True

        # Demo Matrix
        res_demo_matrix = await client.post("/api/notifications/test?demo=true", json={"channel": "matrix"})
        assert res_demo_matrix.status_code == 200
        assert res_demo_matrix.json()["success"] is True

        # Live Gotify mock
        with patch("app.services.notifier.notifier.send_test_notification", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = (True, "Test alert sent to Gotify")
            res_live = await client.post(
                "/api/notifications/test",
                json={"channel": "gotify", "gotify_url": "http://127.0.0.1:8080", "gotify_token": "tok"}
            )
            assert res_live.status_code == 200
            assert res_live.json()["success"] is True
            assert res_live.json()["message"] == "Test alert sent to Gotify"

@pytest.mark.asyncio(loop_scope="module")
async def test_weekly_activity_digest_full(tmp_path):
    """Verify DigestManager statistics, formatting, and dispatch."""
    import datetime
    from app.services.digest_manager import DigestManager

    # 1. Demo statistics
    mock_events_file = tmp_path / "events.json"
    digest = DigestManager(events_file=mock_events_file)
    demo_stats = digest.generate_digest_stats(days=7, demo=True)
    assert demo_stats["window_days"] == 7
    assert demo_stats["total_scrobbles"] == 18
    assert demo_stats["watch_time_formatted"] == "16h 20m"
    assert "Severance" in demo_stats["cowatch_shows"]

    # 2. Live statistics from mock events.json
    now = datetime.datetime.now()
    two_days_ago = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    ten_days_ago = (now - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")

    sample_events = [
        # In-window movie
        {
            "timestamp": two_days_ago,
            "action": "scrobble",
            "type": "movie",
            "title": "Dune: Part Two (2024)",
            "server": "Plex",
            "device": "Apple TV 4K",
            "duration_minutes": 166,
            "cowatch_status": "Co-watched with @alice",
        },
        # In-window episode
        {
            "timestamp": two_days_ago,
            "action": "scrobble",
            "type": "episode",
            "title": "Severance S02E01",
            "show_title": "Severance",
            "server": "Jellyfin",
            "device": "Shield TV",
            "duration_minutes": 55,
            "cowatch_status": "Co-watched with @alice",
        },
        # In-window rating
        {
            "timestamp": two_days_ago,
            "action": "rate",
            "type": "movie",
            "title": "Dune: Part Two (2024)",
            "server": "Plex",
        },
        # Out-of-window event (should not be counted in 7-day stats)
        {
            "timestamp": ten_days_ago,
            "action": "scrobble",
            "type": "movie",
            "title": "Old Movie",
            "duration_minutes": 120,
        },
    ]
    with open(mock_events_file, "w", encoding="utf-8") as f:
        json.dump(sample_events, f)

    live_stats = digest.generate_digest_stats(days=7, demo=False)
    assert live_stats["total_scrobbles"] == 2
    assert live_stats["movies_watched"] == 1
    assert live_stats["episodes_watched"] == 1
    assert live_stats["total_ratings"] == 1
    assert live_stats["watch_time_minutes"] == 160
    assert live_stats["watch_time_formatted"] == "2h 40m"
    assert live_stats["cowatch_sessions"] == 2
    assert live_stats["top_servers"] == {"Plex": 2, "Jellyfin": 1}
    assert live_stats["top_devices"] == {"Apple TV 4K": 1, "Shield TV": 1}

    # 3. Formatting
    discord_payload = digest.format_discord_digest(live_stats)
    assert "embeds" in discord_payload
    discord_embed = discord_payload["embeds"][0]
    assert "Weekly Activity Digest" in discord_embed["title"]
    assert any("Watch Time" in f["name"] for f in discord_embed["fields"])

    html_text = digest.format_html_digest(live_stats)
    assert "Omniscrobble Weekly Activity Digest" in html_text
    assert "2h 40m" in html_text

    md_text = digest.format_markdown_digest(live_stats)
    assert "Omniscrobble Weekly Digest" in md_text
    assert "Watch Time:" in md_text

    # 4. send_digest demo
    res_demo = await digest.send_digest(demo=True)
    assert res_demo["status"] == "success"
    assert res_demo["success"] is True
    assert "Weekly activity digest dispatched successfully" in res_demo["message"]

@pytest.mark.asyncio(loop_scope="module")
async def test_api_weekly_digest_endpoint():
    """Verify POST /api/notifications/digest endpoint security and demo handling."""
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "super_secret"):
        # 1. Unauthenticated -> 401
        res_unauth = await client.post("/api/notifications/digest")
        assert res_unauth.status_code == 401

        # 2. Demo mode -> 200
        res_demo = await client.post("/api/notifications/digest?demo=true")
        assert res_demo.status_code == 200
        assert res_demo.json()["status"] == "success"

        # 3. Admin authorized live trigger
        client.cookies.set("admin_token", "super_secret")
        with patch("app.services.digest_manager.digest_mgr.send_digest", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": "success", "success": True, "message": "Digest sent"}
            res_live = await client.post("/api/notifications/digest")
            assert res_live.status_code == 200
            assert res_live.json()["success"] is True
            assert res_live.json()["message"] == "Digest sent"

