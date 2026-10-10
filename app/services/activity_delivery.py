"""Activity delivery classification and event outcome persistence."""

import asyncio
import datetime
from collections.abc import Callable, Iterable
from typing import Any, Optional

from app.services.result_normalizer import normalize_result


class ActivityDeliveryManager:
    """Track destination outcomes against activity events using injected state."""

    def __init__(
        self,
        *,
        events: Iterable[dict[str, Any]],
        save_events: Callable[[], None],
        queue: Any,
        log_manager: Any,
        notifier: Any,
    ):
        self.events = events
        self.save_events = save_events
        self.queue = queue
        self.log_manager = log_manager
        self.notifier = notifier

    def _tracker_result_state(self, result: Any) -> str:
        """Reduce upstream responses to a small, safe status for activity badges."""
        return normalize_result(result).state

    def _tracker_delivery_from_result(self, result: dict[str, Any], action: str) -> dict[str, str]:
        """Extract known delivery results from synchronous Trakt and manual dispatches."""
        delivery: dict[str, str] = {}
        synced = result.get("synced_trackers")
        if not isinstance(synced, list):
            synced = result.get("removed_trackers")
        errors = result.get("errors") if isinstance(result.get("errors"), dict) else {}
        if isinstance(synced, list) or errors:
            synced = synced if isinstance(synced, list) else []
            aliases = {"myanimelist": "mal"}
            delivery.update({aliases.get(str(tracker).lower(), str(tracker).lower()): "success" for tracker in synced})
            delivery.update({aliases.get(str(tracker).lower(), str(tracker).lower()): "failed" for tracker in errors})
            queued = result.get("queued_trackers")
            if isinstance(queued, list):
                delivery.update({aliases.get(str(tracker).lower(), str(tracker).lower()): "queued" for tracker in queued})
            return delivery
        aliases = {"myanimelist": "mal"}
        reported_trackers = result.get("trackers", [])
        if not isinstance(reported_trackers, list):
            reported_trackers = []
        for tracker in reported_trackers:
            tracker_key = aliases.get(str(tracker).lower(), str(tracker).lower())
            result_key = "myanimelist" if tracker_key == "mal" else tracker_key
            delivery[tracker_key] = self._tracker_result_state(result.get(result_key))
        skipped = result.get("skipped") if isinstance(result.get("skipped"), dict) else {}
        for tracker in skipped:
            delivery[aliases.get(str(tracker).lower(), str(tracker).lower())] = "skipped"
        if delivery:
            return delivery
        if action in {"bypassed", "none"} or result.get("status") in {"ignored", "skipped"}:
            delivery["trakt"] = "skipped"
        else:
            delivery["trakt"] = self._tracker_result_state(result)
        return delivery

    def update_event_tracker_delivery(
        self,
        event_id: str,
        delivery: dict[str, str],
        results: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist asynchronous tracker outcomes onto the originating activity event."""
        for event_entry in self.events:
            if event_entry.get("event_id") != event_id:
                continue
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            current = event_entry.setdefault("tracker_delivery", {})
            details = event_entry.setdefault("tracker_delivery_details", {})
            for tracker, state in delivery.items():
                if state not in {"success", "queued", "failed", "skipped", "partial"}:
                    continue
                current[tracker] = state
                detail = details.setdefault(tracker, {"attempts": 0, "history": []})
                reason = self._delivery_reason(state, (results or {}).get(tracker))
                category = self._delivery_category(state, (results or {}).get(tracker))
                detail.update({"state": state, "reason": reason, "updated_at": now})
                detail["category"] = category
                detail["attempts"] = int(detail.get("attempts", 0)) + 1
                history = detail.setdefault("history", [])
                history.append({"state": state, "category": category, "reason": reason, "timestamp": now})
                del history[:-10]
            event_entry["delivery_status"] = self._aggregate_delivery_status(current)
            self.save_events()
            return

    def _aggregate_delivery_status(self, delivery: dict[str, str]) -> str:
        states = set(delivery.values())
        has_failure = bool(states.intersection({"failed", "partial"}))
        has_success = "success" in states
        has_queued = "queued" in states
        if has_failure and (has_success or has_queued):
            return "partial"
        if has_failure:
            return "failed"
        if has_queued:
            return "queued"
        if has_success:
            return "success"
        return "skipped"

    def _delivery_reason(self, state: str, result: Any = None) -> str:
        """Create a concise, redacted explanation for a destination outcome."""
        if isinstance(result, dict):
            for key in ("reason", "error", "message", "errors"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return self.log_manager.sanitize_line(value.strip())[:240]
                if isinstance(value, list) and value:
                    return self.log_manager.sanitize_line(str(value[0]))[:240]
            for value in result.values():
                if isinstance(value, dict):
                    reason = self._delivery_reason(state, value)
                    if reason != self._delivery_reason(state):
                        return reason
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            reason = self._delivery_reason(state, item)
                            if reason != self._delivery_reason(state):
                                return reason
        return {
            "success": "Delivered successfully.",
            "queued": "Saved for retry after a temporary error.",
            "failed": "The destination could not process this operation.",
            "partial": "Some household destinations did not complete the operation.",
            "skipped": "The destination did not receive this event.",
        }.get(state, "Outcome recorded.")

    def _delivery_category(self, state: str, result: Any = None) -> str:
        """Classify an outcome without exposing raw upstream responses."""
        if state in {"success", "queued", "partial"}:
            return {"success": "delivered", "queued": "queued", "partial": "partial"}[state]
        if state == "skipped":
            reason = self._delivery_reason(state, result).lower()
            return "unsupported" if any(term in reason for term in ("unsupported", "does not support", "not applicable", "not configured", "disabled")) else "intentional_skip"
        if state == "failed" and "uncertain" in self._delivery_reason(state, result).lower():
            return "transient"

        return normalize_result(result).category

    def _initial_delivery_details(self, result: dict[str, Any], delivery: dict[str, str]) -> dict[str, dict[str, Any]]:
        """Build compact, redacted destination details for a newly recorded event."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        details: dict[str, dict[str, Any]] = {}
        for tracker, state in delivery.items():
            tracker_result = result.get(tracker)
            if tracker == "mal":
                tracker_result = result.get("myanimelist", tracker_result)
            skipped_result = result.get("skipped", {})
            if tracker_result is None and isinstance(skipped_result, dict):
                tracker_result = skipped_result.get(tracker)
            errors = result.get("errors")
            if isinstance(errors, dict) and tracker in errors:
                tracker_result = {"error": errors[tracker]}
            reason = self._delivery_reason(state, tracker_result)
            category = self._delivery_category(state, tracker_result)
            details[tracker] = {
                "state": state,
                "category": category,
                "reason": reason,
                "attempts": 1,
                "updated_at": now,
                "history": [{"state": state, "category": category, "reason": reason, "timestamp": now}],
            }
        return details

    def record_queue_delivery(
        self,
        event_id: str,
        event_type: str,
        queue_item_id: int,
        state: str,
        reason: str,
        attempt: int,
    ) -> None:
        """Attach a queue retry result and aggregate queue state to its source event."""
        tracker = "simkl" if event_type.startswith("simkl_") else "trakt"
        destination = f"{tracker.title()} · {event_type.removeprefix('simkl_').replace('_', ' ')} · queue {queue_item_id}"
        safe_reason = self.log_manager.sanitize_line(reason.strip())[:240] if reason else self._delivery_reason(state)
        category = self._delivery_category(state, {"error": safe_reason} if reason else None)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for event_entry in self.events:
            if event_entry.get("event_id") != event_id:
                continue
            detail = event_entry.setdefault("tracker_delivery_details", {}).setdefault(destination, {"attempts": 0, "history": []})
            previous_state = detail.get("state")
            history = detail.setdefault("history", [])
            history.append({"state": state, "category": category, "reason": safe_reason, "timestamp": now})
            del history[:-10]
            scheduled_retry = state == "queued" and safe_reason == "Manually queued for retry."
            updated_attempts = int(detail.get("attempts", 0)) if scheduled_retry else max(attempt, int(detail.get("attempts", 0)) + 1)
            detail.update({
                "state": state,
                "category": category,
                "reason": safe_reason,
                "attempts": updated_attempts,
                "updated_at": now,
                "history": history,
                "queue_item_id": queue_item_id,
                "queue_event_type": event_type,
            })
            if state == "success" and previous_state == "queued" and self.notifier._profile_subscribed("queue_recovery", str(event_entry.get("user", ""))):
                asyncio.create_task(self.notifier.send_operational_notification(
                    "queue_recovery", "Queued sync recovered",
                    f"A queued {event_type.replace('_', ' ')} update was delivered successfully.",
                ))
            self.save_events()
            break

        overall_state = self.queue.get_event_queue_state(event_id, tracker=tracker)
        self.update_event_tracker_delivery(event_id, {tracker: overall_state})

    def update_event_cowatch_status(self, event_id: str, status: str, reason: Optional[str] = None) -> None:
        """Persist the completed outcome of a background Co-Watch sync."""
        for event_entry in self.events:
            if event_entry.get("event_id") == event_id:
                current = event_entry.setdefault("cowatch_status", {})
                current.update({
                    "status": status,
                    "scheduled": False,
                    "synced": status == "success",
                })
                if reason:
                    current["reason"] = reason
                self.update_event_tracker_delivery(
                    event_id,
                    {"household": status},
                    {"household": {"error": reason} if reason else {}},
                )
                self.save_events()
                return

    async def record_mirror_delivery(self, event_id: str, mirror_call: Any) -> None:
        """Attach a background media-server mirror result to its source activity event."""
        try:
            result = await mirror_call
        except Exception as exc:
            result = {"status": "failed", "error": str(exc)}
        mirror_results = result.get("results", {}) if isinstance(result, dict) else {}
        failures = mirror_results.get("failed", []) if isinstance(mirror_results, dict) else []
        mirrored = mirror_results.get("mirrored", []) if isinstance(mirror_results, dict) else []
        if isinstance(result, dict) and result.get("status") in {"failed", "error"}:
            state = "partial" if mirrored else "failed"
        elif failures:
            state = "failed" if not mirrored else "partial"
        elif mirrored:
            state = "success"
        else:
            state = "skipped"
        self.update_event_tracker_delivery(event_id, {"media_servers": state}, {"media_servers": result})
