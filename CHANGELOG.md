# Changelog

All notable changes to swrfid will be documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

### Known limitations (not implemented)

- On-device Mask-filter configuration — opaque "Special params" blob
  (`CMD 0x2E / 0x2F`); requires capture from the vendor Windows tool.
- WiFi / RJ45 provisioning — opaque "Net params" blob
  (`CMD 0x26 / 0x27`).
- EPC Gen2 Lock / Kill / BlockPermalock — not in firmware.
- Granular per-tag error reporting — firmware returns binary
  success / failed only.
- TCP transport — sketched in `swrfid.protocol` but not wrapped yet.
