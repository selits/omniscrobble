import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional

try:
    from app.config import Config
    from app.clients.trakt_client import TraktClient
    from app.services.result_normalizer import normalize_result
except ImportError:
    from config import Config
    from trakt_client import TraktClient
    from services.result_normalizer import normalize_result

logger = logging.getLogger("queue_manager")


class QueueManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Config.QUEUE_DB_FILE
        self._future_schema_version: int | None = None
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._future_schema_version is not None:
            raise RuntimeError("Offline queue uses a newer schema; access is disabled with this version.")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > 1:
            self._future_schema_version = version
            conn.close()
            raise RuntimeError("Offline queue uses a newer schema; access is disabled with this version.")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def reload_schema(self) -> None:
        """Reinspect a restored database before any queue access resumes."""
        self._future_schema_version = None
        self.init_db()

    def init_db(self) -> None:
        """Create the queued_events table and indexes if they do not exist."""
        if self.db_path.exists():
            with sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True) as probe:
                version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            if version > 1:
                self._future_schema_version = version
                logger.error("Offline queue schema %s is newer than supported; access is disabled.", version)
                return
        with self._get_connection() as conn:
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > 1:
                self._future_schema_version = schema_version
                logger.error("Offline queue schema %s is newer than supported; leaving it unchanged.", schema_version)
                return
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queued_events (
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
                )
                """
            )
            try:
                conn.execute("ALTER TABLE queued_events ADD COLUMN username TEXT DEFAULT 'default'")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE queued_events ADD COLUMN completed_at INTEGER DEFAULT NULL")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE queued_events ADD COLUMN event_id TEXT DEFAULT NULL")
            except Exception:
                pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_events_status ON queued_events(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_events_completed_at ON queued_events(completed_at)"
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()

    def enqueue(
        self,
        event_type: str,
        payload: dict[str, Any],
        error: str = "",
        username: str = "default",
        event_id: Optional[str] = None,
    ) -> int:
        """Insert a failed event into the offline retry queue."""
        payload_str = json.dumps(payload)
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queued_events (username, event_type, payload, created_at, retry_count, last_error, status, event_id)
                VALUES (?, ?, ?, ?, 0, ?, 'pending', ?)
                """,
                (username or "default", event_type, payload_str, now, error, event_id),
            )
            conn.commit()
            item_id = cursor.lastrowid or 0
        logger.info(f"Enqueued {event_type} event (id: {item_id}) for offline retry")
        return item_id

    def attach_event(self, item_ids: list[int], event_id: str) -> None:
        """Associate queued operations created before their source event was persisted."""
        if not item_ids or not event_id:
            return
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE queued_events SET event_id = ? WHERE id = ?",
                [(event_id, item_id) for item_id in item_ids],
            )
            conn.commit()

    def get_event_queue_state(self, event_id: str) -> str:
        """Summarize linked queue items for one source activity event."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT status FROM queued_events WHERE event_id = ?",
                (event_id,),
            ).fetchall()
        if not rows:
            return "success"
        statuses = {str(row["status"]) for row in rows}
        has_pending = "pending" in statuses
        has_failed = "failed" in statuses
        has_completed = "completed" in statuses
        if has_failed and (has_pending or has_completed):
            return "partial"
        if has_pending:
            return "queued"
        if has_failed:
            return "failed"
        return "success"

    def retry_failed_item(
        self,
        item_id: int,
        event_id: str,
        confirm_duplicate_history: bool = False,
    ) -> str:
        """Requeue one failed operation linked to an event, with explicit duplicate-risk approval."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT event_type, status, event_id, last_error FROM queued_events WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row or row["event_id"] != event_id:
                return "not_found"
            if row["status"] != "failed":
                return "not_failed"
            event_type = str(row["event_type"])
            if event_type not in {"sync_collection", "sync_ratings", "sync_watchlist", "sync_history", "scrobble_stop"}:
                return "unsupported"
            last_error = str(row["last_error"] or "").lower()
            if any(term in last_error for term in ("unauthorized", "forbidden", "invalid token", "not authenticated", "http 400", "http 401", "http 403", "http 404")):
                return "not_retryable"
            retryable_hint = any(term in last_error for term in (
                "timeout", "network", "connect", "unavailable", "temporarily", "http 429",
                "http 500", "http 502", "http 503", "http 504", "service busy",
            ))
            if not retryable_hint:
                return "not_retryable"
            if event_type in {"sync_history", "scrobble_stop"} and not confirm_duplicate_history:
                return "confirmation_required"
            conn.execute(
                "UPDATE queued_events SET status = 'pending', retry_count = 0, last_error = '' WHERE id = ?",
                (item_id,),
            )
            conn.commit()
        return "queued"

    def get_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve pending events sorted by created_at ascending."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, event_type, payload, created_at, retry_count, last_error, status, event_id
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
                "username": r["username"] if "username" in r.keys() else "default",
                "event_type": r["event_type"],
                "payload": p,
                "created_at": r["created_at"],
                "retry_count": r["retry_count"],
                "last_error": r["last_error"],
                "status": r["status"],
                "event_id": r["event_id"],
            })
        return items

    def mark_success(self, item_id: int) -> None:
        """Mark a successfully processed item as completed in the queue."""
        now = int(time.time())
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE queued_events SET status = 'completed', completed_at = ? WHERE id = ?",
                (now, item_id),
            )
            conn.commit()

    def mark_failure(self, item_id: int, error: str = "", max_retries: int = 5) -> str:
        """Increment retry count, and transition to 'failed' if max_retries reached."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT retry_count FROM queued_events WHERE id = ?", (item_id,)
            )
            row = cursor.fetchone()
            if not row:
                return "missing"

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
            return new_status

    def get_pending_count(self) -> int:
        """Return count of items currently in 'pending' status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM queued_events WHERE status = 'pending'"
            )
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0

    def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
        """Return one queue item by ID, independent of its current status."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, username, event_type, payload, created_at, retry_count, last_error, status, event_id FROM queued_events WHERE id = ?",
                (item_id,),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except Exception:
            payload = {}
        return {
            "id": row["id"],
            "username": row["username"],
            "event_type": row["event_type"],
            "payload": payload,
            "created_at": row["created_at"],
            "retry_count": row["retry_count"],
            "last_error": row["last_error"],
            "status": row["status"],
            "event_id": row["event_id"],
        }

    def get_all_count(self) -> dict[str, int]:
        """Return total counts by status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM queued_events GROUP BY status"
            )
            rows = cursor.fetchall()
        counts = {"pending": 0, "failed": 0, "completed": 0}
        for r in rows:
            counts[r["status"]] = int(r["cnt"])
        return counts

    def prune_queue(self, days: int = 90) -> int:
        """Prune completed and failed records older than the specified retention days (default 90)."""
        cutoff = int(time.time()) - (days * 86400)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM queued_events
                WHERE (status = 'completed' AND (completed_at < ? OR (completed_at IS NULL AND created_at < ?)))
                   OR (status = 'failed' AND created_at < ?)
                """,
                (cutoff, cutoff, cutoff),
            )
            conn.commit()
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Pruned {deleted} offline queue records older than {days} days")
        return deleted

    prune_completed = prune_queue

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


async def process_queue(
    trakt_client: TraktClient,
    queue_mgr: QueueManager,
    max_items: int = 20,
    user_mgr: Optional[Any] = None,
    on_result: Optional[Any] = None,
) -> dict[str, int]:
    """Drain pending items from the offline queue and dispatch to Trakt."""
    pending = queue_mgr.get_pending(limit=max_items)
    if not pending:
        return {"processed": 0, "succeeded": 0, "failed": 0}

    succeeded = 0
    failed = 0

    for item in pending:
        item_id = item["id"]
        username = item.get("username", "default")
        event_type = item["event_type"]
        payload = item["payload"]

        def report(state: str, reason: str = "") -> None:
            if on_result and item.get("event_id"):
                try:
                    on_result(item["event_id"], event_type, item_id, state, reason, item.get("retry_count", 0) + 2)
                except Exception as callback_error:
                    logger.warning("Could not attach queue result to activity event %s: %s", item.get("event_id"), callback_error)

        client = user_mgr.get_client(username) if user_mgr else trakt_client

        # Keep user-specific work pending until that profile is connected.
        # Otherwise an unauthenticated response can be mistaken for success
        # and permanently discard Co-Watch history.
        if user_mgr and not client.is_authenticated():
            logger.info(f"Queued event {item_id} ({event_type}) is waiting for Trakt authentication for @{username}.")
            continue

        try:
            res: dict[str, Any] = {}
            if event_type == "scrobble_stop":
                res = await client.scrobble_stop(payload)
            elif event_type == "sync_history":
                res = await client.sync_history(payload)
            elif event_type == "sync_ratings":
                res = await client.sync_ratings(payload)
            elif event_type == "sync_collection":
                res = await client.sync_collection(payload)
            elif event_type == "sync_watchlist":
                res = await client.sync_watchlist(payload)
            elif event_type == "scrobble_start":
                res = await client.scrobble_start(payload)
            elif event_type == "scrobble_pause":
                res = await client.scrobble_pause(payload)
            else:
                logger.warning(f"Unknown queued event type: {event_type}")
                reason = f"Unknown event type: {event_type}"
                queue_mgr.mark_failure(item_id, reason, max_retries=1)
                report("failed", reason)
                failed += 1
                continue

            normalized = normalize_result(res)
            status = res.get("status")
            error_val = res.get("error")
            if normalized.state == "failed":
                logger.warning(f"Queued event {item_id} ({event_type}) returned an error: {error_val or res}")
                reason = str(error_val or res)
                if normalized.retryable:
                    queue_status = queue_mgr.mark_failure(item_id, reason, max_retries=5)
                    report("queued" if queue_status == "pending" else "failed", reason)
                    failed += 1
                    logger.warning(f"Queued event {item_id} ({event_type}) failed temporarily")
                    break
                queue_mgr.mark_failure(item_id, reason, max_retries=1)
                report("failed", reason)
                failed += 1
                continue

            if normalized.state == "queued":
                report("queued", str(error_val or "Saved for retry."))
                failed += 1
                continue

            # A 409 is an idempotent conflict from Trakt and counts as delivered.
            if not normalized.state == "success" and status != 409:
                reason = str(error_val or "The tracker did not confirm this operation.")
                queue_mgr.mark_failure(item_id, reason, max_retries=1)
                report("failed", reason)
                failed += 1
                continue
            else:
                # Success or idempotent response.
                queue_mgr.mark_success(item_id)
                report("success")
                succeeded += 1
                logger.info(f"Successfully processed queued event {item_id} ({event_type})")

        except Exception as e:
            logger.error(f"Error processing queued event {item_id}: {e}")
            queue_status = queue_mgr.mark_failure(item_id, str(e))
            report("queued" if queue_status == "pending" else "failed", str(e))
            failed += 1
            break

    return {"processed": len(pending), "succeeded": succeeded, "failed": failed}
