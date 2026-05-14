"""
swrfid.cli — command-line interface for the SW RFID driver.

Run with ``python3 -m swrfid`` or via the ``swrfid`` console_script
installed by pip. Every subcommand maps to one or two documented
protocol opcodes. Use ``--dry-run`` to print the bytes that would be
sent without opening the port — handy for testing without hardware.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Optional

from . import __version__
from . import protocol as proto
from .protocol import Cmd, MemBank, ParamAddr, WorkMode
from .reader import RFIDReader, ReaderError, DEFAULT_BAUD
from .ports import find_readers, list_serial_ports


def _strip_hex_prefix(s: str) -> str:
    s = s.strip().lower().replace(' ', '')
    if s.startswith('0x'):
        s = s[2:]
    return s


def _hex_bytes(s: str) -> bytes:
    s = _strip_hex_prefix(s)
    if len(s) % 2:
        raise argparse.ArgumentTypeError("hex must be even-length: %r" % s)
    try:
        return bytes.fromhex(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _hex_byte(s: str) -> int:
    try:
        v = int(_strip_hex_prefix(s), 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    if not (0 <= v <= 0xFF):
        raise argparse.ArgumentTypeError("byte out of range: %d" % v)
    return v


def _mem_bank(s: str) -> MemBank:
    key = s.strip().upper()
    aliases = {
        'R': 'RESERVED', 'P': 'RESERVED', 'PASSWORD': 'RESERVED',
        'E': 'EPC', 'T': 'TID', 'U': 'USER',
    }
    key = aliases.get(key, key)
    try:
        return MemBank[key]
    except KeyError:
        raise argparse.ArgumentTypeError(
            "unknown bank: %r. Use RESERVED/EPC/TID/USER" % s
        )


def _work_mode(s: str) -> WorkMode:
    try:
        return WorkMode[s.strip().upper()]
    except KeyError:
        raise argparse.ArgumentTypeError(
            "unknown mode: %r. Use ANSWER/ACTIVE/TRIGGER" % s
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='swrfid',
        description=("Pure-Python driver for SW UHF RFID readers "
                     "(YanPoDo RU5100/5300/5500). v%s" % __version__),
    )
    parser.add_argument('--port', help="Serial port (e.g. /dev/ttyUSB0, COM3)")
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD)
    parser.add_argument('--addr', type=_hex_byte, default=0xFF,
                        help="Reader address byte (default 0xFF broadcast)")
    parser.add_argument('--timeout', type=float, default=2.0)
    parser.add_argument('--dry-run', action='store_true',
                        help="Print outgoing frames; do not open the port")
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--version', action='version',
                        version='swrfid %s' % __version__)

    sub = parser.add_subparsers(dest='action', required=True)

    sub.add_parser('list-ports', help="Show every serial port the OS sees")
    sub.add_parser('info', help="Read system info (softver / hwver / SN)")
    sub.add_parser('check-module', help="Module status")
    sub.add_parser('antenna', help="Antenna presence bitmap")
    sub.add_parser('read-freq', help="Read current frequency / region")
    sub.add_parser('inventory', help="Command-mode single-pass inventory")
    sub.add_parser('start-read', help="Send CMD_START_READ (0x41)")
    sub.add_parser('stop-read', help="Send CMD_STOP_READ (0x40)")

    sp = sub.add_parser('set-power', help="Set RF power byte")
    sp.add_argument('value', type=_hex_byte,
                    help="Power byte 0..0x11 (5100), 0..0x1A (5300), 0..0x1E (5500)")

    sp = sub.add_parser('set-mode', help="Set work mode")
    sp.add_argument('mode', type=_work_mode, help="ANSWER | ACTIVE | TRIGGER")

    sp = sub.add_parser('set-region', help="Set frequency region")
    sp.add_argument('region', help="US, EU, CN, KR, AU, JP, etc.")

    sp = sub.add_parser('listen',
                        help="Active-mode listener — print tags as they arrive")
    sp.add_argument('--seconds', type=float, default=10.0)
    sp.add_argument('--start-read', action='store_true',
                    help="Send START_READ first; STOP_READ on exit")

    sp = sub.add_parser('read-tag', help="Read tag memory")
    sp.add_argument('bank', type=_mem_bank)
    sp.add_argument('word_addr', type=int)
    sp.add_argument('word_len', type=int)
    sp.add_argument('--password', type=_hex_bytes,
                    default=b'\x00\x00\x00\x00')

    sp = sub.add_parser('write-tag', help="Write tag memory (word-aligned)")
    sp.add_argument('bank', type=_mem_bank)
    sp.add_argument('word_addr', type=int)
    sp.add_argument('payload', type=_hex_bytes,
                    help="Hex bytes to write (even length)")
    sp.add_argument('--password', type=_hex_bytes,
                    default=b'\x00\x00\x00\x00')

    sp = sub.add_parser('write-epc',
                        help="Re-program a tag's EPC (12 bytes / 96 bits)")
    sp.add_argument('epc', type=_hex_bytes,
                    help="New EPC, 24 hex chars / 12 bytes")
    sp.add_argument('--password', type=_hex_bytes,
                    default=b'\x00\x00\x00\x00')

    sp = sub.add_parser('relay', help="Relay control")
    sp.add_argument('state', choices=['on', 'off', 'close', 'release'])

    sp = sub.add_parser('raw', help="Send arbitrary command")
    sp.add_argument('cmd', type=_hex_byte)
    sp.add_argument('data', type=_hex_bytes, nargs='?', default=b'')

    sp = sub.add_parser('get-param', help="Read a single parameter")
    sp.add_argument('param', choices=[x.name for x in ParamAddr])

    sp = sub.add_parser('set-param', help="Set a single parameter")
    sp.add_argument('param', choices=[x.name for x in ParamAddr])
    sp.add_argument('value', type=_hex_byte)

    return parser


def _build_only(args) -> bytes:
    a = args.action
    if a == 'list-ports':
        return b''
    if a == 'info':
        return proto.cmd_read_system_param(args.addr)
    if a == 'check-module':
        return proto.cmd_check_module(args.addr)
    if a == 'antenna':
        return proto.cmd_check_ant(args.addr)
    if a == 'set-power':
        return proto.cmd_set_rf_power(args.value, args.addr)
    if a == 'set-mode':
        return proto.cmd_set_work_mode(args.mode, args.addr)
    if a == 'set-region':
        return proto.cmd_set_freq_region(args.region, args.addr)
    if a == 'read-freq':
        return proto.cmd_read_freq(args.addr)
    if a == 'inventory':
        return proto.cmd_inventory(args.addr)
    if a == 'start-read':
        return proto.cmd_start_read(args.addr)
    if a == 'stop-read':
        return proto.cmd_stop_read(args.addr)
    if a == 'read-tag':
        return proto.cmd_read_tag(args.bank, args.word_addr, args.word_len,
                                  args.password, args.addr)
    if a == 'write-tag':
        return proto.cmd_write_tag(args.bank, args.word_addr, args.payload,
                                   args.password, args.addr)
    if a == 'write-epc':
        if len(args.epc) != 12:
            raise argparse.ArgumentTypeError(
                "EPC must be exactly 12 bytes (96 bits); got %d" % len(args.epc)
            )
        return proto.cmd_write_tag(MemBank.EPC, 2, args.epc,
                                   args.password, args.addr)
    if a == 'relay':
        if args.state in ('on', 'close'):
            return proto.cmd_relay_close(args.addr)
        return proto.cmd_relay_release(args.addr)
    if a == 'raw':
        return proto.build_frame(args.cmd, args.data, args.addr)
    if a == 'get-param':
        return proto.cmd_read_one_param(ParamAddr[args.param], args.addr)
    if a == 'set-param':
        return proto.cmd_set_one_param(ParamAddr[args.param], args.value,
                                       args.addr)
    if a == 'listen':
        return proto.cmd_start_read(args.addr) if args.start_read else b''
    raise ValueError("unknown action: %s" % a)


def _print_ports() -> int:
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
        print("Plug in the reader and try again. On Linux, check `lsusb`.")
        return 1
    print("%-22s %-10s %-7s %s" % ("Device", "VID:PID", "FTDI?", "Description"))
    print("-" * 72)
    for p in ports:
        print("%-22s %-10s %-7s %s" % (
            p.device, p.vidpid, 'yes' if p.is_ftdi else 'no',
            p.description,
        ))
    likely = find_readers()
    if likely:
        print()
        print("Likely SW RFID reader(s):")
        for d in likely:
            print("  %s" % d)
    else:
        print()
        print("No FTDI devices detected — none of the ports above look like "
              "an SW RFID reader. Use --port <DEVICE> to override.")
    return 0


def _resolve_port(args) -> str:
    if args.port:
        return args.port
    found = find_readers()
    if len(found) == 1:
        print("(auto-detected reader on %s)" % found[0], file=sys.stderr)
        return found[0]
    if len(found) > 1:
        print("Multiple readers found. Pick one with --port:", file=sys.stderr)
        for d in found:
            print("  --port %s" % d, file=sys.stderr)
        sys.exit(2)
    print("No reader auto-detected. Use --port, or `swrfid list-ports`.",
          file=sys.stderr)
    sys.exit(2)


def _run_live(reader: RFIDReader, args) -> int:
    a = args.action
    if a == 'info':
        info = reader.system_info()
        print("Software version: %s" % info.soft_version)
        print("Hardware version: %s" % info.hard_version)
        print("Serial number:    %s" % info.serial_hex)
        return 0
    if a == 'check-module':
        ok = reader.check_module()
        print("Module: %s" % ("OK" if ok else "ERROR"))
        return 0 if ok else 1
    if a == 'antenna':
        for ant, present in reader.antenna_status().items():
            print("  Ant%2d: %s" % (ant, 'present' if present else '-'))
        return 0
    if a == 'set-power':
        reader.set_rf_power(args.value)
        print("RF power byte set to 0x%02X" % args.value)
        return 0
    if a == 'set-mode':
        reader.set_work_mode(args.mode)
        print("Work mode set to %s" % args.mode.name)
        return 0
    if a == 'set-region':
        reader.set_freq_region(args.region)
        print("Region set to %s" % args.region.upper())
        return 0
    if a == 'read-freq':
        n1, n2 = reader.read_freq_bytes()
        print("Freq bytes: 0x%02X 0x%02X" % (n1, n2))
        for region, (rn1, rn2) in proto.REGION_FREQ.items():
            if (rn1, rn2) == (n1, n2):
                print("  matches region: %s" % region)
                break
        return 0
    if a == 'inventory':
        tags = reader.inventory()
        print("Tags found: %d" % len(tags))
        for t in tags:
            print("  EPC=%s  ant=%d  rssi=0x%02X"
                  % (t.epc_hex, t.antenna, t.rssi))
        return 0
    if a == 'listen':
        seen = {}

        def cb(dev_sn, tags):
            for t in tags:
                seen[t.epc_hex] = seen.get(t.epc_hex, 0) + 1
                print("  EPC=%s  ant=%d  rssi=0x%02X"
                      % (t.epc_hex, t.antenna, t.rssi))

        reader.start_active_listener(cb)
        if args.start_read:
            reader.start_read()
        try:
            time.sleep(args.seconds)
        finally:
            if args.start_read:
                try:
                    reader.stop_read()
                except Exception:
                    pass
            reader.stop_active_listener()
        print()
        print("Unique EPCs in %.1fs: %d" % (args.seconds, len(seen)))
        for epc, n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print("  %s: %d" % (epc, n))
        return 0
    if a == 'read-tag':
        data = reader.read_tag(args.bank, args.word_addr, args.word_len,
                               args.password)
        print("Read %d bytes from %s: %s"
              % (len(data), args.bank.name, data.hex().upper()))
        return 0
    if a == 'write-tag':
        reader.write_tag(args.bank, args.word_addr, args.payload,
                         args.password)
        print("Wrote %d bytes to %s@%d"
              % (len(args.payload), args.bank.name, args.word_addr))
        return 0
    if a == 'write-epc':
        if len(args.epc) != 12:
            print("EPC must be exactly 12 bytes (96 bits); got %d"
                  % len(args.epc), file=sys.stderr)
            return 2
        tags_before = reader.inventory()
        if len(tags_before) != 1:
            print("Expected exactly 1 tag in field, found %d. "
                  "Move other tags away and try again."
                  % len(tags_before), file=sys.stderr)
            return 1
        print("Re-programming tag %s -> %s"
              % (tags_before[0].epc_hex, args.epc.hex().upper()))
        reader.write_tag(MemBank.EPC, 2, args.epc, args.password)
        readback = reader.read_tag(MemBank.EPC, 2, 6)
        if readback == args.epc:
            print("Verified: tag now reports EPC = %s" % readback.hex().upper())
            return 0
        print("Verification failed. Wrote %s, read back %s"
              % (args.epc.hex().upper(), readback.hex().upper()), file=sys.stderr)
        return 1
    if a == 'relay':
        if args.state in ('on', 'close'):
            reader.relay_close()
            print("Relay closed")
        else:
            reader.relay_release()
            print("Relay released")
        return 0
    if a == 'start-read':
        reader.start_read()
        print("START_READ ACKed")
        return 0
    if a == 'stop-read':
        reader.stop_read()
        print("STOP_READ ACKed")
        return 0
    if a == 'get-param':
        v = reader.read_one_param(ParamAddr[args.param])
        print("%s = 0x%02X (%d)" % (args.param, v, v))
        return 0
    if a == 'set-param':
        reader.set_one_param(ParamAddr[args.param], args.value)
        print("%s set to 0x%02X" % (args.param, args.value))
        return 0
    if a == 'raw':
        rsp = reader.send_command(args.cmd, args.data)
        print("Response cmd=0x%02X status=0x%02X data=%s"
              % (rsp.cmd, rsp.status, rsp.data.hex().upper()))
        return 0 if rsp.ok() else 1
    print("Unknown action: %s" % a, file=sys.stderr)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    if args.action == 'list-ports':
        return _print_ports()
    if args.dry_run:
        frame = _build_only(args)
        if not frame:
            print("(no frame for this action in dry-run)")
            return 0
        print("Would send %d bytes: %s" % (len(frame), frame.hex().upper()))
        print("  Decoded: HEAD=%s LEN=%d ADDR=0x%02X CMD=0x%02X DATA=%s CKSUM=0x%02X"
              % (frame[:2].hex().upper(),
                 int.from_bytes(frame[2:4], 'big'),
                 frame[4], frame[5],
                 frame[6:-1].hex().upper(),
                 frame[-1]))
        return 0
    port = _resolve_port(args)
    try:
        with RFIDReader(port, args.baud, args.addr, args.timeout) as reader:
            return _run_live(reader, args)
    except ReaderError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
