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

That starts a standalone desktop window. A Desktop shortcut named **Orbio BioSensor BLE** launches the same thing.

`--browser` opens the old tab at `http://127.0.0.1:8765/` instead. `--simulate` uses a fake device.

Without hardware, add `--simulate`. The fake `Orbio-sim001` device is found automatically. After connect, send `1` from **Control** to start a sweep. Fake 4992-byte frames are written under `data\`.

## How the remote behaves

From `R:\My Documents\Orbio Health\BioSensor 2026\Orbio Biosensor BLE.pdf`:

- Advertised name: `Orbio-` plus 6 characters from the Bluetooth ID
- The device advertises just before a sweep. Default is 30 s sweep, 10 min sleep, so keep scanning until it appears
- The app starts in **Scanning**. **Pause Scanning** / **Resume Scanning** stop or restart that watch. After connect the status shows **Connected**, and Sweep Data notifications are enabled. The remote only transfers a sweep if notifications stay enabled
- One sweep is 4992 bytes: 32 I/Q samples × 39 frequencies (700–1080 MHz, 10 MHz steps), 16-bit I and 16-bit Q, split across BLE packets of up to 240 bytes. A 10-byte header (`AA BB CC`, type, timestamp, length) appears only at the start of each sweep
- PPG and accelerometer formats are still TBD in the spec, so they are not captured yet

## What gets saved

Each completed sweep writes three files in `data\`:

- `.bin` — raw 4992 bytes
- `.csv` — `frequency_mhz,sample_index,i,q`
- `.json` — capture metadata

The CSV layout assumed for Phase 1 (the PDF figure was not machine-readable) is: for each frequency, 32 little-endian signed int16 I/Q pairs.

## How the I/Q plot works

The plot starts **blank**. Each completed 4992-byte sweep is 39 frequencies × 32 samples = 1248 I/Q points. **# Sweeps Shown** (default **10**) overlays the last N completed frames. Send `1` from Control to start a sweep set (that also clears the overlay). Send `2` to stop.

- **I** is the horizontal axis, **Q** is the vertical axis. Color runs from 700 MHz (blue) to 1080 MHz (yellow).
- The crosshair is at **Center I** / **Center Q**. Those fill from the data until you type values. Both axes share the same autoscale around that center.
- Hover the plot or **Freq** and use the mouse wheel to highlight one frequency (700–1080 MHz, 10 MHz steps). Click **Freq** for All.
- Stream shows **Packet Loss # = lost / total BLE packets**. **Clear Stats** zeros that count.

**Clear Plot** empties the on-screen drawing only. **Clear Data** deletes captured `data/sweep_*` files and leaves the five memory slots. **Save** / **Recall** store a copy of whatever is on the plot in a slot.

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
