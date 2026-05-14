# UHF RFID Reader — Wire Protocol Reference

**Source manual:** `SW_SDK_V3.2/[ENG]Manual/UHF RFID Reader User's Manual v1.9.doc`
(14 pages, last revised 2021-01-11 by the vendor). This document is a
condensed working reference compiled for the `beta/pure-python-driver`
branch.

The reader family covers at least the RU5100 / RU5300 / RU5500
(`DevType=0xC3` for the 5300). Hardware setup of the unit in this project
is documented in `README.md` — it is an FTDI 0403:6001 USB-serial bridge.

## 1. Physical layer

- Default: 115200 8N1, no parity
- Configurable baud (CMD 0x24, param 0x07): 9600 / 19200 / 38400 / 57600 / 115200
- Also supported: RJ45, WiFi, Wiegand (per device parameter `Transport`)
- Manual §1 claims "LSB first" but every worked example is **big-endian**
  for multi-byte fields. Trust the examples.

## 2. Frame format

### Command (host → reader)

```
+-----------+-----------+-----------+------+------+----------+----------+
| 0x53      | 0x57      | Length (BE)      | Addr | Cmd  | Data...  | Checksum |
+-----------+-----------+-----------+------+------+----------+----------+
   2 bytes               2 bytes            1 B   1 B   N B          1 B
```

### Response (reader → host)

```
+-----------+-----------+-----------+------+------+--------+----------+----------+
| 0x43      | 0x54      | Length (BE)      | Addr | Cmd  | Status | Data...  | Checksum |
+-----------+-----------+-----------+------+------+--------+----------+----------+
   2 bytes               2 bytes            1 B   1 B   1 B     M B          1 B
```

- `Length`: counts everything after the Length field itself
  (Addr + Cmd + [Status +] Data + Checksum). Range 3..1024.
- `Addr`: reader address. Default 0x00; 0xFF is broadcast.
- `Status`: `0x01` = success, `0x00` = failed. Binary only — no granular
  error codes.
- `Checksum`: two's-complement of the byte-sum over **the whole frame
  except the checksum byte itself** (starting at the 0x53/0x43 head).

### Checksum reference (manual §3.2)

```c
unsigned char CheckSum(unsigned char *uBuff, unsigned short iBuffLen) {
    unsigned char uSum = 0;
    for (unsigned short i = 0; i < iBuffLen; i++)
        uSum += uBuff[i];
    uSum = (~uSum) + 1;
    return uSum;
}
```

Python equivalent: `(-sum(payload)) & 0xFF`
(see `core/rfid_protocol.checksum`).

## 3. Operation commands

| Code | Name | Notes |
|---:|---|---|
| 0x01 | INVENTORY_TAG | Command-mode EPC Gen2 inventory; returns N tags |
| 0x02 | READ_TAG_DATA | Read words from {Reserved, EPC, TID, User} bank with 32-bit password |
| 0x03 | WRITE_TAG_DATA | Write words with 32-bit password |
| 0x10 | READ_SYSTEM_PARAM | softver / hwver / 7-byte serial number |
| 0x20 | READ_DEVICE_PARAM | Full 22-field parameter struct |
| 0x21 | SET_DEVICE_PARAM | Same |
| 0x22 | DEFAULT_DEVICE_PARAM | Reset all params to defaults |
| 0x23 | READ_ONE_PARAM | Read named single parameter |
| 0x24 | SET_ONE_PARAM | Set named single parameter |
| 0x26 | READ_NET_PARAM | **Opaque ~0x68-byte blob (WiFi/RJ45 config)** |
| 0x27 | SET_NET_PARAM | Same |
| 0x28 | DEFAULT_NET_PARAM | |
| 0x2B | READ_DEVICE_TIME | YY MM DD HH MM SS bytes |
| 0x2C | SET_DEVICE_TIME | Same |
| 0x2E | READ_SPECIAL_PARAM | **Opaque ~0x85-byte blob (Mask filter etc.)** |
| 0x2F | SET_SPECIAL_PARAM | Same |
| 0x3E | READ_FREQ | Returns 2-byte region key |
| 0x3F | SET_FREQ | Set 2-byte region key |
| 0x40 | STOP_READ | Stop active-mode reading |
| 0x41 | START_READ | Start active-mode reading |
| 0x45 | ACTIVE_DATA | Reader-initiated tag broadcast (host doesn't request) |
| 0x85 | CLOSE_RELAY | Close relay contact |
| 0x86 | RELEASE_RELAY | Release relay contact |
| 0xE0 | CHECK_MODULE | UHF RF module health check |
| 0xE1 | CHECK_ANT | 16-bit antenna presence bitmap |
| 0xFF | HEARTBEAT | TCP/WiFi keep-alive; reader → host; no ACK required |

### Single parameter addresses (CMD 0x23 / 0x24)

| Addr | Field | Range |
|---:|---|---|
| 0x01 | Transport | 0=USB, 1=RS232, 2=RJ45, 3=WiFi, 4=Wiegand |
| 0x02 | WorkMode | 0=Answer, 1=Active, 2=Trigger |
| 0x03 | DeviceAddr | 0x00-0xFE |
| 0x04 | FilterTime | 0-255 (0 = filter disabled) |
| 0x05 | RFPower | 0..0x11 (5100), 0..0x1A (5300), 0..0x1E (5500). **Byte ≠ dBm directly; manual has a typo in the worked example. Bench-verify.** |
| 0x06 | BeepEnable | 0/1 |
| 0x07 | UartBaudRate | 0=9600, 1=19200, 2=38400, 3=57600, 4=115200 |

### Region frequency table (CMD 0x3E / 0x3F)

Send/receive as two raw bytes `N1 N2`.

| Region | N1 | N2 | Region | N1 | N2 |
|---|---:|---:|---|---:|---:|
| US, CA, MX | 0x31 | 0x80 | EU, NZ, IN | 0x4E | 0x00 |
| CN, HK, TH | 0x2C | 0xA3 | KR, JP | 0x29 | 0x9D |
| AU | 0x2E | 0x9F | SG | 0x2C | 0x81 |
| TW | 0x31 | 0xA7 | BR | 0x31 | 0x99 |
| IL | 0x1C | 0x99 | ZA | 0x24 | 0x9D |
| MY | 0x28 | 0xA1 | | | |

US: `Fs = 902.75 + N * 0.5 MHz, N ∈ [0, 49]`.
EU: `Fs = 865.1 + N * 0.2 MHz, N ∈ [0, 14]`.

### Device parameter struct (CMD 0x20 / 0x21)

22-byte struct prefixed by `DevType (1) + DefaultSwitch (1=0x55 to load
from struct, anything else loads firmware defaults)`. Fields (also as
constants in `core/rfid_protocol.py`):

```
Transport, WorkMode, DeviceAddr, FilterTime, RFPower, BeepEnable,
UartBaudRate, FreqH, FreqL, ScanArea, StartPos, ScanLength,
TriggerTime, WgProtocol, WgOutPutMode, WgOutTime, WgPulseWidth,
WgPulseInterTime, AntH, AntL, QValue (0-6), Session (0-3)
```

FreqH/FreqL encode band and min/max channels:
- bits `[7:6]` of FreqH and `[7:6]` of FreqL together select the band
  (per manual: `FreqH7=0,FreqH6=0,FreqL7=1,FreqL6=0` → US;
  `FreqH7=0,FreqH6=1,FreqL7=0,FreqL6=0` → EU)
- bits `[5:0]` of FreqH = max channel index
- bits `[5:0]` of FreqL = min channel index

## 4. Active-mode broadcast (CMD 0x45)

Reader autonomously emits when `WorkMode = Active` and `START_READ` has
been issued (or device is configured to auto-start).

Frame `Data` field:

```
DevSN (7) | TagCount (1) | [ TagLen (1) Type (1) Ant (1) EPC (TagLen-3) RSSI (1) ]*
```

`TagLen` counts every byte after itself. With a standard 96-bit EPC,
`TagLen=0x0F`: type(1) + ant(1) + EPC(12) + rssi(1).

Host may send `0x45` back as an ACK with no data — manual notes "not
necessary". The existing `core/rfid_reader.py` parses this same frame
shape via its `_extract_hex_tag` path (the `CT` prefix it keys on is
the 0x43 0x54 response head).

## 5. Inventory response (CMD 0x01)

```
TagCount (2, BE) | [ TagLen (1) Type (1) Ant (1) EPC (TagLen-3) RSSI (1) ]*
```

"No tag" responses have `status = 0x00` and a single data byte (the
manual example shows `0x64`). Treat any non-success response as "zero
tags".

## 6. Tag memory operations (CMD 0x02 / 0x03)

Data field for both reads and writes:

```
Bank (1) | WordAddr (1) | WordLen (1) | Password (4)  [ | Payload (2*WordLen) ]
```

- `Bank`: 0=Reserved, 1=EPC, 2=TID, 3=User
- `WordAddr`: 16-bit word offset within the bank
- `WordLen`: number of 16-bit words to read/write
- `Password`: 32-bit access password (`00 00 00 00` if unlocked)

Read response data is `WordLen * 2` bytes. Write response is just the
status byte.

## 7. Custom output mode

Configured via device parameters, the reader can switch to a passthrough
"Wiegand-style" framing mode:

```
0x02 | ASCII tag ID | 0x0D | 0x0A | 0x03
```

`ASCII tag ID` is the hex EPC as plain ASCII characters
(e.g. `"00112233445566778899AABB"`).

## 8. Known limitations

1. **No Lock / Kill / BlockPermalock commands.** Not in this manual and
   not in any of the four DLL APIs (`SWComApi.dll`, `SWHidApi.dll`,
   `SWNetClientApi.dll`, `SWNetServerApi.dll`). Probably absent from
   firmware; cannot be added by any driver.
2. **Status byte is binary.** No granular error codes (tag-not-found vs.
   CRC-error vs. password-mismatch vs. region-not-allowed).
3. **`READ_NET_PARAM` / `READ_SPECIAL_PARAM` blobs are opaque.** Field
   layout would need to be captured from `ReaderSoftV4.2` (the vendor
   Windows tool) while toggling GUI controls.
4. **RF power → dBm mapping is undocumented**, and the manual's worked
   example labels a 5300 device but uses a 5500-range value (likely a
   copy-paste typo).
5. **Wiegand timing parameters** are exposed but the parity-bit and
   pulse waveform aren't fully specified.
6. **No firmware-upgrade protocol** in the wire spec.
   `How to Upgrade Device.doc` is GUI-driven.
7. **No Gen2 Select / Mask command per operation.** Tag-mask filtering
   lives in the on-device `SPECIAL_PARAM` blob (per the FAQ
   `How to read a specified label.txt`) and persists until reprogrammed.
8. **Manual inconsistency in CMD_CHECK_MODULE (§4 item 19).** The worked
   example shows `43 54 00 05 01 E0 01 82` (Module OK) and
   `43 54 00 05 01 E0 00 83` (Module Error). Both are 8 bytes but
   declare `LEN=0x05`, which would require a 9-byte frame. The
   checksums printed are computed using LEN=0x05, so the example is
   self-consistent but framed incorrectly. Treat real-device frames as
   `LEN=0x04` (8 bytes); see `tests/test_rfid_protocol.py
   ::test_manual_check_module_example_is_inconsistent`. If a real reader
   actually emits the 8-byte form with LEN=0x05, the framer will reject
   it and we'll need to special-case this command — file an issue with
   a capture if you see this.
