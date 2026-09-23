#!/usr/bin/env python3
"""EyeBuddy app: camera, serial log and ST-Link controls in one window.

One file. Run it on a Debian or Ubuntu machine and it installs the toolkit it needs, then
starts. `--install` also puts it on PATH and in the launcher.

The ST-Link noctalia plugin stays the backend. Actions go through `noctalia msg plugin`,
state comes back through the plugin's state.json / job.json.
"""
import argparse
import collections
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# GTK cannot be installed from PyPI, so the toolkit has to come from the distribution.
PACKAGES = {
    "apt-get": {"toolkit": ["python3-gi", "python3-gi-cairo", "gir1.2-gtk-4.0", "gir1.2-adw-1",
                            "python3-serial"],
                # gtk4paintablesink is left out on purpose: few archives carry it, and the
                # picture falls back to painting the frames by hand without it.
                "camera": ["gir1.2-gst-plugins-base-1.0", "gstreamer1.0-plugins-base",
                           "gstreamer1.0-plugins-good"]},
    "dnf": {"toolkit": ["python3-gobject", "gtk4", "libadwaita", "python3-pyserial"],
            "camera": ["gstreamer1-plugins-base", "gstreamer1-plugins-good"]},
    "pacman": {"toolkit": ["python-gobject", "gtk4", "libadwaita", "python-pyserial"],
               "camera": ["gst-plugins-base", "gst-plugins-good"]},
}
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=EyeBuddy
Comment=Camera, serial log and ST-Link controls for the eyebuds bench
Exec={command}
Icon=camera-web
Categories=Development;Utility;
StartupWMClass=dev.uxstream.EyebudsDev
"""


def package_manager():
    return next((name for name in PACKAGES if shutil.which(name)), None)


def install_packages(manager, packages):
    """Install with the system package manager, asking for the password the way the session can."""
    verb = ["-S", "--noconfirm"] if manager == "pacman" else ["install", "-y"]
    command = [shutil.which(manager)] + verb + packages
    if os.geteuid():
        # A terminal can ask for a password itself; a launcher-started window needs polkit to ask.
        lift = "sudo" if sys.stdin.isatty() and shutil.which("sudo") else "pkexec"
        if not shutil.which(lift):
            return False
        command = [shutil.which(lift)] + command
    return subprocess.run(command).returncode == 0


def ensure_toolkit():
    """Import the toolkit, and when it is not there, install it once and start again."""
    try:
        import cairo, gi, serial  # noqa: F401
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
        return
    except (ImportError, ValueError) as error:
        manager = package_manager()
        if manager is None or os.environ.get("EYEBUDDY_BOOTSTRAPPED"):
            sys.exit(f"EyeBuddy needs GTK 4, libadwaita and pyserial for Python: {error}")
        print(f"EyeBuddy needs a few packages from your distribution: {error}")
        if not install_packages(manager, PACKAGES[manager]["toolkit"]):
            packages = " ".join(PACKAGES[manager]["toolkit"])
            sys.exit(f"That install did not go through. Try it yourself:\n"
                     f"  sudo {manager} update && sudo {manager} install {packages}")
        os.environ["EYEBUDDY_BOOTSTRAPPED"] = "1" # one attempt, so a bad install cannot loop
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])


def install_launcher():
    """Copy the file onto PATH and write the desktop entry that starts it."""
    target = Path.home() / ".local/bin/eyebuddy"
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != target.resolve():
        shutil.copy2(__file__, target)
    target.chmod(0o755)
    desktop = Path.home() / ".local/share/applications/eyebuddy.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(DESKTOP_ENTRY.format(command=target))
    subprocess.run(["update-desktop-database", str(desktop.parent)], check=False,
                   stderr=subprocess.DEVNULL)
    print(f"Installed {target}")
    if str(target.parent) not in os.environ.get("PATH", "").split(":"):
        print(f"Add {target.parent} to your PATH to start it by name.")


ensure_toolkit()

import cairo  # noqa: E402
import colorsys  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

try: # the log and the chart are the point, so a machine without GStreamer still runs the app
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
except (ImportError, ValueError):
    Gst = None

import serial  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: camera rotation and size, mutes, raw/pretty view.
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "eyebuds-dev/settings.json"
STATE_TEXT = {
    "running": "Running", "halted": "Halted", "reset": "In reset",
    "debug-running": "Running", "unknown": "Unknown",
}
CAMERA_HEIGHT = 560
RECENT_LINES = 12 # how far back a repeated line is still collapsed into its earlier one
OUTLIER_SIGMA = 5 # how far from a field's mean a value has to be before it is called out
OUTLIER_QUIET = 20 # seconds before the same field may warn again
OUTLIER_TTL = 300 # seconds an outlier stays listed under the chart
OUTLIER_SHOWN = 6 # how many of them are listed at once
RANGE_ROWS = 24 # fields the range table has room to be useful about
NUM_COLUMN = 52 # width of one number column in the table strip
NUM_ROW = 13 # smallest height one row of the table strip may have
AXIS_GUTTER = 38 # left margin the bands write their y values in
TIME_AXIS = 14 # bottom margin the clock ticks are written in
BAND_FLOOR = 46 # smallest height a band is given before the rest is shared out
EVENT_BAND = 72 # timeline of one-off events, kept off the metric chart
CHART_WINDOW = 120 # seconds the chart shows; older samples slide out to the left and are dropped
LABEL_PLATE = 17 # height of the rounded plate behind a label
LABEL_GAP = 19 # how close two labels may sit before they push each other away
LABEL_SPRING = 55 # how hard a label is pulled back to the height of its own line
LABEL_PUSH = 900 # how hard overlapping labels shove each other apart
LABEL_DAMPING = 11 # how quickly that motion settles
WORST_MARKS = 6 # how many "worst value" labels the chart carries at once
# The sensor trades resolution for framerate: 30 fps at 640x480, 7 fps at 1600x1200.
CAMERA_SIZES = [("640x480", 30), ("800x600", 20), ("1280x720", 11), ("1600x1200", 7)]


def camera_modes(device):
    """Discrete sizes and their best framerate, from v4l2-ctl. Falls back to the known list."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return CAMERA_SIZES
    modes = {}
    size = None
    for line in out.splitlines():
        if m := re.search(r"Size: Discrete (\d+x\d+)", line):
            size = m.group(1)
        elif size and (m := re.search(r"\(([\d.]+) fps\)", line)):
            modes[size] = max(modes.get(size, 0), round(float(m.group(1))))
    return sorted(modes.items(), key=lambda kv: int(kv[0].split("x")[0])) or CAMERA_SIZES
# The firmware colours its log levels with SGR sequences. SGR is rendered with text tags,
# every other escape sequence is dropped.
ANSI = re.compile(r"\x1b\[([0-9;?]*)([ -/]*[@-~])")
# "00:08:05.905 TRACE REND jpeg_lcd.c:398: Display rendering rate: 29.9 fps"
LOG_LINE = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3})\s+(\w+)\s+(\S+)\s+(\S+:\d+):\s*(.*?)\s*$")
LEVELS = {  # glyph and colour per level, replacing the word
    "TRACE": ("·", "#7f848e"), "DEBUG": ("○", "#61afef"), "INFO": ("●", "#98c379"),
    "WARN": ("▲", "#e5c07b"), "WARNING": ("▲", "#e5c07b"), "ERROR": ("✖", "#e06c75"), "FATAL": ("✖", "#e06c75"),
}
NUMBERS = re.compile(r"-?\d+(?:\.\d+)?")


# Which direction is good, guessed from the field's label and unit.
GOOD_LOW = re.compile(r"drop|err|fail|defer|queue|retry|lost|miss|latency|delay|jitter|usage|load|temp|reason",
                      re.IGNORECASE)
GOOD_HIGH = re.compile(r"rate|fps|bitrate|rssi|signal|throughput|speed|bandwidth|kbps|mbps|level", re.IGNORECASE)


# Bands are scale groups: everything in one shares an axis, so a height reads as a real value.
# (pattern the unit must match, band, factor into the band's base unit)
UNIT_BANDS = (
    (re.compile(r"^(/s|fps|hz)$", re.IGNORECASE), "per second", 1.0),
    (re.compile(r"^(us|µs)$", re.IGNORECASE), "milliseconds", 0.001),
    (re.compile(r"^ms$", re.IGNORECASE), "milliseconds", 1.0),
    (re.compile(r"^(s|sec|secs)$", re.IGNORECASE), "milliseconds", 1000.0),
    (re.compile(r"^%$"), "percent", 1.0),
    (re.compile(r"^(db|dbm)$", re.IGNORECASE), "decibel", 1.0),
    (re.compile(r"^(b|byte|bytes)$", re.IGNORECASE), "bytes", 1.0),
    (re.compile(r"^(kb|kib)$", re.IGNORECASE), "bytes", 1024.0),
    (re.compile(r"^mb$", re.IGNORECASE), "bytes", 1048576.0),
    (re.compile(r"^bps$", re.IGNORECASE), "bits per second", 1.0),
    (re.compile(r"^kbps$", re.IGNORECASE), "bits per second", 1000.0),
    (re.compile(r"^mbps$", re.IGNORECASE), "bits per second", 1000000.0),
)
BAND_UNIT = {"per second": "/s", "milliseconds": "ms", "percent": "%", "decibel": "dB",
             "bytes": "B", "bits per second": "bps", "count": ""}
BAND_ORDER = ["per second", "bits per second", "milliseconds", "percent", "decibel", "bytes", "count"]


def band_of(label, unit):
    """The band a field belongs to, and the factor into that band's base unit."""
    for pattern, band, factor in UNIT_BANDS:
        if pattern.match(unit):
            return band, factor
    if re.search(r"\brate\b|\bfps\b", label, re.IGNORECASE):
        return "per second", 1.0
    if re.search(r"rssi|signal", label, re.IGNORECASE):
        return "decibel", 1.0
    if re.search(r"usage|percent", label, re.IGNORECASE):
        return "percent", 1.0
    return "count", 1.0


def fmt_si(value):
    """A number short enough for an axis tick or a table cell."""
    size = abs(value)
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if size >= limit:
            return f"{value / limit:.3g}{suffix}"
    if size >= 0.01 or value == 0:
        return f"{value:.4g}"
    return f"{value:.1e}"


def spread(values, gap, low, high):
    """Sorted values nudged apart to at least `gap`, kept inside [low, high] where they fit."""
    out = list(values)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + gap)
    if out and out[-1] > high:
        out[-1] = high
        for i in range(len(out) - 2, -1, -1):
            out[i] = min(out[i], out[i + 1] - gap)
    if out and out[0] < low:
        out[0] = low
        for i in range(1, len(out)):
            out[i] = max(out[i], out[i - 1] + gap)
    return out


def polarity_of(label, unit):
    """-1 when lower is better, 1 when higher is better, 0 when it is just a number."""
    text = f"{label} {unit}"
    if GOOD_LOW.search(text):
        return -1
    if GOOD_HIGH.search(text):
        return 1
    return 0


def level_rgb(level, polarity):
    """Colour for a value at `level` (0…1 of its range), green where that is good."""
    level = min(1.0, max(0.0, level))
    if polarity == 0:
        return colorsys.hls_to_rgb(0.58, 0.45 + 0.3 * level, 0.55) # no good/bad, just brighter
    good = level if polarity > 0 else 1 - level
    return colorsys.hls_to_rgb(good * 0.33, 0.62, 0.85) # red → yellow → green


def heat_color(level, polarity=-1):
    r, g, b = level_rgb(level, polarity)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def spline(cr, points):
    """Catmull-Rom through the points, as cubic beziers: no overshoot, no corners."""
    if len(points) < 2:
        return
    cr.move_to(*points[0])
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[0]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                    p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6,
                    p2[0], p2[1])


class CameraView(Gtk.Widget):
    """The camera picture, drawn at any angle and scaled to fit what is left of the box."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=False)
        self.texture = None
        self.angle = 0.0

    def set_frame(self, texture):
        size = None if self.texture is None else (self.texture.get_width(), self.texture.get_height())
        self.texture = texture
        if size != (texture.get_width(), texture.get_height()):
            self.queue_resize()
        self.queue_draw()
        return False

    def set_angle(self, angle):
        self.angle = angle % 360
        self.queue_resize() # the turned picture is a different shape, so it asks for another height
        self.queue_draw()

    def _box(self):
        """The shape of the box, which follows the nearest quarter turn.

        Letting it follow the angle itself would reshape the whole bottom of the window on every
        degree, and a quarter turn is the only turn that changes which way the picture stands.
        """
        width, height = self.texture.get_width(), self.texture.get_height()
        return (width, height) if round(self.angle / 90) % 2 == 0 else (height, width)

    def do_measure(self, orientation, for_size):
        """Height for width: as tall as the picture needs to fill the width it is given."""
        if self.texture is None or orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        box_w, box_h = self._box()
        height = box_h * for_size / box_w if for_size > 0 else box_h
        return 0, min(round(height), CAMERA_HEIGHT), -1, -1

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        radians = math.radians(self.angle)
        cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
        texture_w, texture_h = self.texture.get_width(), self.texture.get_height()
        # Turned about its middle and grown until it covers the box, so a few degrees crop the
        # corners instead of shrinking the whole picture into a diamond of empty space.
        scale = max((width * cos + height * sin) / texture_w,
                    (width * sin + height * cos) / texture_h)
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.rotate(self.angle)
        snapshot.scale(scale, scale)
        snapshot.append_texture(self.texture, Graphene.Rect().init(-texture_w / 2, -texture_h / 2,
                                                                  texture_w, texture_h))
        snapshot.restore()
        snapshot.pop()


class MultiGraph(Gtk.DrawingArea):
    """Every tracked field in one chart, each line labelled where it ends.

    x is the last CHART_WINDOW seconds, marked with clock ticks along the bottom. Lines are
    grouped into bands by unit, and one band is one axis shared by its lines, so a curve's
    height reads as a real value against the ticks in the left gutter.
    """

    def __init__(self, height=200, points=400, on_click=None, labels=True):
        super().__init__(content_height=height, hexpand=True, vexpand=True)
        self.show_labels = labels
        self.points = points
        self.on_click = on_click
        self.series = {}
        self.t0 = None
        self.label_hits = [] # (x0, x1, y0, y1, series key) for clicks
        self.visible = set() # the keys worth a label and a row, chosen by the window
        self.events = collections.deque() # one-off lines, drawn on their own timeline
        self.now = self.span = 0
        self.scales = {} # band -> the lo/hi/log axis its lines are drawn against
        self.titles = {} # band -> what is written in its top left corner
        self.plot_l, self.plot_r = AXIS_GUTTER, 0
        # Redrawn every frame: the curve slides with the clock instead of only when a sample lands.
        self.last_frame = None
        self.add_tick_callback(self._tick)
        click = Gtk.GestureClick()
        click.connect("released", self._on_released)
        self.add_controller(click)

    def _on_released(self, _gesture, _n, x, y):
        if self.on_click:
            for x0, x1, y0, y1, key in self.label_hits:
                if x0 <= x <= x1 and y0 <= y <= y1:
                    self.on_click(key)
                    return

    def add_series(self, key, color, label, polarity, unit, pattern):
        band, factor = band_of(label, unit)
        self.series[key] = {"key": key, "t": [], "v": [], "color": color, "label": label, "polarity": polarity,
                            "group": band, "band_key": None, "factor": factor, "unit": unit, "pattern": pattern,
                            "value": None, "lo": None, "hi": None,
                            "worst": None, "best": None, "flag": 0.0,
                            "y": None, "label_y": None, "label_vy": 0.0, "band": None}

    def drop_pattern(self, pattern):
        for key in [k for k, s in self.series.items() if s["pattern"] == pattern]:
            del self.series[key]
        if not self.series:
            self.t0 = None

    def flag(self, key):
        if key in self.series:
            self.series[key]["flag"] = time.time()

    def push(self, key, level, value, lo, hi):
        series = self.series.get(key)
        if series is None:
            return
        now = time.time()
        # Worst means lowest where high is good, highest where low is good, and the biggest
        # excursion either way when the field has no direction.
        polarity = series["polarity"]
        badness = (1 - level) if polarity > 0 else level if polarity < 0 else abs(level - 0.5) * 2
        factor = series["factor"] # everything in a band is stored in that band's base unit
        value, lo, hi = value * factor, lo * factor, hi * factor
        sample = (badness, value, now)
        if series["worst"] is None or badness > series["worst"][0]:
            series["worst"] = sample
        if series["best"] is None or badness < series["best"][0]:
            series["best"] = sample
        series["value"], series["lo"], series["hi"] = value, lo, hi
        self.t0 = now if self.t0 is None else self.t0
        series["t"].append(now)
        series["v"].append(value)
        cutoff = now - CHART_WINDOW
        while series["t"] and series["t"][0] < cutoff: # what leaves the window is forgotten
            series["t"].pop(0)
            series["v"].pop(0)

    def set_visible(self, keys):
        self.visible = set(keys)

    def add_event(self, text, color):
        """A one-off line worth marking on the rail: a state change, a warning, an error."""
        now = time.time()
        self.events.append((now, text, color))
        while self.events and self.events[0][0] < now - CHART_WINDOW:
            self.events.popleft()
        self.t0 = now if self.t0 is None else self.t0

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt = min(now - self.last_frame, 0.1) if self.last_frame else 0
        self.last_frame = now
        if dt:
            self._step_labels(dt)
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _step_labels(self, dt):
        """Labels are beads on a thread: pulled to their line's height, pushed off each other."""
        bands = {}
        for series in self.series.values():
            if (series["key"] in self.visible and series["y"] is not None
                    and series["label_y"] is not None and series["band"] is not None):
                bands.setdefault(series["band"], []).append(series)
        for band, beads in bands.items():
            beads.sort(key=lambda s: s["label_y"])
            for a, b in zip(beads, beads[1:]):
                overlap = LABEL_GAP - (b["label_y"] - a["label_y"])
                if overlap > 0:
                    a["label_vy"] -= LABEL_PUSH * overlap * dt / LABEL_GAP
                    b["label_vy"] += LABEL_PUSH * overlap * dt / LABEL_GAP
            top, height = band
            for bead in beads:
                bead["label_vy"] += (bead["y"] - bead["label_y"]) * LABEL_SPRING * dt
                bead["label_vy"] *= max(0.0, 1 - LABEL_DAMPING * dt)
                bead["label_y"] += bead["label_vy"] * dt
            # The spring alone lets labels pile up against a band edge, so separate them for real.
            beads.sort(key=lambda s: s["label_y"])
            placed = spread([b["label_y"] for b in beads], LABEL_GAP, top + 10, top + height - 6)
            for bead, y in zip(beads, placed):
                if abs(y - bead["label_y"]) > 0.05:
                    bead["label_vy"] *= 0.5
                bead["label_y"] = y

    def _x(self, t):
        """Time to x, with now at the right edge: samples drift left and off the chart."""
        span = self.plot_r - self.plot_l
        return self.plot_r - 1 - (self.now - t) / self.span * (span - 2)

    def _scale(self, band, members):
        """One axis for a band: the union of its lines, logarithmic when they span decades."""
        lo = min(min(s["v"]) for s in members)
        hi = max(max(s["v"]) for s in members)
        tops = [top for top in (max(s["v"]) for s in members) if top > 0]
        positive = [v for s in members for v in s["v"] if v > 0]
        if band != "percent" and lo >= 0 and tops and (
                max(tops) / min(tops) > 50 # lines that live on scales this far apart
                or hi / min(positive) > 1000): # or one line that alone covers three decades
            return {"lo": max(hi / 1e4, min(positive)), "hi": hi, "log": True} # four decades at most
        if hi <= lo:
            hi = lo + 1
        if 0 < lo < hi * 0.25:
            lo = 0.0 # a zero baseline where it costs almost nothing, so heights compare
        return {"lo": lo, "hi": hi + (hi - lo) * 0.08, "log": False}

    def _norm(self, band, value):
        scale = self.scales[band]
        lo, hi, v = scale["lo"], scale["hi"], value
        if scale["log"]:
            lo, hi, v = math.log10(lo), math.log10(hi), math.log10(max(value, lo))
        return min(1.0, max(0.0, (v - lo) / (hi - lo))) if hi > lo else 0.5

    def _ticks(self, band):
        """Round values to draw a band's gridlines at."""
        scale = self.scales[band]
        if scale["log"]:
            first = math.floor(math.log10(scale["lo"]))
            decades = [10.0 ** e for e in range(first, math.ceil(math.log10(scale["hi"])) + 1)]
            return [v for v in decades if scale["lo"] <= v <= scale["hi"]]
        span = scale["hi"] - scale["lo"]
        step = 10.0 ** math.floor(math.log10(span / 3)) if span > 0 else 1.0
        for mult in (1, 2, 5, 10):
            if span / (step * mult) <= 5:
                step *= mult
                break
        first = math.ceil(scale["lo"] / step) * step
        return [first + i * step for i in range(6) if first + i * step <= scale["hi"]]

    def _bands(self, h):
        """A band per unit and per thousandfold within it, sized by how many lines it carries.

        Splitting on magnitude is what keeps an axis readable: a line peaking at 30k and one
        peaking at 10 cannot share a scale that either of them can be read off.
        """
        members = {}
        for series in self.series.values():
            if series["key"] in self.visible and len(series["v"]) >= 2:
                decade = int(math.log10(max(abs(series["hi"]), 1)) // 3)
                series["band_key"] = key = (series["group"], decade)
                members.setdefault(key, []).append(series)
        if not members:
            return {}
        self.scales = {key: self._scale(key[0], group) for key, group in members.items()}
        # A band says its unit, and how big its lines are too once a unit runs across several bands.
        split = collections.Counter(band for band, _ in members)
        self.titles = {(band, decade): (BAND_UNIT.get(band) or band)
                       + (f" {fmt_si(1000.0 ** decade)}+" if split[band] > 1 else "")
                       + (" · log" if self.scales[(band, decade)]["log"] else "")
                       for band, decade in members}
        live = sorted(members, key=lambda key: (BAND_ORDER.index(key[0]) if key[0] in BAND_ORDER
                                                else len(BAND_ORDER), key[1]))
        # Each band gets a floor, then the rest is shared out by how many lines it carries.
        floor = min(BAND_FLOOR, h / len(live))
        spare = h - floor * len(live)
        total = sum(len(members[band]) for band in live)
        bands, top = {}, 0.0
        for band in live:
            height = floor + spare * len(members[band]) / total
            bands[band] = (top, height)
            top += height
        return bands

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        table = 3 * NUM_COLUMN + 26 # the strip on the right, one row of numbers per line
        self.plot_l, self.plot_r = AXIS_GUTTER, w - table
        self.label_hits = []
        if self.t0 is None:
            return
        self.now = time.time()
        self.span = CHART_WINDOW
        event_h = EVENT_BAND if any(when >= self.now - self.span for when, _, _ in self.events) else 0
        bands = self._bands(h - event_h - TIME_AXIS)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        if event_h:
            self._draw_events(cr, event_h)
        for i, (band, (top, height)) in enumerate(sorted(bands.items(), key=lambda kv: kv[1][0])):
            top += event_h
            cr.set_source_rgba(1, 1, 1, 0.05 if i % 2 else 0.03) # alternating, so bands separate
            cr.rectangle(0, top, self.plot_r, height)
            cr.fill()
            cr.set_font_size(9)
            for value in self._ticks(band):
                y = top + height - 3 - self._norm(band, value) * (height - 6)
                cr.set_source_rgba(1, 1, 1, 0.07)
                cr.set_line_width(1)
                cr.move_to(self.plot_l, y)
                cr.line_to(self.plot_r, y)
                cr.stroke()
                if y > top + 18:
                    text = fmt_si(value)
                    cr.set_source_rgba(1, 1, 1, 0.4)
                    cr.move_to(self.plot_l - 4 - cr.text_extents(text).width, y + 3)
                    cr.show_text(text)
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.move_to(4, top + 11)
            cr.show_text(self.titles[band])
        self._draw_time_axis(cr, h)

        cr.set_font_size(11)
        labels = []
        for key, series in self.series.items():
            if key not in self.visible or len(series["v"]) < 2 or series.get("band_key") not in bands:
                continue # muted from the table, empty, or its band is gone
            band = series["band_key"]
            top, height = bands[band]
            top += event_h
            series["band"] = (top, height)
            rgb = tuple(int(series["color"][i:i + 2], 16) / 255 for i in (1, 3, 5))
            points = [(self._x(t), top + height - 3 - self._norm(band, v) * (height - 6))
                      for t, v in zip(series["t"], series["v"])]
            cr.set_source_rgb(*rgb)
            cr.set_line_width(1.4)
            spline(cr, points)
            cr.stroke()
            for kind, fill in (("worst", True), ("best", False)):
                mark = series[kind]
                if mark and len(series["v"]) > 4 and mark[2] > self.now - self.span:
                    cr.arc(self._x(mark[2]), top + height - 3 - self._norm(band, mark[1]) * (height - 6),
                           2.5, 0, 6.2832)
                    cr.fill() if fill else cr.stroke()
            series["y"] = points[-1][1]
            if series["label_y"] is None:
                series["label_y"] = series["y"]

            flag = "! " if time.time() - series["flag"] < 10 else ""
            text = f"{flag}{series['label']}" # the numbers live in the table
            labels.append({"key": key, "series": series, "rgb": rgb, "text": text,
                           "width": cr.text_extents(text).width, "y": series["label_y"],
                           "tip": (points[-1][0], series["y"])})

        for label in labels:
            rgb, ly, width = label["rgb"], label["y"], label["width"]
            tip_x, tip_y = label["tip"]
            tx = max(self.plot_l + 4, tip_x - 8 - width) # just left of the newest sample
            if abs(ly - tip_y) > 1:
                cr.set_source_rgba(*rgb, 0.4)
                cr.set_line_width(1)
                cr.move_to(tip_x, tip_y)
                cr.line_to(tx + width + 4, ly + 2)
                cr.stroke()
            cr.set_source_rgba(0, 0, 0, 0.45) # a dark rounded plate keeps the name readable
            rounded_rect(cr, tx - 5, ly - 6, width + 10, LABEL_PLATE, 5)
            cr.fill()
            cr.set_source_rgb(*rgb)
            cr.set_font_size(11)
            cr.move_to(tx, ly + 6)
            cr.show_text(label["text"])
        self._draw_table(cr, w, h, labels)

    def _draw_table(self, cr, w, h, labels):
        """The strip on the right: one row per line, in its colour, never two on the same height."""
        cr.set_font_size(10)
        cr.set_source_rgba(1, 1, 1, 0.35)
        columns = [w - 8 - 2 * NUM_COLUMN, w - 8 - NUM_COLUMN, w - 8] # right edge of min, now, max
        for heading, right in zip(("min", "now", "max"), columns):
            cr.move_to(right - cr.text_extents(heading).width, 12)
            cr.show_text(heading)
        labels = sorted(labels, key=lambda label: label["y"])
        swatch = self.plot_r + 6
        for label, ry in zip(labels, spread([label["y"] for label in labels], NUM_ROW, 24, h - 6)):
            series, rgb = label["series"], label["rgb"]
            if abs(ry - label["y"]) > 2: # a leader back to the label, once the row has moved off it
                cr.set_source_rgba(*rgb, 0.25)
                cr.set_line_width(1)
                cr.move_to(self.plot_r - 2, label["y"] + 1)
                cr.line_to(swatch, ry + 1)
                cr.stroke()
            cr.set_source_rgba(*rgb, 0.9) # the row carries the line's colour, so the two pair up
            cr.set_line_width(2.5)
            cr.move_to(swatch, ry + 1)
            cr.line_to(swatch + 8, ry + 1)
            cr.stroke()
            for value, right, dim in zip((series["lo"], series["value"], series["hi"]), columns,
                                         (True, False, True)):
                number = fmt_si(value)
                cr.set_source_rgba(*rgb, 0.5 if dim else 1)
                cr.move_to(right - cr.text_extents(number).width, ry + 4)
                cr.show_text(number)
            self.label_hits.append((self.plot_r, w, ry - 6, ry + 6, label["key"]))

    def _draw_time_axis(self, cr, h):
        """Clock ticks along the bottom, so a spike on the chart can be found in the log."""
        cr.set_font_size(9)
        step = 30
        marks = [t for t in (math.ceil((self.now - self.span) / step) * step + i * step
                             for i in range(int(self.span / step) + 2)) if t <= self.now]
        for t in marks:
            x = self._x(t)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.set_line_width(1)
            cr.move_to(x, 0)
            cr.line_to(x, h - TIME_AXIS)
            cr.stroke()
            text = datetime.fromtimestamp(t).strftime("%H:%M:%S")
            cr.set_source_rgba(1, 1, 1, 0.4)
            cr.move_to(min(x + 3, self.plot_r - cr.text_extents(text).width), h - 4)
            cr.show_text(text)

    def _draw_events(self, cr, height):
        """A stem timeline of its own, so warnings do not stripe the metric chart."""
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.rectangle(0, 0, self.plot_r, height)
        cr.fill()
        cr.set_font_size(9)
        cr.set_source_rgba(1, 1, 1, 0.3)
        cr.move_to(4, 12)
        cr.show_text("events")
        base = height - 6
        cap = 42 # dots sit here; names live in the rows above
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        cr.move_to(0, base)
        cr.line_to(self.plot_r, base)
        cr.stroke()
        rows = [[] for _ in range(3)] # x ranges already spoken for, per row of names
        for when, text, color in self.events:
            if when < self.now - self.span:
                continue
            x = self._x(when)
            rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
            cr.set_source_rgb(*rgb)
            cr.set_line_width(1.2)
            cr.move_to(x, base)
            cr.line_to(x, cap)
            cr.stroke()
            cr.arc(x, cap, 2.6, 0, 6.2832)
            cr.fill()
            text = text[:26]
            width = cr.text_extents(text).width + 8
            x0 = min(x + 5, self.plot_r - width)
            if x0 < 46: # leave the band name alone
                continue
            for i, taken in enumerate(rows):
                if all(x0 >= end or x0 + width <= start for start, end in taken):
                    taken.append((x0, x0 + width))
                    cr.move_to(x0, 13 + i * 12)
                    cr.show_text(text)
                    break


def ts_seconds(ts):
    """Firmware timestamp HH:MM:SS.mmm as seconds."""
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def field_label(text, drop_unit=""):
    """Label for a numeric field: the words right before it, minus the previous field's unit."""
    if drop_unit:
        text = re.sub(r"^\s*" + re.escape(drop_unit) + r"\b", "", text)
    words = re.sub(r"%%", "%", re.sub(r"[^\w%/ -]", " ", text)).split()
    return " ".join(words[-3:])[:22] or "?"


def field_unit(text):
    """Unit right after a number: the first word, if it looks like one."""
    m = re.match(r"\s*([A-Za-z%/]{1,6})\b", text)
    return m.group(1) if m else ""


def pattern_of(plain):
    """Mute key for a log line: module, location and message with every number replaced by #."""
    m = LOG_LINE.match(plain.rstrip("\r\n"))
    body = f"{m.group(3)} {m.group(4)} {m.group(5)}" if m else plain.strip()
    return NUMBERS.sub("#", body)


SGR_COLORS = {
    30: "#3b4252", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b", 34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#c8ccd4",
    90: "#7f848e", 91: "#ef8a8a", 92: "#b5e890", 93: "#f0d38a", 94: "#8ac4ff", 95: "#dc9bf0", 96: "#7ad4df", 97: "#ffffff",
}


def camera_needs(device):
    """What is missing before there can be a picture, and whether it can be installed here."""
    missing = []
    if Gst is None:
        missing.append("the GStreamer bindings for Python")
    else:
        Gst.init_check(None)
        for element, what in (("v4l2src", "the v4l2 camera source"),
                              ("videoconvert", "the video format converter")):
            if Gst.ElementFactory.find(element) is None:
                missing.append(what)
    if not Path(device).exists():
        missing.append(f"a camera at {device}")
    manager = package_manager()
    # Only software can be installed. A camera that is not plugged in is not a package.
    software = [need for need in missing if not need.startswith("a camera")]
    return missing, manager if software else None


def send(action, **payload):
    cmd = ["noctalia", "msg", "plugin", PLUGIN, "all", action]
    if payload:
        cmd.append(json.dumps(payload))
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass # no noctalia here, so there is no ST-Link backend to talk to either


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_settings(**values):
    current = read_json(SETTINGS) or {}
    current.update(values)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(current))


class SerialReader(threading.Thread):
    """Reads the first ttyACM/ttyUSB port, logs to a file and hands lines to the UI.

    Reconnects on its own, so a reflash that re-enumerates the port just shows a gap.
    """

    def __init__(self, baud, logdir, on_line):
        super().__init__(daemon=True)
        self.baud, self.logdir, self.on_line = baud, Path(logdir), on_line
        self.port = "" # not None, so the first look with nothing plugged in still says so
        self.warned = False

    def run(self):
        while True:
            ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
            if not ports:
                if self.port is not None: # said once, not once a second until something turns up
                    self._emit("(no serial port)\n", status="No port")
                    self.port = None
                time.sleep(1)
                continue
            self.port = ports[0]
            try:
                self._pump()
            except (serial.SerialException, OSError) as exc:
                self._emit(f"(port gone: {exc})\n", status="Disconnected")
                if "Permission denied" in str(exc) and not self.warned:
                    self.warned = True # the usual first-run trip-up on a distribution that is not NixOS
                    self._emit(f"(reading {self.port} needs the dialout group: "
                               f"sudo usermod -aG dialout $USER, then log out and in)\n")
                time.sleep(1)

    def _pump(self):
        self.logdir.mkdir(parents=True, exist_ok=True)
        logfile = self.logdir / f"{datetime.now():%Y%m%d-%H%M%S}-{Path(self.port).name}.log"
        with serial.Serial(self.port, self.baud, timeout=0.2) as ser, logfile.open("ab") as log:
            # Only the subtitle says where we are connected; the log itself stays firmware output.
            self._emit("", status=f"{self.port} @ {self.baud} → {logfile.name}")
            while True:
                data = ser.read(4096)
                if data:
                    log.write(data)
                    log.flush()
                    self._emit(data.decode("utf-8", "replace"))

    def _emit(self, text, status=None):
        GLib.idle_add(self.on_line, text, status)


class Window(Adw.ApplicationWindow):
    def __init__(self, app, args):
        super().__init__(application=app, title="EyeBuddy", default_width=1400, default_height=900)
        # The chart is drawn as white on dark, and the key letters are white, so the window asks
        # for the dark scheme rather than taking whatever the desktop happens to prefer.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.args = args
        self.settings = read_json(SETTINGS) or {}
        self.rotation = self.settings.get("rotation", args.rotate) % 360
        self.angle_save = None # pending write of the angle, so a dragged slider writes once
        # Without GStreamer or a camera node there is no picture, and the window is log and chart.
        self.camera = Gst is not None and not args.no_camera and Path(args.device).exists()
        self.modes = camera_modes(args.device) if self.camera else CAMERA_SIZES
        self.size = self.settings.get("size", self.modes[0][0])
        self.pipeline = None
        self.build_type = self.settings.get("build_type", "debug")
        self.build_env = self.settings.get("build_env", "production")
        self.job_active = False
        self.stlink = shutil.which("noctalia") is not None # the ST-Link backend, absent elsewhere
        self.mcu_state = None
        self.mcu_text = ""
        self._build_ui()
        self._start_camera()
        self.serial = SerialReader(args.baud, args.logdir, self._on_serial)
        self.serial.start()
        GLib.timeout_add(1000, self._poll_state)
        GLib.timeout_add(500, self._poll_job)
        GLib.timeout_add(1000, self._update_axis)
        GLib.timeout_add(5000, lambda: (self._update_outliers(), True)[1])
        GLib.timeout_add(2000, self._update_ranges)
        GLib.timeout_add(5000, self._flush_ranges)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # --- UI -----------------------------------------------------------------

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        header = Adw.HeaderBar()
        self.status_label = Gtk.Label(label="…", css_classes=["dim-label"])
        header.set_title_widget(Adw.WindowTitle(title="EyeBuddy", subtitle=""))
        self.title_widget = header.get_title_widget()
        root.append(header)

        # Log on top takes whatever is left, the bottom keeps its natural height so the whole
        # camera image and every button always stay visible.
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        root.append(body)

        # Bottom half: a vertical button column on the left, the camera filling the rest.
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=8, margin_start=8, margin_end=8, vexpand=False)
        self.view = CameraView()
        self.view.add_css_class("card")
        self.view.set_visible(self.camera)

        self.textview = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, can_focus=False)
        self.textview.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.buffer = self.textview.get_buffer()
        scroller = Gtk.ScrolledWindow(child=self.textview, hexpand=True, vexpand=True)
        self.scroller = scroller
        self.serial_state = "" # shown in the window subtitle next to the MCU state
        # Muted patterns as chips, each a button that unmutes its pattern.
        self.muted = set(self.settings.get("muted", []))
        self.mute_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                                    row_spacing=0, column_spacing=2, margin_start=6, margin_end=6)
        self.mute_box.set_visible(False)
        css = Gtk.CssProvider()
        rules = """.telemetry { padding: 1px 6px; min-height: 0; }
            .ranges label { font-size: 0.78em; padding: 0; }"""
        if hasattr(css, "load_from_string"): # load_from_string is GTK 4.12, load_from_data is older
            css.load_from_string(rules)
        else:
            css.load_from_data(rules.encode())
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.rows = {}   # pattern -> series state
        self.seen = {}   # pattern -> occurrences before it earns a place on the chart
        self.ranges = self.settings.get("ranges", {}) # pattern -> [[min, max], ...], kept between runs
        self.stats = {} # (pattern, field) -> [n, mean, m2, last_warning] for spotting outliers
        # Shared time axis: every graph spans the same session, so one line says it for all of them.
        self.axis = Gtk.Label(xalign=1, css_classes=["dim-label", "caption"], margin_end=8)
        self.session_start = None
        self.graph = MultiGraph(on_click=self._mute_series)
        self.module_hue = {} # module -> its hue on the chart, handed out as modules turn up
        # Outliers worth a second look, listed only while there are any.
        self.outliers = collections.deque(maxlen=OUTLIER_SHOWN)
        self.outlier_label = Gtk.Label(xalign=0, use_markup=True, margin_start=8, margin_end=8,
                                       css_classes=["caption"], visible=False)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(self.mute_box)
        top.append(self.graph) # the chart draws the table itself, so the rows line up with the lines
        top.append(self.outlier_label)
        top.append(self.axis)
        top.append(scroller)
        self._rebuild_mute_chips()
        # Follow mode: new lines glide the view to the end, but only while it already sits there.
        self.follow = True
        self.gliding = False
        self.tick_id = None
        self.last_value = None
        adj = self.scroller.get_vadjustment()
        adj.connect("value-changed", self._on_scrolled)
        adj.connect("notify::upper", lambda *_: self.follow and self._scroll_to_end())
        self.lines = collections.deque(maxlen=5000) # complete raw lines, colours included
        self.partial = ""
        self.shown = 0
        self.pretty = self.settings.get("pretty", True)
        self.recent = collections.OrderedDict() # pattern -> the line it was last rendered on
        self._refilter()
        top.set_vexpand(True)
        body.append(top)
        body.append(bottom)
        self.sgr_fg = None
        self.sgr_bold = False
        self.tags = {}

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
        bottom.append(controls)
        bottom.append(self.view)
        # Every action is a key, so the column is a list of them rather than a column of buttons.
        self.keys_label = Gtk.Label(xalign=0, use_markup=True, css_classes=["caption"])
        self.angle_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 359, 1)
        self.angle_scale.set_value(self.rotation)
        self.angle_scale.set_draw_value(False)
        self.angle_scale.set_visible(self.camera)
        for mark in (0, 90, 180, 270):
            self.angle_scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.angle_scale.connect("value-changed", lambda scale: self.set_angle(scale.get_value()))
        self.view.set_angle(self.rotation) # the saved angle, which no slider movement has announced yet
        self.job_label = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=24,
                                   css_classes=["dim-label", "caption"])
        self.progress = Gtk.ProgressBar()
        for widget in (self.keys_label, self.angle_scale, self.job_label, self.progress):
            controls.append(widget)
        self._update_keys()

    def _keyed(self, key, name, dim=False):
        """Label with the shortcut key picked out in white, so the binding reads as part of it."""
        text = GLib.markup_escape_text(name)
        if dim: # a key that has no backend to talk to here, left visible but plainly not live
            return f'<span alpha="40%"><b>{key}</b>  {text}</span>'
        return f'<span foreground="#ffffff"><b>{key}</b></span>  {text}'

    def _choice(self, key, off_name, on_name, active):
        """Both sides of a toggle, the one in force lit and the other dimmed."""
        lit = lambda text, on: text if on else f'<span alpha="45%">{GLib.markup_escape_text(text)}</span>'
        return self._keyed(key, "") + f"{lit(off_name, not active)} / {lit(on_name, active)}"

    def _update_keys(self):
        """The key column, rewritten whenever one of the states it shows has moved."""
        width, height = self.size.split("x")
        fps = dict(self.modes).get(self.size, "?")
        halted = getattr(self, "mcu_state", None) == "halted"
        away = not self.stlink # no plugin to send to, so those keys do nothing here
        lines = [
            self._keyed("S", "Resume" if halted else "Halt", away),
            self._keyed("R", "Reset", away),
            self._keyed("H", "Reset + halt", away),
            "",
            *([self._keyed("K", "Camera")] if not self.camera else []),
            *([self._keyed("Q", f"Turn a quarter · {self.rotation}°"),
               self._keyed(",  .", "Turn one degree"),
               self._keyed("Z", f"{width}×{height} @ {fps} fps"),
               ""] if self.camera else []),
            self._choice("D", "Debug", "Release", self.build_type == "release"),
            self._choice("E", "Staging", "Production", self.build_env == "production"),
            self._keyed("B", "Build", away),
            self._keyed("F", "Flash", away),
            self._keyed("A", "Build + flash", away),
            self._keyed("O", "Log", away),
            "",
            self._keyed("C", "Clear log"),
            self._keyed("G", "Follow the end"),
            self._keyed("V", "Raw or pretty"),
            self._keyed("M", "Mute last line"),
            self._keyed("U", "Unmute every line"),
        ]
        self.keys_label.set_markup("\n".join(lines))

    # --- camera -------------------------------------------------------------

    def _start_camera(self):
        if not self.camera:
            return
        Gst.init(None)
        w, h = self.size.split("x")
        # The frames come back as plain buffers and the view turns them into textures itself, which
        # is what lets the picture sit at any angle and asks nothing of the archive but base plugins.
        try:
            self.pipeline = Gst.parse_launch(
                f"v4l2src name=src device={self.args.device} ! video/x-raw,width={w},height={h} "
                f"! queue max-size-buffers=1 leaky=downstream ! videoconvert "
                # Caps the frame height, so the picture's natural size stays bounded.
                f"! videoscale ! video/x-raw,height={CAMERA_HEIGHT} "
                f"! videoconvert ! video/x-raw,format=RGBA "
                f"! appsink name=view emit-signals=true max-buffers=1 drop=true sync=false"
            )
        except GLib.Error as error:
            self._no_camera(error.message)
            return
        self.pipeline.get_by_name("view").connect("new-sample", self._on_view_frame)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _no_camera(self, why):
        """Give up on the picture without giving up on the window."""
        self.camera = False
        self.pipeline = None
        self.view.set_visible(False)
        self._update_keys()
        self._note(f"No camera: {why}")

    def _on_view_frame(self, sink):
        """One RGBA frame into a texture the view can draw at whatever angle it is set to."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = GLib.Bytes.new(info.data)
        finally:
            buffer.unmap(info)
        width, height = caps.get_value("width"), caps.get_value("height")
        texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, data, width * 4)
        GLib.idle_add(self.view.set_frame, texture)
        return Gst.FlowReturn.OK

    def offer_camera(self):
        """Say what the picture is missing, and offer to install the part that is a package."""
        if self.camera:
            return
        missing, manager = camera_needs(self.args.device)
        if not missing:
            self.camera = True # everything is in place now, so bring the picture up
            self.view.set_visible(True)
            self._start_camera()
            self._update_keys()
            return
        body = "The picture needs " + ", ".join(missing) + "."
        # AlertDialog only arrived in libadwaita 1.5, and older distributions are the ones most
        # likely to be missing the camera pieces in the first place.
        modern = hasattr(Adw, "AlertDialog")
        dialog = (Adw.AlertDialog(heading="No camera", body=body) if modern
                  else Adw.MessageDialog(heading="No camera", body=body, transient_for=self))
        dialog.add_response("close", "Close")
        if manager:
            dialog.add_response("install", "Install")
            dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: response == "install" and self._install_camera(manager))
        dialog.present(self) if modern else dialog.present()

    def _install_camera(self, manager):
        """Run the install off the main loop, so the window keeps drawing while it works."""
        self._note(f"Installing the camera packages with {manager}…")

        def work():
            done = install_packages(manager, PACKAGES[manager]["camera"])
            GLib.idle_add(self._note, "Camera packages installed, restart EyeBuddy to use them."
                          if done else "That install did not go through.")
        threading.Thread(target=work, daemon=True).start()

    def next_size(self):
        if not self.camera or self.pipeline is None:
            return
        sizes = [size for size, _ in self.modes]
        index = sizes.index(self.size) + 1 if self.size in sizes else 0
        self.size = sizes[index % len(sizes)]
        self._update_keys()
        save_settings(size=self.size)
        self.pipeline.set_state(Gst.State.NULL) # caps on the source need a full renegotiation
        self._start_camera()

    def rotate(self, delta):
        self.set_angle(self.rotation + delta)

    def set_angle(self, angle):
        """Any angle, not just the quarter turns a video filter can do."""
        self.rotation = round(angle) % 360
        self.view.set_angle(self.rotation)
        if round(self.angle_scale.get_value()) != self.rotation:
            self.angle_scale.set_value(self.rotation)
        self._update_keys()
        if self.angle_save is None: # a dragged slider would write the file on every step
            self.angle_save = GLib.timeout_add(600, self._save_angle)

    def _save_angle(self):
        self.angle_save = None
        save_settings(rotation=self.rotation)
        return False

    # --- serial -------------------------------------------------------------

    def _tag(self):
        key = (self.sgr_fg, self.sgr_bold)
        if key not in self.tags:
            tag = self.buffer.create_tag(None)
            if self.sgr_fg:
                tag.set_property("foreground", self.sgr_fg)
            if self.sgr_bold:
                tag.set_property("weight", 700)
            self.tags[key] = tag
        return self.tags[key]

    def _apply_sgr(self, params):
        for code in (int(p) for p in params.split(";") if p.isdigit()) or [0]:
            if code == 0:
                self.sgr_fg, self.sgr_bold = None, False
            elif code == 1:
                self.sgr_bold = True
            elif code == 22:
                self.sgr_bold = False
            elif code == 39:
                self.sgr_fg = None
            elif code in SGR_COLORS:
                self.sgr_fg = SGR_COLORS[code]

    def _insert_ansi(self, text):
        pos = 0
        for m in ANSI.finditer(text):
            if m.start() > pos:
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), text[pos:m.start()], self._tag())
            if m.group(2) == "m":
                self._apply_sgr(m.group(1))
            pos = m.end()
        if pos < len(text):
            self.buffer.insert_with_tags(self.buffer.get_end_iter(), text[pos:], self._tag())

    def _style(self, fg=None, bold=False, scale=1.0):
        key = ("style", fg, bold, scale)
        if key not in self.tags:
            tag = self.buffer.create_tag(None)
            if fg:
                tag.set_property("foreground", fg)
            if bold:
                tag.set_property("weight", 700)
            if scale != 1.0:
                tag.set_property("scale", scale)
            self.tags[key] = tag
        return self.tags[key]

    def _module_color(self, raw, module):
        # The firmware colours the module tag itself. Fall back to a stable hash colour.
        m = re.search(r"\x1b\[(?:1;)?(3[0-7]|9[0-7])m" + re.escape(module), raw)
        if m:
            return SGR_COLORS[int(m.group(1))]
        return list(SGR_COLORS.values())[hash(module) % 8]

    def _render_pretty(self, raw):
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m:
            self.recent.clear()
            self._insert_ansi(raw)
            return
        ts, level, module, location, message = m.groups()
        key = pattern_of(plain)
        now = ts_seconds(ts)
        if key in self.recent:
            # Seen among the last lines (they often interleave, e.g. an error and its follow-up):
            # rewrite that line in place with the latest values, a count and the arrival rate.
            state = self.recent[key]
            state["count"] += 1
            if 0 < now - state["ts"] < 3600:
                state["gaps"].append(now - state["ts"])
            state["ts"] = now
            start = self.buffer.get_iter_at_mark(state["mark"])
            end = start.copy()
            end.forward_to_line_end()
            self.buffer.delete(start, end)
            self._insert_pretty(self.buffer.get_iter_at_mark(state["mark"]), raw, m.groups(),
                                state["count"], state["gaps"])
            return
        mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
        self._insert_pretty(self.buffer.get_end_iter(), raw, m.groups(), 1, [])
        self.buffer.insert(self.buffer.get_end_iter(), "\n")
        self.recent[key] = {"mark": mark, "count": 1, "ts": now, "gaps": []}
        while len(self.recent) > RECENT_LINES:
            _, old = self.recent.popitem(last=False)
            self.buffer.delete_mark(old["mark"])

    def _insert_pretty(self, it, raw, fields, count, gaps):
        ts, level, module, location, message = fields
        glyph, color = LEVELS.get(level.upper(), ("·", "#7f848e"))
        loud = level.upper() in ("WARN", "WARNING", "ERROR", "FATAL")
        parts = [
            (ts[3:] + " ", self._style("#7f848e", scale=0.85)),
            (glyph + " ", self._style(color, bold=True)),
            (f"{module:<4} ", self._style(self._module_color(raw, module), bold=True)),
            (message, self._style(color if loud else None)),
        ]
        if count > 1:
            badge = f"  ×{count}"
            if gaps:
                mean = sum(gaps) / len(gaps)
                # Spread of the intervals: small means the line arrives on a steady beat.
                spread = (max(gaps) - min(gaps)) / mean if mean else 0
                badge += f" {1 / mean:.1f}/s {'steady' if spread < 0.25 else f'±{spread:.0%}'}"
            parts.append((badge, self._style("#e5c07b", bold=True)))
        parts.append(("  " + location, self._style("#5c6370", scale=0.8)))
        for text, tag in parts:
            self.buffer.insert_with_tags(it, text, tag)

    def toggle_pretty(self):
        self.pretty = not self.pretty
        save_settings(pretty=self.pretty)
        self._refilter()

    def _matches(self, line):
        return not (self.muted and pattern_of(ANSI.sub("", line)) in self.muted)

    def _rebuild_mute_chips(self):
        while child := self.mute_box.get_first_child():
            self.mute_box.remove(child)
        for pattern in sorted(self.muted):
            label = pattern if len(pattern) <= 28 else pattern[:27] + "…"
            chip = Gtk.Button(label=label, tooltip_text=f"{pattern}\nclick to unmute",
                              css_classes=["flat", "caption", "telemetry"])
            chip.connect("clicked", lambda _b, pat=pattern: self.unmute(pat))
            self.mute_box.append(chip)
        self.mute_box.set_visible(bool(self.muted))

    def _set_muted(self, muted):
        for pattern in muted - self.muted:
            self.graph.drop_pattern(pattern)
            self.rows.pop(pattern, None)
        self.muted = muted
        save_settings(muted=sorted(muted))
        self._rebuild_mute_chips()
        self._refilter()

    def mute_last(self):
        if self.lines:
            self._set_muted(self.muted | {pattern_of(ANSI.sub("", self.lines[-1]))})

    def unmute(self, pattern):
        self._set_muted(self.muted - {pattern})

    def unmute_all(self):
        self._set_muted(set())

    def _show_line(self, line):
        if self.pretty and self._to_table(line):
            return
        if self.pretty:
            self._render_pretty(line)
        else:
            self._insert_ansi(line)
        self.shown += 1

    # --- live table ---------------------------------------------------------

    def _to_table(self, raw):
        """Route a repeating telemetry line to the chart. Returns False for lines the log keeps."""
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m:
            return False
        level, module, message = m.group(2).upper(), m.group(3), m.group(5)
        if level in ("WARN", "WARNING", "ERROR", "FATAL"):
            self.graph.add_event(f"{module} {message}", LEVELS.get(level, ("", "#e06c75"))[1])
            return False
        key = pattern_of(plain)
        if key not in self.rows:
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] < 2:
                # Said once so far. Text without numbers is a state change worth marking; a line
                # carrying numbers is probably telemetry that will earn its own line shortly.
                if not NUMBERS.search(message):
                    self.graph.add_event(f"{module} {message}", self._module_color(raw, module))
                return False
            if self.session_start is None:
                self.session_start = time.time()
            self.rows[key] = self._make_row(key, raw, m.groups())
        self._update_row(self.rows[key], m.groups())
        return True

    def _series_color(self, module, index):
        """Hue per module, lightness per field, so one log line's fields read as a family."""
        hue = self.module_hue.setdefault(module, len(self.module_hue) * 0.618 % 1.0)
        r, g, b = colorsys.hls_to_rgb(hue, (0.70, 0.55, 0.80, 0.46)[index % 4], 0.62)
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

    def _make_row(self, key, raw, fields):
        """Register one series per numeric field. The chart labels them, so no widgets here."""
        ts, level, module, location, message = fields
        fields_out = []
        pos = ""
        unit = ""
        at = 0
        for i, num in enumerate(NUMBERS.finditer(message)):
            text = field_label(message[at:num.start()], unit)
            unit = field_unit(message[num.end():])
            series = f"{key}#{i}"
            self.graph.add_series(series, self._series_color(module, i),
                                  f"{module} {text}" if text else message[:24],
                                  polarity_of(text, unit), unit, key)
            fields_out.append({"series": series, "label": text, "unit": unit})
            at = num.end()
        # How often this line arrives, so a stream that reports steady numbers still shows up.
        rate_series = f"{key}#rate"
        self.graph.add_series(rate_series, self._series_color(module, len(fields_out)),
                              f"{module} {(fields_out[0]['label'] if fields_out else message)[:18]} rate",
                              0, "/s", key)
        fields_out.append({"series": rate_series, "label": "rate", "unit": "/s"})
        ranges = self.ranges.setdefault(key, [[None, None] for _ in fields_out])
        while len(ranges) < len(fields_out):
            ranges.append([None, None])
        return {"fields": fields_out, "n": 0, "ranges": ranges, "key": key, "module": module, "last_ts": None,
                "message": message, "numeric": len(fields_out) > 1, "rate": None}

    def _update_row(self, state, fields):
        ts, level, module, location, message = fields
        state["n"] += 1
        now = ts_seconds(ts)
        previous, state["last_ts"] = state.get("last_ts"), now
        rate_field = state["fields"][-1]
        if previous is not None and 0 < now - previous < 60:
            # Smoothed, because the jitter between two log lines says nothing on its own.
            rate = 1 / (now - previous)
            state["rate"] = rate if state.get("rate") is None else state["rate"] * 0.7 + rate * 0.3
            rate = state["rate"]
            rng = state["ranges"][-1]
            rng[0] = rate if rng[0] is None else min(rng[0], rate)
            rng[1] = rate if rng[1] is None else max(rng[1], rate)
            lo, hi = rng
            self.graph.push(rate_field["series"], (rate - lo) / (hi - lo) if hi > lo else 0.5, rate, lo, hi)
        if not state["numeric"]:
            return
        for i, (field, num, rng) in enumerate(zip(state["fields"], NUMBERS.finditer(message), state["ranges"])):
            v = float(num.group())
            if rng[0] is None or v < rng[0] or rng[1] is None or v > rng[1]:
                rng[0] = v if rng[0] is None else min(rng[0], v)
                rng[1] = v if rng[1] is None else max(rng[1], v)
                self.ranges_dirty = True
            lo, hi = rng
            if self._check_outlier(state["key"], i, v, field["label"], module):
                self.graph.flag(field["series"])
            self.graph.push(field["series"], (v - lo) / (hi - lo) if hi > lo else 0.5, v, lo, hi)

    def _flush_ranges(self):
        if getattr(self, "ranges_dirty", False):
            self.ranges_dirty = False
            save_settings(ranges=dict(list(self.ranges.items())[-200:])) # bounded, not unbounded history
        return True

    def _check_outlier(self, key, index, value, label, module):
        """Welford mean/variance per field; a value far outside it is worth saying out loud."""
        st = self.stats.setdefault((key, index), [0, 0.0, 0.0, 0.0])
        st[0] += 1
        delta = value - st[1]
        st[1] += delta / st[0]
        st[2] += delta * (value - st[1])
        if st[0] < 30:
            return False
        sigma = (st[2] / (st[0] - 1)) ** 0.5
        if sigma <= 0 or abs(value - st[1]) < OUTLIER_SIGMA * sigma:
            return False
        now = time.time()
        if now - st[3] < OUTLIER_QUIET:
            return True
        st[3] = now
        self._warn(f"{module} {label}: {value:g} (mean {st[1]:.3g} ±{sigma:.2g})")
        return True

    def _update_ranges(self):
        self.graph.set_visible(series["key"] for series in self._worth_listing())
        return True

    def _worth_listing(self):
        """The fields worth a row: still reporting, actually moving, most restless first."""
        now = time.time()
        scored = []
        for series in self.graph.series.values():
            if series["lo"] is None or series["hi"] <= series["lo"] or not series["t"]:
                continue # never seen two different values, so there is nothing to compare
            if series["t"][-1] < now - CHART_WINDOW:
                continue # gone quiet, and its line has already slid off the chart
            low, high = min(series["v"]), max(series["v"])
            scale = max(abs(high), abs(low), 1e-9)
            movement = (high - low) / scale # what it does on the chart, not the whole session
            # A rate line earns its place only when the line's pace really changes.
            if movement < (0.35 if series["key"].endswith("#rate") else 0.005):
                continue
            recent = now - series["flag"] < OUTLIER_TTL
            scored.append((movement + (10 if recent else 0), series))
        # Round-robin over the log lines they came from, so one chatty message cannot fill the
        # table with variations on itself before other messages get a row at all.
        buckets = {}
        for score, series in sorted(scored, reverse=True, key=lambda pair: pair[0]):
            buckets.setdefault(series["pattern"], []).append(series)
        chosen = []
        while len(chosen) < RANGE_ROWS and any(buckets.values()):
            for bucket in buckets.values():
                if bucket and len(chosen) < RANGE_ROWS:
                    chosen.append(bucket.pop(0))
        return chosen

    def _update_outliers(self):
        cutoff = time.time() - OUTLIER_TTL
        while self.outliers and self.outliers[0][0] < cutoff:
            self.outliers.popleft()
        self.outlier_label.set_visible(bool(self.outliers))
        if self.outliers:
            rows = "\n".join(
                f'<span foreground="#e5c07b">!</span> {GLib.markup_escape_text(text)}'
                f'<span alpha="55%"> {datetime.fromtimestamp(when):%H:%M:%S}</span>'
                for when, text in reversed(self.outliers))
            self.outlier_label.set_markup(rows)

    def _note(self, text):
        """A line from the app itself, in among the firmware's own."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.recent.clear()
        self.buffer.insert_with_tags(self.buffer.get_end_iter(), f"{stamp} · {text}\n",
                                     self._style("#56b6c2"))
        self.follow_end()
        return False

    def _warn(self, text):
        self.outliers.append((time.time(), text))
        self._update_outliers()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.recent.clear() # the warning breaks the run of collapsed lines
        self.buffer.insert_with_tags(self.buffer.get_end_iter(), f"{stamp} ▲ {text}\n",
                                     self._style("#e5c07b", bold=True))
        self.follow_end()

    def _update_axis(self):
        if self.session_start is None:
            self.axis.set_label("")
            return True
        span = int(time.time() - self.session_start)
        self.axis.set_label(f"{span // 60}m {span % 60:02d}s")
        return True

    def _mute_series(self, series_key):
        series = self.graph.series.get(series_key)
        if series:
            self._set_muted(self.muted | {series["pattern"]})

    def _clear_table(self):
        self.session_start = None
        self.rows.clear()
        self.seen.clear()
        self.graph.series.clear()
        self.graph.t0 = None

    def _refilter(self):
        self.buffer.set_text("")
        self.sgr_fg, self.sgr_bold = None, False
        self.shown = 0
        self.recent.clear()
        self._clear_table()
        for line in self.lines:
            if self._matches(line):
                self._show_line(line)
        self._update_count()
        self.follow_end()

    def _update_count(self):
        self._update_subtitle()

    def _at_end(self, adj):
        return adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40

    def _on_scrolled(self, adj):
        value = adj.get_value()
        previous, self.last_value = self.last_value, value
        if self.gliding:
            return # our own glide, not the user
        # Only scrolling back up leaves follow mode. Trimming the buffer and inserting text both
        # move the value too, and those must not stop the log from following.
        if previous is not None and value < previous - 2:
            self.follow = False
        elif self._at_end(adj):
            self.follow = True

    def _scroll_to_end(self):
        # A per-frame glide: every frame closes part of the gap, so a stream of new lines
        # only moves the target instead of restarting a jump.
        if self.tick_id is None:
            self.tick_id = self.scroller.add_tick_callback(self._glide)

    def _glide(self, _widget, _clock):
        adj = self.scroller.get_vadjustment()
        target = adj.get_upper() - adj.get_page_size()
        gap = target - adj.get_value()
        self.gliding = True
        if abs(gap) < 0.5 or not self.follow:
            adj.set_value(target if self.follow else adj.get_value())
            self.gliding = False
            self.tick_id = None
            return GLib.SOURCE_REMOVE
        adj.set_value(adj.get_value() + gap * 0.2)
        self.gliding = False
        return GLib.SOURCE_CONTINUE

    def follow_end(self):
        self.follow = True
        self._scroll_to_end()

    def clear_log(self):
        self.lines.clear()
        self.partial = ""
        self.buffer.set_text("")
        self.shown = 0
        self.recent.clear()
        self._clear_table()
        self._update_count()

    def _on_serial(self, text, status):
        if status:
            self.serial_state = status
            self._update_subtitle()
        self.partial += text
        *complete, self.partial = self.partial.split("\n")
        for line in complete:
            line += "\n"
            self.lines.append(line)
            if self._matches(line):
                self._show_line(line)
        # Keep the view bounded like the backlog so hours of logging do not grow it without end.
        if self.buffer.get_line_count() > 5000:
            self.buffer.delete(self.buffer.get_start_iter(), self.buffer.get_iter_at_line(1000)[1])
        self._update_count()
        if self.follow:
            self._scroll_to_end()
        return False

    # --- plugin state -------------------------------------------------------

    def _update_subtitle(self):
        parts = [self.mcu_text, self.serial_state]
        if self.muted:
            parts.append(f"{len(self.muted)} muted")
        self.title_widget.set_subtitle(" · ".join(p for p in parts if p))

    def _poll_state(self):
        state = read_json(DATA_DIR / "state.json")
        if state:
            self.mcu_state = state.get("state")
            if not state.get("probe"):
                text = "No ST-Link"
            elif state.get("debugger"):
                text = f"Held by {state['debugger']}"
            else:
                text = f"{state['probe']} · {STATE_TEXT.get(self.mcu_state, self.mcu_state)}"
            self.mcu_text = text
            self._update_subtitle()
            self._update_keys()
        return True

    def _poll_job(self):
        job = read_json(DATA_DIR / "job.json")
        if job:
            self.job_active = job.get("phase") in ("build", "flash")
            percent = max(0, min(100, int(job.get("percent") or 0)))
            self.progress.set_fraction(percent / 100)
            self.job_label.set_label(f"{job.get('message', '')}" + (f" · {percent}%" if self.job_active else ""))
            self.progress.remove_css_class("error")
            if job.get("phase") == "error":
                self.progress.add_css_class("error")
        return True

    # --- build --------------------------------------------------------------

    def toggle_type(self, state=None):
        state = self.build_type == "debug" if state is None else state
        self.build_type = "release" if state else "debug"
        self._update_keys()
        save_settings(build_type=self.build_type)

    def toggle_env(self, state=None):
        state = self.build_env == "staging" if state is None else state
        self.build_env = "production" if state else "staging"
        self._update_keys()
        save_settings(build_env=self.build_env)

    def build(self, mode):
        if not self.job_active:
            send("build", mode=mode, build=self.build_type, env=self.build_env)

    # --- keys ---------------------------------------------------------------

    def _on_key(self, _controller, keyval, _keycode, _state):
        key = chr(keyval).lower() if 32 <= keyval < 127 else ""
        actions = {
            "s": lambda: send("toggle"), "r": lambda: send("reset"), "h": lambda: send("reset_halt"),
            "q": lambda: self.rotate(90), "c": self.clear_log,
            "z": self.next_size, "k": self.offer_camera,
            ",": lambda: self.rotate(-1), ".": lambda: self.rotate(1),
            "g": self.follow_end, "v": self.toggle_pretty,
            "m": self.mute_last, "u": self.unmute_all,
            "d": lambda: self.toggle_type(), "e": lambda: self.toggle_env(),
            "b": lambda: self.build("build"), "f": lambda: self.build("flash"), "a": lambda: self.build("both"),
            "o": lambda: send("open_log"),
        }
        if key in actions:
            actions[key]()
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-d", "--device", default="/dev/video0")
    parser.add_argument("-r", "--rotate", type=int, default=270, help="degrees to turn the picture")
    parser.add_argument("-b", "--baud", type=int, default=2000000)
    parser.add_argument("--logdir", default="/tmp/serial-logs")
    parser.add_argument("--no-camera", action="store_true", help="log and chart only, no video")
    parser.add_argument("--install", action="store_true", help="put it on PATH and in the launcher")
    args = parser.parse_args()
    if args.install:
        install_launcher()

    app = Adw.Application(application_id="dev.uxstream.EyebudsDev")
    app.connect("activate", lambda a: Window(a, args).present())
    app.run([])


if __name__ == "__main__":
    main()
