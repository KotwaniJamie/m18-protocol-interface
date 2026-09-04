"""Holds the serial port open with TX low, and reads on command.

The whole point: the port is opened ONCE and break condition (TX low, J2 low)
is asserted the entire time except during an actual read. Packs are connected
and disconnected while this is running, so the line is never sitting high with
a battery attached. See landmine 1 and 7 in CLAUDE.md.

Commands arrive as a one-line file at CMD; results append to LOG.
    count            -- read charge counters only, report them
    capture <serial> -- full capture via the same path tools/capture.py uses
    quit             -- restore idle, close, exit

Nothing here exits the process on error. A bad pack must not end a batch run.
"""
import json, pathlib, sys, time, traceback
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from m18 import M18

PORT = "/dev/cu.usbserial-A94AM9KK"
SCRATCH = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / ".holder"
CMD, LOG = SCRATCH / "cmd", SCRATCH / "log"


def say(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def counters(m):
    d = m.health_data()
    g = lambda k: d.get(k, {}).get("value")
    return (f"redlink={g('charge_count_redlink')} dumb={g('charge_count_dumb')} "
            f"total={g('charge_count_total')} imbal={g('cell_imbalance_mv')}mV "
            f"eserial={g('e_serial')}"), d


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    CMD.unlink(missing_ok=True)
    m = M18(PORT)          # __init__ asserts idle()
    m.idle()               # belt and braces: TX low from here on
    say(f"holder up on {PORT}. TX held LOW. waiting for commands.")

    try:
        while True:
            if not CMD.exists():
                time.sleep(0.15)
                continue
            try:
                cmd = CMD.read_text().strip()
            except OSError:
                continue
            CMD.unlink(missing_ok=True)
            if not cmd:
                continue
            say(f"CMD: {cmd}")

            try:
                if cmd == "quit":
                    say("quit: restoring idle and closing.")
                    break

                if not m.reset():
                    say("RESULT: no response -- pack not connected, or no wifi logo.")
                    m.idle()
                    continue

                if cmd == "count":
                    txt, _ = counters(m)
                    say(f"RESULT: {txt}")

                elif cmd.startswith("capture"):
                    case = cmd[len("capture"):].strip()
                    txt, d = counters(m)
                    es = str(d.get("e_serial", {}).get("value", "unknown"))
                    out = HERE.parent / "data" / "captures"
                    out.mkdir(parents=True, exist_ok=True)
                    (out / f"pack_{es}.json").write_text(
                        json.dumps(d, indent=2, default=str))
                    with (out / "index.jsonl").open("a") as f:
                        f.write(json.dumps({"captured": datetime.now().isoformat(
                            timespec="seconds"), "e_serial": es,
                            "case_serial": case, "note": "via holder.py"}) + "\n")
                    say(f"RESULT: {txt} -- saved pack_{es}.json")
                else:
                    say(f"RESULT: unknown command {cmd!r}")

            except Exception:
                say("ERROR:\n" + traceback.format_exc())
            finally:
                m.idle()   # ALWAYS return the line low before waiting again
    finally:
        m.idle()
        say("TX low. NOTE: closing the port may release it -- "
            "make sure no pack is attached now.")
        time.sleep(0.5)
        m.port.close()
        say("holder down.")


if __name__ == "__main__":
    main()
