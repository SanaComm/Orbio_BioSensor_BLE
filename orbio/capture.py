"""Write captured sweeps to disk as raw binary plus CSV."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from orbio.protocol import IqSample, parse_sweep

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEMORY_DIR = DATA_DIR / "memories"
MEMORY_SLOTS = 5


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_memory_dir() -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR


def _memory_path(slot: int) -> Path:
    if slot < 1 or slot > MEMORY_SLOTS:
        raise ValueError(f"Memory slot must be 1..{MEMORY_SLOTS}")
    return MEMORY_DIR / f"mem{slot}.json"


def list_memories() -> list[dict]:
    ensure_memory_dir()
    slots: list[dict] = []
    for slot in range(1, MEMORY_SLOTS + 1):
        path = _memory_path(slot)
        if not path.is_file():
            slots.append({"slot": slot, "empty": True, "label": f"Mem {slot} (empty)"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            slots.append({"slot": slot, "empty": True, "label": f"Mem {slot} (empty)"})
            continue
        n_samples = payload.get("n_samples") or len(payload.get("points") or [])
        saved_at = str(payload.get("saved_at") or "")[:19].replace("T", " ")
        device = payload.get("device_name") or ""
        extra = " · ".join(part for part in (device, f"{n_samples} pts", saved_at) if part)
        slots.append(
            {
                "slot": slot,
                "empty": False,
                "n_samples": n_samples,
                "saved_at": payload.get("saved_at"),
                "device_name": payload.get("device_name"),
                "label": f"Mem {slot} · {extra}" if extra else f"Mem {slot}",
            }
        )
    return slots


def save_memory(slot: int, points: list[dict], meta: dict | None = None) -> dict:
    if not points:
        raise ValueError("Nothing to save into memory")
    ensure_memory_dir()
    payload = {
        "slot": slot,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(points),
        "points": points,
    }
    if meta:
        payload.update({key: value for key, value in meta.items() if value not in (None, "")})
    path = _memory_path(slot)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"ok": True, "slot": slot, "n_samples": len(points), "memories": list_memories()}


def load_memory(slot: int) -> dict:
    path = _memory_path(slot)
    if not path.is_file():
        raise FileNotFoundError(f"Memory {slot} is empty")
    payload = json.loads(path.read_text(encoding="utf-8"))
    points = payload.get("points") or []
    if not points:
        raise FileNotFoundError(f"Memory {slot} is empty")
    return payload


def clear_capture_files() -> dict:
    """Delete bulk sweep captures. Five memory slots are left alone."""
    deleted = 0
    if DATA_DIR.is_dir():
        for path in DATA_DIR.iterdir():
            if path.is_file() and path.suffix.lower() in {".bin", ".csv", ".json"}:
                if path.name.startswith("sweep_"):
                    path.unlink()
                    deleted += 1
    return {"ok": True, "deleted": deleted, "memories": list_memories()}


@dataclass
class SweepRecord:
    index: int
    captured_at: str
    device_name: str | None
    device_address: str | None
    bin_path: str
    csv_path: str
    json_path: str
    n_samples: int
    i_min: int
    i_max: int
    q_min: int
    q_max: int


def save_sweep(
    payload: bytes,
    *,
    index: int,
    device_name: str | None,
    device_address: str | None,
    extra: dict | None = None,
) -> SweepRecord:
    ensure_data_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = DATA_DIR / f"sweep_{stamp}_{index:04d}"
    bin_path = stem.with_suffix(".bin")
    csv_path = stem.with_suffix(".csv")
    json_path = stem.with_suffix(".json")

    bin_path.write_bytes(payload)
    samples = parse_sweep(payload)
    _write_csv(csv_path, samples)

    i_values = [row.i for row in samples]
    q_values = [row.q for row in samples]
    record = SweepRecord(
        index=index,
        captured_at=datetime.now(timezone.utc).isoformat(),
        device_name=device_name,
        device_address=device_address,
        bin_path=str(bin_path),
        csv_path=str(csv_path),
        json_path=str(json_path),
        n_samples=len(samples),
        i_min=min(i_values),
        i_max=max(i_values),
        q_min=min(q_values),
        q_max=max(q_values),
    )
    meta = asdict(record)
    if extra:
        meta["extra"] = extra
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return record


def load_latest_sweep() -> dict | None:
    """Return points from the newest captured CSV, for dashboard plot preview."""
    if not DATA_DIR.is_dir():
        return None
    csv_files = list(DATA_DIR.glob("sweep_*.csv"))
    if not csv_files:
        return None
    latest = max(csv_files, key=lambda path: path.stat().st_mtime)
    points: list[dict[str, int]] = []
    with latest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            points.append(
                {
                    "i": int(row["i"]),
                    "q": int(row["q"]),
                    "f": int(row["frequency_mhz"]),
                }
            )
    if not points:
        return None
    return {
        "csv_path": str(latest),
        "name": latest.name,
        "n_samples": len(points),
        "points": points,
    }


def _write_csv(path: Path, samples: list[IqSample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frequency_mhz", "sample_index", "i", "q"])
        for row in samples:
            writer.writerow([row.frequency_mhz, row.sample_index, row.i, row.q])
