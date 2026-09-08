"""Reassemble BLE notification chunks into 4992-byte sweeps."""

from __future__ import annotations

from dataclasses import dataclass

from orbio.protocol import (
    FRAME_HEADER_BYTES,
    PACKET_MAGIC,
    PACKET_TYPE_SWEEP,
    SWEEP_BYTES,
    FrameHeader,
    parse_frame_header,
    trailing_magic_prefix_len,
)


@dataclass
class AssemblerStatus:
    buffered_bytes: int
    expected_bytes: int = SWEEP_BYTES

    @property
    def fraction(self) -> float:
        return min(1.0, self.buffered_bytes / self.expected_bytes)


class SweepAssembler:
    """Concatenate notify payloads until a full sweep is present.

    The spec header (``AA BB CC`` + type + timestamp + length) appears only at
    the start of a sweep. Later BLE packets of the same sweep are raw I/Q.
    If the magic appears after I/Q has already been buffered, the partial
    sweep is dropped and counted as packet loss. Headerless streams (simulator
    tests) are still assembled as 4992-byte I/Q frames.
    """

    def __init__(self, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s
        self._buffer = bytearray()
        self._hold = b""
        self._need_header = True
        self._expected_iq = SWEEP_BYTES
        self._last_chunk_at: float | None = None
        self._header: FrameHeader | None = None
        self.dropped_partials = 0
        self.began_new_sweep = False
        self.packet_loss = 0
        self.packet_loss_this_push = 0
        self.byte_count_mismatches = 0
        self.byte_count_mismatch_this_push = 0
        self.last_sweep_header: FrameHeader | None = None
        self.last_byte_count_ok = True
        self.last_assembled_bytes = 0

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def expected_bytes(self) -> int:
        return self._expected_iq

    def reset(self) -> None:
        self._buffer.clear()
        self._hold = b""
        self._need_header = True
        self._expected_iq = SWEEP_BYTES
        self._last_chunk_at = None
        self._header = None
        self.began_new_sweep = False
        self.packet_loss_this_push = 0
        self.byte_count_mismatch_this_push = 0

    def _drop_partial(self, *, packet_loss: bool) -> None:
        if self._buffer:
            self.dropped_partials += 1
            if packet_loss:
                self.packet_loss += 1
                self.packet_loss_this_push += 1
        self._buffer.clear()
        self._header = None
        self._expected_iq = SWEEP_BYTES
        self._need_header = True

    def _record_complete(self, payload: bytes) -> None:
        header = self._header
        ok = len(payload) == SWEEP_BYTES
        if header is not None:
            ok = ok and header.payload_bytes == SWEEP_BYTES
        self.last_sweep_header = header
        self.last_assembled_bytes = len(payload)
        self.last_byte_count_ok = ok
        if not ok:
            self.byte_count_mismatches += 1
            self.byte_count_mismatch_this_push += 1
        self._header = None
        self._need_header = True
        self._expected_iq = SWEEP_BYTES

    def _take_complete(self) -> bytes | None:
        if self._expected_iq <= 0 or len(self._buffer) < self._expected_iq:
            return None
        payload = bytes(self._buffer[: self._expected_iq])
        del self._buffer[: self._expected_iq]
        self._record_complete(payload)
        return payload

    def _start_sweep(self, header: FrameHeader | None) -> None:
        self._header = header
        self._expected_iq = header.payload_bytes if header is not None else SWEEP_BYTES
        if self._expected_iq <= 0:
            self._expected_iq = SWEEP_BYTES
        self._need_header = False
        self.began_new_sweep = True

    def push(self, chunk: bytes, now: float) -> list[bytes]:
        stale = (
            bool(self._buffer)
            and self._last_chunk_at is not None
            and (now - self._last_chunk_at) > self.timeout_s
        )
        if stale:
            self._drop_partial(packet_loss=False)
            self._hold = b""

        stream = bytearray(self._hold + chunk)
        self._hold = b""
        self.packet_loss_this_push = 0
        self.byte_count_mismatch_this_push = 0
        self.began_new_sweep = stale
        complete: list[bytes] = []

        while stream:
            if self._need_header:
                data = bytes(stream)
                idx = data.find(PACKET_MAGIC)
                if idx < 0:
                    prefix = trailing_magic_prefix_len(data)
                    iq = data[:-prefix] if prefix else data
                    hold = data[-prefix:] if prefix else b""
                    if iq:
                        if not self._buffer:
                            self.began_new_sweep = True
                        self._start_sweep(None)
                        self._buffer.extend(iq)
                        taken = self._take_complete()
                        if taken:
                            complete.append(taken)
                            leftover = bytes(self._buffer)
                            self._buffer.clear()
                            stream[:] = leftover + hold
                            continue
                    self._hold = hold
                    break
                if idx > 0:
                    del stream[:idx]
                    continue
                if len(data) < FRAME_HEADER_BYTES:
                    self._hold = data
                    break
                header = parse_frame_header(data)
                del stream[:FRAME_HEADER_BYTES]
                if header is None or header.packet_type != PACKET_TYPE_SWEEP:
                    continue
                self._start_sweep(header)
                continue

            data = bytes(stream)
            idx = data.find(PACKET_MAGIC)
            if idx == 0:
                if self._buffer:
                    self._drop_partial(packet_loss=True)
                else:
                    self._need_header = True
                continue
            if idx > 0:
                self._buffer.extend(stream[:idx])
                del stream[:idx]
                taken = self._take_complete()
                if taken:
                    complete.append(taken)
                    leftover = bytes(self._buffer)
                    self._buffer.clear()
                    stream[0:0] = leftover
                if self._buffer:
                    self._drop_partial(packet_loss=True)
                else:
                    self._need_header = True
                continue

            prefix = trailing_magic_prefix_len(data)
            if prefix:
                self._buffer.extend(stream[:-prefix])
                self._hold = bytes(stream[-prefix:])
                stream.clear()
            else:
                self._buffer.extend(stream)
                stream.clear()
            taken = self._take_complete()
            if taken:
                complete.append(taken)
                leftover = bytes(self._buffer)
                self._buffer.clear()
                if leftover or self._hold:
                    stream[:] = leftover + self._hold
                    self._hold = b""

        if stream or self._buffer:
            self._last_chunk_at = now
        elif complete:
            self._last_chunk_at = now
        return complete
