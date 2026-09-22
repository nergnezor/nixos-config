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

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, GLib, Gst, Gtk  # noqa: E402

import serial  # noqa: E402

PLUGIN = "erik/stlink:service"
DATA_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/plugins/data/erik/stlink"
# Remembered between runs: log height (paned position), camera rotation, filter.
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "eyebuds-dev/settings.json"
STATE_TEXT = {
    "running": "Kör", "halted": "Stoppad", "reset": "I reset",
    "debug-running": "Kör", "unknown": "Okänd",
}
DIRECTIONS = {0: "identity", 90: "90r", 180: "180", 270: "90l"}
# The firmware colours its log levels with SGR sequences. SGR is rendered with text tags,
# every other escape sequence is dropped.
ANSI = re.compile(r"\x1b\[([0-9;?]*)([ -/]*[@-~])")
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

        body = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        root.append(body)
        self.paned = body

        # Bottom half: a vertical button column on the left, the camera filling the rest.
        bottom = Gtk.Box(spacing=8, margin_top=6, margin_bottom=8, margin_start=8, margin_end=8)
        body.set_end_child(bottom)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
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
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(log_header)
        top.append(scroller)
        # Follow mode: new lines glide the view to the end, but only while it already sits there.
        self.follow = True
        self.scroll_anim = None
        adj = self.scroller.get_vadjustment()
        adj.connect("value-changed", self._on_scrolled)
        adj.connect("notify::upper", lambda *_: self.follow and self._scroll_to_end())
        self.lines = collections.deque(maxlen=5000) # complete raw lines, colours included
        self.partial = ""
        self.filter = None
        self.filter_invert = False
        self.shown = 0
        self._refilter()
        body.set_start_child(top)
        # Position is applied once both children exist, otherwise GTK recomputes it on first layout.
        body.set_position(self.settings.get("log_height", 973))
        # Saving starts after the initial layout has settled, so only the user's drags are remembered.
        GLib.timeout_add(2000, lambda: body.connect(
            "notify::position", lambda paned, _p: save_settings(log_height=paned.get_position())) and False)
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
            f"! videoflip name=flip video-direction={DIRECTIONS[self.rotation]} ! gtk4paintablesink name=sink"
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

    def _matches(self, line):
        if self.filter is None:
            return True
        hit = self.filter.search(ANSI.sub("", line)) is not None
        return hit != self.filter_invert

    def _show_line(self, line):
        self._insert_ansi(line)
        self.shown += 1

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
        for line in self.lines:
            if self._matches(line):
                self._show_line(line)
        self._update_count()
        self.follow_end()

    def _update_count(self):
        self.filter_count.set_label(f"{self.shown}/{len(self.lines)}" if self.filter else "")

    def _at_end(self, adj):
        return adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40

    def _on_scrolled(self, adj):
        if self.scroll_anim and self.scroll_anim.get_state() == Adw.AnimationState.PLAYING:
            return # our own glide, not the user
        self.follow = self._at_end(adj)

    def _scroll_to_end(self):
        adj = self.scroller.get_vadjustment()
        target = adj.get_upper() - adj.get_page_size()
        if target <= adj.get_value():
            return
        if self.scroll_anim:
            self.scroll_anim.skip()
        self.scroll_anim = Adw.TimedAnimation.new(
            self.scroller, adj.get_value(), target, 150, Adw.PropertyAnimationTarget.new(adj, "value"))
        self.scroll_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.scroll_anim.play()

    def follow_end(self):
        self.follow = True
        self._scroll_to_end()

    def clear_log(self):
        self.lines.clear()
        self.partial = ""
        self.buffer.set_text("")
        self.shown = 0
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
            "q": lambda: self.rotate(90), "c": self.clear_log, "g": self.follow_end, "/": lambda: self.filter_entry.grab_focus(),
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
