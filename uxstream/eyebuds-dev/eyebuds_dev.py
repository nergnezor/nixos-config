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
    "running": "Kör", "halted": "Stoppad", "reset": "I reset",
    "debug-running": "Kör", "unknown": "Okänd",
}
DIRECTIONS = {0: "identity", 90: "90r", 180: "180", 270: "90l"}
CAMERA_HEIGHT = 560
RECENT_LINES = 12 # how far back a repeated line is still collapsed into its earlier one
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


class Sparkline(Gtk.DrawingArea):
    """Every value of this session, scaled to the session min/max and coloured by level.

    The x axis always spans the whole session: once the buffer is full, pairs of points are
    averaged and each point comes to stand for twice as long.
    """

    def __init__(self, width=96, height=18, points=180, polarity=0):
        super().__init__(content_width=width, content_height=height, valign=Gtk.Align.CENTER)
        self.points = points
        self.polarity = polarity
        self.values = []
        self.bucket = 1 # samples per drawn point
        self.acc = 0.0
        self.acc_n = 0
        self.lo = self.hi = None
        self.shown = []      # eased copy of `values`, what actually gets drawn
        self.tick_id = None

    def push(self, value, lo, hi):
        self.acc += value
        self.acc_n += 1
        if self.acc_n >= self.bucket:
            self.values.append(self.acc / self.acc_n)
            self.acc, self.acc_n = 0.0, 0
            if len(self.values) > self.points:
                self.values = [(a + b) / 2 for a, b in zip(self.values[::2], self.values[1::2])]
                self.bucket *= 2
                self.shown = self.values[:] # decimation reshapes the curve, no point easing that
        self.lo, self.hi = lo, hi
        # Redrawn every frame while it catches up, so the curve slides instead of stepping.
        if self.tick_id is None and self.get_mapped():
            self.tick_id = self.add_tick_callback(self._ease)

    def _ease(self, _widget, _clock):
        if len(self.shown) != len(self.values):
            # Grow towards the new point from the previous one so it slides in from the right.
            self.shown = self.shown[-len(self.values):] + self.values[len(self.shown):len(self.values)]
        done = True
        for i, target in enumerate(self.values):
            gap = target - self.shown[i]
            if abs(gap) > (abs(target) + 1e-9) * 1e-4:
                self.shown[i] += gap * 0.25
                done = False
        self.queue_draw()
        if done:
            self.tick_id = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        values = self.shown if len(self.shown) == len(self.values) else self.values
        if len(values) < 2 or self.hi is None or self.hi <= self.lo:
            return
        span = self.hi - self.lo
        step = w / (len(values) - 1)
        points = [(i * step, h - 1 - (v - self.lo) / span * (h - 2)) for i, v in enumerate(values)]
        # Height maps to value, so a vertical gradient colours each point by its own level:
        # only the peaks come out red.
        gradient = cairo.LinearGradient(0, 1, 0, h - 1)
        for i in range(5):
            r, g, b = level_rgb(1 - i / 4, self.polarity) # stop 0 is the top of the graph
            gradient.add_color_stop_rgb(i / 4, r, g, b)
        cr.move_to(0, h)
        for x, y in points:
            cr.line_to(x, y)
        cr.line_to(points[-1][0], h)
        cr.close_path()
        cr.save()
        cr.clip()
        cr.set_source(gradient)
        cr.paint_with_alpha(0.22)
        cr.restore()
        cr.move_to(*points[0])
        for x, y in points[1:]:
            cr.line_to(x, y)
        cr.set_source(gradient)
        cr.set_line_width(1.2)
        cr.stroke()


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
                self._emit("(ingen serieport)\n", status="Ingen port")
                time.sleep(1)
                continue
            self.port = ports[0]
            try:
                self._pump()
            except (serial.SerialException, OSError) as exc:
                self._emit(f"(port borta: {exc})\n", status="Frånkopplad")
                time.sleep(1)

    def _pump(self):
        self.logdir.mkdir(parents=True, exist_ok=True)
        logfile = self.logdir / f"{datetime.now():%Y%m%d-%H%M%S}-{Path(self.port).name}.log"
        with serial.Serial(self.port, self.baud, timeout=0.2) as ser, logfile.open("ab") as log:
            self._emit(f"(ansluten {self.port} @ {self.baud}, logg {logfile})\n", status=f"{self.port} @ {self.baud}")
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
        self.build_type = "debug"
        self.build_env = "staging"
        self.job_active = False
        self.mcu_state = None
        self.mcu_text = ""
        self._build_ui()
        self._start_camera()
        self.serial = SerialReader(args.baud, args.logdir, self._on_serial)
        self.serial.start()
        GLib.timeout_add(1000, self._poll_state)
        GLib.timeout_add(500, self._poll_job)
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
        # Live table: one row per repeating message pattern, numbers drawn as value + min/max bar.
        # One cell per numeric field, packed in columns: the repeated prose collapses into a label.
        self.table = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["telemetry"],
                                 min_children_per_line=2, max_children_per_line=3, homogeneous=True,
                                 row_spacing=0, column_spacing=6, margin_start=6, margin_end=6, margin_bottom=2)
        css = Gtk.CssProvider()
        css.load_from_string(""".telemetry > flowboxchild { padding: 0; min-height: 0; }
            .telemetry label { padding: 0; font-size: 0.85em; }""")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        table_scroller = Gtk.ScrolledWindow(child=self.table, propagate_natural_height=True, max_content_height=300,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows = {}   # pattern -> row state
        self.seen = {}   # pattern -> occurrences before promotion to the table
        self.ranges = {} # pattern -> [[min, max], ...] per numeric field, for this session only
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(self.mute_box)
        top.append(table_scroller)
        top.append(scroller)
        self._rebuild_mute_chips()
        # Follow mode: new lines glide the view to the end, but only while it already sits there.
        self.follow = True
        self.gliding = False
        self.tick_id = None
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
        self.btn_toggle = self._button(row1, "[S] Stoppa", "media-playback-pause-symbolic", lambda *_: send("toggle"))
        self._button(row1, "[R] Reset", "view-refresh-symbolic", lambda *_: send("reset"))
        self._button(row1, "[H] Reset + stopp", "media-skip-backward-symbolic", lambda *_: send("reset_halt"))
        row1.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        self._button(row1, "[Q] Rotera kamera", "object-rotate-right-symbolic", lambda *_: self.rotate(90))
        labels = [f"{w}×{h} @ {fps} fps" for (size, fps) in self.modes for (w, h) in [size.split("x")]]
        self.size_combo = Gtk.DropDown.new_from_strings(labels)
        sizes = [size for size, _ in self.modes]
        self.size_combo.set_selected(sizes.index(self.size) if self.size in sizes else 0)
        self.size_combo.connect("notify::selected", self._on_size_changed)
        row1.append(self.size_combo)
        controls.append(row1)

        row2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row2.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        row2.append(Gtk.Label(label="Bygge", xalign=0, css_classes=["dim-label", "caption"]))
        self.btn_type = self._button(row2, "[D] Debug", "bug-symbolic", lambda *_: self.toggle_type())
        self.btn_env = self._button(row2, "[E] Staging", "emblem-system-symbolic", lambda *_: self.toggle_env())
        row2.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        self.btn_build = self._button(row2, "[B] Bygg", "applications-engineering-symbolic", lambda *_: self.build("build"))
        self.btn_flash = self._button(row2, "[F] Flasha", "drive-harddisk-symbolic", lambda *_: self.build("flash"))
        self.btn_both = self._button(row2, "[A] Bygg + flasha", "media-playlist-consecutive-symbolic",
                                     lambda *_: self.build("both"), css="suggested-action")
        self._button(row2, "[O] Logg", "text-x-generic-symbolic", lambda *_: send("open_log"))
        controls.append(row2)

        self.job_label = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=24, css_classes=["dim-label", "caption"])
        self.progress = Gtk.ProgressBar()
        controls.append(self.job_label)
        controls.append(self.progress)

    def _button(self, box, label, icon, handler, css=None):
        btn = Gtk.Button(child=Adw.ButtonContent(label=label, icon_name=icon, halign=Gtk.Align.START))
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
                badge += f" {1 / mean:.1f}/s {'jämnt' if spread < 0.25 else f'±{spread:.0%}'}"
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
            chip = Gtk.Button(label=label, tooltip_text=f"{pattern}\nklicka för att avmuta",
                              css_classes=["flat", "caption", "telemetry"])
            chip.connect("clicked", lambda _b, pat=pattern: self.unmute(pat))
            self.mute_box.append(chip)
        self.mute_box.set_visible(bool(self.muted))

    def _set_muted(self, muted):
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
        """Route a repeating telemetry line to its table row. Returns False for log lines."""
        plain = ANSI.sub("", raw)
        m = LOG_LINE.match(plain.rstrip("\r\n"))
        if not m or m.group(2).upper() in ("WARN", "WARNING", "ERROR", "FATAL"):
            return False
        key = pattern_of(plain)
        if key not in self.rows:
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] < 2:
                return False
            self.rows[key] = self._make_row(key, raw, m.groups())
        self._update_row(self.rows[key], m.groups())
        return True

    def _make_row(self, key, raw, fields):
        ts, level, module, location, message = fields
        color = self._module_color(raw, module)
        cells = []
        pos = 0
        unit = ""
        for num in NUMBERS.finditer(message):
            box = Gtk.Box(spacing=4)
            tag = Gtk.Label(css_classes=["monospace"], xalign=0, tooltip_text=location)
            tag.set_markup(f'<span foreground="{color}"><b>{module}</b></span>')
            text = field_label(message[pos:num.start()], unit)
            label = Gtk.Label(label=text, xalign=0, hexpand=True,
                              ellipsize=3, css_classes=["dim-label"]) # 3 = Pango.EllipsizeMode.END
            value = Gtk.Label(css_classes=["monospace"], xalign=1, width_chars=10)
            polarity = polarity_of(text, field_unit(message[num.end():]))
            bar = Sparkline(polarity=polarity)
            for w in (tag, label, value, bar):
                box.append(w)
            # Click anywhere on a cell mutes the whole pattern it came from.
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self._set_muted(self.muted | {key}))
            box.add_controller(click)
            box.set_tooltip_text(f"{message.strip()}\n{location} · klicka för att muta")
            self.table.append(box)
            unit = field_unit(message[num.end():])
            cells.append({"value": value, "bar": bar, "unit": unit, "polarity": polarity})
            pos = num.end()
        if not cells: # no numbers: count, rate and how evenly the line arrives
            box = Gtk.Box(spacing=4)
            tag = Gtk.Label(css_classes=["monospace"], xalign=0)
            tag.set_markup(f'<span foreground="{color}"><b>{module}</b></span>')
            text = Gtk.Label(label=message, xalign=0, hexpand=True, ellipsize=3)
            count = Gtk.Label(css_classes=["monospace"], xalign=1, width_chars=10)
            bar = Sparkline(polarity=0)
            for w in (tag, text, count, bar):
                box.append(w)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self._set_muted(self.muted | {key}))
            box.add_controller(click)
            box.set_tooltip_text(f"{location} · klicka för att muta")
            self.table.append(box)
            cells.append({"value": count, "bar": bar, "unit": "", "polarity": 0})
        ranges = self.ranges.setdefault(key, [[None, None] for _ in cells])
        return {"cells": cells, "n": 0, "ranges": ranges, "numeric": bool(NUMBERS.search(message))}

    def _update_row(self, state, fields):
        ts, level, module, location, message = fields
        state["n"] += 1
        if not state["numeric"]:
            # Intervals between arrivals: the line shows the rate, its shape shows the jitter.
            cell = state["cells"][0]
            now = ts_seconds(ts)
            previous = state.get("last_ts")
            state["last_ts"] = now
            if previous is None or not 0 < now - previous < 3600:
                cell["value"].set_label(f"×{state['n']}")
                return
            gap = now - previous
            gaps = state.setdefault("gaps", [None, None])
            gaps[0] = gap if gaps[0] is None else min(gaps[0], gap)
            gaps[1] = gap if gaps[1] is None else max(gaps[1], gap)
            cell["bar"].push(gap, gaps[0], gaps[1])
            cell["value"].set_markup(
                f"<b>×{state['n']}</b><span size=\"smaller\"> {1 / gap:.1f}/s</span>"
                if gap > 0 else f"<b>×{state['n']}</b>")
            cell["bar"].set_tooltip_text(f"intervall {gap:.2f}s · min {gaps[0]:.2f}s · max {gaps[1]:.2f}s")
            return
        for cell, num, rng in zip(state["cells"], NUMBERS.finditer(message), state["ranges"]):
            v = float(num.group())
            rng[0] = v if rng[0] is None else min(rng[0], v)
            rng[1] = v if rng[1] is None else max(rng[1], v)
            lo, hi = rng
            unit = f" {cell['unit']}" if cell["unit"] else ""
            if hi > lo:
                t = (v - lo) / (hi - lo)
                cell["value"].set_markup(
                    f'<span foreground="{heat_color(t, cell["polarity"])}"><b>{num.group()}</b></span>'
                    f'<span size="smaller">{GLib.markup_escape_text(unit)}</span>')
                cell["bar"].push(v, lo, hi)
                cell["bar"].set_tooltip_text(f"min {lo:g} · max {hi:g}")
            else:
                cell["value"].set_markup(f"<b>{num.group()}</b><span size=\"smaller\">{GLib.markup_escape_text(unit)}</span>")
            if hi <= lo:
                cell["bar"].push(v, lo, hi) # flat so far, keep the history going

    def _clear_table(self):
        while child := self.table.get_first_child():
            self.table.remove(child)
        self.rows.clear()
        self.seen.clear()
        self.ranges.clear()

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
        if self.gliding:
            return # our own glide, not the user
        self.follow = self._at_end(adj)

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
        return False

    # --- plugin state -------------------------------------------------------

    def _update_subtitle(self):
        parts = [self.mcu_text, self.serial_state]
        if self.muted:
            parts.append(f"{len(self.muted)} mutade")
        self.title_widget.set_subtitle(" · ".join(p for p in parts if p))

    def _poll_state(self):
        state = read_json(DATA_DIR / "state.json")
        if state:
            self.mcu_state = state.get("state")
            if not state.get("probe"):
                text = "Ingen ST-Link"
            elif state.get("debugger"):
                text = f"Upptagen av {state['debugger']}"
            else:
                text = f"{state['probe']} · {STATE_TEXT.get(self.mcu_state, self.mcu_state)}"
            self.mcu_text = text
            self._update_subtitle()
            halted = self.mcu_state == "halted"
            self.btn_toggle.get_child().set_label("[S] Starta" if halted else "[S] Stoppa")
            self.btn_toggle.get_child().set_icon_name(
                "media-playback-start-symbolic" if halted else "media-playback-pause-symbolic")
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

    def toggle_type(self):
        self.build_type = "release" if self.build_type == "debug" else "debug"
        self.btn_type.get_child().set_label(f"[D] {self.build_type.capitalize()}")

    def toggle_env(self):
        self.build_env = "production" if self.build_env == "staging" else "staging"
        self.btn_env.get_child().set_label(f"[E] {self.build_env.capitalize()}")

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
            "d": self.toggle_type, "e": self.toggle_env,
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
