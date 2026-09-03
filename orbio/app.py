"""Launch the Phase 1 capture UI."""

from __future__ import annotations

import argparse
import os
import webbrowser
from threading import Timer

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Orbio BioSensor BLE Phase 1 capture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a local simulated Orbio device (no Bluetooth hardware)",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if args.simulate:
        os.environ["ORBIO_SIMULATE"] = "1"

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        Timer(0.8, lambda: webbrowser.open(url)).start()

    print(f"Orbio capture UI: {url}")
    if args.simulate:
        print("Simulator mode: scan, connect, then send command 1 (Start Sweep)")
    uvicorn.run("orbio.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
