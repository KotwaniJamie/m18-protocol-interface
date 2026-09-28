"""
Regression tests for tools/dump.py (Phase 2: full register capture).

No hardware needed. A fake pack answers cmd() from a canned table, so the
classification, decoding and error handling all run with no serial port.

    python3 tests/test_dump.py
    python3 -m unittest discover tests

The contract under test is the one Phase 2 exists for: a register that did
not answer and a register that answered with a sentinel must not end up
looking the same.
"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import m18 as m18mod
import dump

# Registers used below, looked up by label so the tests survive reordering.
BY_ID = {i: r for i, r in enumerate(m18mod.data_id)}
ADDR_TO_ID = {r[0]: i for i, r in enumerate(m18mod.data_id)}

CELL_V = 12          # 0x400A, 10 bytes, cell_v
NOTE_ASCII = 7       # 0x0023, 20 bytes, ascii
MFG_DATE = 4         # 0x0011, 4 bytes, date
CELL_TYPE = 0        # 0x0000, 2 bytes, uint
SERIAL = 2           # 0x0004, 5 bytes, sn


class NoAnswer(Exception):
    """Stands in for cmd()'s ValueError('Empty response')."""


class FakePort:
    break_condition = False
    dtr = False

    def close(self):
        pass


class FakePack:
    """An M18 whose wire layer is a lookup table.

    Values in `payloads` are keyed by register id:
        bytes      -> served as the payload of a well-formed 0x81 frame
        NoAnswer   -> cmd() raises, as it does on a zero-byte read
        b"\\x82.." -> served verbatim as the whole response (not a 0x81 frame)
    Any register not in the table answers with zeros, which decodes cleanly.
    """

    def __init__(self, payloads=None, raw_frames=None):
        self.payloads = payloads or {}
        self.raw_frames = raw_frames or {}
        self.reads = []
        self.idled = 0

    # -- wire layer ------------------------------------------------------
    def cmd(self, a, b, c, length, command=0x01):
        addr = (a << 8) | b
        self.reads.append(addr)
        i = ADDR_TO_ID.get(addr)
        if i in self.raw_frames:
            return bytearray(self.raw_frames[i])
        payload = self.payloads.get(i)
        if payload is NoAnswer:
            raise ValueError("Empty response")
        if payload is None:
            payload = bytes(BY_ID[i][1]) if i is not None else b"\x00" * c
        return bytearray(b"\x81\x04" + bytes([len(payload)]) + payload + b"\x00\x00")

    def reset(self):
        return True

    def idle(self):
        self.idled += 1

    # -- borrowed from the real class, so decoding is not re-implemented --
    bytes2dt = m18mod.M18.bytes2dt
    calculate_temperature = m18mod.M18.calculate_temperature
    read_id = m18mod.M18.read_id
    health_data = m18mod.M18.health_data
    port = FakePort()
    PRINT_TX = PRINT_RX = False


def one(pack, reg_id):
    return dump.read_register(pack, reg_id)


class TestStates(unittest.TestCase):
    def test_ok_uint(self):
        r = one(FakePack({CELL_TYPE: b"\x00\x26"}), CELL_TYPE)
        self.assertEqual(r["state"], dump.OK)
        self.assertEqual(r["value"], 0x26)
        self.assertEqual(r["raw"], "00 26")

    def test_sentinel_is_not_absent(self):
        """The whole point of Phase 2: these are different states."""
        sentinel = one(FakePack({CELL_TYPE: b"\xff\xff"}), CELL_TYPE)
        absent = one(FakePack({CELL_TYPE: NoAnswer}), CELL_TYPE)

        self.assertEqual(sentinel["state"], dump.SENTINEL)
        self.assertEqual(absent["state"], dump.ABSENT)
        self.assertNotEqual(sentinel["state"], absent["state"])

        # Both have value None -- that is exactly why state has to exist.
        self.assertIsNone(sentinel["value"])
        self.assertIsNone(absent["value"])
        # But the bytes survive for the one that answered, and only that one.
        self.assertEqual(sentinel["raw"], "FF FF")
        self.assertIsNone(absent["raw"])

    def test_absent_records_the_error(self):
        r = one(FakePack({CELL_TYPE: NoAnswer}), CELL_TYPE)
        self.assertIn("Empty response", r["error"])

    def test_non_81_frame_is_absent_and_keeps_the_bytes(self):
        r = one(FakePack(raw_frames={CELL_TYPE: b"\x82\x00"}), CELL_TYPE)
        self.assertEqual(r["state"], dump.ABSENT)
        self.assertEqual(r["response_raw"], "82 00")

    def test_truncated_frame(self):
        """A short 0x81 frame must not be decoded as if it were complete."""
        r = one(FakePack(raw_frames={CELL_V: b"\x81\x04\x04\x0e\x42\x0e\x40\x00\x00"}),
                CELL_V)
        self.assertEqual(r["state"], dump.TRUNCATED)
        self.assertIsNone(r["value"])
        # Note the checksum bytes get pulled into the payload slice -- the
        # frame is short, so [3:3+length] runs off the end of the real data.
        # That is exactly why the length check has to happen before decoding:
        # cell_v would otherwise have read five plausible-looking voltages.
        self.assertEqual(r["raw"], "0E 42 0E 40 00 00")

    def test_undecodable_keeps_the_bytes(self):
        bad = b"\xff\xfe" + b"\x00" * 18          # not valid utf-8, not all-FF
        r = one(FakePack({NOTE_ASCII: bad}), NOTE_ASCII)
        self.assertEqual(r["state"], dump.UNDECODABLE)
        self.assertTrue(r["raw"].startswith("FF FE"))
        self.assertIn("UnicodeDecodeError", r["error"])


class TestFlags(unittest.TestCase):
    def test_partial_cell_sentinel(self):
        """One dead cell reading among four live ones is not a whole-register
        sentinel, and averaging it would produce nonsense."""
        payload = (b"\x0e\x42" * 4) + b"\xff\xff"
        r = one(FakePack({CELL_V: payload}), CELL_V)
        self.assertEqual(r["state"], dump.OK)
        self.assertIn("partial_sentinel", r["flags"])
        self.assertEqual(r["value"][4], 0xFFFF)

    def test_all_cells_sentinel_is_a_sentinel(self):
        r = one(FakePack({CELL_V: b"\xff" * 10}), CELL_V)
        self.assertEqual(r["state"], dump.SENTINEL)

    def test_undefined_ascii(self):
        r = one(FakePack({NOTE_ASCII: b"Undefined-----------"}), NOTE_ASCII)
        self.assertEqual(r["state"], dump.OK)
        self.assertIn("undefined_ascii", r["flags"])
        self.assertEqual(r["value"], "Undefined-----------")

    def test_epoch_zero_date(self):
        r = one(FakePack({MFG_DATE: b"\x00\x00\x00\x00"}), MFG_DATE)
        self.assertEqual(r["state"], dump.OK)
        self.assertIn("epoch_zero", r["flags"])

    def test_real_date_has_no_flag(self):
        epoch = int(datetime.datetime(2018, 8, 28, tzinfo=datetime.UTC).timestamp())
        r = one(FakePack({MFG_DATE: epoch.to_bytes(4, "big")}), MFG_DATE)
        self.assertEqual(r["flags"], [])
        self.assertEqual(r["value"].year, 2018)


class TestWholeDump(unittest.TestCase):
    def test_covers_every_register(self):
        pack = FakePack()
        recs = dump.dump_registers(pack)
        self.assertEqual(len(recs), len(m18mod.data_id))
        self.assertEqual([r["id"] for r in recs], list(range(len(m18mod.data_id))))

    def test_refresh_reads_the_data_matrix(self):
        pack = FakePack()
        dump.dump_registers(pack, force_refresh=True)
        for addr_h, addr_l, _ in m18mod.data_matrix:
            self.assertIn((addr_h << 8) | addr_l, pack.reads)

    def test_one_bad_register_does_not_stop_the_dump(self):
        pack = FakePack({CELL_TYPE: NoAnswer, NOTE_ASCII: NoAnswer})
        recs = dump.dump_registers(pack)
        self.assertEqual(len(recs), len(m18mod.data_id))
        self.assertEqual(recs[CELL_TYPE]["state"], dump.ABSENT)
        self.assertEqual(recs[5]["state"], dump.OK)

    def test_line_is_left_low(self):
        pack = FakePack()
        dump.dump_registers(pack)
        self.assertGreater(pack.idled, 0)


class TestArrayShim(unittest.TestCase):
    def test_shape_matches_read_id_array(self):
        recs = dump.dump_registers(FakePack())
        arr = dump.to_array(recs)
        self.assertEqual(len(arr), len(recs))
        self.assertTrue(all(len(pair) == 2 for pair in arr))
        self.assertEqual([p[0] for p in arr], [r["id"] for r in recs])

    def test_equivalent_to_read_id_array_mode(self):
        """The shim must be indistinguishable from upstream read_id().

        This is the test that lets dump.py replace read_id() as the capture
        path without anything downstream noticing. Every data_type is
        exercised, including the four whose array value is a rendered string
        rather than the decoded object.
        """
        epoch = int(datetime.datetime(2018, 8, 28, tzinfo=datetime.UTC).timestamp())
        payloads = {
            SERIAL: (38).to_bytes(2, "big") + (4769294).to_bytes(3, "big"),
            MFG_DATE: epoch.to_bytes(4, "big"),
            CELL_V: b"\x0e\x42\x0e\x40\x0e\x47\x0e\x39\x0e\x44",
            NOTE_ASCII: b"Undefined-----------",
            13: b"\x02\x00",              # adc_t
            18: b"\x17\x80",              # dec_t
            35: b"\x00\x07\x69\x50",    # hhmmss
        }
        via_read_id = FakePack(payloads).read_id(output="array")
        via_dump = dump.to_array(dump.dump_registers(FakePack(payloads)))
        self.assertEqual(via_dump, via_read_id)

    def test_sentinel_and_absent_both_become_none(self):
        pack = FakePack({CELL_TYPE: b"\xff\xff", NOTE_ASCII: NoAnswer})
        arr = dict(dump.to_array(dump.dump_registers(pack)))
        self.assertIsNone(arr[CELL_TYPE])
        self.assertIsNone(arr[NOTE_ASCII])


class TestHealthFrom(unittest.TestCase):
    def _pack(self):
        epoch = int(datetime.datetime(2018, 8, 28, tzinfo=datetime.UTC).timestamp())
        return FakePack({
            SERIAL: (38).to_bytes(2, "big") + (4769294).to_bytes(3, "big"),
            MFG_DATE: epoch.to_bytes(4, "big"),
            CELL_V: b"\x0e\x42\x0e\x40\x0e\x47\x0e\x39\x0e\x44",
        })

    def test_health_uses_the_same_sample(self):
        pack = self._pack()
        recs = dump.dump_registers(pack)
        before = len(pack.reads)
        health = dump.health_from(pack, recs)
        self.assertEqual(len(pack.reads), before, "health_from must not re-read the pack")
        self.assertIsNotNone(health)
        self.assertEqual(str(health["e_serial"]["value"]), "4769294")
        self.assertEqual(str(health["battery_type"]["value"]), "38")

    def test_read_id_is_restored(self):
        """No leftover instance attribute shadowing the real method."""
        pack = self._pack()
        dump.health_from(pack, dump.dump_registers(pack))
        self.assertNotIn("read_id", vars(pack))

    def test_read_id_restored_even_on_failure(self):
        pack = self._pack()
        pack.health_data = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            dump.health_from(pack, dump.dump_registers(pack))
        self.assertNotIn("read_id", vars(pack))

    def test_cell_voltages_agree_with_the_dump(self):
        pack = self._pack()
        recs = dump.dump_registers(pack)
        health = dump.health_from(pack, recs)
        self.assertEqual(health["cell_voltages_mv"]["value"], recs[CELL_V]["value"])


class TestDocument(unittest.TestCase):
    def _doc(self, pack=None):
        pack = pack or FakePack({
            SERIAL: (38).to_bytes(2, "big") + (4769294).to_bytes(3, "big")})
        return dump.build_document(pack, dump.dump_registers(pack),
                                   port="/dev/fake", case_serial="G29JDCBC 180720 3894605")

    def test_identity_and_counts(self):
        doc = self._doc()
        self.assertEqual(doc["schema"], dump.SCHEMA)
        self.assertEqual(doc["e_serial"], 4769294)
        self.assertEqual(doc["battery_type"], 38)
        self.assertEqual(doc["case_serial"], "G29JDCBC 180720 3894605")
        self.assertEqual(sum(doc["state_counts"].values()), len(m18mod.data_id))

    def test_answered_flag(self):
        """A pack that said nothing must not look like a pack of zeros."""
        silent = FakePack({i: NoAnswer for i in range(len(m18mod.data_id))})
        doc = dump.build_document(silent, dump.dump_registers(silent))
        self.assertFalse(doc["answered"])
        self.assertTrue(self._doc()["answered"])

    def test_all_ones_pack_answered(self):
        """A dead pack talks. Silence and blanks are different failures."""
        dead = FakePack({i: b"\xff" * m18mod.data_id[i][1]
                         for i in range(len(m18mod.data_id))})
        self.assertTrue(dump.build_document(dead, dump.dump_registers(dead))["answered"])

    def test_all_ones_pack_yields_no_valid_health_field(self):
        """The dead-pack path, which has never met real hardware.

        Every register answers 0xFFFF. Nothing derived from that may come
        back marked valid -- issue #28 is what happens when it does.
        """
        dead = FakePack({i: b"\xff" * m18mod.data_id[i][1]
                         for i in range(len(m18mod.data_id))})
        doc = dump.build_document(dead, dump.dump_registers(dead))
        self.assertEqual(doc["state_counts"].get(dump.SENTINEL), len(m18mod.data_id))
        valid = [k for k, v in doc["health"].items()
                 if isinstance(v, dict) and v.get("valid")]
        self.assertEqual(valid, [], f"trusted a sentinel: {valid}")

    def test_serialises_as_json(self):
        """Dates are datetime objects; the writer must not choke on them."""
        import json
        epoch = int(datetime.datetime(2018, 8, 28, tzinfo=datetime.UTC).timestamp())
        pack = FakePack({MFG_DATE: epoch.to_bytes(4, "big")})
        text = json.dumps(self._doc(pack), default=str)
        self.assertIn("2018-08-28", text)

    def test_flagged_index(self):
        pack = FakePack({NOTE_ASCII: b"Undefined-----------"})
        doc = self._doc(pack)
        self.assertTrue(any(f["id"] == NOTE_ASCII for f in doc["flagged"]))

    def test_summary_mentions_every_populated_state(self):
        pack = FakePack({CELL_TYPE: b"\xff\xff", NOTE_ASCII: NoAnswer})
        line = dump.summarise(self._doc(pack))
        self.assertIn("sentinel", line)
        self.assertIn("absent", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
