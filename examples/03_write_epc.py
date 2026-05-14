#!/usr/bin/env python3
"""
Re-program a tag's EPC ID.

WARNING: This rewrites the tag's identifier permanently (unless the
tag's EPC bank is later overwritten). Put ONE tag in the field at a
time — if multiple tags are present, the write will land on whichever
responds first to the reader's singulation cycle.

Usage:
    python3 03_write_epc.py /dev/ttyUSB0 E200123456789ABCDEF01122
"""

import sys

from swrfid import RFIDReader, MemBank, find_readers


def main(port: str, new_epc_hex: str) -> int:
    epc = bytes.fromhex(new_epc_hex)
    if len(epc) != 12:
        sys.exit('EPC must be exactly 12 bytes (96 bits); got %d' % len(epc))

    with RFIDReader(port) as r:
        # Verify exactly one tag in field — refuse if 0 or 2+.
        tags_before = r.inventory()
        if len(tags_before) != 1:
            sys.exit(
                'Expected exactly 1 tag in field, found %d. '
                'Move other tags away and try again.' % len(tags_before)
            )
        old_epc = tags_before[0].epc_hex
        print('Re-programming tag %s -> %s' % (old_epc, new_epc_hex.upper()))

        # EPC bank layout per Gen2:
        #   word 0 = CRC, word 1 = PC, words 2..N = EPC payload
        # We write the 96-bit EPC at word_addr=2.
        r.write_tag(MemBank.EPC, word_addr=2, payload=epc)
        print('Wrote 12 bytes.')

        # Verify by reading back.
        read_back = r.read_tag(MemBank.EPC, word_addr=2, word_len=6)
        if read_back == epc:
            print('Verified: tag now reports EPC = %s' % read_back.hex().upper())
            return 0
        sys.exit(
            'Verification failed. Wrote %s, read back %s'
            % (epc.hex().upper(), read_back.hex().upper())
        )


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('Usage: %s <port> <new_epc_hex_24chars>' % sys.argv[0])
    if len(sys.argv) == 2:
        # Allow `script.py <epc>` with auto-detected port
        ports = find_readers()
        if not ports:
            sys.exit('No reader auto-detected. Pass port explicitly.')
        sys.exit(main(ports[0], sys.argv[1]))
    sys.exit(main(sys.argv[1], sys.argv[2]))
