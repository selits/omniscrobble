"""Local, versioned watch-list persistence and interchange formats."""

from __future__ import annotations

from app.services.persistence_guard import persisted_mutation

import threading
import json
import logging
import re
import shutil
import unicodedata
import urllib.parse
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
        self._lock = threading.RLock()
        self.file_path = Path(file_path)
        self._future_schema_version: int | None = None
        self._document = self._load()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    def _load(self) -> dict[str, Any]:
        self._future_schema_version = None
        if not self.file_path.exists():
            return {"version": self.VERSION, "lists": []}
        try:
            value = json.loads(self.file_path.read_text(encoding="utf-8"))
            version = value.get("version", 0) if isinstance(value, dict) else None
            if type(version) is int and version > self.VERSION:
                self._future_schema_version = version
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
            owner = raw.get("owner")
            if owner is not None and (not isinstance(owner, str) or not owner.strip()):
                raise WatchListError("List owner must be a username")
            raw_members = raw.get("members", {})
            if not isinstance(raw_members, dict) or len(raw_members) > 100:
                raise WatchListError("List members must be an object with at most 100 entries")
            members: dict[str, str] = {}
            for username, role in raw_members.items():
                clean_username = str(username).strip().lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", clean_username) or role not in {"editor", "viewer"}:
                    raise WatchListError("List members must have valid usernames and editor or viewer access")
                members[clean_username] = role
            lists.append({"id": str(raw.get("id") or self._id()), "name": name,
                          "owner": owner.strip().lower() if owner else None, "members": members,
                          "auto_action": raw.get("auto_action") if raw.get("auto_action") in {"manual", "request", "acquire"} else "manual",
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
        raw_tags = raw.get("tags", [])
        if not isinstance(raw_tags, list) or len(raw_tags) > 20:
            raise WatchListError("Item tags must be an array with at most 20 entries")
        tags: list[str] = []
        seen_tags: set[str] = set()
        for raw_tag in raw_tags:
            tag = self._clean_text(raw_tag, 40, "Item tag")
            if tag.casefold() not in seen_tags:
                tags.append(tag)
                seen_tags.add(tag.casefold())
        if tags:
            item["tags"] = tags
        notes = raw.get("notes")
        if notes is not None:
            if not isinstance(notes, str):
                raise WatchListError("Item notes must be text")
            if notes.strip():
                item["notes"] = self._clean_text(notes, 1000, "Item notes")
        poster = raw.get("poster_url")
        if isinstance(poster, str) and poster.startswith(("https://", "http://")) and len(poster) <= 2000:
            item["poster_url"] = poster
        availability = raw.get("availability")
        allowed_statuses = {"available", "in_library", "requested", "partially_available", "missing", "unknown"}
        if isinstance(availability, dict) and availability.get("status") in allowed_statuses:
            checked_at = availability.get("checked_at")
            services = availability.get("services", {})
            if isinstance(checked_at, str) and isinstance(services, dict):
                item["availability"] = {
                    "status": availability["status"],
                    "checked_at": checked_at[:40],
                    "services": {str(key)[:32]: str(value)[:32] for key, value in services.items()},
                }
        automation = raw.get("automation")
        if isinstance(automation, dict) and automation.get("status") in {"acquired", "requested", "skipped", "failed"}:
            checked_at = automation.get("checked_at")
            reason = automation.get("reason")
            if isinstance(checked_at, str) and isinstance(reason, str):
                item["automation"] = {"status": automation["status"], "checked_at": checked_at[:40], "reason": reason[:200]}
        return item

    @staticmethod
    def _duplicate_key(item: dict[str, Any]) -> tuple[str, str, int | None]:
        return item["media_type"], item["title"].casefold(), item.get("year")

    def _save(self) -> None:
        if self._future_schema_version is not None:
            raise WatchListError("Watch lists use a newer schema; refusing to overwrite them with this version.")
        self._document["version"] = self.VERSION
        atomic_write_json(self.file_path, self._document)

    def get_all(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._document))

    def get_all_for(self, username: str, is_admin: bool = False) -> dict[str, Any]:
        """Return only visible lists and the caller's relevant permissions."""
        name = username.strip().lower()
        visible = []
        for watch_list in self._document["lists"]:
            is_owner = watch_list.get("owner") == name
            role = "owner" if is_owner else watch_list.get("members", {}).get(name)
            if not is_admin and not role:
                continue
            row = json.loads(json.dumps(watch_list))
            row["can_edit"] = bool(is_admin or role in {"owner", "editor"})
            row["can_manage"] = bool(is_admin or is_owner)
            row["access_role"] = "admin" if is_admin else role
            if not row["can_manage"]:
                row.pop("members", None)
            visible.append(row)
        return {"version": self.VERSION, "lists": visible}

    @persisted_mutation("_document", WatchListError)
    def set_members(self, list_id: str, members: dict[str, str]) -> dict[str, Any]:
        watch_list = self._find(list_id)
        if not isinstance(members, dict) or len(members) > 100:
            raise WatchListError("Choose up to 100 list members")
        clean: dict[str, str] = {}
        for username, role in members.items():
            name = str(username).strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", name) or role not in {"editor", "viewer"}:
                raise WatchListError("Each member must have a valid username and editor or viewer access")
            if name != watch_list.get("owner"):
                clean[name] = role
        watch_list["members"] = clean
        watch_list["updated_at"] = self._now()
        self._save()
        return json.loads(json.dumps(watch_list))

    @persisted_mutation("_document", WatchListError)
    def set_auto_action(self, list_id: str, action: str) -> dict[str, Any]:
        watch_list = self._find(list_id)
        if action not in {"manual", "request", "acquire"}:
            raise WatchListError("Automatic action must be manual, request, or acquire")
        watch_list["auto_action"] = action
        watch_list["updated_at"] = self._now()
        self._save()
        return json.loads(json.dumps(watch_list))

    @persisted_mutation("_document", WatchListError)
    def set_item_automation(self, list_id: str, item_id: str, result: dict[str, str]) -> dict[str, Any]:
        watch_list = self._find(list_id)
        item = next((entry for entry in watch_list["items"] if entry["id"] == item_id), None)
        if item is None:
            raise WatchListError("Watch-list item not found")
        status, checked_at, reason = result.get("status"), result.get("checked_at"), result.get("reason")
        if status not in {"acquired", "requested", "skipped", "failed"} or not isinstance(checked_at, str) or not isinstance(reason, str):
            raise WatchListError("Invalid automatic action result")
        item["automation"] = {"status": status, "checked_at": checked_at[:40], "reason": reason[:200]}
        watch_list["updated_at"] = self._now()
        self._save()
        return json.loads(json.dumps(item))

    @persisted_mutation("_document", WatchListError)
    def set_availability(self, list_id: str, availability: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Persist sanitized availability snapshots for items in one list."""
        watch_list = self._find(list_id)
        allowed = {"available", "in_library", "requested", "partially_available", "missing", "unknown"}
        items = {item["id"]: item for item in watch_list["items"]}
        for item_id, snapshot in availability.items():
            item = items.get(item_id)
            if not item or not isinstance(snapshot, dict):
                continue
            status = snapshot.get("status")
            checked_at = snapshot.get("checked_at")
            services = snapshot.get("services", {})
            if status not in allowed or not isinstance(checked_at, str) or not isinstance(services, dict):
                continue
            item["availability"] = {
                "status": status,
                "checked_at": checked_at[:40],
                "services": {str(key)[:32]: str(value)[:32] for key, value in services.items()},
            }
        watch_list["updated_at"] = self._now()
        self._save()
        return json.loads(json.dumps(watch_list))

    def access_role(self, list_id: str, username: str, is_admin: bool = False) -> str | None:
        watch_list = self._find(list_id)
        name = username.strip().lower()
        if is_admin:
            return "admin"
        if watch_list.get("owner") == name:
            return "owner"
        return watch_list.get("members", {}).get(name)

    def _find(self, list_id: str) -> dict[str, Any]:
        for watch_list in self._document["lists"]:
            if watch_list["id"] == list_id:
                return watch_list
        raise WatchListError("Watch list not found")

    @persisted_mutation("_document", WatchListError)
    def create_list(self, name: str, owner: str = "admin") -> dict[str, Any]:
        name = self._clean_text(name, 100, "List name")
        if len(self._document["lists"]) >= self.MAX_LISTS:
            raise WatchListError("Maximum number of watch lists reached")
        watch_list = {"id": self._id(), "name": name, "owner": owner.strip().lower(), "members": {}, "auto_action": "manual",
                      "created_at": self._now(), "updated_at": self._now(), "items": []}
        self._document["lists"].append(watch_list)
        self._save()
        return watch_list

    @persisted_mutation("_document", WatchListError)
    def rename_list(self, list_id: str, name: str) -> dict[str, Any]:
        watch_list = self._find(list_id)
        watch_list["name"] = self._clean_text(name, 100, "List name")
        watch_list["updated_at"] = self._now()
        self._save()
        return watch_list

    @persisted_mutation("_document", WatchListError)
    def delete_list(self, list_id: str) -> None:
        self._find(list_id)
        self._document["lists"] = [item for item in self._document["lists"] if item["id"] != list_id]
        self._save()

    @persisted_mutation("_document", WatchListError)
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

    @persisted_mutation("_document", WatchListError)
    def update_item(self, list_id: str, item_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        watch_list = self._find(list_id)
        item = next((entry for entry in watch_list["items"] if entry["id"] == item_id), None)
        if item is None:
            raise WatchListError("Watch-list item not found")
        updated = self._clean_item({**item, **updates, "id": item_id,
                                    "added_at": item["added_at"], "position": item["position"]})
        if any(entry["id"] != item_id and self._duplicate_key(entry) == self._duplicate_key(updated)
               for entry in watch_list["items"]):
            raise WatchListError("This item is already in the list")
        updated["id"] = item_id
        watch_list["items"][watch_list["items"].index(item)] = updated
        watch_list["updated_at"] = self._now()
        self._save()
        return json.loads(json.dumps(updated))

    @persisted_mutation("_document", WatchListError)
    def remove_item(self, list_id: str, item_id: str) -> None:
        watch_list = self._find(list_id)
        if not any(item["id"] == item_id for item in watch_list["items"]):
            raise WatchListError("Watch-list item not found")
        watch_list["items"] = [item for item in watch_list["items"] if item["id"] != item_id]
        for position, item in enumerate(watch_list["items"]):
            item["position"] = position
        watch_list["updated_at"] = self._now()
        self._save()

    @persisted_mutation("_document", WatchListError)
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
                metadata = {key: item[key] for key in ("tags", "notes") if key in item}
                suffix = "\t" + urllib.parse.quote(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), safe="") if metadata else ""
                lines.append(f"[{item['media_type']}]\t{item['title']}\t{year}{suffix}")
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
            columns = line.split("\t")
            column_match = re.fullmatch(r"\[(movie|tv|anime)\]", columns[0].strip(), re.I) if len(columns) >= 3 else None
            if column_match:
                item = {"title": columns[1].strip(), "media_type": column_match.group(1).lower(),
                        "year": int(columns[2]) if re.fullmatch(r"\d{4}", columns[2].strip()) else None}
                if len(columns) >= 4 and columns[3].strip():
                    try:
                        metadata = json.loads(urllib.parse.unquote(columns[3]))
                    except (json.JSONDecodeError, ValueError):
                        metadata = {}
                    if isinstance(metadata, dict):
                        item.update({key: metadata[key] for key in ("tags", "notes") if key in metadata})
                current["items"].append(item)
                continue
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
            # Imports never grant or transfer access to local dashboard accounts.
            watch_list["owner"] = None
            watch_list["members"] = {}
            watch_list["auto_action"] = "manual"
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

    @persisted_mutation("_document", WatchListError)
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
