"""Phase 2: read every register in `data_id` and return it as structured data.

`health()` reports on 41 registers. `data_id` has 184. Re-handling forty
batteries to fetch the other 143 is expensive; storing them now is free.

The contract that matters here is that **"the register did not answer" and
"the register answered with a sentinel" are different states and must never
collapse to null.** `read_id()` renders both as `------`, and its "array"
mode returns `None` for both. A dead pack whose BMS talks but reports
0xFFFF everywhere would be indistinguishable from a healthy pack whose
Forge-only registers are simply absent. So each register gets an explicit
`state`, and the raw payload bytes are kept alongside the decoded value --
if the decode is ever found to be wrong, the bytes are still on disk and
nobody has to touch the pile again.

Used as a library by tools/holder.py, or directly:

    .venv/bin/python tools/dump.py --case-serial 'G29JDCBC 180720 3894605'

Nothing here raises on a bad register. One unreadable address must not
cost the other 183.
"""
import argparse
import datetime
import json
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import m18 as m18mod
from m18 import M18, data_id, data_matrix

SCHEMA = "m18-register-dump/1"
DEFAULT_PORT = "/dev/cu.usbserial-A94AM9KK"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "captures"

# States a register can be in. Deliberately more than read_id's two.
OK = "ok"                    # answered, decoded, plausible
SENTINEL = "sentinel"        # answered with all-ones -- BMS is talking, data is not real
ABSENT = "absent"            # no answer, or an answer that was not a 0x81 frame
TRUNCATED = "truncated"      # 0x81 frame, but fewer payload bytes than the length asked for
UNDECODABLE = "undecodable"  # bytes arrived and the decoder refused them


def hexs(b):
    return " ".join(f"{x:02X}" for x in b) if b else ""


def decode(m, data, data_type):
    """Decode a payload exactly as read_id() does, and describe it.

    Returns (value, text, flags). `value` is JSON-friendly except for dates,
    which stay as datetime so callers can do arithmetic; json.dumps needs
    default=str, same as health_data(). Raises on undecodable input -- the
    caller turns that into UNDECODABLE rather than losing the bytes.
    """
    flags = []
    match data_type:
        case "uint":
            v = int.from_bytes(data, "big")
            return v, str(v), flags

        case "date":
            dt = m.bytes2dt(data)
            if dt.timestamp() == 0:
                # Never written. Not a sentinel and not a date -- health_data()
                # treats epoch zero as missing, and so should anything else.
                flags.append("epoch_zero")
            return dt, dt.strftime("%Y-%m-%d %H:%M:%S"), flags

        case "hhmmss":
            dur = int.from_bytes(data, "big")
            mm, ss = divmod(dur, 60)
            hh, mm = divmod(mm, 60)
            return f"{hh}:{mm:02d}:{ss:02d}", f"{hh}:{mm:02d}:{ss:02d}", flags

        case "ascii":
            s = data.decode("utf-8")            # may raise; caller catches
            stripped = s.strip().strip("-").strip("\x00").strip()
            if not stripped or s.startswith("Undefined"):
                # The reference pack reads "Undefined-----------" here. The
                # field answered; it just has nothing in it.
                flags.append("undefined_ascii")
            return s, f'"{s}"', flags

        case "sn":
            btype = int.from_bytes(data[0:2], "big")
            serial = int.from_bytes(data[2:5], "big")
            return ({"battery_type": btype, "e_serial": serial},
                    f"Type: {btype:3d}, Serial: {serial:d}", flags)

        case "adc_t":
            adc = int.from_bytes(data, "big")
            v = m.calculate_temperature(adc)
            return v, f"{v}", flags

        case "dec_t":
            v = data[0] + data[1] / 256
            return round(v, 2), f"{v:.2f}", flags

        case "cell_v":
            cv = [int.from_bytes(data[i:i + 2], "big") for i in range(0, 10, 2)]
            if any(c == 0xFFFF for c in cv):
                # Some cells real, some all-ones. Whole-register sentinel
                # detection misses this, and averaging it produces nonsense.
                flags.append("partial_sentinel")
            return cv, ", ".join(f"{n}: {c:4d}" for n, c in enumerate(cv, 1)), flags

    raise ValueError(f"unknown data_type {data_type!r}")


def read_register(m, i):
    """Read data_id[i] and classify the result. Never raises."""
    addr, length, data_type, label = data_id[i]
    rec = {"id": i, "addr": f"0x{addr:04X}", "length": length,
           "type": data_type, "label": label,
           "state": ABSENT, "raw": None, "value": None, "text": "------",
           "flags": []}

    try:
        response = m.cmd((addr >> 8) & 0xFF, addr & 0xFF, length, length + 5)
    except Exception as e:
        # cmd() raises ValueError("Empty response") on a zero-byte read.
        # That is the no-answer case, not an error worth stopping for.
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    if not (response and len(response) >= 4 and response[0] == 0x81):
        rec["response_raw"] = hexs(response)
        return rec

    data = bytes(response[3:3 + length])
    rec["raw"] = hexs(data)

    if len(data) != length:
        rec["state"] = TRUNCATED
        rec["text"] = f"<{len(data)} of {length} bytes>"
        return rec

    if length and all(b == 0xFF for b in data):
        # All-ones. The BMS answered; the answer means nothing.
        rec["state"] = SENTINEL
        rec["text"] = "<sentinel 0xFF>"
        return rec

    try:
        value, text, flags = decode(m, data, data_type)
    except Exception as e:
        rec["state"] = UNDECODABLE
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    rec["state"] = OK
    rec["value"] = value
    rec["text"] = text
    rec["flags"] = flags
    return rec


def refresh(m):
    """Dummy-read the data_matrix list to repopulate the 0x9000 RAM block."""
    for addr_h, addr_l, length in data_matrix:
        try:
            m.cmd(addr_h, addr_l, length, length + 5)
        except Exception:
            pass          # a stale chunk is better than an aborted dump
    m.idle()
    time.sleep(0.1)


def dump_registers(m, force_refresh=True):
    """Every register in data_id, in order. One pass, one sample."""
    m.reset()
    if force_refresh:
        refresh(m)
    m.reset()
    recs = [read_register(m, i) for i in range(len(data_id))]
    m.idle()
    return recs


# read_id(output="array") does not hand back the decoded value for every
# type -- for these four it hands back its own rendered string, and
# health_data() parses that string back out again (see `serial_raw`). The
# archive keeps the structured form; the shim below has to keep the string,
# or health_data() silently reads None for the e-serial.
AS_TEXT = ("ascii", "sn", "dec_t", "hhmmss")


def as_read_id_value(rec):
    """One record in the shape read_id(output="array") would have produced."""
    if rec["state"] != OK:
        return None
    return rec["text"] if rec["type"] in AS_TEXT else rec["value"]


def to_array(records):
    """The dump in read_id(output="array") shape, so health_data() can eat it.

    Only OK registers carry a value; everything else is None, which is what
    health_data() already expects for a register that did not answer. The
    distinction we worked to preserve lives in the dump, not here -- this is
    a compatibility shim, not the archive.
    """
    return [[r["id"], as_read_id_value(r)] for r in records]


def health_from(m, records):
    """health_data() computed from `records` -- no second read of the pack.

    Cell ADC drifts about 5 mV between reads, so letting health_data() do its
    own read would put two different measurements in one file.
    """
    had_own = "read_id" in vars(m)
    original = vars(m).get("read_id")
    m.read_id = lambda *a, **k: to_array(records)
    try:
        return m.health_data(force_refresh=False)
    finally:
        # Restore by deleting rather than reassigning, or the bound method
        # gets pinned to the instance and shadows the class for good.
        if had_own:
            m.read_id = original
        else:
            del m.read_id


def identity(records):
    """Battery type and e-serial from the 'sn' register, if it answered."""
    for r in records:
        if r["type"] == "sn" and r["state"] == OK:
            return r["value"]["battery_type"], r["value"]["e_serial"]
    return None, None


def build_document(m, records, port="", case_serial="", note=""):
    counts = {}
    for r in records:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    bat_type, e_serial = identity(records)

    try:
        health = health_from(m, records)
    except Exception:
        health = None
        traceback.print_exc()

    return {
        "schema": SCHEMA,
        "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "port": port,
        "case_serial": case_serial,
        "note": note,
        "battery_type": bat_type,
        "e_serial": e_serial,
        "register_count": len(records),
        "state_counts": counts,
        # health() below is a dict of all-invalid fields when nothing
        # answered, which reads like a measurement unless you check every
        # "valid". This says it in one place instead.
        "answered": counts.get(OK, 0) > 0,
        "flagged": [{"id": r["id"], "label": r["label"], "flags": r["flags"]}
                    for r in records if r["flags"]],
        "health": health,
        "registers": records,
    }


def write_document(doc, out_dir=OUT):
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"pack_{doc.get('e_serial') or 'unknown'}_full.json"
    path = out_dir / name
    path.write_text(json.dumps(doc, indent=2, default=str))
    return path


def summarise(doc):
    c = doc["state_counts"]
    parts = [f"{c.get(k, 0)} {k}" for k in (OK, SENTINEL, ABSENT, TRUNCATED, UNDECODABLE)
             if c.get(k)]
    return (f"e-serial {doc.get('e_serial')} type {doc.get('battery_type')}: "
            f"{doc['register_count']} registers -- " + ", ".join(parts))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--case-serial", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    m = M18(args.port)        # __init__ asserts idle()
    try:
        if not m.reset():
            print("No response. Pack not connected, or it has no wifi logo.")
            return 1
        t0 = time.time()
        records = dump_registers(m)
        doc = build_document(m, records, args.port, args.case_serial, args.note)
        path = write_document(doc)
        print(summarise(doc))
        print(f"{time.time() - t0:.1f}s -- wrote {path}")
        return 0
    finally:
        m.idle()
        print("TX low. Detach the pack BEFORE this process exits (landmine 7).")
        time.sleep(0.5)
        m.port.close()


if __name__ == "__main__":
    sys.exit(main())
