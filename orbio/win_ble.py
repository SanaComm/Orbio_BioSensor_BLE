"""Windows COM/WinRT setup for Bleak.

Must run before Bleak (and pythoncom) are imported in that process.
"""

from __future__ import annotations

import sys

_prepared = False


def prepare_windows_ble(*, uninitialize_sta: bool = False) -> None:
    global _prepared
    if sys.platform != "win32":
        return
    sys.coinit_flags = 0
    if uninitialize_sta and not _prepared:
        try:
            from bleak.backends.winrt.util import uninitialize_sta as _uninitialize_sta

            _uninitialize_sta()
        except Exception:
            pass
    _prepared = True
