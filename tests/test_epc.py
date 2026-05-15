"""Unit tests for swrfid.epc.

The bit-layout reference is the GS1 EPC Tag Data Standard. For SGTIN-96,
the partition table comes from epc-rfid.info/sgtin-partition-values (and
matches the official GS1 TDS PDF).

Test data is constructed via a small in-test encoder that mirrors the
TDS definition. The decoder is tested for:

  - round-trip correctness against the encoder
  - identify() routing the header byte to the correct scheme name
  - GTIN-14 check-digit computation against a worked example
"""

import unittest

from swrfid import epc
from swrfid.epc import (
    EPC_HEADERS, SGTIN96_PARTITIONS,
    SGTIN96, describe, identify, parse_sgtin96,
    _gtin_check_digit,
)


def encode_sgtin96(filter_val, partition, company_prefix, item_reference, serial):
    """In-test SGTIN-96 encoder mirroring the TDS spec (§5.3.4).

    Layout: header(8) | filter(3) | partition(3) | CP(M) | IR(N) | serial(38)
    where M + N = 44 per the partition table.
    """
    cp_bits, cp_digits, ir_bits, ir_digits = SGTIN96_PARTITIONS[partition]
    assert len(company_prefix) == cp_digits, (
        "CP %r must be %d digits for partition %d"
        % (company_prefix, cp_digits, partition)
    )
    assert len(item_reference) == ir_digits, (
        "IR %r must be %d digits for partition %d"
        % (item_reference, ir_digits, partition)
    )
    bits = '00110000'                            # header 0x30
    bits += format(filter_val, '03b')
    bits += format(partition, '03b')
    bits += format(int(company_prefix), '0%db' % cp_bits)
    bits += format(int(item_reference), '0%db' % ir_bits)
    bits += format(serial, '038b')
    assert len(bits) == 96, "got %d bits" % len(bits)
    return bytes(int(bits[i:i+8], 2) for i in range(0, 96, 8))


class HeaderIdentificationTests(unittest.TestCase):

    def test_sgtin96_header(self):
        self.assertEqual(EPC_HEADERS[0x30], 'sgtin-96')

    def test_identify_sgtin96(self):
        self.assertEqual(identify(b'\x30' + b'\x00' * 11), 'sgtin-96')

    def test_identify_grai96(self):
        self.assertEqual(identify(b'\x33' + b'\x00' * 11), 'grai-96')

    def test_identify_giai96(self):
        self.assertEqual(identify(b'\x34' + b'\x00' * 11), 'giai-96')

    def test_identify_gid96(self):
        self.assertEqual(identify(b'\x35' + b'\x00' * 11), 'gid-96')

    def test_identify_sscc96(self):
        self.assertEqual(identify(b'\x31' + b'\x00' * 11), 'sscc-96')

    def test_identify_unknown_header(self):
        self.assertEqual(identify(b'\xAB' + b'\x00' * 11), 'unknown')

    def test_identify_empty(self):
        self.assertEqual(identify(b''), 'unknown')


class GtinCheckDigitTests(unittest.TestCase):
    # The GS1 check-digit algorithm is length-sensitive: the alternating
    # 3/1 weighting starts from the rightmost position with weight 3, so
    # the leftmost weight depends on the parity of the input length.
    #
    # This helper is specifically for the 13-digit GTIN-14 body case
    # (which is why it enforces length=13). EAN-13 / ISBN-13 / UPC-12
    # cases would need a different weight pattern; they're not in scope
    # for this driver — recommend the standard `python-stdnum` or
    # `epcpy` libraries for those.

    def test_zero_padded(self):
        # 13 zeros → check digit must be 0 (sum is 0, complement is 0).
        self.assertEqual(_gtin_check_digit('0000000000000'), '0')

    def test_canonical_tds_example(self):
        # GS1 TDS canonical example for SGTIN-96 (CP=0614141 / IR=112345)
        # gives GTIN-14 body '1061414112345'. Verify check digit by the
        # modulo-10 invariant: leftmost digit gets weight 3, alternating
        # with 1; full GTIN-14 must sum to a multiple of 10. (Computed:
        # body sum = 81, expected check = 9, so GTIN-14 = '10614141123459'.)
        body = '1061414112345'
        check = _gtin_check_digit(body)
        self.assertEqual(check, '9')
        full = body + check
        s = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(full))
        self.assertEqual(s % 10, 0, 'GTIN-14 modulo-10 invariant violated')

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            _gtin_check_digit('12345')

    def test_rejects_non_digit(self):
        with self.assertRaises(ValueError):
            _gtin_check_digit('123456789012X')


class SGTIN96ParseTests(unittest.TestCase):

    def test_known_partition_5(self):
        # GS1 TDS canonical example: filter=3, partition=5,
        # CP=0614141 (7 digits), IR=112345 (6 digits), serial=400.
        raw = encode_sgtin96(3, 5, '0614141', '112345', 400)
        # Sanity: header byte is 0x30
        self.assertEqual(raw[0], 0x30)
        s = parse_sgtin96(raw)
        self.assertEqual(s.filter, 3)
        self.assertEqual(s.partition, 5)
        self.assertEqual(s.company_prefix, '0614141')
        self.assertEqual(s.item_reference, '112345')
        self.assertEqual(s.serial, 400)
        self.assertEqual(s.scheme, 'sgtin-96')

    def test_round_trip_all_partitions(self):
        # For each partition, encode and decode and confirm field equality.
        # Use minimal fixed values that fit each partition's digit budget.
        for p, (cp_bits, cp_digits, ir_bits, ir_digits) in SGTIN96_PARTITIONS.items():
            cp = '1' * cp_digits
            ir = '2' * ir_digits
            raw = encode_sgtin96(2, p, cp, ir, 12345)
            s = parse_sgtin96(raw)
            self.assertEqual(s.filter, 2, 'partition=%d' % p)
            self.assertEqual(s.partition, p)
            self.assertEqual(s.company_prefix, cp, 'partition=%d' % p)
            self.assertEqual(s.item_reference, ir, 'partition=%d' % p)
            self.assertEqual(s.serial, 12345)

    def test_max_serial(self):
        raw = encode_sgtin96(0, 5, '0614141', '112345', (1 << 38) - 1)
        s = parse_sgtin96(raw)
        self.assertEqual(s.serial, (1 << 38) - 1)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            parse_sgtin96(b'\x30' * 10)

    def test_rejects_wrong_header(self):
        with self.assertRaises(ValueError):
            parse_sgtin96(b'\x33' + b'\x00' * 11)


class SGTIN96GtinTests(unittest.TestCase):
    """The constructed GTIN-14 is indicator + CP + remaining IR + check."""

    def test_gtin14_for_known_sgtin(self):
        raw = encode_sgtin96(3, 5, '0614141', '112345', 400)
        s = parse_sgtin96(raw)
        # Body 13 digits = '1' + '0614141' + '12345' = '1061414112345'
        # Check digit computed below.
        self.assertEqual(s.gtin14[:13], '1061414112345')
        self.assertEqual(s.gtin14, '1061414112345' + _gtin_check_digit('1061414112345'))
        # And the check digit must satisfy the modulo-10 property:
        s_sum = 0
        for i, ch in enumerate(s.gtin14):
            s_sum += int(ch) * (3 if (13 - i) % 2 == 1 else 1)
        self.assertEqual(s_sum % 10, 0, 'GTIN-14 check digit invariant failed')


class DescribeTests(unittest.TestCase):

    def test_describe_sgtin(self):
        raw = encode_sgtin96(1, 5, '0614141', '112345', 400)
        d = describe(raw)
        self.assertEqual(d.scheme, 'sgtin-96')
        self.assertEqual(d.hex, raw.hex().upper())
        self.assertIsNotNone(d.sgtin)
        self.assertEqual(d.sgtin.serial, 400)

    def test_describe_unknown(self):
        d = describe(b'\xAB' * 12)
        self.assertEqual(d.scheme, 'unknown')
        self.assertIsNone(d.sgtin)

    def test_describe_grai_no_full_parse(self):
        d = describe(b'\x33' + b'\x00' * 11)
        self.assertEqual(d.scheme, 'grai-96')
        # We don't currently parse GRAI-96 fields; sgtin should be None.
        self.assertIsNone(d.sgtin)

    def test_describe_short_bytes(self):
        # Less than 12 bytes — identify still works on the header.
        d = describe(b'\x30\x01\x02')
        self.assertEqual(d.scheme, 'sgtin-96')
        # Can't parse the body, so sgtin stays None.
        self.assertIsNone(d.sgtin)


class UriFormattingTests(unittest.TestCase):

    def test_pure_identity_uri(self):
        raw = encode_sgtin96(3, 5, '0614141', '112345', 400)
        s = parse_sgtin96(raw)
        self.assertEqual(s.pure_identity_uri,
                         'urn:epc:id:sgtin:0614141.112345.400')

    def test_tag_uri(self):
        raw = encode_sgtin96(3, 5, '0614141', '112345', 400)
        s = parse_sgtin96(raw)
        self.assertEqual(s.tag_uri,
                         'urn:epc:tag:sgtin-96:3.0614141.112345.400')


if __name__ == '__main__':
    unittest.main()
