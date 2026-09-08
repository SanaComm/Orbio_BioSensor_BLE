import struct

from orbio.assembler import SweepAssembler
from orbio.protocol import (
    FREQ_MHZ,
    FRAME_HEADER_BYTES,
    MAX_NOTIFY_BYTES,
    N_SAMPLES,
    PACKET_MAGIC,
    SWEEP_BYTES,
    encode_parameter_write,
    encode_sweep_header,
    is_orbio_advertised_name,
    parse_frame_header,
    parse_sweep,
)
from orbio.radio import _fake_sweep


def test_sweep_size_and_frequency_count() -> None:
    payload = _fake_sweep()
    assert len(payload) == SWEEP_BYTES
    samples = parse_sweep(payload)
    assert len(samples) == 32 * 39
    assert samples[0].frequency_mhz == 700
    assert samples[-1].frequency_mhz == 1080
    assert samples[32].frequency_mhz == 710
    assert samples[0].sample_index == 0
    assert samples[31].sample_index == 31


def test_parse_round_trip_known_bytes() -> None:
    i, q = -100, 200
    chunk = struct.pack("<hh", i, q) * (N_SAMPLES * len(FREQ_MHZ))
    samples = parse_sweep(chunk)
    assert samples[0].i == i
    assert samples[0].q == q


def test_sweep_header_is_ten_bytes() -> None:
    header = encode_sweep_header(timestamp=0x01020304, payload_bytes=SWEEP_BYTES)
    assert header[:3] == PACKET_MAGIC
    assert header[3] == 1
    parsed = parse_frame_header(header)
    assert parsed is not None
    assert parsed.packet_type == 1
    assert parsed.timestamp == 0x01020304
    assert parsed.length == SWEEP_BYTES
    assert parsed.payload_bytes == SWEEP_BYTES
    assert len(header) == FRAME_HEADER_BYTES


def test_header_length_5002_means_4992_iq_bytes() -> None:
    header = encode_sweep_header(payload_bytes=SWEEP_BYTES + FRAME_HEADER_BYTES)
    parsed = parse_frame_header(header)
    assert parsed is not None
    assert parsed.payload_bytes == SWEEP_BYTES


def test_assembler_joins_240_byte_chunks() -> None:
    payload = _fake_sweep()
    assembler = SweepAssembler(timeout_s=3)
    complete = []
    now = 0.0
    for start in range(0, SWEEP_BYTES, 240):
        complete.extend(assembler.push(payload[start : start + 240], now))
        if start == 0:
            assert assembler.began_new_sweep
        else:
            assert not assembler.began_new_sweep
        now += 0.01
    assert complete == [payload]
    assert assembler.buffered_bytes == 0
    assert assembler.last_byte_count_ok
    assert assembler.last_assembled_bytes == SWEEP_BYTES


def test_assembler_marks_new_sweep_after_stale_partial() -> None:
    assembler = SweepAssembler(timeout_s=1)
    assembler.push(b"\x00" * 100, 0.0)
    assert assembler.began_new_sweep
    assembler.push(b"\x01" * 50, 0.2)
    assert not assembler.began_new_sweep
    assembler.push(b"\x02" * 40, 5.0)
    assert assembler.began_new_sweep
    assert assembler.dropped_partials == 1


def test_assembler_drops_stale_partial() -> None:
    assembler = SweepAssembler(timeout_s=1)
    assembler.push(b"\x00" * 100, 0.0)
    complete = assembler.push(b"\x01" * SWEEP_BYTES, 5.0)
    assert len(complete) == 1
    assert complete[0].startswith(b"\x01")
    assert assembler.dropped_partials == 1


def test_assembler_strips_leading_sweep_header() -> None:
    payload = _fake_sweep()
    header = encode_sweep_header(timestamp=99)
    assembler = SweepAssembler(timeout_s=3)
    complete = assembler.push(header + payload, 0.0)
    assert complete == [payload]
    assert assembler.packet_loss == 0
    assert assembler.buffered_bytes == 0
    assert assembler.last_sweep_header is not None
    assert assembler.last_sweep_header.timestamp == 99
    assert assembler.last_byte_count_ok


def test_assembler_header_only_on_first_ble_packet() -> None:
    payload = _fake_sweep()
    frame = encode_sweep_header() + payload
    assembler = SweepAssembler(timeout_s=3)
    complete = []
    now = 0.0
    for start in range(0, len(frame), MAX_NOTIFY_BYTES):
        complete.extend(assembler.push(frame[start : start + MAX_NOTIFY_BYTES], now))
        now += 0.01
    assert complete == [payload]
    assert assembler.packet_loss == 0
    assert assembler.last_assembled_bytes == SWEEP_BYTES
    assert assembler.last_byte_count_ok


def test_assembler_strips_header_split_across_chunks() -> None:
    payload = _fake_sweep()
    header = encode_sweep_header()
    assembler = SweepAssembler(timeout_s=3)
    complete = []
    complete.extend(assembler.push(header[:2], 0.0))
    complete.extend(assembler.push(header[2:] + payload[:100], 0.01))
    complete.extend(assembler.push(payload[100:], 0.02))
    assert complete == [payload]
    assert assembler.packet_loss == 0


def test_assembler_counts_mid_sweep_header_as_packet_loss() -> None:
    payload = _fake_sweep()
    header = encode_sweep_header()
    assembler = SweepAssembler(timeout_s=3)
    assembler.push(payload[:200], 0.0)
    assert assembler.buffered_bytes == 200
    complete = assembler.push(header + payload, 0.01)
    assert assembler.packet_loss == 1
    assert assembler.packet_loss_this_push == 1
    assert assembler.began_new_sweep
    assert complete == [payload]
    assert assembler.buffered_bytes == 0


def test_assembler_flags_header_length_mismatch() -> None:
    payload = _fake_sweep()
    header = encode_sweep_header(payload_bytes=100)
    assembler = SweepAssembler(timeout_s=3)
    complete = assembler.push(header + payload[:100], 0.0)
    assert complete == [payload[:100]]
    assert assembler.last_assembled_bytes == 100
    assert not assembler.last_byte_count_ok
    assert assembler.byte_count_mismatches == 1


def test_assembler_packet_count_and_clear_stats() -> None:
    payload = _fake_sweep()
    assembler = SweepAssembler(timeout_s=3)
    chunks = 0
    now = 0.0
    for start in range(0, SWEEP_BYTES, 240):
        assembler.push(payload[start : start + 240], now)
        chunks += 1
        now += 0.01
    assembler.packet_loss = 2
    assert assembler.packet_count == chunks
    assembler.clear_stats()
    assert assembler.packet_count == 0
    assert assembler.packet_loss == 0
    assert assembler.dropped_partials == 0


def test_encode_command() -> None:
    assert encode_parameter_write("3,-100,200") == b"3,-100,200"


def test_orbio_name_filter() -> None:
    assert is_orbio_advertised_name("Orbio-EADB17")
    assert is_orbio_advertised_name("orbio-sim001")
    assert not is_orbio_advertised_name("Apple TV")
    assert not is_orbio_advertised_name(None)
    assert not is_orbio_advertised_name("")
