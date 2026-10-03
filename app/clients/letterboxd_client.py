"""Letterboxd Social Film Diary Client and Import CSV Generator for Omniscrobble.

Automatically logs completed movies (>= 90% or scrobble stop) and ratings into a
local Letterboxd Diary store, and generates official RFC-4180 Letterboxd Import CSVs
ready for 1-click import at https://letterboxd.com/import/.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from app.config import Config
except ImportError:
    from config import Config

logger = logging.getLogger("omniscrobble.letterboxd_client")


class LetterboxdClient:
    """Client for Letterboxd diary logging and CSV import generation."""

    def __init__(
        self,
        config: type[Config] = Config,
        username: Optional[str] = None,
        data_file: Optional[Path] = None,
        diary_file: Optional[Path] = None,
    ):
        self.config = config
        self.username = username or getattr(config, "LETTERBOXD_USERNAME", "") or ""
        base_dir = getattr(config, "DATA_DIR", getattr(config, "BASE_DIR", Path(".")) / "data")
        self.data_file = diary_file or data_file or (Path(base_dir) / "letterboxd_diary.json")
        self._entries: list[dict[str, Any]] = []
        self._load_entries()

    def _load_entries(self) -> None:
        if self.data_file and self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._entries = data if isinstance(data, list) else []
            except Exception as e:
                logger.error("Failed to load Letterboxd diary from %s: %s", self.data_file, e)
                self._entries = []
        else:
            self._entries = []

    def _save_entries(self) -> None:
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save Letterboxd diary to %s: %s", self.data_file, e)

    def is_enabled(self) -> bool:
        return True

    def is_configured(self) -> bool:
        return True  # Diary logging works out of the box with or without username

    async def check_connection(self) -> dict[str, Any]:
        """Return Letterboxd diary status and export metrics."""
        count = len(self._entries)
        uname = self.username or "local_diary"
        return {
            "name": "Letterboxd",
            "configured": True,
            "authenticated": bool(self.username),
            "enabled": True,
            "status": "connected",
            "username": uname,
            "user": uname,
            "diary_count": count,
            "message": f"Diary ready ({count} films logged for @{uname})",
        }

    async def log_movie_entry(
        self,
        title: str,
        year: Optional[int] = None,
        rating: Optional[float | int] = None,
        watched_date: Optional[str] = None,
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int | str] = None,
        tags: Optional[list[str] | str] = None,
        review: Optional[str] = None,
        rewatch: bool = False,
    ) -> dict[str, Any]:
        """Record a completed film in the Letterboxd Diary."""
        if not title:
            return {"status": "ignored", "reason": "Missing title"}

        date_str = watched_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Letterboxd ratings are 0.5 to 5.0 (mapped from 1-10)
        lb_rating = None
        if rating is not None and float(rating) > 0:
            lb_rating = round(float(rating) / 2.0, 1)

        entry = {
            "title": title,
            "year": year or "",
            "rating": rating if rating is not None else "",
            "stars": lb_rating if lb_rating is not None else "",
            "watched_date": date_str,
            "tags": tags if tags is not None else [],
            "review": review or "",
            "Title": title,
            "Year": year or "",
            "Rating": lb_rating if lb_rating is not None else "",
            "Rating10": rating if rating is not None else "",
            "WatchedDate": date_str,
            "Rewatch": "Yes" if rewatch else "No",
            "Tags": tags if tags is not None else [],
            "Review": review or "",
            "imdb_id": str(imdb_id) if imdb_id else "",
            "tmdb_id": str(tmdb_id) if tmdb_id else "",
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }

        # Prevent duplicate entries on the same watched date
        for idx, existing in enumerate(self._entries):
            if (
                existing.get("Title", "").lower() == title.lower()
                and str(existing.get("Year", "")) == str(year or "")
                and existing.get("WatchedDate") == date_str
            ):
                if lb_rating:
                    existing["Rating"] = lb_rating
                    existing["Rating10"] = rating
                    existing["rating"] = rating
                    existing["stars"] = lb_rating
                if tags is not None:
                    existing["Tags"] = tags
                    existing["tags"] = tags
                if review:
                    existing["Review"] = review
                    existing["review"] = review
                if rewatch:
                    existing["Rewatch"] = "Yes"
                self._save_entries()
                return {
                    "status": "updated",
                    "success": True,
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "stars": lb_rating,
                    "rating10": rating,
                    "tags": tags,
                    "entry": existing,
                }

        self._entries.append(entry)
        self._save_entries()
        logger.info("Logged film to Letterboxd Diary: %s (%s)", title, year)
        return {
            "status": "logged",
            "success": True,
            "title": title,
            "year": year,
            "rating": rating,
            "stars": lb_rating,
            "rating10": rating,
            "tags": tags,
            "entry": entry,
        }

    def generate_csv(self) -> str:
        """Generate official Letterboxd import CSV string conforming to Letterboxd specification.
        
        Header columns: Title,Year,WatchedDate,Rating10,Rating,Rewatch,Tags,Review,imdbID,tmdbID
        """
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["Title", "Year", "WatchedDate", "Rating10", "Rating", "Rewatch", "Tags", "Review", "imdbID", "tmdbID"])

        for entry in self._entries:
            tags = entry.get("Tags", entry.get("tags", ""))
            tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
            writer.writerow([
                entry.get("Title", ""),
                entry.get("Year", ""),
                entry.get("WatchedDate", ""),
                entry.get("Rating10", ""),
                entry.get("Rating", ""),
                entry.get("Rewatch", ""),
                tags_str,
                entry.get("Review", ""),
                entry.get("imdb_id", ""),
                entry.get("tmdb_id", ""),
            ])

        return output.getvalue()

    def generate_csv_export(self) -> str:
        """Alias for generate_csv()."""
        return self.generate_csv()

    def get_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._entries[:limit]

    def get_diary_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        """Alias for get_entries()."""
        return self.get_entries(limit=limit)

    def clear_entries(self) -> int:
        count = len(self._entries)
        self._entries = []
        self._save_entries()
        return count

    async def close(self) -> None:
        pass
