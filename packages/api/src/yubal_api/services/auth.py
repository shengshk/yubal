"""Built-in single-account auth (iSyn-style session cookie)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt

logger = logging.getLogger(__name__)

COOKIE_NAME = "yubal_session"
SESSION_TTL = timedelta(days=7)
REMEMBER_TTL = timedelta(days=30)
SETUP_WINDOW = timedelta(minutes=15)


@dataclass
class AuthStatus:
    enabled: bool
    authenticated: bool
    username: str = ""
    needs_setup: bool = False
    setup_locked: bool = False
    setup_expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "authenticated": self.authenticated,
            "username": self.username,
            "needsSetup": self.needs_setup,
            "setupLocked": self.setup_locked,
            "setupExpiresAt": self.setup_expires_at,
        }


class AuthManager:
    """Single-user login manager backed by config/auth.json."""

    def __init__(self, *, enabled: bool, auth_file: Path) -> None:
        self._enabled = enabled
        self._auth_file = auth_file
        self._lock = threading.RLock()
        self._username = ""
        self._password_hash = ""
        self._secret = b""
        self._setup_deadline: datetime | None = None

        if not enabled:
            return

        if not self._load():
            self._setup_deadline = datetime.now(UTC) + SETUP_WINDOW
            logger.warning(
                "Auth file missing; setup window open until %s",
                self._setup_deadline.isoformat(),
            )

    def _load(self) -> bool:
        if not self._auth_file.exists():
            return False
        try:
            data = json.loads(self._auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read auth file: %s", e)
            return False

        username = str(data.get("username", "")).strip()
        password_hash = _normalize_bcrypt(str(data.get("passwordHash", "")).strip())
        secret = str(data.get("sessionSecret", "")).strip()
        if not username or not password_hash or not secret:
            return False
        if not password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            logger.error("Auth password hash invalid")
            return False

        with self._lock:
            self._username = username
            self._password_hash = password_hash
            self._secret = secret.encode("utf-8")
            self._setup_deadline = None
        return True

    def _save(self, username: str, password_hash: str, secret: str) -> None:
        self._auth_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "username": username,
            "passwordHash": password_hash,
            "sessionSecret": secret,
        }
        tmp = self._auth_file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.chmod(0o600)
        tmp.replace(self._auth_file)
        try:
            self._auth_file.chmod(0o600)
        except OSError:
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    def needs_setup(self) -> bool:
        with self._lock:
            return self._enabled and not self._username

    def setup_locked(self) -> bool:
        with self._lock:
            if not self._enabled or self._username:
                return False
            if self._setup_deadline is None:
                return True
            return datetime.now(UTC) > self._setup_deadline

    def status(self, cookie_value: str | None) -> AuthStatus:
        if not self._enabled:
            return AuthStatus(enabled=False, authenticated=True)

        needs = self.needs_setup()
        locked = self.setup_locked()
        expires = ""
        with self._lock:
            if needs and self._setup_deadline is not None:
                expires = self._setup_deadline.isoformat()

        if needs:
            return AuthStatus(
                enabled=True,
                authenticated=False,
                needs_setup=not locked,
                setup_locked=locked,
                setup_expires_at=expires,
            )

        username = self._session_username(cookie_value) if self.valid_session(cookie_value) else ""
        return AuthStatus(
            enabled=True,
            authenticated=bool(username),
            username=username,
        )

    def setup(
        self, username: str, password: str, confirm_password: str
    ) -> tuple[bool, str, str | None]:
        """Returns (ok, error_message, session_cookie_or_none)."""
        if not self._enabled:
            return True, "", None
        if not self.needs_setup():
            return False, "auth already initialized", None
        if self.setup_locked():
            return False, "auth setup expired", None

        username = username.strip()
        if not username:
            return False, "username is required", None
        if len(username) > 80:
            return False, "username is too long", None
        if password == "":
            return False, "password is required", None
        if password != confirm_password:
            return False, "passwords do not match", None

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")

        with self._lock:
            if self._username:
                return False, "auth already initialized", None
            if self._setup_deadline is None or datetime.now(UTC) > self._setup_deadline:
                return False, "auth setup expired", None
            self._save(username, password_hash, secret)
            self._username = username
            self._password_hash = password_hash
            self._secret = secret.encode("utf-8")
            self._setup_deadline = None

        cookie = self.make_session_cookie(username, remember=False)
        return True, "", cookie

    def login(
        self, username: str, password: str, remember: bool
    ) -> tuple[bool, str, str | None]:
        if not self._enabled:
            return True, "", None
        if self.needs_setup():
            if self.setup_locked():
                return False, "auth setup expired", None
            return False, "auth setup required", None

        username = username.strip()
        with self._lock:
            stored_user = self._username
            stored_hash = self._password_hash

        if not username or not hmac.compare_digest(username, stored_user):
            return False, "invalid username or password", None
        if (
            bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
            is False
        ):
            return False, "invalid username or password", None

        cookie = self.make_session_cookie(stored_user, remember=remember)
        return True, "", cookie

    def valid_session(self, cookie_value: str | None) -> bool:
        return bool(self._session_username(cookie_value))

    def _session_username(self, cookie_value: str | None) -> str:
        if not self._enabled or not cookie_value:
            return ""
        with self._lock:
            secret = self._secret
            stored_user = self._username
        if not secret or not stored_user:
            return ""

        try:
            payload, signature = cookie_value.split(".", 1)
        except ValueError:
            return ""
        expected = _sign(secret, payload)
        if not hmac.compare_digest(signature, expected):
            return ""
        try:
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            user, exp_s = raw.decode("utf-8").split("|", 1)
            expires = int(exp_s)
        except (ValueError, UnicodeDecodeError):
            return ""
        if datetime.now(UTC).timestamp() > expires:
            return ""
        if not hmac.compare_digest(user, stored_user):
            return ""
        return user

    def make_session_cookie(self, username: str, *, remember: bool) -> str:
        ttl = REMEMBER_TTL if remember else SESSION_TTL
        expires = int((datetime.now(UTC) + ttl).timestamp())
        with self._lock:
            secret = self._secret
        raw = f"{username}|{expires}".encode()
        payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"{payload}.{_sign(secret, payload)}"

    def cookie_max_age(self, remember: bool) -> int:
        ttl = REMEMBER_TTL if remember else SESSION_TTL
        return int(ttl.total_seconds())


def _normalize_bcrypt(hash_str: str) -> str:
    if hash_str.startswith("$2y$"):
        return "$2a$" + hash_str[4:]
    return hash_str


def _sign(secret: bytes, payload: str) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
