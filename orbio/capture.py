"""Write captured sweeps to disk as raw binary plus CSV."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from orbio.protocol import AccelSample, IqSample, PpgSample, parse_accel, parse_ppg, parse_sweep

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEMORY_DIR = DATA_DIR / "memories"
MEMORY_SLOTS = 5
MEMORY_KINDS = ("iq", "ppg", "accel")


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_memory_dir() -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR


def _normalize_kind(kind: str | None) -> str:
    value = (kind or "iq").strip().lower()
    if value in {"iq", "sweep"}:
        return "iq"
    if value not in MEMORY_KINDS:
        raise ValueError(f"Memory kind must be one of {', '.join(MEMORY_KINDS)}")
    return value


def _memory_path(slot: int, kind: str = "iq") -> Path:
    if slot < 1 or slot > MEMORY_SLOTS:
        raise ValueError(f"Memory slot must be 1..{MEMORY_SLOTS}")
    kind = _normalize_kind(kind)
    if kind == "iq":
        return MEMORY_DIR / f"mem{slot}.json"
    return MEMORY_DIR / f"{kind}_mem{slot}.json"


def list_memories(kind: str = "iq") -> list[dict]:
    kind = _normalize_kind(kind)
    ensure_memory_dir()
    slots: list[dict] = []
    for slot in range(1, MEMORY_SLOTS + 1):
        path = _memory_path(slot, kind)
        if not path.is_file():
            slots.append({"slot": slot, "empty": True, "kind": kind, "label": f"Mem {slot} (empty)"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            slots.append({"slot": slot, "empty": True, "kind": kind, "label": f"Mem {slot} (empty)"})
            continue
        n_samples = payload.get("n_samples") or len(payload.get("points") or [])
        saved_at = str(payload.get("saved_at") or "")[:19].replace("T", " ")
        device = payload.get("device_name") or ""
        extra = " · ".join(part for part in (device, f"{n_samples} pts", saved_at) if part)
        slots.append(
            {
                "slot": slot,
                "empty": False,
                "kind": kind,
                "n_samples": n_samples,
                "saved_at": payload.get("saved_at"),
                "device_name": payload.get("device_name"),
                "label": f"Mem {slot} · {extra}" if extra else f"Mem {slot}",
            }
        )
    return slots


def save_memory(
    slot: int,
    points: list[dict] | None = None,
    meta: dict | None = None,
    *,
    kind: str = "iq",
    series: object | None = None,
) -> dict:
    kind = _normalize_kind(kind)
    ensure_memory_dir()
    payload: dict = {
        "slot": slot,
        "kind": kind,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    if kind == "iq":
        if not points:
            raise ValueError("Nothing to save into memory")
        payload["n_samples"] = len(points)
        payload["points"] = points
    elif kind == "ppg":
        channels = _normalize_ppg_series(series)
        n_samples = sum(len(channel) for channel in channels)
        if n_samples <= 0:
            raise ValueError("Nothing to save into memory")
        payload["n_samples"] = n_samples
        payload["series"] = channels
    else:
        axes = _normalize_accel_series(series)
        n_samples = len(axes["x"])
        if n_samples <= 0:
            raise ValueError("Nothing to save into memory")
        payload["n_samples"] = n_samples
        payload["series"] = axes
    if meta:
        payload.update({key: value for key, value in meta.items() if value not in (None, "")})
    path = _memory_path(slot, kind)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"ok": True, "slot": slot, "kind": kind, "n_samples": payload["n_samples"], "memories": list_memories(kind)}


def load_memory(slot: int, kind: str = "iq") -> dict:
    kind = _normalize_kind(kind)
    path = _memory_path(slot, kind)
    if not path.is_file():
        raise FileNotFoundError(f"Memory {slot} is empty")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if kind == "iq":
        if not (payload.get("points") or []):
            raise FileNotFoundError(f"Memory {slot} is empty")
    elif kind == "ppg":
        if _ppg_series_count(payload.get("series")) <= 0:
            raise FileNotFoundError(f"Memory {slot} is empty")
    else:
        series = payload.get("series") or {}
        if not (series.get("x") or series.get("y") or series.get("z")):
            raise FileNotFoundError(f"Memory {slot} is empty")
    payload["kind"] = kind
    return payload


def _normalize_ppg_series(series: object | None) -> list[list[float]]:
    if not isinstance(series, list) or len(series) != 8:
        raise ValueError("PPG memory needs 8 channel arrays")
    channels: list[list[float]] = []
    for channel in series:
        if not isinstance(channel, list):
            raise ValueError("PPG memory channels must be arrays")
        channels.append([float(value) for value in channel])
    return channels


def _ppg_series_count(series: object | None) -> int:
    if not isinstance(series, list):
        return 0
    return sum(len(channel) for channel in series if isinstance(channel, list))


def _normalize_accel_series(series: object | None) -> dict[str, list[float]]:
    if not isinstance(series, dict):
        raise ValueError("Accel memory needs x, y, and z arrays")
    axes: dict[str, list[float]] = {}
    for key in ("x", "y", "z"):
        values = series.get(key)
        if not isinstance(values, list):
            raise ValueError("Accel memory needs x, y, and z arrays")
        axes[key] = [float(value) for value in values]
    n = len(axes["x"])
    if len(axes["y"]) != n or len(axes["z"]) != n:
        raise ValueError("Accel X/Y/Z memory lengths must match")
    return axes


def clear_capture_files() -> dict:
    """Delete bulk sweep/PPG/accel captures. Memory slots are left alone."""
    deleted = 0
    if DATA_DIR.is_dir():
        for path in DATA_DIR.iterdir():
            if path.is_file() and path.suffix.lower() in {".bin", ".csv", ".json"}:
                if path.name.startswith(("sweep_", "ppg_", "accel_")):
                    path.unlink()
                    deleted += 1
    return {"ok": True, "deleted": deleted}


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


def _write_ppg_csv(path: Path, samples: list[PpgSample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["channel", "sample_index", "value"])
        for row in samples:
            writer.writerow([row.channel, row.sample_index, row.value])


def _write_accel_csv(path: Path, samples: list[AccelSample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_index", "x", "y", "z"])
        for row in samples:
            writer.writerow([row.sample_index, row.x, row.y, row.z])


@dataclass
class PpgRecord:
    index: int
    captured_at: str
    device_name: str | None
    device_address: str | None
    bin_path: str
    csv_path: str
    json_path: str
    n_samples: int
    n_bytes: int
    value_min: int
    value_max: int


@dataclass
class AccelRecord:
    index: int
    captured_at: str
    device_name: str | None
    device_address: str | None
    bin_path: str
    csv_path: str
    json_path: str
    n_samples: int
    n_bytes: int
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    z_min: int
    z_max: int


def save_ppg(
    payload: bytes,
    *,
    index: int,
    device_name: str | None,
    device_address: str | None,
    extra: dict | None = None,
) -> PpgRecord:
    samples = parse_ppg(payload)
    ensure_data_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = DATA_DIR / f"ppg_{stamp}_{index:04d}"
    bin_path = stem.with_suffix(".bin")
    csv_path = stem.with_suffix(".csv")
    json_path = stem.with_suffix(".json")
    bin_path.write_bytes(payload)
    _write_ppg_csv(csv_path, samples)
    values = [row.value for row in samples] or [0]
    record = PpgRecord(
        index=index,
        captured_at=datetime.now(timezone.utc).isoformat(),
        device_name=device_name,
        device_address=device_address,
        bin_path=str(bin_path),
        csv_path=str(csv_path),
        json_path=str(json_path),
        n_samples=len(samples),
        n_bytes=len(payload),
        value_min=min(values),
        value_max=max(values),
    )
    meta = asdict(record)
    if extra:
        meta["extra"] = extra
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return record


def save_accel(
    payload: bytes,
    *,
    index: int,
    device_name: str | None,
    device_address: str | None,
    extra: dict | None = None,
) -> AccelRecord:
    samples = parse_accel(payload)
    ensure_data_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = DATA_DIR / f"accel_{stamp}_{index:04d}"
    bin_path = stem.with_suffix(".bin")
    csv_path = stem.with_suffix(".csv")
    json_path = stem.with_suffix(".json")
    bin_path.write_bytes(payload)
    _write_accel_csv(csv_path, samples)
    xs = [row.x for row in samples] or [0]
    ys = [row.y for row in samples] or [0]
    zs = [row.z for row in samples] or [0]
    record = AccelRecord(
        index=index,
        captured_at=datetime.now(timezone.utc).isoformat(),
        device_name=device_name,
        device_address=device_address,
        bin_path=str(bin_path),
        csv_path=str(csv_path),
        json_path=str(json_path),
        n_samples=len(samples),
        n_bytes=len(payload),
        x_min=min(xs),
        x_max=max(xs),
        y_min=min(ys),
        y_max=max(ys),
        z_min=min(zs),
        z_max=max(zs),
    )
    meta = asdict(record)
    if extra:
        meta["extra"] = extra
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return record
