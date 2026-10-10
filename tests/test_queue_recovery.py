"""Queue leasing and retry-safety regression tests."""

import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients.trakt_client import TraktClient
from app.services.queue_manager import QueueManager, process_queue


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


def test_stale_history_claim_is_failed_for_manual_recovery(tmp_path):
    queue = QueueManager(tmp_path / "stale_history.db")
    item_id = queue.enqueue("scrobble_stop", {"movie": {"title": "Example"}}, event_id="event-3")
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
