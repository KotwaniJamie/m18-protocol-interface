# M18 Battery Health Project — Sector67 fork

Fork of [mnh-jansson/m18-protocol](https://github.com/mnh-jansson/m18-protocol).

**Goal:** batch-scan a pile of Milwaukee M18 packs at Sector67, capture the case-printed
serial alongside the on-wire diagnostics, and produce a one-page printable PDF health
report per battery to support triage for repair, sale, or recycling.

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
  `date`, `hhmmss`, `ascii`, `sn`, `adc_t`, `dec_t`, `cell_v`. About 90 entries, many
  still labelled Unknown.
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

7. **TX state after the port closes is unknown.** `M18.__init__` calls `idle()` as soon as
   the port opens, so any single command is safe while running. But `--idle` sets break
   condition, prints, and exits — closing the port. Whether the FTDI chip *holds* the line
   low after the host releases it, or reverts to UART idle-high, has not been measured. If
   it reverts, then `--idle`-then-connect protects nothing and every `--health` run costs a
   dumb-charge increment on exit. **Unmeasured as of 2026-09-04.** One multimeter reading
   settles it: run `--idle`, wait for the shell prompt, then meter TX to GND with no pack
   attached. <1V means it holds; ~3.3V means it does not. Phase 3 must hold one process
   open across the whole batch either way, but this decides whether stock single-shot
   commands are usable at all during development.

## Plan

- **Phase 0** — ✅ Done 2026-09-04. Adapter identified and verified genuine, full read
  chain confirmed against a real pack. See Hardware below. The README's `m.high()` J1/J2
  voltage check was deliberately **skipped**: a successful `--health` read exercises the
  same path (J2 drive + J1 receive) while staying under the 0.48s dumb-charge threshold,
  whereas `m.high()` holds TX high indefinitely and guarantees a register-32 increment.
  Still outstanding: hand-scan four or five more packs spanning the age range, including
  a known-dead pack and a pack with no wifi logo, before writing the batch runner.
- **Phase 1** — ✅ Done, on `health-data`. `health_data()` returns 30 fields as
  `{"value", "valid"}`; `health()` renders them. Validity propagates — a value derived
  from a sentinel is never marked more trustworthy than the register it came from.
  Output verified byte-identical across 1000 randomised packs. Date fields are
  `datetime` objects, so JSON needs `default=str`.
- **Phase 2** — Capture all ~90 registers to JSON per battery, not just the 41 that
  `health()` uses. Storage is free; re-handling 40 batteries later is not.
- **Phase 3** — Batch runner. Hold the port open, poll `reset()` to detect insertion,
  capture case serial, scan, write JSON, idle, next. One bad battery must not end the run.
- **Phase 4** — JSON to one-page PDF, as a separate step from scanning so reports can be
  re-rendered without re-scanning.

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
  on the first real battery. Phase 2 must record absent and sentinel as **distinct**
  states, never collapse both to null.

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
| `reset()` True, then sentinel values | dead pack — BMS talks, data is garbage |
| `reset()` True, clean values | good pack |

Note the first row is still two states sharing one signature. Since a no-logo pack is
identified visually before it is ever connected, the runner should record the logo
observation as operator input, and then treat silence from a pack that *has* a logo as a
connection fault worth retrying.

**`health()`'s error handling is unusable for batch work.** The no-logo failure printed
`Check battery is connected and you have correct serial port` — the same message
landmine 3's division-by-zero produced. The broad `except` flattens every distinct
failure into one misleading sentence. Across 40 packs that sends you chasing adapter
faults that do not exist. Phase 3 needs its own error path; do not reuse `health()`'s.

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

## Imbalance does not discriminate — do not build the report on it

Five packs, six years, 8x the cycle range:

| e-serial | built | cycles | imbalance | charges | overheat | lowV | empty |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4769294 | 2018-08-28 | 33.16 | 15 mV | 86 | 8 | 57 | 13 |
| 4825661 | 2018-07-20 | 31.41 | 7 mV | 96 | 3 | 22 | 7 |
| 5133836 | 2018-07-27 | 18.56 | 11 mV | 56 | 2 | 5 | 2 |
| 4769289 | 2018-08-28 | — | 7 mV | 100 | — | — | — |
| 6278308 | 2024-04-22 | 4.33 | 17-22 mV | 21 | 0 | 3 | 1 |

The hardest-used pack has the best balance; the nearly-new one has the worst. Worse, pack
6278308 measured **17 mV and 22 mV in two reads seconds apart** — a 30% swing on the
headline number, consistent with the ±5 mV ADC noise. Imbalance in the 7-22 mV band is
measurement noise, not pack condition.

`low_voltage_events` by contrast scales cleanly with cycles (57 / 22 / 5 / 3), and
`times_overheated` and `discharged_to_empty` order the packs consistently. **Those are the
triage signals.** Imbalance may still flag a genuinely failing pack, but nothing in the
healthy range should be sorted by it.

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
- Does the FTDI line hold low after the port closes? See landmine 7. Unmeasured.
- What does a pack with **no wifi logo** return — clean refusal, sentinels, or silence?
  Scott reports his testing only worked on packs new enough to carry the logo, so this is
  a hard filter on the pile, not an edge case. Phase 3 must tell apart three failures that
  look alike: no diagnostic support, dead pack, bad connection.
- Health thresholds are deliberately undecided. Scan the pile first, look at the actual
  distribution of imbalance and cycle counts, then set cutoffs. Inventing thresholds up
  front produces confident-looking nonsense. **Partially answered 2026-09-04**: imbalance
  is out (see above); build on cycles, `low_voltage_events`, `times_overheated`,
  `discharged_to_empty`. Actual cutoffs still need the full pile.
- Do the case labels carry a barcode? Still unknown, and the sticker serial is
  alphanumeric (`G29NDCBC`), so any capture path must accept letters. A numeric-only
  field would silently mangle it.
