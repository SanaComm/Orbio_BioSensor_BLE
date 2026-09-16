"""BLE interface constants from Orbio Biosensor BLE 26-09-11.pdf (v2)."""

from __future__ import annotations

from dataclasses import dataclass

DEVICE_NAME_PREFIX = "Orbio-"


def is_orbio_advertised_name(name: str | None) -> bool:
    return bool(name and "orbio" in name.lower())

DATA_SERVICE_UUID = "87fa031b-ba2d-48c2-9a29-3a627f913b4a"
SWEEP_DATA_UUID = "abacd824-160e-4c85-ad02-67483ad42060"

CONTROL_SERVICE_UUID = "97e5c533-f246-4db6-ba82-dc9e97c58d3b"
SET_PARAMETERS_UUID = "f48a06b7-9f70-48bb-ba71-2e5edc537e59"
REPORT_FW_ID_UUID = "66b45f2c-11ee-4687-8aee-8da8c1155384"
REPORT_PARAMETERS_UUID = "a2904127-b96c-4c5c-9d39-6c209cd4e92c"

MAX_NOTIFY_BYTES = 240
N_FREQUENCIES = 39
N_SAMPLES = 32
BYTES_PER_IQ = 4  # int16 I + int16 Q
SWEEP_BYTES = N_SAMPLES * BYTES_PER_IQ * N_FREQUENCIES  # 4992
FREQ_MHZ = tuple(range(700, 1081, 10))  # 700..1080 step 10 MHz

PPG_CHANNELS = 8
PPG_BYTES_PER_SAMPLE = 3
PPG_TYPICAL_SAMPLES = 72
PPG_TYPICAL_BYTES = PPG_TYPICAL_SAMPLES * PPG_BYTES_PER_SAMPLE  # 216
ACCEL_AXES = 3
ACCEL_BYTES_PER_SAMPLE = 6  # int16 X, Y, Z little-endian
ACCEL_TYPICAL_SAMPLES = 25
TIME_WINDOW_SAMPLES = 500

# Start-of-frame header (spec figure). Appears once at the beginning of a
# frame, not on later BLE notify packets of the same frame.
#   3 magic + 1 type + 4 timestamp + 2 packet length  = 10 bytes
PACKET_MAGIC = bytes((0xAA, 0xBB, 0xCC))
PACKET_TYPE_SWEEP = 1
PACKET_TYPE_PPG = 2
PACKET_TYPE_ACCEL = 3
FRAME_HEADER_BYTES = 10

KNOWN_FRAME_TYPES = {PACKET_TYPE_SWEEP, PACKET_TYPE_PPG, PACKET_TYPE_ACCEL}


def normalize_packet_type(packet_type: int) -> int:
    """Map binary 1/2/3 and ASCII '1'/'2'/'3' to the spec frame types."""
    if packet_type in KNOWN_FRAME_TYPES:
        return packet_type
    mapped = {ord("1"): PACKET_TYPE_SWEEP, ord("2"): PACKET_TYPE_PPG, ord("3"): PACKET_TYPE_ACCEL}
    return mapped.get(packet_type, packet_type)

assert len(FREQ_MHZ) == N_FREQUENCIES
assert SWEEP_BYTES == 4992
assert FRAME_HEADER_BYTES == 10

# Phase 1: 39 frequencies, 32 samples of little-endian signed int16 I then Q.
IQ_BYTEORDER = "little"
IQ_SIGNED = True


@dataclass(frozen=True)
class ControlCommand:
    code: int
    name: str
    example: str
    help: str


CONTROL_COMMANDS = (
    ControlCommand(10, "start_sweep", "10", "Start sweep (no parameters)"),
    ControlCommand(11, "stop_sweep", "11", "Stop sweep (no parameters)"),
    ControlCommand(12, "iq_offsets", "12,-100,200", "I/Q offsets in mV, each -775..775"),
    ControlCommand(13, "set_frequency", "13,830", "Frequency in MHz, 700..1080"),
    ControlCommand(14, "run_calibration", "14", "Run calibration (result handling TBD)"),
    ControlCommand(15, "set_lna_vga", "15,1,7", "LNA 0..3 and VGA 0..7, each step -6 dB"),
    ControlCommand(16, "sweep_sleep_timing", "16,60,600", "Sweep seconds, interval seconds (10..6000)"),
    ControlCommand(17, "set_trigger_frequency", "17,0", "TP1 pulse: 0=all frequencies, or 1..20"),
    ControlCommand(20, "start_ppg", "20", "Start PPG sampling"),
    ControlCommand(21, "stop_ppg", "21", "Stop PPG sampling"),
    ControlCommand(30, "start_accel", "30", "Start accelerometer sampling"),
    ControlCommand(31, "stop_accel", "31", "Stop accelerometer sampling"),
    ControlCommand(40, "print_samples", "40,1", "Print samples: 40,1 enable, 40,0 disable"),
)


def encode_parameter_write(command: str) -> bytes:
    """Encode a Set Parameters write as comma-separated ASCII."""
    payload = command.strip()
    if not payload:
        raise ValueError("Control command is empty")
    return payload.encode("ascii")


@dataclass(frozen=True)
class FrameHeader:
    packet_type: int
    timestamp: int
    length: int
    payload_bytes: int


def encode_frame_header(
    packet_type: int,
    *,
    timestamp: int = 0,
    payload_bytes: int,
) -> bytes:
    """Build the 10-byte start-of-frame header. Length is little-endian payload size."""
    if not 0 <= packet_type <= 0xFF:
        raise ValueError("packet type must fit in 1 byte")
    if not 0 <= timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp must fit in 4 bytes")
    if not 0 <= payload_bytes <= 0xFFFF:
        raise ValueError("payload length must fit in 2 bytes")
    return (
        PACKET_MAGIC
        + bytes((packet_type,))
        + timestamp.to_bytes(4, "little")
        + payload_bytes.to_bytes(2, "little")
    )


def encode_sweep_header(*, timestamp: int = 0, payload_bytes: int = SWEEP_BYTES) -> bytes:
    """Build the 10-byte start-of-sweep header. Length is little-endian I/Q size."""
    return encode_frame_header(PACKET_TYPE_SWEEP, timestamp=timestamp, payload_bytes=payload_bytes)


def _choose_length_field(le: int, be: int) -> int:
    plausible = {SWEEP_BYTES, SWEEP_BYTES + FRAME_HEADER_BYTES}
    if le in plausible:
        return le
    if be in plausible:
        return be
    return le


def iq_payload_bytes(length_field: int) -> int:
    """Map the header length field to I/Q bytes after the header."""
    if length_field == SWEEP_BYTES + FRAME_HEADER_BYTES:
        return SWEEP_BYTES
    if length_field > 0:
        return length_field
    return SWEEP_BYTES


def _plausible_short_frame_length(length: int, packet_type: int) -> bool:
    """PPG/accel frames fit in one or a few BLE packets, not tens of KB."""
    if packet_type == PACKET_TYPE_PPG:
        unit = PPG_BYTES_PER_SAMPLE
    elif packet_type == PACKET_TYPE_ACCEL:
        unit = ACCEL_BYTES_PER_SAMPLE
    else:
        return False
    if length <= 0 or length > MAX_NOTIFY_BYTES * 4:
        return False
    if length % unit == 0:
        return True
    return length >= FRAME_HEADER_BYTES and (length - FRAME_HEADER_BYTES) % unit == 0


def _choose_short_frame_length(le: int, be: int, packet_type: int) -> int:
    """Sweeps use little-endian length; PPG/accel firmware sends it big-endian.

    Bytes ``00 D8`` are 216 BE (typical PPG payload) but 55296 LE, which is what
    made the assembler wait forever and treat the next header as packet loss.
    """
    le_ok = _plausible_short_frame_length(le, packet_type)
    be_ok = _plausible_short_frame_length(be, packet_type)
    if be_ok and not le_ok:
        return be
    if le_ok and not be_ok:
        return le
    if be_ok and le_ok:
        return be if be <= MAX_NOTIFY_BYTES and le > MAX_NOTIFY_BYTES else le
    if 0 < be <= MAX_NOTIFY_BYTES:
        return be
    if 0 < le <= MAX_NOTIFY_BYTES:
        return le
    return le if le else be


def payload_bytes_from_length(length_field: int, packet_type: int) -> int:
    """Map the header length field to payload bytes after the header."""
    if packet_type == PACKET_TYPE_SWEEP:
        return iq_payload_bytes(length_field)
    if length_field <= 0:
        return 0
    unit = PPG_BYTES_PER_SAMPLE if packet_type == PACKET_TYPE_PPG else ACCEL_BYTES_PER_SAMPLE
    if packet_type not in {PACKET_TYPE_PPG, PACKET_TYPE_ACCEL}:
        return length_field
    without_header = length_field - FRAME_HEADER_BYTES
    if without_header > 0 and without_header % unit == 0 and length_field % unit != 0:
        return without_header
    return length_field


def parse_frame_header(data: bytes) -> FrameHeader | None:
    if len(data) < FRAME_HEADER_BYTES or not data.startswith(PACKET_MAGIC):
        return None
    packet_type = normalize_packet_type(data[3])
    timestamp = int.from_bytes(data[4:8], "little", signed=False)
    le = int.from_bytes(data[8:10], "little", signed=False)
    be = int.from_bytes(data[8:10], "big", signed=False)
    if packet_type == PACKET_TYPE_SWEEP:
        length = _choose_length_field(le, be)
    else:
        length = _choose_short_frame_length(le, be, packet_type)
    return FrameHeader(packet_type, timestamp, length, payload_bytes_from_length(length, packet_type))


def trailing_magic_prefix_len(data: bytes) -> int:
    """Bytes at the end of ``data`` that could be the start of PACKET_MAGIC."""
    max_n = min(len(PACKET_MAGIC) - 1, len(data))
    for n in range(max_n, 0, -1):
        if PACKET_MAGIC.startswith(data[-n:]):
            return n
    return 0


@dataclass(frozen=True)
class IqSample:
    frequency_mhz: int
    sample_index: int
    i: int
    q: int


@dataclass(frozen=True)
class PpgSample:
    channel: int
    sample_index: int
    value: int


@dataclass(frozen=True)
class AccelSample:
    sample_index: int
    x: int
    y: int
    z: int


def parse_sweep(payload: bytes) -> list[IqSample]:
    """Parse one 4992-byte sweep into frequency/sample I/Q rows."""
    if len(payload) != SWEEP_BYTES:
        raise ValueError(f"Sweep payload is {len(payload)} bytes, expected {SWEEP_BYTES}")

    samples: list[IqSample] = []
    offset = 0
    for freq in FREQ_MHZ:
        for sample_index in range(N_SAMPLES):
            i = int.from_bytes(payload[offset : offset + 2], IQ_BYTEORDER, signed=IQ_SIGNED)
            q = int.from_bytes(payload[offset + 2 : offset + 4], IQ_BYTEORDER, signed=IQ_SIGNED)
            offset += BYTES_PER_IQ
            samples.append(IqSample(freq, sample_index, i, q))
    return samples


def encode_ppg_sample(channel: int, value: int) -> bytes:
    """Pack one PPG sample: 4-bit tag (1..8) + signed 20-bit big-endian value."""
    if not 1 <= channel <= PPG_CHANNELS:
        raise ValueError(f"PPG channel must be 1..{PPG_CHANNELS}")
    raw = value & 0xFFFFF
    return bytes(
        (
            ((channel & 0xF) << 4) | ((raw >> 16) & 0xF),
            (raw >> 8) & 0xFF,
            raw & 0xFF,
        )
    )


def _sign_extend_20(raw: int) -> int:
    raw &= 0xFFFFF
    if raw & 0x80000:
        return raw - 0x100000
    return raw


def parse_ppg(payload: bytes) -> list[PpgSample]:
    """Parse 3-byte PPG samples. Tag 1..8 in the top nibble; 20-bit signed BE value."""
    if len(payload) % PPG_BYTES_PER_SAMPLE:
        raise ValueError(f"PPG payload is {len(payload)} bytes, not a multiple of {PPG_BYTES_PER_SAMPLE}")
    samples: list[PpgSample] = []
    per_channel = [0] * (PPG_CHANNELS + 1)
    for offset in range(0, len(payload), PPG_BYTES_PER_SAMPLE):
        b0, b1, b2 = payload[offset : offset + 3]
        channel = (b0 >> 4) & 0xF
        value = _sign_extend_20(((b0 & 0x0F) << 16) | (b1 << 8) | b2)
        index = per_channel[channel] if 1 <= channel <= PPG_CHANNELS else 0
        if 1 <= channel <= PPG_CHANNELS:
            per_channel[channel] += 1
        samples.append(PpgSample(channel, index, value))
    return samples


def parse_accel(payload: bytes) -> list[AccelSample]:
    """Parse little-endian int16 X,Y,Z groups."""
    if len(payload) % ACCEL_BYTES_PER_SAMPLE:
        raise ValueError(f"Accel payload is {len(payload)} bytes, not a multiple of {ACCEL_BYTES_PER_SAMPLE}")
    samples: list[AccelSample] = []
    for index, offset in enumerate(range(0, len(payload), ACCEL_BYTES_PER_SAMPLE)):
        x = int.from_bytes(payload[offset : offset + 2], "little", signed=True)
        y = int.from_bytes(payload[offset + 2 : offset + 4], "little", signed=True)
        z = int.from_bytes(payload[offset + 4 : offset + 6], "little", signed=True)
        samples.append(AccelSample(index, x, y, z))
    return samples


def parse_fw_id(payload: bytes) -> int | None:
    if not payload:
        return None
    return payload[0]


def parse_report_parameters(payload: bytes) -> dict[str, str]:
    text = payload.decode("ascii", errors="replace").strip()
    fields = [part.strip() for part in text.split(",")]
    names = ("pll_gain_db", "active_sweep_s", "interval_s")
    parsed = {"raw": text}
    for index, name in enumerate(names):
        if index < len(fields) and fields[index] != "":
            parsed[name] = fields[index]
    extra = fields[len(names) :]
    if extra:
        parsed["extra"] = ",".join(extra)
    return parsed
