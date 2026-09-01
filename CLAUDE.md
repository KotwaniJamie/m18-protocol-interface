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
   via DTR. Issue #61 (June 2026) is someone still stuck.

6. **Python version.** `bytes2dt()` uses `datetime.UTC`, which needs 3.11+. `.python-version`
   pins 3.13. Issue #49 is someone hitting this.

## Plan

- **Phase 0** — Verify hardware. Identify the adapter chip, run the README's J1/J2 voltage
  checks, hand-scan five batteries spanning the age range before writing anything.
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
- Which USB-serial chip is in the adapter, and is it genuine?
- Health thresholds are deliberately undecided. Scan the pile first, look at the actual
  distribution of imbalance and cycle counts, then set cutoffs. Inventing thresholds up
  front produces confident-looking nonsense.
