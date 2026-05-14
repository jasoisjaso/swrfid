# Troubleshooting

## "No reader found" / port doesn't appear

### Linux

1. Run `swrfid list-ports`. If empty, the kernel doesn't see the device.
2. Plug in and run `lsusb`. You should see a line containing
   `0403:6001 Future Technology Devices International, Ltd FT232 Serial`.
3. If not present, check the USB cable (some are charge-only).
4. If present but no port shows up, check `dmesg | tail -20` for FTDI
   binding errors. The `ftdi_sio` kernel module should auto-load.
5. If the kernel claims the device but `swrfid list-ports` doesn't see
   it, you might be on a stripped-down distro missing pyserial:
   `pip install pyserial`.

### Windows

1. Plug in, then open Device Manager → Ports (COM & LPT).
2. You should see a "USB Serial Port (COMx)" entry.
3. If you see "USB Serial Converter" with no COM port, install the
   FTDI VCP driver from <https://ftdichip.com/drivers/vcp-drivers/>.
4. Yellow warning triangle: right-click → Update Driver → point at the
   FTDI VCP installer.

### macOS

1. `ls /dev/cu.*` should show `cu.usbserial-XXXX`.
2. If not, install the FTDI VCP driver from
   <https://ftdichip.com/drivers/vcp-drivers/>.

## "Permission denied" opening the port (Linux)

```bash
sudo usermod -aG dialout $USER
newgrp dialout            # or log out + back in
```

Verify: `groups | grep dialout`.

One-shot alternative: `sudo chmod 666 /dev/ttyUSB0` (resets on reboot).

## `swrfid info` times out

- **Wrong baud rate.** Default is 115200; some used-market units have
  been reprogrammed. Try `--baud 9600`.
- **Wrong reader address.** Default is broadcast (`0xFF`), which always
  works unless the reader has been programmed to ignore broadcasts.
  Use `--addr 0xNN` to match a specific configured address.
- **USB cable / hub issue.** Try a different cable, plug directly into
  the PC (skip hubs).
- **Reader in firmware-update mode.** Power-cycle.

## `swrfid inventory` finds zero tags

- **RF power too low.** `swrfid set-power 0x14` (mid-range) and retry.
- **Wrong region** — tag and reader must be on the same band. Try
  `swrfid set-region US` (or `EU`, `JP`, etc.) for your area.
- **Antenna fault** on a multi-antenna reader. `swrfid antenna` to
  verify the bitmap matches your physical wiring.
- **Tag too close to metal** (detunes the antenna). Move to a
  non-metal surface for testing.
- **Tag broken / wrong frequency.** Try a known-good Gen2 tag.
- **Reader in `ACTIVE` mode with `STOP_READ` issued.** Switch to
  `ANSWER` mode: `swrfid set-mode ANSWER`.

## "Checksum mismatch" errors

Indicates wire-level data corruption. Common causes:

- Cheap USB hub with poor shielding — use a powered hub or direct
  connection.
- Cable too long (>2 m for FTDI default config) — use a shorter cable.
- Reader near a noisy power supply — move it.
- Reader at the edge of its 5 V budget — try a different USB port or
  a powered hub.

The driver re-syncs on bad checksums, so occasional corruption causes a
missed frame, not a crash. If checksum errors are frequent
(multiple per second), fix the wiring before trusting tag data.

## `swrfid write-tag` returns "no response" or status=0

- **No tag in the field, or multiple tags.** For writes, put ONE tag in
  the field at a time. The reader writes to whichever tag responds
  first to its singulation cycle.
- **Wrong password.** The tag's access password is per-bank in EPC
  Gen2. Use `--password XXXXXXXX` matching the tag's configured
  password (default unlocked: `00000000`).
- **Word-alignment.** Payload length must be a multiple of 2 bytes
  (one word). The CLI rejects misaligned input.
- **Tag in permalock state.** Cannot be undone — the tag is read-only
  for that bank.

## "I want to read just one specific tag"

The on-device Mask filter lives in the "Special params" blob and is
not currently exposed by this driver — the blob layout isn't
documented in the vendor manual. Use the vendor Windows tool
`ReaderSoftV4.2` → AdvanceSet → Mask to program the filter; it
persists across reboots, and subsequent `swrfid inventory` calls will
only see matching tags.

If you're comfortable serial-sniffing, capture a
`SET_SPECIAL_PARAM (0x2F)` exchange from `ReaderSoftV4.2` while
toggling the mask and file an issue with the bytes — we can extend the
driver.

## "I want WiFi / RJ45 / 4G provisioning"

Same answer as Mask filtering — the "Net params" blob (`CMD 0x27`) is
opaque in the manual. Use the vendor tool to provision the network
side, then talk to the reader over TCP using the same wire protocol.

This driver doesn't yet have a TCP transport, but it's a ~50-line
wrapper around `socket` instead of `serial`. PRs welcome.

## "My reader isn't a YanPoDo — will this work?"

If the supplier's docs refer to `SWComApi.dll`, `SWHidApi.dll`,
`SWNetClientApi.dll`, or `SWNetServerApi.dll`, **yes**. The same wire
protocol underlies all of them — the DLLs only differ in transport.
First-hand confirmation: YanPoDo RU5305. Reported compatibility:
Sunwell, Daily RFID, and several unbranded OEM units sold on
Aliexpress.

If it's a different protocol family (Impinj R420, ThingMagic M6e, ...)
this driver will not work — those use LLRP or vendor-specific
protocols.

## How do I check what firmware I have?

```bash
swrfid info
```

prints software version, hardware version, and 7-byte serial number.
The documented vendor firmware ladder is `1.0` → `1.4` at time of
writing. If you see something newer, the protocol may have extensions
this driver doesn't know about — please file an issue with the version
and any unexpected behaviour.

## Reporting bugs

When opening an issue, please include:

- Output of `swrfid info`.
- Output of `swrfid list-ports`.
- A reproducer command (with `--verbose` so we see frame bytes).
- Your OS and Python version.
- If you suspect a wire-protocol bug, a logic-analyser or serial-sniffer
  capture is gold.
