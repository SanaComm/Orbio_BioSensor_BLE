"""One-shot BLE scan in a child process.

Windows WinRT can crash the process when a scanner is started a second time
in the same Python runtime. Running each scan here keeps the capture UI alive.
"""

from __future__ import annotations

import asyncio
import json
import sys

from orbio.protocol import (
    CONTROL_SERVICE_UUID,
    DATA_SERVICE_UUID,
    DEVICE_NAME_PREFIX,
    is_orbio_advertised_name,
)
from orbio.win_ble import prepare_windows_ble

ORBIO_SERVICE_UUIDS = {
    DATA_SERVICE_UUID.lower(),
    CONTROL_SERVICE_UUID.lower(),
}


def _enable_windows_extended_ads() -> None:
    """Phones see BLE 5 extended ads by default; older Windows/Bleak does not."""
    if sys.platform != "win32":
        return
    try:
        from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementWatcher
    except Exception:
        return
    original = BluetoothLEAdvertisementWatcher.start

    def start(self):
        try:
            self.allow_extended_advertisements = True
        except Exception:
            pass
        return original(self)

    BluetoothLEAdvertisementWatcher.start = start


async def _adapter_info() -> dict:
    info = {"extended_advertising_supported": None, "low_energy_supported": None}
    if sys.platform != "win32":
        return info
    try:
        from winrt.windows.devices.bluetooth import BluetoothAdapter

        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None:
            return info
        info["low_energy_supported"] = bool(adapter.is_low_energy_supported)
        info["extended_advertising_supported"] = bool(adapter.is_extended_advertising_supported)
        info["central_role_supported"] = bool(adapter.is_central_role_supported)
    except Exception as exc:
        info["error"] = str(exc)
    return info


def _is_likely_orbio(name: str | None, address: str, service_uuids: list[str]) -> bool:
    if is_orbio_advertised_name(name) or (name and name.startswith(DEVICE_NAME_PREFIX)):
        return True
    return any(uuid.lower() in ORBIO_SERVICE_UUIDS for uuid in service_uuids)


async def scan_once(timeout_s: float) -> dict:
    from bleak import BleakScanner

    _enable_windows_extended_ads()
    discovered = await BleakScanner.discover(
        timeout=timeout_s,
        return_adv=True,
        scanning_mode="active",
    )
    rows: list[dict] = []
    for address, value in discovered.items():
        if isinstance(value, tuple) and len(value) >= 2:
            device, advertisement = value[0], value[1]
        else:
            device, advertisement = None, value

        if device is not None and not isinstance(device, str):
            address = getattr(device, "address", address)
            name = device.name or getattr(advertisement, "local_name", None)
        else:
            name = getattr(advertisement, "local_name", None)

        rssi = getattr(advertisement, "rssi", None)
        service_uuids = [str(uuid) for uuid in (getattr(advertisement, "service_uuids", None) or [])]
        rows.append(
            {
                "name": name,
                "address": address,
                "rssi": rssi,
                "service_uuids": service_uuids,
                "likely_orbio": _is_likely_orbio(name, address, service_uuids),
            }
        )
    return {"adapter": await _adapter_info(), "devices": rows}


def main() -> None:
    prepare_windows_ble(uninitialize_sta=True)
    timeout_s = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    payload = asyncio.run(scan_once(timeout_s))
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
