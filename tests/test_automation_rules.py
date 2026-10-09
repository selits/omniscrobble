from datetime import datetime, timezone

import pytest

from app.services.automation_rules import AutomationRuleError, AutomationRulesManager


def make_rule(**overrides):
    rule = {
        "name": "Living room films",
        "enabled": True,
        "priority": 100,
        "conditions": {"servers": ["plex"], "media_types": ["movie"]},
        "action": "suppress",
    }
    rule.update(overrides)
    return rule


def test_rule_evaluation_is_ordered_explains_conflicts_and_defaults_to_allow(tmp_path):
    manager = AutomationRulesManager(tmp_path / "rules.json")
    rules = manager.replace_rules([
        make_rule(name="First match", priority=10),
        make_rule(name="Later allow", priority=20, action="allow"),
    ])
    matched = manager.evaluate({"server": "Plex", "media_type": "movie"})
    assert matched["decision"] == "suppress"
    assert matched["matched_rule"]["name"] == "First match"
    assert matched["conflicts"] == [{"id": rules[1]["id"], "name": "Later allow", "action": "allow", "priority": 20}]
    assert "conflicting rule" in matched["reason"]
    assert manager.evaluate({"server": "emby", "media_type": "movie"})["decision"] == "allow"


def test_rule_conditions_support_device_user_library_and_local_time_windows(tmp_path):
    manager = AutomationRulesManager(tmp_path / "rules.json")
    manager.replace_rules([make_rule(conditions={
        "servers": ["plex"],
        "libraries": ["Kids"],
        "media_types": ["movie"],
        "devices": ["Living Room"],
        "users": ["Casey"],
        "time_window": {"start": "20:00", "end": "01:00", "days": ["mon"], "timezone": "UTC"},
    })])
    event = {"server": "plex", "library": "Kids", "media_type": "movie", "device": "living room", "user": "casey"}
    after_midnight = manager.evaluate(event, now=datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc))
    assert after_midnight["decision"] == "suppress"
    assert manager.evaluate(event, now=datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc))["decision"] == "allow"


def test_rules_reject_match_all_invalid_windows_and_incomplete_routes(tmp_path):
    manager = AutomationRulesManager(tmp_path / "rules.json")
    with pytest.raises(AutomationRuleError, match="at least one condition"):
        manager.replace_rules([make_rule(conditions={})])
    with pytest.raises(AutomationRuleError, match="Wildcard-only"):
        manager.replace_rules([make_rule(conditions={"servers": ["*"]})])
    with pytest.raises(AutomationRuleError, match="HH:MM"):
        manager.replace_rules([make_rule(conditions={"time_window": {"start": "late", "end": "01:00", "days": ["mon"]}})])
    with pytest.raises(AutomationRuleError, match="select at least one"):
        manager.replace_rules([make_rule(action="route")])
    with pytest.raises(AutomationRuleError, match="same priority"):
        manager.replace_rules([make_rule(action="allow", priority=4), make_rule(action="suppress", priority=4)])


def test_rules_persist_and_preview_evaluation_does_not_write(tmp_path):
    path = tmp_path / "rules.json"
    manager = AutomationRulesManager(path)
    saved = manager.replace_rules([make_rule()])
    before = path.read_text()
    candidate = AutomationRulesManager.validate_rules([make_rule(action="allow")])
    preview = manager.evaluate_rules({"server": "plex", "media_type": "movie"}, candidate)
    assert preview["decision"] == "allow"
    assert path.read_text() == before
    assert AutomationRulesManager(path).list_rules() == saved


@pytest.mark.asyncio
async def test_live_suppress_rule_holds_event_before_tracker_dispatch(tmp_path, monkeypatch):
    from app import main as main_mod

    manager = AutomationRulesManager(tmp_path / "rules.json")
    manager.replace_rules([make_rule()])
    monkeypatch.setattr(main_mod, "automation_rules_mgr", manager)
    old_events = list(main_mod.recent_events)
    main_mod.recent_events.clear()
    try:
        media = main_mod.ParsedMedia(event="media.play", username="casey", media_type="movie", title="A sample film", server_type="plex", duration_ms=7_200_000)
        result = await main_mod.process_media_event(media)
        assert result["status"] == "ignored"
        assert result["rule"]["name"] == "Living room films"
        assert main_mod.recent_events[0]["rule_evaluation"]["decision"] == "suppress"
    finally:
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(old_events)


@pytest.mark.asyncio
async def test_live_route_rule_uses_selected_trackers_and_records_precedence(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app import main as main_mod

    manager = AutomationRulesManager(tmp_path / "route-rules.json")
    manager.replace_rules([make_rule(
        action="route",
        trackers=["trakt"],
        conditions={"servers": ["plex"], "media_types": ["movie"]},
    )])
    monkeypatch.setattr(main_mod, "automation_rules_mgr", manager)
    monkeypatch.setattr(main_mod.multi_tracker, "dispatch_scrobble", AsyncMock(return_value={
        "status": "success", "trackers": ["trakt"], "trakt": {"status": "success"}, "skipped": {},
    }))
    monkeypatch.setattr(main_mod.playback_mgr, "update_playback", lambda *_args, **_kwargs: None)
    old_events = list(main_mod.recent_events)
    main_mod.recent_events.clear()
    try:
        media = main_mod.ParsedMedia(event="media.play", username="casey", media_type="movie", title="Route film", server_type="plex", duration_ms=7_200_000)
        result = await main_mod.process_media_event(media)
        assert result["status"] == "success"
        assert result["rule"]["name"] == "Living room films"
        main_mod.multi_tracker.dispatch_scrobble.assert_awaited_once()
        assert main_mod.multi_tracker.dispatch_scrobble.await_args.kwargs["selected_trackers"] == ["trakt"]
        event = main_mod.recent_events[0]
        assert event["rule_evaluation"]["decision"] == "route"
        assert event["tracker_delivery"]["trakt"] == "success"
    finally:
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(old_events)


@pytest.mark.asyncio
async def test_automation_rule_admin_api_supports_evaluate_and_non_mutating_preview(tmp_path, monkeypatch):
    import httpx
    from app import main as main_mod
    from app.config import Config

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "automation-test-secret")
    monkeypatch.setattr(main_mod, "automation_rules_mgr", AutomationRulesManager(tmp_path / "api-rules.json"))
    transport = httpx.ASGITransport(app=main_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        denied = await client.get("/api/automation/rules")
        assert denied.status_code == 401
        headers = {"x-webhook-secret": "automation-test-secret"}
        saved = await client.put("/api/automation/rules", headers=headers, json=[make_rule()])
        assert saved.status_code == 200
        payload = {"event": {"server": "plex", "media_type": "movie"}}
        evaluated = await client.post("/api/automation/rules/evaluate", headers=headers, json=payload)
        assert evaluated.json()["decision"] == "suppress"
        before = await client.get("/api/automation/rules", headers=headers)
        preview = await client.post("/api/automation/rules/preview", headers=headers, json={
            "rules": [make_rule(action="allow")],
            "event": payload["event"],
        })
        assert preview.status_code == 200
        assert preview.json()["current"]["decision"] == "suppress"
        assert preview.json()["proposed"]["decision"] == "allow"
        assert preview.json()["decision_changes"] is True
        assert preview.json()["writes_applied"] is False
        after = await client.get("/api/automation/rules", headers=headers)
        assert after.json() == before.json()


@pytest.mark.asyncio
async def test_review_queue_rejects_or_approves_held_events_without_raw_payload(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    import httpx
    from app import main as main_mod
    from app.config import Config

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "review-test-secret")
    manager = AutomationRulesManager(tmp_path / "review-rules.json")
    manager.replace_rules([make_rule(action="review")])
    monkeypatch.setattr(main_mod, "automation_rules_mgr", manager)
    original_events = list(main_mod.recent_events)
    main_mod.recent_events.clear()
    transport = httpx.ASGITransport(app=main_mod.app)
    try:
        first = main_mod.ParsedMedia(event="media.play", username="casey", media_type="movie", title="Reject film", server_type="plex", duration_ms=7_200_000, file_path="/private/library/file.mkv", raw_payload={"token": "never-store"})
        held = await main_mod.process_media_event(first)
        event_id = held["event_id"]
        stored = main_mod.recent_events[0]
        assert stored["review_status"] == "pending"
        assert "file_path" not in stored["review_payload"]
        assert "raw_payload" not in stored["review_payload"]

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/api/automation/review")).status_code == 401
            headers = {"x-webhook-secret": "review-test-secret"}
            queue = await client.get("/api/automation/review", headers=headers)
            assert queue.json()["total"] == 1
            assert "review_payload" not in queue.json()["pending"][0]
            rejected = await client.post(f"/api/automation/review/{event_id}", headers=headers, json={"decision": "reject"})
            assert rejected.status_code == 200
            assert main_mod.recent_events[0]["review_status"] == "rejected"

            second = main_mod.ParsedMedia(event="media.play", username="casey", media_type="movie", title="Approve film", server_type="plex", duration_ms=7_200_000)
            approved_event = await main_mod.process_media_event(second)
            replay = AsyncMock(return_value={"status": "success", "event_id": "approved-playback-event"})
            monkeypatch.setattr(main_mod, "process_media_event", replay)
            approved = await client.post(f"/api/automation/review/{approved_event['event_id']}", headers=headers, json={"decision": "approve"})
            assert approved.status_code == 200
            replay.assert_awaited_once()
            assert replay.await_args.kwargs["skip_automation_rules"] is True
            assert next(event for event in main_mod.recent_events if event["event_id"] == approved_event["event_id"])["review_status"] == "approved"
    finally:
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(original_events)


@pytest.mark.asyncio
async def test_concurrent_review_approvals_dispatch_only_once(tmp_path, monkeypatch):
    """A pending review is claimed before approval dispatch yields to another request."""
    import asyncio
    import httpx
    from app import main as main_mod
    from app.config import Config

    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "review-race-secret")
    manager = AutomationRulesManager(tmp_path / "review-race-rules.json")
    manager.replace_rules([make_rule(action="review")])
    monkeypatch.setattr(main_mod, "automation_rules_mgr", manager)
    original_events = list(main_mod.recent_events)
    main_mod.recent_events.clear()
    media = main_mod.ParsedMedia(
        event="media.play", username="casey", media_type="movie", title="Single approval",
        server_type="plex", duration_ms=7_200_000,
    )
    held = await main_mod.process_media_event(media)
    dispatched = asyncio.Event()
    dispatch_count = 0

    async def slow_dispatch(*_args, **_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        await asyncio.sleep(0.05)
        dispatched.set()
        return {"status": "success", "event_id": "one-replay"}

    monkeypatch.setattr(main_mod, "process_media_event", slow_dispatch)
    transport = httpx.ASGITransport(app=main_mod.app)
    headers = {"x-webhook-secret": "review-race-secret"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first, second = await asyncio.gather(
                client.post(f"/api/automation/review/{held['event_id']}", headers=headers, json={"decision": "approve"}),
                client.post(f"/api/automation/review/{held['event_id']}", headers=headers, json={"decision": "approve"}),
            )
        assert dispatched.is_set()
        assert sorted([first.status_code, second.status_code]) == [200, 404]
        assert dispatch_count == 1
    finally:
        main_mod.recent_events.clear()
        main_mod.recent_events.extend(original_events)
