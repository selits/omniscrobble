import json
import pytest

from app.config import Config
from app.services.compatibility_manager import CompatibilityManager
from app.services.settings_manager import SettingsManager
from app.services.automation_rules import AutomationRuleError, AutomationRulesManager


def test_unknown_and_newer_schema_versions_are_distinguished():
    unknown = CompatibilityManager._classify_schema("settings", None)
    newer = CompatibilityManager._classify_schema("settings", 2)

    assert unknown["status"] == "unknown"
    assert newer["status"] == "unsupported"
    assert "not compatible" not in unknown["action"].lower()


def test_sonarr_rule_marks_documented_majors_supported_and_unknown_versions_unknown(tmp_path):
    manager = CompatibilityManager()
    manager.record_integration_check("sonarr", "connected", "4.0.9")
    report = manager.inspect(
        app_version="3.2.0", python_version=(3, 12), settings_version=1, settings_exists=True,
        watch_lists_path=tmp_path / "watch_lists.json", automation_path=tmp_path / "rules.json",
        accounts_path=tmp_path / "accounts.json", queue_path=tmp_path / "queue.db",
    )
    assert report["integrations"]["sonarr"]["compatibility"] == "supported"
    assert report["ruleset"]["integration_rules"]["sonarr"]["supported_major_versions"] == [3, 4]
    manager.record_integration_check("sonarr", "connected", "5.0.0")
    report = manager.inspect(
        app_version="3.2.0", python_version=(3, 12), settings_version=1, settings_exists=True,
        watch_lists_path=tmp_path / "watch_lists.json", automation_path=tmp_path / "rules.json",
        accounts_path=tmp_path / "accounts.json", queue_path=tmp_path / "queue.db",
    )
    assert report["integrations"]["sonarr"]["compatibility"] == "unknown"


def test_legacy_settings_are_kept_readable_and_versioned_on_save(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"servers": {"plex": True}}), encoding="utf-8")
    manager = SettingsManager(Config, settings_file=settings_file, data_dir=tmp_path)

    assert manager._loaded_schema_version == 0
    assert manager.is_server_enabled("plex") is True

    manager.update_notifications({"notify_on_scrobble": False})
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == SettingsManager.SCHEMA_VERSION
    assert saved["servers"]["plex"] is True
    assert manager._loaded_schema_version == SettingsManager.SCHEMA_VERSION


def test_newer_settings_schema_is_preserved_when_save_is_attempted(tmp_path):
    settings_file = tmp_path / "settings.json"
    original = {"schema_version": 9, "servers": {"plex": True}, "future_option": "keep"}
    settings_file.write_text(json.dumps(original), encoding="utf-8")
    manager = SettingsManager(Config, settings_file=settings_file, data_dir=tmp_path)

    try:
        manager.update_notifications({"notify_on_scrobble": False})
    except RuntimeError as error:
        assert "newer schema" in str(error)
    else:
        raise AssertionError("Newer settings schema should not be overwritten")

    assert json.loads(settings_file.read_text(encoding="utf-8")) == original


def test_compatibility_inspection_does_not_contact_integrations(tmp_path):
    manager = CompatibilityManager()
    report = manager.inspect(
        app_version="3.2.0", python_version=(3, 12), settings_version=1, settings_exists=True,
        watch_lists_path=tmp_path / "watch_lists.json", automation_path=tmp_path / "rules.json",
        accounts_path=tmp_path / "accounts.json", queue_path=tmp_path / "queue.db",
    )

    assert report["status"] == "compatible"
    assert report["integrations"] == {}
    assert "explicit System connection test" in report["integration_version_policy"]


def test_newer_automation_rules_are_not_overwritten(tmp_path):
    rules_file = tmp_path / "automation_rules.json"
    original = {"version": 8, "rules": []}
    rules_file.write_text(json.dumps(original), encoding="utf-8")
    manager = AutomationRulesManager(rules_file)

    with pytest.raises(AutomationRuleError, match="newer schema"):
        manager.replace_rules([])

    assert json.loads(rules_file.read_text(encoding="utf-8")) == original
