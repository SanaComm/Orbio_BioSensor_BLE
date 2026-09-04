"""Radio backends: real BLE via Bleak, plus a local simulator."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Callable, Protocol

from orbio.protocol import (
    DEVICE_NAME_PREFIX,
    FREQ_MHZ,
    N_SAMPLES,
    REPORT_FW_ID_UUID,
    REPORT_PARAMETERS_UUID,
    SET_PARAMETERS_UUID,
    SWEEP_BYTES,
    SWEEP_DATA_UUID,
    encode_parameter_write,
    is_orbio_advertised_name,
)

NotifyCallback = Callable[..., None]


@dataclass(frozen=True)
class DeviceInfo:
    name: str | None
    address: str
    rssi: int | None = None
    simulated: bool = False
    likely_orbio: bool = False


class RadioBackend(Protocol):
    async def scan(self, timeout_s: float) -> list[DeviceInfo]: ...
    async def connect(self, address: str) -> DeviceInfo: ...
    async def disconnect(self) -> None: ...
    async def start_notify(self, callback: NotifyCallback) -> None: ...
    async def stop_notify(self) -> None: ...
    async def write_parameters(self, command: str) -> None: ...
    async def read_fw_id(self) -> bytes: ...
    async def read_parameters(self) -> bytes: ...


class BleakBackend:
    def __init__(self) -> None:
        self._client = None
        self._connected: DeviceInfo | None = None
        self._scan_proc = None
        self.last_scan_note = None

    def cancel_scan(self) -> None:
        proc = self._scan_proc
        if proc is not None and proc.returncode is None:
            proc.kill()

    async def scan(self, timeout_s: float) -> list[DeviceInfo]:
        import json
        import sys
        from pathlib import Path

        found: dict[str, DeviceInfo] = {}
        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(Path(__file__).resolve().parent.parent),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "orbio.ble_scan",
            str(timeout_s),
            **kwargs,
        )
        self._scan_proc = proc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ConnectionError("BLE scan timed out") from None
        finally:
            self._scan_proc = None

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ConnectionError(detail or f"BLE scan exited with code {proc.returncode}")

        try:
            payload = json.loads(stdout.decode("utf-8") or "[]")
        except json.JSONDecodeError as exc:
            raise ConnectionError(f"BLE scan returned invalid data: {exc}") from exc

        if isinstance(payload, dict):
            self.last_scan_note = payload.get("adapter")
            rows = payload.get("devices") or []
        else:
            self.last_scan_note = None
            rows = payload

        for row in rows:
            name = row.get("name")
            if not is_orbio_advertised_name(name):
                continue
            address = row.get("address")
            if not address:
                continue
            found[address] = DeviceInfo(
                name=name,
                address=address,
                rssi=row.get("rssi"),
                likely_orbio=True,
            )

        def _sort_key(item: DeviceInfo) -> tuple:
            orbio = 0 if item.likely_orbio or (item.name or "").startswith(DEVICE_NAME_PREFIX) else 1
            strength = -(item.rssi if item.rssi is not None else -999)
            return (orbio, strength, (item.name or item.address).lower())

        return sorted(found.values(), key=_sort_key)

    async def connect(self, address: str) -> DeviceInfo:
        from bleak import BleakClient

        client = BleakClient(address, timeout=20.0)
        await client.connect()
        if not client.is_connected:
            raise ConnectionError(f"Failed to connect to {address}")
        self._client = client
        name = getattr(client, "name", None) or DEVICE_NAME_PREFIX + "unknown"
        self._connected = DeviceInfo(name=name, address=address)
        return self._connected

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self._connected = None
        if client is not None and client.is_connected:
            await client.disconnect()

    async def start_notify(self, callback: NotifyCallback) -> None:
        await self._require_client().start_notify(SWEEP_DATA_UUID, callback)

    async def stop_notify(self) -> None:
        client = self._require_client()
        try:
            await client.stop_notify(SWEEP_DATA_UUID)
        except Exception:
            pass

    async def write_parameters(self, command: str) -> None:
        payload = encode_parameter_write(command)
        await self._require_client().write_gatt_char(SET_PARAMETERS_UUID, payload, response=True)

    async def read_fw_id(self) -> bytes:
        data = await self._require_client().read_gatt_char(REPORT_FW_ID_UUID)
        return bytes(data)

    async def read_parameters(self) -> bytes:
        data = await self._require_client().read_gatt_char(REPORT_PARAMETERS_UUID)
        return bytes(data)

    def _require_client(self):
        if self._client is None or not self._client.is_connected:
            raise ConnectionError("Not connected to a BLE device")
        return self._client


class SimulatorBackend:
    """Emits Orbio-shaped sweep notifications without hardware."""

    def __init__(self) -> None:
        self._connected: DeviceInfo | None = None
        self._callback: NotifyCallback | None = None
        self._sweep_task: asyncio.Task | None = None
        self._streaming = False
        self._fw = 1
        self._params = "10,0,0,30,600"

    async def scan(self, timeout_s: float) -> list[DeviceInfo]:
        await asyncio.sleep(min(timeout_s, 0.4))
        return [
            DeviceInfo(
                name="Orbio-sim001",
                address="SIM:00:00:00:00:01",
                rssi=-47,
                simulated=True,
                likely_orbio=True,
            )
        ]

    async def connect(self, address: str) -> DeviceInfo:
        self._connected = DeviceInfo(name="Orbio-sim001", address=address, rssi=-47, simulated=True)
        return self._connected

    async def disconnect(self) -> None:
        await self.stop_notify()
        self._connected = None
        self._streaming = False

    async def start_notify(self, callback: NotifyCallback) -> None:
        self._callback = callback
        if self._sweep_task:
            self._sweep_task.cancel()
        self._sweep_task = asyncio.create_task(self._maybe_stream())

    async def stop_notify(self) -> None:
        self._callback = None
        if self._sweep_task:
            self._sweep_task.cancel()
            self._sweep_task = None

    async def write_parameters(self, command: str) -> None:
        code = command.strip().split(",", 1)[0]
        if code == "1":
            self._streaming = True
        elif code == "2":
            self._streaming = False

    async def read_fw_id(self) -> bytes:
        return bytes([self._fw])

    async def read_parameters(self) -> bytes:
        return self._params.encode("ascii")

    async def _maybe_stream(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.5)
                if self._streaming and self._callback:
                    payload = _fake_sweep()
                    for start in range(0, SWEEP_BYTES, 240):
                        chunk = payload[start : start + 240]
                        self._callback(0, bytearray(chunk))
                        await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return


def _fake_sweep() -> bytes:
    chunks = bytearray()
    for freq_index, freq in enumerate(FREQ_MHZ):
        for sample in range(N_SAMPLES):
            i = int(8000 + 4000 * ((freq - 700) / 380) + 200 * sample)
            q = int(1200 * ((sample - 16) / 16) + 80 * freq_index)
            i = max(-32768, min(32767, i))
            q = max(-32768, min(32767, q))
            chunks.extend(struct.pack("<hh", i, q))
    assert len(chunks) == SWEEP_BYTES
    return bytes(chunks)
