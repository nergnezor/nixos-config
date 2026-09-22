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

import colorsys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, GLib, Gst, Gtk  # noqa: E402

import serial  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: camera rotation, filter, mutes, min/max ranges.
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "eyebuds-dev/settings.json"
STATE_TEXT = {
    "running": "Kör", "halted": "Stoppad", "reset": "I reset",
    "debug-running": "Kör", "unknown": "Okänd",
}
DIRECTIONS = {0: "identity", 90: "90r", 180: "180", 270: "90l"}
CAMERA_HEIGHT = 560
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


def heat_color(t):
    """0 = cold (blue) … 1 = hot (red), through green and yellow."""
    t = min(1.0, max(0.0, t))
    r, g, b = colorsys.hls_to_rgb((1 - t) * 0.62, 0.62, 0.85)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


class HeatBar(Gtk.DrawingArea):
    """A small bar filled to `fraction` with the heat colour, drawn with Cairo."""

    def __init__(self):
        super().__init__(content_width=56, content_height=8, valign=Gtk.Align.CENTER)
        self.fraction = 0.0
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        self.fraction = min(1.0, max(0.0, fraction))
        self.queue_draw()

    def _draw(self, _area, cr, w, h):
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        r, g, b = colorsys.hls_to_rgb((1 - self.fraction) * 0.62, 0.62, 0.85)
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, w * self.fraction, h)
        cr.fill()


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
        self.build_type = "debug"
        self.build_env = "staging"
        self.job_active = False
        self.mcu_state = None
        self._build_ui()
        self._start_camera()
        self.serial = SerialReader(args.baud, args.logdir, self._on_serial)
        self.serial.start()
        GLib.timeout_add(1000, self._poll_state)
        GLib.timeout_add(500, self._poll_job)
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
        self.serial_status = Gtk.Label(label="Serielogg", xalign=0, hexpand=True, css_classes=["dim-label", "caption"], margin_start=6)
        # Filter: case-insensitive regex over the uncoloured line, "!" first inverts it, "/" focuses it.
        self.filter_entry = Gtk.SearchEntry(placeholder_text="[/] Filter (regex, !x utesluter)", width_chars=32)
        self.filter_entry.set_text(self.settings.get("filter", ""))
        self.filter_entry.connect("search-changed", lambda *_: self._refilter())
        self.filter_entry.connect("stop-search", lambda *_: self.set_focus(None))
        self.filter_entry.connect("activate", lambda *_: self.set_focus(None))
        self.filter_count = Gtk.Label(label="", css_classes=["dim-label", "caption"], margin_end=6)
        log_header = Gtk.Box(spacing=6)
        log_header.append(self.serial_status)
        log_header.append(self.filter_count)
        log_header.append(self.filter_entry)
        # Muted patterns as chips, each a button that unmutes its pattern.
        self.muted = set(self.settings.get("muted", []))
        self.mute_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                                    row_spacing=2, column_spacing=4, margin_start=6, margin_end=6)
        self.mute_box.set_visible(False)
        # Live table: one row per repeating message pattern, numbers drawn as value + min/max bar.
        self.table = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["telemetry"],
                                 margin_start=6, margin_end=6, margin_bottom=4)
        css = Gtk.CssProvider()
        css.load_from_string(""".telemetry row { padding: 0 4px; min-height: 0; border-bottom: 1px solid alpha(currentColor, 0.08); }
            .telemetry label { padding: 0; }
            .telemetry button { min-height: 0; min-width: 0; padding: 2px; }""")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        table_scroller = Gtk.ScrolledWindow(child=self.table, propagate_natural_height=True, max_content_height=420,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows = {}   # pattern -> row state
        self.seen = {}   # pattern -> occurrences before promotion to the table
        self.ranges = self.settings.get("ranges", {}) # pattern -> [[min, max], ...] per numeric field
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(log_header)
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
        self.filter = None
        self.filter_invert = False
        self.shown = 0
        self.pretty = self.settings.get("pretty", True)
        self.last_key = None   # (level, module, location, message) of the last rendered line
        self.last_count = 1
        self.line_mark = None # start of the last rendered line
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
        self._button(row1, "[C] Rensa logg", "edit-clear-all-symbolic", lambda *_: self.clear_log())
        self._button(row1, "[G] Till slutet", "go-bottom-symbolic", lambda *_: self.follow_end())
        self._button(row1, "[V] Råvy", "view-list-symbolic", lambda *_: self.toggle_pretty())
        self._button(row1, "[M] Muta senaste", "audio-volume-muted-symbolic", lambda *_: self.mute_last())
        self._button(row1, "[U] Avmuta allt", "audio-volume-high-symbolic", lambda *_: self.unmute_all())
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
        self.pipeline = Gst.parse_launch(
            f"v4l2src device={self.args.device} ! queue max-size-buffers=1 leaky=downstream ! videoconvert "
            f"! videoflip name=flip video-direction={DIRECTIONS[self.rotation]} "
            # Caps the frame height so the picture's natural size, and with it the bottom part, stays bounded.
            f"! videoscale ! video/x-raw,height={CAMERA_HEIGHT} ! gtk4paintablesink name=sink"
        )
        sink = self.pipeline.get_by_name("sink")
        self.picture.set_paintable(sink.props.paintable)
        self.pipeline.set_state(Gst.State.PLAYING)

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
            self.last_key = None
            self._insert_ansi(raw)
            return
        ts, level, module, location, message = m.groups()
        key = pattern_of(plain)
        if key == self.last_key and self.line_mark is not None:
            # Same message again (numbers may differ): rewrite the previous line with the latest
            # values and a repeat counter instead of adding a new one.
            self.last_count += 1
            start = self.buffer.get_iter_at_mark(self.line_mark)
            end = start.copy()
            end.forward_to_line_end()
            self.buffer.delete(start, end)
            self._insert_pretty(self.buffer.get_iter_at_mark(self.line_mark), raw, m.groups(), self.last_count)
            return
        self.last_key, self.last_count = key, 1
        self.line_mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), True)
        self._insert_pretty(self.buffer.get_end_iter(), raw, m.groups(), 1)
        self.buffer.insert(self.buffer.get_end_iter(), "\n")

    def _insert_pretty(self, it, raw, fields, count):
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
            parts.append((f"  ×{count}", self._style("#e5c07b", bold=True)))
        parts.append(("  " + location, self._style("#5c6370", scale=0.8)))
        for text, tag in parts:
            self.buffer.insert_with_tags(it, text, tag)

    def toggle_pretty(self):
        self.pretty = not self.pretty
        save_settings(pretty=self.pretty)
        self._refilter()

    def _matches(self, line):
        plain = ANSI.sub("", line)
        if self.muted and pattern_of(plain) in self.muted:
            return False
        if self.filter is None:
            return True
        hit = self.filter.search(plain) is not None
        return hit != self.filter_invert

    def _rebuild_mute_chips(self):
        while child := self.mute_box.get_first_child():
            self.mute_box.remove(child)
        for pattern in sorted(self.muted):
            label = pattern if len(pattern) <= 60 else pattern[:57] + "…"
            chip = Gtk.Button(child=Adw.ButtonContent(label=label, icon_name="window-close-symbolic"),
                              tooltip_text=pattern, css_classes=["flat", "caption"])
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
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Label(css_classes=["monospace"], xalign=0, valign=Gtk.Align.START, tooltip_text=location))
        box.get_last_child().set_markup(f'<span foreground="{self._module_color(raw, module)}"><b>{module}</b></span>')
        # Message as one wrapping label (values coloured with markup), bars after it in field order.
        text = Gtk.Label(xalign=0, wrap=True, wrap_mode=2, hexpand=True, max_width_chars=40, css_classes=["monospace"],
                         valign=Gtk.Align.START) # 2 = Pango.WrapMode.WORD_CHAR
        bars = Gtk.Box(spacing=3, valign=Gtk.Align.START, margin_top=4)
        nfields = len(NUMBERS.findall(message))
        for _ in range(nfields):
            bars.append(HeatBar())
        count = Gtk.Label(label="", css_classes=["dim-label", "caption"], width_chars=4, xalign=1, valign=Gtk.Align.START)
        mute = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"], tooltip_text="Muta",
                          valign=Gtk.Align.START)
        mute.connect("clicked", lambda *_: self._set_muted(self.muted | {key}))
        for w in (text, bars, count, mute):
            box.append(w)
        row = Gtk.ListBoxRow(child=box, activatable=False)
        self.table.append(row)
        ranges = self.ranges.setdefault(key, [[None, None] for _ in range(nfields)])
        return {"row": row, "text": text, "bars": bars, "count": count, "n": 0, "ranges": ranges}

    def _update_row(self, state, fields):
        ts, level, module, location, message = fields
        state["n"] += 1
        state["count"].set_label(f"×{state['n']}")
        state["count"].set_tooltip_text(f"senast {ts}")
        markup = []
        pos = 0
        bar = state["bars"].get_first_child()
        for num, rng in zip(NUMBERS.finditer(message), state["ranges"]):
            v = float(num.group())
            rng[0] = v if rng[0] is None else min(rng[0], v)
            rng[1] = v if rng[1] is None else max(rng[1], v)
            lo, hi = rng
            markup.append(GLib.markup_escape_text(message[pos:num.start()]))
            if hi > lo:
                t = (v - lo) / (hi - lo)
                markup.append(f'<span foreground="{heat_color(t)}"><b>{num.group()}</b></span>')
                if bar:
                    bar.set_fraction(t)
                    bar.set_tooltip_text(f"{message[pos:num.start()].strip()} min {lo:g} · max {hi:g}")
            else:
                markup.append(f"<b>{num.group()}</b>") # constant so far, nothing to grade
            if bar:
                bar.set_visible(hi > lo)
                bar = bar.get_next_sibling()
            pos = num.end()
        markup.append(GLib.markup_escape_text(message[pos:]))
        state["text"].set_markup("".join(markup))
        self.ranges_dirty = True

    def _flush_ranges(self):
        if getattr(self, "ranges_dirty", False):
            self.ranges_dirty = False
            # Bounded so the settings file cannot grow without end.
            keep = dict(list(self.ranges.items())[-200:])
            save_settings(ranges=keep)
        return True

    def _clear_table(self):
        while child := self.table.get_first_child():
            self.table.remove(child)
        self.rows.clear()
        self.seen.clear()

    def _refilter(self):
        text = self.filter_entry.get_text()
        save_settings(filter=text)
        self.filter_invert = text.startswith("!")
        pattern = text[1:] if self.filter_invert else text
        try:
            self.filter = re.compile(pattern, re.IGNORECASE) if pattern else None
            self.filter_entry.remove_css_class("error")
        except re.error:
            self.filter_entry.add_css_class("error")
            return
        self.buffer.set_text("")
        self.sgr_fg, self.sgr_bold = None, False
        self.shown = 0
        self.last_key, self.line_mark = None, None
        self._clear_table()
        for line in self.lines:
            if self._matches(line):
                self._show_line(line)
        self._update_count()
        self.follow_end()

    def _update_count(self):
        self.filter_count.set_label(f"{self.shown}/{len(self.lines)}" if (self.filter or self.muted) else "")

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
        self.last_key, self.line_mark = None, None
        self._clear_table()
        self._update_count()

    def _on_serial(self, text, status):
        if status:
            self.serial_status.set_label(f"Serielogg · {status}")
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
            self.title_widget.set_subtitle(text)
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
        if self.get_focus() is not None and self.get_focus().is_ancestor(self.filter_entry):
            return False # typing in the filter, not a shortcut
        key = chr(keyval).lower() if 32 <= keyval < 127 else ""
        actions = {
            "s": lambda: send("toggle"), "r": lambda: send("reset"), "h": lambda: send("reset_halt"),
            "q": lambda: self.rotate(90), "c": self.clear_log, "g": self.follow_end, "v": self.toggle_pretty, "m": self.mute_last, "u": self.unmute_all, "/": lambda: self.filter_entry.grab_focus(),
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
