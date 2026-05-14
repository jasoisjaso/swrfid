# swrfid

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Pure Python](https://img.shields.io/badge/pure-python-orange)](https://pypi.org/)

Pure-Python driver for the **SW family of UHF RFID readers** — YanPoDo,
Sunwell, Daily-RFID and other OEM units that ship with the
`SWComApi.dll` / `SWHidApi.dll` / `SWNetClientApi.dll` SDK. Covers the
**RU5100** (desktop, USB-powered), **RU5300** (mid-range, 2–5 m), and
**RU5500** (long-range, multi-antenna) classes that all share the same
wire protocol.

The vendor ships a closed-source Windows DLL with no public Python
bindings and a sample app (`ReaderSoftV4.2`) that is Windows-only.
This is a clean re-implementation of the **documented** wire protocol
in pure Python, so the same code runs on Linux, Windows, and macOS with
no native dependencies.

---

## Features

- **Tag inventory** — both active-mode (reader broadcasts continuously)
  and on-demand command mode
- **Read and write tag memory** — all four EPC Gen2 banks
  (Reserved / EPC / TID / User) with optional 32-bit access password
- **Re-program tags** — change a tag's EPC ID for asset tracking
- **RF power control** — per-model max (5100: ~17 dBm, 5300: ~26 dBm,
  5500: ~30 dBm; bench-verify the mapping)
- **Region / frequency setup** — 18 countries documented, easy
  `set_region('US')`
- **Work modes** — Active broadcast, Answer (poll), Trigger
- **Antenna status** — for multi-antenna readers
- **Module health, system info, relay control**
- **Cross-platform** — Linux, Windows, macOS. Auto-detects FTDI USB
  serial port.
- **CLI included** — `swrfid info`, `swrfid inventory`,
  `swrfid listen`, …
- **No native deps** — pure Python over `pyserial`. No DLLs, no SDK.

## What's not supported

These aren't in the documented protocol and aren't exposed by the
vendor DLL either, so they probably aren't in the firmware:

- EPC Gen2 **Lock / Kill / BlockPermalock** commands.
- Granular per-tag error reporting — firmware returns binary
  success / failed only.
- **Firmware updates** — vendor's `ReaderSoftV4.2` GUI tool only.
- **On-device Mask filter** — lives in an opaque "Special params"
  blob; use the vendor Windows tool to program it.
- **WiFi / RJ45 credentials** — opaque "Net params" blob; same caveat.

---

## Installation

```bash
pip install swrfid
```

Until the package is published to PyPI, install directly from GitHub:

```bash
pip install git+https://github.com/jasoisjaso/swrfid.git
```

Or for development:

```bash
git clone https://github.com/jasoisjaso/swrfid.git
cd swrfid
pip install -e .
```

### Linux (Ubuntu / Debian / Mint / Pop / Raspberry Pi OS)

The reader appears as an FTDI USB-serial device (`/dev/ttyUSB0`).
Your user needs permission to read it:

```bash
sudo usermod -aG dialout $USER
newgrp dialout            # or log out + back in
```

Verify:

```bash
swrfid list-ports
```

You should see an entry with VID:PID `0403:6001` and FTDI `yes`.

### Windows

Plug in the reader. Windows 10/11 generally ships with the FTDI driver
and will detect a new COM port (e.g. `COM3`). If it doesn't, install
the FTDI VCP driver from <https://ftdichip.com/drivers/vcp-drivers/>.

```cmd
swrfid list-ports
swrfid --port COM3 info
```

### macOS

```bash
swrfid list-ports
swrfid --port /dev/cu.usbserial-XXXX info
```

If the port doesn't appear, install the FTDI VCP driver from
<https://ftdichip.com/drivers/vcp-drivers/>.

---

## Easiest path — desktop GUI

Don't want to learn shell commands? After installing, just run:

```bash
swrfid-gui
```

This opens a small cross-platform window (Linux / Windows / macOS) where you can:

- **Auto-detect** the reader and **connect with one click**
- Run a **one-shot inventory** or **live-listen** for tags
- Adjust **region, RF power, and work mode** with sliders / dropdowns
- Safely **re-program a tag's EPC** (with a confirmation dialog and read-back verification)
- See live **tag counts, antenna numbers, and RSSI** in a sortable table

The GUI uses `tkinter`, which ships with the official Python installer on
Windows and macOS. On Linux distros that strip it out, install once with:

```bash
sudo apt install python3-tk            # Debian / Ubuntu / Mint / Pop / Raspbian
sudo dnf install python3-tkinter       # Fedora / RHEL
```

Everything below is for users who prefer scripting / automation. The GUI
talks to the same protocol module as the CLI and Python API.

---

## Quick start — command line (60 seconds)

```bash
# 1. Install
pip install swrfid

# 2. Plug in the reader. Auto-detect should work.
swrfid list-ports

# 3. Read the reader's serial number
swrfid info

# 4. Watch tags arrive in real time for 10 seconds
swrfid listen --start-read --seconds 10
```

You should see every tag in the reader's field logged per scan.

---

## Python API

### Auto-detect the port

```python
from swrfid import RFIDReader, find_readers

ports = find_readers()
if not ports:
    raise SystemExit("No reader found. Plug it in or run `swrfid list-ports`.")

with RFIDReader(ports[0]) as r:
    print(r.system_info())
```

### Read tags (one-shot)

```python
from swrfid import RFIDReader

with RFIDReader('/dev/ttyUSB0') as r:
    for tag in r.inventory():
        print(tag.epc_hex, 'rssi=0x%02X' % tag.rssi)
```

### Read tags (continuous active mode)

```python
from swrfid import RFIDReader
import time

def on_tags(dev_sn, tags):
    for t in tags:
        print(t.epc_hex)

with RFIDReader('/dev/ttyUSB0') as r:
    r.start_active_listener(on_tags)
    r.start_read()
    time.sleep(60)            # listen for 1 minute
    r.stop_read()
```

### Re-program a tag's EPC

```python
from swrfid import RFIDReader, MemBank

NEW_EPC = bytes.fromhex('E2 00 12 34 56 78 9A BC DE F0 11 22'.replace(' ', ''))  # 12 bytes

with RFIDReader('/dev/ttyUSB0') as r:
    # Put ONE tag in the field, then write at EPC bank, word offset 2
    r.write_tag(MemBank.EPC, word_addr=2, payload=NEW_EPC)

    # Verify
    print(r.read_tag(MemBank.EPC, 2, 6).hex().upper())
```

### Read TID (factory-unique tag identifier)

```python
from swrfid import RFIDReader, MemBank

with RFIDReader('/dev/ttyUSB0') as r:
    tid = r.read_tag(MemBank.TID, word_addr=0, word_len=6)  # 12 bytes / 96 bits
    print('TID:', tid.hex().upper())
```

### Configure the reader

```python
from swrfid import RFIDReader, WorkMode

with RFIDReader('/dev/ttyUSB0') as r:
    r.set_region('US')          # Or EU, CN, KR, AU, JP, ...
    r.set_rf_power(0x1A)        # 26 dBm-byte on a 5300
    r.set_work_mode(WorkMode.ANSWER)
    r.set_beep(True)
```

### Module health & antenna status

```python
from swrfid import RFIDReader

with RFIDReader('/dev/ttyUSB0') as r:
    print('Module OK:', r.check_module())
    for ant, present in r.antenna_status().items():
        print('Ant %2d: %s' % (ant, 'present' if present else '-'))
```

---

## CLI reference

Every subcommand supports `--port`, `--baud`, `--timeout`, `--verbose`,
and `--dry-run` (build the frame but don't open the port). If `--port`
is omitted, the driver auto-detects an FTDI device.

```
swrfid list-ports                          # diagnose USB
swrfid info                                # softver / hwver / SN
swrfid check-module                        # module health
swrfid antenna                             # antenna bitmap

swrfid inventory                           # one-shot scan
swrfid listen --start-read --seconds 10    # continuous active mode

swrfid set-power 0x1A                      # 26 dBm-byte (5300 max)
swrfid set-region US                       # US, EU, CN, KR, AU, JP, ...
swrfid read-freq                           # show current region
swrfid set-mode ACTIVE                     # ACTIVE | ANSWER | TRIGGER
swrfid start-read                          # start active broadcasting
swrfid stop-read

swrfid read-tag EPC 2 6                    # read 6 words from EPC bank
swrfid write-tag EPC 2 001122334455...     # write bytes to EPC bank
swrfid write-epc E200123456789ABCDEF01122  # re-program 96-bit EPC (safety-checked)

swrfid relay on                            # close relay
swrfid relay off                           # release relay

swrfid get-param RF_POWER                  # read named param
swrfid set-param RF_POWER 0x14             # set named param

swrfid raw 0x10                            # send arbitrary command + data
```

Every subcommand also works with `--dry-run` so you can sanity-check
the bytes without hardware:

```bash
swrfid --dry-run inventory
# Would send 7 bytes: 53570003FF0153
#   Decoded: HEAD=5357 LEN=3 ADDR=0xFF CMD=0x01 DATA= CKSUM=0x53
```

---

## Model reference

| Model | Form factor | Antennas | Max RF power | Typical use |
|---|---|---|---|---|
| RU5100 | USB desktop | 1 internal | 17 dBm (0x11) | Tag programming, ID lookup |
| RU5300 | Integrated | 1–2 external | 26 dBm (0x1A) | Mid-range portal, access control |
| RU5500 | Module + multi-antenna | up to 16 | 30 dBm (0x1E) | Long-range warehouse / dock |

All three share the same wire protocol and DLL API surface. The RF
power byte → dBm mapping is **not** rigorously documented — bench
verify for your specific unit. See [`docs/MODELS.md`](docs/MODELS.md)
for more detail.

---

## Documentation

| File | Purpose |
|---|---|
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Full wire-protocol reference: frame format, checksum, all 26 opcodes, region table, parameter struct, known manual bugs |
| [`docs/MODELS.md`](docs/MODELS.md) | Per-model power, antenna, regulatory notes; how to identify your model |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common setup problems and fixes for Linux / Windows / macOS |
| [`examples/`](examples/) | Runnable scripts for each common task |

---

## Troubleshooting at a glance

| Symptom | Likely cause | Fix |
|---|---|---|
| `PermissionError` opening `/dev/ttyUSB0` | User not in `dialout` group | `sudo usermod -aG dialout $USER && newgrp dialout` |
| No COM port on Windows | FTDI driver missing | Install FTDI VCP driver |
| `swrfid list-ports` empty | Reader unplugged or USB cable | Check `lsusb` (Linux) / Device Manager (Windows) |
| `swrfid info` times out | Wrong baud or device address | Default is 115200 8N1, addr 0xFF; try `--baud 9600` |
| Tag count = 0 | RF power too low, region wrong, antenna fault | `set-power 0x1A`, `set-region US`, check `antenna` |
| Garbage / checksum errors | Cable noise, hub issue | Powered USB hub, shorter cable |

Full version in [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

## Compatibility

Confirmed working with:

- YanPoDo RU5305 (FTDI 0403:6001, firmware sw=1.4 hw=1.1)

Reported compatible (community, not yet independently verified):

- YanPoDo RU5100, RU5500 series
- Sunwell SW1620 family
- Daily RFID DL920 series
- Several unbranded OEM units sold on Aliexpress under the
  "SW protocol" / "SWComApi" label

If your reader works (or doesn't), please open an issue with the
`swrfid info` output so we can extend this list.

---

## License

MIT — see [`LICENSE`](LICENSE). Free for commercial and private use.

This driver was written from the vendor's published protocol manual
("UHF RFID Reader User's Manual v1.9"). The vendor's DLLs are neither
used nor redistributed. The author is not affiliated with YanPoDo or
any other SW-protocol reader vendor.

---

## Contributing

Bug reports and PRs welcome. Especially valuable:

- Captures of the opaque "Special params" / "Net params" blobs from
  `ReaderSoftV4.2` (so we can decode the Mask filter and WiFi/RJ45
  config bytes)
- Confirmation of behaviour on RU5100 and RU5500 hardware
- TCP transport wrapper (currently only USB serial)
- More region presets (the manual lists 18; firmware may support more)
