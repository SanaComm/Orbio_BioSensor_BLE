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


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


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


def _write_csv(path: Path, samples: list[IqSample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frequency_mhz", "sample_index", "i", "q"])
        for row in samples:
            writer.writerow([row.frequency_mhz, row.sample_index, row.i, row.q])
