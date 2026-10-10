"""Manual scrobble error reporting and retry-queue integration tests."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Config
from app.main import app
from app.services.queue_manager import QueueManager


@pytest.mark.parametrize(
    ("status_code", "expected_response", "expected_queue_state"),
    [(503, "failed", "failed"), (429, "queued", "pending")],
)
@pytest.mark.asyncio
async def test_manual_scrobble_retains_tracker_failures_for_recovery(
    tmp_path, monkeypatch, status_code, expected_response, expected_queue_state
):
    import app.main as main

    queue = QueueManager(tmp_path / f"manual-{status_code}.db")
    monkeypatch.setattr(main, "queue_mgr", queue)
    monkeypatch.setattr(main.activity_delivery_mgr, "queue", queue)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    client.cookies.set("admin_token", "test-secret")
    result = {
        "status": "error",
        "synced_trackers": [],
        "errors": {"trakt": f"HTTP {status_code}", "simkl": f"HTTP {status_code}"},
        "retryable_trackers": ["trakt", "simkl"],
        "tracker_error_codes": {"trakt": status_code, "simkl": status_code},
    }

    try:
        with patch.object(Config, "WEBHOOK_SECRET", "test-secret"), \
             patch("app.main.get_cached_trakt_profile", new_callable=AsyncMock, return_value={"username": "admin"}), \
             patch("app.main.multi_tracker.dispatch_manual_scrobble", new_callable=AsyncMock, return_value=result), \
             patch("app.main.notifier.dispatch", new_callable=AsyncMock):
            response = await client.post(
                "/api/scrobble/manual",
                json={"media_type": "movie", "title": "Example Movie", "trackers": ["trakt", "simkl"]},
            )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == expected_response
        assert body["event_id"]
        with queue._get_connection() as connection:
            rows = connection.execute(
                "SELECT id, event_type, status, event_id FROM queued_events ORDER BY id"
            ).fetchall()
        assert {row["event_type"]: row["status"] for row in rows} == {
            "sync_history": expected_queue_state,
            "simkl_sync_history": expected_queue_state,
        }
        assert all(row["event_id"] == body["event_id"] for row in rows)
        event = next(entry for entry in main.recent_events if entry["event_id"] == body["event_id"])
        assert event["tracker_delivery"]["trakt"] == expected_queue_state.replace("pending", "queued")
        assert event["tracker_delivery"]["simkl"] == expected_queue_state.replace("pending", "queued")
        for tracker in ("Trakt", "Simkl"):
            detail = next(
                item for name, item in event["tracker_delivery_details"].items()
                if name.startswith(tracker) and "queue" in name
            )
            assert detail["queue_item_id"]
            if status_code == 503:
                assert detail["category"] == "transient"
    finally:
        await client.aclose()
        for entry in list(main.recent_events):
            if entry.get("title") == "Example Movie (N/A)" and entry.get("action") == "manual_scrobble":
                main.recent_events.remove(entry)
        main.save_recent_events()
