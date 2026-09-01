"""
Canned battery fixtures + a stubbed read_id, so health() can be exercised
with no serial port attached.

Values are in the shape read_id(..., output="array") returns:
    [[register_id, array_value], ...]
where array_value's Python type depends on the register's data_type:
    uint -> int | date -> datetime | cell_v -> [int]*5
    adc_t -> float | dec_t -> str | hhmmss -> str | sn -> str
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import m18

UTC = datetime.UTC


def dt(y, mo, d):
    return datetime.datetime(y, mo, d, tzinfo=UTC)


def sn(btype, serial):
    return f"Type: {btype:3d}, Serial: {serial:d}"


def hhmmss(sec):
    mm, ss = divmod(sec, 60)
    hh, mm = divmod(mm, 60)
    return f"{hh}:{mm:02d}:{ss:02d}"


def adc_t(adc):
    return m18.M18.calculate_temperature(None, adc)


BUCKETS = list(range(44, 64))


def base(buckets=None, over=None):
    """A plausible, healthy 5Ah XC pack."""
    v = {
        4:  dt(2021, 3, 15),          # manufacture date
        8:  dt(2026, 8, 20),          # battery's own clock ("now")
        25: dt(2026, 8, 1),           # last tool use
        26: dt(2026, 8, 10),          # last charge
        28: 1985,                     # days since first charge
        12: [3650, 3648, 3655, 3641, 3652],
        13: adc_t(0x0200),            # non-Forge temp
        18: None,                     # Forge temp absent
        29: 1250000,                  # total discharge (amp-sec)
        39: 12, 40: 3, 41: 0, 42: 7, 43: 2,
        33: 210, 32: 18, 31: 228,     # redlink, dumb, total charge count
        35: hhmmss(486000),           # total charging time
        36: hhmmss(93600),            # idle on charger
        38: 4,                        # low-voltage charges
        2:  sn(40, 8675309),
    }
    b = buckets if buckets is not None else (
        [42000, 21000, 9000, 4200, 1800, 900, 400, 180, 90, 40,
         20, 10, 5, 3, 2, 1, 1, 0, 0, 0])
    v.update({r: b[i] for i, r in enumerate(BUCKETS)})
    v.update(over or {})
    return v


def make_read_id(values):
    def read_id(self, id_array=None, force_refresh=True, output="label"):
        return [[i, values.get(i)] for i in id_array]
    return read_id


def build(values):
    """Return an M18 instance with no serial port, wired to `values`."""
    bat = object.__new__(m18.M18)
    bat.read_id = make_read_id(values).__get__(bat, m18.M18)
    bat.txrx_save_and_set = lambda *a, **k: None
    bat.txrx_restore = lambda *a, **k: None
    return bat


# ---------------------------------------------------------------- fixtures
CASES = {}

CASES["normal"] = base()

# Issue #47: pack that has never drawn >10A -> every bucket zero.
CASES["unused"] = base(buckets=[0] * 20)

# Landmine #2: dead pack returns 0xFFFF / 0xFFFFFFFF sentinels.
CASES["dead_sentinels"] = base(
    buckets=[0xFFFF] * 20,
    over={28: 0xFFFF, 29: 0xFFFFFFFF, 39: 0xFFFF, 40: 0xFFFF,
       41: 0xFFFF, 42: 0xFFFF, 43: 0xFFFF,
       31: 0xFFFFFFFF, 32: 0xFFFF, 33: 0xFFFF, 38: 0xFFFF,
       35: hhmmss(0xFFFFFFFF), 36: hhmmss(0xFFFFFFFF),
       25: dt(1970, 1, 1), 26: dt(1970, 1, 1)})

# Battery type code absent from bat_lookup -> cycles cannot be derived.
CASES["unknown_type"] = base(over={2: sn(999, 1234567)})

# Forge pack: dec_t temperature present, adc_t absent.
CASES["forge"] = base(over={13: None, 18: "31.75", 2: sn(383, 4242424)})

# Forge temp reading exactly zero -> "0.00" is a truthy string, so the
# original code prints it. Guards against a truthiness regression.
CASES["forge_zero_temp"] = base(over={13: None, 18: "0.00"})

# Single bucket carries all the time -> 100% bar.
CASES["one_bucket"] = base(buckets=[0] * 19 + [3600])
