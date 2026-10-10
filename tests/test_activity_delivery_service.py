"""Unit tests for explicitly injected activity delivery state and collaborators."""

import json
from unittest.mock import MagicMock

from app.services.activity_delivery import ActivityDeliveryManager
from app.services.queue_manager import QueueManager


def test_activity_delivery_manager_updates_injected_event_store():
    event = {"event_id": "event-1"}
    saved = []

    class LogManager:
        @staticmethod
        def sanitize_line(line):
            return f"safe:{line}"

    delivery = ActivityDeliveryManager(
        events=[event],
        save_events=lambda: saved.append(True),
        queue=object(),
        log_manager=LogManager(),
        notifier=object(),
    )

    delivery.update_event_tracker_delivery(
        "event-1",
        {"trakt": "success", "simkl": "failed"},
        {"simkl": {"error": "temporary upstream failure"}},
    )

    assert event["tracker_delivery"] == {"trakt": "success", "simkl": "failed"}
    assert event["delivery_status"] == "partial"
    assert event["tracker_delivery_details"]["simkl"]["reason"] == "safe:temporary upstream failure"
    assert saved == [True]


def test_household_queue_mapping_survives_reload_and_keeps_owner_outcome_separate(tmp_path):
    queue = QueueManager(tmp_path / "destinations.db")
    event = {"event_id": "shared-event", "tracker_delivery": {"trakt": "queued"}}
    log = MagicMock()
    log.sanitize_line.side_effect = lambda text: text
    notifier = MagicMock()
    notifier._profile_subscribed.return_value = False
    manager = ActivityDeliveryManager(events=[event], save_events=MagicMock(), queue=queue,
                                      log_manager=log, notifier=notifier)
    owner_id = queue.enqueue("sync_history", {}, event_id="shared-event")
    partner_id = queue.enqueue("sync_history", {}, event_id="shared-event", username="partner")
    manager.register_cowatch_queue("shared-event", partner_id, "partner:trakt")
    manager.record_queue_delivery("shared-event", "sync_history", partner_id, "queued", "HTTP 429", 1)

    # Reconstruct persisted activity state as it would be loaded after restart.
    event = json.loads(json.dumps(event))
    manager.events = [event]
    queue.mark_failure(partner_id, "Outcome uncertain", max_retries=1)
    manager.record_queue_delivery("shared-event", "sync_history", partner_id, "failed", "Outcome uncertain", 2)
    queue.mark_success(owner_id)
    manager.record_queue_delivery("shared-event", "sync_history", owner_id, "success", "", 2)
    assert event["tracker_delivery"]["trakt"] == "success"
    assert event["tracker_delivery"]["household"] == "failed"
    assert event["cowatch_status"]["synced"] is False
    assert event["delivery_status"] == "partial"
