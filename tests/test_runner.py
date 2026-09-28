"""
Tests for tools/runner.py (Phase 3: the batch runner).

No hardware. A fake battery that can be "plugged in" and "unplugged" by the
test script stands in for the pack, and a scripted terminal stands in for
the operator, so whole sessions -- including Ctrl+C mid-read and quitting
with a battery attached -- run in about a second.

    python3 tests/test_runner.py

The classifier is also checked against the seven real captures in
data/captures/, so a rule change that would mislabel a known pack fails here.
"""
import datetime
import glob
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
sys.path.insert(0, HERE)

import m18 as m18mod
import dump
import runner
from test_dump import FakePack, NoAnswer

CAPTURES = pathlib.Path(ROOT) / "data" / "captures"
UTC = datetime.UTC


def epoch(y, mo, d, h=0, mi=0, s=0):
    return int(datetime.datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp()).to_bytes(4, "big")


# --- a battery that can be plugged and unplugged ---------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s


class ProbePort:
    """Records every change to the line, so tests can see how long it was high."""

    def __init__(self, pack, clock):
        self.pack, self.clock = pack, clock
        self.timeout = 0.8
        self.dtr = True
        self._break = True
        self.high_spans = []          # seconds the line was high, per high period
        self._high_since = None
        self.pending = b""
        self.closed = False
        self.closed_with_pack_attached = None
        self.closed_with_line_low = None
        self.read_raises = False

    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, v):
        if not v and self._break:                       # going high
            self._high_since = self.clock.t
        if v and not self._break and self._high_since is not None:   # going low
            self.high_spans.append(self.clock.t - self._high_since)
            self._high_since = None
        self._break = v

    def reset_input_buffer(self):
        self.pending = b""

    @property
    def in_waiting(self):
        return len(self.pending)

    def write(self, data):
        # Sending bytes pulls the line low for each start bit, ending the
        # current unbroken high stretch -- which is what the pack counts.
        if not self._break and self._high_since is not None:
            self.high_spans.append(self.clock.t - self._high_since)
            self._high_since = self.clock.t
        sync = bytes([m18mod.M18.reverse_bits(None, 0xAA)])
        if data == sync and self.pack.attached and self.pack.answers_sync:
            self.pending = sync

    def read(self, n):
        if self.read_raises:
            raise OSError("device went away")
        if self.pending:
            out, self.pending = self.pending[:n], self.pending[n:]
            return out
        self.clock.sleep(self.timeout)                  # a silent read waits the timeout
        return b""

    def close(self):
        self.closed = True
        self.closed_with_pack_attached = self.pack.attached
        self.closed_with_line_low = self._break


class SessionPack(FakePack):
    """FakePack plus a physical connection the test script can toggle."""

    SYNC_BYTE = 0xAA
    reverse_bits = m18mod.M18.reverse_bits
    send = m18mod.M18.send

    def __init__(self, payloads=None, clock=None, attached=True, fail_after=None):
        super().__init__(payloads)
        self.clock = clock or Clock()
        self.port = ProbePort(self, self.clock)
        self.attached = attached
        self.answers_sync = True
        self.fail_after = fail_after          # unplugged mid-read after this many register reads
        self.ACC = 4

    def reset(self):
        """Stock handshake: high 0.3 s, then waits the full timeout if silent."""
        self.port.break_condition = True
        self.clock.sleep(0.3)
        self.port.break_condition = False
        self.clock.sleep(0.3)
        if self.attached and self.answers_sync:
            return True
        self.clock.sleep(0.8)
        return False

    def cmd(self, a, b, c, length, command=0x01):
        if self.fail_after is not None and len(self.reads) >= self.fail_after:
            self.attached = False
        if not self.attached:
            self.reads.append((a << 8) | b)
            raise ValueError("Empty response")
        return super().cmd(a, b, c, length, command)

    def idle(self):
        super().idle()
        self.port.break_condition = True
        self.port.dtr = True


def healthy_payloads(mfg=(2018, 8, 28), eserial=4769294, btype=38, cells=None):
    return {
        2: btype.to_bytes(2, "big") + eserial.to_bytes(3, "big"),
        4: epoch(*mfg),
        8: epoch(2026, 9, 28),
        12: cells or b"\x0e\x42\x0e\x40\x0e\x47\x0e\x39\x0e\x44",
        13: b"\x02\x00",
        31: (87).to_bytes(4, "big"), 32: (5).to_bytes(2, "big"), 33: (82).to_bytes(2, "big"),
        # Forge-only registers refuse on a non-Forge pack, as on hardware.
        **{i: NoAnswer for i, r in enumerate(m18mod.data_id) if "(Forge)" in r[3]},
    }


# NoAnswer raises "Empty response"; real Forge refusals are a fast 82 04 frame.
# Use the frame form so the fake matches hardware.
def as_refusals(payloads):
    raw = {i: b"\x82\x04" for i, v in payloads.items() if v is NoAnswer}
    return {i: v for i, v in payloads.items() if v is not NoAnswer}, raw


def make_pack(clock=None, **kw):
    fail_after = kw.pop("fail_after", None)
    attached = kw.pop("attached", True)
    p, raw = as_refusals(healthy_payloads(**kw))
    pack = SessionPack(p, clock=clock, attached=attached, fail_after=fail_after)
    pack.raw_frames = raw
    return pack


# --- a scripted operator ----------------------------------------------------

class ScriptIO:
    """poll_script / ask_script entries are strings, None, callables, or
    exceptions. A callable is called and its return value used, which is how
    the script plugs and unplugs the battery at the right moment."""

    def __init__(self, poll_script=(), ask_script=()):
        self.poll_script = list(poll_script)
        self.ask_script = list(ask_script)
        self.said, self.asked = [], []

    def say(self, msg=""):
        self.said.append(msg)

    def _next(self, script, fallback):
        if not script:
            return fallback
        item = script.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item() if callable(item) else item

    def ask(self, prompt):
        self.asked.append(prompt)
        if not self.ask_script:
            raise AssertionError(f"unscripted question: {prompt!r}")
        return self._next(self.ask_script, "")

    def poll(self, timeout):
        return self._next(self.poll_script, "quit")

    @property
    def text(self):
        return "\n".join(self.said)


def unplug(pack, answer=""):
    def f():
        pack.attached = False
        return answer
    return f


def plug(pack, fail_after=None):
    def f():
        pack.attached = True
        pack.fail_after = fail_after
        pack.reads.clear()
        return None
    return f


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name) / "captures"
        self.runs = pathlib.Path(self.tmp.name) / "runs"
        self.out.mkdir()
        self.clock = Clock()
        self._sleep, self._now = runner._sleep, runner._now
        runner._sleep = self.clock.sleep
        runner._now = lambda: self.clock.t

    def tearDown(self):
        runner._sleep, runner._now = self._sleep, self._now
        self.tmp.cleanup()

    def session(self, pack, io, **kw):
        r = runner.Runner(pack, io, "/dev/fake", out_dir=self.out, runs_dir=self.runs, **kw)
        r.run()
        return r

    def log(self, r):
        return [json.loads(l) for l in r.log_path.read_text().splitlines()]


# --- probe ------------------------------------------------------------------

class TestProbe(RunnerCase):
    def test_attached(self):
        pack = make_pack(self.clock)
        self.assertTrue(runner.probe(pack))
        self.assertTrue(pack.port.break_condition, "line must be left LOW")

    def test_nothing_attached(self):
        pack = make_pack(self.clock, attached=False)
        self.assertFalse(runner.probe(pack))
        self.assertTrue(pack.port.break_condition)

    def test_error_leaves_line_low_and_timeout_restored(self):
        pack = make_pack(self.clock)
        pack.port.read_raises = True
        self.assertFalse(runner.probe(pack))
        self.assertTrue(pack.port.break_condition)

    def test_high_time_stays_under_the_dumb_charge_threshold(self):
        """The safety property of the whole probe.

        A battery plugged in mid-knock sees at most this much *unbroken* high.
        The sync byte splits a knock into two stretches; the silent wait after
        it is the one that grows with the timeout. Upstream reset() waits
        0.8 s there, past 0.48 s. The probe caps it.
        """
        for attached in (True, False):
            pack = make_pack(self.clock, attached=attached)
            runner.probe(pack)
            longest = max(pack.port.high_spans)
            self.assertLess(longest, 0.48, f"attached={attached}: {longest:.2f}s unbroken high")

    def test_timeout_covers_the_measured_reply(self):
        """Pack 2 answered in 201-215 ms (holder.py latency, 2026-09-28)."""
        self.assertGreater(runner.PROBE_TIMEOUT, 0.215)

    def test_reports_latency(self):
        pack = make_pack(self.clock)
        self.assertIsNotNone(runner.probe_latency(pack))
        pack.attached = False
        self.assertIsNone(runner.probe_latency(pack))

    def test_does_not_touch_port_settings(self):
        """Changing port.timeout re-runs tcsetattr; the probe must not."""
        pack = make_pack(self.clock)
        pack.port.timeout = 0.8
        runner.probe(pack)
        pack.attached = False
        runner.probe(pack)
        self.assertEqual(pack.port.timeout, 0.8)

    def test_slow_pack_missed_by_probe_is_still_caught_at_close(self):
        """If PROBE_TIMEOUT is ever too short for a real pack -- as 0.05 s was --
        close_safely must still refuse, via the stock handshake."""
        pack = make_pack(self.clock)
        pack.answers_sync = True
        real = runner.probe
        runner.probe = lambda m, **k: False          # probe blind to this pack
        try:
            io = ScriptIO(ask_script=[unplug(pack)])
            runner.close_safely(pack, io)
        finally:
            runner.probe = real
        self.assertIn("still attached", io.text)
        self.assertFalse(pack.port.closed_with_pack_attached)

    def test_upstream_reset_would_not_be_safe_here(self):
        """Documents why probe() exists rather than calling M18.reset()."""
        pack = make_pack(self.clock, attached=False)
        pack.port.break_condition = False
        self.clock.sleep(0.3)
        pack.port.write(b"\x55")           # the sync byte
        pack.port.read(1)                  # what reset() does: wait the full timeout
        pack.port.break_condition = True
        self.assertGreater(pack.port.high_spans[-1], 0.48)


# --- sticker ------------------------------------------------------------------

class TestSticker(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(runner.parse_sticker("G29JDCBC 180720 3894605"),
                         ("G29JDCBC 180720 3894605", None))

    def test_lowercase_and_spacing(self):
        self.assertEqual(runner.parse_sticker("  g29jdcbc   180720 3894605 ")[0],
                         "G29JDCBC 180720 3894605")

    def test_no_spaces(self):
        self.assertEqual(runner.parse_sticker("G29JDCBC1807203894605")[0],
                         "G29JDCBC 180720 3894605")

    def test_letters_kept(self):
        """A numeric-only field would mangle this. CLAUDE.md, Case serials."""
        s, problem = runner.parse_sticker("G29NDCBC 240423 5076988")
        self.assertIsNone(problem)
        self.assertTrue(s.startswith("G29NDCBC"))

    def test_wrong_shape(self):
        s, problem = runner.parse_sticker("3894605")
        self.assertIsNotNone(problem)
        self.assertEqual(s, "3894605")

    def test_date_matches_across_midnight(self):
        """Pack 2: sticker 240423, battery 2024-04-22 23:57:52 UTC."""
        built = datetime.datetime(2024, 4, 22, 23, 57, 52, tzinfo=UTC)
        ok, _ = runner.sticker_date_check("G29NDCBC 240423 5076988", built)
        self.assertTrue(ok)

    def test_date_mismatch(self):
        built = datetime.datetime(2018, 8, 28, tzinfo=UTC)
        ok, msg = runner.sticker_date_check("G29NDCBC 240423 5076988", built)
        self.assertFalse(ok)
        self.assertIn("2024-04-23", msg)

    def test_impossible_date(self):
        ok, _ = runner.sticker_date_check("G29NDCBC 241399 5076988",
                                          datetime.datetime(2024, 4, 22, tzinfo=UTC))
        self.assertFalse(ok)

    def test_uncheckable_is_not_a_mismatch(self):
        self.assertIsNone(runner.sticker_date_check("UNREADABLE", None)[0])
        self.assertIsNone(runner.sticker_date_check("G29NDCBC 240423 5076988", None)[0])

    def test_json_string_dates(self):
        ok, _ = runner.sticker_date_check("G29NDCBC 240423 5076988",
                                          "2024-04-22 23:57:52+00:00")
        self.assertTrue(ok)


# --- classify, against the real captures --------------------------------------

def real_captures():
    out = {}
    for f in sorted(CAPTURES.glob("pack_*_full.json")):
        if f.stem.count("_") == 2:       # skip timestamped re-scans
            d = json.loads(f.read_text())
            out[str(d["e_serial"])] = d
    return out


class TestClassifyRealPacks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs = real_captures()

    def test_all_seven_present(self):
        self.assertEqual(len(self.packs), 7)

    def test_labels(self):
        """Six clean packs, one flagged for imbalance -- matches CLAUDE.md."""
        got = {es: runner.classify(d)["result"] for es, d in self.packs.items()}
        expected = {es: runner.OK for es in self.packs}
        expected["5950263"] = runner.FLAGGED
        self.assertEqual(got, expected)

    def test_modern_pack_sentinels_are_not_death(self):
        """91 sentinels outside the health registers, on a healthy pack."""
        d = self.packs["6278308"]
        self.assertEqual(d["state_counts"].get("sentinel"), 91)
        self.assertEqual(runner.classify(d)["result"], runner.OK)

    def test_low_charge_imbalance_gets_the_balancing_advice(self):
        reasons = runner.classify(self.packs["5950263"])["reasons"]
        self.assertTrue(any("67 mV" in r and "balancing" in r for r in reasons), reasons)

    def test_every_sticker_matches_its_battery(self):
        for es, d in self.packs.items():
            ok, msg = runner.sticker_date_check(d["case_serial"],
                                                d["health"]["manufacture_date"]["value"])
            self.assertTrue(ok, f"{es}: {msg}")


class TestClassifySynthetic(unittest.TestCase):
    def doc(self, pack):
        return dump.build_document(pack, dump.dump_registers(pack))

    def test_dead(self):
        dead = FakePack({i: b"\xff" * m18mod.data_id[i][1] for i in range(len(m18mod.data_id))})
        self.assertEqual(runner.classify(self.doc(dead))["result"], runner.DEAD)

    def test_a_few_blank_health_readings_are_flagged_not_dead(self):
        p = make_pack()
        for i in (39, 40):
            p.payloads[i] = b"\xff\xff"
        v = runner.classify(self.doc(p))
        self.assertEqual(v["result"], runner.FLAGGED)

    def test_silent_after_sync(self):
        silent = FakePack({i: NoAnswer for i in range(len(m18mod.data_id))})
        self.assertEqual(runner.classify(self.doc(silent))["result"], runner.DROPPED)

    def test_unplugged_mid_read(self):
        v = runner.classify(self.doc(make_pack(fail_after=60)))
        self.assertEqual(v["result"], runner.DROPPED)
        self.assertIn("connection lost", v["reasons"][0])

    def test_non_forge_refusal_is_dropped(self):
        p = make_pack()
        p.raw_frames[29] = b"\x82\x04"
        self.assertEqual(runner.classify(self.doc(p))["result"], runner.DROPPED)

    def test_healthy_fake_is_ok(self):
        self.assertEqual(runner.classify(self.doc(make_pack()))["result"], runner.OK)


class TestGiveUp(unittest.TestCase):
    def test_stops_quickly_when_unplugged(self):
        """Without this, each of ~150 remaining registers waits 0.8 s."""
        p = make_pack(fail_after=60)
        recs = dump.dump_registers(p)
        self.assertEqual(len(recs), len(m18mod.data_id))
        attempted_after_loss = len(p.reads) - 60
        self.assertLessEqual(attempted_after_loss, dump.GIVE_UP_AFTER)
        self.assertEqual(recs[-1]["error"], dump.NOT_ATTEMPTED)

    def test_forge_refusals_do_not_trigger_it(self):
        """13 refused registers on a healthy pack are fast 82 04s, not silence."""
        recs = dump.dump_registers(make_pack())
        self.assertFalse(any(r.get("error") == dump.NOT_ATTEMPTED for r in recs))


# --- whole sessions -------------------------------------------------------------

STICKER = "B41VDCBA 180828 1431974"


class TestSessions(RunnerCase):
    def test_one_pack_start_to_finish(self):
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"],
                      ask_script=[STICKER, unplug(pack)])
        r = self.session(pack, io)

        saved = self.out / "pack_4769294_full.json"
        self.assertTrue(saved.exists())
        doc = json.loads(saved.read_text())
        self.assertEqual(doc["case_serial"], STICKER)
        self.assertEqual(doc["runner"]["result"], runner.OK)
        h = doc["health"]
        self.assertEqual((h["charge_count_redlink"]["value"], h["charge_count_dumb"]["value"],
                          h["charge_count_total"]["value"]), (82, 5, 87))
        self.assertEqual([e["result"] for e in self.log(r)], [runner.OK])
        self.assertTrue(pack.port.closed)
        self.assertFalse(pack.port.closed_with_pack_attached)
        self.assertTrue(pack.port.closed_with_line_low)

    def test_refuses_to_close_with_a_pack_attached(self):
        """quit typed while a battery is still connected."""
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=["quit"], ask_script=[unplug(pack)])
        self.session(pack, io)
        self.assertIn("still attached", io.text)
        self.assertFalse(pack.port.closed_with_pack_attached)

    def test_ctrl_c_during_sticker_entry(self):
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None], ask_script=[KeyboardInterrupt, unplug(pack)])
        self.session(pack, io)
        self.assertTrue(pack.port.closed)
        self.assertFalse(pack.port.closed_with_pack_attached)
        # The read ran to completion before the port closed under it.
        self.assertGreaterEqual(len(pack.reads), len(m18mod.data_id))

    def test_ctrl_c_while_refusing_does_not_close(self):
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=["quit"], ask_script=[KeyboardInterrupt, unplug(pack)])
        self.session(pack, io)
        self.assertIn("Not closing", io.text)
        self.assertFalse(pack.port.closed_with_pack_attached)

    def test_input_closed_with_pack_attached_waits_for_removal(self):
        """EOF on stdin must not become 'close the port with a battery on it'."""
        pack = make_pack(self.clock)
        real_probe = runner.probe
        seen = {"n": 0}

        def probe(m, **k):
            seen["n"] += 1
            if seen["n"] == 8:            # removed a few seconds after input died
                m.attached = False
            return real_probe(m, **k)

        runner.probe = probe
        try:
            io = ScriptIO(poll_script=["quit"], ask_script=[EOFError])
            self.session(pack, io)
        finally:
            runner.probe = real_probe
        self.assertIn("holding the line low", io.text)
        self.assertTrue(pack.port.closed)
        self.assertFalse(pack.port.closed_with_pack_attached)

    def test_input_closed_during_sticker_entry(self):
        pack = make_pack(self.clock)
        real_probe = runner.probe
        seen = {"n": 0}

        def probe(m, **k):
            seen["n"] += 1
            if seen["n"] == 4:
                m.attached = False
            return real_probe(m, **k)

        runner.probe = probe
        try:
            io = ScriptIO(poll_script=[None], ask_script=[EOFError, EOFError])
            self.session(pack, io)
        finally:
            runner.probe = real_probe
        self.assertFalse(pack.port.closed_with_pack_attached)
        self.assertGreaterEqual(len(pack.reads), len(m18mod.data_id), "read was cut off")

    def test_removal_is_verified(self):
        """Pressing Enter with the battery still in is caught."""
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"], ask_script=[STICKER, "", unplug(pack)])
        self.session(pack, io)
        self.assertIn("still see a battery", io.text)

    def test_duplicate_saved_as_rescan(self):
        original = self.out / "pack_4769294_full.json"
        original.write_text('{"original": true}')
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"], ask_script=[STICKER, "", unplug(pack)])
        self.session(pack, io)
        self.assertEqual(original.read_text(), '{"original": true}')
        self.assertEqual(len(list(self.out.glob("pack_4769294_full_*.json"))), 1)

    def test_duplicate_declined(self):
        (self.out / "pack_4769294_full.json").write_text("{}")
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"], ask_script=[STICKER, "n", unplug(pack)])
        r = self.session(pack, io)
        self.assertEqual(len(list(self.out.glob("pack_4769294_full*.json"))), 1)
        self.assertIsNone(self.log(r)[0]["file"])

    def test_wrong_sticker_caught_and_retyped(self):
        pack = make_pack(self.clock)
        wrong = "G29NDCBC 240423 5076988"
        io = ScriptIO(poll_script=[None, "quit"],
                      ask_script=[wrong, "r", STICKER, unplug(pack)])
        self.session(pack, io)
        self.assertIn("WARNING", io.text)
        doc = json.loads((self.out / "pack_4769294_full.json").read_text())
        self.assertEqual(doc["case_serial"], STICKER)

    def test_odd_sticker_accepted_on_confirmation(self):
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"], ask_script=["ABC123", "y", unplug(pack)])
        self.session(pack, io)
        doc = json.loads((self.out / "pack_4769294_full.json").read_text())
        self.assertEqual(doc["case_serial"], "ABC123")

    def test_unplugged_mid_read_then_retried(self):
        """DROPPED, reseat, read again, Enter reuses the sticker, one file."""
        pack = make_pack(self.clock, fail_after=60)
        io = ScriptIO(poll_script=[None, plug(pack), None, "quit"],
                      ask_script=[STICKER, "", "", unplug(pack)])
        r = self.session(pack, io)
        self.assertEqual([e["result"] for e in self.log(r)], [runner.DROPPED, runner.OK])
        self.assertTrue(all(e["case_serial"] == STICKER for e in self.log(r)))
        self.assertEqual(len(list(self.out.glob("pack_*_full*.json"))), 1)
        self.assertIn("Reseat", io.text)

    def test_nologo_command(self):
        pack = make_pack(self.clock, attached=False)
        io = ScriptIO(poll_script=["nologo g29jdcbc 180720 3894605", "quit"])
        r = self.session(pack, io)
        e = self.log(r)[0]
        self.assertEqual(e["result"], runner.NO_LOGO)
        self.assertEqual(e["case_serial"], "G29JDCBC 180720 3894605")

    def test_crash_on_one_pack_does_not_end_the_run(self):
        pack = make_pack(self.clock)
        calls = {"n": 0}
        real = dump.build_document

        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("synthetic")
            return real(*a, **k)

        dump.build_document = boom
        try:
            io = ScriptIO(poll_script=[None, plug(pack), None, "quit"],
                          ask_script=[STICKER, unplug(pack), STICKER, unplug(pack)])
            r = self.session(pack, io)
        finally:
            dump.build_document = real
        self.assertEqual([e["result"] for e in self.log(r)], ["ERROR", runner.OK])

    def test_auto_remove(self):
        pack = make_pack(self.clock)
        io = ScriptIO(poll_script=[None, "quit"], ask_script=[STICKER])
        r = runner.Runner(pack, io, "/dev/fake", out_dir=self.out, runs_dir=self.runs,
                          auto_remove=True)
        real_probe = runner.probe
        seen = {"n": 0}

        def probe(m, **k):
            seen["n"] += 1
            if seen["n"] == 4:            # still attached for a couple of polls, then pulled
                m.attached = False
            return real_probe(m, **k)

        runner.probe = probe
        try:
            r.run()
        finally:
            runner.probe = real_probe
        self.assertIn("Removed.", io.text)
        self.assertFalse(pack.port.closed_with_pack_attached)


if __name__ == "__main__":
    unittest.main(verbosity=2)
