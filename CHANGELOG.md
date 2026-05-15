# Changelog

All notable changes to swrfid will be documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0.dev0] — unreleased

### Added

- **EPC identification and SGTIN-96 parser** (`swrfid.epc`).
  Identifies every GS1 EPC Tag Data Standard scheme by header byte;
  fully parses SGTIN-96 (most common format) into filter / partition /
  company prefix / item reference / serial, with derived GTIN-14 and
  Pure Identity URI. For other schemes, recommends the canonical
  `epcpy` library.
- **`swrfid diagnose` CLI command** — 7-step "why isn't this working?"
  checklist that walks port discovery / open / system info / module
  health / RF power / region / work mode / antenna / inventory smoke
  test, with copy-pasteable suggestions for each failure.
- **`swrfid calibrate` CLI command** — RF-power-byte sweep against a
  reference tag, producing a JSON calibration curve with the lowest
  reliable power level for the test distance.
- **`swrfid decode-epc` CLI command** — offline EPC parser that needs no
  hardware. Identifies the GS1 scheme and prints structured fields for
  SGTIN-96.
- **`swrfid-mqtt` console script** — long-running MQTT bridge for
  Home Assistant / Node-RED integration. Publishes each tag read as
  JSON, optionally with HA MQTT-discovery config so HA auto-creates a
  sensor entity. Requires the `[mqtt]` extra (`pip install swrfid[mqtt]`).
- **Active-mode dedup + RSSI filter.**
  `RFIDReader.start_active_listener` now accepts `dedup_window` and
  `rssi_min` arguments; the CLI `listen` and GUI listen toggle expose
  these as `--dedup` / `--rssi-min` and as sliders.
- **GUI Tools menu** — Diagnose Connection, Calibrate Power, Decode
  EPC..., MQTT Bridge... — all the new CLI features available without
  leaving the GUI.
- **GUI tag table EPC format column** — shows the GS1 scheme (SGTIN-96,
  GRAI-96, ...) inline; double-click any tag for a full decode dialog.
- **GUI dedup + RSSI sliders** in the reader-settings panel, picked up
  on next "Start Listening".
- **`swrfid.epc.describe()` Python helper** + matching exports
  (`describe_epc`, `identify_epc`, `parse_sgtin96`, `SGTIN96`,
  `EPCDescription`).

### Changed

- **Cleaner port-open errors.** `RFIDReader.open()` now wraps
  `FileNotFoundError`, `OSError`, and pyserial `SerialException` as
  `ReaderError` with platform-appropriate suggestions (e.g.
  "use `swrfid list-ports`"). Previously these leaked raw OS
  exceptions out of `swrfid diagnose`.
- README rewritten in user-journey style, leading with `swrfid-gui` for
  everyday users.

### Optional dependencies

- `swrfid[mqtt]` — pulls in `paho-mqtt>=2.0` for the MQTT bridge.
- `swrfid[epc]`  — pulls in `epcpy>=0.8` for full GS1 EPC TDS parsing
  (we cover SGTIN-96 ourselves).
- `swrfid[all]`  — both.

### Internal

- New protocol module `swrfid.epc` (bit-layout reference: GS1 EPC TDS,
  partition table verified against epc-rfid.info).
- New filter wrapper `_FilteredCallback` in `swrfid.reader`.
- 32 new unit tests (EPC parser + filter wrapper) bringing the suite
  to 80 tests total. All pass on every Python 3.8–3.12 / OS combo.

## [0.1.0] — 2026-05-15

Initial public release.

### Added

- Frame build/parse with verified checksum (48 unit tests against the
  vendor manual's worked hex examples).
- System info, module health, antenna status.
- RF power, work mode, baud rate, beeper, region / frequency.
- Active-mode tag listener and command-mode inventory.
- Tag memory read / write across all four EPC Gen2 banks
  (Reserved, EPC, TID, User) with optional 32-bit access password.
- Relay control.
- CLI (`swrfid` console script after install, or `python3 -m swrfid`).
- Cross-platform port auto-detection (Linux, Windows, macOS).
- Documented protocol reference, per-model notes, troubleshooting guide.
- Runnable examples for each common task.
- Tkinter GUI (`swrfid-gui` console script).

### Known limitations (not implemented)

- On-device Mask-filter configuration — opaque "Special params" blob
  (`CMD 0x2E / 0x2F`); requires capture from the vendor Windows tool.
- WiFi / RJ45 provisioning — opaque "Net params" blob
  (`CMD 0x26 / 0x27`).
- EPC Gen2 Lock / Kill / BlockPermalock — not in firmware.
- Granular per-tag error reporting — firmware returns binary
  success / failed only.
- TCP transport — sketched in `swrfid.protocol` but not wrapped yet.
