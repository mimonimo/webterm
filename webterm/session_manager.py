"""Session manager — save/load/manage connection profiles."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

SESSIONS_FILE = Path("sessions.json")


@dataclass
class SessionProfile:
    id: str = ""
    name: str = ""
    protocol: Literal["ssh", "telnet"] = "ssh"
    host: str = ""
    port: int = 22
    username: str = ""
    auth_method: Literal["password", "key"] = "password"
    # password is never saved to disk — only used in-memory
    password: str = ""
    key_path: str = ""
    group: str = "Default"
    color: str = "#58a6ff"
    favorite: bool = False
    sort_order: int = 0
    # Jump host (ProxyJump) — connect through this host to reach the target
    jump_host: str = ""
    jump_port: int = 22
    jump_username: str = ""
    jump_auth_method: Literal["password", "key"] = "password"
    jump_password: str = ""      # never saved to disk
    jump_key_path: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if self.protocol == "telnet" and self.port == 22:
            self.port = 23

    @property
    def has_jump_host(self) -> bool:
        return bool(self.jump_host)

    def to_safe_dict(self) -> dict:
        """Return dict without password for serialization."""
        d = asdict(self)
        d.pop("password", None)
        d.pop("jump_password", None)
        return d


class SessionStore:
    """Persistent session storage backed by JSON file."""

    def __init__(self, path: Path = SESSIONS_FILE):
        self._path = path
        self._sessions: dict[str, SessionProfile] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for item in data:
                    s = SessionProfile(**{k: v for k, v in item.items() if k != "password"})
                    self._sessions[s.id] = s
            except (json.JSONDecodeError, TypeError):
                pass

    def _save(self):
        data = [s.to_safe_dict() for s in self._sessions.values()]
        self._path.write_text(json.dumps(data, indent=2))

    def list_all(self) -> list[dict]:
        return [s.to_safe_dict() for s in self._sessions.values()]

    def get(self, session_id: str) -> SessionProfile | None:
        return self._sessions.get(session_id)

    def add(self, profile: SessionProfile) -> SessionProfile:
        self._sessions[profile.id] = profile
        self._save()
        return profile

    def update(self, session_id: str, **kwargs) -> SessionProfile | None:
        s = self._sessions.get(session_id)
        if not s:
            return None
        for k, v in kwargs.items():
            if hasattr(s, k) and k not in ("id", "password"):
                setattr(s, k, v)
        self._save()
        return s

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._save()
            return True
        return False

    def get_groups(self) -> list[str]:
        groups = set(s.group for s in self._sessions.values())
        return sorted(groups)
