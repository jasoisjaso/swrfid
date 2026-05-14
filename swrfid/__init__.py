"""swrfid — Pure-Python driver for the SW UHF RFID reader family.

Supports YanPoDo RU5100 / RU5300 / RU5500 and other "SW protocol" UHF
readers that ship with the SWComApi.dll / SWHidApi.dll / SWNetClientApi.dll
SDK. The wire protocol is the same across all four DLLs and all three
transports (USB serial, RJ45, WiFi).

Quick start::

    from swrfid import RFIDReader, find_readers

    port = (find_readers() or ['/dev/ttyUSB0'])[0]
    with RFIDReader(port) as r:
        info = r.system_info()
        print('Reader SN:', info.serial_hex)
        for tag in r.inventory():
            print('Tag:', tag.epc_hex, 'RSSI:', tag.rssi)
"""

from . import protocol
from .protocol import (
    BROADCAST_ADDR, Cmd, FrameError, MemBank, ParamAddr,
    REGION_FREQ, RF_POWER_MAX, Response, ResponseBuffer, Status,
    SystemInfo, TagEntry, Transport, WorkMode,
    build_frame, checksum,
    parse_active_data, parse_antenna_status, parse_inventory,
    parse_response, parse_system_info,
)
from .reader import DEFAULT_BAUD, DEFAULT_TIMEOUT, ReaderError, RFIDReader
from .ports import PortInfo, find_readers, list_serial_ports

__version__ = "0.1.0"

__all__ = [
    "RFIDReader", "ReaderError", "DEFAULT_BAUD", "DEFAULT_TIMEOUT",
    "Cmd", "ParamAddr", "Transport", "WorkMode", "MemBank", "Status",
    "TagEntry", "SystemInfo", "Response", "ResponseBuffer", "FrameError",
    "REGION_FREQ", "RF_POWER_MAX", "BROADCAST_ADDR",
    "build_frame", "checksum",
    "parse_response", "parse_active_data", "parse_inventory",
    "parse_antenna_status", "parse_system_info",
    "find_readers", "list_serial_ports", "PortInfo",
    "protocol",
    "__version__",
]
