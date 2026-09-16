"""Reassemble BLE notification chunks into typed frames (sweep / PPG / accel)."""

from __future__ import annotations

from dataclasses import dataclass

from orbio.protocol import (
    ACCEL_BYTES_PER_SAMPLE,
    FRAME_HEADER_BYTES,
    KNOWN_FRAME_TYPES,
    PACKET_MAGIC,
    PACKET_TYPE_ACCEL,
    PACKET_TYPE_PPG,
    PACKET_TYPE_SWEEP,
    PPG_BYTES_PER_SAMPLE,
    SWEEP_BYTES,
    FrameHeader,
    parse_frame_header,
    trailing_magic_prefix_len,
)


@dataclass
class AssembledFrame:
    packet_type: int
    payload: bytes
    header: FrameHeader | None = None

    def startswith(self, prefix: bytes) -> bool:
        return self.payload.startswith(prefix)


@dataclass
class AssemblerStatus:
    buffered_bytes: int
    expected_bytes: int = SWEEP_BYTES

    @property
    def fraction(self) -> float:
        return min(1.0, self.buffered_bytes / max(1, self.expected_bytes))


class SweepAssembler:
    """Concatenate notify payloads until a full frame is present.

    The spec header (``AA BB CC`` + type + timestamp + length) appears only at
    the start of a frame. Later BLE packets of the same frame are raw payload.
    If the magic appears after payload has already been buffered, the partial
    frame is dropped and counted as packet loss. Headerless streams (older
    simulator tests) are still assembled as 4992-byte I/Q frames.
    """

    def __init__(self, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s
        self._buffer = bytearray()
        self._hold = b""
        self._need_header = True
        self._expected = SWEEP_BYTES
        self._last_chunk_at: float | None = None
        self._header: FrameHeader | None = None
        self.dropped_partials = 0
        self.began_new_sweep = False
        self.packet_loss = 0
        self.packet_loss_this_push = 0
        self.packet_count = 0
        self.byte_count_mismatches = 0
        self.byte_count_mismatch_this_push = 0
        self.last_sweep_header: FrameHeader | None = None
        self.last_byte_count_ok = True
        self.last_assembled_bytes = 0
        self.last_packet_type = PACKET_TYPE_SWEEP
        self.opened_packet_type: int | None = None
        self.new_headers_this_push: list[FrameHeader] = []
        self.unknown_headers_this_push: list[int] = []
        self.allow_headerless = True

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def expected_bytes(self) -> int:
        return self._expected

    def reset(self) -> None:
        self._buffer.clear()
        self._hold = b""
        self._need_header = True
        self._expected = SWEEP_BYTES
        self._last_chunk_at = None
        self._header = None
        self.began_new_sweep = False
        self.opened_packet_type = None
        self.packet_loss_this_push = 0
        self.byte_count_mismatch_this_push = 0

    def clear_stats(self) -> None:
        self.packet_loss = 0
        self.packet_loss_this_push = 0
        self.packet_count = 0
        self.dropped_partials = 0
        self.byte_count_mismatches = 0
        self.byte_count_mismatch_this_push = 0

    def _drop_partial(self, *, packet_loss: bool) -> None:
        if self._buffer:
            self.dropped_partials += 1
            if packet_loss:
                self.packet_loss += 1
                self.packet_loss_this_push += 1
        self._buffer.clear()
        self._header = None
        self._expected = SWEEP_BYTES
        self._need_header = True

    def _record_complete(self, payload: bytes, header: FrameHeader | None) -> None:
        packet_type = header.packet_type if header is not None else PACKET_TYPE_SWEEP
        if packet_type == PACKET_TYPE_SWEEP:
            ok = len(payload) == SWEEP_BYTES
            if header is not None:
                ok = ok and header.payload_bytes == SWEEP_BYTES
        else:
            ok = True
            if header is not None and header.payload_bytes > 0:
                ok = len(payload) == header.payload_bytes
            if packet_type == PACKET_TYPE_PPG:
                ok = ok and (len(payload) % PPG_BYTES_PER_SAMPLE == 0)
            elif packet_type == PACKET_TYPE_ACCEL:
                ok = ok and (len(payload) % ACCEL_BYTES_PER_SAMPLE == 0)
        self.last_sweep_header = header
        self.last_packet_type = packet_type
        self.last_assembled_bytes = len(payload)
        self.last_byte_count_ok = ok
        if not ok:
            self.byte_count_mismatches += 1
            self.byte_count_mismatch_this_push += 1
        self._header = None
        self._need_header = True
        self._expected = SWEEP_BYTES

    def _take_complete(self) -> AssembledFrame | None:
        if self._expected <= 0 or len(self._buffer) < self._expected:
            return None
        payload = bytes(self._buffer[: self._expected])
        del self._buffer[: self._expected]
        header = self._header
        packet_type = header.packet_type if header is not None else PACKET_TYPE_SWEEP
        self._record_complete(payload, header)
        return AssembledFrame(packet_type, payload, header)

    def _start_frame(self, header: FrameHeader | None, remaining: int = 0) -> None:
        self._header = header
        if header is None:
            self._expected = SWEEP_BYTES
        elif header.payload_bytes > 0:
            self._expected = header.payload_bytes
        elif header.packet_type == PACKET_TYPE_SWEEP:
            self._expected = SWEEP_BYTES
        else:
            self._expected = remaining if remaining > 0 else 0
        if self._expected < 0:
            self._expected = SWEEP_BYTES
        self._need_header = False
        self.began_new_sweep = True
        self.opened_packet_type = header.packet_type if header is not None else PACKET_TYPE_SWEEP

    def push(self, chunk: bytes, now: float) -> list[AssembledFrame]:
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
        self.new_headers_this_push = []
        self.unknown_headers_this_push = []
        complete: list[AssembledFrame] = []
        if chunk:
            self.packet_count += 1

        while stream:
            if self._need_header:
                data = bytes(stream)
                idx = data.find(PACKET_MAGIC)
                if idx < 0:
                    prefix = trailing_magic_prefix_len(data)
                    iq = data[:-prefix] if prefix else data
                    hold = data[-prefix:] if prefix else b""
                    if iq and self.allow_headerless:
                        if not self._buffer:
                            self.began_new_sweep = True
                        self._start_frame(None)
                        self._buffer.extend(iq)
                        taken = self._take_complete()
                        if taken:
                            complete.append(taken)
                            leftover = bytes(self._buffer)
                            self._buffer.clear()
                            stream[:] = leftover + hold
                            continue
                    self._hold = hold
                    stream.clear()
                    break
                if idx > 0:
                    del stream[:idx]
                    continue
                if len(data) < FRAME_HEADER_BYTES:
                    self._hold = data
                    break
                header = parse_frame_header(data)
                raw_type = data[3]
                del stream[:FRAME_HEADER_BYTES]
                if header is None:
                    continue
                if header.packet_type not in KNOWN_FRAME_TYPES:
                    self.unknown_headers_this_push.append(raw_type)
                    self.new_headers_this_push.append(header)
                    self._start_frame(header, remaining=len(stream))
                    continue
                self.new_headers_this_push.append(header)
                self._start_frame(header, remaining=len(stream))
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
