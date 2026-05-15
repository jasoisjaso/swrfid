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
from . import epc
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
from .epc import (
    EPCDescription, SGTIN96, describe as describe_epc,
    identify as identify_epc, parse_sgtin96,
)

__version__ = "0.2.0.dev0"

__all__ = [
    # Core driver
    "RFIDReader", "ReaderError", "DEFAULT_BAUD", "DEFAULT_TIMEOUT",
    "Cmd", "ParamAddr", "Transport", "WorkMode", "MemBank", "Status",
    "TagEntry", "SystemInfo", "Response", "ResponseBuffer", "FrameError",
    "REGION_FREQ", "RF_POWER_MAX", "BROADCAST_ADDR",
    "build_frame", "checksum",
    "parse_response", "parse_active_data", "parse_inventory",
    "parse_antenna_status", "parse_system_info",
    # Port discovery
    "find_readers", "list_serial_ports", "PortInfo",
    # EPC identification + parsing
    "EPCDescription", "SGTIN96", "describe_epc", "identify_epc",
    "parse_sgtin96",
    # Sub-packages
    "protocol", "epc",
    # Version
    "__version__",
]
