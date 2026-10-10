"""Queue leasing and retry-safety regression tests."""

import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients.trakt_client import TraktClient
from app.services.queue_manager import QueueManager, process_queue
from app.services.activity_delivery import ActivityDeliveryManager


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["sync_history", "scrobble_stop", "simkl_sync_history"])
async def test_expired_history_claim_updates_activity_for_manual_recovery(tmp_path, event_type):
    queue = QueueManager(tmp_path / "expired-activity.db")
    event = {"event_id": "expired-event"}
    log = MagicMock()
    log.sanitize_line.side_effect = lambda text: text
    delivery = ActivityDeliveryManager(events=[event], save_events=MagicMock(), queue=queue,
                                      log_manager=log, notifier=MagicMock())
    item_id = queue.enqueue(event_type, {}, event_id="expired-event")
    delivery.record_queue_delivery("expired-event", event_type, item_id, "queued", "HTTP 429", 1)
    queue.claim_pending(limit=1)
    with queue._get_connection() as conn:
        conn.execute("UPDATE queued_events SET claimed_at = ? WHERE id = ?", (int(time.time()) - 301, item_id))
    callback = MagicMock(side_effect=delivery.record_queue_delivery)
    result = await process_queue(MagicMock(), queue, on_result=callback)
    assert result["processed"] == 0
    callback.assert_called_once()
    assert queue.get_item(item_id)["status"] == "failed"
    assert event["delivery_status"] == "failed"
    detail = next(d for d in event["tracker_delivery_details"].values() if d.get("queue_item_id") == item_id)
    assert detail["state"] == "failed" and detail["category"] == "transient"
    assert queue.retry_failed_item(item_id, "expired-event") == "confirmation_required"
    await process_queue(MagicMock(), queue, on_result=callback)
    callback.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_queue_workers_dispatch_a_claimed_item_once(tmp_path):
    queue = QueueManager(tmp_path / "concurrent_queue.db")
    queue.enqueue("sync_ratings", {"movies": []}, event_id="event-1")
    request_started = asyncio.Event()
    allow_response = asyncio.Event()

    async def delayed_rating_sync(_payload):
        request_started.set()
        await allow_response.wait()
        return {"status": 201}

    client = MagicMock(spec=TraktClient)
    client.sync_ratings = AsyncMock(side_effect=delayed_rating_sync)
    first_worker = asyncio.create_task(process_queue(client, queue))
    await asyncio.wait_for(request_started.wait(), timeout=2)

    second_result = await process_queue(client, queue)
    assert second_result == {"processed": 0, "succeeded": 0, "failed": 0}
    assert client.sync_ratings.await_count == 1
    assert queue.get_pending_count() == 1

    allow_response.set()
    first_result = await first_worker
    assert first_result["succeeded"] == 1
    assert client.sync_ratings.await_count == 1
    assert queue.get_pending_count() == 0


@pytest.mark.asyncio
async def test_uncertain_history_write_requires_confirmation_after_transient_failure(tmp_path):
    queue = QueueManager(tmp_path / "uncertain_history.db")
    item_id = queue.enqueue("sync_history", {"movies": []}, event_id="event-2")
    client = MagicMock(spec=TraktClient)
    client.sync_history = AsyncMock(return_value={"status": 503, "error": "HTTP 503"})

    result = await process_queue(client, queue)

    item = queue.get_item(item_id)
    assert result["failed"] == 1
    assert item["status"] == "failed"
    assert "outcome uncertain" in item["last_error"].lower()
    client.sync_history.assert_awaited_once()
    assert queue.retry_failed_item(item_id, "event-2") == "confirmation_required"
    assert queue.retry_failed_item(item_id, "event-2", confirm_duplicate_history=True) == "queued"


@pytest.mark.asyncio
async def test_manual_simkl_history_retry_uses_simkl_and_preserves_confirmation_gate(tmp_path):
    queue = QueueManager(tmp_path / "simkl_history.db")
    item_id = queue.enqueue(
        "simkl_sync_history",
        {
            "media": {
                "event": "manual.scrobble",
                "username": "admin",
                "media_type": "movie",
                "title": "Example Movie",
                "progress": 100.0,
                "ids": {"imdb": "tt1234567"},
            },
            "watched_at": "2026-10-09T12:00:00+00:00",
        },
        error="Outcome uncertain after manual delivery; retry may duplicate watch history.",
        event_id="event-simkl",
        status="failed",
    )
    assert queue.retry_failed_item(item_id, "event-simkl") == "confirmation_required"
    assert queue.retry_all_failed() == 0
    assert queue.retry_failed_item(item_id, "event-simkl", confirm_duplicate_history=True) == "queued"

    simkl = MagicMock()
    simkl.is_enabled.return_value = True
    simkl.is_authenticated.return_value = True
    simkl.sync_history = AsyncMock(return_value={"status": "success"})
    updates = []
    result = await process_queue(
        MagicMock(spec=TraktClient),
        queue,
        simkl_client=simkl,
        on_result=lambda *args: updates.append(args),
    )

    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    simkl.sync_history.assert_awaited_once()
    media_arg = simkl.sync_history.await_args.args[0]
    assert media_arg.title == "Example Movie"
    assert simkl.sync_history.await_args.kwargs["watched_at"] == "2026-10-09T12:00:00+00:00"
    assert updates[0][0:4] == ("event-simkl", "simkl_sync_history", item_id, "success")
    assert queue.get_event_queue_state("event-simkl", tracker="simkl") == "success"


@pytest.mark.asyncio
async def test_manual_simkl_rate_limit_stays_queued_for_automatic_retry(tmp_path):
    queue = QueueManager(tmp_path / "simkl_rate_limit.db")
    item_id = queue.enqueue(
        "simkl_sync_history",
        {"media": {"event": "manual.scrobble", "username": "admin", "media_type": "movie", "title": "Example"}},
        event_id="event-rate-limit",
    )
    simkl = MagicMock()
    simkl.is_enabled.return_value = True
    simkl.is_authenticated.return_value = True
    simkl.sync_history = AsyncMock(return_value={"status": "error", "code": 429, "error": "rate_limited"})

    result = await process_queue(MagicMock(spec=TraktClient), queue, simkl_client=simkl)

    assert result["failed"] == 1
    assert queue.get_item(item_id)["status"] == "pending"
    assert queue.get_pending_count() == 1


@pytest.mark.parametrize("event_type", ["sync_history", "scrobble_stop", "simkl_sync_history"])
def test_stale_history_claim_is_failed_for_manual_recovery(tmp_path, event_type):
    queue = QueueManager(tmp_path / "stale_history.db")
    item_id = queue.enqueue(event_type, {"movie": {"title": "Example"}}, event_id="event-3")
    assert queue.claim_pending()[0]["status"] == "processing"

    with queue._get_connection() as connection:
        connection.execute(
            "UPDATE queued_events SET claimed_at = ? WHERE id = ?",
            (int(time.time()) - 301, item_id),
        )
        connection.commit()

    # Claiming again expires the old lease but does not automatically replay history.
    assert queue.claim_pending() == []
    stale = queue.get_item(item_id)
    assert stale["status"] == "failed"
    assert queue.retry_failed_item(item_id, "event-3") == "confirmation_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_tracker", ["trakt", "simkl"])
async def test_disconnected_destination_does_not_starve_other_queued_work(tmp_path, blocked_tracker):
    queue = QueueManager(tmp_path / "disconnected.db")
    blocked_id = queue.enqueue(
        "sync_history" if blocked_tracker == "trakt" else "simkl_sync_history",
        {}, username="disconnected",
    )
    ready_id = queue.enqueue("sync_ratings", {"movies": []}, username="connected")
    disconnected = MagicMock()
    disconnected.is_authenticated.return_value = False
    connected = MagicMock(spec=TraktClient)
    connected.is_authenticated.return_value = True
    connected.sync_ratings = AsyncMock(return_value={"status": 201})
    users = MagicMock()
    users.get_client.side_effect = lambda user: disconnected if user == "disconnected" else connected

    result = await process_queue(connected, queue, user_mgr=users, max_items=1)

    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    connected.sync_ratings.assert_awaited_once()
    assert queue.get_item(ready_id)["status"] == "completed"
    assert queue.get_item(blocked_id)["status"] == "pending"
    assert queue.get_item(blocked_id)["retry_count"] == 0


def test_schema_v1_queue_migrates_claim_timestamp_to_v2(tmp_path):
    database = tmp_path / "legacy_queue.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE queued_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL DEFAULT 'default',
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                completed_at INTEGER DEFAULT NULL,
                event_id TEXT DEFAULT NULL
            )"""
        )
        connection.execute("PRAGMA user_version = 1")

    queue = QueueManager(database)
    with sqlite3.connect(database) as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(queued_events)")}
    assert schema_version == 2
    assert "claimed_at" in columns
    assert queue.get_pending_count() == 0
