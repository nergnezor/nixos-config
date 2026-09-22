#!/usr/bin/env python3
"""EyeBuds dev app: camera, serial log and ST-Link controls in one window.

The ST-Link noctalia plugin stays the backend. Actions go through `noctalia msg plugin`,
state comes back through the plugin's state.json / job.json.
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import cairo
import colorsys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gst, Gtk  # noqa: E402

import serial  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: camera rotation and size, mutes, raw/pretty view.
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "eyebuds-dev/settings.json"
STATE_TEXT = {
    "running": "Running", "halted": "Halted", "reset": "In reset",
    "debug-running": "Running", "unknown": "Unknown",
}
DIRECTIONS = {0: "identity", 90: "90r", 180: "180", 270: "90l"}
CAMERA_HEIGHT = 560
RECENT_LINES = 12 # how far back a repeated line is still collapsed into its earlier one
OUTLIER_SIGMA = 5 # how far from a field's mean a value has to be before it is called out
OUTLIER_QUIET = 20 # seconds before the same field may warn again
OUTLIER_TTL = 300 # seconds an outlier stays listed under the chart
OUTLIER_SHOWN = 6 # how many of them are listed at once
RANGE_ROWS = 12 # fields the range table has room to be useful about
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


SERIES_COLORS = ["#61afef", "#98c379", "#e5c07b", "#e06c75", "#c678dd", "#56b6c2",
                 "#d19a66", "#7fd1b9", "#f08cc3", "#a3be8c", "#88c0d0", "#bf8bff"]


class MultiGraph(Gtk.DrawingArea):
    """Every tracked field in one chart, each line labelled where it ends.

    x is the session (a minute at minimum), y is each field's own 0…1 range. The labels carry
    the name, the current value and the range seen, so the chart needs no legend beside it.
    """

    def __init__(self, height=200, points=400, on_click=None, labels=True):
        super().__init__(content_height=height, hexpand=True, vexpand=True)
        self.show_labels = labels
        self.points = points
        self.on_click = on_click
        self.series = {}
        self.t0 = None
        self.label_hits = [] # (y0, y1, series key) for clicks
        self.now = self.span = 0
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

    def add_series(self, key, color, label, polarity, unit, pattern, group):
        self.series[key] = {"t": [], "v": [], "color": color, "label": label, "polarity": polarity,
                            "group": group,
                            "unit": unit, "pattern": pattern, "value": None, "lo": None, "hi": None,
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
        sample = (badness, value, now, level)
        if series["worst"] is None or badness > series["worst"][0]:
            series["worst"] = sample
        if series["best"] is None or badness < series["best"][0]:
            series["best"] = sample
        series["value"], series["lo"], series["hi"] = value, lo, hi
        self.t0 = now if self.t0 is None else self.t0
        series["t"].append(now)
        series["v"].append(min(1.0, max(0.0, level)))
        cutoff = now - CHART_WINDOW
        while series["t"] and series["t"][0] < cutoff: # what leaves the window is forgotten
            series["t"].pop(0)
            series["v"].pop(0)

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
            if series["y"] is not None and series["label_y"] is not None:
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
                bead["label_y"] = min(top + height - 6, max(top + 10, bead["label_y"] + bead["label_vy"] * dt))

    def _x(self, t, w):
        """Time to x, with now at the right edge: samples drift left and off the chart."""
        return w - 1 - (self.now - t) / self.span * (w - 2)

    def _bands(self, h):
        """Every series shares one band: the whole plot."""
        return collections.defaultdict(lambda: (0.0, h))

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        plot = w
        self.label_hits = []
        if self.t0 is None:
            return
        self.now = time.time()
        self.span = CHART_WINDOW
        bands = self._bands(h)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.rectangle(0, 0, plot, h)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.set_line_width(1)
        for i in range(1, 4): # quarter lines, something for the eye to measure against
            cr.move_to(0, h * i / 4)
            cr.line_to(plot, h * i / 4)
        cr.stroke()

        cr.set_font_size(11)
        labels = []
        for key, series in self.series.items():
            if len(series["v"]) < 2:
                continue # nothing left in the window: it has slid off to the left
            top, height = bands[series["group"]]
            series["band"] = (top, height)
            rgb = tuple(int(series["color"][i:i + 2], 16) / 255 for i in (1, 3, 5))
            points = [(self._x(t, plot), top + height - 3 - v * (height - 6))
                      for t, v in zip(series["t"], series["v"])]
            cr.set_source_rgb(*rgb)
            cr.set_line_width(1.4)
            spline(cr, points)
            cr.stroke()
            for kind, fill in (("worst", True), ("best", False)):
                mark = series[kind]
                if mark and len(series["v"]) > 4 and mark[2] > self.now - self.span:
                    cr.arc(self._x(mark[2], plot), top + height - 3 - mark[3] * (height - 6), 2.5, 0, 6.2832)
                    cr.fill() if fill else cr.stroke()
            # The label sticks to the value its line last showed; the physics step keeps labels
            # from sitting on top of each other.
            series["y"] = points[-1][1]
            if series["label_y"] is None:
                series["label_y"] = series["y"]

            tip_x = points[-1][0]
            unit = f" {series['unit']}" if series["unit"] else ""
            text = (f"{'!' if time.time() - series['flag'] < 10 else ''}"
                    f"{series['group']} {series['label']} {series['value']:g}{unit}")
            width = cr.text_extents(text).width
            # Right beside the last sample, on whichever side of it the label still fits.
            tx = tip_x + 6 if tip_x + 6 + width < w - 4 else tip_x - 6 - width
            tx = max(4, min(w - 4 - width, tx))
            labels.append({"key": key, "series": series, "rgb": rgb, "text": text, "width": width,
                           "x": tx, "y": series["label_y"], "tip": (tip_x, series["y"])})

        if not self.show_labels:
            return # the table beside the chart names the lines instead
        self._separate(labels, h)
        for label in labels:
            series, rgb, tx, ly, width = (label["series"], label["rgb"], label["x"], label["y"],
                                          label["width"])
            series["label_y"] = ly # the simulation carries on from where the drawing settled
            cr.set_source_rgba(*rgb, 0.4) # a leader line from the label back to its last sample
            cr.set_line_width(1)
            cr.move_to(*label["tip"])
            cr.line_to(tx + (width + 6 if tx < label["tip"][0] else -6), ly + 2)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.10) # a dim rounded plate keeps the text readable
            rounded_rect(cr, tx - 5, ly - 6, width + 10, LABEL_PLATE, 5)
            cr.fill()
            cr.set_source_rgb(*rgb)
            cr.move_to(tx, ly + 6)
            cr.show_text(label["text"])
            self.label_hits.append((tx - 5, tx + width + 5, ly - 6, ly + LABEL_PLATE - 6, label["key"]))

    @staticmethod
    def _separate(labels, h):
        """Last word on placement: plates whose x ranges meet may never share a y range."""
        labels.sort(key=lambda label: label["y"])
        for i, label in enumerate(labels):
            for other in labels[:i]:
                if label["x"] > other["x"] + other["width"] + 10 or other["x"] > label["x"] + label["width"] + 10:
                    continue # side by side, so their heights do not matter
                label["y"] = max(label["y"], other["y"] + LABEL_PLATE + 2)
        overflow = max((label["y"] + LABEL_PLATE - h for label in labels), default=0)
        if overflow > 0: # ran out of room at the bottom, so lift the whole stack
            for label in labels:
                label["y"] = max(8, label["y"] - overflow)


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


def send(action, **payload):
    cmd = ["noctalia", "msg", "plugin", PLUGIN, "all", action]
    if payload:
        cmd.append(json.dumps(payload))
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        self.port = None

    def run(self):
        while True:
            ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
            if not ports:
                self._emit("(no serial port)\n", status="No port")
                time.sleep(1)
                continue
            self.port = ports[0]
            try:
                self._pump()
            except (serial.SerialException, OSError) as exc:
                self._emit(f"(port gone: {exc})\n", status="Disconnected")
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
        super().__init__(application=app, title="EyeBuds dev", default_width=1400, default_height=900)
        self.args = args
        self.settings = read_json(SETTINGS) or {}
        self.rotation = self.settings.get("rotation", args.rotate)
        self.modes = camera_modes(args.device)
        self.size = self.settings.get("size", self.modes[0][0])
        self.build_type = self.settings.get("build_type", "debug")
        self.build_env = self.settings.get("build_env", "production")
        self.job_active = False
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
        header.set_title_widget(Adw.WindowTitle(title="EyeBuds dev", subtitle=""))
        self.title_widget = header.get_title_widget()
        root.append(header)

        # Log on top takes whatever is left, the bottom keeps its natural height so the whole
        # camera image and every button always stay visible.
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        root.append(body)

        # Bottom half: a vertical button column on the left, the camera filling the rest.
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=8, margin_start=8, margin_end=8, vexpand=False)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=False)
        self.picture.add_css_class("card")

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
        css.load_from_string(""".telemetry { padding: 1px 6px; min-height: 0; }
            .ranges label { font-size: 0.78em; padding: 0; }""")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.rows = {}   # pattern -> series state
        self.seen = {}   # pattern -> occurrences before it earns a place on the chart
        self.ranges = self.settings.get("ranges", {}) # pattern -> [[min, max], ...], kept between runs
        self.stats = {} # (pattern, field) -> [n, mean, m2, last_warning] for spotting outliers
        # Shared time axis: every graph spans the same session, so one line says it for all of them.
        self.axis = Gtk.Label(xalign=1, css_classes=["dim-label", "caption"], margin_end=8)
        self.session_start = None
        self.graph = MultiGraph(on_click=self._mute_series, labels=False)
        self.next_color = 0
        # Every series' range in an aligned grid, so the chart only has to carry current values.
        self.range_grid = Gtk.Grid(column_spacing=6, row_spacing=0, margin_start=6, margin_end=6,
                                   margin_top=4, valign=Gtk.Align.START, css_classes=["ranges"],
                                   hexpand=False)
        # Outliers worth a second look, listed only while there are any.
        self.outliers = collections.deque(maxlen=OUTLIER_SHOWN)
        self.outlier_label = Gtk.Label(xalign=0, use_markup=True, margin_start=8, margin_end=8,
                                       css_classes=["caption"], visible=False)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(self.mute_box)
        # The table is the chart's legend, so they sit side by side and share colours and rows.
        chart_row = Gtk.Box()
        chart_row.append(self.graph)
        chart_row.append(self.range_grid)
        top.append(chart_row)
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
        bottom.append(self.picture)

        row1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.btn_toggle = self._button(row1, "S", "Halt", lambda *_: send("toggle"))
        self._button(row1, "R", "Reset", lambda *_: send("reset"))
        self._button(row1, "H", "Reset + halt", lambda *_: send("reset_halt"))
        row1.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        self._button(row1, "Q", "Rotate camera", lambda *_: self.rotate(90))
        labels = [f"{w}×{h} @ {fps} fps" for (size, fps) in self.modes for (w, h) in [size.split("x")]]
        self.size_combo = Gtk.DropDown.new_from_strings(labels)
        sizes = [size for size, _ in self.modes]
        self.size_combo.set_selected(sizes.index(self.size) if self.size in sizes else 0)
        self.size_combo.connect("notify::selected", self._on_size_changed)
        row1.append(self.size_combo)
        controls.append(row1)

        row2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row2.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        row2.append(Gtk.Label(label="Build", xalign=0, css_classes=["dim-label", "caption"]))
        self.btn_type = self._switch(row2, "D", "Debug", "Release",
                                     self.build_type == "release", self.toggle_type)
        self.btn_env = self._switch(row2, "E", "Staging", "Production",
                                    self.build_env == "production", self.toggle_env)
        row2.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        self.btn_build = self._button(row2, "B", "Build", lambda *_: self.build("build"))
        self.btn_flash = self._button(row2, "F", "Flash", lambda *_: self.build("flash"))
        self.btn_both = self._button(row2, "A", "Build + flash", lambda *_: self.build("both"),
                                     css="suggested-action")
        self._button(row2, "O", "Log", lambda *_: send("open_log"))
        controls.append(row2)

        self.job_label = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=24, css_classes=["dim-label", "caption"])
        self.progress = Gtk.ProgressBar()
        controls.append(self.job_label)
        controls.append(self.progress)

    def _keyed(self, key, name):
        """Label with the shortcut key picked out in white, so the binding reads as part of it."""
        return f'<span foreground="#ffffff"><b>{key}</b></span>  {GLib.markup_escape_text(name)}'

    def _switch(self, box, key, off_name, on_name, active, handler):
        """Both choices flank the switch, the active one lit, with the shortcut key on the left."""
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=f'<span foreground="#ffffff"><b>{key}</b></span>', use_markup=True))
        off = Gtk.Label(label=off_name, xalign=1, hexpand=True)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        on = Gtk.Label(label=on_name, xalign=0, hexpand=True)
        switch.connect("state-set", lambda _s, state: handler(state))

        def light(*_):
            for label, lit in ((off, not switch.get_active()), (on, switch.get_active())):
                label.set_css_classes([] if lit else ["dim-label"])
        switch.connect("notify::active", light)
        light()
        for widget in (off, switch, on):
            row.append(widget)
        box.append(row)
        return switch

    def _button(self, box, key, name, handler, css=None):
        btn = Gtk.Button(child=Gtk.Label(label=self._keyed(key, name), use_markup=True, xalign=0,
                                         hexpand=True))
        if css:
            btn.add_css_class(css)
        btn.connect("clicked", handler)
        box.append(btn)
        return btn

    # --- camera -------------------------------------------------------------

    def _start_camera(self):
        Gst.init(None)
        w, h = self.size.split("x")
        self.pipeline = Gst.parse_launch(
            f"v4l2src name=src device={self.args.device} ! video/x-raw,width={w},height={h} "
            f"! queue max-size-buffers=1 leaky=downstream ! videoconvert "
            f"! videoflip name=flip video-direction={DIRECTIONS[self.rotation]} "
            # Caps the frame height so the picture's natural size, and with it the bottom part, stays bounded.
            f"! videoscale ! video/x-raw,height={CAMERA_HEIGHT} "
            f"! gtk4paintablesink name=sink"
        )
        self.picture.set_paintable(self.pipeline.get_by_name("sink").props.paintable)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _on_size_changed(self, combo, _param):
        self.size = self.modes[combo.get_selected()][0]
        save_settings(size=self.size)
        self.pipeline.set_state(Gst.State.NULL) # caps on the source need a full renegotiation
        self._start_camera()

    def rotate(self, delta):
        self.rotation = (self.rotation + delta) % 360
        self.pipeline.get_by_name("flip").set_property("video-direction", DIRECTIONS[self.rotation])
        save_settings(rotation=self.rotation)

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
        if not m or m.group(2).upper() in ("WARN", "WARNING", "ERROR", "FATAL"):
            return False
        key = pattern_of(plain)
        if key not in self.rows:
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] < 2:
                return False
            if self.session_start is None:
                self.session_start = time.time()
            self.rows[key] = self._make_row(key, raw, m.groups())
        self._update_row(self.rows[key], m.groups())
        return True

    def _make_row(self, key, raw, fields):
        """Register one series per numeric field. The chart labels them, so no widgets here."""
        ts, level, module, location, message = fields
        color_of = self._module_color(raw, module)
        fields_out = []
        pos = ""
        unit = ""
        at = 0
        for i, num in enumerate(NUMBERS.finditer(message)):
            text = field_label(message[at:num.start()], unit)
            unit = field_unit(message[num.end():])
            series = f"{key}#{i}"
            color = SERIES_COLORS[self.next_color % len(SERIES_COLORS)]
            self.next_color += 1
            self.graph.add_series(series, color, text or message[:24],
                                  polarity_of(text, unit), unit, key, module)
            fields_out.append({"series": series, "label": text, "unit": unit})
            at = num.end()
        ranges = self.ranges.setdefault(key, [[None, None] for _ in fields_out])
        while len(ranges) < len(fields_out):
            ranges.append([None, None])
        return {"fields": fields_out, "n": 0, "ranges": ranges, "key": key, "module": module,
                "message": message, "numeric": bool(fields_out)}

    def _update_row(self, state, fields):
        ts, level, module, location, message = fields
        state["n"] += 1
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
        entries = self._worth_listing()
        while child := self.range_grid.get_first_child():
            self.range_grid.remove(child)
        if not entries:
            return True
        # Name, min, now, max per entry, in as many columns as the width allows, so the block
        # stays a few rows tall however many fields the firmware reports.
        # A third of the window at most: the chart is what this row is for.
        self.range_grid.set_size_request(self.get_width() // 3, -1)
        for offset, heading in ((1, "min"), (2, "now"), (3, "max")):
            self.range_grid.attach(Gtk.Label(label=heading, xalign=1, width_chars=6,
                                             css_classes=["caption", "dim-label"]), offset, 0, 1, 1)
        for row, series in enumerate(entries, start=1):
            flagged = "! " if time.time() - series["flag"] < 10 else ""
            name = Gtk.Label(xalign=0, use_markup=True, ellipsize=3, max_width_chars=20,
                             css_classes=["caption"], tooltip_text="click to mute")
            name.set_markup(f'<span foreground="{series["color"]}">{flagged}'
                            f'{GLib.markup_escape_text(series["group"] + " " + series["label"])}</span>')
            cells = [name]
            for value, dim in ((series["lo"], True), (series["value"], False), (series["hi"], True)):
                classes = ["caption", "monospace"] + (["dim-label"] if dim else [])
                cells.append(Gtk.Label(label=f"{value:g}", xalign=1, width_chars=6, css_classes=classes))
            for column, cell in enumerate(cells):
                self.range_grid.attach(cell, column, row, 1, 1)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_, s=series: self._set_muted(self.muted | {s["pattern"]}))
            name.add_controller(click)
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
            scale = max(abs(series["hi"]), abs(series["lo"]), 1e-9)
            spread = (series["hi"] - series["lo"]) / scale
            if spread < 0.02:
                continue # near enough constant to be noise in a table
            recent = now - series["flag"] < OUTLIER_TTL
            scored.append((spread + (10 if recent else 0), series))
        scored.sort(reverse=True, key=lambda pair: pair[0])
        return [series for _, series in scored[:RANGE_ROWS]]

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
            halted = self.mcu_state == "halted"
            self.btn_toggle.get_child().set_label(self._keyed("S", "Resume" if halted else "Halt"))
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
        for btn in (self.btn_build, self.btn_flash, self.btn_both):
            btn.set_sensitive(not self.job_active)
        return True

    # --- build --------------------------------------------------------------

    def toggle_type(self, state=None):
        state = not self.btn_type.get_active() if state is None else state
        self.build_type = "release" if state else "debug"
        self.btn_type.set_active(state)
        save_settings(build_type=self.build_type)

    def toggle_env(self, state=None):
        state = not self.btn_env.get_active() if state is None else state
        self.build_env = "production" if state else "staging"
        self.btn_env.set_active(state)
        save_settings(build_env=self.build_env)

    def build(self, mode):
        if not self.job_active:
            send("build", mode=mode, build=self.build_type, env=self.build_env)

    # --- keys ---------------------------------------------------------------

    def _on_key(self, _controller, keyval, _keycode, _state):
        key = chr(keyval).lower() if 32 <= keyval < 127 else ""
        actions = {
            "s": lambda: send("toggle"), "r": lambda: send("reset"), "h": lambda: send("reset_halt"),
            "q": lambda: self.rotate(90), "c": self.clear_log, "g": self.follow_end, "v": self.toggle_pretty,
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
    parser.add_argument("-r", "--rotate", type=int, default=270, choices=[0, 90, 180, 270])
    parser.add_argument("-b", "--baud", type=int, default=2000000)
    parser.add_argument("--logdir", default="/tmp/serial-logs")
    args = parser.parse_args()

    app = Adw.Application(application_id="dev.uxstream.EyebudsDev")
    app.connect("activate", lambda a: Window(a, args).present())
    app.run([])


if __name__ == "__main__":
    main()
