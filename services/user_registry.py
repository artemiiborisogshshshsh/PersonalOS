"""Minimal B2C user identity and Telegram chat ownership registry.

The registry deliberately stores only an opaque local user ID, Telegram chat
ID and timezone. OAuth credentials and schedule contents remain in their
respective user-scoped stores and are never copied here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import os
import re


_CHAT_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_USER_ID = re.compile(r"^user-[a-f0-9]{24}$")


@dataclass(frozen=True)
class UserAccount:
    id: str
    telegram_chat_id: str
    timezone: str = "Asia/Tomsk"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not _USER_ID.fullmatch(self.id):
            raise ValueError("Invalid user ID")
        if not _CHAT_ID.fullmatch(str(self.telegram_chat_id)):
            raise ValueError("Invalid Telegram chat ID")
        if not self.timezone:
            raise ValueError("Timezone is required")


class UserRegistryStore:
    """Atomically binds one private Telegram chat to one local B2C user."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def _user_id(chat_id: str) -> str:
        # Avoid using a raw Telegram identifier as a directory name or as an
        # externally displayed application identifier.
        return "user-" + sha256(chat_id.encode("ascii")).hexdigest()[:24]

    def get_or_create(self, telegram_chat_id: str, *, timezone_name: str = "Asia/Tomsk") -> UserAccount:
        chat_id = str(telegram_chat_id)
        if not _CHAT_ID.fullmatch(chat_id):
            raise ValueError("Invalid Telegram chat ID")
        state = self._load()
        for account in state:
            if account.telegram_chat_id == chat_id:
                return account
        account = UserAccount(
            id=self._user_id(chat_id), telegram_chat_id=chat_id,
            timezone=timezone_name, created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save([*state, account])
        return account

    def resolve(self, telegram_chat_id: str) -> UserAccount | None:
        chat_id = str(telegram_chat_id)
        return next((account for account in self._load()
                     if account.telegram_chat_id == chat_id), None)

    def accounts(self) -> tuple[UserAccount, ...]:
        """Return the registry inventory for trusted maintenance code only."""
        return tuple(self._load())

    def assert_owner(self, user_id: str, telegram_chat_id: str) -> UserAccount:
        account = self.resolve(telegram_chat_id)
        if account is None or account.id != user_id:
            raise PermissionError("Telegram chat does not own this user state")
        return account

    def _load(self) -> list[UserAccount]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != self.VERSION:
            raise ValueError("Unsupported user registry version")
        accounts = [UserAccount(**item) for item in payload.get("accounts", [])]
        if len({item.id for item in accounts}) != len(accounts):
            raise ValueError("Duplicate user ID in registry")
        if len({item.telegram_chat_id for item in accounts}) != len(accounts):
            raise ValueError("Duplicate Telegram chat ID in registry")
        return accounts

    def _save(self, accounts: list[UserAccount]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump({"version": self.VERSION, "accounts": [asdict(item) for item in accounts]},
                          output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class UserStatePaths:
    """Derive bounded per-user state paths without accepting path fragments."""

    def __init__(self, data_root: Path):
        self.data_root = data_root

    def directory(self, user_id: str) -> Path:
        if not _USER_ID.fullmatch(user_id):
            raise ValueError("Invalid user ID")
        root = self.data_root / "users"
        if root.is_symlink():
            raise ValueError("User data root must not be a symlink")
        target = root / user_id
        if target.exists() and target.is_symlink():
            raise ValueError("User data directory must not be a symlink")
        return target
