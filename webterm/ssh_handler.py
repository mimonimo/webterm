"""SSH connection handler using paramiko."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

import paramiko

from webterm.session_manager import SessionProfile


class SSHConnection:
    """Manages a single SSH connection with PTY."""

    def __init__(self, profile: SessionProfile):
        self.profile = profile
        self.client: paramiko.SSHClient | None = None
        self.channel: paramiko.Channel | None = None
        self._connected = False

    def connect(self) -> str:
        """Establish SSH connection. Returns welcome message or error."""
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.profile.host,
            "port": self.profile.port,
            "username": self.profile.username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if self.profile.auth_method == "key" and self.profile.key_path:
            try:
                pkey = paramiko.RSAKey.from_private_key_file(self.profile.key_path)
            except paramiko.ssh_exception.SSHException:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(self.profile.key_path)
                except paramiko.ssh_exception.SSHException:
                    pkey = paramiko.ECDSAKey.from_private_key_file(self.profile.key_path)
            connect_kwargs["pkey"] = pkey
        else:
            connect_kwargs["password"] = self.profile.password

        self.client.connect(**connect_kwargs)
        self.channel = self.client.invoke_shell(
            term="xterm-256color",
            width=120,
            height=40,
        )
        self.channel.settimeout(0.1)
        self._connected = True

        # Read initial output
        time.sleep(0.5)
        initial = b""
        try:
            while self.channel.recv_ready():
                initial += self.channel.recv(4096)
        except Exception:
            pass

        return initial.decode(errors="replace")

    def send(self, data: str):
        """Send data to the SSH channel."""
        if self.channel and self._connected:
            self.channel.send(data)

    def recv(self) -> str:
        """Receive data from the SSH channel (non-blocking)."""
        if not self.channel or not self._connected:
            return ""
        try:
            if self.channel.recv_ready():
                data = self.channel.recv(8192)
                return data.decode(errors="replace")
        except Exception:
            pass
        return ""

    def resize(self, cols: int, rows: int):
        """Resize the PTY."""
        if self.channel and self._connected:
            try:
                self.channel.resize_pty(width=cols, height=rows)
            except Exception:
                pass

    @property
    def is_active(self) -> bool:
        if not self._connected or not self.channel:
            return False
        return self.channel.get_transport() is not None and self.channel.get_transport().is_active()

    def close(self):
        """Close the SSH connection."""
        self._connected = False
        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    def list_remote_dir(self, path: str = ".") -> list[dict]:
        """List remote directory via SFTP."""
        if not self.client:
            return []
        try:
            sftp = self.client.open_sftp()
            entries = []
            for attr in sftp.listdir_attr(path):
                import stat
                is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                entries.append({
                    "name": attr.filename,
                    "size": attr.st_size or 0,
                    "is_dir": is_dir,
                    "modified": attr.st_mtime or 0,
                })
            sftp.close()
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
            return entries
        except Exception as e:
            return [{"error": str(e)}]

    def download_file(self, remote_path: str) -> bytes:
        """Download a file via SFTP."""
        if not self.client:
            raise ConnectionError("Not connected")
        sftp = self.client.open_sftp()
        import io
        buf = io.BytesIO()
        sftp.getfo(remote_path, buf)
        sftp.close()
        buf.seek(0)
        return buf.read()

    def upload_file(self, remote_path: str, data: bytes):
        """Upload a file via SFTP."""
        if not self.client:
            raise ConnectionError("Not connected")
        sftp = self.client.open_sftp()
        import io
        sftp.putfo(io.BytesIO(data), remote_path)
        sftp.close()
