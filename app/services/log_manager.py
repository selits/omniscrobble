import asyncio
import datetime
import logging
import re
import shutil
from collections import deque
from typing import Any, Optional

from app.config import Config

_QUERY_TOKEN_RE = re.compile(r"([?&]token=)[^&\s]+")
_WEBHOOK_HEADER_RE = re.compile(r"(x-webhook-secret:\s*)[^\s,;]+", flags=re.IGNORECASE)
_BEARER_TOKEN_RE = re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]+", flags=re.IGNORECASE)


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
            self.buffer.append({
                "timestamp": datetime.datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "name": record.name,
                "message": msg,
                "raw": msg,
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


class LogManager:
    """Manages application log capture, systemd journalctl integration, and secret redaction."""

    def __init__(self, capacity: int = 1000):
        self.handler = RingBufferLogHandler(capacity=capacity)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.handler.setFormatter(formatter)
        # Attach to the root logger to capture all application & framework logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self.handler)
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
                return {"source": "app_buffer", "lines": buffered}

        # Attempt journalctl read if requested or in auto mode
        if source in ("auto", "journalctl") and shutil.which("journalctl"):
            try:
                for unit in ("omniscrobble.service", "plex-trakt.service", "plex-trakt"):
                    proc = await asyncio.create_subprocess_exec(
                        "journalctl", "--user", "-u", unit, "-n", str(lines), "--no-pager",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
                    if proc.returncode == 0 and stdout:
                        raw_lines = stdout.decode("utf-8", errors="replace").splitlines()
                        if raw_lines:
                            return {"source": "journalctl", "lines": [self.sanitize_line(line) for line in raw_lines]}
            except Exception:
                pass

        # In-memory buffer fallback
        return {
            "source": "app_buffer",
            "lines": self.get_buffer_logs(lines=lines),
        }

    def get_buffer_logs(self, lines: int = 150) -> list[str]:
        """Return sanitized lines directly from the in-memory ring buffer."""
        lines = max(10, min(lines, 500))
        buffer_list = list(self.handler.buffer)[-lines:]
        return [self.sanitize_line(entry["raw"]) for entry in buffer_list]


log_mgr = LogManager()
