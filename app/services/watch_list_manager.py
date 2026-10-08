"""Local, versioned watch-list persistence and interchange formats."""

from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.atomic_writer import atomic_write_json

logger = logging.getLogger("omniscrobble.watch_list_manager")


class WatchListError(ValueError):
    """Invalid watch-list operation or imported data."""


class WatchListManager:
    VERSION = 1
    MAX_LISTS = 100
    MAX_ITEMS = 5000
    MAX_TITLE = 300
    MEDIA_TYPES = {"movie", "tv", "anime"}

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self._document = self._load()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    def _load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"version": self.VERSION, "lists": []}
        try:
            value = json.loads(self.file_path.read_text(encoding="utf-8"))
            return self._validate_document(value)
        except (json.JSONDecodeError, WatchListError) as exc:
            backup_path = self.file_path.with_name(
                f"{self.file_path.name}.invalid-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            try:
                shutil.copy2(self.file_path, backup_path)
                logger.error("Invalid watch-list data was preserved at %s: %s", backup_path, exc)
            except OSError as backup_error:
                logger.error("Could not preserve invalid watch-list data at %s: %s", self.file_path, backup_error)
            return {"version": self.VERSION, "lists": []}

    def _validate_document(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("version") != self.VERSION or not isinstance(value.get("lists"), list):
            raise WatchListError("Unsupported watch-list document")
        lists = []
        item_count = 0
        for raw in value["lists"]:
            if not isinstance(raw, dict):
                raise WatchListError("Each list must be an object")
            name = self._clean_text(raw.get("name"), 100, "List name")
            raw_items = raw.get("items", [])
            if not isinstance(raw_items, list):
                raise WatchListError("List items must be an array")
            items = [self._clean_item(item) for item in raw_items]
            item_count += len(items)
            lists.append({"id": str(raw.get("id") or self._id()), "name": name,
                          "created_at": raw.get("created_at") or self._now(),
                          "updated_at": raw.get("updated_at") or self._now(), "items": items})
        if len(lists) > self.MAX_LISTS or item_count > self.MAX_ITEMS:
            raise WatchListError("Watch-list document exceeds supported limits")
        return {"version": self.VERSION, "lists": lists}

    @staticmethod
    def _clean_text(value: Any, limit: int, label: str) -> str:
        if not isinstance(value, str):
            raise WatchListError(f"{label} must be text")
        text = value.strip()
        if not text or len(text) > limit or any(unicodedata.category(char) == "Cc" for char in text):
            raise WatchListError(f"{label} must contain 1 to {limit} characters")
        return text

    def _clean_item(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise WatchListError("Each item must be an object")
        title = self._clean_text(raw.get("title"), self.MAX_TITLE, "Item title")
        media_type = str(raw.get("media_type") or "").lower()
        if media_type not in self.MEDIA_TYPES:
            raise WatchListError("Media type must be movie, tv, or anime")
        year = raw.get("year")
        if year is not None and (not isinstance(year, int) or year < 1800 or year > 2200):
            raise WatchListError("Year must be between 1800 and 2200")
        ids = raw.get("ids") or {}
        if not isinstance(ids, dict):
            raise WatchListError("Item IDs must be an object")
        clean_ids = {}
        for key in ("tmdb", "tvdb", "imdb", "anilist", "mal", "kitsu"):
            val = ids.get(key)
            if isinstance(val, (int, str)) and str(val).strip() and len(str(val)) <= 40:
                clean_ids[key] = val
        item = {"id": str(raw.get("id") or self._id()), "title": title,
                "media_type": media_type, "year": year, "ids": clean_ids,
                "added_at": raw.get("added_at") or self._now(),
                "position": int(raw.get("position", 0)) if str(raw.get("position", 0)).lstrip("-").isdigit() else 0}
        poster = raw.get("poster_url")
        if isinstance(poster, str) and poster.startswith(("https://", "http://")) and len(poster) <= 2000:
            item["poster_url"] = poster
        return item

    @staticmethod
    def _duplicate_key(item: dict[str, Any]) -> tuple[str, str, int | None]:
        return item["media_type"], item["title"].casefold(), item.get("year")

    def _save(self) -> None:
        self._document["version"] = self.VERSION
        atomic_write_json(self.file_path, self._document)

    def get_all(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._document))

    def _find(self, list_id: str) -> dict[str, Any]:
        for watch_list in self._document["lists"]:
            if watch_list["id"] == list_id:
                return watch_list
        raise WatchListError("Watch list not found")

    def create_list(self, name: str) -> dict[str, Any]:
        name = self._clean_text(name, 100, "List name")
        if len(self._document["lists"]) >= self.MAX_LISTS:
            raise WatchListError("Maximum number of watch lists reached")
        watch_list = {"id": self._id(), "name": name, "created_at": self._now(), "updated_at": self._now(), "items": []}
        self._document["lists"].append(watch_list)
        self._save()
        return watch_list

    def rename_list(self, list_id: str, name: str) -> dict[str, Any]:
        watch_list = self._find(list_id)
        watch_list["name"] = self._clean_text(name, 100, "List name")
        watch_list["updated_at"] = self._now()
        self._save()
        return watch_list

    def delete_list(self, list_id: str) -> None:
        self._find(list_id)
        self._document["lists"] = [item for item in self._document["lists"] if item["id"] != list_id]
        self._save()

    def add_item(self, list_id: str, item: dict[str, Any]) -> dict[str, Any]:
        watch_list = self._find(list_id)
        clean = self._clean_item(item)
        key = self._duplicate_key(clean)
        if any(self._duplicate_key(entry) == key for entry in watch_list["items"]):
            raise WatchListError("This item is already in the list")
        if sum(len(entry["items"]) for entry in self._document["lists"]) >= self.MAX_ITEMS:
            raise WatchListError("Maximum number of watch-list items reached")
        clean["position"] = len(watch_list["items"])
        watch_list["items"].append(clean)
        watch_list["updated_at"] = self._now()
        self._save()
        return clean

    def remove_item(self, list_id: str, item_id: str) -> None:
        watch_list = self._find(list_id)
        if not any(item["id"] == item_id for item in watch_list["items"]):
            raise WatchListError("Watch-list item not found")
        watch_list["items"] = [item for item in watch_list["items"] if item["id"] != item_id]
        for position, item in enumerate(watch_list["items"]):
            item["position"] = position
        watch_list["updated_at"] = self._now()
        self._save()

    def reorder(self, list_id: str, item_ids: list[str]) -> dict[str, Any]:
        watch_list = self._find(list_id)
        items = {item["id"]: item for item in watch_list["items"]}
        if len(item_ids) != len(items) or set(item_ids) != set(items):
            raise WatchListError("Order must include every item exactly once")
        watch_list["items"] = [items[item_id] for item_id in item_ids]
        for position, item in enumerate(watch_list["items"]):
            item["position"] = position
        watch_list["updated_at"] = self._now()
        self._save()
        return watch_list

    def export_text(self) -> str:
        lines = ["# Omniscrobble Watch Lists v1"]
        for watch_list in self._document["lists"]:
            lines.append(f"## {watch_list['name']}")
            for item in watch_list["items"]:
                year = str(item.get("year") or "")
                lines.append(f"[{item['media_type']}]\t{item['title']}\t{year}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def parse_text(self, text: str) -> dict[str, Any]:
        if len(text.encode("utf-8")) > 2_000_000:
            raise WatchListError("Import is too large (2 MB maximum)")
        lists: list[dict[str, Any]] = []
        current = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line == "# Omniscrobble Watch Lists v1" or line.startswith("# "):
                continue
            if line.startswith("## "):
                current = {"name": line[3:].strip(), "items": []}
                lists.append(current)
                continue
            if current is None:
                current = {"name": "Imported", "items": []}
                lists.append(current)
            match = re.match(r"^\[(movie|tv|anime)\]\s*(?:\t|\s{2,}|:)\s*(.+?)(?:\s*\t\s*(\d{4}))?$", line, re.I)
            if match:
                current["items"].append({"title": match.group(2).strip(), "media_type": match.group(1).lower(), "year": int(match.group(3)) if match.group(3) else None})
            else:
                current["items"].append({"title": line.removeprefix("- ").strip(), "media_type": "movie"})
        return self._validate_import({"version": self.VERSION, "lists": lists})

    def _validate_import(self, value: Any) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 2_000_000:
            raise WatchListError("Import is too large (2 MB maximum)")
        document = self._validate_document(value)
        for watch_list in document["lists"]:
            # Imported list and item IDs are intentionally regenerated on apply.
            watch_list["id"] = self._id()
            seen = set()
            unique = []
            duplicates = 0
            for item in watch_list["items"]:
                key = self._duplicate_key(item)
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
                item["id"] = self._id()
                unique.append(item)
            watch_list["items"] = unique
            watch_list["duplicates_skipped"] = duplicates
        return document

    def preview_import(self, value: Any, replace: bool = False) -> dict[str, Any]:
        if isinstance(value, str):
            if len(value.encode("utf-8")) > 2_000_000:
                raise WatchListError("Import is too large (2 MB maximum)")
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = self.parse_text(value)
        if not isinstance(value, dict):
            raise WatchListError("Import must be a JSON object or watch-list text")
        document = self._validate_import(value)
        return {"lists": document["lists"], "list_count": len(document["lists"]),
                "item_count": sum(len(item["items"]) for item in document["lists"]),
                "duplicates_skipped": sum(item.get("duplicates_skipped", 0) for item in document["lists"]),
                "replace": bool(replace)}

    def apply_import(self, value: Any, replace: bool = False) -> dict[str, Any]:
        preview = self.preview_import(value, replace)
        original_lists = json.loads(json.dumps(self._document["lists"]))
        incoming = preview["lists"]
        duplicate_count = preview["duplicates_skipped"]
        if replace:
            self._document["lists"] = incoming
        else:
            for watch_list in incoming:
                existing = next((item for item in self._document["lists"] if item["name"].casefold() == watch_list["name"].casefold()), None)
                if existing:
                    seen = {self._duplicate_key(item) for item in existing["items"]}
                    for item in watch_list["items"]:
                        key = self._duplicate_key(item)
                        if key in seen:
                            duplicate_count += 1
                            continue
                        item["position"] = len(existing["items"])
                        existing["items"].append(item)
                        seen.add(key)
                    existing["updated_at"] = self._now()
                else:
                    self._document["lists"].append(watch_list)
        if len(self._document["lists"]) > self.MAX_LISTS or sum(len(item["items"]) for item in self._document["lists"]) > self.MAX_ITEMS:
            self._document["lists"] = original_lists
            raise WatchListError("Imported data would exceed supported watch-list limits")
        self._save()
        return {"status": "ok", "lists": self.get_all()["lists"], "duplicates_skipped": duplicate_count}
