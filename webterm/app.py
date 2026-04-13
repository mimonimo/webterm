"""WebTerm — FastAPI application with WebSocket terminal bridge."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from webterm import __version__
from webterm.session_manager import SessionStore, SessionProfile, _safe_profile
from webterm.ssh_handler import SSHConnection
from webterm.telnet_handler import TelnetConnection

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(title="WebTerm", version=__version__)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

store = SessionStore()

# Active connections: ws_id -> connection object
active_connections: dict[str, SSHConnection | TelnetConnection] = {}


# ─── Pages ───

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": __version__},
    )


# ─── Session API ───

@app.get("/api/sessions")
async def list_sessions():
    return JSONResponse(store.list_all())


@app.post("/api/sessions")
async def create_session(request: Request):
    try:
        data = await request.json()
        profile = _safe_profile(data)
        store.add(profile)
        return JSONResponse(profile.to_safe_dict())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, request: Request):
    data = await request.json()
    s = store.update(session_id, **data)
    if s:
        return JSONResponse(s.to_safe_dict())
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    if store.delete(session_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.patch("/api/sessions/{session_id}/favorite")
async def toggle_favorite(session_id: str):
    s = store.get(session_id)
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    store.update(session_id, favorite=not s.favorite)
    return JSONResponse({"ok": True, "favorite": not s.favorite})


@app.get("/api/sessions/group/{group_name}")
async def list_group_sessions(group_name: str):
    all_sessions = store.list_all()
    group_sessions = [s for s in all_sessions if s.get("group", "Default") == group_name]
    return JSONResponse(group_sessions)


@app.get("/api/sessions/favorites")
async def list_favorites():
    all_sessions = store.list_all()
    favs = [s for s in all_sessions if s.get("favorite")]
    return JSONResponse(favs)


# ─── SFTP API ───

@app.get("/api/sftp/{ws_id}/ls")
async def sftp_list(ws_id: str, path: str = "."):
    conn = active_connections.get(ws_id)
    if not conn or not isinstance(conn, SSHConnection):
        return JSONResponse({"error": "No active SSH connection"}, status_code=400)
    entries = conn.list_remote_dir(path)
    return JSONResponse(entries)


@app.get("/api/sftp/{ws_id}/download")
async def sftp_download(ws_id: str, path: str):
    conn = active_connections.get(ws_id)
    if not conn or not isinstance(conn, SSHConnection):
        return JSONResponse({"error": "No active SSH connection"}, status_code=400)
    try:
        data = conn.download_file(path)
        filename = Path(path).name
        return FileResponse(
            path=None,
            content=data,
            filename=filename,
            media_type="application/octet-stream",
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── WebSocket Terminal ───

@app.websocket("/ws/terminal/{ws_id}")
async def terminal_ws(websocket: WebSocket, ws_id: str):
    await websocket.accept()

    conn = None
    try:
        # Wait for connection config
        init_data = await websocket.receive_text()
        config = json.loads(init_data)

        protocol = config.get("protocol", "ssh")
        profile = SessionProfile(
            host=config["host"],
            port=int(config.get("port", 22 if protocol == "ssh" else 23)),
            username=config.get("username", ""),
            password=config.get("password", ""),
            auth_method=config.get("auth_method", "password"),
            key_path=config.get("key_path", ""),
            protocol=protocol,
            jump_host=config.get("jump_host", ""),
            jump_port=int(config.get("jump_port", 22)),
            jump_username=config.get("jump_username", ""),
            jump_auth_method=config.get("jump_auth_method", "password"),
            jump_password=config.get("jump_password", ""),
            jump_key_path=config.get("jump_key_path", ""),
        )

        if profile.has_jump_host:
            jump_label = f"{profile.jump_username or profile.username}@{profile.jump_host}"
            target_label = f"{profile.username}@{profile.host}"
            await websocket.send_text(json.dumps({
                "type": "status",
                "message": f"Connecting via Jump Host: {jump_label} → {target_label} ..."
            }))
        else:
            await websocket.send_text(json.dumps({
                "type": "status",
                "message": f"Connecting to {profile.host}:{profile.port} via {protocol.upper()}..."
            }))

        # Establish connection in thread pool
        loop = asyncio.get_event_loop()

        if protocol == "ssh":
            conn = SSHConnection(profile)
        else:
            conn = TelnetConnection(profile)

        welcome = await loop.run_in_executor(None, conn.connect)
        active_connections[ws_id] = conn

        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": f"Connected to {profile.host}",
        }))

        if welcome:
            await websocket.send_text(json.dumps({
                "type": "output",
                "data": welcome,
            }))

        # Bidirectional data relay
        async def read_from_server():
            while conn.is_active:
                data = await loop.run_in_executor(None, conn.recv)
                if data:
                    await websocket.send_text(json.dumps({
                        "type": "output",
                        "data": data,
                    }))
                else:
                    await asyncio.sleep(0.02)

        reader_task = asyncio.create_task(read_from_server())

        try:
            while True:
                msg = await websocket.receive_text()
                parsed = json.loads(msg)

                if parsed.get("type") == "input":
                    await loop.run_in_executor(None, conn.send, parsed["data"])
                elif parsed.get("type") == "resize":
                    cols = parsed.get("cols", 120)
                    rows = parsed.get("rows", 40)
                    await loop.run_in_executor(None, conn.resize, cols, rows)

        except WebSocketDisconnect:
            pass
        finally:
            reader_task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(e),
            }))
        except Exception:
            pass
    finally:
        if conn:
            conn.close()
        active_connections.pop(ws_id, None)


def main():
    """Launch WebTerm server."""
    @click.command()
    @click.option("--host", "-h", default="0.0.0.0", help="Bind address")
    @click.option("--port", "-p", default=8765, help="Port number")
    @click.option("--reload", "-r", is_flag=True, help="Auto-reload on changes")
    @click.version_option(__version__, prog_name="webterm")
    def run(host: str, port: int, reload: bool):
        """WebTerm — Web-based SSH & Telnet client."""
        print(f"""
  ╔══════════════════════════════════════════════════╗
  ║                                                  ║
  ║   \\033[1;36mWebTerm\\033[0m v{__version__}                            ║
  ║   Web-based SSH & Telnet Client                  ║
  ║                                                  ║
  ║   \\033[1mOpen:\\033[0m  http://{host if host != '0.0.0.0' else 'localhost'}:{port}           ║
  ║                                                  ║
  ╚══════════════════════════════════════════════════╝
        """)
        uvicorn.run(
            "webterm.app:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )

    run()


if __name__ == "__main__":
    main()
