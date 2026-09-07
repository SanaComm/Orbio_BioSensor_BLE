"""Launch the Phase 1 capture UI as a desktop window."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import uvicorn

LOG_PATH = Path(__file__).resolve().parent.parent / "orbio_launch.log"


def _ensure_console_python() -> None:
    """pythonw.exe hides crashes; relaunch with python.exe so the window can start."""
    exe = Path(sys.executable)
    if exe.name.lower() != "pythonw.exe":
        return
    python = exe.with_name("python.exe")
    if not python.is_file():
        return
    os.execv(str(python), [str(python), "-m", "orbio.app", *sys.argv[1:]])


def _show_error(message: str) -> None:
    LOG_PATH.write_text(message, encoding="utf-8")
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Orbio BioSensor BLE", 0x10)


def _run_server(server: uvicorn.Server) -> None:
    if sys.platform == "win32":
        sys.coinit_flags = 0
    server.run()


def _wait_until_started(server: uvicorn.Server, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server.started:
            return
        time.sleep(0.05)
    raise RuntimeError("Capture server did not start")


def _server_already_up(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _open_window(url: str) -> None:
    try:
        import webview
    except ImportError:
        print("pywebview is not installed; opening the system browser instead.")
        webbrowser.open(url)
        while True:
            time.sleep(1)

    webview.create_window(
        "Orbio BioSensor BLE",
        url,
        width=1400,
        height=900,
        min_size=(1100, 720),
    )
    webview.start()


def main(argv: list[str] | None = None) -> None:
    _ensure_console_python()
    parser = argparse.ArgumentParser(description="Orbio BioSensor BLE Phase 1 capture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a local simulated Orbio device (no Bluetooth hardware)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open the system browser instead of a desktop window",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Run the local server only (no desktop window, no browser)",
    )
    args = parser.parse_args(argv)

    if args.simulate:
        os.environ["ORBIO_SIMULATE"] = "1"

    url = f"http://{args.host}:{args.port}/"
    own_server = False
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    try:
        if _server_already_up(url):
            print(f"Using capture server already running at {url}")
        else:
            config = uvicorn.Config(
                "orbio.api:app",
                host=args.host,
                port=args.port,
                reload=False,
                log_level="info",
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=_run_server, args=(server,), name="orbio-uvicorn", daemon=False)
            thread.start()
            own_server = True
            _wait_until_started(server)
            print(f"Orbio capture UI: {url}")
        if args.simulate:
            print("Simulator mode: fake Orbio-sim001 appears automatically")

        if args.no_window:
            if thread:
                thread.join()
            return

        if args.browser:
            webbrowser.open(url)
            if thread:
                thread.join()
            return

        _open_window(url)
    except Exception as exc:
        _show_error(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}\nLog: {LOG_PATH}")
        raise
    finally:
        if own_server and server is not None:
            server.should_exit = True
            if thread is not None:
                thread.join(timeout=8)


if __name__ == "__main__":
    main()
