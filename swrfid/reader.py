"""
swrfid.reader — high-level pure-Python driver for the SW UHF RFID
reader family. Wraps :mod:`swrfid.protocol` with a pyserial transport,
a streaming response framer, and timeout / retry handling.

Capabilities (all derived from the documented opcode set; see
docs/PROTOCOL.md):

  - System info (CMD_READ_SYSTEM_PARAM 0x10)
  - Single-parameter read / write (CMD 0x23 / 0x24)
  - RF power, work mode, baud rate, beeper toggle
  - Frequency / region (CMD 0x3E / 0x3F)
  - Start / stop active-mode reading (CMD 0x41 / 0x40)
  - Active-mode tag listener (CMD 0x45 broadcasts) via callback
  - Command-mode inventory (CMD 0x01)
  - Tag memory read / write across all four banks (CMD 0x02 / 0x03)
  - Antenna status bitmap (CMD 0xE1)
  - Module health (CMD 0xE0)
  - Relay close / release (CMD 0x85 / 0x86)

NOT implemented (out of the documented surface):
  - Full device-param struct R/W (CMD 0x20 / 0x21) — 22-field struct.
    Use :meth:`send_raw` if you need it.
  - Net-params (0x26 / 0x27) and special-params (0x2E / 0x2F) — opaque
    blobs; field layout requires capture from ReaderSoftV4.2.
  - Lock / Kill / BlockPermalock — not in firmware.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Tuple

import serial  # pyserial

from . import protocol as p
from .protocol import (
    BROADCAST_ADDR,
    Cmd,
    FrameError,
    MemBank,
    ParamAddr,
    Response,
    ResponseBuffer,
    SystemInfo,
    TagEntry,
    Transport,
    WorkMode,
    parse_active_data,
    parse_antenna_status,
    parse_inventory,
    parse_system_info,
)

logger = logging.getLogger(__name__)

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 2.0


class ReaderError(RuntimeError):
    """Raised when the reader doesn't respond or returns a failure status."""


class RFIDReader:
    """High-level pure-Python driver for the RU5305 family.

    Context-manager use::

        with RFIDReader('/dev/ttyUSB0') as r:
            info = r.system_info()
            r.set_rf_power(0x1A)          # 26 dBm byte on a 5300
            r.set_work_mode(WorkMode.ANSWER)
            tags = r.inventory()

    Active-mode use::

        r = RFIDReader('/dev/ttyUSB0')
        r.open()
        r.start_active_listener(lambda dev_sn, tags: print(tags))
        r.start_read()
        ...
        r.stop_read()
        r.close()
    """

    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        addr: int = BROADCAST_ADDR,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.port = port
        self.baud = baud
        self.addr = addr
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._framer = ResponseBuffer()
        self._io_lock = threading.Lock()
        self._listener_thread: Optional[threading.Thread] = None
        self._listener_stop = threading.Event()
        self._active_cb: Optional[Callable[[bytes, List[TagEntry]], None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        if self._serial is not None:
            return
        logger.info("Opening %s @ %d baud", self.port, self.baud)
        try:
            self._serial = serial.Serial(
                self.port,
                baudrate=self.baud,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=0.1,
            )
        except PermissionError as exc:
            raise ReaderError(
                "Permission denied on %s: %s. On Linux, add the user to "
                "the 'dialout' group: "
                "sudo usermod -aG dialout $USER && newgrp dialout"
                % (self.port, exc)
            ) from exc
        # Some FTDI bridges drop the first bytes after open; give them a beat.
        time.sleep(0.2)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._framer = ResponseBuffer()

    def close(self) -> None:
        self.stop_active_listener()
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def __enter__(self) -> "RFIDReader":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ------------------------------------------------------------------
    # Low-level send/receive
    # ------------------------------------------------------------------

    def send_raw(self, frame: bytes) -> None:
        """Write raw bytes to the serial port. Use the protocol module's
        ``build_frame`` to construct properly framed commands."""
        if self._serial is None:
            raise ReaderError("not open")
        logger.debug("TX %s", frame.hex())
        self._serial.write(frame)

    def send_command(
        self,
        cmd: int,
        data: bytes = b'',
        timeout: Optional[float] = None,
        expect_status_ok: bool = False,
    ) -> Response:
        """Send a command and return the matching response.

        Unsolicited frames (active-mode broadcasts, heartbeats) interleaved
        with the awaited response are dispatched to the active-mode
        callback if registered, otherwise discarded. If ``expect_status_ok``
        is True, raises ReaderError when the response status byte is not
        Status.SUCCESS.
        """
        if self._serial is None:
            raise ReaderError("not open")
        if timeout is None:
            timeout = self.timeout
        frame = p.build_frame(cmd, data, self.addr)
        with self._io_lock:
            logger.debug("TX cmd=0x%02X data=%s", cmd, data.hex())
            self._serial.write(frame)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = self._serial.read(256)
                if not chunk:
                    continue
                for rsp in self._framer.feed(chunk):
                    logger.debug(
                        "RX cmd=0x%02X status=%d data=%s",
                        rsp.cmd, rsp.status, rsp.data.hex(),
                    )
                    if rsp.cmd == cmd:
                        if expect_status_ok and not rsp.ok():
                            raise ReaderError(
                                "cmd 0x%02X returned status=0x%02X"
                                % (cmd, rsp.status)
                            )
                        return rsp
                    self._dispatch_unsolicited(rsp)
        raise ReaderError(
            "no response for cmd 0x%02X within %.2fs" % (cmd, timeout)
        )

    def _dispatch_unsolicited(self, rsp: Response) -> None:
        if rsp.cmd == Cmd.ACTIVE_DATA and self._active_cb is not None:
            try:
                dev_sn, tags = parse_active_data(rsp)
                self._active_cb(dev_sn, tags)
            except FrameError as exc:
                logger.warning("Malformed ACTIVE_DATA: %s", exc)
        elif rsp.cmd == Cmd.HEARTBEAT:
            logger.debug("Heartbeat: %s", rsp.data.hex())
        else:
            logger.debug(
                "Unsolicited cmd=0x%02X: %s", rsp.cmd, rsp.data.hex()
            )

    # ------------------------------------------------------------------
    # System / status
    # ------------------------------------------------------------------

    def system_info(self) -> SystemInfo:
        rsp = self.send_command(Cmd.READ_SYSTEM_PARAM, expect_status_ok=True)
        return parse_system_info(rsp)

    def check_module(self) -> bool:
        rsp = self.send_command(Cmd.CHECK_MODULE)
        return rsp.ok()

    def antenna_status(self) -> dict:
        rsp = self.send_command(Cmd.CHECK_ANT, expect_status_ok=True)
        return parse_antenna_status(rsp)

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def read_one_param(self, param: ParamAddr) -> int:
        rsp = self.send_command(
            Cmd.READ_ONE_PARAM, bytes([int(param)]), expect_status_ok=True,
        )
        if len(rsp.data) < 2:
            raise ReaderError(
                "short READ_ONE_PARAM response: %s" % rsp.data.hex()
            )
        return rsp.data[1]

    def set_one_param(self, param: ParamAddr, value: int) -> None:
        if not (0 <= value <= 0xFF):
            raise ValueError("value out of range: %d" % value)
        self.send_command(
            Cmd.SET_ONE_PARAM, bytes([int(param), value]),
            expect_status_ok=True,
        )

    def set_rf_power(self, db_byte: int) -> None:
        self.set_one_param(ParamAddr.RF_POWER, db_byte)

    def set_work_mode(self, mode: WorkMode) -> None:
        self.set_one_param(ParamAddr.WORK_MODE, int(mode))

    def set_beep(self, enabled: bool) -> None:
        self.set_one_param(ParamAddr.BEEP_ENABLE, 1 if enabled else 0)

    def read_freq_bytes(self) -> Tuple[int, int]:
        rsp = self.send_command(Cmd.READ_FREQ, expect_status_ok=True)
        if len(rsp.data) < 2:
            raise ReaderError("short READ_FREQ response: %s" % rsp.data.hex())
        return rsp.data[0], rsp.data[1]

    def set_freq_region(self, region: str) -> None:
        n1, n2 = p.REGION_FREQ[region.upper()]
        self.send_command(
            Cmd.SET_FREQ, bytes([n1, n2]), expect_status_ok=True,
        )

    # ------------------------------------------------------------------
    # Read control
    # ------------------------------------------------------------------

    def start_read(self) -> None:
        self.send_command(Cmd.START_READ, expect_status_ok=True)

    def stop_read(self) -> None:
        self.send_command(Cmd.STOP_READ, expect_status_ok=True)

    # ------------------------------------------------------------------
    # Inventory / tag I/O
    # ------------------------------------------------------------------

    def inventory(self) -> List[TagEntry]:
        rsp = self.send_command(Cmd.INVENTORY_TAG)
        return parse_inventory(rsp)

    def read_tag(
        self,
        bank: MemBank,
        word_addr: int,
        word_len: int,
        password: bytes = b'\x00\x00\x00\x00',
    ) -> bytes:
        frame_data = bytes([int(bank), word_addr, word_len]) + password
        rsp = self.send_command(
            Cmd.READ_TAG_DATA, frame_data, expect_status_ok=True,
        )
        return rsp.data

    def write_tag(
        self,
        bank: MemBank,
        word_addr: int,
        payload: bytes,
        password: bytes = b'\x00\x00\x00\x00',
    ) -> None:
        if len(payload) % 2:
            raise ValueError("payload must be word-aligned")
        word_len = len(payload) // 2
        frame_data = (
            bytes([int(bank), word_addr, word_len]) + password + payload
        )
        self.send_command(
            Cmd.WRITE_TAG_DATA, frame_data, expect_status_ok=True,
        )

    # ------------------------------------------------------------------
    # Relay
    # ------------------------------------------------------------------

    def relay_close(self) -> None:
        self.send_command(Cmd.CLOSE_RELAY, expect_status_ok=True)

    def relay_release(self) -> None:
        self.send_command(Cmd.RELEASE_RELAY, expect_status_ok=True)

    # ------------------------------------------------------------------
    # Active-mode listener
    # ------------------------------------------------------------------

    def start_active_listener(
        self,
        callback: Callable[[bytes, List[TagEntry]], None],
    ) -> None:
        """Spawn a daemon thread that reads serial bytes and dispatches
        every ACTIVE_DATA broadcast to ``callback(dev_sn, tags)``.

        ``send_command`` shares the same callback when an active-mode
        broadcast lands while we are awaiting a command response, so the
        listener and synchronous commands coexist cleanly.
        """
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        self._active_cb = callback
        self._listener_stop.clear()
        t = threading.Thread(
            target=self._listener_loop, daemon=True,
            name="rfid-v2-listener",
        )
        self._listener_thread = t
        t.start()

    def stop_active_listener(self) -> None:
        self._listener_stop.set()
        t = self._listener_thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._listener_thread = None

    def _listener_loop(self) -> None:
        while not self._listener_stop.is_set():
            if self._serial is None:
                break
            with self._io_lock:
                try:
                    chunk = self._serial.read(256)
                except serial.SerialException as exc:
                    logger.warning("Listener serial error: %s", exc)
                    break
                if chunk:
                    for rsp in self._framer.feed(chunk):
                        self._dispatch_unsolicited(rsp)
            if not chunk:
                time.sleep(0.02)
