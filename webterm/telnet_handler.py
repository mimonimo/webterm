"""Telnet connection handler."""

from __future__ import annotations

import socket
import time

from webterm.session_manager import SessionProfile


class TelnetConnection:
    """Manages a single Telnet connection."""

    def __init__(self, profile: SessionProfile):
        self.profile = profile
        self._sock: socket.socket | None = None
        self._connected = False

    def connect(self) -> str:
        """Establish Telnet connection."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(10)
        self._sock.connect((self.profile.host, self.profile.port))
        self._sock.setblocking(False)
        self._connected = True

        # Read initial banner/negotiation
        time.sleep(1)
        initial = self._read_raw()

        # Auto-send username/password if provided
        if self.profile.username:
            time.sleep(0.3)
            self._sock.send(self.profile.username.encode() + b"\r\n")
            time.sleep(0.5)
            initial += self._read_raw()

            if self.profile.password:
                time.sleep(0.3)
                self._sock.send(self.profile.password.encode() + b"\r\n")
                time.sleep(0.5)
                initial += self._read_raw()

        return self._clean_telnet(initial)

    def _read_raw(self) -> bytes:
        data = b""
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except (BlockingIOError, socket.error):
            pass
        return data

    def _clean_telnet(self, data: bytes) -> str:
        """Strip telnet negotiation bytes and decode."""
        cleaned = b""
        i = 0
        while i < len(data):
            if data[i] == 0xFF and i + 2 < len(data):
                cmd = data[i + 1]
                if cmd in (0xFB, 0xFC, 0xFD, 0xFE):
                    # WILL/WONT/DO/DONT — respond with WONT/DONT
                    opt = data[i + 2]
                    if cmd == 0xFD:  # DO -> WONT
                        try:
                            self._sock.send(bytes([0xFF, 0xFC, opt]))
                        except Exception:
                            pass
                    elif cmd == 0xFB:  # WILL -> DONT
                        try:
                            self._sock.send(bytes([0xFF, 0xFE, opt]))
                        except Exception:
                            pass
                    i += 3
                    continue
                elif cmd == 0xFF:
                    cleaned += b"\xFF"
                    i += 2
                    continue
                else:
                    i += 2
                    continue
            else:
                cleaned += bytes([data[i]])
            i += 1
        return cleaned.decode(errors="replace")

    def send(self, data: str):
        """Send data to the Telnet connection."""
        if self._sock and self._connected:
            try:
                self._sock.send(data.encode())
            except Exception:
                self._connected = False

    def recv(self) -> str:
        """Receive data (non-blocking)."""
        if not self._sock or not self._connected:
            return ""
        raw = self._read_raw()
        if raw:
            return self._clean_telnet(raw)
        return ""

    def resize(self, cols: int, rows: int):
        """Telnet doesn't support PTY resize — NAWS negotiation would be needed."""
        pass

    @property
    def is_active(self) -> bool:
        if not self._connected or not self._sock:
            return False
        try:
            self._sock.getpeername()
            return True
        except Exception:
            return False

    def close(self):
        """Close the Telnet connection."""
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
