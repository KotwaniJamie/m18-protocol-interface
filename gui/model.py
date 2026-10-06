"""What the screen shows, worked out from one capture.

The GUI draws; this decides. Everything a person could misread -- a sentinel
shown as a number, a Forge-only register shown as a fault, a verdict worded
differently from the runner's -- is settled here, where it can be tested
without a window.

Takes a capture document as tools/dump.py builds it, live (dates are
datetime) or loaded back from JSON (dates are strings). Returns plain
JSON-safe data.
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import dump
import runner
from m18 import HEALTH_BUCKET_REGISTERS, HEALTH_REGISTERS

HEALTH_IDS = runner.HEALTH_IDS

# The cell bars run on a real scale: empty to full for a Li-ion cell.
CELL_MIN_MV = 3000
CELL_MAX_MV = 4200

# Time at 110 A and above is the hard end of the histogram; drawn in black.
HARD_AMPS = 110

# Verdict states. These map onto the three colours the design allows on red:
# white (good), caution yellow (needs a look), black (dead).
GOOD, LOOK, DEAD = "good", "look", "dead"

# Chips in "Show all readings". A register's state is part of its value:
# "not kept by this model", "never written" and 0 must not render alike.
RECORDED = "recorded"
NOT_KEPT = "not kept by this model"
NEVER_WRITTEN = "never written"
BLANK = "blank"
UNREADABLE = "unreadable"
NOT_READ = "not read"
CHIP_ORDER = (RECORDED, NOT_KEPT, NEVER_WRITTEN, BLANK, UNREADABLE, NOT_READ)


def _field(h, key):
    f = (h or {}).get(key) or {}
    return f.get("value"), bool(f.get("valid"))


def _date(v):
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _long_date(v):
    d = _date(v)
    return f"{d.day} {d:%b %Y}" if d else None


def _hours(hhmmss):
    """'8:30:18' -> '8 h 30 min'."""
    try:
        h, m, _ = (int(x) for x in str(hhmmss).split(":"))
    except ValueError:
        return None
    return f"{h:,} h {m} min" if h else f"{m} min"


def _num(v, places=0):
    if not isinstance(v, (int, float)):
        return None
    return f"{v:,.{places}f}"


# --- verdict ---------------------------------------------------------------

def imbalance_level(mv):
    if not isinstance(mv, (int, float)):
        return None
    if mv > runner.IMBALANCE_BALANCE_MV:
        return "balance"
    if mv > runner.IMBALANCE_OK_MV:
        return "slight"
    return "ok"


def verdict(doc):
    """One plain-words verdict, the reasons behind it, and what to do.

    The outcome is runner.classify()'s, so the GUI and the batch runner can
    never disagree about a pack. Only the wording is the GUI's own.
    """
    c = runner.classify(doc)
    result = c["result"]
    h = doc.get("health") or {}
    imbal, _ = _field(h, "cell_imbalance_mv")
    level = imbalance_level(imbal)

    # The imbalance reason is reworded; every other reason passes through.
    others = [r for r in c["reasons"] if not r.startswith("cell imbalance")]

    if result == runner.DROPPED:
        return {"state": LOOK, "result": result, "status": "! Reseat it", "title": "Couldn't finish reading",
                "summary": "The battery stopped answering partway through. "
                           "Nothing was saved.",
                "action": "Reseat the battery and it will be read again.",
                "reasons": c["reasons"]}
    if result == runner.DEAD:
        blank = sum(1 for r in doc.get("registers") or []
                    if r["id"] in HEALTH_IDS and r["state"] == dump.SENTINEL)
        return {"state": DEAD, "result": result, "status": "× Unusable", "title": "Records unusable",
                "summary": f"The battery talks, but {blank} of the {len(HEALTH_IDS)} "
                           "records PackCheck judges it by are blank. Its history "
                           "can't be trusted.",
                "action": "Set it aside. Don't judge it by anything below.",
                "reasons": []}

    # Titles and chip text follow the chosen mockup (design/packcheck-mockup.html
    # in the private notes repo): one short verdict, one chip.
    reasons = []
    status = "! Needs a look"
    if level == "balance":
        title = "Needs balancing"
        status = "! Needs balancing"
        summary = (f"Cell imbalance (ΔV) is {imbal} mV. M18 packs don't balance "
                   "their own cells, so this gap is capacity the pack can't use.")
        action = ("Balance the cells by hand, then re-scan after a few charges. "
                  "If the gap comes back, a cell is weak and needs replacing.")
    elif level == "slight":
        title = "Worth balancing"
        summary = (f"Cell imbalance (ΔV) is {imbal} mV, just over the 10 mV line. "
                   "Readings vary by about ±5 mV.")
        action = "Re-scan to confirm. Balancing by hand gets back full capacity."
    elif result == runner.FLAGGED:
        title = "Needs a look"
        summary = "Something in this battery's records is out of the ordinary."
        action = "See the notes below."
    else:
        title = "Good to use"
        status = "✓ Good"
        summary = (f"Cells are balanced to within {imbal} mV, and nothing in its "
                   "records stands out." if isinstance(imbal, (int, float))
                   else "Nothing in its records stands out.")
        action = None
    reasons.extend(r.replace(" -- ", " — ") for r in others)
    return {"state": GOOD if result == runner.OK else LOOK, "result": result,
            "status": status, "title": title, "summary": summary, "action": action,
            "reasons": reasons}


# --- cells -----------------------------------------------------------------

def cells(doc):
    h = doc.get("health") or {}
    mv, valid = _field(h, "cell_voltages_mv")
    imbal, imbal_ok = _field(h, "cell_imbalance_mv")
    pack_v, _ = _field(h, "pack_voltage_v")
    if not isinstance(mv, list) or not mv:
        return {"valid": False, "cells": [], "imbalance_mv": None}
    real = [v for v in mv if isinstance(v, int) and v != 0xFFFF]
    low = min(real) if real else None
    level = imbalance_level(imbal) if imbal_ok else None
    out = []
    for n, v in enumerate(mv, 1):
        blank = not isinstance(v, int) or v == 0xFFFF
        frac = 0 if blank else (v - CELL_MIN_MV) / (CELL_MAX_MV - CELL_MIN_MV)
        out.append({
            "n": n,
            "mv": None if blank else v,
            "volts": None if blank else f"{v / 1000:.3f}",
            "fraction": max(0.0, min(1.0, frac)),
            # Only the lowest cell is singled out, and only when the gap is
            # beyond the normal range -- otherwise there is nothing to see.
            "low": (not blank and v == low and level in ("slight", "balance")),
        })
    return {"valid": valid, "cells": out,
            "imbalance_mv": imbal if imbal_ok else None,
            "imbalance_level": level,
            "pack_volts": f"{pack_v:.2f}" if isinstance(pack_v, (int, float)) else None,
            "scale": {"min": CELL_MIN_MV, "max": CELL_MAX_MV}}


# --- the numbers behind the verdict ------------------------------------------

def stat(label, value, valid=True, unit=None, note=None):
    return {"label": label, "value": value if valid and value is not None else None,
            "unit": unit, "note": note, "valid": bool(valid and value is not None)}


def key_numbers(doc):
    h = doc.get("health") or {}
    cyc, cyc_ok = _field(h, "total_discharge_cycles")
    ah, ah_ok = _field(h, "total_discharge_ah")
    red, red_ok = _field(h, "charge_count_redlink")
    dumb, dumb_ok = _field(h, "charge_count_dumb")
    tot, tot_ok = _field(h, "charge_count_total")
    return [
        stat("Cycles", _num(cyc, 1), cyc_ok, note="full discharges, equivalent"),
        stat("Charges", _num(tot), tot_ok,
             note=(f"{red} on a Milwaukee charger, {dumb} other"
                   if red_ok and dumb_ok else None)),
        stat("Delivered", _num(ah), ah_ok, unit="Ah", note="over its life"),
    ]


def wear(doc):
    h = doc.get("health") or {}
    rows = [
        ("times_overheated", "Overheated"),
        ("low_voltage_events", "Ran low on voltage"),
        ("discharged_to_empty", "Run to empty"),
        ("overcurrent_events", "Overcurrent events"),
        ("low_voltage_charges", "Charged from very low"),
    ]
    out = []
    for key, label in rows:
        v, ok = _field(h, key)
        out.append(stat(label, _num(v), ok, unit="time" if v == 1 else "times"))
    return out


def details(doc):
    h = doc.get("health") or {}
    built, built_ok = _field(h, "manufacture_date")
    last_chg, lc_ok = _field(h, "days_since_last_charge")
    last_use, lu_ok = _field(h, "days_since_last_tool_use")
    chg_time, ct_ok = _field(h, "total_charge_time")
    temp, t_ok = _field(h, "temperature_c")
    return [
        stat("Built", _long_date(built), built_ok),
        stat("Last charged", _days_ago(last_chg), lc_ok),
        stat("Last used", _days_ago(last_use), lu_ok),
        stat("Time on charger", _hours(chg_time), ct_ok),
        stat("Temperature", f"{temp:.0f} °C" if isinstance(temp, (int, float)) else None, t_ok),
    ]


def _days_ago(d):
    if not isinstance(d, int) or d < 0:
        return None
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d:,} days ago"


def tiles(doc):
    """The six numbers under the verdict, in the mockup's order and units."""
    h = doc.get("health") or {}
    cyc, cyc_ok = _field(h, "total_discharge_cycles")
    tot, tot_ok = _field(h, "charge_count_total")
    temp, t_ok = _field(h, "temperature_c")
    hot, hot_ok = _field(h, "times_overheated")
    flat, flat_ok = _field(h, "discharged_to_empty")
    built, b_ok = _field(h, "manufacture_date")
    d = _date(built)
    return [
        stat("Full cycles", _num(cyc, 1), cyc_ok),
        stat("Charges", _num(tot), tot_ok),
        stat("Temp °C", _num(temp, 1), t_ok),
        stat("Overheated", _num(hot), hot_ok),
        stat("Ran flat", _num(flat), flat_ok),
        stat("Built", d.strftime("%Y-%m-%d") if d else None, b_ok),
    ]


# --- how hard it's been worked -------------------------------------------------

def usage(doc):
    """Time spent at each current, from the 10-200 A histogram.

    Registers 44-63 are populated on both firmware generations, which is why
    this chart is built on them and not on the finer RAM-block histogram.
    """
    h = doc.get("health") or {}
    buckets = h.get("discharge_buckets") or []
    total, total_ok = _field(h, "tool_time_s")
    bars = []
    for b in buckets:
        lo = int(b["range"].split("-")[0].strip("> A"))
        bars.append({
            "range": b["range"].replace("> 200A", "200A+"),
            "from_amps": lo,
            "seconds": b["seconds"] if b.get("valid") else None,
            "hard": lo >= HARD_AMPS,
            "valid": bool(b.get("valid")),
        })
    hard = sum(b["seconds"] or 0 for b in bars if b["hard"])
    if not (total_ok and total):
        hard_text = None
    elif not hard:
        hard_text = "None of it at 110 A or more."
    elif 100 * hard / total < 1:
        hard_text = "Less than 1% of it at 110 A or more."
    else:
        hard_text = f"{round(100 * hard / total)}% of it at 110 A or more."
    return {
        "bars": bars,
        "total": _hours(_hms(total)) if total_ok else None,
        "hard_text": hard_text,
        "valid": total_ok,
    }


def _hms(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


# --- every register -------------------------------------------------------------

def chip(rec):
    state, label = rec["state"], rec["label"]
    if rec.get("error") == dump.NOT_ATTEMPTED:
        return NOT_READ
    if state == dump.OK:
        flags = rec.get("flags") or []
        if "epoch_zero" in flags or "undefined_ascii" in flags:
            return NEVER_WRITTEN
        if "partial_sentinel" in flags:
            return BLANK
        return RECORDED
    if state == dump.SENTINEL:
        # All-ones in a health register means the record is gone. Anywhere
        # else, newer firmware simply doesn't keep that statistic.
        return BLANK if rec["id"] in HEALTH_IDS else NOT_KEPT
    if state == dump.ABSENT and "(Forge)" in label:
        return NOT_KEPT
    return UNREADABLE


def readings(doc):
    rows = []
    for r in doc.get("registers") or []:
        c = chip(r)
        rows.append({
            "id": r["id"], "addr": r["addr"], "label": " ".join(r["label"].split()),
            "value": r["text"] if c in (RECORDED, BLANK) and r["text"] != "------" else None,
            "chip": c, "raw": r.get("raw"),
        })
    counts = {k: 0 for k in CHIP_ORDER}
    for row in rows:
        counts[row["chip"]] += 1
    return {"rows": rows, "counts": [{"chip": k, "n": n} for k, n in counts.items() if n],
            "total": len(rows)}


# --- the whole screen ------------------------------------------------------------

def identity(doc):
    h = doc.get("health") or {}
    desc, _ = _field(h, "battery_description")
    return {
        "sticker": doc.get("case_serial") or None,
        "name": doc.get("custom_name") or None,   # the user's own name, when the sticker is worn off
        "serial": doc.get("e_serial"),
        "type": doc.get("battery_type"),
        "model": desc,
        "captured": _long_date(doc.get("captured_at")),
    }


def history(captures, e_serial):
    """Imbalance across every earlier scan of the same pack, oldest first.

    Drift over time is the weak-cell test: a pack balanced by hand that
    drifts back has a weak cell.
    """
    points = []
    for doc in captures:
        if doc.get("e_serial") != e_serial:
            continue
        mv, ok = _field(doc.get("health"), "cell_imbalance_mv")
        when = _date(doc.get("captured_at"))
        if ok and when and all(p["when"] != when.isoformat() for p in points):
            points.append({"when": when.isoformat(), "date": _long_date(when), "mv": mv})
    points.sort(key=lambda p: p["when"])
    return points


def view(doc, earlier=()):
    """Everything one battery's screen needs."""
    v = verdict(doc)
    readable = v["result"] != runner.DROPPED
    return {
        "verdict": v,
        "identity": identity(doc),
        "cells": cells(doc) if readable else None,
        "numbers": key_numbers(doc) if readable else [],
        "tiles": tiles(doc) if readable else [],
        "wear": wear(doc) if readable else [],
        "details": details(doc) if readable else [],
        "usage": usage(doc) if readable else None,
        "history": history(list(earlier) + [doc], doc.get("e_serial")) if readable else [],
        "readings": readings(doc),
    }
