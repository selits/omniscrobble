import threading
import time
from typing import Any, Optional


class MetricsRegistry:
    """Thread-safe in-memory Prometheus metrics registry for plex-trakt-webhook."""

    def __init__(self):
        self._lock = threading.Lock()
        self.requests_total: dict[tuple[str, int], int] = {}
        self.scrobbles_total: dict[tuple[str, str], int] = {}
        self.ratings_total: dict[str, int] = {}
        self.collections_total: dict[tuple[str, str], int] = {}
        self.cowatch_total: dict[str, int] = {}

    def record_request(self, endpoint: str, status: int) -> None:
        key = (endpoint, status)
        with self._lock:
            self.requests_total[key] = self.requests_total.get(key, 0) + 1

    def record_scrobble(self, media_type: str, status: str = "success") -> None:
        key = (media_type or "unknown", status)
        with self._lock:
            self.scrobbles_total[key] = self.scrobbles_total.get(key, 0) + 1

    def record_rating(self, status: str = "success") -> None:
        with self._lock:
            self.ratings_total[status] = self.ratings_total.get(status, 0) + 1

    def record_collection(self, media_type: str, status: str = "success") -> None:
        key = (media_type or "unknown", status)
        with self._lock:
            self.collections_total[key] = self.collections_total.get(key, 0) + 1

    def record_cowatch(self, status: str = "success") -> None:
        with self._lock:
            self.cowatch_total[status] = self.cowatch_total.get(status, 0) + 1

    def generate_prometheus_text(
        self,
        uptime_seconds: float,
        queue_pending: int = 0,
        active_streams: int = 0,
    ) -> str:
        """Produce Prometheus exposition text (version 0.0.4)."""
        lines: list[str] = [
            "# HELP plex_trakt_uptime_seconds Total runtime of the plex-trakt-webhook service in seconds.",
            "# TYPE plex_trakt_uptime_seconds gauge",
            f"plex_trakt_uptime_seconds {uptime_seconds:.1f}",
            "",
            "# HELP plex_trakt_active_streams Current count of active Plex playback sessions.",
            "# TYPE plex_trakt_active_streams gauge",
            f"plex_trakt_active_streams {active_streams}",
            "",
            "# HELP plex_trakt_queue_pending Count of items awaiting retry in offline SQLite queue.",
            "# TYPE plex_trakt_queue_pending gauge",
            f"plex_trakt_queue_pending {queue_pending}",
            "",
        ]

        with self._lock:
            req_snapshot = dict(self.requests_total)
            scrobble_snapshot = dict(self.scrobbles_total)
            rating_snapshot = dict(self.ratings_total)
            col_snapshot = dict(self.collections_total)
            cowatch_snapshot = dict(self.cowatch_total)

        # Requests
        lines.append("# HELP plex_trakt_requests_total Total HTTP requests handled by endpoint.")
        lines.append("# TYPE plex_trakt_requests_total counter")
        if req_snapshot:
            for (ep, code), count in sorted(req_snapshot.items()):
                lines.append(f'plex_trakt_requests_total{{endpoint="{ep}",status="{code}"}} {count}')
        else:
            lines.append('plex_trakt_requests_total{endpoint="webhook",status="200"} 0')
        lines.append("")

        # Scrobbles
        lines.append("# HELP plex_trakt_scrobbles_total Total scrobble events sent to Trakt.")
        lines.append("# TYPE plex_trakt_scrobbles_total counter")
        if scrobble_snapshot:
            for (mtype, st), count in sorted(scrobble_snapshot.items()):
                lines.append(f'plex_trakt_scrobbles_total{{media_type="{mtype}",status="{st}"}} {count}')
        else:
            lines.append('plex_trakt_scrobbles_total{media_type="movie",status="success"} 0')
            lines.append('plex_trakt_scrobbles_total{media_type="episode",status="success"} 0')
        lines.append("")

        # Ratings
        lines.append("# HELP plex_trakt_ratings_total Total ratings synchronized to Trakt.")
        lines.append("# TYPE plex_trakt_ratings_total counter")
        if rating_snapshot:
            for st, count in sorted(rating_snapshot.items()):
                lines.append(f'plex_trakt_ratings_total{{status="{st}"}} {count}')
        else:
            lines.append('plex_trakt_ratings_total{status="success"} 0')
        lines.append("")

        # Collection
        lines.append("# HELP plex_trakt_collections_total Total media items synced to Trakt collection.")
        lines.append("# TYPE plex_trakt_collections_total counter")
        if col_snapshot:
            for (mtype, st), count in sorted(col_snapshot.items()):
                lines.append(f'plex_trakt_collections_total{{media_type="{mtype}",status="{st}"}} {count}')
        else:
            lines.append('plex_trakt_collections_total{media_type="movie",status="success"} 0')
            lines.append('plex_trakt_collections_total{media_type="episode",status="success"} 0')
        lines.append("")

        # Co-Watch
        lines.append("# HELP plex_trakt_cowatch_total Total co-watch dual-scrobbles executed.")
        lines.append("# TYPE plex_trakt_cowatch_total counter")
        if cowatch_snapshot:
            for st, count in sorted(cowatch_snapshot.items()):
                lines.append(f'plex_trakt_cowatch_total{{status="{st}"}} {count}')
        else:
            lines.append('plex_trakt_cowatch_total{status="success"} 0')
        lines.append("")

        return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()
