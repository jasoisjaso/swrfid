"""
swrfid.diagnose — Run a "why isn't this working?" checklist against a
reader and produce a human-readable report.

This is what an experienced RFID hacker would walk through manually when
a buyer says "I plugged it in but it doesn't read anything". Each check
returns a status (ok / warn / fail / info) plus a one-line message and an
optional suggestion. The CLI/GUI render this as a colored checklist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import protocol as proto
from .ports import find_readers, list_serial_ports
from .protocol import ParamAddr, WorkMode, REGION_FREQ
from .reader import RFIDReader, ReaderError


STATUS_OK = 'ok'
STATUS_WARN = 'warn'
STATUS_FAIL = 'fail'
STATUS_INFO = 'info'

STATUS_ICONS = {
    STATUS_OK: '[OK]',
    STATUS_WARN: '[!! ]',
    STATUS_FAIL: '[FAIL]',
    STATUS_INFO: '[..]',
}


@dataclass
class Check:
    name: str
    status: str
    message: str
    suggestion: Optional[str] = None

    def format(self, color: bool = False) -> str:
        if color:
            colors = {
                STATUS_OK: '\033[32m',
                STATUS_WARN: '\033[33m',
                STATUS_FAIL: '\033[31m',
                STATUS_INFO: '\033[36m',
            }
            reset = '\033[0m'
            head = '%s%-7s%s %s' % (
                colors.get(self.status, ''),
                STATUS_ICONS.get(self.status, '?'),
                reset,
                self.name,
            )
        else:
            head = '%-7s %s' % (STATUS_ICONS.get(self.status, '?'), self.name)
        line = '%s  %s' % (head, self.message)
        if self.suggestion:
            line += '\n        -> ' + self.suggestion
        return line


def diagnose(port: Optional[str] = None) -> List[Check]:
    """Run the full diagnostic checklist.

    If ``port`` is None, attempts to auto-detect via find_readers().
    Returns a list of Check results in the order they were performed.
    """
    checks: List[Check] = []

    # 1. Port discovery
    if port is None:
        candidates = find_readers()
        if not candidates:
            ports = list_serial_ports()
            if not ports:
                checks.append(Check(
                    'Port discovery', STATUS_FAIL,
                    'No serial ports detected.',
                    'Plug in the reader USB cable. On Linux check `lsusb` for FTDI 0403:6001.',
                ))
            else:
                checks.append(Check(
                    'Port discovery', STATUS_FAIL,
                    'No FTDI reader auto-detected; %d other port(s) present.' % len(ports),
                    'Try `swrfid --port <DEVICE> diagnose` to override, or install FTDI VCP driver.',
                ))
            return checks
        port = candidates[0]
        checks.append(Check(
            'Port discovery', STATUS_OK,
            'Auto-detected FTDI reader on %s.' % port,
        ))
    else:
        checks.append(Check(
            'Port discovery', STATUS_INFO,
            'Using user-specified port %s.' % port,
        ))

    # 2. Open the port and read system info
    reader = RFIDReader(port)
    try:
        reader.open()
    except ReaderError as exc:
        checks.append(Check(
            'Open port', STATUS_FAIL,
            'Could not open %s: %s' % (port, exc),
            'On Linux: sudo usermod -aG dialout $USER && newgrp dialout',
        ))
        return checks
    checks.append(Check('Open port', STATUS_OK, '%s opened at 115200 8N1.' % port))

    try:
        info = reader.system_info()
    except ReaderError as exc:
        checks.append(Check(
            'System info', STATUS_FAIL,
            'CMD_READ_SYSTEM_PARAM failed: %s' % exc,
            'Try a different baud (--baud 9600) or address (--addr 0x00).',
        ))
        reader.close()
        return checks
    checks.append(Check(
        'System info', STATUS_OK,
        'Reader SN %s sw=%s hw=%s.'
        % (info.serial_hex, info.soft_version, info.hard_version),
    ))

    # 3. Module health
    try:
        ok = reader.check_module()
    except ReaderError as exc:
        checks.append(Check(
            'Module health', STATUS_FAIL,
            'CMD_CHECK_MODULE failed: %s' % exc,
        ))
        ok = None
    if ok is True:
        checks.append(Check('Module health', STATUS_OK, 'RF module reports OK.'))
    elif ok is False:
        checks.append(Check(
            'Module health', STATUS_FAIL,
            'RF module reports ERROR.',
            'The RF front-end is non-functional. Likely a hardware fault.',
        ))

    # 4. RF power
    try:
        power = reader.read_one_param(ParamAddr.RF_POWER)
        if power == 0:
            checks.append(Check(
                'RF power', STATUS_WARN,
                'Power byte is 0x00 — transmitter is off.',
                'Run `swrfid set-power 0x14` (mid-range) or 0x1A (5300 max).',
            ))
        else:
            checks.append(Check(
                'RF power', STATUS_OK,
                'Power byte 0x%02X.' % power,
            ))
    except ReaderError as exc:
        checks.append(Check('RF power', STATUS_WARN, 'Could not read power: %s' % exc))

    # 5. Frequency / region
    try:
        n1, n2 = reader.read_freq_bytes()
        matches = [r for r, key in REGION_FREQ.items() if key == (n1, n2)]
        if matches:
            checks.append(Check(
                'Region', STATUS_OK,
                'Region %s (0x%02X 0x%02X).' % (matches[0], n1, n2),
            ))
        else:
            checks.append(Check(
                'Region', STATUS_WARN,
                'Unrecognised region bytes 0x%02X 0x%02X.' % (n1, n2),
                'Run `swrfid set-region US` (or EU/CN/KR/AU/JP) to match your area.',
            ))
    except ReaderError as exc:
        checks.append(Check('Region', STATUS_WARN, 'Could not read region: %s' % exc))

    # 6. Work mode
    try:
        mode = reader.read_one_param(ParamAddr.WORK_MODE)
        mode_name = WorkMode(mode).name if mode in WorkMode._value2member_map_ else 'UNKNOWN(0x%02X)' % mode
        checks.append(Check(
            'Work mode', STATUS_INFO,
            'WorkMode %s.' % mode_name,
            None if mode == int(WorkMode.ANSWER) else
            'For one-shot inventory tests, run `swrfid set-mode ANSWER`.',
        ))
    except ReaderError as exc:
        checks.append(Check('Work mode', STATUS_WARN, 'Could not read mode: %s' % exc))

    # 7. Antenna status
    try:
        ant = reader.antenna_status()
        present = sum(1 for v in ant.values() if v)
        if present == 0:
            checks.append(Check(
                'Antenna', STATUS_WARN,
                'No antennas detected on any port.',
                'Single-antenna readers (RU5100/single-port 5300) may report 0 here — informational only. '
                'On multi-antenna readers (RU5500), check antenna cables and termination.',
            ))
        else:
            checks.append(Check(
                'Antenna', STATUS_OK,
                '%d antenna(s) detected: %s.'
                % (present, ', '.join('A%d' % n for n, v in ant.items() if v)),
            ))
    except ReaderError:
        # CMD_CHECK_ANT is for multi-antenna models; single-ant readers may not implement it.
        checks.append(Check(
            'Antenna', STATUS_INFO,
            'CMD_CHECK_ANT not supported (likely a single-antenna model).',
        ))

    # 8. Inventory smoke test
    try:
        # Switch to answer mode for the test; restore afterwards.
        original_mode = None
        try:
            original_mode = reader.read_one_param(ParamAddr.WORK_MODE)
            if original_mode != int(WorkMode.ANSWER):
                reader.set_work_mode(WorkMode.ANSWER)
        except ReaderError:
            pass
        tags = reader.inventory()
        if tags:
            checks.append(Check(
                'Inventory smoke test', STATUS_OK,
                '%d tag(s) detected.' % len(tags),
            ))
            for t in tags[:3]:
                checks.append(Check(
                    '  tag', STATUS_INFO,
                    'EPC=%s ant=%d rssi=0x%02X' % (t.epc_hex, t.antenna, t.rssi),
                ))
        else:
            checks.append(Check(
                'Inventory smoke test', STATUS_WARN,
                'No tags detected in field.',
                'Place a known-good Gen2 tag on the antenna face and re-run. '
                'If still none: try `swrfid set-power 0x1A` to maximise range, '
                'or confirm region with `swrfid read-freq`.',
            ))
        if original_mode is not None and original_mode != int(WorkMode.ANSWER):
            try:
                reader.set_one_param(ParamAddr.WORK_MODE, original_mode)
            except ReaderError:
                pass
    except ReaderError as exc:
        checks.append(Check(
            'Inventory smoke test', STATUS_FAIL,
            'CMD_INVENTORY_TAG failed: %s' % exc,
        ))

    reader.close()
    return checks


def print_report(checks: List[Check], color: bool = False) -> int:
    """Print a diagnostic report and return an exit code.

    Exit codes: 0 if no fail/warn, 1 if any warn, 2 if any fail.
    """
    print('swrfid diagnostic report')
    print('=' * 30)
    for c in checks:
        print(c.format(color=color))
    print()
    fails = sum(1 for c in checks if c.status == STATUS_FAIL)
    warns = sum(1 for c in checks if c.status == STATUS_WARN)
    if fails:
        print('%d failure(s), %d warning(s).' % (fails, warns))
        return 2
    if warns:
        print('No failures. %d warning(s) — see suggestions above.' % warns)
        return 1
    print('All checks passed.')
    return 0
