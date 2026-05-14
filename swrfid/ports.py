"""Cross-platform serial-port discovery for SW UHF RFID readers.

Looks for FTDI FT232 USB-serial bridges (VID 0x0403, PID 0x6001) since
that is the chip used by the YanPoDo RU5305 and most SW-family readers.
Use :func:`list_serial_ports` to see every port the OS knows about —
useful for diagnosing setups where the reader uses a different
USB-serial chip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from serial.tools import list_ports

logger = logging.getLogger(__name__)

FTDI_VID = 0x0403
FTDI_FT232_PID = 0x6001


@dataclass
class PortInfo:
    device: str
    description: str
    vid: Optional[int]
    pid: Optional[int]
    serial_number: Optional[str]
    is_ftdi: bool

    @property
    def vidpid(self) -> str:
        if self.vid is None or self.pid is None:
            return 'unknown'
        return '%04X:%04X' % (self.vid, self.pid)


def list_serial_ports() -> List[PortInfo]:
    """Return every serial port the OS knows about, FTDI-flagged."""
    out = []
    for p in list_ports.comports():
        is_ftdi = (p.vid == FTDI_VID and p.pid == FTDI_FT232_PID)
        out.append(PortInfo(
            device=p.device,
            description=p.description or '',
            vid=p.vid,
            pid=p.pid,
            serial_number=p.serial_number,
            is_ftdi=is_ftdi,
        ))
    return out


def find_readers() -> List[str]:
    """Return likely-RFID-reader serial-port device paths.

    Picks FTDI FT232 devices first (the chip in the RU5305 and most
    SW-family readers) and sorts by USB serial number so the same
    physical reader gets the same device path across reboots, replugs,
    and USB-port changes. Returns ``[]`` if no FTDI devices are present.
    """
    ports = list_serial_ports()
    ftdi = [p for p in ports if p.is_ftdi]
    ftdi.sort(key=lambda p: (p.serial_number or '', p.device))
    return [p.device for p in ftdi]
