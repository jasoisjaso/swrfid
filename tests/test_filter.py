"""Unit tests for the _FilteredCallback wrapper used by RFIDReader.

These tests run without any hardware — they construct TagEntry objects
directly and drive the callback synchronously.
"""

import time
import unittest

from swrfid.protocol import TagEntry
from swrfid.reader import _FilteredCallback


def make_tag(epc_hex, rssi=80, ant=1):
    return TagEntry(
        type_code=1,
        antenna=ant,
        epc=bytes.fromhex(epc_hex),
        rssi=rssi,
    )


class NoFilterTests(unittest.TestCase):

    def test_passthrough_when_no_filter_active(self):
        # Note: RFIDReader bypasses the wrapper entirely when both filters
        # default; constructing it directly with 0/0 still passes through.
        seen = []
        cb = _FilteredCallback(lambda sn, tags: seen.append(list(tags)), 0.0, 0)
        cb(b'sn', [make_tag('AABB')])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0].epc_hex, 'AABB')


class RssiFilterTests(unittest.TestCase):

    def test_drops_below_min(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append(list(tags)), 0.0, 60,
        )
        cb(b'sn', [make_tag('AA', rssi=50), make_tag('BB', rssi=70)])
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(seen[0][0].epc_hex, 'BB')

    def test_all_below_min_drops_call_entirely(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append(list(tags)), 0.0, 0xFF,
        )
        cb(b'sn', [make_tag('AA', rssi=50)])
        self.assertEqual(seen, [])

    def test_passthrough_when_zero(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append(list(tags)), 0.0, 0,
        )
        cb(b'sn', [make_tag('AA', rssi=0)])
        self.assertEqual(len(seen), 1)


class DedupTests(unittest.TestCase):

    def test_dedup_window_suppresses_repeats(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append([t.epc_hex for t in tags]),
            10.0, 0,
        )
        t = make_tag('AABB')
        cb(b'sn', [t])
        cb(b'sn', [t])    # within the window, should be deduped
        cb(b'sn', [t])    # ditto
        # Only the first call should have produced a callback invocation.
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], ['AABB'])

    def test_distinct_tags_not_deduped(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append([t.epc_hex for t in tags]),
            10.0, 0,
        )
        cb(b'sn', [make_tag('AABB'), make_tag('CCDD')])
        cb(b'sn', [make_tag('AABB')])   # same as first → dropped
        cb(b'sn', [make_tag('EEFF')])   # new → forwarded
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], ['AABB', 'CCDD'])
        self.assertEqual(seen[1], ['EEFF'])

    def test_dedup_window_expires(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append([t.epc_hex for t in tags]),
            0.05, 0,
        )
        cb(b'sn', [make_tag('AABB')])
        time.sleep(0.07)   # past window
        cb(b'sn', [make_tag('AABB')])
        self.assertEqual(len(seen), 2)


class CombinedTests(unittest.TestCase):

    def test_rssi_and_dedup_together(self):
        seen = []
        cb = _FilteredCallback(
            lambda sn, tags: seen.append([t.epc_hex for t in tags]),
            5.0, 60,
        )
        cb(b'sn', [
            make_tag('AA', rssi=50),    # rssi-dropped
            make_tag('BB', rssi=70),    # forwarded
            make_tag('CC', rssi=55),    # rssi-dropped
        ])
        cb(b'sn', [make_tag('BB', rssi=80)])  # dedup-dropped
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], ['BB'])


if __name__ == '__main__':
    unittest.main()
