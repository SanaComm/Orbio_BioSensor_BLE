"""High-level capture session used by the web UI and CLI."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from orbio.assembler import SweepAssembler
from orbio.capture import SweepRecord, save_sweep
from orbio.protocol import is_orbio_advertised_name, parse_fw_id, parse_report_parameters
from orbio.radio import BleakBackend, DeviceInfo, RadioBackend, SimulatorBackend

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


@dataclass
class SessionStatus:
    backend: str
    scanning: bool = False
    connected: bool = False
    notifications: bool = False
    device: DeviceInfo | None = None
    fw_id: int | None = None
    parameters: dict[str, str] | None = None
    buffered_bytes: int = 0
    expected_bytes: int = 4992
    sweep_count: int = 0
    last_sweep: SweepRecord | None = None
    devices: list[DeviceInfo] = field(default_factory=list)
    watching: bool = False


class CaptureSession:
    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate
        self.backend: RadioBackend = SimulatorBackend() if simulate else BleakBackend()
        self.assembler = SweepAssembler()
        self.status = SessionStatus(backend="simulator" if simulate else "bleak")
        self._handlers: list[EventHandler] = []
        self._watch_task: asyncio.Task | None = None
        self._watch_stop = asyncio.Event()
        self._watch_pass_s = 20.0
        self._watch_name = "Orbio"
        self._sweep_index = 0
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def add_handler(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def remove_handler(self, handler: EventHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        if self.status.connected:
            raise RuntimeError("Disconnect before scanning")
        if self.status.watching:
            raise RuntimeError("A scan is already running")
        self.status.scanning = True
        await self._emit("status", self.public_status())
        try:
            devices = await self.backend.scan(timeout_s)
            self.status.devices = devices
            await self._emit("devices", {"devices": [asdict(device) for device in devices]})
            return devices
        finally:
            self.status.scanning = False
            await self._emit("status", self.public_status())

    def _cancel_backend_scan(self) -> None:
        cancel = getattr(self.backend, "cancel_scan", None)
        if callable(cancel):
            cancel()

    async def start_watch(self, name_contains: str = "Orbio", pass_s: float = 20.0) -> None:
        if self.status.connected:
            return
        if self._watch_task is not None and not self._watch_task.done():
            return
        needle = name_contains.strip() or "Orbio"
        self._watch_name = needle
        self._watch_pass_s = pass_s
        self._watch_stop = asyncio.Event()
        self._watch_task = asyncio.create_task(self._watch_loop(needle, pass_s))

    async def stop_watch(self) -> None:
        self._watch_stop.set()
        self._cancel_backend_scan()
        task = self._watch_task
        self._watch_task = None
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _watch_loop(self, name_contains: str, pass_s: float) -> None:
        needle = name_contains.lower()
        self.status.scanning = True
        self.status.watching = True
        await self._emit("status", self.public_status())
        await self._emit(
            "log",
            {
                "message": (
                    f"Looking for advertised names containing '{name_contains}'. "
                    "This keeps scanning until one appears; the remote only advertises "
                    f"just before a sweep. Each pass is {pass_s:.0f}s."
                )
            },
        )
        announced: set[str] = set()
        adapter_logged = False
        pass_no = 0
        try:
            while not self._watch_stop.is_set():
                pass_no += 1
                started = time.monotonic()
                try:
                    devices = await self.backend.scan(pass_s)
                except ConnectionError as exc:
                    if self._watch_stop.is_set():
                        break
                    await self._emit("log", {"message": f"Scan pass {pass_no} failed: {exc}. Retrying…"})
                    await asyncio.sleep(2)
                    continue

                if self._watch_stop.is_set():
                    break

                if not adapter_logged:
                    adapter = getattr(self.backend, "last_scan_note", None)
                    if isinstance(adapter, dict) and adapter.get("extended_advertising_supported") is not None:
                        await self._emit(
                            "log",
                            {
                                "message": (
                                    "PC Bluetooth adapter: extended advertising "
                                    f"{'YES' if adapter.get('extended_advertising_supported') else 'NO'}"
                                )
                            },
                        )
                        adapter_logged = True

                hits = [
                    device
                    for device in devices
                    if is_orbio_advertised_name(device.name) or (device.name and needle in device.name.lower())
                ]
                if hits:
                    self.status.devices = hits
                    await self._emit("devices", {"devices": [asdict(device) for device in hits]})
                    for device in hits:
                        if device.address in announced:
                            continue
                        announced.add(device.address)
                        rssi = f"{device.rssi} dBm" if device.rssi is not None else "RSSI unknown"
                        await self._emit(
                            "log",
                            {
                                "message": (
                                    f"Found {device.name}  {device.address}  {rssi}. "
                                    "Click Connect on that row."
                                )
                            },
                        )
                elif not self.status.devices:
                    await self._emit(
                        "log",
                        {
                            "message": (
                                f"No '{name_contains}' advertisement in pass {pass_no}. "
                                "Continuing…"
                            )
                        },
                    )

                remaining = pass_s - (time.monotonic() - started)
                if remaining > 0.5 and not self._watch_stop.is_set():
                    try:
                        await asyncio.wait_for(self._watch_stop.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        pass
        finally:
            self.status.scanning = False
            self.status.watching = False
            await self._emit("status", self.public_status())

    async def connect(self, address: str) -> None:
        await self.stop_watch()
        await self._emit("log", {"message": f"Connecting to {address}..."})
        try:
            device = await self.backend.connect(address)
        except Exception:
            await self.start_watch(self._watch_name, self._watch_pass_s)
            raise
        known = next((item for item in self.status.devices if item.address == address), None)
        if known is not None:
            device = DeviceInfo(
                name=known.name or device.name,
                address=device.address,
                rssi=known.rssi,
                simulated=known.simulated,
            )
        self.status.connected = True
        self.status.device = device
        self.assembler.reset()
        await self._emit("log", {"message": f"Connected to {device.name or device.address}"})

        try:
            fw_raw = await self.backend.read_fw_id()
            self.status.fw_id = parse_fw_id(fw_raw)
            await self._emit("log", {"message": f"Firmware ID: {self.status.fw_id}"})
        except Exception as exc:
            await self._emit("log", {"message": f"Could not read firmware ID: {exc}"})

        try:
            params_raw = await self.backend.read_parameters()
            self.status.parameters = parse_report_parameters(params_raw)
            await self._emit("log", {"message": f"Parameters: {self.status.parameters.get('raw', '')}"})
        except Exception as exc:
            await self._emit("log", {"message": f"Could not read parameters: {exc}"})

        await self.backend.start_notify(self._on_notify)
        self.status.notifications = True
        await self._emit(
            "log",
            {
                "message": (
                    "Sweep notifications enabled. Data is sent after each sweep "
                    "if notifications stay enabled. Click Start Sweep or wait for "
                    "the next scheduled sweep."
                )
            },
        )
        await self._emit("status", self.public_status())

    async def disconnect(self, resume_watch: bool = True) -> None:
        try:
            if self.status.notifications:
                await self.backend.stop_notify()
        finally:
            await self.backend.disconnect()
            self.status.connected = False
            self.status.notifications = False
            self.status.device = None
            self.assembler.reset()
            await self._emit("log", {"message": "Disconnected"})
            await self._emit("status", self.public_status())
            if resume_watch:
                await self.start_watch(self._watch_name, self._watch_pass_s)

    async def send_command(self, command: str) -> None:
        if not self.status.connected:
            raise RuntimeError("Not connected")
        await self.backend.write_parameters(command)
        await self._emit("log", {"message": f"Sent command: {command}"})

    def public_status(self) -> dict[str, Any]:
        device = asdict(self.status.device) if self.status.device else None
        last = asdict(self.status.last_sweep) if self.status.last_sweep else None
        return {
            "backend": self.status.backend,
            "scanning": self.status.scanning,
            "connected": self.status.connected,
            "notifications": self.status.notifications,
            "device": device,
            "fw_id": self.status.fw_id,
            "parameters": self.status.parameters,
            "buffered_bytes": self.assembler.buffered_bytes,
            "expected_bytes": self.status.expected_bytes,
            "sweep_count": self.status.sweep_count,
            "last_sweep": last,
            "devices": [asdict(item) for item in self.status.devices],
            "watching": self.status.watching,
        }

    def _on_notify(self, _handle: int | bytearray, data: bytearray | None = None) -> None:
        # Bleak calls callback(sender, data). The simulator calls callback(data).
        chunk = data if data is not None else _handle
        if not isinstance(chunk, (bytes, bytearray)):
            return
        payload = bytes(chunk)

        async def _handle_chunk() -> None:
            try:
                complete = self.assembler.push(payload, time.monotonic())
                self.status.buffered_bytes = self.assembler.buffered_bytes
                await self._emit(
                    "packet",
                    {
                        "bytes": len(payload),
                        "buffered_bytes": self.assembler.buffered_bytes,
                        "expected_bytes": self.status.expected_bytes,
                        "dropped_partials": self.assembler.dropped_partials,
                    },
                )
                for sweep in complete:
                    await self._complete_sweep(sweep)
            except Exception as exc:
                await self._emit("log", {"message": f"Failed to handle BLE payload: {exc}"})

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        asyncio.run_coroutine_threadsafe(_handle_chunk(), loop)

    async def _complete_sweep(self, payload: bytes) -> None:
        self._sweep_index += 1
        device = self.status.device
        record = save_sweep(
            payload,
            index=self._sweep_index,
            device_name=device.name if device else None,
            device_address=device.address if device else None,
            extra={"fw_id": self.status.fw_id, "parameters": self.status.parameters},
        )
        self.status.sweep_count = self._sweep_index
        self.status.last_sweep = record
        self.status.buffered_bytes = self.assembler.buffered_bytes
        await self._emit("sweep", asdict(record))
        await self._emit(
            "log",
            {"message": f"Sweep {record.index} saved ({record.n_samples} I/Q samples) -> {record.csv_path}"},
        )
        await self._emit("status", self.public_status())

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in list(self._handlers):
            result = handler(event, payload)
            if asyncio.iscoroutine(result):
                await result
