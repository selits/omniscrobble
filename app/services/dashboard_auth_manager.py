"""Local dashboard identities, password verification, sessions, and role policy."""

from __future__ import annotations

from app.services.persistence_guard import persisted_mutation

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.services.atomic_writer import atomic_write_json

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,31}$")
_PBKDF2_ITERATIONS = 310_000
_SESSION_LIFETIME_SECONDS = 12 * 60 * 60

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "member": frozenset({"view_activity", "view_own_analytics", "manage_personal_trackers", "view_shared_watch_lists", "manage_shared_watch_lists"}),
}


@dataclass(frozen=True)
class DashboardPrincipal:
    username: str
    role: str
    source: str


@dataclass(frozen=True)
class DashboardSession:
    principal: DashboardPrincipal
    session_token: str
    csrf_token: str


class DashboardAuthManager:
    """Persist account password hashes and hold revocable browser sessions in memory."""

    def __init__(self, accounts_file: Optional[Path] = None, session_lifetime_seconds: int = _SESSION_LIFETIME_SECONDS):
        self.accounts_file = Path(accounts_file or (Path(__file__).resolve().parents[2] / "data" / "dashboard_accounts.json"))
        self.audit_file = self.accounts_file.with_name("dashboard_accounts_audit.json")
        self.session_lifetime_seconds = session_lifetime_seconds
        self._lock = threading.RLock()
        self._accounts: dict[str, dict[str, Any]] = {}
        self._future_schema_version: int | None = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._failed_logins: dict[str, list[float]] = {}
        self._load()

    @staticmethod
    def normalize_username(username: str) -> str:
        normalized = str(username or "").strip().lower()
        if not _USERNAME_RE.fullmatch(normalized):
            raise ValueError("Username must be 3–32 characters and use letters, numbers, dots, underscores, or hyphens.")
        return normalized

    @staticmethod
    def _password_record(password: str, salt: Optional[bytes] = None) -> str:
        password_bytes = str(password).encode("utf-8")
        salt_bytes = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt_bytes, _PBKDF2_ITERATIONS)
        return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt_bytes.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, password_record: str) -> bool:
        try:
            scheme, iterations, salt_hex, expected_hex = password_record.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
            return hmac.compare_digest(actual.hex(), expected_hex)
        except (ValueError, TypeError):
            return False

    def _load(self) -> None:
        self._future_schema_version = None
        try:
            payload = json.loads(self.accounts_file.read_text(encoding="utf-8"))
            version = payload.get("version", 0) if isinstance(payload, dict) else None
            if type(version) is int and version > 1:
                self._future_schema_version = version
            entries = payload.get("accounts", []) if isinstance(payload, dict) else []
            self._accounts = {
                str(item["username"]).lower(): item
                for item in entries
                if isinstance(item, dict)
                and item.get("role") in ROLE_PERMISSIONS
                and isinstance(item.get("username"), str)
                and isinstance(item.get("password_hash"), str)
            }
        except FileNotFoundError:
            self._accounts = {}
        except (OSError, ValueError, TypeError):
            self._accounts = {}

    def reload_accounts(self) -> None:
        """Load restored accounts and revoke all pre-restore browser sessions."""
        with self._lock:
            self._sessions.clear()
            self._failed_logins.clear()
            self._load()

    def _save(self) -> None:
        if self._future_schema_version is not None:
            raise RuntimeError("Dashboard accounts use a newer schema; refusing to overwrite them with this version.")
        atomic_write_json(self.accounts_file, {"version": 1, "accounts": list(self._accounts.values())})

    @persisted_mutation("_accounts", RuntimeError)
    def create_account(self, username: str, password: str, role: str = "member") -> dict[str, Any]:
        name = self.normalize_username(username)
        if role not in ROLE_PERMISSIONS:
            raise ValueError("Role must be admin or member.")
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        with self._lock:
            if name in self._accounts:
                raise ValueError("An account with that username already exists.")
            now = int(time.time())
            self._accounts[name] = {
                "username": name,
                "role": role,
                "password_hash": self._password_record(password),
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            self._save()
            return self._public_account(self._accounts[name])

    @staticmethod
    def _public_account(account: dict[str, Any]) -> dict[str, Any]:
        return {key: account[key] for key in ("username", "role", "enabled", "created_at", "updated_at") if key in account}

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_account(item) for item in sorted(self._accounts.values(), key=lambda item: item["username"])]

    def get_account(self, username: str) -> Optional[dict[str, Any]]:
        with self._lock:
            account = self._accounts.get(str(username or "").strip().lower())
            return self._public_account(account) if account else None

    def authenticate(self, username: str, password: str, client_key: str = "unknown") -> Optional[DashboardSession]:
        name = str(username or "").strip().lower()
        with self._lock:
            now = time.time()
            attempts = [stamp for stamp in self._failed_logins.get(client_key, []) if now - stamp < 60]
            if len(attempts) >= 10:
                self._failed_logins[client_key] = attempts
                return None
            account = self._accounts.get(name)
            if not account or not account.get("enabled") or not self._verify_password(password, account["password_hash"]):
                attempts.append(now)
                self._failed_logins[client_key] = attempts
                return None
            self._failed_logins.pop(client_key, None)
            session_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(24)
            self._sessions[hashlib.sha256(session_token.encode()).hexdigest()] = {
                "username": name,
                "csrf_hash": hashlib.sha256(csrf_token.encode()).hexdigest(),
                "expires_at": now + self.session_lifetime_seconds,
            }
            session = DashboardSession(DashboardPrincipal(name, account["role"], "local_account"), session_token, csrf_token)
            self.record_audit(name, "login", name)
            return session

    def resolve_session(self, session_token: str) -> Optional[DashboardPrincipal]:
        if not session_token:
            return None
        key = hashlib.sha256(session_token.encode()).hexdigest()
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None
            if time.time() >= session["expires_at"]:
                self._sessions.pop(key, None)
                return None
            account = self._accounts.get(session["username"])
            if not account or not account.get("enabled"):
                self._sessions.pop(key, None)
                return None
            return DashboardPrincipal(account["username"], account["role"], "local_account")

    def session_has_csrf(self, session_token: str, csrf_token: str) -> bool:
        if not session_token or not csrf_token:
            return False
        key = hashlib.sha256(session_token.encode()).hexdigest()
        supplied = hashlib.sha256(csrf_token.encode()).hexdigest()
        with self._lock:
            session = self._sessions.get(key)
            return bool(session and time.time() < session["expires_at"] and hmac.compare_digest(session["csrf_hash"], supplied))

    def revoke_session(self, session_token: str) -> None:
        if session_token:
            with self._lock:
                self._sessions.pop(hashlib.sha256(session_token.encode()).hexdigest(), None)

    @persisted_mutation("_accounts", RuntimeError)
    def update_account(self, username: str, *, role: Optional[str] = None, password: Optional[str] = None, enabled: Optional[bool] = None) -> Optional[dict[str, Any]]:
        name = str(username or "").strip().lower()
        with self._lock:
            account = self._accounts.get(name)
            if not account:
                return None
            if role is not None:
                if role not in ROLE_PERMISSIONS:
                    raise ValueError("Role must be admin or member.")
                account["role"] = role
            if password is not None:
                if len(password) < 12:
                    raise ValueError("Password must be at least 12 characters.")
                account["password_hash"] = self._password_record(password)
            if enabled is not None:
                account["enabled"] = bool(enabled)
            account["updated_at"] = int(time.time())
            self._save()
            if not account["enabled"] or password is not None or role is not None:
                self._revoke_user_sessions(name)
            return self._public_account(account)

    @persisted_mutation("_accounts", RuntimeError)
    def delete_account(self, username: str) -> bool:
        name = str(username or "").strip().lower()
        with self._lock:
            if name not in self._accounts:
                return False
            del self._accounts[name]
            self._save()
            self._revoke_user_sessions(name)
            return True

    def _revoke_user_sessions(self, username: str) -> None:
        for token_hash, session in list(self._sessions.items()):
            if session.get("username") == username:
                self._sessions.pop(token_hash, None)

    def record_audit(self, actor: str, action: str, subject: str) -> None:
        """Persist a bounded audit trail without passwords, tokens, or request payloads."""
        with self._lock:
            try:
                existing = json.loads(self.audit_file.read_text(encoding="utf-8"))
                entries = existing if isinstance(existing, list) else []
            except (FileNotFoundError, OSError, ValueError):
                entries = []
            entries.append({"timestamp": int(time.time()), "actor": actor, "action": action, "subject": subject})
            atomic_write_json(self.audit_file, entries[-500:])

    @staticmethod
    def has_permission(role: str, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(role, frozenset())
        return "*" in permissions or permission in permissions
