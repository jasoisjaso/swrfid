"""
tests/test_rfid_protocol.py — unit tests against documented hex examples
from "UHF RFID Reader User's Manual v1.9" §3-4. Every test below cites
the manual section / item number that produced the expected bytes.

Run:  python3 -m unittest tests.test_rfid_protocol -v
Or:   python3 -m pytest tests/test_rfid_protocol.py -v
"""

import unittest

from swrfid import protocol as p
from swrfid.protocol import (
    Cmd,
    MemBank,
    ParamAddr,
    ResponseBuffer,
    WorkMode,
    build_frame,
    checksum,
    parse_active_data,
    parse_antenna_status,
    parse_inventory,
    parse_response,
    parse_system_info,
)


def H(s):
    return bytes.fromhex(s.replace(' ', ''))


class ChecksumTests(unittest.TestCase):
    """Verify the C reference implementation matches every worked example."""

    def test_start_read_example(self):
        # Manual §4 item 17: CMD_START_READ → 53 57 00 03 FF 41 13
        self.assertEqual(checksum(H('53 57 00 03 FF 41')), 0x13)

    def test_stop_read_example(self):
        # §4 item 16: CMD_STOP_READ → 53 57 00 03 FF 40 14
        self.assertEqual(checksum(H('53 57 00 03 FF 40')), 0x14)

    def test_inventory_example(self):
        # §4 item 24: CMD_INVENTORY_TAG → 53 57 00 03 FF 01 53
        self.assertEqual(checksum(H('53 57 00 03 FF 01')), 0x53)

    def test_default_device_param_example(self):
        # §4 item 4: CMD_DEFAULT_DEVICE_PARAM → 53 57 00 03 FF 22 32
        self.assertEqual(checksum(H('53 57 00 03 FF 22')), 0x32)

    def test_set_freq_us_example(self):
        # §4 item 15: SET_FREQ US → 53 57 00 05 FF 3F 31 80 62
        self.assertEqual(checksum(H('53 57 00 05 FF 3F 31 80')), 0x62)

    def test_read_tag_example(self):
        # §4 item 25 → 53 57 00 0A FF 02 01 02 06 00 00 00 00 42
        self.assertEqual(
            checksum(H('53 57 00 0A FF 02 01 02 06 00 00 00 00')),
            0x42,
        )

    def test_write_tag_example(self):
        # §4 item 26
        body = H(
            '53 57 00 16 FF 03 01 02 06 00 00 00 00 '
            '00 11 22 33 44 55 66 77 88 99 AA BB'
        )
        self.assertEqual(checksum(body), 0xD3)

    def test_set_one_param_example(self):
        # §4 item 6: SET_ONE_PARAM RF_POWER=0x1A → 53 57 00 05 FF 24 05 1A 0F
        self.assertEqual(checksum(H('53 57 00 05 FF 24 05 1A')), 0x0F)


class BuildFrameTests(unittest.TestCase):

    def test_start_read(self):
        self.assertEqual(
            build_frame(Cmd.START_READ),
            H('53 57 00 03 FF 41 13'),
        )

    def test_stop_read(self):
        self.assertEqual(
            build_frame(Cmd.STOP_READ),
            H('53 57 00 03 FF 40 14'),
        )

    def test_inventory(self):
        self.assertEqual(
            build_frame(Cmd.INVENTORY_TAG),
            H('53 57 00 03 FF 01 53'),
        )

    def test_set_rf_power_26db(self):
        self.assertEqual(
            p.cmd_set_rf_power(0x1A),
            H('53 57 00 05 FF 24 05 1A 0F'),
        )

    def test_set_work_mode_answer(self):
        self.assertEqual(
            p.cmd_set_work_mode(WorkMode.ANSWER),
            H('53 57 00 05 FF 24 02 00 2C'),
        )

    def test_set_freq_us(self):
        self.assertEqual(
            p.cmd_set_freq_region('US'),
            H('53 57 00 05 FF 3F 31 80 62'),
        )

    def test_read_system_param(self):
        # §4 item 1: 53 57 00 03 FF 10 44
        self.assertEqual(
            p.cmd_read_system_param(),
            H('53 57 00 03 FF 10 44'),
        )

    def test_check_module(self):
        # §4 item 19: 53 57 00 03 FF E0 74
        self.assertEqual(
            p.cmd_check_module(),
            H('53 57 00 03 FF E0 74'),
        )

    def test_check_ant(self):
        # §4 item 20: 53 57 00 03 FF E1 73
        self.assertEqual(
            p.cmd_check_ant(),
            H('53 57 00 03 FF E1 73'),
        )

    def test_relay_close(self):
        # §4 item 21: 53 57 00 03 FF 85 CF
        self.assertEqual(
            p.cmd_relay_close(),
            H('53 57 00 03 FF 85 CF'),
        )

    def test_relay_release(self):
        # §4 item 22: 53 57 00 03 FF 86 CE
        self.assertEqual(
            p.cmd_relay_release(),
            H('53 57 00 03 FF 86 CE'),
        )

    def test_read_tag_example(self):
        self.assertEqual(
            p.cmd_read_tag(MemBank.EPC, 2, 6),
            H('53 57 00 0A FF 02 01 02 06 00 00 00 00 42'),
        )

    def test_write_tag_example(self):
        payload = H('00 11 22 33 44 55 66 77 88 99 AA BB')
        self.assertEqual(
            p.cmd_write_tag(MemBank.EPC, 2, payload),
            H(
                '53 57 00 16 FF 03 01 02 06 00 00 00 00 '
                '00 11 22 33 44 55 66 77 88 99 AA BB D3'
            ),
        )

    def test_read_freq(self):
        # §4 item 14: 53 57 00 03 FF 3E 16
        self.assertEqual(p.cmd_read_freq(), H('53 57 00 03 FF 3E 16'))

    def test_default_device_param(self):
        # §4 item 4
        self.assertEqual(
            build_frame(Cmd.DEFAULT_DEVICE_PARAM),
            H('53 57 00 03 FF 22 32'),
        )

    def test_addr_validation(self):
        with self.assertRaises(ValueError):
            build_frame(Cmd.INVENTORY_TAG, addr=0x100)

    def test_data_length_validation(self):
        with self.assertRaises(ValueError):
            build_frame(Cmd.SET_DEVICE_PARAM, b'\x00' * 1100)

    def test_unknown_region_rejected(self):
        with self.assertRaises(ValueError):
            p.cmd_set_freq_region('MARS')

    def test_write_tag_word_alignment(self):
        with self.assertRaises(ValueError):
            p.cmd_write_tag(MemBank.EPC, 0, b'\x00\x11\x22')  # 3 bytes

    def test_write_tag_password_length(self):
        with self.assertRaises(ValueError):
            p.cmd_write_tag(
                MemBank.EPC, 0, b'\x00\x00', password=b'\x00\x00\x00',
            )


class ParseResponseTests(unittest.TestCase):

    def test_simple_ack(self):
        # §4 item 4 response: 43 54 00 04 00 22 01 42
        rsp = parse_response(H('43 54 00 04 00 22 01 42'))
        self.assertEqual(rsp.addr, 0)
        self.assertEqual(rsp.cmd, 0x22)
        self.assertEqual(rsp.status, 1)
        self.assertEqual(rsp.data, b'')
        self.assertTrue(rsp.ok())

    def test_set_freq_ack(self):
        # §4 item 15 response: 43 54 00 04 00 3F 01 25
        rsp = parse_response(H('43 54 00 04 00 3F 01 25'))
        self.assertTrue(rsp.ok())
        self.assertEqual(rsp.cmd, 0x3F)

    def test_set_one_param_ack(self):
        # §4 item 6 response: 43 54 00 04 00 24 01 40
        rsp = parse_response(H('43 54 00 04 00 24 01 40'))
        self.assertTrue(rsp.ok())

    def test_system_info(self):
        # §4 item 1 response
        raw = H('43 54 00 0D 00 10 01 14 11 C3 DD 93 8E 17 01 23 2A')
        rsp = parse_response(raw)
        info = parse_system_info(rsp)
        self.assertEqual(info.soft_version, '1.4')
        self.assertEqual(info.hard_version, '1.1')
        self.assertEqual(info.serial_hex, 'C3DD938E170123')

    def test_module_ok_synthesized(self):
        # The manual's §4 item 19 worked example has an inconsistent LEN
        # field (says 0x05 but ships an 8-byte frame; correct LEN for a
        # status-only ack is 0x04). The checksum it prints (0x82/0x83) is
        # computed using LEN=0x05, so the example is "self-consistent but
        # mis-framed". The synthesized frames below use the framing rule
        # the manual states elsewhere and match the rest of the protocol.
        rsp = parse_response(H('43 54 00 04 01 E0 01 83'))
        self.assertTrue(rsp.ok())
        self.assertEqual(rsp.cmd, 0xE0)

    def test_module_error_synthesized(self):
        rsp = parse_response(H('43 54 00 04 01 E0 00 84'))
        self.assertFalse(rsp.ok())
        self.assertEqual(rsp.cmd, 0xE0)

    def test_manual_check_module_example_is_inconsistent(self):
        # Documents the manual bug: the literal bytes from §4 item 19
        # don't parse under the framing rule used everywhere else in the
        # manual. If a future firmware revision actually sends these
        # bytes, this test will start failing — that's the signal to
        # revisit the LEN semantics for CMD_CHECK_MODULE specifically.
        with self.assertRaises(p.FrameError):
            parse_response(H('43 54 00 05 01 E0 01 82'))

    def test_antenna_status(self):
        # §4 item 20: 43 54 00 06 00 E1 01 FF F2 90
        rsp = parse_response(H('43 54 00 06 00 E1 01 FF F2 90'))
        status = parse_antenna_status(rsp)
        # Bitmap FF F2 = 1111 1111 1111 0010
        # bit0=0 (ant1), bit1=1 (ant2), bit2=0 (ant3), bit3=0 (ant4)
        self.assertFalse(status[1])
        self.assertTrue(status[2])
        self.assertFalse(status[3])
        self.assertFalse(status[4])
        for i in range(9, 17):
            self.assertTrue(status[i], "ant%d" % i)

    def test_read_freq_response_korea(self):
        # §4 item 14: 43 54 00 06 00 3E 01 29 9D 5E
        rsp = parse_response(H('43 54 00 06 00 3E 01 29 9D 5E'))
        self.assertTrue(rsp.ok())
        self.assertEqual(rsp.data[:2], H('29 9D'))

    def test_bad_head_rejects(self):
        with self.assertRaises(p.FrameError):
            parse_response(H('AA BB 00 04 00 22 01 42'))

    def test_bad_checksum_rejects(self):
        with self.assertRaises(p.FrameError):
            parse_response(H('43 54 00 04 00 22 01 FF'))

    def test_too_short_rejects(self):
        with self.assertRaises(p.FrameError):
            parse_response(H('43 54 00'))


class ParseActiveDataTests(unittest.TestCase):

    def test_two_tags(self):
        # §4 item 18 example
        raw = H(
            '43 54 00 2C 00 45 01'
            'C4 DD 93 8E 17 01 23 02'
            '0F 01 02 E2 00 20 75 60 10 01 82 04 80 E0 64 33'
            '0F 01 03 E3 00 20 75 60 10 01 82 04 80 E0 64 34'
            '07'
        )
        rsp = parse_response(raw)
        dev_sn, tags = parse_active_data(rsp)
        self.assertEqual(dev_sn.hex().upper(), 'C4DD938E170123')
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0].antenna, 2)
        self.assertEqual(tags[0].epc_hex, 'E2002075601001820480E064')
        self.assertEqual(tags[0].rssi, 0x33)
        self.assertEqual(tags[1].antenna, 3)
        self.assertEqual(tags[1].rssi, 0x34)


class ParseInventoryTests(unittest.TestCase):

    def test_three_tags(self):
        # §4 item 24 example
        raw = H(
            '43 54 00 36 00 01 01'
            '00 03'
            '0F 01 02 E2 00 20 67 55 16 02 70 12 70 92 0F 49'
            '0F 01 02 E2 00 20 75 60 10 01 86 25 10 16 A3 48'
            '0F 01 02 E2 00 20 75 60 10 01 85 01 01 01 01 4D'
            'E4'
        )
        rsp = parse_response(raw)
        tags = parse_inventory(rsp)
        self.assertEqual(len(tags), 3)
        self.assertEqual(tags[0].epc_hex, 'E2002067551602701270920F')
        self.assertEqual(tags[0].rssi, 0x49)
        self.assertEqual(tags[2].rssi, 0x4D)

    def test_no_tag(self):
        # §4 item 24: "No Tag" response: 43 54 00 04 00 01 00 64
        rsp = parse_response(H('43 54 00 04 00 01 00 64'))
        self.assertEqual(parse_inventory(rsp), [])


class ResponseBufferTests(unittest.TestCase):

    def test_one_frame(self):
        buf = ResponseBuffer()
        frames = list(buf.feed(H('43 54 00 04 00 22 01 42')))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, 0x22)

    def test_two_frames_concatenated(self):
        buf = ResponseBuffer()
        two = (
            H('43 54 00 04 00 22 01 42')
            + H('43 54 00 04 00 3F 01 25')
        )
        frames = list(buf.feed(two))
        self.assertEqual(len(frames), 2)
        self.assertEqual([f.cmd for f in frames], [0x22, 0x3F])

    def test_split_across_chunks(self):
        buf = ResponseBuffer()
        full = H('43 54 00 04 00 22 01 42')
        self.assertEqual(list(buf.feed(full[:5])), [])
        frames = list(buf.feed(full[5:]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, 0x22)

    def test_resync_on_garbage(self):
        buf = ResponseBuffer()
        chunk = H('00 11 22 33') + H('43 54 00 04 00 22 01 42')
        frames = list(buf.feed(chunk))
        self.assertEqual(len(frames), 1)

    def test_active_data_interleaved_with_response(self):
        # Simulate the realistic case where an active-mode broadcast lands
        # between a command and its response.
        buf = ResponseBuffer()
        active = H(
            '43 54 00 2C 00 45 01'
            'C4 DD 93 8E 17 01 23 02'
            '0F 01 02 E2 00 20 75 60 10 01 82 04 80 E0 64 33'
            '0F 01 03 E3 00 20 75 60 10 01 82 04 80 E0 64 34'
            '07'
        )
        ack = H('43 54 00 04 00 22 01 42')
        frames = list(buf.feed(active + ack))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].cmd, 0x45)
        self.assertEqual(frames[1].cmd, 0x22)


if __name__ == '__main__':
    unittest.main()
