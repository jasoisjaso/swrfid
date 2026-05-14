#!/usr/bin/env python3
"""
One-shot inventory in command mode (no continuous broadcasting).

Command mode: the reader stays silent until you ask. This is the right
choice for "scan now and tell me what you see" UIs — e.g. a button on
a desktop app, or a barcode-scanner-style workflow.

Usage:
    python3 02_inventory_once.py [/dev/ttyUSB0]
"""

import sys

from swrfid import RFIDReader, WorkMode, find_readers


def main(port: str) -> int:
    with RFIDReader(port) as r:
        # Command-mode inventory requires WorkMode.ANSWER; switch in case
        # the reader is currently in ACTIVE broadcasting mode.
        r.set_work_mode(WorkMode.ANSWER)

        tags = r.inventory()
        if not tags:
            print('No tags in field.')
            return 0
        print('Found %d tag(s):' % len(tags))
        for t in tags:
            print('  EPC=%s  ant=%d  rssi=0x%02X'
                  % (t.epc_hex, t.antenna, t.rssi))
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        ports = find_readers()
        if not ports:
            sys.exit('No reader auto-detected. Pass the port as argv[1].')
        port = ports[0]
    sys.exit(main(port))
