# M18 Battery Health Project — Sector67 fork

Fork of [mnh-jansson/m18-protocol](https://github.com/mnh-jansson/m18-protocol).

**Goal:** batch-scan a pile of Milwaukee M18 packs at Sector67, capture the case-printed
serial alongside the on-wire diagnostics, and present each pack's health so a human can
triage it for repair, sale, or recycling.

Two delivery surfaces, one data layer:

- **A GUI** — the primary one. Someone walks up, connects a pack, and sees everything the
  battery knows about itself laid out clearly. It has to look good and be readable by
  someone who is not us; this is a makerspace tool other members will use.
- **A one-page printable PDF** per battery, for the packs that get tagged and shelved.

Both render from the same JSON. Scanning, storing, and presenting stay separate so a
report can be re-rendered without re-handling the battery.

## Upstream status (verified 2026-09-01)

- Last commit `2025-10-13`. Dormant roughly ten months.
- 951 stars, 153 forks, 13 open issues.
- **No LICENSE file.** Defaults to all-rights-reserved. Issue #11 has been open since
  Sept 2025 with no maintainer response. Forking on GitHub is fine under GitHub's ToS,
  but do not redistribute outside GitHub or relicense until this is resolved.
- **PRs #51 and #52 are a trap.** Both are titled as a Tkinter GUI. The actual diff is a
  full C# WinForms application (~8,000 lines of `.cs`, `.csproj`, `.resx`) plus a
  410-line `m18gui.py`, and it *deletes* `README.md`, all four `.bat` files, and
  `docs/Protocol.ods`. Do not build on top of these.

## Code architecture

Single file `m18.py`, ~1030 lines, one `M18` class. Dependencies: `pyserial==3.5`,
`requests`. Requires Python 3.13. Serial is 4800 baud, 2 stop bits, and bytes are
bit-reversed on the wire (see `send()` / `read_response()`).

- `data_id[]` is the register table: `[addr, len, type, label]`. Types are `uint`,
  `date`, `hhmmss`, `ascii`, `sn`, `adc_t`, `dec_t`, `cell_v`. **184 entries** (the
  "about 90" in earlier notes was wrong), many still labelled Unknown. `health()` reads
  41 of them; `tools/dump.py` reads all 184.
- `data_matrix[]` is the dummy-read list that refreshes the `0x9000` RAM block before
  real reads happen.
- **`health_data()` returns the derived values; `health()` formats and prints them.**
  Was previously all inline inside print statements and discarded — that was the central
  obstacle to this project, and is fixed on `health-data` (not yet upstream).
- `read_id(output=...)` has four modes: `"label"` (prints), `"raw"` (prints, for
  spreadsheet paste), `"array"` (returns `[[id, value], ...]`), and `"form"`.
- Registers are looked up by id through `HEALTH_REGISTERS`. This used to be **positional
  indexing** into a hand-built `reg_list`, where inserting or reordering an entry silently
  shifted every field after it.

## Landmines

1. **The dumb-charge counter.** Connecting with TX high increments register 32
   (`"Dumb charge count (J2>7.1V for >=0.48s)"`) permanently. `m18_idle.bat` exists
   solely because of this. Any batch loop must guarantee idle state before every
   connection, or scanning the pile permanently corrupts the data we are collecting.
   This is the highest-consequence mistake available in this project.

2. **Dead packs return garbage, not errors.** Sentinel values `0xFFFF` and `0xFFFFFFFF`
   render as 65535 and 4294967295. Expect negative day counts and charge times like
   `1193046:28:15` (that is 2³² seconds). See issue #28 for a real example. Reports must
   detect and flag these, never present them as measurements.

3. **Division by zero on an unused pack.** Fixed on `fix-divzero` (issue #47). There were
   two sites, not one: the 10-200A loop and the `>200A` tail. The crash was swallowed by
   the broad `except` in `health()`, so it surfaced as "Check battery is connected" —
   it looked like an adapter fault.

4. **The e-serial does not match the case serial.** The code states this explicitly.
   There is no way to read the case-printed serial over the wire; it must be captured
   out of band and correlated once.

5. **Adapter compatibility is unresolved upstream.** Issue #7 (50 comments) covers
   CP2102 problems. Counterfeit FT232 chips do not support break condition and emulate it
   via DTR. Issue #61 (June 2026) is someone still stuck. **Not a problem for us** — our
   adapter is verified genuine and working (see Hardware below). Keep this in mind only
   when reading upstream issues, where it is the single most common cause of failure.

6. **Python version.** `bytes2dt()` uses `datetime.UTC`, which needs 3.11+. `.python-version`
   pins 3.13. Issue #49 is someone hitting this.

7. **TX reverts to idle-high when the port closes.** ✅ Answered 2026-09-04 by behaviour,
   not by multimeter: pack 4769294 incremented its dumb count across the gap between two
   separate single-shot commands, while pack 6278308 saw three connections and two reads
   under one held-open process and did not move. So the FTDI chip does *not* hold the line
   low after the host releases it, and `--idle`-then-connect protects nothing.
   Consequence: **never run stock single-shot `--health` / `--ss` with a pack attached.**
   One process holds the port for the whole session. See "The dumb-charge counter" below
   for the full measurement.

## Plan

- **Phase 0** — ✅ Done 2026-09-04. Adapter identified and verified genuine, full read
  chain confirmed against a real pack. See Hardware below. The README's `m.high()` J1/J2
  voltage check was deliberately **skipped**: a successful `--health` read exercises the
  same path (J2 drive + J1 receive) while staying under the 0.48s dumb-charge threshold,
  whereas `m.high()` holds TX high indefinitely and guarantees a register-32 increment.
  Seven logo packs hand-scanned with full `--health` + `--ss` captures, plus one no-logo
  pack characterised. No dead pack was available, so the sentinel path is still untested
  against hardware.
- **Phase 1** — ✅ Done, on `health-data`. `health_data()` returns 30 fields as
  `{"value", "valid"}`; `health()` renders them. Validity propagates — a value derived
  from a sentinel is never marked more trustworthy than the register it came from.
  Output verified byte-identical across 1000 randomised packs. Date fields are
  `datetime` objects, so JSON needs `default=str`.
- **Phase 2** — ✅ Done 2026-09-04, `tools/dump.py`. Archive complete 2026-09-28: all
  seven packs captured in full, `data/captures/pack_<e-serial>_full.json`. All **184** registers (not ~90 —
  `data_id` has 184 entries) to JSON per battery, each with an explicit state and its raw
  payload bytes. 24s per pack on hardware. `to_array()` reproduces
  `read_id(output="array")` exactly, so `health_data()` runs off the same sample instead
  of re-reading; a test pins that equivalence across all eight data types. Verified
  against two packs of different firmware generations — see "What the full dump found".
- **Phase 3** — Batch runner. 🔨 **Built 2026-09-28, `tools/runner.py`; hardware test
  pending.** See "Phase 3: the batch runner" below for design, status, and the remaining
  step.
- **Phase 4** — **The GUI.** Connect a pack, see its health. Reads the Phase 2 JSON, or
  drives Phase 3 live. Must show wear honestly, flag sentinels rather than printing them
  as numbers, and never surface `health()`'s one-size-fits-all error string. Design work,
  not just plumbing — see the Goal.
- **Phase 5** — One-page printable PDF from the same JSON, for tagging and shelving.

## Hardware (verified 2026-09-04)

- **Adapter** — FT232R USB UART, `VID:PID=0403:6001`, `SER=A94AM9KK`, bcdDevice 6.00,
  manufacturer FTDI. Consistent with a genuine part, and confirmed genuine and wired to
  3.3V by hand. Built by Scott Hasse, who tested it on his own packs.
- **Port on this Mac** — `/dev/cu.usbserial-A94AM9KK`. Use `cu.*`, never `tty.*`; the
  latter blocks waiting on carrier detect.
- **Wiring** — TXD→J2, RXD→J1, GND→B−, per `docs/wiring.png`.
- **Working invocation** —
  `.venv/bin/python m18.py --port /dev/cu.usbserial-A94AM9KK --health`

### Reference pack

E-serial 4769294 is the known-good control: type 38 (3Ah XC, 5s2p 18650), built
2018-08-28, 86 charges, 33.16 equivalent cycles, 15 mV imbalance, 0 overcurrent events.
Full 190-value `--ss` dump captured. **Zero sentinels anywhere** in that dump, which is
what makes it a clean baseline — compare suspect packs against it.

Two findings from that first read, both of which affect later phases:

- **Read noise is ±5 mV on cells and ±0.1 °C on temperature.** Back-to-back `--health`
  and `--ss` runs seconds apart disagreed: cell 4 read 3900 then 3905, temperature 23.62
  then 23.71. This is live ADC, so drift is expected — but the reference pack's headline
  imbalance is 15 mV, only three times the noise floor. Any imbalance threshold must sit
  well clear of ±5 mV or the pile gets sorted by measurement noise. Reinforces the
  existing rule: scan first, set cutoffs from the real distribution.
- **13 registers return nothing at all** on a fully healthy pack, plus one ASCII field
  reading `"Undefined-----------"`. These are *not* sentinels — they are genuinely
  unreadable. This is the exact case where the old code raised mid-report and
  `health_data()` continues with `None`. That defensive path is not theoretical; it fires
  on the first real battery. Phase 2 records absent and sentinel as **distinct** states
  and never collapses both to null. Since resolved: **all 13 are labelled "(Forge)"** —
  they are Forge-only fields on a non-Forge pack, not a fault. See below.

Also note `Time idling on charger: 7746:09:12` (322 days) on the reference pack is a
real accumulated value, not a sentinel. Reports must not flag it.

### Packs without the wifi logo do not respond — confirmed 2026-09-04

A pack with no wifi-like logo by the serial returns **nothing**. Not garbage, not a
malformed frame — zero bytes. Five consecutive `reset()` sync attempts all returned
False, `in_waiting` stayed 0 after settling, and `reset()` failed via the
`except ValueError` path in `read_response()` ("Empty response" on a zero-byte read),
never reaching the `Unexpected response:` branch. Wiring was untouched from the
successful read of the reference pack. Corroborated by Scott's independent testing on
his own packs.

This is a hard gate. No diagnostic radio is listening and nothing in software works
around it. Sorting the pile by logo before scanning is not an optimisation, it is the
first triage step. Transcript: `data/captures/no_wifi_logo_no_response.txt`.

**Phase 3 must call `reset()` first and branch on it**, rather than diving into
`read_id()` and interpreting the wreckage afterwards:

| Signature | Meaning |
| --- | --- |
| `reset()` False, `in_waiting == 0` | no diagnostic support (or bad connection) |
| `reset()` True, sentinels in the **health** registers | dead pack — BMS talks, data is garbage |
| `reset()` True, clean values | good pack |

Sentinels *outside* the health registers are normal on newer firmware and mean nothing
about pack condition — see "What the full dump found". Note the first row is still two
states sharing one signature. Since a no-logo pack is
identified visually before it is ever connected, the runner should record the logo
observation as operator input, and then treat silence from a pack that *has* a logo as a
connection fault worth retrying.

**`health()`'s error handling is unusable for batch work.** The no-logo failure printed
`Check battery is connected and you have correct serial port` — the same message
landmine 3's division-by-zero produced. The broad `except` flattens every distinct
failure into one misleading sentence. Across 40 packs that sends you chasing adapter
faults that do not exist. Phase 3 needs its own error path; do not reuse `health()`'s.

## What the full dump found — all seven packs (2026-09-04, completed 2026-09-28)

Every pack read end to end with `tools/dump.py`. 24-25 seconds each, so all 40 is about
17 minutes of wire time.

| e-serial | type | ok | sentinel | absent | imbal | redlink | dumb | total | cycles |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5133886 | 40 | 171 | 0 | 13 | 2 mV | 75 | 6 | 81 | 34.34 |
| 4769294 | 38 | 171 | 0 | 13 | 11 mV | 82 | 5 | 87 | 33.16 |
| 4769289 | 38 | 171 | 0 | 13 | 11 mV | 96 | 4 | 100 | 31.92 |
| 4825661 | 40 | 171 | 0 | 13 | 9 mV | 88 | 9 | 97 | 31.41 |
| 5133836 | 40 | 171 | 0 | 13 | 11 mV | 52 | 4 | 56 | 18.56 |
| 6278308 | 424 | **89** | **91** | **4** | 22 mV | 19 | 2 | 21 | 4.33 |
| 5950263 | 38 | 171 | 0 | 13 | 67 mV | 5 | 3 | 8 | 2.14 |

**The register-state split is exactly by firmware generation, with zero variance inside a
generation.** All six 2018-2020 packs (types 38 and 40) read 171/0/13. The one 2024 pack
(type 424) reads 89/91/4. Nothing about pack condition, age within a generation, or use
changes those numbers. That makes state counts a reliable generation fingerprint and a
cheap sanity check on a capture.

Derived health values matched the September `health()`-only reads apart from the battery's
own clock, and no dumb-charge counter moved during any holder-driven capture.

### Sentinels do not mean a dead pack

This is the correction that matters. **91 of 184 registers read all-ones on a perfectly
healthy 2024 pack.** They are contiguous, all in the `0x9000`/`0x9100` RAM block from
`0x909C` up: the high-current histogram tail (150A through 200A+) and the whole
charge-started / charge-ended voltage histogram. All six 2018-2020 packs (types 38 and 40)
populate every one of them; the single type-424 pack populates none.

So the Phase 3 triage table's `reset()` True + sentinels ⇒ dead pack **only holds for the
41 health registers**. Applied to the whole table it condemns every modern pack in the
pile. Scope it, and treat a sentinel in the RAM block as "this firmware does not keep that
statistic".

The trade runs both ways. The type-424 pack answers **nine Forge registers the type-38
packs leave absent** (`0x0015`, `0x0019`, `0x4000`, `0x4016`, `0x401B`, `0x6000`, `0x6002`,
`0x6004`, `0x6008`). Newer firmware implements more of the Forge address space and less of
the RAM histogram.

### Re-scan after 24 days: the noise band holds, and 67 mV is not noise

Seven packs re-read on 2026-09-28 against their 2026-09-04 baselines. Cycles and redlink
counts were unchanged on all seven — none had been used or properly charged in between.
Imbalance moved as follows:

| pack | Sep 04 | Sep 28 | delta |
| --- | --- | --- | --- |
| 5133886 | 5 mV | 2 mV | −3 |
| 4825661 | 7 mV | 9 mV | +2 |
| 4769289 | 7 mV | 11 mV | +4 |
| 4769294 | 11 mV | 11 mV | 0 |
| 5133836 | 11 mV | 11 mV | 0 |
| 6278308 | 22 mV | 22 mV | 0 |
| 5950263 | **67 mV** | **67 mV** | **0** |

Every movement is inside the ±5 mV read noise established on the reference pack, which
confirms that figure independently and across 24 days rather than across seconds. And
**5950263 did not drift at all** — its 67 mV is a stable, real measurement, not a noisy
one, and idle time alone neither widened nor healed it.

That still does not say whether it is repairable. The balancing test has not been run: its
charge count is unchanged at 8, so it has not been charged since the first scan. **Charge
it fully two or three times, then re-scan.** Until then it stays undecided.

### One pack gained a dumb charge between sessions

4825661 read `88, 8, (96)` in September and `88, 9, (97)` today. Redlink unchanged, cycles
identical to the decimal — so it was never genuinely charged or used, and only the dumb
counter moved. The single known mechanism is TX resting high with a pack attached, which
is what happens when the stock single-shot path exits with the battery still connected.
5133836 was captured in the same September session and did not move, which fits the
0.48 s threshold making it timing-dependent rather than certain.

Not provable to the second, but the contrast is the useful part: **eight holder-driven
captures across two sessions, zero increments.** The holder is not a nicety.

### Consequences for the GUI

- **A register's state is part of its value.** "not recorded by this firmware", "never
  written", and "0" are three different things and must not render alike.
- **The charge-start / charge-end voltage histograms only exist on the older packs.** They
  are good data — they show how the pack was actually treated — but any screen built
  around them is blank on anything made after ~2023.
- Comparing packs across generations on RAM-block statistics is not valid. Cycles, Ah,
  charge counts and the 10-200A buckets (registers 44-63) are populated on both, and are
  the fields to build on. That agrees with the wear metrics chosen from the seven-pack
  scan.

## Phase 3: the batch runner (2026-09-28)

    .venv/bin/python tools/runner.py

One process, port held low throughout. Plug a battery in, it reads; type the sticker
while it reads; it reports; unplug, press Enter; next. `holder.py` stays as the manual
tool for one-off experiments.

### Status

| Step | State |
| --- | --- |
| 1. Runner + hardware-free tests | ✅ done — 81 tests pass; smoke-run against the real adapter with no pack attached |
| 2. Repeated-probe test on pack 2 (`tools/poll_test.py`) | ⏳ **next — needs pack 2 attached** |
| 3. Enable `--auto-remove` if step 2 passes | blocked on 2 |
| First real batch run | after 2 |

Until step 2 passes, run **without** `--auto-remove`: removal is confirmed by pressing
Enter, and the runner knocks on an attached pack once per confirmation, which is the same
exposure as every capture so far.

### Insertion polling — `probe()`

A knock once a second: the `reset()` sync handshake. Answer = a battery arrived.

**It is not `M18.reset()`, on purpose.** `reset()` leaves the line high while it waits for
the sync reply, and it waits the port's full 0.8 s timeout. With nothing attached that is
harmless — but a battery plugged in *during* that wait sees 0.3 + 0.8 = 1.1 s of high,
more than twice the 0.48 s threshold. `probe()` uses a 0.05 s reply timeout (the reply is
one byte, ~2 ms at 4800 baud) and forces the line low in a `finally`, so the worst case is
about 0.35 s. `test_high_time_stays_under_the_dumb_charge_threshold` pins this, and fails
if the timeout is put back to 0.8 s (checked by mutation).

Why eight full dumps never cost a charge, found while building this: the 13 "absent"
registers are **not silence** on hardware. They come back as an instant `82 04` refusal
frame, so the line never sits high waiting on them.

### Operator entry — the sticker

- Asked **while the read runs** (background thread), so typing costs nothing.
- Format `XXXXXXXX YYMMDD NNNNNNN`, case-insensitive, spaces optional, letters kept. An
  odd-shaped sticker is accepted after a confirmation (other models may differ). `?`
  records `UNREADABLE`.
- **YYMMDD is checked against the battery's own manufacture date**, ±1 day. Mismatch →
  warning, retype or keep. All seven real captures pass this check (tested).
- A repeat e-serial is never overwritten: saved as `pack_<es>_full_<timestamp>.json`
  alongside the original, or skipped.
- `nologo <sticker>` logs a pack that cannot be read, so the inventory is complete.

### The error path — `classify()`

| Result | Rule |
| --- | --- |
| `DROPPED` | nothing came back after sync, **or** any non-Forge register failed. Every register that failed on either firmware generation was Forge-labelled, so anything else means the connection broke. Not saved; the sticker is offered again on reseat. |
| `DEAD` | e-serial register, or ≥5 of the 41 health registers, all-ones |
| `FLAGGED` | 1-4 blank health registers, partial cell sentinel, unknown battery type, or imbalance > 30 mV (with balancing advice when charges < 20) |
| `OK` | none of the above |

Against the seven real captures: six `OK`, and `5950263` `FLAGGED` with the
charge-it-first advice — exactly what CLAUDE.md concluded by hand. The 30 mV line is
provisional, a "look at me" not a verdict; cutoffs still wait for the full pile.

Every battery gets a line in `data/runs/run_<timestamp>.jsonl`, and an exception on one
pack is logged as `ERROR` and the run continues.

`dump_registers()` now gives up after 5 consecutive *silent* registers and marks the rest
`not attempted: connection lost`. Unplugged at register 60, the old code would have spent
about two minutes timing out on the remaining 124 before anyone knew.

### Refusing to close with a battery attached — `close_safely()`

`quit`, Ctrl+C, or the input closing all end in `close_safely()`, which probes twice and
**will not close the port while a pack answers.** Ctrl+C during the refusal is ignored.
If stdin is gone (nobody to press Enter) it keeps holding the line low and polls until the
pack is removed, then closes. A background read is always joined before anything else
touches the port. Mutation-checked: removing the attached check fails three tests.

Its one blind spot: a no-logo pack never answers, so it cannot be detected. The runner
says so on exit.

### Bugs caught while building

- **A dead pack was classified `DROPPED`.** Phase 2's `answered` counted only `ok`
  registers, so a pack answering every question with all-ones looked silent. Now means
  "any bytes came back". Would have sent a dead pack back for endless reseating.
- EOF on stdin during the close refusal would have fallen straight through to closing the
  port — the exact outcome `close_safely()` exists to prevent.
- A non-Ctrl+C error during sticker entry left the background read still using the port.

## Case serials

The sticker reads `G29JDCBC 180720 3894605` — an 8-char model code, a YYMMDD date, and a
7-digit number. **We use it purely as an identifier to pair with the e-serial.** Do not
read anything further into it.

Two operational facts, both verified across five packs:

- **The 7-digit number appears nowhere on the wire.** Searched all five `--ss` dumps.
  Landmine 4 confirmed empirically; out-of-band capture is genuinely required, and there
  is no shortcut.
- **It is alphanumeric.** Any capture path — scanner or keyboard — must accept letters. A
  numeric-only field would silently mangle `G29NDCBC`.

The date field matches the wire manufacture date to within a day (pack 2's sticker reads
`240423` against a wire time of `2024-04-22 23:57:52`, eight minutes before midnight UTC).
Cheap cross-check that a sticker got paired with the right capture, which is worth having
when a human is typing 40 of them.

## Health signals, from the seven-pack scan (2026-09-04)

Sorted by cycles. All seven carry the wifi logo and read cleanly; no sentinels anywhere.

| e-serial | case | built | cycles | Ah | imbal | chgs | ovht | lowV | empty |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5133886 | 3988041 | 2018-07-27 | 34.34 | 171.68 | 5 mV | 81 | 3 | 35 | 8 |
| 4769294 | 1431974 | 2018-08-28 | 33.16 | 99.47 | 11 mV | 87 | 8 | 57 | 13 |
| 4769289 | 1431973 | 2018-08-28 | 31.92 | 95.76 | 7 mV | 100 | 1 | 59 | 22 |
| 4825661 | 3894605 | 2018-07-20 | 31.41 | 157.07 | 7 mV | 96 | 3 | 22 | 7 |
| 5133836 | 3988036 | 2018-07-27 | 18.56 | 92.79 | 11 mV | 56 | 2 | 5 | 2 |
| 6278308 | 5076988 | 2024-04-22 | 4.33 | 21.64 | 22 mV | 21 | 0 | 3 | 1 |
| 5950263 | 0670871 | 2020-05-21 | 2.14 | 6.41 | **67 mV** | 8 | 0 | 1 | 1 |

### Imbalance trends *inverse* to use — probably balancing opportunity, not damage

By descending cycles the imbalance column reads 5, 11, 7, 7, 11, 22, 67. Not monotonic —
there is scatter inside the noise band, and 4769294 alone read 15 mV and 11 mV on two scans
the same day. But the shape is unambiguous: the five well-used packs all sit at 5-11 mV,
and the two least-used sit at 22 and 67.

Cell balancing happens during charging. 5133886 has had 81 charges to balance; 5950263 has
had 8. So **high imbalance on a lightly used pack may be idle drift, not a bad cell
group** — and those two conclusions call for opposite actions.

**Do not condemn 5950263 on this scan.** Charge it fully two or three times and re-scan. If
imbalance collapses toward 10 mV it is healthy and merely neglected; if it holds at 67 mV
it is a genuine repair candidate. Cheap test, and it must happen before any pack is written
off for imbalance. Same for any low-cycle pack in the wider pile.

### What the report should use

- **Wear** — cycles, Ah, `low_voltage_events`, `times_overheated`, `discharged_to_empty`.
  These order the packs consistently and are the honest condition signal.
- **Imbalance** — a flag only, above the noise band, and interpreted *alongside charge
  count*. Below ~22 mV it carries no information. Not a score; never render it as a grade.
- 4769289 has 100 charges, 59 low-voltage events and 22 discharges to empty — the most
  abused pack in the set — yet reads 7 mV. Abuse history and balance are independent.

Cutoffs still need the full pile. Seven packs establish the shape, not the lines.

### The dumb-charge counter: cause isolated and solved — measured 2026-09-04

Pack 4769294 read `82, 4, (86)` on its first scan and `82, 5, (87)` on a re-scan later the
same day. Redlink unchanged; the dumb count went up by one. Landmine 1 is not theoretical.

**The cause is the port closing with a pack attached**, not reading and not connecting.
Closing the port releases break condition, TX reverts to UART idle-high, J2 goes above
7.1V, and 0.48s later the pack counts a dumb charge. 4769294 sat through a gap between two
separate commands (`--health` then `--ss`) with a pack attached; 6278308 only ever saw one
held-open process that day and did **not** increment.

**Verified against hardware with `tools/holder.py`** (one process, port opened once, break
asserted the entire time except during reads):

| | pack 6278308 |
| --- | --- |
| baseline | `redlink=19 dumb=2 total=21` |
| after 3 physical connect/disconnect cycles + 2 full reads | `redlink=19 dumb=2 total=21` |

Three connections and two reads, zero increments. So:

- **Reading is safe.** Communication toggles TX far faster than 0.48s; it never rests high.
- **Connecting is safe, provided the line is already held low.**
- **Closing the port with a pack attached is the only dangerous act.**

**The rule for Phase 3:** one process holds the port for the entire run, break asserted
between packs, and no battery is attached when it exits. That is the whole mitigation, and
it costs nothing — it is the same design that avoids paying the 10-second startup 40 times.
`tools/holder.py` is the working skeleton.

Corollary: never run the stock single-shot `--health` / `--ss` commands back to back on an
attached pack. That is exactly what corrupted 4769294. Use the holder.

### Still untested

No dead pack was available, so a pack whose *health* registers are all sentinels has never
been on the bench. `tests/test_dump.py` exercises the all-ones case end to end in software
and asserts that no derived field survives marked valid, but that is a fixture, not a
battery. If any pack in the wider pile returns sentinels in the health registers, capture
the raw output before anything else.

(Sentinels in the RAM block are a different matter and turned out to be routine — see
"What the full dump found".)

## Fork state (2026-09-01)

- `master` — clean mirror of `upstream/master`. Tracks upstream, never committed to.
- `sector67` — this file. Long-lived, never PR'd upstream.
- `fix-divzero` — one commit, issue #47. Ready to PR, not yet submitted.
- `health-data` — built on `fix-divzero`. Two commits: the `health_data()` split, and
  hardware-free regression tests (`tests/`, stdlib only). The tests are a separate
  commit so they can be dropped for a minimal upstream PR.

`tests/test_health.py` stubs `read_id()` with canned registers, so the arithmetic and
formatting run with no pack attached. Printed output is pinned to
`tests/golden_health.txt`; regenerate deliberately with `--update-golden`.

## Branch discipline

- `master` stays a clean mirror of upstream. Never commit to it directly.
- One branch per upstream PR. Small, single-purpose. A branch containing a bugfix *and*
  800 lines of PDF code will not be merged.
- `sector67` is the long-lived branch for the batch runner, inventory, PDF layout, and
  asset tags. It is never PR'd upstream.
- Upstream is a protocol research repo and will not want a PDF dependency.
- Do not commit this file to any branch destined for upstream.

## Conventions

- When the task is an upstream fix, change only what was asked. No opportunistic
  refactoring, no style cleanup, no reformatting untouched lines.
- When refactoring `health()`, preserve the printed output exactly.
- No new dependencies on upstream-bound branches.
- You cannot test against hardware. Any code touching the serial port has to be run by a
  human with a battery physically attached, and the output pasted back. Write it to fail
  loudly and log raw bytes so that loop is short.

## Open questions

- How many batteries, what age spread, and how many carry the wifi logo that indicates
  diagnostic support?
- Do the case labels have a scannable barcode, and does it encode the printed serial?
  This decides between a USB scanner and manual entry.
- ~~Which USB-serial chip is in the adapter, and is it genuine?~~ Answered 2026-09-04:
  genuine FT232R. See Hardware.
- ~~What does a pack with **no wifi logo** return?~~ Answered 2026-09-04: silence, zero
  bytes. See above. Phase 3 still has to tell apart three failures that look alike: no
  diagnostic support, dead pack, bad connection — and the first two share a signature.
- What does the GUI actually need to show, and to whom? A Sector67 member triaging a pack
  wants a verdict and the two or three numbers behind it. We want everything. Those are
  different screens; decide before laying anything out.
- Health thresholds are deliberately undecided. Scan the pile first, look at the actual
  distribution of imbalance and cycle counts, then set cutoffs. Inventing thresholds up
  front produces confident-looking nonsense. **Partially answered 2026-09-04**: imbalance
  is out (see above); build on cycles, `low_voltage_events`, `times_overheated`,
  `discharged_to_empty`. Actual cutoffs still need the full pile.
- Do the case labels carry a barcode? Still unknown, and the sticker serial is
  alphanumeric (`G29NDCBC`), so any capture path must accept letters. A numeric-only
  field would silently mangle it.
