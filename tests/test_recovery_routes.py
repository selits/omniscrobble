"""Admin recovery endpoint and configuration diagnostic tests."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Config
from app.main import app
from app.services.notifier import notifier


def make_async_test_client(app, **kwargs):
    raise_server_exceptions = kwargs.pop("raise_server_exceptions", True)
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=raise_server_exceptions, client=("testclient", 50000)
    )
    return httpx.AsyncClient(transport=transport, base_url="http://testserver", **kwargs)


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_health_is_admin_only_read_only_and_secret_free():
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        unauthenticated = await client.get("/api/health/recovery")
        assert unauthenticated.status_code == 401

        authenticated = await client.get("/api/health/recovery?token=admin_secret")
        assert authenticated.status_code == 200
        data = authenticated.json()
        webhook_check = next(check for check in data["checks"] if check["id"] == "webhook_auth")
        assert webhook_check["next_action"] == "webhook"

    assert data["read_only"] is True
    assert data["checks"]
    assert all({"id", "label", "status", "summary", "next_action", "last_checked"} <= check.keys() for check in data["checks"])
    serialized = json.dumps(data)
    assert "admin_secret" not in serialized
    assert "access_token" not in serialized
    capability_check = next(check for check in data["checks"] if check["id"] == "tracker_capabilities")
    assert "tracker_capabilities" in capability_check
    assert "OAuth permission scopes are not introspected" in capability_check["summary"]
    config_check = next(check for check in data["checks"] if check["id"] == "configuration")
    assert "effective_settings" in config_check
    assert all("source" in setting and "value" in setting for setting in config_check["effective_settings"])
    assert "access_token" not in json.dumps(config_check)
    compatibility_check = next(check for check in data["checks"] if check["id"] == "compatibility")
    assert compatibility_check["application"]["version"]
    assert compatibility_check["runtime"]["minimum_supported"] == "3.10"
    assert {row["id"] for row in compatibility_check["compatibility_items"]} >= {
        "settings", "watch_lists", "automation_rules", "dashboard_accounts", "offline_queue"
    }
    assert compatibility_check["ruleset"]["last_reviewed"]

@pytest.mark.asyncio(loop_scope="function")
async def test_recovery_webhook_action_explains_missing_secret():
    client = make_async_test_client(app)
    with patch("app.main.is_admin_request", return_value=True), patch.object(Config, "WEBHOOK_SECRET", ""):
        response = await client.get("/api/health/recovery")
    assert response.status_code == 200
    webhook_check = next(check for check in response.json()["checks"] if check["id"] == "webhook_auth")
    assert webhook_check["next_action"] == "webhook_setup"

def test_configuration_diagnostics_find_issues_without_returning_secrets(tmp_path):
    from app.main import _build_configuration_diagnostics
    from app.services.settings_manager import settings_mgr
    settings = {
        "rules": {"scrobble_threshold": 120, "movie_scrobble_threshold": 90, "min_duration_seconds": -5},
        "arr": {"auto_add_from_watchlist": True, "sonarr_url": "http://private-host", "sonarr_api_key": ""},
        "notifications": {"telegram_bot_token": "private-telegram-secret", "telegram_chat_id": "", "weekly_digest_enabled": True, "weekly_digest_day": "funday", "weekly_digest_hour": 27},
        "trackers": {"trakt": False},
        "credentials": {"trakt": {"client_id": "private-id", "client_secret": "private-secret"}},
    }
    with patch.object(settings_mgr, "settings_file", tmp_path / "no-settings.json"), \
         patch.object(settings_mgr, "get_all_settings", return_value=settings), \
         patch.object(settings_mgr, "is_server_enabled", return_value=False), \
         patch.object(Config, "SCROBBLE_MODE", "invalid-mode"), \
         patch.object(notifier, "get_status", return_value={"telegram": False, "matrix": False}):
        result = _build_configuration_diagnostics([])
    codes = {finding["code"] for finding in result["findings"]}
    assert {"invalid_scrobble_mode", "threshold_out_of_range", "negative_min_duration", "invalid_digest_day", "invalid_digest_hour", "incomplete_notification", "incomplete_arr_connection", "arr_automation_unconfigured", "configured_tracker_disabled", "digest_without_channel"} <= codes
    serialized = json.dumps(result)
    assert "private-secret" not in serialized
    assert "private-telegram-secret" not in serialized
    assert "private-host" not in serialized

@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_connection_test_is_admin_gated_and_redacts_results():
    client = make_async_test_client(app)
    with patch.object(Config, "WEBHOOK_SECRET", "admin_secret"):
        unauthenticated = await client.post("/api/health/recovery/test", json={"integration": "plex"})
        assert unauthenticated.status_code == 401

        with patch("app.main.test_sync_connection", new_callable=AsyncMock, return_value={
            "status": "connected", "server_name": "private-host", "token": "private-token"
        }) as connection_test:
            response = await client.post("/api/health/recovery/test?token=admin_secret", json={"integration": "plex"})
            connection_test.assert_awaited_once()
        assert response.status_code == 200
        assert response.json()["status"] == "connected"
        assert "private-host" not in response.text
        assert "private-token" not in response.text

        with patch("app.main.test_arr_connection", new_callable=AsyncMock, side_effect=RuntimeError("private failure")):
            failed = await client.post("/api/health/recovery/test?token=admin_secret", json={"integration": "sonarr"})
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert "private failure" not in failed.text

        with patch("app.main.notifier.get_status", return_value={"discord": True}), patch(
            "app.main.test_notification_endpoint",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "delivered to private destination"},
        ) as notification_test:
            notification = await client.post(
                "/api/health/recovery/test?token=admin_secret",
                json={"integration": "notification:discord"},
            )
            notification_test.assert_awaited_once()
        assert notification.status_code == 200
        assert notification.json()["status"] == "connected"
        assert "private destination" not in notification.text

        with patch("app.main.get_multi_trackers_status", new_callable=AsyncMock, return_value={
            "trackers": {"trakt": {"enabled": True, "authenticated": True, "status": "healthy"}}
        }) as tracker_test:
            tracker_response = await client.post(
                "/api/health/recovery/test?token=admin_secret",
                json={"integration": "trackers"},
            )
            tracker_test.assert_awaited_once()
        assert tracker_response.status_code == 200
        assert tracker_response.json()["status"] == "connected"

        invalid = await client.post("/api/health/recovery/test?token=admin_secret", json={"integration": "unknown"})
        assert invalid.status_code == 400
