#!/usr/bin/env python3
"""
Read tags continuously in active mode for 30 seconds.

Active mode: the reader broadcasts every tag it sees in its field,
multiple times per second. This is the lowest-latency mode and is the
right choice for tracking-style applications (checkout, doorway portal).

Usage:
    python3 01_read_tags_active.py [/dev/ttyUSB0]

If no port is given, the driver auto-detects an FTDI device.
"""

import sys
import time

from swrfid import RFIDReader, find_readers


def main(port: str) -> int:
    seen: dict[str, int] = {}

    def on_tags(dev_sn, tags):
        for t in tags:
            seen[t.epc_hex] = seen.get(t.epc_hex, 0) + 1
            print('  %s  ant=%d  rssi=0x%02X' % (t.epc_hex, t.antenna, t.rssi))

    with RFIDReader(port) as r:
        info = r.system_info()
        print('Reader SN %s, sw=%s, hw=%s'
              % (info.serial_hex, info.soft_version, info.hard_version))
        print('Listening for 30 seconds. Wave a tag in front of the antenna...')
        print()

        r.start_active_listener(on_tags)
        r.start_read()
        try:
            time.sleep(30)
        finally:
            r.stop_read()
            r.stop_active_listener()

    print()
    print('Unique EPCs seen: %d' % len(seen))
    for epc, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print('  %s  x%d' % (epc, n))
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        ports = find_readers()
        if not ports:
            sys.exit('No reader auto-detected. Pass the port as argv[1].')
        port = ports[0]
        print('Using auto-detected port: %s' % port)
    sys.exit(main(port))
