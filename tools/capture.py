"""Hand-scan helper: capture one pack in a single process.

Opens the port ONCE and does everything inside it, so TX never reverts to
idle-high between commands with a pack attached (see landmine 7).

    .venv/bin/python tools/capture.py --case-serial 1234567 --note "wifi logo"

Writes, all from one sample of the pack:
    data/captures/pack_<e-serial>_full.json  -- all 184 registers (Phase 2)
    data/captures/pack_<e-serial>_health.txt -- health() as a human reads it
    data/captures/pack_<e-serial>_ss.txt     -- the raw spreadsheet column

Falls back loudly: any failure is printed and recorded, never swallowed.
For a batch of packs use tools/holder.py instead -- this opens and closes
the port each run, and closing it with a pack attached costs a dumb charge.
"""
import argparse, contextlib, io, json, pathlib, sys, traceback
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dump
from m18 import M18

DEFAULT_PORT = "/dev/cu.usbserial-A94AM9KK"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "captures"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--case-serial", default="", help="serial printed on the case")
    ap.add_argument("--note", default="", help="free text, e.g. logo present, condition")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    m = M18(args.port)  # __init__ calls idle()

    # 1. sync probe first -- this is the Phase 3 triage branch
    try:
        synced = m.reset()
    except Exception as e:
        synced = False
        print(f"reset() raised {type(e).__name__}: {e}")
    print(f"sync: {'OK' if synced else 'NO RESPONSE'}")
    if not synced:
        print("Pack did not answer. If it has the wifi logo, reseat and retry;")
        print("if it does not, this is expected and there is nothing to capture.")
        m.idle()
        return 1

    # 2. every register, once. Everything below is derived from this one
    # sample, so the .json and the .txt files can never disagree.
    eser, data, doc = "unknown", None, None
    try:
        records = dump.dump_registers(m)
        doc = dump.build_document(m, records, args.port, args.case_serial, args.note)
        data = doc.get("health")
        if doc.get("e_serial"):
            eser = str(doc["e_serial"])
    except Exception:
        traceback.print_exc()

    # 3. rendered health + raw register dump, captured without a second open
    def grab(fn):
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn()
        except Exception:
            buf.write("\n" + traceback.format_exc())
        return buf.getvalue()

    # health() and read_id() would each re-read the pack, and cell ADC drifts
    # about 5 mV between reads -- the rendered text would then describe a
    # different sample than the JSON. Pin both to the dump we already have.
    if data is not None:
        m.health_data = lambda force_refresh=True, _d=data: _d
    if doc is not None:
        m.read_id = lambda *a, _r=records, **k: dump.to_array(_r)
    health_txt = grab(lambda: m.health(force_refresh=False))
    ss_txt = "\n".join(r["text"] for r in records) if doc is not None else ""
    m.idle()

    base = OUT / f"pack_{eser}"
    (base.with_name(base.name + "_health.txt")).write_text(health_txt)
    (base.with_name(base.name + "_ss.txt")).write_text(ss_txt)
    if doc is not None:
        dump.write_document(doc, OUT)

    meta = {"captured": stamp, "e_serial": eser,
            "case_serial": args.case_serial, "note": args.note}
    with (OUT / "index.jsonl").open("a") as f:
        f.write(json.dumps(meta) + "\n")

    print(health_txt)
    if doc is not None:
        print(dump.summarise(doc))
    print(f"--- saved pack_{eser}_{{health,ss}}.txt + _full.json ---")
    print(f"--- meta: {meta} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
