"""Unit tests for explicitly injected activity delivery state and collaborators."""

from app.services.activity_delivery import ActivityDeliveryManager


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
