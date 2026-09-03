# Orbio BioSensor BLE

Phase 1: connect over BLE and capture streaming sweep data onto this PC.

GitHub: [SanaComm/Orbio_BioSensor_BLE](https://github.com/SanaComm/Orbio_BioSensor_BLE)

## Run on this PC

Python 3.10+ is required. Bluetooth must be on for a real device.

```powershell
cd C:\Users\Les\Orbio_BioSensor_BLE
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m orbio.app
```

That opens `http://127.0.0.1:8765/`.

Without hardware, add `--simulate`. Scan, Connect, then **Start sweep**. Fake 4992-byte frames are written under `data\`.

## How the remote behaves

From `R:\My Documents\Orbio Health\BioSensor 2026\Orbio Biosensor BLE.pdf`:

- Advertised name: `Orbio-` plus 6 characters from the Bluetooth ID
- The device advertises just before a sweep. Default is 30 s sweep, 10 min sleep, so keep scanning until it appears
- After connect, the app enables Sweep Data notifications. The remote only transfers a sweep if notifications are enabled
- One sweep is 4992 bytes: 32 I/Q samples × 39 frequencies (700–1080 MHz, 10 MHz steps), 16-bit I and 16-bit Q, split across BLE packets of up to 240 bytes
- PPG and accelerometer formats are still TBD in the spec, so they are not captured yet

## What gets saved

Each completed sweep writes three files in `data\`:

- `.bin` — raw 4992 bytes
- `.csv` — `frequency_mhz,sample_index,i,q`
- `.json` — capture metadata

The CSV layout assumed for Phase 1 (the PDF figure was not machine-readable) is: for each frequency, 32 little-endian signed int16 I/Q pairs.

## Controls

Set Parameters is a comma-separated ASCII write. The UI can send:

| Command | Example |
| --- | --- |
| Start sweep | `1` |
| Stop sweep | `2` |
| I/Q offsets (mV) | `3,-100,200` |
| Frequency (MHz) | `4,830` |
| LNA, VGA | `6,1,7` |
| Sweep s, interval s | `7,30,600` |
| PLL gain (dB) | `9,10` |

Phase 2 can decide how to plot or analyze the captured I/Q.
