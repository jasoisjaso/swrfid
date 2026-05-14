"""
swrfid.protocol — pure-Python wire-protocol module for the SW UHF RFID
reader family (YanPoDo RU5100 / RU5300 / RU5500 and protocol-compatible
units shipping the SW*Api.dll SDK).

Frame construction and parsing only — no I/O, no platform dependencies.
Every byte sequence in tests/test_protocol.py is validated against the
hex examples in "UHF RFID Reader User's Manual v1.9". See
docs/PROTOCOL.md for the full wire-protocol reference.

swrfid.reader wraps this module with a pyserial transport and timing.
Keeping frame logic transport-free means the same module can be reused
over USB/RS232, RJ45, or WiFi without changes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# Frame headers (manual §3.1 / §3.2).
# Host → Reader frames start with ASCII "SW" (0x53 0x57).
# Reader → Host frames start with ASCII "CT" (0x43 0x54).
HEAD_CMD = b'\x53\x57'
HEAD_RSP = b'\x43\x54'

BROADCAST_ADDR = 0xFF
DEFAULT_ADDR = 0x00


class Cmd(enum.IntEnum):
    """Operation command codes (manual §4)."""
    INVENTORY_TAG = 0x01
    READ_TAG_DATA = 0x02
    WRITE_TAG_DATA = 0x03
    READ_SYSTEM_PARAM = 0x10
    READ_DEVICE_PARAM = 0x20
    SET_DEVICE_PARAM = 0x21
    DEFAULT_DEVICE_PARAM = 0x22
    READ_ONE_PARAM = 0x23
    SET_ONE_PARAM = 0x24
    READ_NET_PARAM = 0x26
    SET_NET_PARAM = 0x27
    DEFAULT_NET_PARAM = 0x28
    READ_DEVICE_TIME = 0x2B
    SET_DEVICE_TIME = 0x2C
    READ_SPECIAL_PARAM = 0x2E
    SET_SPECIAL_PARAM = 0x2F
    READ_FREQ = 0x3E
    SET_FREQ = 0x3F
    STOP_READ = 0x40
    START_READ = 0x41
    ACTIVE_DATA = 0x45
    CLOSE_RELAY = 0x85
    RELEASE_RELAY = 0x86
    CHECK_MODULE = 0xE0
    CHECK_ANT = 0xE1
    HEARTBEAT = 0xFF


class ParamAddr(enum.IntEnum):
    """Single-parameter addresses for CMD 0x23 / 0x24 (manual §4 items 5, 6)."""
    TRANSPORT = 0x01
    WORK_MODE = 0x02
    DEVICE_ADDR = 0x03
    FILTER_TIME = 0x04
    RF_POWER = 0x05
    BEEP_ENABLE = 0x06
    UART_BAUD_RATE = 0x07


class Transport(enum.IntEnum):
    USB = 0
    RS232 = 1
    RJ45 = 2
    WIFI = 3
    WIEGAND = 4


class WorkMode(enum.IntEnum):
    ANSWER = 0
    ACTIVE = 1
    TRIGGER = 2


class MemBank(enum.IntEnum):
    """EPC Gen2 tag memory bank IDs (manual §4 items 25/26)."""
    RESERVED = 0
    EPC = 1
    TID = 2
    USER = 3


class Status(enum.IntEnum):
    FAILED = 0
    SUCCESS = 1


# Region frequency table (manual §4 items 14, 15). Send N1, N2 as the data
# field of SET_FREQ (0x3F). Reading via READ_FREQ (0x3E) returns the same
# two bytes plus a success-status byte.
REGION_FREQ: Dict[str, Tuple[int, int]] = {
    'US':         (0x31, 0x80),
    'EU':         (0x4E, 0x00),
    'CN':         (0x2C, 0xA3),
    'KR':         (0x29, 0x9D),
    'AU':         (0x2E, 0x9F),
    'NZ':         (0x4E, 0x00),
    'IN':         (0x4E, 0x00),
    'SG':         (0x2C, 0x81),
    'HK':         (0x2C, 0xA3),
    'TW':         (0x31, 0xA7),
    'CA':         (0x31, 0x80),
    'MX':         (0x31, 0x80),
    'BR':         (0x31, 0x99),
    'IL':         (0x1C, 0x99),
    'ZA':         (0x24, 0x9D),
    'TH':         (0x2C, 0xA3),
    'MY':         (0x28, 0xA1),
    'JP':         (0x29, 0x9D),
}

# RF power maximum per model (manual §4 items 5, 6). Upper bound for the
# value byte of SET_ONE_PARAM with ParamAddr.RF_POWER. The manual's
# example "05 1E: RF Power is 0x1E(30 dbm)" labels a 5300 device but
# 0x1E exceeds the 5300 stated max of 0x1A — likely a typo carried over
# from a 5500 example. Validate the byte-to-dBm linearity on bench
# before relying on it.
RF_POWER_MAX: Dict[str, int] = {
    '5100': 0x11,
    '5300': 0x1A,
    '5500': 0x1E,
}


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

def checksum(payload: bytes) -> int:
    """Two's-complement uint8 checksum of payload.

    C reference (manual §3.2):

        unsigned char uSum = 0;
        for (i = 0; i < iBuffLen; i++) uSum += uBuff[i];
        uSum = (~uSum) + 1;

    Empirically verified against every worked example in the manual: the
    checksum scope INCLUDES the head bytes HEAD_CMD / HEAD_RSP — i.e. it
    covers the entire frame except the trailing checksum byte itself.
    """
    return (-sum(payload)) & 0xFF


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def build_frame(cmd: int, data: bytes = b'', addr: int = BROADCAST_ADDR) -> bytes:
    """Build a host→reader command frame.

    Layout: HEAD(0x53 0x57) | LEN(big-endian uint16) | ADDR | CMD | DATA | CKSUM
    LEN counts ADDR + CMD + DATA + CKSUM = len(data) + 3.

    Manual §1 says "least significant byte transmitted first", but every
    worked example uses big-endian for LEN and every multi-byte field.
    Examples are authoritative.
    """
    if not (0 <= addr <= 0xFF):
        raise ValueError("addr out of range: %d" % addr)
    if not (0 <= cmd <= 0xFF):
        raise ValueError("cmd out of range: %d" % cmd)
    length = 3 + len(data)
    if length > 1024:
        raise ValueError("frame too long: %d > 1024 (manual §3.1 max)" % length)
    body = HEAD_CMD + length.to_bytes(2, 'big') + bytes([addr, cmd]) + data
    return body + bytes([checksum(body)])


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

@dataclass
class Response:
    addr: int
    cmd: int
    status: int
    data: bytes
    raw: bytes = field(repr=False)

    def ok(self) -> bool:
        return self.status == Status.SUCCESS


class FrameError(ValueError):
    """Raised on malformed frames (bad head, length, checksum)."""


def parse_response(buf: bytes) -> Response:
    """Parse a single complete reader→host response frame.

    Caller is responsible for framing — use ResponseBuffer for the
    streaming case where multiple frames may be interleaved with
    unsolicited active-mode broadcasts.
    """
    if len(buf) < 7:
        raise FrameError("too short: %d bytes" % len(buf))
    if buf[:2] != HEAD_RSP:
        raise FrameError("bad head: %s" % buf[:2].hex())
    length = int.from_bytes(buf[2:4], 'big')
    if length < 3 or length > 1024:
        raise FrameError("length out of range: %d" % length)
    expected = 4 + length
    if len(buf) < expected:
        raise FrameError("truncated: have %d, need %d" % (len(buf), expected))
    ck_expected = buf[expected - 1]
    ck_actual = checksum(buf[:expected - 1])
    if ck_expected != ck_actual:
        raise FrameError(
            "checksum mismatch: frame says 0x%02X, computed 0x%02X"
            % (ck_expected, ck_actual)
        )
    addr = buf[4]
    cmd = buf[5]
    status = buf[6]
    data = bytes(buf[7:expected - 1])
    return Response(
        addr=addr, cmd=cmd, status=status, data=data,
        raw=bytes(buf[:expected]),
    )


class ResponseBuffer:
    """Stateful streaming parser.

    Feed raw serial bytes via ``feed()``; iterate complete Response objects.
    Resyncs on garbage by walking the buffer one byte at a time when the
    head/length/checksum don't validate. Tolerates active-mode broadcasts
    interleaved with command responses.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> Iterator[Response]:
        self._buf.extend(chunk)
        while True:
            i = self._buf.find(HEAD_RSP)
            if i < 0:
                # No head in buffer; keep last byte in case it's the first
                # half of a head straddling the next chunk boundary.
                if len(self._buf) > 1:
                    del self._buf[:-1]
                return
            if i > 0:
                del self._buf[:i]
            if len(self._buf) < 4:
                return
            length = int.from_bytes(self._buf[2:4], 'big')
            if length < 3 or length > 1024:
                del self._buf[:2]
                continue
            frame_len = 4 + length
            if len(self._buf) < frame_len:
                return
            frame = bytes(self._buf[:frame_len])
            try:
                rsp = parse_response(frame)
            except FrameError:
                del self._buf[:2]
                continue
            del self._buf[:frame_len]
            yield rsp


# ---------------------------------------------------------------------------
# Tag-list parsing — shared by ACTIVE_DATA (0x45) and INVENTORY_TAG (0x01)
# ---------------------------------------------------------------------------

@dataclass
class TagEntry:
    """One tag from an ACTIVE_DATA or INVENTORY_TAG response.

    Per-tag layout (manual §4 items 18 and 24):
        TagLen(1) | Type(1) | Ant(1) | EPC(TagLen-3) | RSSI(1)
    TagLen counts Type + Ant + EPC + RSSI (everything after itself).
    """
    type_code: int
    antenna: int
    epc: bytes
    rssi: int

    @property
    def epc_hex(self) -> str:
        return self.epc.hex().upper()


def _parse_tag_list(buf: bytes, offset: int, count: int) -> List[TagEntry]:
    tags: List[TagEntry] = []
    pos = offset
    for _ in range(count):
        if pos >= len(buf):
            raise FrameError("tag list truncated")
        tag_len = buf[pos]
        if tag_len < 3:
            raise FrameError("bad tag_len: %d" % tag_len)
        end = pos + 1 + tag_len
        if end > len(buf):
            raise FrameError("tag entry runs past buffer")
        type_code = buf[pos + 1]
        ant = buf[pos + 2]
        epc = bytes(buf[pos + 3 : pos + tag_len])
        rssi = buf[pos + tag_len]
        tags.append(TagEntry(
            type_code=type_code, antenna=ant, epc=epc, rssi=rssi,
        ))
        pos = end
    return tags


def parse_active_data(rsp: Response) -> Tuple[bytes, List[TagEntry]]:
    """Parse a CMD_ACTIVE_DATA (0x45) response.

    Data layout: DevSN(7) | TagCount(1) | [TagEntry]*
    """
    if rsp.cmd != Cmd.ACTIVE_DATA:
        raise FrameError("not ACTIVE_DATA: cmd=0x%02X" % rsp.cmd)
    if len(rsp.data) < 8:
        raise FrameError("data too short: %d" % len(rsp.data))
    dev_sn = rsp.data[:7]
    count = rsp.data[7]
    tags = _parse_tag_list(rsp.data, 8, count)
    return dev_sn, tags


def parse_inventory(rsp: Response) -> List[TagEntry]:
    """Parse a CMD_INVENTORY_TAG (0x01) success response.

    Data layout: TagCount(2, big-endian) | [TagEntry]*
    Returns an empty list on the "no tag" failure response (status=0x00,
    data=0x64 per the manual's worked example).
    """
    if rsp.cmd != Cmd.INVENTORY_TAG:
        raise FrameError("not INVENTORY_TAG: cmd=0x%02X" % rsp.cmd)
    if not rsp.ok() or len(rsp.data) < 2:
        return []
    count = int.from_bytes(rsp.data[:2], 'big')
    return _parse_tag_list(rsp.data, 2, count)


# ---------------------------------------------------------------------------
# Convenience command builders
# ---------------------------------------------------------------------------

def cmd_start_read(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.START_READ, addr=addr)


def cmd_stop_read(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.STOP_READ, addr=addr)


def cmd_inventory(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.INVENTORY_TAG, addr=addr)


def cmd_read_system_param(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.READ_SYSTEM_PARAM, addr=addr)


def cmd_check_module(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.CHECK_MODULE, addr=addr)


def cmd_check_ant(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.CHECK_ANT, addr=addr)


def cmd_read_one_param(param: ParamAddr, addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.READ_ONE_PARAM, bytes([int(param)]), addr=addr)


def cmd_set_one_param(param: ParamAddr, value: int, addr: int = BROADCAST_ADDR) -> bytes:
    if not (0 <= value <= 0xFF):
        raise ValueError("param value out of range: %d" % value)
    return build_frame(Cmd.SET_ONE_PARAM, bytes([int(param), value]), addr=addr)


def cmd_set_rf_power(db_byte: int, addr: int = BROADCAST_ADDR) -> bytes:
    """Set RF power. ``db_byte`` is the device's power byte (0..model max).

    The byte-to-dBm linearity is undocumented; bench-verify before
    trusting the mapping.
    """
    return cmd_set_one_param(ParamAddr.RF_POWER, db_byte, addr=addr)


def cmd_set_work_mode(mode: WorkMode, addr: int = BROADCAST_ADDR) -> bytes:
    return cmd_set_one_param(ParamAddr.WORK_MODE, int(mode), addr=addr)


def cmd_set_freq_region(region: str, addr: int = BROADCAST_ADDR) -> bytes:
    key = region.upper()
    if key not in REGION_FREQ:
        raise ValueError(
            "unknown region: %r. Known: %s"
            % (region, sorted(REGION_FREQ))
        )
    n1, n2 = REGION_FREQ[key]
    return build_frame(Cmd.SET_FREQ, bytes([n1, n2]), addr=addr)


def cmd_read_freq(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.READ_FREQ, addr=addr)


def cmd_read_tag(
    bank: MemBank,
    word_addr: int,
    word_len: int,
    password: bytes = b'\x00\x00\x00\x00',
    addr: int = BROADCAST_ADDR,
) -> bytes:
    """Read tag memory (manual §4 item 25). word_len is in 16-bit words."""
    if len(password) != 4:
        raise ValueError("password must be exactly 4 bytes")
    if not (0 <= word_addr <= 0xFF):
        raise ValueError("word_addr out of range: %d" % word_addr)
    if not (1 <= word_len <= 0xFF):
        raise ValueError("word_len out of range: %d" % word_len)
    return build_frame(
        Cmd.READ_TAG_DATA,
        bytes([int(bank), word_addr, word_len]) + password,
        addr=addr,
    )


def cmd_write_tag(
    bank: MemBank,
    word_addr: int,
    payload: bytes,
    password: bytes = b'\x00\x00\x00\x00',
    addr: int = BROADCAST_ADDR,
) -> bytes:
    """Write tag memory (manual §4 item 26). Payload must be word-aligned."""
    if len(password) != 4:
        raise ValueError("password must be exactly 4 bytes")
    if len(payload) % 2:
        raise ValueError(
            "payload must be word-aligned (even byte count); got %d"
            % len(payload)
        )
    word_len = len(payload) // 2
    if not (1 <= word_len <= 0xFF):
        raise ValueError("payload length out of range: %d words" % word_len)
    return build_frame(
        Cmd.WRITE_TAG_DATA,
        bytes([int(bank), word_addr, word_len]) + password + payload,
        addr=addr,
    )


def cmd_relay_close(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.CLOSE_RELAY, addr=addr)


def cmd_relay_release(addr: int = BROADCAST_ADDR) -> bytes:
    return build_frame(Cmd.RELEASE_RELAY, addr=addr)


# ---------------------------------------------------------------------------
# Antenna status (manual §4 item 20)
# ---------------------------------------------------------------------------

def parse_antenna_status(rsp: Response) -> Dict[int, bool]:
    """Decode CHECK_ANT response into {antenna_index_1based: present}.

    Bitmap is 16 bits; bit i is antenna (i+1). Big-endian per the
    manual's worked example "FF F2" → ants 1..4 = None, Exist, None, None.
    """
    if rsp.cmd != Cmd.CHECK_ANT or not rsp.ok():
        raise FrameError(
            "not a CHECK_ANT success: cmd=0x%02X status=%d"
            % (rsp.cmd, rsp.status)
        )
    if len(rsp.data) < 2:
        raise FrameError("antenna status data too short")
    bitmap = int.from_bytes(rsp.data[:2], 'big')
    return {i + 1: bool(bitmap & (1 << i)) for i in range(16)}


# ---------------------------------------------------------------------------
# System info (manual §4 item 1)
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    soft_version: str      # e.g. "1.4"
    hard_version: str      # e.g. "1.1"
    serial_number: bytes   # 7 raw bytes

    @property
    def serial_hex(self) -> str:
        return self.serial_number.hex().upper()


def parse_system_info(rsp: Response) -> SystemInfo:
    if rsp.cmd != Cmd.READ_SYSTEM_PARAM or not rsp.ok():
        raise FrameError("not a READ_SYSTEM_PARAM success")
    if len(rsp.data) < 9:
        raise FrameError("system info data too short")
    sv = rsp.data[0]
    hv = rsp.data[1]
    return SystemInfo(
        soft_version="%d.%d" % ((sv >> 4) & 0x0F, sv & 0x0F),
        hard_version="%d.%d" % ((hv >> 4) & 0x0F, hv & 0x0F),
        serial_number=bytes(rsp.data[2:9]),
    )
