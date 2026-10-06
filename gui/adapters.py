"""Find the USB-serial adapter, and say how far its chip can be trusted.

Between reads the program holds the battery's data line low with a serial
"break". A pack that sees the line high for 0.48 s or more counts a dumb
charge, permanently. Some USB-serial chips cannot really send a break and
fake it -- and with those, every battery connected gets a charge added.
So the chip matters, and anything not tested is said out loud, not
quietly used.
"""
from serial.tools import list_ports

TESTED, UNTESTED, RISKY = "tested", "untested", "risky"

# (vid, pid) -> (chip name, status)
CHIPS = {
    (0x0403, 0x6001): ("FTDI FT232R", TESTED),
    (0x0403, 0x6010): ("FTDI FT2232", UNTESTED),
    (0x0403, 0x6011): ("FTDI FT4232", UNTESTED),
    (0x0403, 0x6014): ("FTDI FT232H", UNTESTED),
    (0x0403, 0x6015): ("FTDI FT-X", UNTESTED),
    (0x10C4, 0xEA60): ("Silicon Labs CP210x", RISKY),
    (0x1A86, 0x7523): ("WCH CH340", RISKY),
    (0x1A86, 0x5523): ("WCH CH341", RISKY),
    (0x067B, 0x2303): ("Prolific PL2303", RISKY),
}

MESSAGES = {
    TESTED: "The chip PackCheck was tested with. (Counterfeit FT232R chips report "
            "the same ID; if yours came from an unknown seller, it may not be the real thing.)",
    UNTESTED: "PackCheck hasn't been tested with this chip. If it can't hold the line "
              "low, every battery you connect gets a dumb charge added to its record, "
              "permanently.",
    RISKY: "Adapters with this chip often fake the signal that keeps the line low. If "
           "this one does, every battery you connect gets a dumb charge added to its "
           "record, permanently. An FTDI FT232R adapter is the safe choice.",
}

RANK = {TESTED: 0, UNTESTED: 1, RISKY: 2}


def describe(port):
    """One port as {device, chip, status, message}. Non-USB ports give None."""
    if port.vid is None:
        return None
    chip, status = CHIPS.get((port.vid, port.pid), (None, UNTESTED))
    if chip is None:
        chip = port.description if port.description and port.description != "n/a" \
            else f"USB serial {port.vid:04X}:{port.pid:04X}"
    return {"device": port.device, "chip": chip, "status": status,
            "id": f"{port.vid:04X}:{port.pid:04X}", "message": MESSAGES[status]}


def find(comports=list_ports.comports):
    """USB serial ports, the best-trusted first."""
    found = [d for d in (describe(p) for p in comports()) if d]
    # On macOS each device also appears as /dev/tty.*, which blocks waiting
    # for carrier detect. list_ports gives cu.* already; this is a guard.
    found = [d for d in found if "/dev/tty." not in d["device"]]
    return sorted(found, key=lambda d: (RANK[d["status"]], d["device"]))


def by_device(device, comports=list_ports.comports):
    """A port named on the command line, described if it is USB."""
    for p in comports():
        if p.device == device:
            return describe(p) or {"device": device, "chip": "serial port",
                                   "status": UNTESTED, "id": None,
                                   "message": MESSAGES[UNTESTED]}
    return None
