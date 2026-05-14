# Model reference — SW UHF RFID reader family

What we know about the SW-protocol reader family. All values are
derived from the vendor manual; **bench-verify before relying on them
for safety / regulatory compliance**.

## Common attributes (all models)

- 115200 8N1 default UART (configurable down to 9600)
- EPC Gen2 air protocol
- All four memory banks (Reserved, EPC, TID, User)
- Active, Answer, and Trigger work modes
- 18 region frequency presets (`docs/PROTOCOL.md` §3)
- Wiegand 26 / 34 output (parameters exposed; waveform under-specified)
- Optional RJ45 / WiFi modules (provisioned via the vendor tool —
  this driver doesn't yet decode that opaque param blob)

## RU5100 — desktop / tag-programming class

- USB bus-powered (no external supply)
- Single internal antenna (no external port)
- Max RF power byte: **0x11** (typically 17 dBm)
- Typical range: 10–30 cm
- Use cases: tag commissioning, EPC encoding, access-card reading,
  POS / inventory desk lookups

The "DESKTOP READER WRITER" unit covered by `ReaderSoftV4.2/
YanPoDo DESKTOP READER WRITER user manual.doc` is in this class.

## RU5300 — mid-range / general purpose

- 12 V external power
- 1–2 external antenna ports (typically RP-SMA or N-type)
- Max RF power byte: **0x1A** (typically 26 dBm)
- Typical range: 2–5 m with a 5–6 dBi panel antenna
- Use cases: warehouse checkout / return stations, conveyor portals,
  vehicle access control

This is the most common SW-family model and the one the vendor manual
is primarily written for.

## RU5500 — long-range / multi-antenna

- 12 V external power
- 4 to 16 antenna ports (model-dependent)
- Max RF power byte: **0x1E** (typically 30 dBm)
- Typical range: 8–12 m with high-gain antennas
- Use cases: dock doors, retail loss prevention, racing timing,
  large-area asset tracking

## RF power byte → dBm mapping

The vendor manual's worked example claims `0x1E = 30 dBm` but labels a
5300 device, which has a documented maximum of `0x1A`. This is almost
certainly a copy-paste typo from a 5500 example. **Treat the
byte-to-dBm mapping as linear-but-unverified** until you have checked
it with a power meter or a known reference tag at a known distance.

Suggested calibration procedure:

1. Place a reference tag at a fixed distance (e.g. 30 cm) on the
   antenna's bore-sight, in free space (no metal nearby).
2. `swrfid set-power 0x00` and try `swrfid inventory` repeatedly until
   the tag is first reliably detected — this is your sensitivity floor.
3. Step up in 0x02 increments, logging the success rate at each step.
4. The first 100 % reliable read tells you the effective sensitivity
   for that distance; the manufacturer's claimed max
   (`0x11` / `0x1A` / `0x1E`) tells you the top of the curve.
5. Cross-reference with regional regulatory limits (FCC 36 dBm EIRP,
   ETSI 33 dBm ERP, etc.) before deploying in production.

## Antenna bitmap

`CMD_CHECK_ANT (0xE1)` returns a 16-bit bitmap of present antennas.
On single-antenna readers (RU5100, single-port RU5300) this is
informational only; on multi-antenna RU5500s it is the canonical way to
detect open antenna ports — important because transmitting into an
unterminated port can damage the RF module over time.

## Region / frequency

All models accept the same 18-region preset table. There is no public
information on which models support which regions in firmware; the
vendor allows setting any region. If your device is locked to a region
at the factory, `swrfid set-region` may silently no-op — the protocol
provides no granular error response.

## Identifying your model

```bash
swrfid info
```

returns the software version, hardware version, and 7-byte serial
number. The vendor parameter struct includes a `DevType` byte
(`0xC3` = 5300) but no direct "this is a 5300" header — model is
inferred from the max RF power your firmware accepts.

A practical heuristic:

```bash
for v in 12 18 1B 1F; do
  swrfid set-power 0x$v && echo "0x$v accepted" || echo "0x$v rejected"
done
```

- Rejects above 0x11: RU5100
- Rejects above 0x1A: RU5300
- Rejects above 0x1E: RU5500
