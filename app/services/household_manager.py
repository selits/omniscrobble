import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

try:
    from app.config import Config
    from app.plex_parser import ParsedMedia
    from app.services.atomic_writer import atomic_write_json
    from app.services.cowatch_manager import CowatchManager
except ImportError:
    from config import Config
    from plex_parser import ParsedMedia
    from atomic_writer import atomic_write_json
    from cowatch_manager import CowatchManager

logger = logging.getLogger("household_manager")


class HouseholdManager(CowatchManager):
    """Manages multi-tenant household routing rules beyond 2-user co-watching.

    Maintains backward compatibility with traditional CO_WATCH_* settings
    while providing granular routing based on player/device, media type, and shows.
    """

    def __init__(self, config: type[Config] = Config):
        super().__init__(config=config)
        self.rules_file: Path = getattr(
            config,
            "HOUSEHOLD_RULES_DATA_FILE",
            getattr(config, "BASE_DIR", Path(".")) / "data" / "household_rules.json",
        )
        self._rules: list[dict[str, Any]] = []
        self._load_rules()

    def _load_rules(self) -> None:
        """Load household routing rules from persistent JSON file."""
        if not self.rules_file.exists():
            self._rules = []
            return

        try:
            with open(self.rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    cleaned_rules = []
                    for r in data:
                        if isinstance(r, dict):
                            rule_id = str(r.get("id") or f"rule_{uuid.uuid4().hex[:8]}")
                            name = str(r.get("name") or "Routing Rule").strip()
                            targets = [str(t).strip() for t in (r.get("targets") or []) if str(t).strip()]
                            devices = [str(d).strip() for d in (r.get("devices") or []) if str(d).strip()]
                            shows = [str(s).strip() for s in (r.get("shows") or []) if str(s).strip()]
                            media_types = [str(m).strip().lower() for m in (r.get("media_types") or []) if str(m).strip()]
                            enabled = bool(r.get("enabled", True))
                            cleaned_rules.append({
                                "id": rule_id,
                                "name": name,
                                "targets": targets,
                                "devices": devices,
                                "shows": shows,
                                "media_types": media_types,
                                "enabled": enabled,
                            })
                    self._rules = cleaned_rules
                else:
                    self._rules = []
        except Exception as e:
            logger.error(f"Error loading household rules from {self.rules_file}: {e}")
            self._rules = []

    def _save_rules(self) -> None:
        """Persist current household routing rules to disk."""
        try:
            atomic_write_json(self.rules_file, self._rules)
        except Exception as e:
            logger.error(f"Error saving household rules to {self.rules_file}: {e}")

    def get_rules(self) -> list[dict[str, Any]]:
        """Return the current list of household routing rules."""
        return [dict(r) for r in self._rules]

    def add_rule(
        self,
        name: str,
        targets: list[str],
        devices: Optional[list[str]] = None,
        shows: Optional[list[str]] = None,
        media_types: Optional[list[str]] = None,
        enabled: bool = True,
        rule_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a new household routing rule."""
        rid = rule_id or f"rule_{uuid.uuid4().hex[:8]}"
        clean_name = name.strip() or f"Rule {len(self._rules) + 1}"
        clean_targets = [t.strip() for t in targets if t and t.strip()]
        clean_devices = [d.strip() for d in (devices or []) if d and d.strip()]
        clean_shows = [s.strip() for s in (shows or []) if s and s.strip()]
        clean_types = [m.strip().lower() for m in (media_types or []) if m and m.strip()]

        rule = {
            "id": rid,
            "name": clean_name,
            "targets": clean_targets,
            "devices": clean_devices,
            "shows": clean_shows,
            "media_types": clean_types,
            "enabled": bool(enabled),
        }
        self._rules.append(rule)
        self._save_rules()
        logger.info(f"Added household routing rule '{clean_name}' (id: {rid}) targeting {clean_targets}")
        return dict(rule)

    def update_rule(self, rule_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update an existing household routing rule by ID."""
        for rule in self._rules:
            if rule["id"] == rule_id:
                if "name" in updates:
                    rule["name"] = str(updates["name"]).strip()
                if "targets" in updates and isinstance(updates["targets"], list):
                    rule["targets"] = [str(t).strip() for t in updates["targets"] if str(t).strip()]
                if "devices" in updates and isinstance(updates["devices"], list):
                    rule["devices"] = [str(d).strip() for d in updates["devices"] if str(d).strip()]
                if "shows" in updates and isinstance(updates["shows"], list):
                    rule["shows"] = [str(s).strip() for s in updates["shows"] if str(s).strip()]
                if "media_types" in updates and isinstance(updates["media_types"], list):
                    rule["media_types"] = [str(m).strip().lower() for m in updates["media_types"] if str(m).strip()]
                if "enabled" in updates:
                    rule["enabled"] = bool(updates["enabled"])
                self._save_rules()
                logger.info(f"Updated household routing rule '{rule['name']}' (id: {rule_id})")
                return dict(rule)
        return None

    def delete_rule(self, rule_id: str) -> bool:
        """Remove a household routing rule by ID."""
        initial_len = len(self._rules)
        self._rules = [r for r in self._rules if r["id"] != rule_id]
        if len(self._rules) < initial_len:
            self._save_rules()
            logger.info(f"Deleted household routing rule id: {rule_id}")
            return True
        return False

    def toggle_rule(self, rule_id: str) -> Optional[bool]:
        """Toggle active state of a household routing rule."""
        for rule in self._rules:
            if rule["id"] == rule_id:
                rule["enabled"] = not rule["enabled"]
                self._save_rules()
                logger.info(f"Toggled household routing rule '{rule['name']}' to enabled={rule['enabled']}")
                return rule["enabled"]
        return None

    def _is_matching_show_list(self, show_title: Optional[str], allowed_shows: list[str]) -> bool:
        """Check if show matches any item in the allowed list using normalization."""
        if not show_title or not allowed_shows:
            return False
        normalized_input = self._normalize(show_title)
        for s in allowed_shows:
            if s == "*" or self._normalize(s) == normalized_input:
                return True
        return False

    def resolve_targets(self, media: ParsedMedia) -> list[str]:
        """Resolve all eligible secondary usernames for dual-scrobbling/syncing.

        Combines traditional 2-user Co-Watch partner (if eligible) and any
        matching granular household routing rules. Deduplicates usernames and
        excludes the playing user.
        """
        targets: list[str] = []
        playing_user = (media.username or "").strip().lower()

        # 1. Traditional Co-Watch partner
        eligible, _ = self.check_cowatch_eligibility(media)
        if eligible and self.config.CO_WATCH_USER:
            partner = self.config.CO_WATCH_USER.strip()
            if partner and partner.lower() != playing_user:
                targets.append(partner)

        # 2. Granular Household Routing Rules
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue

            # Check devices/players filter
            allowed_devices = [d.strip().lower() for d in rule.get("devices", []) if d.strip()]
            if allowed_devices and "*" not in allowed_devices:
                media_player = (media.player or "").strip().lower()
                media_device = (media.device or "").strip().lower()
                if not any(d in (media_player, media_device) for d in allowed_devices):
                    continue

            # Check media types filter
            allowed_types = [m.strip().lower() for m in rule.get("media_types", []) if m.strip()]
            if allowed_types and "*" not in allowed_types:
                m_type = (media.media_type or "").strip().lower()
                if m_type not in allowed_types:
                    continue

            # Check shows filter
            allowed_shows = [s.strip() for s in rule.get("shows", []) if s.strip()]
            if allowed_shows and "*" not in allowed_shows:
                if media.media_type == "episode":
                    show_name = media.show_title or media.title or ""
                    if not self._is_matching_show_list(show_name, allowed_shows):
                        continue
                else:
                    # Specific shows filter requested, but item is not an episode
                    continue

            # Rule passed all criteria: collect target users
            for t in rule.get("targets", []):
                t_clean = t.strip()
                if not t_clean or t_clean.lower() == playing_user:
                    continue
                if not any(x.lower() == t_clean.lower() for x in targets):
                    targets.append(t_clean)

        return targets

    def get_status(self) -> dict[str, Any]:
        """Return the current co-watching and household routing status summary."""
        status = super().get_status()
        status["household_rules"] = self.get_rules()
        status["household_enabled"] = len(self.get_rules()) > 0 or bool(self.config.CO_WATCH_USER)
        return status


household_mgr = HouseholdManager()
