# swrfid

[![tests](https://github.com/jasoisjaso/swrfid/actions/workflows/test.yml/badge.svg)](https://github.com/jasoisjaso/swrfid/actions/workflows/test.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Pure Python](https://img.shields.io/badge/pure-python-orange)](https://pypi.org/)

**Take your YanPoDo / SW UHF RFID reader from "what does this do?" to a
working setup in under five minutes — on Linux, Windows, or macOS, with
no vendor DLL required.**

`swrfid` is a clean re-implementation of the documented SW wire
protocol in pure Python. It works with **YanPoDo RU5100 / RU5300 /
RU5500** and OEM readers that ship with the `SWComApi.dll` /
`SWHidApi.dll` / `SWNetClientApi.dll` SDK. No closed-source binaries,
no FTDI hacks beyond what your OS already supports.

---

## Quick start — the easy path

```bash
pip install git+https://github.com/jasoisjaso/swrfid.git
swrfid-gui
```

That opens a window. Plug in the reader, click **Connect**, and click
**Inventory** or **Start Listening**. Tags appear in the table with EPC,
format (SGTIN, GRAI, etc.), antenna, RSSI, and read count.

> **Linux only:** if `swrfid-gui` complains about tkinter, run
> `sudo apt install python3-tk` (or `sudo dnf install python3-tkinter`
> on Fedora) and try again.

If you don't see your reader in the port dropdown, see the
[Hardware setup](#hardware-setup) section below.

---

## What you get

| Want to... | Use this | Notes |
|---|---|---|
| **Scan tags without writing code** | `swrfid-gui` | Tkinter window, ships with the package |
| **Run from a script / SSH session** | `swrfid` CLI | Every operation as a subcommand |
| **Integrate with Home Assistant / Node-RED** | `swrfid-mqtt` | MQTT bridge with HA auto-discovery |
| **Diagnose "it doesn't work"** | `swrfid diagnose` | Walks a 7-step checklist with suggestions |
| **Calibrate range for your setup** | `swrfid calibrate` | Power-sweep against a reference tag |
| **Decode a tag's EPC** | `swrfid decode-epc <hex>` | Identifies the GS1 scheme + parses SGTIN-96 |
| **Re-program a tag's EPC** | `swrfid-gui` "Write EPC..." or `swrfid write-epc` | Safe — single-tag check + read-back verify |
| **Build your own app** | `from swrfid import RFIDReader` | Pure-Python API with type hints |

---

## Hardware setup

The reader appears as an FTDI USB-serial device (VID:PID `0403:6001`).
`swrfid` auto-detects it on every platform.

### Linux

```bash
sudo usermod -aG dialout $USER
newgrp dialout              # or log out + back in
swrfid list-ports
```

You should see one entry with FTDI `yes`. If not, run `lsusb` to
confirm the kernel sees the device — you're looking for
`0403:6001 Future Technology Devices International, Ltd FT232 Serial`.

### Windows

Plug in the reader. Windows 10/11 usually ships with the FTDI driver
and assigns a COM port automatically. If you see "USB Serial Converter"
without a COM port in Device Manager, install the official
[FTDI VCP driver](https://ftdichip.com/drivers/vcp-drivers/).

```cmd
swrfid list-ports
swrfid --port COM3 info
```

### macOS

```bash
swrfid list-ports
swrfid --port /dev/cu.usbserial-XXXX info
```

The serial bridge requires the FTDI VCP driver on macOS. If `list-ports`
shows nothing, grab it from
[ftdichip.com](https://ftdichip.com/drivers/vcp-drivers/).

---

## The GUI in detail

`swrfid-gui` is a single tkinter window. No extra installs needed
beyond Python itself (tkinter ships with the official Python installer
on Windows and macOS; one apt-install on Linux).

**What you can do from the GUI:**

- **Auto-detect** the reader port and **connect with one click**
- Read a one-shot **inventory**, or **listen** in active mode for
  live tag streams
- Adjust **region, RF power, work mode** via dropdowns and a slider
- Apply a **dedup window** (don't log the same tag every 30 ms) and an
  **RSSI minimum** (filter out weak/distant reads)
- See live **tag counts, antenna numbers, RSSI, EPC format** in a
  sortable table — **double-click a tag** for a full EPC decode
- Safely **re-program a tag's EPC** (two confirmation dialogs, single-
  tag check, automatic read-back verification)
- **Diagnose** the connection: 7-step checklist that surfaces wrong
  region / dead RF / wrong baud / no tags / etc.
- **Calibrate** RF power against a reference tag — sweeps power bytes
  and reports the lowest reliable level for the distance you're testing
- **Decode an EPC** offline (paste hex, see scheme + fields)
- **Bridge to MQTT** for Home Assistant / Node-RED integration — see
  the [Home Assistant](#home-assistant--mqtt) section

The GUI runs the active-mode listener in a background daemon thread and
drains tag events through a tk `after(50 ms)` timer, so the UI stays
responsive even at hundreds of reads/sec.

---

## CLI tour

Every subcommand supports `--port`, `--baud`, `--addr`, `--timeout`,
`-v` (verbose), and `--dry-run` (build the frame but don't open the
port). If `--port` is omitted, an FTDI reader is auto-detected.

```bash
# Discovery
swrfid list-ports                          # see every serial port + FTDI flag
swrfid diagnose                            # full why-isn't-this-working report
swrfid info                                # softver / hwver / reader serial

# Tag inventory
swrfid inventory                           # one-shot scan
swrfid listen --start-read --seconds 10    # continuous active mode
swrfid listen --dedup 2 --rssi-min 0x40 --start-read --seconds 30

# Tag programming
swrfid write-epc E2 0012345678 9ABCDEF 01122  # 12 bytes / 96 bits

# Re-program a tag's EPC — refuses if 0 or >1 tags in field, verifies write.
swrfid read-tag TID 0 6                    # 12 bytes / 96 bits of TID
swrfid read-tag EPC 2 6                    # EPC bank words 2..7
swrfid write-tag USER 0 DEADBEEFCAFEBABE   # write 8 bytes to user memory

# Reader configuration
swrfid set-power 0x1A                      # 26 dBm-byte (5300 max)
swrfid set-region US                       # US, EU, CN, KR, AU, JP, ...
swrfid set-mode ANSWER                     # poll mode for one-shot inventory
swrfid set-mode ACTIVE                     # broadcast mode for live streams
swrfid antenna                             # multi-antenna presence bitmap
swrfid check-module                        # RF module health
swrfid relay on                            # close the on-board relay
swrfid relay off

# EPC parsing
swrfid decode-epc 3074257BF46DB64000000190 # offline — no hardware

# Calibration
swrfid calibrate --tag 3074257BF46DB64000000190 --samples 5 \\
                 --output cal-rx5300-30cm.json

# Advanced
swrfid raw 0x10                            # send any opcode
swrfid get-param RF_POWER                  # read any single parameter
swrfid set-param BEEP_ENABLE 0             # mute the reader's beeper
```

**`--dry-run` mode** is hardware-free — it prints the bytes that *would*
be sent, decoded by frame field, so you can verify protocol correctness
without plugging anything in:

```bash
$ swrfid --dry-run inventory
Would send 7 bytes: 53570003FF0153
  Decoded: HEAD=5357 LEN=3 ADDR=0xFF CMD=0x01 DATA= CKSUM=0x53
```

---

## Python API

### Connect and read tags

```python
from swrfid import RFIDReader, find_readers

port = (find_readers() or ["/dev/ttyUSB0"])[0]
with RFIDReader(port) as r:
    info = r.system_info()
    print("Reader SN:", info.serial_hex)
    for tag in r.inventory():
        print("Tag:", tag.epc_hex, "RSSI:", tag.rssi)
```

### Listen in active mode with dedup + RSSI filter

```python
from swrfid import RFIDReader
import time

def on_tags(dev_sn, tags):
    for t in tags:
        print(t.epc_hex)

with RFIDReader("/dev/ttyUSB0") as r:
    r.start_active_listener(
        on_tags,
        dedup_window=2.0,    # don't re-fire the same EPC within 2 s
        rssi_min=0x40,       # drop reads below this RSSI byte
    )
    r.start_read()
    time.sleep(60)
    r.stop_read()
```

### Decode an EPC

```python
from swrfid import describe_epc

desc = describe_epc(bytes.fromhex("3074257BF46DB64000000190"))
print(desc.scheme)                # 'sgtin-96'
if desc.sgtin is not None:
    print(desc.sgtin.gtin14)       # '10614141123459'
    print(desc.sgtin.pure_identity_uri)
    # urn:epc:id:sgtin:0614141.112345.400
```

For the long tail of EPC schemes (GRAI, GIAI, GSRN, ADI, ...) install
the dedicated [`epcpy`](https://github.com/nedap/retail-epcpy)
library — `swrfid` identifies every scheme by header byte but only
fully parses SGTIN-96 (the most common format).

### Re-program a tag's EPC

```python
from swrfid import RFIDReader, MemBank

new_epc = bytes.fromhex("E2 00 12 34 56 78 9A BC DE F0 11 22".replace(" ", ""))

with RFIDReader("/dev/ttyUSB0") as r:
    # Refuse if 0 or 2+ tags in field — single-tag write only.
    before = r.inventory()
    if len(before) != 1:
        raise SystemExit("Place exactly one tag in the field.")

    r.write_tag(MemBank.EPC, word_addr=2, payload=new_epc)
    print("Verified:", r.read_tag(MemBank.EPC, 2, 6).hex().upper())
```

### Configure the reader

```python
from swrfid import RFIDReader, WorkMode

with RFIDReader("/dev/ttyUSB0") as r:
    r.set_region("US")
    r.set_rf_power(0x1A)
    r.set_work_mode(WorkMode.ANSWER)
    r.set_beep(True)
```

### Auto-detect ports

```python
from swrfid import find_readers, list_serial_ports

ftdi_ports = find_readers()         # ["/dev/ttyUSB0"] on Linux, ["COM3"] on Win
all_ports = list_serial_ports()     # every serial port, with FTDI flag
```

---

## Home Assistant / MQTT

`swrfid-mqtt` is a long-running daemon that publishes tag reads to an
MQTT broker. It installs as a console script when you opt into the
`[mqtt]` extra:

```bash
pip install 'swrfid[mqtt] @ git+https://github.com/jasoisjaso/swrfid.git'
```

Run it pointing at your broker:

```bash
swrfid-mqtt \\
    --port /dev/ttyUSB0 \\
    --broker mqtt://homeassistant.local:1883 \\
    --topic rfid/swrfid \\
    --dedup 2.0 \\
    --ha-discovery
```

Each tag read publishes JSON to `<topic>/tags`:

```json
{
  "epc": "3074257BF46DB64000000190",
  "scheme": "sgtin-96",
  "antenna": 1,
  "rssi": 73,
  "timestamp": "2026-05-15T01:34:22.123Z",
  "reader_sn": "C3DD938E170123",
  "gtin14": "10614141123459",
  "serial": 400
}
```

`--ha-discovery` also publishes a Home Assistant MQTT-discovery config
so HA auto-creates a sensor entity for the last tag — you can then
build automations like "open the gate when the last EPC matches my
car's tag".

Or use it via the GUI: **Tools → MQTT Bridge...** opens a dialog with
broker URL, topic, dedup, RSSI minimum, and HA-discovery toggle.

---

## Diagnostics

The most common buyer experience: **"I plugged it in and nothing
happens."** Run:

```bash
swrfid diagnose
```

The checker walks 7 steps and prints a coloured report with suggestions:

```
swrfid diagnostic report
==============================
[OK]    Port discovery   Auto-detected FTDI reader on /dev/ttyUSB0.
[OK]    Open port        /dev/ttyUSB0 opened at 115200 8N1.
[OK]    System info      Reader SN C3DD938E170123 sw=1.4 hw=1.1.
[OK]    Module health    RF module reports OK.
[OK]    RF power         Power byte 0x14.
[!!]    Region           Unrecognised region bytes 0x29 0x9D.
        -> Run `swrfid set-region US` to match your area.
[..]    Work mode        WorkMode ACTIVE.
        -> For one-shot inventory tests, run `swrfid set-mode ANSWER`.
[OK]    Antenna          1 antenna(s) detected: A1.
[!!]    Inventory test   No tags detected in field.
        -> Place a known-good Gen2 tag on the antenna face and re-run.

No failures. 2 warning(s) — see suggestions above.
```

Every check has a one-line message and (where applicable) a copy-
pasteable command to fix it.

---

## Calibrating RF power for your setup

The vendor manual's RF-power-to-dBm mapping is undocumented (and has a
typo in the worked example). For accurate range tuning, calibrate
empirically with a known reference tag:

```bash
swrfid calibrate \\
    --tag 3074257BF46DB64000000190 \\
    --samples 5 \\
    --output cal-30cm.json
```

The tool sweeps power bytes 0x00 → 0x1E, runs 5 inventories per byte,
and prints a histogram of detection success rates:

```
  [ 1/31] power=0x00 [-----] 0/5  rssi=-
  [ 2/31] power=0x01 [-----] 0/5  rssi=-
  ...
  [ 9/31] power=0x08 [##---] 2/5  rssi=58.0
  [10/31] power=0x09 [####-] 4/5  rssi=62.5
  [11/31] power=0x0A [#####] 5/5  rssi=66.0
  ...

First detectable power:               0x08
Recommended power (100% reliability): 0x0A
```

Save the JSON for later — it's your reader's per-unit calibration curve
at the given test distance.

---

## Model reference

| Model | Form factor | Antennas | Max RF power | Typical use |
|---|---|---|---|---|
| **RU5100** | USB desktop | 1 internal | 17 dBm (0x11) | Tag programming, ID lookup, POS |
| **RU5300** | Integrated | 1–2 external | 26 dBm (0x1A) | Mid-range portal, access control |
| **RU5500** | Module + multi-antenna | up to 16 | 30 dBm (0x1E) | Long-range warehouse / dock |

All three share the same wire protocol and DLL API surface. Power byte
maximums are model-specific — `swrfid set-power` rejects values above
the documented maximum. See [`docs/MODELS.md`](docs/MODELS.md) for the
full breakdown including how to identify your unit empirically.

---

## What's NOT supported (and why)

These features aren't in the documented protocol and aren't exposed by
the vendor DLL either — they're almost certainly missing from the
firmware:

- **EPC Gen2 Lock / Kill / BlockPermalock** commands
- **Granular per-tag error reporting** (firmware returns binary
  success/failed only)
- **Firmware updates** (vendor's `ReaderSoftV4.2` GUI tool only)
- **On-device Mask filter** — opaque "Special params" blob; use the
  vendor Windows tool to program it, then `swrfid` reads only matching
  tags
- **WiFi / RJ45 credentials** — opaque "Net params" blob, same caveat

If you need any of these, please file an issue with the bytes captured
from `ReaderSoftV4.2` and we'll see what we can do.

---

## Documentation

| File | What's in it |
|---|---|
| [`README.md`](README.md) | This file |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Wire protocol reference — frames, checksum, opcodes, region table, parameter struct |
| [`docs/MODELS.md`](docs/MODELS.md) | Per-model power, antenna, regulatory notes |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common setup problems on Linux / Windows / macOS |
| [`examples/`](examples/) | Runnable Python scripts for each common task |

---

## Compatibility

**Confirmed working with:**

- YanPoDo RU5305 (FTDI 0403:6001, firmware sw=1.4 / hw=1.1)

**Reported compatible (community, not yet independently verified):**

- YanPoDo RU5100, RU5500 series
- Sunwell SW1620 family
- Daily RFID DL920 series
- Various unbranded "SW protocol" OEM units sold on AliExpress

If your reader works (or doesn't), please open an issue with
`swrfid info` output and we'll extend this list.

**Tested on:** Linux (Ubuntu 20.04+, Debian, Fedora, Raspberry Pi OS),
Windows 10/11, macOS 12+. CI runs every commit against Python 3.8–3.12
on all three OSes.

---

## Adjacent projects

| Project | Niche |
|---|---|
| [`epcpy`](https://github.com/nedap/retail-epcpy) | Full GS1 EPC Tag Data Standard parser for *every* scheme. Use this when SGTIN-96 isn't enough. |
| [`pyepc`](https://github.com/fulfilio/pyepc) | Another GS1 EPC toolkit in Python. |
| [`wabson/chafon-rfid`](https://github.com/wabson/chafon-rfid) | Same idea as swrfid for **Chafon** UHF readers (different protocol family). Stale, but a useful reference. |
| [`metratec/rfid-sdk-python`](https://github.com/metratec/rfid-sdk-python) | Asyncio driver for **Metratec** readers (different vendor). |
| [`nkargas/Gen2-UHF-RFID-Reader`](https://github.com/nkargas/Gen2-UHF-RFID-Reader) | SDR-based EPC Gen2 implementation. Research-grade, needs HackRF/USRP. |

---

## License

MIT — see [`LICENSE`](LICENSE). Free for commercial and private use.

This driver was written from the vendor's published protocol manual
("UHF RFID Reader User's Manual v1.9"). The vendor's DLLs are not used
and not redistributed. The author is not affiliated with YanPoDo or any
other SW-protocol reader vendor.

---

## Contributing

PRs and issues welcome. Especially valuable:

- **Confirmation of behaviour on RU5100 and RU5500 hardware** (we have
  RU5305 covered)
- **Captures of the "Special params" / "Net params" blobs** from
  `ReaderSoftV4.2` so we can decode the Mask filter and WiFi/RJ45
  config bytes
- **TCP transport wrapper** for networked readers (~50 lines wrapping
  `socket` instead of `serial`)
- **More region presets** beyond the 18 the manual documents
- **MicroPython port** for ESP32 deployments
