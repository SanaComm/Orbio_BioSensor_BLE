"""Local HTTP + WebSocket control plane for Phase 1 capture."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orbio.session import CaptureSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ScanRequest(BaseModel):
    timeout_s: float = Field(default=8.0, ge=1.0, le=600.0)


class ConnectRequest(BaseModel):
    address: str


class WatchRequest(BaseModel):
    name_contains: str = "Orbio"
    pass_s: float = Field(default=20.0, ge=5.0, le=120.0)


class CommandRequest(BaseModel):
    command: str


class AppState:
    def __init__(self) -> None:
        self.session: CaptureSession | None = None
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()


state = AppState()


async def broadcast(event: str, payload: dict[str, Any]) -> None:
    message = json.dumps({"event": event, "payload": payload})
    stale: list[WebSocket] = []
    for client in state.clients:
        try:
            await client.send_text(message)
        except Exception:
            stale.append(client)
    for client in stale:
        state.clients.discard(client)


def _session() -> CaptureSession:
    if state.session is None:
        raise RuntimeError("Capture session is not ready")
    return state.session


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    simulate = os.environ.get("ORBIO_SIMULATE", "").strip() in {"1", "true", "yes"}
    session = CaptureSession(simulate=simulate)
    session.add_handler(broadcast)
    state.session = session
    await session.start_watch()
    yield
    await session.stop_watch()
    if session.status.connected:
        await session.disconnect(resume_watch=False)
    state.session = None


app = FastAPI(title="Orbio BioSensor BLE", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return _session().public_status()


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scan")
async def scan(body: ScanRequest) -> dict[str, Any]:
    async with state.lock:
        try:
            devices = await _session().scan(body.timeout_s)
        except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
            raise _http_error(exc) from exc
    return {"devices": [asdict(device) for device in devices]}


@app.post("/api/watch")
async def watch(body: WatchRequest) -> dict[str, Any]:
    try:
        await _session().start_watch(
            name_contains=body.name_contains,
            pass_s=body.pass_s,
        )
    except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
        raise _http_error(exc) from exc
    return _session().public_status()


@app.post("/api/watch/stop")
async def watch_stop() -> dict[str, str]:
    await _session().stop_watch()
    return {"ok": "true"}


@app.post("/api/connect")
async def connect(body: ConnectRequest) -> dict[str, Any]:
    async with state.lock:
        try:
            await _session().connect(body.address)
        except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
            raise _http_error(exc) from exc
    return _session().public_status()


@app.post("/api/disconnect")
async def disconnect() -> dict[str, Any]:
    async with state.lock:
        try:
            await _session().disconnect()
        except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
            raise _http_error(exc) from exc
    return _session().public_status()


@app.post("/api/command")
async def command(body: CommandRequest) -> dict[str, str]:
    async with state.lock:
        try:
            await _session().send_command(body.command)
        except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
            raise _http_error(exc) from exc
    return {"ok": "true", "command": body.command}


@app.websocket("/ws")
async def websocket(socket: WebSocket) -> None:
    await socket.accept()
    state.clients.add(socket)
    await socket.send_text(json.dumps({"event": "status", "payload": _session().public_status()}))
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        state.clients.discard(socket)
