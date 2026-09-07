"""Reassemble BLE notification chunks into 4992-byte sweeps."""

from __future__ import annotations

from dataclasses import dataclass

from orbio.protocol import SWEEP_BYTES


@dataclass
class AssemblerStatus:
    buffered_bytes: int
    expected_bytes: int = SWEEP_BYTES

    @property
    def fraction(self) -> float:
        return min(1.0, self.buffered_bytes / self.expected_bytes)


class SweepAssembler:
    """Concatenate notify payloads until a full sweep is present.

    The firmware does not document sequence numbers. Chunks are appended in
    arrival order. If a gap longer than ``timeout_s`` occurs mid-sweep, the
    partial buffer is dropped.
    """

    def __init__(self, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s
        self._buffer = bytearray()
        self._last_chunk_at: float | None = None
        self.dropped_partials = 0
        self.began_new_sweep = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self._last_chunk_at = None
        self.began_new_sweep = False

    def push(self, chunk: bytes, now: float) -> list[bytes]:
        if not chunk:
            self.began_new_sweep = False
            return []

        stale = (
            bool(self._buffer)
            and self._last_chunk_at is not None
            and (now - self._last_chunk_at) > self.timeout_s
        )
        if stale:
            self.dropped_partials += 1
            self._buffer.clear()

        self.began_new_sweep = stale or not self._buffer
        self._last_chunk_at = now
        self._buffer.extend(chunk)

        complete: list[bytes] = []
        while len(self._buffer) >= SWEEP_BYTES:
            complete.append(bytes(self._buffer[:SWEEP_BYTES]))
            del self._buffer[:SWEEP_BYTES]
        return complete
