"""Step 2 of Phase 3: does knocking on an ATTACHED battery cost a dumb charge?

The runner's automatic removal detection probes a connected pack about once
a second until it disappears. Each probe lets the line go high for ~0.35 s --
under the 0.48 s threshold, but the counter has never been watched across
hundreds of back-to-back probes. This watches it.

    .venv/bin/python tools/poll_test.py            # 200 probes, 1 s apart
    .venv/bin/python tools/poll_test.py --probes 50

Reads the charge counters, probes N times exactly as `runner.py --auto-remove`
would, reads the counters again, and writes the result to data/captures/.
Also measures how often the probe sees a pack that is definitely there --
auto-remove treats two misses in a row as "removed", so misses matter too.

Uses the runner's own close_safely(): it will not exit with the pack attached.
"""
import argparse
import datetime
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import dump
import runner

COUNTERS = {"redlink": 33, "dumb": 32, "total": 31}
SERIAL = 2


def counters(m):
    """Just the charge counters and identity, from a fresh RAM refresh."""
    m.reset()
    dump.refresh(m)
    m.reset()
    out = {}
    for name, rid in {**COUNTERS, "serial": SERIAL}.items():
        r = dump.read_register(m, rid)
        out[name] = r["value"] if r["state"] == dump.OK else f"<{r['state']}>"
    m.idle()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=runner.DEFAULT_PORT)
    ap.add_argument("--probes", type=int, default=200)
    ap.add_argument("--gap", type=float, default=runner.POLL_INTERVAL)
    args = ap.parse_args()

    io = runner.Terminal()
    runner.install_signal_handlers()
    from m18 import M18
    m = M18(args.port)
    m.idle()
    result = None
    try:
        io.say(f"Port open, line held LOW. Plug in the test battery. "
               f"(probe timeout {runner.PROBE_TIMEOUT}s)")
        waited = 0
        while runner.probe_latency(m) is None:
            waited += 1
            if waited % 10 == 0:
                io.say(f"  no answer after {waited} knocks")
            time.sleep(0.5)
        io.say("Battery detected.")

        before = counters(m)
        io.say(f"BEFORE  {before}")
        est = args.probes * (0.6 + args.gap)
        io.say(f"Probing {args.probes} times, {args.gap}s apart (about {est/60:.1f} min). "
               "Leave the battery connected.")

        hits, misses_in_row, worst_run, lat = 0, 0, 0, []
        t0 = time.time()
        for i in range(1, args.probes + 1):
            l = runner.probe_latency(m)
            if l is not None:
                lat.append(l)
                hits += 1
                misses_in_row = 0
            else:
                misses_in_row += 1
                worst_run = max(worst_run, misses_in_row)
            if i % 20 == 0:
                io.say(f"  {i}/{args.probes}  seen {hits}/{i}")
            time.sleep(args.gap)

        after = counters(m)
        io.say(f"AFTER   {after}")

        moved = {k: (before[k], after[k]) for k in COUNTERS if before[k] != after[k]}
        result = {
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "probes": args.probes, "gap_s": args.gap,
            "probe_timeout_s": runner.PROBE_TIMEOUT,
            "seconds": round(time.time() - t0, 1),
            "seen": hits, "longest_miss_run": worst_run,
            "reply_latency_ms": ({"min": round(min(lat) * 1000, 1),
                                  "median": round(sorted(lat)[len(lat) // 2] * 1000, 1),
                                  "max": round(max(lat) * 1000, 1)} if lat else None),
            "before": before, "after": after, "moved": moved,
            "verdict": "PASS" if not moved else "FAIL",
        }
        io.say("")
        if moved:
            io.say(f"FAIL -- counters moved: {moved}. Keep Enter-to-confirm removal.")
        else:
            io.say(f"PASS -- {args.probes} probes, no counter moved.")
        if lat:
            io.say(f"Reply latency: {result['reply_latency_ms']} ms "
                   f"(timeout {runner.PROBE_TIMEOUT * 1000:.0f} ms)")
        io.say(f"Detection: saw the battery on {hits}/{args.probes} probes; "
               f"longest run of misses {worst_run}"
               + (" -- auto-remove would have falsely declared it removed."
                  if worst_run >= runner.REMOVAL_MISSES else "."))

        out = HERE.parent / "data" / "captures" / f"poll_test_{datetime.date.today()}.json"
        out.write_text(json.dumps(result, indent=2, default=str))
        io.say(f"Saved {out.name}")
    except KeyboardInterrupt:
        io.say("\nInterrupted.")
    finally:
        runner.close_safely(m, io)
    return 0 if result and result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
