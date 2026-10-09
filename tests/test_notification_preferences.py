from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Config
from app.plex_parser import ParsedMedia
from app.services.notifier import Notifier
from app.services.settings_manager import SettingsManager


def test_notification_routes_are_validated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PLEX_ALLOWED_USERS", ["viewer"])
    settings_file = tmp_path / "settings.json"
    manager = SettingsManager(Config, settings_file=settings_file, data_dir=tmp_path)
    manager.update_notifications({"notification_routes": {
        "scrobble": {"destinations": ["ntfy", "unknown", "ntfy"], "severity": "high", "profiles": ["viewer", "outsider"]},
        "rate": {"destinations": ["matrix"], "severity": "invalid"},
        "unknown-event": {"destinations": ["discord"], "severity": "critical"},
    }})

    routes = SettingsManager(Config, settings_file=settings_file, data_dir=tmp_path).get_notifications()["notification_routes"]
    assert routes["scrobble"] == {"destinations": ["ntfy"], "severity": "high", "profiles": ["viewer"]}
    assert routes["rate"]["destinations"] == ["discord", "gotify", "matrix", "ntfy", "pushover", "telegram"]
    assert "unknown-event" not in routes


@pytest.mark.asyncio
async def test_notifier_skips_profiles_not_subscribed_to_event(monkeypatch):
    import app.services.notifier as notifier_module

    monkeypatch.setattr(notifier_module, "settings_mgr", SimpleNamespace(get_custom_notifications=lambda: {
        "notify_on_scrobble": True,
        "notification_routes": {"scrobble": {"destinations": ["discord"], "severity": "normal", "profiles": ["household"]}},
    }))
    notifier = Notifier(SimpleNamespace(NOTIFY_ON_SCROBBLE=True, ARR_NOTIFY_ON_ADD=True))
    notifier.send_discord = AsyncMock()
    media = ParsedMedia(event="media.scrobble", username="viewer", media_type="movie", title="Arrival")

    await notifier.dispatch(media, "mark_watched")

    notifier.send_discord.assert_not_awaited()


@pytest.mark.asyncio
async def test_operational_route_preview_uses_saved_channel_and_severity(monkeypatch):
    import app.services.notifier as notifier_module

    monkeypatch.setattr(notifier_module, "settings_mgr", SimpleNamespace(get_custom_notifications=lambda: {
        "notification_routes": {"queue_recovery": {"destinations": ["ntfy"], "severity": "critical"}},
    }))
    notifier = Notifier(SimpleNamespace())
    monkeypatch.setattr(notifier, "_get_ntfy_url", lambda: "https://ntfy.example/topic")
    monkeypatch.setattr(notifier, "_get_ntfy_auth_token", lambda: "")
    http = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200)))

    delivered = await notifier.send_operational_notification(
        "queue_recovery", "Preview", "Sample notification", client=http
    )

    assert delivered is True
    assert http.post.await_args.kwargs["headers"]["Priority"] == "5"


@pytest.mark.asyncio
async def test_notifier_routes_scrobble_to_selected_channel_with_severity(monkeypatch):
    import app.services.notifier as notifier_module

    fake_settings = SimpleNamespace(get_custom_notifications=lambda: {
        "notify_on_scrobble": True,
        "notification_routes": {"scrobble": {"destinations": ["ntfy"], "severity": "critical"}},
    })
    monkeypatch.setattr(notifier_module, "settings_mgr", fake_settings)
    notifier = Notifier(SimpleNamespace(NOTIFY_ON_SCROBBLE=True, ARR_NOTIFY_ON_ADD=True))
    monkeypatch.setattr(notifier, "_get_discord_url", lambda: "")
    monkeypatch.setattr(notifier, "_get_telegram_token", lambda: "")
    monkeypatch.setattr(notifier, "_get_telegram_chat_id", lambda: "")
    monkeypatch.setattr(notifier, "_get_ntfy_url", lambda: "https://ntfy.example/topic")
    monkeypatch.setattr(notifier, "_get_pushover_user_key", lambda: "")
    monkeypatch.setattr(notifier, "_get_pushover_api_token", lambda: "")
    monkeypatch.setattr(notifier, "_get_gotify_url", lambda: "")
    monkeypatch.setattr(notifier, "_get_gotify_token", lambda: "")
    monkeypatch.setattr(notifier, "_get_matrix_homeserver_url", lambda: "")
    monkeypatch.setattr(notifier, "_get_matrix_access_token", lambda: "")
    monkeypatch.setattr(notifier, "_get_matrix_room_id", lambda: "")
    notifier.send_ntfy = AsyncMock()
    notifier.send_discord = AsyncMock()
    notifier.send_telegram = AsyncMock()

    media = ParsedMedia(event="media.scrobble", username="viewer", media_type="movie", title="Arrival")
    await notifier.dispatch(media, "mark_watched")

    notifier.send_ntfy.assert_awaited_once()
    assert notifier.send_ntfy.await_args.kwargs["severity"] == "critical"
    notifier.send_discord.assert_not_awaited()
    notifier.send_telegram.assert_not_awaited()
