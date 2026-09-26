import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional

from config import Config
from trakt_client import TraktClient

logger = logging.getLogger("queue_manager")


class QueueManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Config.QUEUE_DB_FILE
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Create the queued_events table and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queued_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_events_status ON queued_events(status)"
            )
            conn.commit()

    def enqueue(self, event_type: str, payload: dict[str, Any], error: str = "") -> int:
        """Insert a failed event into the offline retry queue."""
        payload_str = json.dumps(payload)
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queued_events (event_type, payload, created_at, retry_count, last_error, status)
                VALUES (?, ?, ?, 0, ?, 'pending')
                """,
                (event_type, payload_str, now, error),
            )
            conn.commit()
            item_id = cursor.lastrowid or 0
        logger.info(f"Enqueued {event_type} event (id: {item_id}) for offline retry")
        return item_id

    def get_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve pending events sorted by created_at ascending."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, event_type, payload, created_at, retry_count, last_error, status
                FROM queued_events
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        items = []
        for r in rows:
            try:
                p = json.loads(r["payload"])
            except Exception:
                p = {}
            items.append({
                "id": r["id"],
                "event_type": r["event_type"],
                "payload": p,
                "created_at": r["created_at"],
                "retry_count": r["retry_count"],
                "last_error": r["last_error"],
                "status": r["status"],
            })
        return items

    def mark_success(self, item_id: int) -> None:
        """Remove a successfully processed item from the queue."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM queued_events WHERE id = ?", (item_id,))
            conn.commit()

    def mark_failure(self, item_id: int, error: str = "", max_retries: int = 5) -> None:
        """Increment retry count, and transition to 'failed' if max_retries reached."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT retry_count FROM queued_events WHERE id = ?", (item_id,)
            )
            row = cursor.fetchone()
            if not row:
                return

            new_count = row["retry_count"] + 1
            new_status = "failed" if new_count >= max_retries else "pending"
            conn.execute(
                """
                UPDATE queued_events
                SET retry_count = ?, last_error = ?, status = ?
                WHERE id = ?
                """,
                (new_count, error, new_status, item_id),
            )
            conn.commit()

    def get_pending_count(self) -> int:
        """Return count of items currently in 'pending' status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM queued_events WHERE status = 'pending'"
            )
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0

    def get_all_count(self) -> dict[str, int]:
        """Return total counts by status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM queued_events GROUP BY status"
            )
            rows = cursor.fetchall()
        counts = {"pending": 0, "failed": 0}
        for r in rows:
            counts[r["status"]] = int(r["cnt"])
        return counts

    def clear_queue(self) -> None:
        """Purge all queued events."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM queued_events")
            conn.commit()

    def retry_all_failed(self) -> int:
        """Reset failed items back to pending status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE queued_events SET status = 'pending', retry_count = 0 WHERE status = 'failed'"
            )
            conn.commit()
            return cursor.rowcount


async def process_queue(trakt_client: TraktClient, queue_mgr: QueueManager, max_items: int = 20) -> dict[str, int]:
    """Drain pending items from the offline queue and dispatch to Trakt."""
    pending = queue_mgr.get_pending(limit=max_items)
    if not pending:
        return {"processed": 0, "succeeded": 0, "failed": 0}

    succeeded = 0
    failed = 0

    for item in pending:
        item_id = item["id"]
        event_type = item["event_type"]
        payload = item["payload"]

        try:
            res: dict[str, Any] = {}
            if event_type == "scrobble_stop":
                res = await trakt_client.scrobble_stop(payload)
            elif event_type == "sync_history":
                res = await trakt_client.sync_history(payload)
            elif event_type == "sync_ratings":
                res = await trakt_client.sync_ratings(payload)
            elif event_type == "scrobble_start":
                res = await trakt_client.scrobble_start(payload)
            elif event_type == "scrobble_pause":
                res = await trakt_client.scrobble_pause(payload)
            else:
                logger.warning(f"Unknown queued event type: {event_type}")
                queue_mgr.mark_failure(item_id, f"Unknown event type: {event_type}")
                failed += 1
                continue

            status = res.get("status")
            error_val = res.get("error")

            # Check if Trakt responded with temporary error (5xx, 429, or connection error)
            is_temp_error = (
                status in (500, 502, 503, 504, 429)
                or (isinstance(error_val, str) and any(e in error_val.lower() for e in ("connect", "timeout", "network", "service unavailable")))
            )

            if is_temp_error:
                logger.warning(f"Queued event {item_id} ({event_type}) failed with temporary error: {res}")
                queue_mgr.mark_failure(item_id, str(error_val or f"HTTP {status}"))
                failed += 1
                # Stop processing the remainder of this batch to prevent hammering unreachable server
                break
            else:
                # Success or non-retryable response (e.g. 200, 201, 409 conflict)
                queue_mgr.mark_success(item_id)
                succeeded += 1
                logger.info(f"Successfully processed queued event {item_id} ({event_type})")

        except Exception as e:
            logger.error(f"Error processing queued event {item_id}: {e}")
            queue_mgr.mark_failure(item_id, str(e))
            failed += 1
            break

    return {"processed": len(pending), "succeeded": succeeded, "failed": failed}
