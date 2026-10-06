"""
Tests for the GUI's decisions (gui/model.py) and its port-owning thread
(gui/scanner.py). No window and no hardware: the same plug-in-able fake
battery as tests/test_runner.py.

    python3 tests/test_gui.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "gui"))
sys.path.insert(0, HERE)

import m18 as m18mod
import adapters
import dump
import model
import runner
import scanner
from test_runner import Clock, make_pack


def cells_mv(*mv):
    return b"".join(v.to_bytes(2, "big") for v in mv)


class Timing(unittest.TestCase):
    """Runs every knock and wait on a fake clock."""

    def setUp(self):
        self.clock = Clock()
        self._saved = runner._sleep, runner._now
        runner._sleep = self.clock.sleep
        runner._now = lambda: self.clock.t

    def tearDown(self):
        runner._sleep, runner._now = self._saved


def capture(pack, sticker=""):
    records = dump.dump_registers(pack)
    return dump.build_document(pack, records, "fake", sticker)


def as_loaded(doc):
    """The same document after a round trip through the JSON file."""
    return json.loads(json.dumps(doc, default=str))


# --- the view model ----------------------------------------------------------

class TestVerdict(Timing):
    def check(self, cells, result, state, title_word):
        doc = capture(make_pack(self.clock, cells=cells))
        for d in (doc, as_loaded(doc)):
            v = model.verdict(d)
            self.assertEqual(v["result"], result)
            self.assertEqual(v["result"], runner.classify(d)["result"])
            self.assertEqual(v["state"], state)
            self.assertIn(title_word, v["title"])

    def test_balanced_pack_is_good(self):
        self.check(cells_mv(3900, 3902, 3898, 3901, 3900), runner.OK, model.GOOD, "Good")

    def test_slightly_out(self):
        self.check(cells_mv(3900, 3915, 3900, 3900, 3900), runner.FLAGGED, model.LOOK, "Worth balancing")

    def test_out_of_balance(self):
        self.check(cells_mv(3900, 3967, 3900, 3900, 3900), runner.FLAGGED, model.LOOK, "Needs balancing")

    def test_imbalance_is_called_by_its_name(self):
        doc = capture(make_pack(self.clock, cells=cells_mv(3900, 3967, 3900, 3900, 3900)))
        self.assertIn("Cell imbalance (ΔV)", model.verdict(doc)["summary"])

    def test_dead_pack(self):
        pack = make_pack(self.clock)
        for i in list(runner.HEALTH_IDS):
            if i != runner.SERIAL_ID and m18mod.data_id[i][1] in (2, 4):
                pack.payloads[i] = b"\xff" * m18mod.data_id[i][1]
        doc = capture(pack)
        v = model.view(doc)
        self.assertEqual(v["verdict"]["state"], model.DEAD)
        # A sentinel never reaches the screen as a number.
        for s in v["numbers"] + v["wear"]:
            self.assertFalse(s["valid"], s["label"])
            self.assertIsNone(s["value"], s["label"])
        self.assertNotIn("65535", json.dumps(v["numbers"] + v["wear"] + v["details"]))


class TestMockupWording(Timing):
    """Chip text and the six tiles follow the chosen mockup."""

    def test_status_chips(self):
        cases = [((3900, 3902, 3898, 3901, 3900), "✓ Good"),
                 ((3900, 3915, 3900, 3900, 3900), "! Needs a look"),
                 ((3900, 3967, 3900, 3900, 3900), "! Needs balancing")]
        for mv, chip in cases:
            v = model.verdict(capture(make_pack(self.clock, cells=cells_mv(*mv))))
            self.assertEqual(v["status"], chip)

    def test_six_tiles_in_order(self):
        v = model.view(capture(make_pack(self.clock)))
        self.assertEqual([t["label"] for t in v["tiles"]],
                         ["Full cycles", "Charges", "Temp °C", "Overheated", "Ran flat", "Built"])

    def test_tiles_never_show_a_sentinel(self):
        pack = make_pack(self.clock)
        for i in list(runner.HEALTH_IDS):
            if i != runner.SERIAL_ID and m18mod.data_id[i][1] in (2, 4):
                pack.payloads[i] = b"\xff" * m18mod.data_id[i][1]
        v = model.view(capture(pack))
        self.assertNotIn("65535", json.dumps(v["tiles"]))


class TestCells(Timing):
    def test_lowest_cell_marked_only_when_out_of_range(self):
        ok = model.cells(capture(make_pack(self.clock, cells=cells_mv(3900, 3905, 3898, 3901, 3900))))
        self.assertFalse(any(c["low"] for c in ok["cells"]))
        bad = model.cells(capture(make_pack(self.clock, cells=cells_mv(3900, 3967, 3880, 3900, 3900))))
        self.assertEqual([c["n"] for c in bad["cells"] if c["low"]], [3])

    def test_scale_is_three_to_four_point_two_volts(self):
        c = model.cells(capture(make_pack(self.clock, cells=cells_mv(3000, 3600, 4200, 2900, 4300))))
        self.assertEqual([x["fraction"] for x in c["cells"]], [0.0, 0.5, 1.0, 0.0, 1.0])

    def test_partial_sentinel_cell_is_blank(self):
        c = model.cells(capture(make_pack(self.clock, cells=cells_mv(3900, 0xFFFF, 3900, 3900, 3900))))
        self.assertIsNone(c["cells"][1]["mv"])


class TestChips(unittest.TestCase):
    def rec(self, i, state, label="x", flags=(), error=None):
        r = {"id": i, "state": state, "label": label, "flags": list(flags)}
        if error:
            r["error"] = error
        return r

    def test_each_state(self):
        health = next(iter(runner.HEALTH_IDS))
        other = next(i for i in range(len(m18mod.data_id)) if i not in runner.HEALTH_IDS)
        self.assertEqual(model.chip(self.rec(other, dump.OK)), model.RECORDED)
        self.assertEqual(model.chip(self.rec(other, dump.OK, flags=["epoch_zero"])), model.NEVER_WRITTEN)
        self.assertEqual(model.chip(self.rec(other, dump.OK, flags=["undefined_ascii"])), model.NEVER_WRITTEN)
        self.assertEqual(model.chip(self.rec(other, dump.SENTINEL)), model.NOT_KEPT)
        self.assertEqual(model.chip(self.rec(health, dump.SENTINEL)), model.BLANK)
        self.assertEqual(model.chip(self.rec(other, dump.ABSENT, "Temp (Forge)")), model.NOT_KEPT)
        self.assertEqual(model.chip(self.rec(other, dump.ABSENT)), model.UNREADABLE)
        self.assertEqual(model.chip(self.rec(other, dump.ABSENT, error=dump.NOT_ATTEMPTED)),
                         model.NOT_READ)


class TestHistory(Timing):
    def test_oldest_first_and_no_duplicates(self):
        doc = as_loaded(capture(make_pack(self.clock)))
        a, b = dict(doc, captured_at="2026-09-04T10:00:00"), dict(doc, captured_at="2026-09-28T10:00:00")
        pts = model.history([b, a, b, dict(doc, e_serial=999)], doc["e_serial"])
        self.assertEqual([p["when"][:10] for p in pts], ["2026-09-04", "2026-09-28"])


class TestAdapters(unittest.TestCase):
    class Port:
        def __init__(self, device, vid, pid, description="USB"):
            self.device, self.vid, self.pid, self.description = device, vid, pid, description

    def test_tested_chip_comes_first_and_ttys_are_skipped(self):
        ports = [self.Port("/dev/cu.SLAB", 0x10C4, 0xEA60), self.Port("/dev/cu.ft", 0x0403, 0x6001),
                 self.Port("/dev/tty.ft", 0x0403, 0x6001), self.Port("/dev/cu.Bluetooth", None, None)]
        found = adapters.find(lambda: ports)
        self.assertEqual([f["device"] for f in found], ["/dev/cu.ft", "/dev/cu.SLAB"])
        self.assertEqual(found[0]["status"], adapters.TESTED)
        self.assertEqual(found[1]["status"], adapters.RISKY)

    def test_unknown_chip_is_untested(self):
        d = adapters.describe(self.Port("COM7", 0x1234, 0x5678))
        self.assertEqual(d["status"], adapters.UNTESTED)


# --- the scanner ----------------------------------------------------------------

TESTED = {"device": "/dev/cu.fake", "chip": "FTDI FT232R", "status": adapters.TESTED,
          "id": "0403:6001", "message": ""}
RISKY = dict(TESTED, chip="WCH CH340", status=adapters.RISKY)


class TestScanner(Timing):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = pathlib.Path(self.tmp.name)
        self.closed_calls = 0

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def make(self, pack, ports=(TESTED,)):
        opened = []

        def open_m18(device):
            opened.append(device)
            return pack

        def on_closed():
            self.closed_calls += 1

        sc = scanner.Scanner(self.data, open_m18=open_m18, find_ports=lambda: list(ports),
                             on_closed=on_closed)
        sc._pause = self.clock.sleep
        sc.opened = opened
        return sc

    def run_until(self, sc, phase, limit=50):
        for _ in range(limit):
            if sc.state["phase"] == phase:
                return
            self.assertTrue(sc.step(), f"stopped before {phase}")
        self.fail(f"never reached {phase}; at {sc.state['phase']}")

    def logged(self):
        runs = list((self.data / "runs").glob("*.jsonl"))
        return [json.loads(l) for p in runs for l in p.read_text().splitlines()]

    def test_one_battery_end_to_end(self):
        pack = make_pack(self.clock)
        sc = self.make(pack)
        self.run_until(sc, scanner.WAITING)
        sc.set_sticker("abcd1234 180828 7654321")         # typed during the read
        self.run_until(sc, scanner.ATTACHED)
        saved = list((self.data / "captures").glob("pack_*_full.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(sc.state["view"]["verdict"]["result"], runner.OK)

        info = sc.set_sticker("ABCD1234 180828 7654322")  # corrected after the read
        self.assertTrue(info["date_ok"])
        self.assertEqual(json.loads(saved[0].read_text())["case_serial"], "ABCD1234 180828 7654322")

        pack.attached = False
        self.run_until(sc, scanner.WAITING)
        self.assertTrue(sc.state["removed"])
        self.assertEqual([(e["result"], e["case_serial"]) for e in self.logged()],
                         [(runner.OK, "ABCD1234 180828 7654322")])
        self.assertFalse(pack.port.closed)

    def test_sticker_against_the_wrong_battery_is_caught(self):
        sc = self.make(make_pack(self.clock))
        self.run_until(sc, scanner.ATTACHED)
        info = sc.set_sticker("ABCD1234 200101 7654321")
        self.assertFalse(info["date_ok"])

    def test_rescan_is_saved_beside_the_original(self):
        pack = make_pack(self.clock)
        sc = self.make(pack)
        self.run_until(sc, scanner.ATTACHED)
        pack.attached = False
        self.run_until(sc, scanner.WAITING)
        first = next((self.data / "captures").glob("pack_*.json"))
        first.write_text(json.dumps(dict(json.loads(first.read_text()),
                                         captured_at="2026-09-04T10:00:00")))
        pack.attached = True
        self.run_until(sc, scanner.ATTACHED)
        names = sorted(p.name for p in (self.data / "captures").glob("pack_*.json"))
        self.assertEqual(len(names), 2)
        self.assertTrue(sc.state["saved"]["rescan"])
        self.assertEqual(len(sc.state["view"]["history"]), 2)

    def test_dropped_read_is_not_saved_and_sticker_is_kept(self):
        pack = make_pack(self.clock, fail_after=60)
        sc = self.make(pack)
        real = dump.dump_registers

        def typed_during_read(m, **k):
            sc.set_sticker("ABCD1234 180828 7654321")
            return real(m, **k)

        dump.dump_registers = typed_during_read
        try:
            self.run_until(sc, scanner.ATTACHED)
        finally:
            dump.dump_registers = real
        self.assertEqual(sc.state["view"]["verdict"]["result"], runner.DROPPED)
        self.assertEqual(list((self.data / "captures").glob("pack_*.json")), [])
        self.assertEqual(sc.retry_sticker, "ABCD1234 180828 7654321")

    def test_will_not_close_with_a_battery_attached(self):
        pack = make_pack(self.clock)
        sc = self.make(pack)
        self.run_until(sc, scanner.ATTACHED)
        self.assertFalse(sc.request_close())
        for _ in range(5):
            self.assertTrue(sc.step())
        self.assertEqual(sc.state["phase"], scanner.CLOSING)
        self.assertFalse(pack.port.closed)
        self.assertEqual(self.closed_calls, 0)

        pack.attached = False
        self.assertFalse(sc.step())                 # closes, and the loop ends
        self.assertTrue(pack.port.closed)
        self.assertFalse(pack.port.closed_with_pack_attached)
        self.assertTrue(pack.port.closed_with_line_low)
        self.assertEqual(self.closed_calls, 1)
        self.assertTrue(sc.request_close())

    def test_close_can_be_called_off(self):
        pack = make_pack(self.clock)
        sc = self.make(pack)
        self.run_until(sc, scanner.ATTACHED)
        sc.request_close()
        sc.step()
        sc.cancel_close()
        self.assertEqual(sc.state["phase"], scanner.ATTACHED)
        sc.step()
        self.assertFalse(pack.port.closed)

    def test_untested_adapter_is_not_opened_until_accepted(self):
        pack = make_pack(self.clock, attached=False)
        sc = self.make(pack, ports=(RISKY,))
        for _ in range(3):
            sc.step()
        self.assertEqual(sc.state["phase"], scanner.CONFIRM)
        self.assertEqual(sc.opened, [])
        sc.accept_adapter(RISKY["device"])
        self.run_until(sc, scanner.WAITING)
        self.assertEqual(sc.opened, [RISKY["device"]])

    def test_no_adapter_closes_at_once(self):
        sc = self.make(make_pack(self.clock), ports=())
        sc.step()
        self.assertEqual(sc.state["phase"], scanner.NO_ADAPTER)
        sc.request_close()
        self.assertFalse(sc.step())
        self.assertEqual(self.closed_calls, 1)


if __name__ == "__main__":
    unittest.main()
