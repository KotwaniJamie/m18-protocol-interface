"""PackCheck: connect an M18 battery, see what it knows about itself.

    .venv/bin/python gui/app.py
    .venv/bin/python gui/app.py --port /dev/cu.usbserial-XXXX
    .venv/bin/python gui/app.py --open path/to/pack_<serial>_full.json

The window refuses to close while a battery is attached: closing the port
lets the data line go high, and a battery left on it counts a "dumb charge",
permanently. Unplug first.
"""
import argparse
import json
import pathlib
import signal
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import webview

import model
import scanner as scanner_mod

ROOT = HERE.parent


def default_data_dir():
    # Inside a checkout, scans go where the batch runner puts them.
    repo = ROOT / "data"
    return repo if repo.is_dir() else pathlib.Path.home() / "PackCheck"


class Api:
    """What the page can call. Nothing here touches the serial port."""

    def __init__(self, scanner, opened=None):
        self._scanner = scanner
        self._opened = opened
        self._window = None

    def state(self):
        s = self._scanner.snapshot()
        s["opened"] = self._opened
        return s

    def set_sticker(self, text):
        return self._scanner.set_sticker(text)

    def set_name(self, text):
        return self._scanner.set_name(text)

    def accept_adapter(self, device):
        self._scanner.accept_adapter(device)

    def cancel_close(self):
        self._scanner.cancel_close()

    def quit(self):
        if self._scanner.request_close():
            self._window.destroy()

    def open_scan(self):
        paths = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(self._scanner.captures),
            file_types=("Battery scans (*.json)",))
        if paths:
            self._opened = load_scan(paths[0], self._scanner.captures)
        return self._opened

    def close_scan(self):
        self._opened = None

    # -- the saved-scans page (reads files only; never touches the port) --
    def list_scans(self):
        return model.catalogue(load_all(self._scanner.captures))

    def open_saved(self, name):
        path = capture_path(self._scanner.captures, name)
        if path is None:
            return None
        self._opened = load_scan(path, self._scanner.captures)
        return self._opened


def capture_path(captures_dir, name):
    """The saved scan called `name`, only if it really is one in the captures
    folder. The page sends a bare file name; anything else is refused."""
    name = str(name or "")
    if "/" in name or "\\" in name or not (name.startswith("pack_") and name.endswith(".json")):
        return None
    path = pathlib.Path(captures_dir) / name
    return path if path.is_file() else None


def load_all(captures_dir):
    """(file name, document) for every full capture, skipping unreadable files."""
    out = []
    for p in sorted(pathlib.Path(captures_dir).glob("pack_*_full*.json")):
        try:
            out.append((p.name, json.loads(p.read_text())))
        except (OSError, ValueError):
            pass
    return out


def load_scan(path, captures_dir):
    path = pathlib.Path(path)
    doc = json.loads(path.read_text())
    earlier = []
    es = doc.get("e_serial")
    if es:
        for p in sorted(pathlib.Path(captures_dir).glob(f"pack_{es}_full*.json")):
            try:
                earlier.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                pass
    return {"file": path.name, "view": model.view(doc, earlier)}


def main():
    ap = argparse.ArgumentParser(description="PackCheck")
    ap.add_argument("--port", help="adapter serial port (found automatically if left out)")
    ap.add_argument("--data", type=pathlib.Path, default=None,
                    help="where scans are saved (default: data/ in a checkout, else ~/PackCheck)")
    ap.add_argument("--open", type=pathlib.Path, help="show a saved scan")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    data_dir = args.data or default_data_dir()
    window = None

    def closed():
        if window is not None:
            window.destroy()

    sc = scanner_mod.Scanner(data_dir, port=args.port, on_closed=closed)
    opened = load_scan(args.open, sc.captures) if args.open else None
    api = Api(sc, opened)

    window = webview.create_window(
        "PackCheck", str(HERE / "web" / "index.html"), js_api=api,
        width=1180, height=860, min_size=(420, 600), background_color="#DB011C")
    api._window = window

    def on_closing():
        # Returning False keeps the window open. The scanner closes the port
        # once no battery answers, then destroys the window itself.
        return sc.request_close()

    window.events.closing += on_closing

    # Ctrl+C in the terminal goes the same safe way as the close button.
    signal.signal(signal.SIGINT, lambda *a: sc.request_close())
    signal.signal(signal.SIGTERM, lambda *a: sc.request_close())

    sc.start()
    webview.start(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
