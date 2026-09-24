# Orbio BioSensor BLE

Connect over BLE and capture RF sweep I/Q, PPG, and accelerometer data onto this PC.

GitHub: [SanaComm/Orbio_BioSensor_BLE](https://github.com/SanaComm/Orbio_BioSensor_BLE)

Spec: [`docs/Orbio Biosensor BLE 26-09-11.pdf`](docs/Orbio%20Biosensor%20BLE%2026-09-11.pdf) (v2).

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

Without hardware, add `--simulate`. The fake `Orbio-sim001` device is found automatically. After connect, send `10` from **Control** for I/Q sweeps, `20` for PPG, or `30` for accel. Frames are written under `data\`.

## How the remote behaves

From the v2 spec:

- Advertised name: `Orbio-` plus 6 characters from the Bluetooth ID
- The device advertises just before a sweep. Default is 30 s sweep, 10 min sleep, so keep scanning until it appears
- The app starts in **Scanning**. **Pause Scanning** / **Resume Scanning** stop or restart that watch. After connect the status shows **Connected**, and Sweep Data notifications are enabled. The remote only transfers a frame if notifications stay enabled
- Sweep, PPG, and accel frames share the same notify characteristic. A 10-byte header appears only at the start of each frame: `AA BB CC`, 1-byte type, 4-byte timestamp, 2-byte length. Type `1` is sweep, `2` is PPG, `3` is accel
- Sweep length is little-endian (4992 I/Q bytes). PPG and accel length on the remote is big-endian (typical PPG payload **216**, typical accel **144–162**)
- One sweep is 4992 bytes: 32 I/Q samples × 39 frequencies (700–1080 MHz, 10 MHz steps), 16-bit I and 16-bit Q, split across BLE packets of up to 240 bytes
- One PPG frame is typically 72 samples of 3 bytes (216 bytes) and usually fits in one BLE packet with the header. Each sample’s top nibble is the channel tag 1–8; the remaining 20 bits are a signed big-endian value
- One accel frame is typically 24–27 XYZ samples, 2 bytes per axis, little-endian, and also fits in one packet with the header

## What gets saved

Each completed frame writes three files in `data\`:

| Kind | Stem | CSV columns |
| --- | --- | --- |
| Sweep | `sweep_*` | `frequency_mhz,sample_index,i,q` |
| PPG | `ppg_*` | `channel,sample_index,value` |
| Accel | `accel_*` | `sample_index,x,y,z` |

`.bin` is the raw payload. `.json` is capture metadata. **Clear Data** deletes those capture files and leaves plot memories alone.

Each plot also has **five Save / Recall slots** of its own under `data/memories/` (gitignored):

| Plot | Files | What is stored |
| --- | --- | --- |
| I/Q | `mem1.json` … `mem5.json` | The I/Q points currently on the plot |
| PPG | `ppg_mem1.json` … `ppg_mem5.json` | The eight channel traces currently on the plot |
| Accel | `accel_mem1.json` … `accel_mem5.json` | The X/Y/Z traces currently on the plot |

The slot dropdown, **Save**, and **Recall** sit in the right-hand column on all three plots. Switching modes reloads that plot’s five slots, so I/Q Mem 1 is never mixed with PPG Mem 1.

## How the plot works

The lower-right window is **one mode at a time**. Send `10` / `20` / `30` to start capture on the remote and switch the plot. Incoming headers also switch it: type `1` → I/Q, type `2` → PPG, type `3` → accel. While PPG or accel is selected, later sweep frames are still saved but do not replace that plot. **Enter** in the Control field sends the command (same as **Send**), only while **Connected**.

**I/Q.** The plot starts **blank**. Each completed 4992-byte sweep is 39 frequencies × 32 samples = 1248 I/Q points. **# Sweeps Shown** (default **10**) overlays the last N completed frames. Send `10` to start a sweep set (that also clears the overlay). Send `11` to stop.

- **I** is the horizontal axis, **Q** is the vertical axis. Color runs from 700 MHz (blue) to 1080 MHz (yellow).
- The crosshair is at **Center I** / **Center Q**. Those fill from the data until you type values. Both axes share the same autoscale around that center.
- Hover the I/Q or Mag / Phase plot, or **Freq**, and use the mouse wheel to highlight one frequency (700–1080 MHz, 10 MHz steps). Click **Freq** for All.
- **Mag / Phase** switches to amplitude and phase versus frequency, using the same **Center I** / **Center Q** as the constellation (not 0,0). Each I/Q sample is drawn as a point (no connecting lines): magnitude = √((I−centerI)²+(Q−centerQ)²) and phase = atan2(Q−centerQ, I−centerI) in degrees on a fixed ±180° axis. Positive phase is teal; negative phase is orange, on both Mag and Phase. The same **# Sweeps Shown** overlay applies. **S / E** is a third view: at each frequency the shown samples are split into two I/Q clusters by the principal axis through Center I/Q (not a 0° phase cut). The two complex means Z̄₊ and Z̄₋ are formed from raw I+jQ, then S(f)=(Z̄₊−Z̄₋)/2 is the signal (reversing path) and E(f)=(Z̄₊+Z̄₋)/2 is the error (common/DC path). The sign of S is chosen so ∠S is 0°–180°. ∠E stays ±180° (true offset direction). With a frequency selected, |S| and ∠S at that marker are shown next to **Freq**. A frequency is drawn only if both clusters are populated and sit on opposite sides of the center. Overlay several sweeps so both LO phase states are present. **I / Q** returns to the constellation.

**PPG.** Eight stacked traces (channels 1–8), each with its own Y autoscale. The X axis is a 500-sample scrolling window.

**Accel.** Three stacked traces (X, Y, Z) with the same 500-sample window.

On every plot, **Save** copies what is on screen into the selected slot for that mode, and **Recall** draws that slot back. **Clear Plot** empties the on-screen drawing only. Stream shows **Packet Loss # = lost / total BLE packets**. **Clear Stats** zeros packet loss, the Stream card, and the sweep/PPG/accel counts so the next capture starts at #1. Files already on disk are kept.

## Controls

Set Parameters is a comma-separated ASCII write. Sweep commands start at **10**, PPG at **20**, accel at **30**, misc at **40**. Click a row to fill the box, then **Send** or press **Enter**.

| Command | Example |
| --- | --- |
| Start sweep | `10` |
| Stop sweep | `11` |
| I/Q offsets (mV) | `12,-100,200` |
| Frequency (MHz) | `13,830` |
| LNA, VGA | `15,1,7` |
| Sweep s, interval s | `16,30,600` |
| Start PPG | `20` |
| Stop PPG | `21` |
| Start accel | `30` |
| Stop accel | `31` |
| Print samples | `40,1` / `40,0` |
