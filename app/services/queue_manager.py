import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional

try:
    from app.config import Config
    from app.clients.trakt_client import TraktClient
    from app.plex_parser import ParsedMedia
    from app.services.result_normalizer import normalize_result
except ImportError:
    from config import Config
    from trakt_client import TraktClient
    from plex_parser import ParsedMedia
    from services.result_normalizer import normalize_result

logger = logging.getLogger("queue_manager")
QUEUE_SCHEMA_VERSION = 2
QUEUE_CLAIM_LEASE_SECONDS = 300
DUPLICATE_SENSITIVE_EVENT_TYPES = {"sync_history", "scrobble_stop", "simkl_sync_history"}


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
        if version > QUEUE_SCHEMA_VERSION:
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
            if version > QUEUE_SCHEMA_VERSION:
                self._future_schema_version = version
                logger.error("Offline queue schema %s is newer than supported; access is disabled.", version)
                return
        with self._get_connection() as conn:
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > QUEUE_SCHEMA_VERSION:
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
                    event_id TEXT DEFAULT NULL,
                    claimed_at INTEGER DEFAULT NULL
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
            try:
                conn.execute("ALTER TABLE queued_events ADD COLUMN claimed_at INTEGER DEFAULT NULL")
            except Exception:
                pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_events_status ON queued_events(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queued_events_completed_at ON queued_events(completed_at)"
            )
            conn.execute(f"PRAGMA user_version = {QUEUE_SCHEMA_VERSION}")
            conn.commit()

    def enqueue(
        self,
        event_type: str,
        payload: dict[str, Any],
        error: str = "",
        username: str = "default",
        event_id: Optional[str] = None,
        status: str = "pending",
    ) -> int:
        """Insert a failed event into the offline retry queue."""
        if status not in {"pending", "failed"}:
            raise ValueError("Queue entries may only be enqueued as pending or failed.")
        payload_str = json.dumps(payload)
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queued_events (username, event_type, payload, created_at, retry_count, last_error, status, event_id)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (username or "default", event_type, payload_str, now, error, status, event_id),
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

    def get_event_queue_state(self, event_id: str, tracker: Optional[str] = None,
                              exclude_ids: Optional[set[int]] = None) -> str:
        """Summarize linked queue items for one source activity event."""
        if tracker == "simkl":
            event_filter = "AND event_type = 'simkl_sync_history'"
        elif tracker == "trakt":
            event_filter = "AND event_type != 'simkl_sync_history'"
        else:
            event_filter = ""
        excluded = sorted(exclude_ids or ())
        if excluded:
            event_filter += f" AND id NOT IN ({', '.join('?' for _ in excluded)})"
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT status FROM queued_events WHERE event_id = ? {event_filter}",
                (event_id, *excluded),
            ).fetchall()
        if not rows:
            return "success"
        statuses = {str(row["status"]) for row in rows}
        has_pending = "pending" in statuses
        has_processing = "processing" in statuses
        has_failed = "failed" in statuses
        has_completed = "completed" in statuses
        if has_failed and (has_pending or has_processing or has_completed):
            return "partial"
        if has_pending or has_processing:
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
            if event_type not in {"sync_collection", "sync_ratings", "sync_watchlist", "sync_history", "scrobble_stop", "simkl_sync_history"}:
                return "unsupported"
            last_error = str(row["last_error"] or "").lower()
            if any(term in last_error for term in ("unauthorized", "forbidden", "invalid token", "not authenticated", "http 400", "http 401", "http 403", "http 404")):
                return "not_retryable"
            retryable_hint = any(term in last_error for term in (
                "timeout", "network", "connect", "unavailable", "temporarily", "http 429",
                "http 500", "http 502", "http 503", "http 504", "service busy", "uncertain", "interrupted",
            ))
            if not retryable_hint:
                return "not_retryable"
            if event_type in DUPLICATE_SENSITIVE_EVENT_TYPES and not confirm_duplicate_history:
                return "confirmation_required"
            conn.execute(
                "UPDATE queued_events SET status = 'pending', retry_count = 0, last_error = '', claimed_at = NULL WHERE id = ?",
                (item_id,),
            )
            conn.commit()
        return "queued"

    def claim_pending(self, limit: int = 20, lease_seconds: int = QUEUE_CLAIM_LEASE_SECONDS,
                      exclude_ids: Optional[set[int]] = None, on_expired: Optional[Any] = None) -> list[dict[str, Any]]:
        """Atomically lease pending work so concurrent workers cannot dispatch it twice."""
        now = int(time.time())
        cutoff = now - max(1, int(lease_seconds))
        history_types = sorted(DUPLICATE_SENSITIVE_EVENT_TYPES)
        history_placeholders = ", ".join("?" for _ in history_types)
        excluded = sorted(exclude_ids or ())
        exclusion = f"AND id NOT IN ({', '.join('?' for _ in excluded)})" if excluded else ""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            expired_history = conn.execute(
                f"""SELECT id, event_id, event_type, retry_count FROM queued_events
                WHERE status = 'processing' AND (claimed_at IS NULL OR claimed_at <= ?)
                    AND event_type IN ({history_placeholders})""",
                (cutoff, *history_types),
            ).fetchall()
            # An interrupted history write may already have reached its tracker. Keep it
            # failed for explicit, duplicate-aware manual recovery. Other queue
            # operations can safely return to pending.
            conn.execute(
                f"""
                UPDATE queued_events
                SET status = CASE WHEN event_type IN ({history_placeholders}) THEN 'failed' ELSE 'pending' END,
                    last_error = CASE WHEN event_type IN ({history_placeholders})
                        THEN 'Outcome uncertain after interrupted delivery; retry may duplicate history.'
                        ELSE last_error END,
                    claimed_at = NULL
                WHERE status = 'processing' AND (claimed_at IS NULL OR claimed_at <= ?)
                """,
                (*history_types, *history_types, cutoff),
            )
            rows = conn.execute(
                f"""
                SELECT id FROM queued_events
                WHERE status = 'pending' {exclusion}
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (*excluded, max(0, int(limit))),
            ).fetchall()
            item_ids = [int(row["id"]) for row in rows]
            if item_ids:
                conn.executemany(
                    "UPDATE queued_events SET status = 'processing', claimed_at = ? WHERE id = ? AND status = 'pending'",
                    [(now, item_id) for item_id in item_ids],
                )
            conn.commit()
        # Report after committing so Activity sees the recovered queue state.
        if on_expired:
            for item in expired_history:
                if item["event_id"]:
                    try:
                        on_expired(item["event_id"], item["event_type"], item["id"], "failed",
                                   "Outcome uncertain after interrupted delivery; retry may duplicate history.",
                                   item["retry_count"] + 2)
                    except Exception as exc:
                        logger.warning("Could not record expired queue item %s: %s", item["id"], exc)
        return [item for item_id in item_ids if (item := self.get_item(item_id)) is not None]

    def release_claim(self, item_id: int) -> bool:
        """Return an unprocessed leased item to pending."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE queued_events SET status = 'pending', claimed_at = NULL WHERE id = ? AND status = 'processing'",
                (item_id,),
            )
            conn.commit()
            return cursor.rowcount == 1

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
                "UPDATE queued_events SET status = 'completed', completed_at = ?, claimed_at = NULL WHERE id = ?",
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
                SET retry_count = ?, last_error = ?, status = ?, claimed_at = NULL
                WHERE id = ?
                """,
                (new_count, error, new_status, item_id),
            )
            conn.commit()
            return new_status

    def get_pending_count(self) -> int:
        """Return pending and currently leased queue item count."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM queued_events WHERE status IN ('pending', 'processing')"
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
        counts = {"pending": 0, "processing": 0, "failed": 0, "completed": 0}
        for r in rows:
            status = str(r["status"])
            counts[status] = int(r["cnt"])
            if status == "processing":
                counts["pending"] += int(r["cnt"])
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
        """Requeue safe idempotent failures; history writes need item-level confirmation."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE queued_events SET status = 'pending', retry_count = 0, claimed_at = NULL
                WHERE status = 'failed' AND event_type NOT IN ('sync_history', 'scrobble_stop', 'simkl_sync_history')
                """
            )
            conn.commit()
            return cursor.rowcount


async def process_queue(
    trakt_client: TraktClient,
    queue_mgr: QueueManager,
    max_items: int = 20,
    user_mgr: Optional[Any] = None,
    on_result: Optional[Any] = None,
    simkl_client: Optional[Any] = None,
) -> dict[str, int]:
    """Drain pending items from the offline queue and dispatch to their tracker."""
    succeeded = 0
    failed = 0
    processed = 0
    examined_ids: set[int] = set()

    while processed < max(0, max_items):
        pending = queue_mgr.claim_pending(limit=1, exclude_ids=examined_ids, on_expired=on_result)
        if not pending:
            break
        item = pending[0]
        item_id = item["id"]
        examined_ids.add(item_id)
        username = item.get("username", "default")
        event_type = item["event_type"]
        payload = item["payload"]

        def report(state: str, reason: str = "") -> None:
            if on_result and item.get("event_id"):
                try:
                    on_result(item["event_id"], event_type, item_id, state, reason, item.get("retry_count", 0) + 2)
                except Exception as callback_error:
                    logger.warning("Could not attach queue result to activity event %s: %s", item.get("event_id"), callback_error)

        client = user_mgr.get_client(username) if user_mgr and event_type != "simkl_sync_history" else trakt_client

        # Keep user-specific work pending until that profile is connected.
        # Otherwise an unauthenticated response can be mistaken for success
        # and permanently discard Co-Watch history.
        if event_type == "simkl_sync_history" and (
            simkl_client is None
            or not simkl_client.is_enabled()
            or not simkl_client.is_authenticated()
        ):
            logger.info("Queued Simkl event %s is waiting for Simkl authentication.", item_id)
            queue_mgr.release_claim(item_id)
            continue
        if event_type != "simkl_sync_history" and user_mgr and not client.is_authenticated():
            logger.info(f"Queued event {item_id} ({event_type}) is waiting for Trakt authentication for @{username}.")
            queue_mgr.release_claim(item_id)
            continue

        processed += 1
        try:
            res: dict[str, Any] = {}
            if event_type == "simkl_sync_history":
                media = ParsedMedia.model_validate(payload.get("media") or {})
                res = await simkl_client.sync_history(media, watched_at=payload.get("watched_at"))
            elif event_type == "scrobble_stop":
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
                    if event_type in DUPLICATE_SENSITIVE_EVENT_TYPES and normalized.status_code != 429:
                        reason = "Outcome uncertain after transient tracker failure; retry may duplicate watch history."
                        queue_mgr.mark_failure(item_id, reason, max_retries=1)
                        report("failed", reason)
                        failed += 1
                        break
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
                queue_mgr.release_claim(item_id)
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
            if event_type in DUPLICATE_SENSITIVE_EVENT_TYPES:
                reason = "Outcome uncertain after interrupted tracker delivery; retry may duplicate watch history."
                queue_mgr.mark_failure(item_id, reason, max_retries=1)
                report("failed", reason)
            else:
                reason = str(e)
                queue_status = queue_mgr.mark_failure(item_id, reason)
                report("queued" if queue_status == "pending" else "failed", reason)
            failed += 1
            break

    return {"processed": processed, "succeeded": succeeded, "failed": failed}
