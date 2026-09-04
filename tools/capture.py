"""Phase 0 hand-scan helper: capture one pack in a single process.

Opens the port ONCE and does everything inside it, so TX never reverts to
idle-high between commands with a pack attached (see landmine 7).

    .venv/bin/python tools/capture.py --case-serial 1234567 --note "wifi logo"

Writes data/captures/pack_<e-serial>_{health,ss}.txt and a meta line.
Falls back loudly: any failure is printed and recorded, never swallowed.
"""
import argparse, contextlib, io, json, pathlib, sys, traceback
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
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

    # 2. structured data, for the e-serial and for later use
    eser, data = "unknown", None
    try:
        data = m.health_data()
        v = data.get("e_serial", {}).get("value")
        if v:
            eser = str(v)
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

    # health() calls health_data() itself; let it re-read and the rendered text
    # would come from a DIFFERENT sample than the JSON (cell ADC drifts ~5mV
    # between reads). Pin it to the dict we already captured so the .json and
    # the .txt describe one single measurement.
    if data is not None:
        m.health_data = lambda force_refresh=True, _d=data: _d
    health_txt = grab(lambda: m.health(force_refresh=False))
    ss_txt = grab(lambda: m.read_id(force_refresh=False, output="raw"))
    m.idle()

    base = OUT / f"pack_{eser}"
    (base.with_name(base.name + "_health.txt")).write_text(health_txt)
    (base.with_name(base.name + "_ss.txt")).write_text(ss_txt)
    if data is not None:
        (base.with_name(base.name + ".json")).write_text(
            json.dumps(data, indent=2, default=str))

    meta = {"captured": stamp, "e_serial": eser,
            "case_serial": args.case_serial, "note": args.note}
    with (OUT / "index.jsonl").open("a") as f:
        f.write(json.dumps(meta) + "\n")

    print(health_txt)
    print(f"--- saved pack_{eser}_{{health,ss}}.txt + .json ---")
    print(f"--- meta: {meta} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
