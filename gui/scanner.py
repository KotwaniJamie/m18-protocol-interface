"""The one thread that owns the serial port.

Same rules as tools/runner.py, because the hazard is the same: one process
holds the port for the whole session with the line held low, batteries come
and go freely, and the port is never closed while a battery answers --
closing lets the line go high, and 0.48 s later the pack counts a dumb
charge, permanently.

The window never touches the port. It reads snapshot() and calls the few
methods below; everything that talks to a battery happens on this thread.
"""
import datetime
import json
import pathlib
import sys
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "gui"))

import adapters
import dump
import model
import runner

# Phases the screen follows.
NO_ADAPTER = "no_adapter"     # nothing plugged in, or nothing usable
CONFIRM = "confirm_adapter"   # an untested chip: the person has to choose
WAITING = "waiting"           # line low, knocking once a second
READING = "reading"           # a battery answered; reading all registers
ATTACHED = "attached"         # read done, battery still on the adapter
CLOSING = "closing"           # asked to quit; waiting for the battery to go
CLOSED = "closed"             # port closed, safe to exit

class Scanner(threading.Thread):
    def __init__(self, data_dir, port=None, open_m18=None, find_ports=adapters.find,
                 on_closed=None, now=datetime.datetime.now):
        super().__init__(daemon=True, name="scanner")
        self.data_dir = pathlib.Path(data_dir)
        self.captures = self.data_dir / "captures"
        self.forced_port = port
        self.open_m18 = open_m18 or _open_m18
        self.find_ports = find_ports
        self.on_closed = on_closed
        self.now = now

        self.m = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.close_wanted = False
        self.accepted = set()     # untested adapters the person chose to use
        self.retry_sticker = None
        self.retry_name = None    # the user's own name for a pack whose sticker is worn off
        self.misses = 0
        self.pack = None          # the battery currently on screen
        self.run_log = None
        self.version = 0
        self.state = {"phase": NO_ADAPTER, "adapter": None, "progress": 0,
                      "view": None, "sticker": None, "removed": False,
                      "error": None, "saved": None}

    # -- what the window sees -------------------------------------------------
    def snapshot(self):
        with self.lock:
            return {**self.state, "version": self.version}

    def _set(self, **changes):
        with self.lock:
            self.state.update(changes)
            self.version += 1

    # -- what the window can ask for ------------------------------------------
    def accept_adapter(self, device):
        self.accepted.add(device)
        self.wake.set()

    def request_close(self):
        """Ask to quit. True means the port is already closed and the window
        may go. Otherwise the scanner closes it once no battery answers, then
        calls on_closed()."""
        if self.state["phase"] == CLOSED:
            return True
        self.close_wanted = True
        self.wake.set()
        return False

    def cancel_close(self):
        if self.state["phase"] != CLOSED:
            self.close_wanted = False
            self._set(phase=self._resting_phase())

    def set_sticker(self, text):
        """The case sticker, typed any time from insertion to the next battery."""
        text = (text or "").strip()
        if not text:
            sticker, problem = None, None
        elif text == "?":   # the sticker is worn off; see set_name()
            sticker, problem = "UNREADABLE", None
        else:
            sticker, problem = runner.parse_sticker(text)
        with self.lock:
            pack = self.pack
        date_ok, date_msg = None, None
        if pack is not None:
            pack["sticker"] = sticker
            if sticker and sticker != "UNREADABLE" and pack.get("name"):
                self.set_name("")   # a real sticker makes the made-up name redundant
            doc = pack.get("doc")
            if doc is not None and pack.get("path"):
                doc["case_serial"] = sticker or ""
                pack["path"].write_text(json.dumps(doc, indent=2, default=str))
            if doc is not None and sticker and sticker != "UNREADABLE":
                mfg = ((doc.get("health") or {}).get("manufacture_date") or {}).get("value")
                date_ok, date_msg = runner.sticker_date_check(sticker, mfg)
                if date_ok is None:
                    date_msg = None
        info = {"text": sticker, "problem": problem, "date_ok": date_ok,
                "date_message": date_msg, "name": (pack or {}).get("name")}
        view = self.state.get("view")
        if view is not None:
            view = {**view, "identity": {**view["identity"], "sticker": sticker}}
        self._set(sticker=info, view=view)
        return info

    def set_name(self, text):
        """The user's own name for a pack whose case sticker is worn off.

        Kept apart from the sticker (which stays "UNREADABLE") so a made-up
        name is never mistaken for a printed serial or date-checked.
        """
        name = " ".join((text or "").split())[:60] or None
        with self.lock:
            pack = self.pack
        if pack is not None:
            pack["name"] = name
            doc = pack.get("doc")
            if doc is not None:
                doc["custom_name"] = name
                if pack.get("path"):
                    pack["path"].write_text(json.dumps(doc, indent=2, default=str))
        sticker = {**(self.state.get("sticker") or {}), "name": name}
        view = self.state.get("view")
        if view is not None:
            view = {**view, "identity": {**view["identity"], "name": name}}
        self._set(sticker=sticker, view=view)
        return sticker

    # -- the loop -----------------------------------------------------------
    def run(self):
        while self.step():
            pass

    def _pause(self, seconds):
        """Wait, but wake early when the window asks for something."""
        self.wake.wait(seconds)
        self.wake.clear()

    def step(self):
        """One turn of the loop. False once the port is closed for good."""
        try:
            if self.close_wanted:
                if self._try_close():
                    return False
                self._pause(runner.POLL_INTERVAL)
                return True
            if self.m is None:
                self._connect()
                if self.m is None:
                    self._pause(1.0)
                return True
            phase = self.state["phase"]
            if phase == WAITING:
                if runner.probe(self.m):
                    self._read()
                else:
                    self._pause(runner.POLL_INTERVAL)
            elif phase == ATTACHED:
                self._watch_removal()
            else:
                self._pause(runner.POLL_INTERVAL)
        except Exception as e:
            # The adapter was pulled, most likely. Whatever it was, say it
            # and start over; never leave the line in an unknown state.
            traceback.print_exc()
            self._drop_port()
            self._set(phase=NO_ADAPTER, error=f"Lost the adapter ({e.__class__.__name__}).")
            self._pause(1.0)
        return True

    def _resting_phase(self):
        if self.m is None:
            return NO_ADAPTER
        return ATTACHED if self.pack and not self.state["removed"] else WAITING

    def _connect(self):
        found = ([adapters.by_device(self.forced_port)] if self.forced_port
                 else self.find_ports())
        found = [a for a in found if a]
        if not found:
            self._set(phase=NO_ADAPTER, adapter=None)
            return
        a = found[0]
        if a["status"] != adapters.TESTED and a["device"] not in self.accepted:
            if self.state["phase"] != CONFIRM or self.state["adapter"] != a:
                self._set(phase=CONFIRM, adapter=a)
            return
        self.m = self.open_m18(a["device"])     # opening asserts idle()
        self.m.idle()
        self._set(phase=WAITING, adapter=a, error=None)

    def _drop_port(self):
        m, self.m = self.m, None
        if m is not None:
            try:
                m.idle()
                m.port.close()
            except Exception:
                pass

    # -- one battery ----------------------------------------------------------
    def _read(self):
        self._finish_pack()
        sticker, name = self.retry_sticker, self.retry_name
        self.pack = {"sticker": sticker, "name": name, "doc": None, "path": None, "logged": False}
        self.misses = 0
        self._set(phase=READING, progress=0, view=None, removed=False, saved=None,
                  sticker={"text": sticker, "problem": None, "date_ok": None,
                           "date_message": None, "name": name})

        def progress(done, total):
            self._set(progress=round(100 * done / total))

        t0 = time.time()
        try:
            records = dump.dump_registers(self.m, progress=progress)
        except Exception:
            self.retry_sticker = self.pack["sticker"]
            self.retry_name = self.pack["name"]
            self._log(result=runner.DROPPED, case_serial=self.pack["sticker"],
                      error=traceback.format_exc())
            self.pack["logged"] = True
            self._set(phase=ATTACHED, view=_dropped_view(), progress=100)
            return
        seconds = round(time.time() - t0, 1)

        doc = dump.build_document(self.m, records, self.state["adapter"]["device"],
                                  self.pack["sticker"] or "", note="via PackCheck")
        verdict = runner.classify(doc)
        earlier = self._earlier(doc.get("e_serial"))
        view = model.view(doc, earlier)
        self.pack["doc"] = doc

        if verdict["result"] == runner.DROPPED:
            # A partial read must never overwrite a good one: not saved, and
            # the sticker is offered again on reseat.
            self.retry_sticker = self.pack["sticker"]
            self.retry_name = self.pack["name"]
            self._log(result=verdict["result"], reasons=verdict["reasons"],
                      case_serial=self.pack["sticker"], e_serial=doc.get("e_serial"),
                      state_counts=doc["state_counts"])
            self.pack["logged"] = True
            self._set(phase=ATTACHED, view=view, progress=100)
            return

        self.retry_sticker = self.retry_name = None
        doc["runner"] = {"result": verdict["result"], "reasons": verdict["reasons"],
                         "read_seconds": seconds}
        es = doc.get("e_serial")
        name = (runner.rescan_name(es, self.now()) if runner.existing_captures(self.captures, es)
                else f"pack_{es or 'unknown'}_full.json")
        self.pack["path"] = dump.write_document(doc, self.captures, name)
        self.pack["verdict"] = verdict
        self.pack["seconds"] = seconds
        self._set(phase=ATTACHED, view=view, progress=100,
                  saved={"file": self.pack["path"].name, "rescan": bool(earlier)})
        # Re-check whatever was typed during the read against the build date,
        # and write any name given during the read into the saved file.
        self.set_sticker(self.pack["sticker"] or "")
        if self.pack.get("name"):
            self.set_name(self.pack["name"])

    def _watch_removal(self):
        """One knock per turn; two silent knocks in a row mean it was removed."""
        self._pause(runner.POLL_INTERVAL)
        if self.close_wanted:
            return
        self.misses = 0 if runner.probe(self.m) else self.misses + 1
        if self.misses >= runner.REMOVAL_MISSES:
            self._finish_pack()
            self._set(phase=WAITING, removed=True)

    def _finish_pack(self):
        """Log the battery once, with the sticker as it finally stands."""
        p = self.pack
        if not p or p["logged"] or p.get("doc") is None:
            return
        p["logged"] = True
        doc = p["doc"]
        v = p["verdict"]
        with (self.captures / "index.jsonl").open("a") as f:
            f.write(json.dumps({"captured": doc["captured_at"], "e_serial": doc["e_serial"],
                                "case_serial": p["sticker"], "custom_name": p.get("name"),
                                "note": f"PackCheck: {v['result']}"}) + "\n")
        self._log(result=v["result"], reasons=v["reasons"], case_serial=p["sticker"],
                  custom_name=p.get("name"), e_serial=doc.get("e_serial"), file=p["path"].name,
                  state_counts=doc["state_counts"], read_seconds=p["seconds"])

    def _earlier(self, e_serial):
        out = []
        for path in runner.existing_captures(self.captures, e_serial):
            try:
                out.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass
        return out

    def _log(self, **entry):
        runs = self.data_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        if self.run_log is None:
            self.run_log = runs / f"run_{self.now():%Y%m%d-%H%M%S}.jsonl"
        entry = {"time": self.now().isoformat(timespec="seconds"), "via": "PackCheck", **entry}
        with self.run_log.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    # -- closing --------------------------------------------------------------
    def _try_close(self):
        """Close the port only once no battery answers. True when closed."""
        if self.m is not None:
            self._set(phase=CLOSING)
            if runner.probe(self.m) or runner.present_slow(self.m):
                return False
            self._finish_pack()
            self.m.idle()
            runner._sleep(0.5)
            try:
                self.m.port.close()
            except Exception:
                pass
            self.m = None
        self._set(phase=CLOSED)
        if self.on_closed:
            self.on_closed()
        return True


def _dropped_view():
    return {"verdict": {"state": model.LOOK, "result": runner.DROPPED,
                        "status": "! Reseat it", "title": "Couldn't finish reading",
                        "summary": "The battery stopped answering partway through. "
                                   "Nothing was saved.",
                        "action": "Reseat the battery and it will be read again.",
                        "reasons": ["the read failed outright"]},
            "identity": {"sticker": None, "name": None, "serial": None, "type": None,
                         "model": None, "captured": None},
            "cells": None, "numbers": [], "wear": [], "details": [], "usage": None,
            "history": [], "readings": {"rows": [], "counts": [], "total": 0}}


def _open_m18(device):
    from m18 import M18
    return M18(device)
