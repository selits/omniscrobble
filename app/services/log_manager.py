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


class LogManager:
    """Manages application log capture, systemd journalctl integration, and secret redaction."""

    def __init__(self, capacity: int = 1000):
        self.handler = RingBufferLogHandler(capacity=capacity)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.handler.setFormatter(formatter)
        # Attach to the root logger to capture all application & framework logs
        logging.getLogger().addHandler(self.handler)

    def sanitize_line(self, line: str) -> str:
        """Mask secrets, tokens, and authorization headers from a log line."""
        if not line:
            return ""
        # Mask query tokens: ?token=... or &token=...
        sanitized = _QUERY_TOKEN_RE.sub(r"\1●●●●●●●●", line)
        # Mask webhook secret headers: x-webhook-secret: ...
        sanitized = _WEBHOOK_HEADER_RE.sub(r"\1●●●●●●●●", sanitized)
        # Mask Bearer tokens: Bearer ...
        sanitized = _BEARER_TOKEN_RE.sub(r"\1●●●●●●●●", sanitized)
        # Mask configured secret if present and non-empty
        if Config.WEBHOOK_SECRET and Config.WEBHOOK_SECRET in sanitized:
            sanitized = sanitized.replace(Config.WEBHOOK_SECRET, "●●●●●●●●")
        return sanitized

    async def get_logs(self, lines: int = 150, source: str = "auto") -> dict[str, Any]:
        """Fetch sanitized logs from journalctl (if available) or fallback to in-memory buffer."""
        lines = max(10, min(lines, 500))

        # Attempt journalctl read if requested or in auto mode
        if source in ("auto", "journalctl") and shutil.which("journalctl"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "journalctl",
                    "--user",
                    "-u",
                    "plex-trakt",
                    "-n",
                    str(lines),
                    "--no-pager",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
                if proc.returncode == 0 and stdout:
                    raw_lines = stdout.decode("utf-8", errors="replace").splitlines()
                    if raw_lines:
                        sanitized = [self.sanitize_line(line) for line in raw_lines]
                        return {
                            "source": "journalctl",
                            "lines": sanitized,
                        }
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
