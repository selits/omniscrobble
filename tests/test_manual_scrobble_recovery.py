"""Manual scrobble error reporting and retry-queue integration tests."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        "retry_payloads": {"trakt": {"movies": [{"title": "Example Movie"}]}},
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


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
@pytest.mark.asyncio
async def test_manual_episode_retry_preserves_original_show_payload(tmp_path, monkeypatch, failure):
    import app.main as main
    from app.services.queue_manager import process_queue

    queue = QueueManager(tmp_path / "episode-retry.db")
    monkeypatch.setattr(main, "queue_mgr", queue)
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(main, "get_cached_trakt_profile", AsyncMock(return_value={"username": "admin"}))
    monkeypatch.setattr(main, "record_watch_stat", MagicMock())
    monkeypatch.setattr(main, "log_event", MagicMock(return_value="episode-event"))
    monkeypatch.setattr(main, "record_queue_delivery", MagicMock())
    monkeypatch.setattr(main.playback_mgr, "stop_playback", MagicMock())
    monkeypatch.setattr(main.notifier, "dispatch", AsyncMock())
    monkeypatch.setattr(main.notifier, "send_failure_alert", AsyncMock())
    monkeypatch.setattr(main.multi_tracker.anime_resolver, "resolve", AsyncMock(return_value=None))
    monkeypatch.setattr(main.trakt, "is_authenticated", lambda: True)
    sync = AsyncMock()
    sync.side_effect = [
        TimeoutError("network timeout") if failure == "timeout" else {"status": "error", "code": failure, "error": f"HTTP {failure}"},
        {"added": {"episodes": 1}},
    ]
    monkeypatch.setattr(main.trakt, "sync_history", sync)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("admin_token", "test-secret")
        response = await client.post("/api/scrobble/manual", json={
            "media_type": "episode", "title": "Example Show", "show_title": "Example Show",
            "year": 2024, "season": 1, "episode": 2, "ids": {"tmdb": 12345}, "trackers": ["trakt"],
        })
    assert response.status_code == 200
    assert "retry_payloads" not in response.json()["result"]
    original = sync.await_args_list[0].args[0]
    show = original["shows"][0]
    assert show["ids"] == {"tmdb": 12345}
    assert show["year"] == 2024
    assert show["seasons"][0]["episodes"][0]["number"] == 2
    item = queue.get_item(1)
    assert item["payload"] == original
    if failure != 429:
        assert queue.retry_failed_item(1, "episode-event", confirm_duplicate_history=True) == "queued"
    result = await process_queue(main.trakt, queue)
    assert result["succeeded"] == 1
    assert sync.await_args_list[1].args[0] == original
