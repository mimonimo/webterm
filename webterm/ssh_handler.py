"""SSH connection handler using paramiko — supports Jump Host (ProxyJump)."""

from __future__ import annotations

import io
import stat
import time

import paramiko

from webterm.session_manager import SessionProfile


def _load_pkey(key_path: str) -> paramiko.PKey:
    """Try loading a private key file with multiple key types."""
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(key_path)
        except (paramiko.ssh_exception.SSHException, ValueError):
            continue
    raise paramiko.ssh_exception.SSHException(f"Cannot load key: {key_path}")


def _build_connect_kwargs(
    host: str,
    port: int,
    username: str,
    auth_method: str,
    password: str,
    key_path: str,
    sock=None,
) -> dict:
    """Build paramiko connect kwargs."""
    kwargs = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": 10,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if sock is not None:
        kwargs["sock"] = sock
    if auth_method == "key" and key_path:
        kwargs["pkey"] = _load_pkey(key_path)
    else:
        kwargs["password"] = password
    return kwargs


class SSHConnection:
    """Manages a single SSH connection with PTY, optionally via a Jump Host."""

    def __init__(self, profile: SessionProfile):
        self.profile = profile
        self.client: paramiko.SSHClient | None = None
        self.channel: paramiko.Channel | None = None
        self._connected = False
        # Jump host resources
        self._jump_client: paramiko.SSHClient | None = None
        self._jump_channel: paramiko.Channel | None = None

    def connect(self) -> str:
        """Establish SSH connection (with optional jump host). Returns welcome text."""
        sock = None

        # ── Step 1: Connect to Jump Host if configured ──
        if self.profile.has_jump_host:
            self._jump_client = paramiko.SSHClient()
            self._jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            jump_kwargs = _build_connect_kwargs(
                host=self.profile.jump_host,
                port=self.profile.jump_port,
                username=self.profile.jump_username or self.profile.username,
                auth_method=self.profile.jump_auth_method,
                password=self.profile.jump_password,
                key_path=self.profile.jump_key_path,
            )
            self._jump_client.connect(**jump_kwargs)

            # Open a tunnel from jump host to the target
            transport = self._jump_client.get_transport()
            dest = (self.profile.host, self.profile.port)
            local = ("127.0.0.1", 0)
            self._jump_channel = transport.open_channel("direct-tcpip", dest, local)
            sock = self._jump_channel

        # ── Step 2: Connect to Target (directly or via tunnel) ──
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        target_kwargs = _build_connect_kwargs(
            host=self.profile.host,
            port=self.profile.port,
            username=self.profile.username,
            auth_method=self.profile.auth_method,
            password=self.profile.password,
            key_path=self.profile.key_path,
            sock=sock,
        )
        self.client.connect(**target_kwargs)

        # ── Step 3: Open interactive shell ──
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
        t = self.channel.get_transport()
        return t is not None and t.is_active()

    @property
    def hop_info(self) -> str:
        """Return a human-readable connection path."""
        if self.profile.has_jump_host:
            jump = f"{self.profile.jump_username or self.profile.username}@{self.profile.jump_host}:{self.profile.jump_port}"
            target = f"{self.profile.username}@{self.profile.host}:{self.profile.port}"
            return f"{jump} → {target}"
        return f"{self.profile.username}@{self.profile.host}:{self.profile.port}"

    def close(self):
        """Close all connections (target + jump host)."""
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
        # Close jump host resources
        if self._jump_channel:
            try:
                self._jump_channel.close()
            except Exception:
                pass
        if self._jump_client:
            try:
                self._jump_client.close()
            except Exception:
                pass

    def list_remote_dir(self, path: str = ".") -> list[dict]:
        """List remote directory via SFTP (on the target, not jump host)."""
        if not self.client:
            return []
        try:
            sftp = self.client.open_sftp()
            entries = []
            for attr in sftp.listdir_attr(path):
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
        sftp.putfo(io.BytesIO(data), remote_path)
        sftp.close()
