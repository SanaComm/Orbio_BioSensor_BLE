"""High-level capture session used by the web UI and CLI."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from orbio.assembler import SweepAssembler
from orbio.capture import SweepRecord, save_sweep
from orbio.protocol import SWEEP_BYTES, is_orbio_advertised_name, parse_fw_id, parse_report_parameters, parse_sweep
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
    last_iq_points: list[dict[str, int]] = field(default_factory=list)
    mtu: int | None = None
    connection_interval_ms: float | None = None
    connection_latency: int | None = None


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
        self._connecting = False
        self._expect_disconnect = False
        self._handling_remote_drop = False
        if hasattr(self.backend, "on_disconnected"):
            self.backend.on_disconnected = self._on_remote_disconnected
        if hasattr(self.backend, "on_connection_info"):
            self.backend.on_connection_info = self._on_connection_info
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
        self._watch_task = asyncio.create_task(self._watch_then_connect(needle, pass_s))

    async def _watch_then_connect(self, name_contains: str, pass_s: float) -> None:
        found: DeviceInfo | None = None
        try:
            found = await self._watch_loop(name_contains, pass_s)
        finally:
            if self._watch_task is asyncio.current_task():
                self._watch_task = None
        if found is not None and not self.status.connected and not self._connecting:
            try:
                await self._do_connect(found.address)
            except Exception as exc:
                detail = str(exc).strip() or repr(exc)
                await self._emit("log", {"message": f"Auto-connect failed: {detail}"})

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

    async def _watch_loop(self, name_contains: str, pass_s: float) -> DeviceInfo | None:
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
                    device = hits[0]
                    rssi = f"{device.rssi} dBm" if device.rssi is not None else "RSSI unknown"
                    await self._emit(
                        "log",
                        {
                            "message": (
                                f"Found {device.name}  {device.address}  {rssi}. "
                                "Connecting automatically…"
                            )
                        },
                    )
                    return device
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
            return None
        finally:
            self.status.scanning = False
            self.status.watching = False
            await self._emit("status", self.public_status())

    async def connect(self, address: str) -> None:
        await self.stop_watch()
        await self._do_connect(address)

    async def _do_connect(self, address: str) -> None:
        if self.status.connected or self._connecting:
            return
        self._connecting = True
        try:
            await self._connect_body(address)
        finally:
            self._connecting = False

    async def _connect_body(self, address: str) -> None:
        known = next((item for item in self.status.devices if item.address == address), None)
        label = (known.name if known else None) or address
        last_error: Exception | None = None
        device: DeviceInfo | None = None
        for attempt in range(1, 5):
            wait = attempt > 1 or known is None
            await self._emit(
                "log",
                {
                    "message": (
                        f"Connect attempt {attempt}/4 to {label}: "
                        + (
                            "waiting for a live advertisement, then connecting…"
                            if wait
                            else "connecting immediately (already seen)…"
                        )
                    )
                },
            )
            try:
                device = await self.backend.connect(
                    address,
                    name=known.name if known else None,
                    wait_for_advertisement=wait,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                detail = str(exc).strip() or repr(exc)
                await self._emit("log", {"message": f"Attempt {attempt} failed: {detail}"})
                try:
                    await self.backend.disconnect()
                except Exception:
                    pass
                if attempt < 4:
                    delay = min(2**attempt, 15)
                    await self._emit("log", {"message": f"Retrying in {delay}s…"})
                    await asyncio.sleep(delay)
        if device is None:
            await self.start_watch(self._watch_name, self._watch_pass_s)
            raise last_error if last_error else ConnectionError("Connect failed")
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
        await self._refresh_connection_info(delay_s=0.4)
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

    def _on_remote_disconnected(self) -> None:
        if self._expect_disconnect or self._handling_remote_drop or not self.status.connected:
            return
        self._handling_remote_drop = True
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._handling_remote_drop = False
                return
            self._loop = loop
        asyncio.run_coroutine_threadsafe(self._handle_remote_disconnect(), loop)

    async def _handle_remote_disconnect(self) -> None:
        try:
            if self._expect_disconnect or not self.status.connected:
                return
            await self._emit(
                "log",
                {"message": "Remote closed the BLE connection. Returning to scan."},
            )
            try:
                await self.disconnect(resume_watch=True)
            except Exception as exc:
                detail = str(exc).strip() or repr(exc)
                await self._emit("log", {"message": f"Cleanup after remote disconnect failed: {detail}"})
                self.status.connected = False
                self.status.notifications = False
                self.status.device = None
                self.status.mtu = None
                self.status.connection_interval_ms = None
                self.status.connection_latency = None
                await self.start_watch(self._watch_name, self._watch_pass_s)
        finally:
            self._handling_remote_drop = False

    async def disconnect(self, resume_watch: bool = True) -> None:
        self._expect_disconnect = True
        try:
            if self.status.notifications:
                await self.backend.stop_notify()
        finally:
            try:
                await self.backend.disconnect()
            finally:
                was_connected = self.status.connected
                self.status.connected = False
                self.status.notifications = False
                self.status.device = None
                self.status.mtu = None
                self.status.connection_interval_ms = None
                self.status.connection_latency = None
                self.assembler.reset()
                if was_connected:
                    await self._emit("log", {"message": "Disconnected"})
                await self._emit("status", self.public_status())
                self._expect_disconnect = False
                if resume_watch:
                    await self.start_watch(self._watch_name, self._watch_pass_s)

    async def send_command(self, command: str) -> None:
        if not self.status.connected:
            raise RuntimeError("Not connected")
        await self.backend.write_parameters(command)
        await self._emit("log", {"message": f"Sent command: {command}"})
        code = command.strip().split(",", 1)[0]
        if code == "1":
            await self._reset_iq_plot("Plot cleared for a new sweep set.")

    async def clear_stats(self) -> dict[str, Any]:
        self.assembler.clear_stats()
        status = self.public_status()
        await self._emit("log", {"message": "Packet loss stats cleared"})
        await self._emit("packet_loss", {"count": 0, "total": 0})
        await self._emit("status", status)
        return status

    def _apply_connection_info(self, info: dict[str, Any]) -> bool:
        changed = False
        mtu = info.get("mtu")
        interval_ms = info.get("connection_interval_ms")
        latency = info.get("connection_latency")
        if mtu != self.status.mtu:
            self.status.mtu = mtu
            changed = True
        if interval_ms != self.status.connection_interval_ms:
            self.status.connection_interval_ms = interval_ms
            changed = True
        if latency != self.status.connection_latency:
            self.status.connection_latency = latency
            changed = True
        return changed

    def _connection_info_log(self) -> str:
        parts: list[str] = []
        if self.status.mtu:
            parts.append(f"MTU {self.status.mtu} bytes")
        if self.status.connection_interval_ms:
            parts.append(f"interval {self.status.connection_interval_ms:g} ms")
        elif self.status.connected:
            parts.append("interval not reported")
        if self.status.connection_latency:
            parts.append(f"latency {self.status.connection_latency} events")
        return "BLE link: " + ", ".join(parts) if parts else "BLE link parameters not available"

    async def _refresh_connection_info(self, delay_s: float = 0.0) -> None:
        reader = getattr(self.backend, "read_connection_info", None)
        if not callable(reader):
            return
        if delay_s:
            await asyncio.sleep(delay_s)
        try:
            info = reader()
        except Exception as exc:
            await self._emit("log", {"message": f"Could not read BLE link parameters: {exc}"})
            return
        self._apply_connection_info(info)
        await self._emit("log", {"message": self._connection_info_log()})

    def _on_connection_info(self, info: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return

        async def _update() -> None:
            if self._apply_connection_info(info):
                await self._emit("log", {"message": self._connection_info_log()})
                await self._emit("status", self.public_status())

        asyncio.run_coroutine_threadsafe(_update(), loop)

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
            "expected_bytes": self.assembler.expected_bytes,
            "sweep_count": self.status.sweep_count,
            "last_sweep": last,
            "devices": [asdict(item) for item in self.status.devices],
            "watching": self.status.watching,
            "dropped_partials": self.assembler.dropped_partials,
            "packet_loss": self.assembler.packet_loss,
            "packet_count": self.assembler.packet_count,
            "mtu": self.status.mtu,
            "connection_interval_ms": self.status.connection_interval_ms,
            "connection_latency": self.status.connection_latency,
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
                self.status.expected_bytes = self.assembler.expected_bytes
                if self.assembler.packet_loss_this_push:
                    count = self.assembler.packet_loss
                    total = self.assembler.packet_count
                    await self._emit(
                        "log",
                        {
                            "message": (
                                f"Packet Loss # = {count} / {total} "
                                "(AA BB CC appeared mid-sweep; header is start-of-sweep only)"
                            )
                        },
                    )
                    await self._emit("packet_loss", {"count": count, "total": total})
                await self._emit(
                    "packet",
                    {
                        "bytes": len(payload),
                        "buffered_bytes": self.assembler.buffered_bytes,
                        "expected_bytes": self.status.expected_bytes,
                        "dropped_partials": self.assembler.dropped_partials,
                        "packet_loss": self.assembler.packet_loss,
                        "packet_count": self.assembler.packet_count,
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
        header = self.assembler.last_sweep_header
        got = len(payload)
        header_length = header.length if header is not None else None
        byte_count_ok = got == SWEEP_BYTES and self.assembler.last_byte_count_ok
        count_msg = f"Sweep byte count {got} {'matches' if byte_count_ok else 'DOES NOT MATCH'} expected {SWEEP_BYTES}"
        if header_length is not None:
            count_msg += f" (header length field {header_length})"
        await self._emit("log", {"message": count_msg})
        if not byte_count_ok:
            await self._emit(
                "log",
                {"message": f"Discarding sweep: PC received {got} I/Q bytes, spec requires {SWEEP_BYTES}"},
            )
            await self._emit("status", self.public_status())
            return

        self._sweep_index += 1
        device = self.status.device
        extra: dict[str, Any] = {"fw_id": self.status.fw_id, "parameters": self.status.parameters}
        extra["n_bytes"] = got
        extra["expected_bytes"] = SWEEP_BYTES
        extra["byte_count_ok"] = True
        if header is not None:
            extra["frame_type"] = header.packet_type
            extra["frame_timestamp"] = header.timestamp
            extra["frame_length"] = header.length
        record = save_sweep(
            payload,
            index=self._sweep_index,
            device_name=device.name if device else None,
            device_address=device.address if device else None,
            extra=extra,
        )
        self.status.sweep_count = self._sweep_index
        self.status.last_sweep = record
        self.status.buffered_bytes = self.assembler.buffered_bytes
        samples = parse_sweep(payload)
        points = [{"i": row.i, "q": row.q, "f": row.frequency_mhz} for row in samples]
        self.status.last_iq_points = points
        await self._emit(
            "sweep",
            {
                **asdict(record),
                "points": points,
                "n_bytes": got,
                "expected_bytes": SWEEP_BYTES,
                "byte_count_ok": True,
                "header_length": header_length,
                "header_timestamp": header.timestamp if header is not None else None,
            },
        )
        await self._emit(
            "log",
            {"message": f"Sweep {record.index} saved ({record.n_samples} I/Q samples) -> {record.csv_path}"},
        )
        await self._emit("status", self.public_status())

    async def _reset_iq_plot(self, message: str | None = None) -> None:
        self.status.last_iq_points = []
        await self._emit("plot_reset", {})
        if message:
            await self._emit("log", {"message": message})

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in list(self._handlers):
            result = handler(event, payload)
            if asyncio.iscoroutine(result):
                await result
