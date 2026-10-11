import asyncio
import datetime
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from app.config import Config

_QUERY_TOKEN_RE = re.compile(r"([?&]token=)[^&\s]+")
_WEBHOOK_HEADER_RE = re.compile(r"(x-webhook-secret:\s*)[^\s,;]+", flags=re.IGNORECASE)
_BEARER_TOKEN_RE = re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]+", flags=re.IGNORECASE)
_JOURNAL_LEVELS = {
    "0": "EMERGENCY", "1": "ALERT", "2": "CRITICAL", "3": "ERROR",
    "4": "WARNING", "5": "NOTICE", "6": "INFO", "7": "DEBUG",
}


def _sanitize_line(line: str) -> str:
    if not line:
        return ""
    sanitized = _QUERY_TOKEN_RE.sub(r"\1●●●●●●●●", line)
    sanitized = _WEBHOOK_HEADER_RE.sub(r"\1●●●●●●●●", sanitized)
    sanitized = _BEARER_TOKEN_RE.sub(r"\1●●●●●●●●", sanitized)
    if Config.WEBHOOK_SECRET and Config.WEBHOOK_SECRET in sanitized:
        sanitized = sanitized.replace(Config.WEBHOOK_SECRET, "●●●●●●●●")
    return sanitized


class RingBufferLogHandler(logging.Handler):
    """Logging handler that retains the latest N log records in an in-memory ring buffer."""

    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            timestamp = datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat()
            message = _sanitize_line(record.getMessage())
            self.buffer.append({
                "timestamp": timestamp,
                "level": record.levelname,
                "name": record.name,
                "message": message,
                "line": _sanitize_line(msg),
                "raw": _sanitize_line(msg),
            })
        except Exception:
            self.handleError(record)


class SecretRedactionFilter(logging.Filter):
    """Redact credentials before access records reach either journald or the UI."""

    def filter(self, record: logging.LogRecord) -> bool:
        # AccessFormatter unpacks these five values itself. Keep their types and
        # positions intact instead of replacing them with a formatted message.
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            record.args = tuple(
                _sanitize_line(value) if isinstance(value, str) else value
                for value in record.args
            )
            return True
        record.msg = _sanitize_line(record.getMessage())
        record.args = ()
        return True


class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating log output created with owner-only file permissions."""

    def _open(self):
        descriptor = os.open(
            self.baseFilename,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        return io.open(descriptor, mode=self.mode, encoding=self.encoding, errors=self.errors)


class LogManager:
    """Manages application log capture, systemd journalctl integration, and secret redaction."""

    def __init__(self, capacity: int = 1000):
        self.handler = RingBufferLogHandler(capacity=capacity)
        formatter = logging.Formatter("%(asctime)sZ [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        self.handler.setFormatter(formatter)
        log_directory = Config.BASE_DIR / "data" / "logs"
        log_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(log_directory, 0o700)
        except OSError:
            logging.getLogger(__name__).warning("Could not restrict permissions on log archive directory")
        archive_defaults = Config.normalize_log_archive_settings(
            Config.LOG_ARCHIVE_MAX_BYTES,
            Config.LOG_ARCHIVE_BACKUP_COUNT,
        )
        archive_handler = PrivateRotatingFileHandler(
            log_directory / "omniscrobble.log",
            maxBytes=archive_defaults["max_bytes"],
            backupCount=archive_defaults["backup_count"],
            encoding="utf-8",
        )
        archive_handler.setFormatter(formatter)
        archive_handler.addFilter(SecretRedactionFilter())
        self.archive_handler = archive_handler
        # Attach to the root logger to capture all application & framework logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self.handler)
        root_logger.addHandler(archive_handler)
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        logging.getLogger("uvicorn.access").addFilter(SecretRedactionFilter())

    def sanitize_line(self, line: str) -> str:
        """Mask secrets, tokens, and authorization headers from a log line."""
        return _sanitize_line(line)

    async def get_logs(self, lines: int = 150, source: str = "auto") -> dict[str, Any]:
        """Fetch sanitized logs from journalctl (if available) or fallback to in-memory buffer."""
        lines = max(10, min(lines, 500))

        # Auto mode should show this process's application records first. Journald may
        # contain only Uvicorn access lines, which hides the sync diagnostics we need.
        if source == "auto":
            buffered = self.get_buffer_logs(lines=lines)
            if buffered:
                return {"source": "app_buffer", "lines": buffered, "entries": self.get_buffer_entries(lines=lines)}

        # Attempt journalctl read if requested or in auto mode
        if source in ("auto", "journalctl") and shutil.which("journalctl"):
            try:
                for unit in ("omniscrobble.service", "plex-trakt.service", "plex-trakt"):
                    proc = await asyncio.create_subprocess_exec(
                        "journalctl", "--user", "-u", unit, "-n", str(lines), "--no-pager", "--utc", "--output=json",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
                    if proc.returncode == 0 and stdout:
                        raw_lines = stdout.decode("utf-8", errors="replace").splitlines()
                        if raw_lines:
                            entries = []
                            for raw_line in raw_lines:
                                try:
                                    record = json.loads(raw_line)
                                except (TypeError, ValueError):
                                    record = {"MESSAGE": raw_line}
                                raw_timestamp = str(record.get("__REALTIME_TIMESTAMP", ""))
                                try:
                                    timestamp = datetime.datetime.fromtimestamp(
                                        int(raw_timestamp) / 1_000_000, datetime.timezone.utc
                                    ).isoformat()
                                except (TypeError, ValueError, OverflowError, OSError):
                                    timestamp = ""
                                level = _JOURNAL_LEVELS.get(str(record.get("PRIORITY", "")), "UNKNOWN")
                                name = str(record.get("SYSLOG_IDENTIFIER") or record.get("_SYSTEMD_UNIT") or record.get("_COMM") or "")
                                message = self.sanitize_line(str(record.get("MESSAGE", "")))
                                prefix = f"{timestamp} " if timestamp else ""
                                rendered = self.sanitize_line(f"{prefix}[{level}] {name}: {message}")
                                entries.append({
                                    "timestamp": timestamp,
                                    "level": level,
                                    "name": self.sanitize_line(name),
                                    "message": message,
                                    "line": self.sanitize_line(rendered),
                                })
                            return {"source": "journalctl", "lines": [entry["line"] for entry in entries], "entries": entries}
            except Exception:
                pass

        # In-memory buffer fallback
        return {
            "source": "app_buffer",
            "lines": self.get_buffer_logs(lines=lines),
            "entries": self.get_buffer_entries(lines=lines),
        }

    def get_buffer_logs(self, lines: int = 150) -> list[str]:
        """Return sanitized lines directly from the in-memory ring buffer."""
        lines = max(10, min(lines, 500))
        buffer_list = list(self.handler.buffer)[-lines:]
        return [self.sanitize_line(entry["raw"]) for entry in buffer_list]

    def get_buffer_entries(self, lines: int = 150) -> list[dict[str, str]]:
        """Return structured, sanitized log entries from the in-process buffer."""
        lines = max(10, min(lines, 500))
        buffer_list = list(self.handler.buffer)[-lines:]
        return [
            {
                "timestamp": str(entry.get("timestamp", "")),
                "level": str(entry.get("level", "UNKNOWN")),
                "name": str(entry.get("name", "")),
                "message": self.sanitize_line(str(entry.get("message", ""))),
                "line": self.sanitize_line(str(entry.get("line", entry.get("raw", "")))),
            }
            for entry in buffer_list
        ]

    def get_archive_settings(self) -> dict[str, int]:
        handler = self.archive_handler
        max_bytes = int(handler.maxBytes)
        backup_count = int(handler.backupCount)
        return {
            "max_bytes": max_bytes,
            "backup_count": backup_count,
            "max_total_bytes": max_bytes * (backup_count + 1),
        }

    def _prune_archive_files(self) -> None:
        """Enforce both the configured rotation count and the aggregate disk cap."""
        handler = self.archive_handler
        directory = Path(os.path.dirname(handler.baseFilename))
        active_file = Path(handler.baseFilename)
        archives = sorted(
            (path for path in directory.glob("omniscrobble.log.*") if path.is_file() and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale_archive in archives[handler.backupCount:]:
            try:
                stale_archive.unlink()
            except OSError:
                logging.getLogger(__name__).warning("Could not remove an expired log archive")
        kept = archives[:handler.backupCount]
        try:
            total_bytes = active_file.stat().st_size if active_file.is_file() and not active_file.is_symlink() else 0
        except OSError:
            total_bytes = 0
        for archive in kept:
            try:
                total_bytes += archive.stat().st_size
            except OSError:
                continue
        max_total_bytes = handler.maxBytes * (handler.backupCount + 1)
        for stale_archive in reversed(kept):
            if total_bytes <= max_total_bytes:
                break
            try:
                size = stale_archive.stat().st_size
                stale_archive.unlink()
                total_bytes -= size
            except OSError:
                logging.getLogger(__name__).warning("Could not remove an oversized log archive")

    def reconfigure_archive(self, max_bytes: int, backup_count: int) -> dict[str, int]:
        """Apply validated archive limits and immediately prune excess rotations."""
        normalized = Config.normalize_log_archive_settings(max_bytes, backup_count)
        max_bytes = normalized["max_bytes"]
        backup_count = normalized["backup_count"]
        handler = self.archive_handler
        with handler.lock:
            handler.maxBytes = max_bytes
            handler.backupCount = backup_count
            self._prune_archive_files()
        return self.get_archive_settings()


log_mgr = LogManager()
