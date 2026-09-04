"""BLE interface constants from Orbio Biosensor BLE.pdf (v1)."""

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

assert len(FREQ_MHZ) == N_FREQUENCIES
assert SWEEP_BYTES == 4992

# Spec layout is not pictured in the extracted PDF. Phase 1 assumes:
# for each of 39 frequencies, 32 samples of little-endian signed int16 I then Q.
IQ_BYTEORDER = "little"
IQ_SIGNED = True


@dataclass(frozen=True)
class ControlCommand:
    code: int
    name: str
    example: str
    help: str


CONTROL_COMMANDS = (
    ControlCommand(1, "start_sweep", "1", "Start sweep (no parameters)"),
    ControlCommand(2, "stop_sweep", "2", "Stop sweep (no parameters)"),
    ControlCommand(3, "iq_offsets", "3,-100,200", "I/Q offsets in mV, each -775..775"),
    ControlCommand(4, "set_frequency", "4,830", "Frequency in MHz, 700..1080"),
    ControlCommand(5, "run_calibration", "5", "Run calibration (result handling TBD)"),
    ControlCommand(6, "set_lna_vga", "6,1,7", "LNA 0..3 and VGA 0..7, each step -6 dB"),
    ControlCommand(7, "sweep_sleep_timing", "7,60,6000", "Sweep seconds, interval seconds (10..6000)"),
    ControlCommand(8, "set_trigger_frequency", "8,0", "TP1 pulse: 0=all frequencies, or 1..20"),
    ControlCommand(9, "pll_gain", "9,10", "PLL gain 0..18 dB"),
    ControlCommand(10, "start_accel", "10", "Start accelerometer sampling"),
    ControlCommand(11, "stop_accel", "11", "Stop accelerometer sampling"),
    ControlCommand(12, "start_ppg", "12", "Start PPG sampling"),
    ControlCommand(13, "stop_ppg", "13", "Stop PPG sampling"),
)


def encode_parameter_write(command: str) -> bytes:
    """Encode a Set Parameters write as comma-separated ASCII."""
    payload = command.strip()
    if not payload:
        raise ValueError("Control command is empty")
    return payload.encode("ascii")


@dataclass(frozen=True)
class IqSample:
    frequency_mhz: int
    sample_index: int
    i: int
    q: int


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


def parse_fw_id(payload: bytes) -> int | None:
    if not payload:
        return None
    return payload[0]


def parse_report_parameters(payload: bytes) -> dict[str, str]:
    text = payload.decode("ascii", errors="replace").strip()
    fields = [part.strip() for part in text.split(",")]
    # Spec says "seven" values but lists five names. Keep raw plus known labels.
    names = ("pll_gain_db", "i_offset_mv", "q_offset_mv", "active_sweep_s", "interval_s")
    parsed = {"raw": text}
    for index, name in enumerate(names):
        if index < len(fields) and fields[index] != "":
            parsed[name] = fields[index]
    extra = fields[len(names) :]
    if extra:
        parsed["extra"] = ",".join(extra)
    return parsed
