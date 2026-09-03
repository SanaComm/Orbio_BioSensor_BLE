import struct

from orbio.assembler import SweepAssembler
from orbio.protocol import FREQ_MHZ, N_SAMPLES, SWEEP_BYTES, encode_parameter_write, parse_sweep
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


def test_assembler_joins_240_byte_chunks() -> None:
    payload = _fake_sweep()
    assembler = SweepAssembler(timeout_s=3)
    complete = []
    now = 0.0
    for start in range(0, SWEEP_BYTES, 240):
        complete.extend(assembler.push(payload[start : start + 240], now))
        now += 0.01
    assert complete == [payload]
    assert assembler.buffered_bytes == 0


def test_assembler_drops_stale_partial() -> None:
    assembler = SweepAssembler(timeout_s=1)
    assembler.push(b"\x00" * 100, 0.0)
    complete = assembler.push(b"\x01" * SWEEP_BYTES, 5.0)
    assert len(complete) == 1
    assert complete[0].startswith(b"\x01")
    assert assembler.dropped_partials == 1


def test_encode_command() -> None:
    assert encode_parameter_write("3,-100,200") == b"3,-100,200"
