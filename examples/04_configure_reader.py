#!/usr/bin/env python3
"""
Configure the reader for first-time use:
  - Set frequency region
  - Set RF power (conservative mid-range default)
  - Set work mode
  - Enable beep on successful read
  - Print resulting parameters

Edit the constants below to match your deployment.

Usage:
    python3 04_configure_reader.py [/dev/ttyUSB0]
"""

import sys

from swrfid import RFIDReader, WorkMode, ParamAddr, find_readers


# >>> Edit these for your deployment <<<
REGION = 'US'                       # US / EU / CN / KR / AU / JP / ...
RF_POWER_BYTE = 0x14                # ~20 dBm-byte; safe mid-range
WORK_MODE = WorkMode.ANSWER         # ANSWER (poll) | ACTIVE (broadcast) | TRIGGER
BEEP_ON_READ = True


def main(port: str) -> int:
    with RFIDReader(port) as r:
        info = r.system_info()
        print('Configuring %s (sw=%s, hw=%s)'
              % (info.serial_hex, info.soft_version, info.hard_version))

        r.set_region(REGION)
        r.set_rf_power(RF_POWER_BYTE)
        r.set_work_mode(WORK_MODE)
        r.set_beep(BEEP_ON_READ)

        print()
        print('Done. Current parameters:')
        print('  Region    : %s' % REGION)
        print('  RF power  : 0x%02X' % r.read_one_param(ParamAddr.RF_POWER))
        print('  Work mode : %s'
              % WorkMode(r.read_one_param(ParamAddr.WORK_MODE)).name)
        print('  Beep      : %s' % bool(r.read_one_param(ParamAddr.BEEP_ENABLE)))

        n1, n2 = r.read_freq_bytes()
        print('  Freq bytes: 0x%02X 0x%02X' % (n1, n2))
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
