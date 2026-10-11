"""Regression tests for application log selection and credential redaction."""

import logging
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from uvicorn.logging import AccessFormatter

from app.config import Config
from app.services import log_manager as log_manager_module
from app.services.log_manager import LogManager, RingBufferLogHandler, SecretRedactionFilter


def make_log_manager() -> LogManager:
    """Build a manager without attaching another handler to the process root logger."""
    manager = LogManager.__new__(LogManager)
    manager.handler = RingBufferLogHandler(capacity=20)
    manager.handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    return manager


def test_archive_pruning_waits_for_saved_limits_on_startup(tmp_path, monkeypatch):
    """Do not prune archives using environment defaults before saved settings load."""
    monkeypatch.setattr(Config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(Config, "LOG_ARCHIVE_MAX_BYTES", 1_048_576)
    monkeypatch.setattr(Config, "LOG_ARCHIVE_BACKUP_COUNT", 1)
    log_dir = tmp_path / "data" / "logs"
    log_dir.mkdir(parents=True)
    for index in range(1, 4):
        archive = log_dir / f"omniscrobble.log.{index}"
        archive.write_text(f"archive {index}", encoding="utf-8")
        os.utime(archive, (index, index))

    manager = LogManager()
    root = logging.getLogger()
    try:
        assert len(list(log_dir.glob("omniscrobble.log.*"))) == 3

        manager.reconfigure_archive(max_bytes=1_048_576, backup_count=2)

        archives = sorted(path.name for path in log_dir.glob("omniscrobble.log.*"))
        assert archives == ["omniscrobble.log.2", "omniscrobble.log.3"]
    finally:
        root.removeHandler(manager.handler)
        root.removeHandler(manager.archive_handler)
        manager.handler.close()
        manager.archive_handler.close()


@pytest.mark.asyncio
async def test_auto_log_source_prefers_in_process_application_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make_log_manager()
    manager.handler.emit(logging.LogRecord("sync", logging.INFO, __file__, 1, "Partner sync delivered", (), None))
    journal = AsyncMock()
    monkeypatch.setattr(log_manager_module.shutil, "which", lambda _name: "/usr/bin/journalctl")
    monkeypatch.setattr(log_manager_module.asyncio, "create_subprocess_exec", journal)

    result = await manager.get_logs(source="auto")

    assert result["source"] == "app_buffer"
    assert "Partner sync delivered" in result["lines"][0]
    journal.assert_not_awaited()


@pytest.mark.asyncio
async def test_journal_fallback_checks_current_systemd_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make_log_manager()
    process = MagicMock(returncode=0)
    payload = {"MESSAGE": "service log", "PRIORITY": "3", "SYSLOG_IDENTIFIER": "omniscrobble", "__REALTIME_TIMESTAMP": "1791680400000000"}
    process.communicate = AsyncMock(return_value=((json.dumps(payload) + "\n").encode(), b""))
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(log_manager_module.shutil, "which", lambda _name: "/usr/bin/journalctl")
    monkeypatch.setattr(log_manager_module.asyncio, "create_subprocess_exec", create_process)

    result = await manager.get_logs(source="journalctl")

    assert result["source"] == "journalctl"
    assert result["entries"][0]["level"] == "ERROR"
    assert result["entries"][0]["name"] == "omniscrobble"
    assert result["entries"][0]["message"] == "service log"
    assert result["lines"] == [result["entries"][0]["line"]]
    assert create_process.await_args.args[:4] == ("journalctl", "--user", "-u", "omniscrobble.service")
    assert "--output=json" in create_process.await_args.args


def test_uvicorn_access_log_filter_redacts_webhook_query_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "private-secret")
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        'request: %s', ("POST /webhook?token=private-secret HTTP/1.1",), None,
    )

    assert SecretRedactionFilter().filter(record)
    assert "private-secret" not in record.getMessage()
    assert "token=●●●●●●●●" in record.getMessage()


@pytest.mark.parametrize("path", ["/health", "/webhook?token=private-secret&source=plex"])
def test_redacted_access_record_remains_compatible_with_uvicorn(monkeypatch, path):
    monkeypatch.setattr(Config, "WEBHOOK_SECRET", "private-secret")
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "POST", path, "1.1", 200), None,
    )
    formatter = AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False,
    )
    assert SecretRedactionFilter().filter(record)
    rendered = formatter.format(record)
    assert "POST " in rendered and "200 OK" in rendered
    assert "private-secret" not in rendered
    assert "private-secret" not in record.getMessage()
    if "token=" in path:
        assert "token=●●●●●●●●&source=plex" in rendered
