"""Phase 3: the batch runner. Plug in a battery, type its sticker, next.

    .venv/bin/python tools/runner.py

One process holds the serial port for the whole session with TX held low, so
batteries can be connected and removed freely (see "The dumb-charge counter"
in CLAUDE.md). For each battery:

    1. notice it was plugged in           -- probe(), about once a second
    2. read all 184 registers             -- tools/dump.py, in a background thread
    3. ask for the case sticker meanwhile -- checked against the battery's own date
    4. say what happened, in plain words  -- classify(), never health()'s one sentence
    5. wait for it to be removed          -- Enter to confirm, or --auto-remove

While waiting for a battery, type a command and press Enter:
    nologo <sticker>  -- record a pack with no wifi logo (it cannot be read)
    help              -- list commands
    quit              -- finish; refuses to close while a battery is attached

Nothing that goes wrong with one battery ends the run.
"""
import argparse
import datetime
import json
import pathlib
import re
import select
import sys
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import dump
from m18 import HEALTH_BUCKET_REGISTERS, HEALTH_REGISTERS

DEFAULT_PORT = "/dev/cu.usbserial-A94AM9KK"
DATA = HERE.parent / "data"

# --- detecting a battery ---------------------------------------------------

# How long to wait for the sync reply. The reply is one byte at 4800 baud,
# about 2 ms. Upstream reset() waits the port's 0.8 s timeout instead, and the
# line sits HIGH for that whole wait -- harmless with nothing attached, but a
# battery plugged in during that window sees 0.3 + 0.8 = 1.1 s of high, past
# the 0.48 s dumb-charge threshold. Capped here, the worst case is ~0.35 s.
PROBE_TIMEOUT = 0.05
POLL_INTERVAL = 1.0
REMOVAL_MISSES = 2          # consecutive silent probes before a pack counts as removed

_sleep = time.sleep         # patched by tests


def probe(m, timeout=PROBE_TIMEOUT):
    """Is a battery with diagnostics attached? One knock, line left LOW.

    The same handshake as M18.reset(), with a short reply timeout and an
    unconditional idle() afterwards, so the line is never left high whether
    the pack answered, stayed silent, or something raised.
    """
    port = m.port
    old_timeout = port.timeout
    try:
        port.timeout = timeout
        m.ACC = 4
        port.break_condition = True
        port.dtr = True
        _sleep(0.3)
        port.break_condition = False
        port.dtr = False
        _sleep(0.3)
        m.send(bytes([m.SYNC_BYTE]))
        b = port.read(1)
        return bool(b) and m.reverse_bits(b[0]) == m.SYNC_BYTE
    except Exception:
        return False
    finally:
        m.idle()
        port.timeout = old_timeout


# --- the case sticker ------------------------------------------------------

STICKER = re.compile(r"^([A-Z0-9]{8})\s*(\d{6})\s*(\d{7})$")


def parse_sticker(text):
    """'g29jdcbc 180720 3894605' -> ('G29JDCBC 180720 3894605', None).

    Returns (normalised, None) if it matches the 8/6/7 shape every pack so
    far has used, or (stripped text, reason) if not -- the caller may still
    accept it, since other models may print differently.
    """
    s = " ".join(text.strip().upper().split())
    m = STICKER.match(s)
    if not m:
        return s, "expected 8 letters/digits, a 6-digit date, a 7-digit number"
    return " ".join(m.groups()), None


def as_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v).date()
        except ValueError:
            return None
    return None


def sticker_date_check(sticker, manufacture_date, tolerance_days=1):
    """Does the sticker's YYMMDD agree with the battery's own build date?

    Returns (ok, message). All seven packs matched within a day (the sticker
    uses local time, the register UTC). None means it could not be checked,
    which is not the same as a mismatch.
    """
    m = STICKER.match(sticker or "")
    built = as_date(manufacture_date)
    if not m or built is None:
        return None, "could not compare dates"
    yymmdd = m.group(2)
    try:
        printed = datetime.date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:]))
    except ValueError:
        return False, f"sticker date {yymmdd} is not a real date"
    delta = abs((printed - built).days)
    if delta <= tolerance_days:
        return True, f"sticker {printed} matches build date {built}"
    return False, f"sticker says {printed}, battery says it was built {built}"


# --- what happened ---------------------------------------------------------

OK, FLAGGED, DEAD, DROPPED, NO_LOGO = "OK", "FLAGGED", "DEAD", "DROPPED", "NO_LOGO"

HEALTH_IDS = frozenset(HEALTH_REGISTERS.values()) | frozenset(HEALTH_BUCKET_REGISTERS)
SERIAL_ID = HEALTH_REGISTERS["serial_raw"]

# This many health registers reading all-ones means the BMS is talking but the
# data is gone. Fewer than this is odd enough to flag but not to write off.
DEAD_MIN_SENTINELS = 5

# Provisional -- CLAUDE.md: below ~22 mV imbalance carries no information, and
# real cutoffs wait for the full pile. This is a "look at me", not a verdict.
IMBALANCE_FLAG_MV = 30
LOW_CHARGE_COUNT = 20


def classify(doc):
    """Turn a capture into one outcome and the plain-English reasons for it.

    Works on a live document or one loaded back from JSON.
    """
    regs = doc.get("registers") or []
    h = doc.get("health") or {}
    val = lambda k: (h.get(k) or {}).get("value")

    if not doc.get("answered"):
        return {"result": DROPPED, "reasons": ["the battery answered the knock, then nothing else"]}

    # On both firmware generations, every register that failed to answer was
    # a Forge-only one. Anything else failing means the connection broke.
    broken = [r for r in regs
              if r["state"] in (dump.ABSENT, dump.TRUNCATED) and "(Forge)" not in r["label"]]
    if broken:
        lost = any(r.get("error") == dump.NOT_ATTEMPTED for r in broken)
        first = broken[0]
        return {"result": DROPPED, "reasons": [
            ("connection lost partway through" if lost else "some registers did not answer")
            + f" (from register {first['id']}, {first['label'].strip()})"]}

    sent = [r for r in regs if r["id"] in HEALTH_IDS and r["state"] == dump.SENTINEL]
    serial_dead = any(r["id"] == SERIAL_ID for r in sent)
    if serial_dead or len(sent) >= DEAD_MIN_SENTINELS:
        return {"result": DEAD, "reasons": [
            f"{len(sent)} of {len(HEALTH_IDS)} health readings are blank (all-ones) -- "
            "the battery talks but its records are unusable"]}

    reasons = []
    if sent:
        reasons.append(f"{len(sent)} health reading(s) blank: "
                       + ", ".join(r["label"].strip() for r in sent))
    for r in regs:
        if "partial_sentinel" in r.get("flags", []):
            reasons.append(f"some cells unreadable in {r['label'].strip()}")
        if r["state"] == dump.UNDECODABLE and r["id"] in HEALTH_IDS:
            reasons.append(f"could not decode {r['label'].strip()}")

    imbal, charges = val("cell_imbalance_mv"), val("charge_count_total")
    if isinstance(imbal, (int, float)) and imbal > IMBALANCE_FLAG_MV:
        msg = f"cells {imbal} mV apart"
        if isinstance(charges, int) and charges < LOW_CHARGE_COUNT:
            msg += (f" on only {charges} charges -- may just need balancing; "
                    "charge fully 2-3 times and re-scan before judging")
        reasons.append(msg)

    if (h.get("battery_description") or {}).get("valid") is False:
        reasons.append(f"unknown battery type {val('battery_type')} -- cycles cannot be worked out")

    return {"result": FLAGGED if reasons else OK, "reasons": reasons}


# --- files -----------------------------------------------------------------

def existing_captures(out_dir, e_serial):
    return sorted(out_dir.glob(f"pack_{e_serial}_full*.json")) if e_serial else []


def rescan_name(e_serial, now=None):
    now = now or datetime.datetime.now()
    return f"pack_{e_serial}_full_{now:%Y%m%d-%H%M%S}.json"


# --- the terminal ----------------------------------------------------------

class Terminal:
    """Real stdin/stdout. Tests swap in a scripted stand-in."""

    def say(self, msg=""):
        print(msg, flush=True)

    def ask(self, prompt):
        return input(prompt)

    def poll(self, timeout):
        """A typed line if one arrives within `timeout` seconds, else None."""
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        line = sys.stdin.readline()
        if line == "":           # stdin closed
            return "quit"
        return line.strip()


# --- the runner ------------------------------------------------------------

class Runner:
    def __init__(self, m, io, port_name="", out_dir=DATA / "captures",
                 runs_dir=DATA / "runs", auto_remove=False, now=None):
        self.m = m
        self.io = io
        self.port_name = port_name
        self.out_dir = pathlib.Path(out_dir)
        self.auto_remove = auto_remove
        self.now = now or datetime.datetime.now
        runs_dir = pathlib.Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = runs_dir / f"run_{self.now():%Y%m%d-%H%M%S}.jsonl"
        self.retry_sticker = None
        self.tally = {}

    # -- logging --------------------------------------------------------
    def log(self, **entry):
        entry = {"time": self.now().isoformat(timespec="seconds"), **entry}
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        self.tally[entry.get("result")] = self.tally.get(entry.get("result"), 0) + 1

    # -- top level ------------------------------------------------------
    def run(self):
        self.io.say("Runner up. Serial line held LOW -- plug batteries in and out freely.")
        self.io.say("Waiting for a battery.  (type 'help' for commands)")
        try:
            while self.wait_for_pack():
                try:
                    self.handle_pack()
                except (KeyboardInterrupt, EOFError):
                    raise
                except Exception:
                    self.io.say("Something went wrong with this battery -- logged, carrying on.")
                    self.io.say(traceback.format_exc())
                    self.log(result="ERROR", error=traceback.format_exc())
                self.wait_for_removal()
                self.io.say("\nWaiting for the next battery.")
        except KeyboardInterrupt:
            self.io.say("\nStopping.")
        except EOFError:
            self.io.say("\nInput closed. Stopping.")
        finally:
            self.safe_close()

    # -- step 1: notice a battery ----------------------------------------
    def wait_for_pack(self):
        """True when a battery appears, False when the operator quits."""
        while True:
            line = self.io.poll(POLL_INTERVAL)
            if line:
                if not self.command(line):
                    return False
                continue
            if probe(self.m):
                return True

    def command(self, line):
        """Handle a typed command. Returns False to quit."""
        word, _, rest = line.partition(" ")
        word = word.lower()
        if word in ("quit", "q", "exit"):
            return False
        if word == "help":
            self.io.say("  nologo <sticker>   record a pack with no wifi logo\n"
                        "  quit               finish (unplug the battery first)")
        elif word == "nologo":
            sticker, problem = parse_sticker(rest)
            if not rest.strip():
                self.io.say("  usage: nologo <sticker>")
                return True
            if problem:
                self.io.say(f"  note: {problem} -- recorded as typed")
            self.log(result=NO_LOGO, case_serial=sticker)
            self.io.say(f"  recorded {sticker} as no-logo (cannot be read)")
        else:
            self.io.say(f"  unknown command {word!r} -- type 'help'")
        return True

    # -- steps 2-4: read, ask, report ------------------------------------
    def handle_pack(self):
        self.io.say("\nBattery detected -- reading (about 25 seconds).")
        box = {}

        def read():
            t0 = time.time()
            try:
                box["records"] = dump.dump_registers(self.m)
            except Exception:
                box["error"] = traceback.format_exc()
            box["seconds"] = time.time() - t0

        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        try:
            sticker = self.ask_sticker()
            if worker.is_alive():
                self.io.say("  still reading...")
            worker.join()
        except BaseException:
            worker.join()             # never touch the port under a live read
            raise

        if "error" in box:
            self.retry_sticker = sticker
            self.log(result=DROPPED, case_serial=sticker, error=box["error"])
            self.report(DROPPED, ["the read failed outright"], sticker, None, None)
            return

        doc = dump.build_document(self.m, box["records"], self.port_name, sticker)
        verdict = classify(doc)
        result, reasons = verdict["result"], verdict["reasons"]

        if result == DROPPED:
            # A partial read must never overwrite a good one.
            self.retry_sticker = sticker
            self.log(result=result, reasons=reasons, case_serial=sticker,
                     e_serial=doc.get("e_serial"), state_counts=doc["state_counts"])
            self.report(result, reasons, sticker, doc, None)
            return
        self.retry_sticker = None

        sticker = self.confirm_date(sticker, doc)
        doc["case_serial"] = sticker
        doc["runner"] = {"result": result, "reasons": reasons,
                         "read_seconds": round(box["seconds"], 1)}

        name = self.choose_filename(doc)
        path = dump.write_document(doc, self.out_dir, name) if name else None
        with (self.out_dir / "index.jsonl").open("a") as f:
            f.write(json.dumps({"captured": doc["captured_at"], "e_serial": doc["e_serial"],
                                "case_serial": sticker,
                                "note": f"runner: {result}"
                                + ("" if path else " (not saved, duplicate)")}) + "\n")
        self.log(result=result, reasons=reasons, case_serial=sticker,
                 e_serial=doc.get("e_serial"), file=path.name if path else None,
                 state_counts=doc["state_counts"], read_seconds=round(box["seconds"], 1))
        self.report(result, reasons, sticker, doc, path)

    def ask_sticker(self):
        default = self.retry_sticker
        while True:
            hint = f" [Enter = {default}]" if default else ""
            raw = self.io.ask(f"  Case sticker{hint}: ").strip()
            if not raw and default:
                return default
            if not raw:
                self.io.say("  Type the sticker, or '?' if it is unreadable.")
                continue
            if raw == "?":
                return "UNREADABLE"
            sticker, problem = parse_sticker(raw)
            if not problem:
                return sticker
            yn = self.io.ask(f"  '{sticker}' -- {problem}. Use it anyway? [y/N] ")
            if yn.strip().lower().startswith("y"):
                return sticker

    def confirm_date(self, sticker, doc):
        """Catch a sticker typed against the wrong battery before it is saved."""
        while True:
            mfg = ((doc.get("health") or {}).get("manufacture_date") or {}).get("value")
            ok, msg = sticker_date_check(sticker, mfg)
            if ok is not False:
                return sticker
            self.io.say(f"  WARNING: {msg}.")
            yn = self.io.ask("  Retype the sticker (r) or keep it (k)? [r/k] ")
            if yn.strip().lower().startswith("k"):
                return sticker
            sticker = self.ask_sticker()

    def choose_filename(self, doc):
        """None means do not save. Never silently overwrites an earlier capture."""
        es = doc.get("e_serial")
        prior = existing_captures(self.out_dir, es)
        if not prior:
            return f"pack_{es or 'unknown'}_full.json"
        self.io.say(f"  e-serial {es} was already captured ({prior[-1].name}).")
        yn = self.io.ask("  Save this as a re-scan alongside it? [Y/n] ")
        if yn.strip().lower().startswith("n"):
            return None
        return rescan_name(es, self.now())

    def report(self, result, reasons, sticker, doc, path):
        h = (doc or {}).get("health") or {}
        v = lambda k: (h.get(k) or {}).get("value")
        self.io.say("  " + "-" * 60)
        head = f"  {result:8} e-serial {doc.get('e_serial') if doc else '?'}"
        if v("battery_description"):
            head += f" -- {v('battery_description')}"
        self.io.say(head)
        self.io.say(f"           sticker {sticker}")
        if doc and doc.get("answered") and result != DROPPED:
            cyc = v("total_discharge_cycles")
            cyc = f"{cyc:.2f}" if isinstance(cyc, (int, float)) else "--"
            self.io.say(f"           cycles {cyc} | charges {v('charge_count_redlink')}"
                        f"+{v('charge_count_dumb')}={v('charge_count_total')} | "
                        f"imbalance {v('cell_imbalance_mv')} mV")
        for r in reasons:
            self.io.say(f"           * {r}")
        if result == DROPPED:
            self.io.say("           Reseat the battery; it will be read again and "
                        "you can press Enter to reuse the sticker.")
        elif result == DEAD:
            self.io.say("           Set this one aside -- its records cannot be trusted.")
        if path:
            self.io.say(f"           saved {path.name}")
        elif doc and result != DROPPED:
            self.io.say("           not saved (duplicate)")
        self.io.say("  " + "-" * 60)

    # -- step 5: wait for removal ----------------------------------------
    def wait_for_removal(self):
        if self.auto_remove:
            self.io.say("  Remove the battery.")
            misses = 0
            while misses < REMOVAL_MISSES:
                _sleep(POLL_INTERVAL)
                misses = 0 if probe(self.m) else misses + 1
            self.io.say("  Removed.")
            return
        while True:
            self.io.ask("  Unplug the battery, then press Enter. ")
            if not probe(self.m):
                return
            self.io.say("  I can still see a battery attached.")

    # -- shutdown ---------------------------------------------------------
    def safe_close(self):
        close_safely(self.m, self.io)
        summary = ", ".join(f"{n} {k}" for k, n in sorted(self.tally.items()) if k)
        self.io.say(f"Port closed. {summary or 'Nothing recorded'}.")
        if self.log_path.exists():
            self.io.say(f"Run log: {self.log_path}")


def close_safely(m, io):
    """Close the port only once no battery is attached.

    Closing lets the line go high; a battery on the other end then counts a
    dumb charge 0.48 s later. This is the one irreversible mistake the
    project can make, so the program refuses rather than trusting memory.
    """
    can_ask = True
    while True:
        try:
            if not probe(m) and not probe(m):
                break
            io.say("\nA battery is still attached. Closing now would add a dumb "
                   "charge to it, permanently.")
            if can_ask:
                io.ask("Unplug it, then press Enter. ")
            else:
                # Nobody left to press Enter. Keep holding the line low and
                # keep checking; close the moment the battery is gone.
                _sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            io.say("\nNot closing while a battery is attached.")
        except EOFError:
            can_ask = False
            io.say("\nInput closed -- holding the line low until the battery "
                   "is removed.")
    io.say("No battery detected. (A pack with no wifi logo cannot be detected, "
           "so this check cannot protect one -- unplug those yourself.)")
    m.idle()
    _sleep(0.5)
    try:
        m.port.close()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Phase 3 batch runner")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--auto-remove", action="store_true",
                    help="detect removal by probing, instead of pressing Enter "
                         "(only once the repeated-probe test has passed)")
    args = ap.parse_args()

    from m18 import M18
    m = M18(args.port)       # __init__ asserts idle()
    m.idle()
    Runner(m, Terminal(), args.port, auto_remove=args.auto_remove).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
