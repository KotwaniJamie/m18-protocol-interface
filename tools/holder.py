"""Holds the serial port open with TX low, and reads on command.

The whole point: the port is opened ONCE and break condition (TX low, J2 low)
is asserted the entire time except during an actual read. Packs are connected
and disconnected while this is running, so the line is never sitting high with
a battery attached. See landmine 1 and 7 in CLAUDE.md.

Commands arrive as a one-line file at CMD; results append to LOG.
    count            -- read charge counters only, report them (cheap, ~5s)
    capture <serial> -- all 184 registers to JSON via tools/dump.py (~1 min)
    latency [n]      -- time the sync reply over n stock handshakes (default 20)
    probes [n]       -- n runner.probe() knocks, 1 s apart (step 2 of Phase 3)
    quit             -- restore idle, close, exit

Nothing here exits the process on error. A bad pack must not end a batch run.
"""
import json, pathlib, sys, time, traceback
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dump
import runner
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


def latency(m, n):
    """Time the sync reply using exactly M18.reset()'s sequence and timeout.

    The runner's short-timeout probe could not see a pack that reset() sees
    (2026-09-28). This measures how long the reply really takes. Also reports
    how long the line was high each time: 0.3 s pre-sync plus the wait.
    """
    import struct
    rows = []
    for _ in range(n):
        port = m.port
        port.break_condition = True
        port.dtr = True
        time.sleep(0.3)
        port.break_condition = False
        port.dtr = False
        high_from = time.monotonic()
        time.sleep(0.3)
        m.send(struct.pack(">B", m.SYNC_BYTE))
        sent = time.monotonic()
        b = port.read(1)                          # port timeout, 0.8 s, as reset() uses
        got = time.monotonic()
        m.idle()
        low_at = time.monotonic()
        ok = bool(b) and m.reverse_bits(b[0]) == m.SYNC_BYTE
        rows.append((ok, got - sent, low_at - high_from))
        time.sleep(0.5)
    lat = sorted(r[1] for r in rows if r[0])
    high = sorted(r[2] for r in rows)
    ms = lambda s: f"{s * 1000:.0f}"
    if not lat:
        return f"no replies in {n} handshakes"
    return (f"{len(lat)}/{n} replied. reply latency ms min {ms(lat[0])} "
            f"median {ms(lat[len(lat)//2])} max {ms(lat[-1])}. "
            f"line high ms min {ms(high[0])} median {ms(high[len(high)//2])} max {ms(high[-1])}")


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

                if cmd.startswith("probes"):
                    parts = cmd.split()
                    n = int(parts[1]) if len(parts) > 1 else 200
                    say(f"probing {n} times, {runner.POLL_INTERVAL}s apart, "
                        f"timeout {runner.PROBE_TIMEOUT}s")
                    lat, run, worst = [], 0, 0
                    for i in range(1, n + 1):
                        l = runner.probe_latency(m)
                        if l is None:
                            run += 1
                            worst = max(worst, run)
                        else:
                            lat.append(l)
                            run = 0
                        if i % 25 == 0:
                            say(f"  {i}/{n} seen {len(lat)}")
                        time.sleep(runner.POLL_INTERVAL)
                    lat.sort()
                    ms = (f"latency ms min {lat[0]*1000:.0f} median {lat[len(lat)//2]*1000:.0f} "
                          f"max {lat[-1]*1000:.0f}" if lat else "no replies")
                    say(f"RESULT: seen {len(lat)}/{n}, longest miss run {worst}, {ms}")
                    continue

                if cmd.startswith("latency"):
                    parts = cmd.split()
                    n = int(parts[1]) if len(parts) > 1 else 20
                    say(f"RESULT: {latency(m, n)}")

                elif cmd == "count":
                    txt, _ = counters(m)
                    say(f"RESULT: {txt}")

                elif cmd.startswith("capture"):
                    case = cmd[len("capture"):].strip()
                    t0 = time.time()
                    records = dump.dump_registers(m)
                    doc = dump.build_document(m, records, PORT, case, "via holder.py")
                    path = dump.write_document(doc)
                    h = doc.get("health") or {}
                    g = lambda k: h.get(k, {}).get("value")
                    say(f"RESULT: {dump.summarise(doc)}")
                    say(f"        redlink={g('charge_count_redlink')} "
                        f"dumb={g('charge_count_dumb')} total={g('charge_count_total')} "
                        f"imbal={g('cell_imbalance_mv')}mV "
                        f"cycles={g('total_discharge_cycles')}")
                    if doc["flagged"]:
                        say(f"        flagged: {doc['flagged']}")
                    with (HERE.parent / "data" / "captures" / "index.jsonl").open("a") as f:
                        f.write(json.dumps({"captured": doc["captured_at"],
                                            "e_serial": doc["e_serial"],
                                            "case_serial": case,
                                            "note": "full dump via holder.py"}) + "\n")
                    say(f"        {time.time() - t0:.1f}s -- saved {path.name}")
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
