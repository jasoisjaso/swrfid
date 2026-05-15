"""
swrfid.epc — Electronic Product Code identification and parsing.

Identifies the encoding scheme of an EPC (SGTIN-96, GRAI-96, GID-96, ...)
from its header byte, and provides a full structured parser for the
SGTIN-96 scheme (the most common format used in retail and asset
tracking).

For full parsing of other EPC schemes (GRAI-96, GIAI-96, SSCC-96,
SGLN-96, etc.) install the canonical ``epcpy`` library
(https://github.com/nedap/retail-epcpy) which implements every scheme
defined by the GS1 EPC Tag Data Standard:

    pip install epcpy

Bit-layout reference: GS1 EPC Tag Data Standard, verified against
https://www.epc-rfid.info/sgtin-partition-values for the SGTIN-96
partition table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# EPC header bytes as defined by the GS1 EPC Tag Data Standard.
# Source: GS1 TDS §14.2 ("Header values and their associated EPC schemes").
EPC_HEADERS = {
    0x2C: 'gdti-96',
    0x2D: 'gsrn-96',
    0x2F: 'usdod-96',
    0x30: 'sgtin-96',
    0x31: 'sscc-96',
    0x32: 'sgln-96',
    0x33: 'grai-96',
    0x34: 'giai-96',
    0x35: 'gid-96',
    0x36: 'sgtin-198',
    0x37: 'grai-170',
    0x38: 'giai-202',
    0x39: 'sgln-195',
    0x3A: 'gdti-113',
    0x3B: 'adi-var',
    0x3C: 'cpi-96',
    0x3D: 'cpi-var',
    0x3E: 'gdti-174',
    0x3F: 'sgcn-96',
    0x40: 'itip-110',
    0x41: 'itip-212',
}


def identify(epc: bytes) -> str:
    """Identify the EPC encoding scheme from the leading header byte.

    Returns a short scheme name like ``'sgtin-96'``, or ``'unknown'`` for
    unrecognised headers.
    """
    if not epc:
        return 'unknown'
    return EPC_HEADERS.get(epc[0], 'unknown')


# ---------------------------------------------------------------------------
# SGTIN-96 (header 0x30) — Serialized Global Trade Item Number
#
# Layout (96 bits total):
#   bits  0..7    Header (=0x30)
#   bits  8..10   Filter value (3 bits)
#   bits 11..13   Partition value (3 bits, selects company prefix length)
#   bits 14..(13+P_bits)         Company Prefix (M bits per partition)
#   bits (14+P_bits)..(43+rest)  Item Reference (N bits per partition)
#   bits 58..95   Serial number (38 bits, decimal)
#
# Partition table (M + N = 44 bits always):
SGTIN96_PARTITIONS = {
    # partition: (cp_bits, cp_digits, ir_bits, ir_digits)
    0: (40, 12, 4, 1),
    1: (37, 11, 7, 2),
    2: (34, 10, 10, 3),
    3: (30, 9, 14, 4),
    4: (27, 8, 17, 5),
    5: (24, 7, 20, 6),
    6: (20, 6, 24, 7),
}
# ---------------------------------------------------------------------------


@dataclass
class SGTIN96:
    """Parsed SGTIN-96 (header 0x30) EPC."""
    filter: int
    partition: int
    company_prefix: str       # zero-padded decimal string
    item_reference: str       # zero-padded decimal string (includes indicator digit)
    serial: int               # 0..2**38 - 1
    raw: bytes

    @property
    def scheme(self) -> str:
        return 'sgtin-96'

    @property
    def pure_identity_uri(self) -> str:
        """GS1 EPC Pure Identity URI: urn:epc:id:sgtin:CP.IR.SERIAL"""
        return 'urn:epc:id:sgtin:%s.%s.%d' % (
            self.company_prefix, self.item_reference, self.serial,
        )

    @property
    def tag_uri(self) -> str:
        """GS1 EPC Tag URI: urn:epc:tag:sgtin-96:FILTER.CP.IR.SERIAL"""
        return 'urn:epc:tag:sgtin-96:%d.%s.%s.%d' % (
            self.filter, self.company_prefix, self.item_reference, self.serial,
        )

    @property
    def gtin14(self) -> str:
        """The reconstructed GTIN-14 (Item Reference's first digit is the
        indicator digit, prepended to Company Prefix + remaining digits of
        Item Reference, with a check digit). Length 14.
        """
        ir = self.item_reference
        indicator = ir[0]
        body13 = indicator + self.company_prefix + ir[1:]
        return body13 + _gtin_check_digit(body13)


def _gtin_check_digit(thirteen_digits: str) -> str:
    """Compute the GS1 GTIN check digit (Modulo 10) for a 13-digit string."""
    if len(thirteen_digits) != 13 or not thirteen_digits.isdigit():
        raise ValueError('GTIN check-digit input must be 13 digits')
    s = 0
    for i, c in enumerate(thirteen_digits):
        s += int(c) * (3 if i % 2 == 0 else 1)
    return str((10 - (s % 10)) % 10)


def _bits(b: bytes) -> str:
    """Return a contiguous MSB-first bit-string for slicing.

    Note: ``'%08b' % byte`` is NOT valid in C-style formatting (``%`` has
    no binary specifier) — use ``format()`` or ``str.format`` instead.
    """
    return ''.join(format(byte, '08b') for byte in b)


def parse_sgtin96(epc: bytes) -> SGTIN96:
    """Parse a 12-byte SGTIN-96 EPC into its component fields.

    Raises ValueError if the input is not SGTIN-96.
    """
    if len(epc) != 12:
        raise ValueError(
            'SGTIN-96 must be exactly 12 bytes (got %d)' % len(epc)
        )
    if epc[0] != 0x30:
        raise ValueError(
            'header is 0x%02X, not 0x30 (SGTIN-96)' % epc[0]
        )
    bits = _bits(epc)
    # Header is bits[0:8]; we skip it.
    filter_val = int(bits[8:11], 2)
    partition = int(bits[11:14], 2)
    if partition not in SGTIN96_PARTITIONS:
        raise ValueError(
            'invalid SGTIN-96 partition value %d (must be 0..6)' % partition
        )
    cp_bits, cp_digits, ir_bits, ir_digits = SGTIN96_PARTITIONS[partition]
    cp_start = 14
    cp_end = cp_start + cp_bits
    ir_end = cp_end + ir_bits
    cp_int = int(bits[cp_start:cp_end], 2)
    ir_int = int(bits[cp_end:ir_end], 2)
    serial = int(bits[ir_end:96], 2)
    return SGTIN96(
        filter=filter_val,
        partition=partition,
        company_prefix=str(cp_int).zfill(cp_digits),
        item_reference=str(ir_int).zfill(ir_digits),
        serial=serial,
        raw=bytes(epc),
    )


@dataclass
class EPCDescription:
    """Best-effort summary of an EPC for display purposes.

    Always populated:
      scheme   — e.g. 'sgtin-96', 'gid-96', 'unknown'
      hex      — uppercase hex string of the raw EPC bytes

    Optional (set for parsed SGTIN-96):
      sgtin    — full SGTIN96 instance with pure_identity_uri, gtin14, etc.
    """
    scheme: str
    hex: str
    sgtin: Optional[SGTIN96] = None

    @property
    def display(self) -> str:
        if self.sgtin is not None:
            return 'SGTIN-96 GTIN %s ser=%d' % (
                self.sgtin.gtin14, self.sgtin.serial,
            )
        return '%s (%s)' % (self.scheme, self.hex)


def describe(epc: bytes) -> EPCDescription:
    """High-level summary suitable for log lines or UI display.

    Returns an EPCDescription with the scheme name and, where supported,
    a fully parsed structure (currently SGTIN-96 only).
    """
    scheme = identify(epc)
    hex_str = epc.hex().upper()
    if scheme == 'sgtin-96' and len(epc) == 12:
        try:
            return EPCDescription(scheme=scheme, hex=hex_str, sgtin=parse_sgtin96(epc))
        except ValueError:
            pass
    return EPCDescription(scheme=scheme, hex=hex_str)
