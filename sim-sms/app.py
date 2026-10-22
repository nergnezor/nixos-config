#!/usr/bin/env python3
"""Window for reading SMS stored on a SIM card."""

import argparse
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from simcard import SimError, ensure_daemon, peek, read_all


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--pin", default=os.environ.pop("SIM_SMS_PIN", ""))
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--read", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args, rest = parser.parse_known_args(argv[1:])
    return [argv[0], *rest], args


def _documents():
    raw = os.environ.get("XDG_DOCUMENTS_DIR")
    if raw and raw.startswith("/"):
        return Path(raw)
    config = Path.home() / ".config" / "user-dirs.dirs"
    if config.is_file():
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("XDG_DOCUMENTS_DIR="):
                value = line.split("=", 1)[1].strip().strip('"')
                value = value.replace("$HOME", str(Path.home()))
                path = Path(value)
                if path.is_dir():
                    return path
    documents = Path.home() / "Documents"
    if documents.is_dir():
        return documents
    return Path.home()


def _safe(text, limit=48):
    cleaned = []
    for char in (text or "").strip():
        if char.isalnum() or char in "+-.":
            cleaned.append(char)
        else:
            cleaned.append("_")
    result = "".join(cleaned).strip("_")
    return (result or "okant")[:limit]


def message_file(message):
    lines = [
        f"Riktning: {message.direction}",
        f"Nummer: {message.peer or 'okänt'}",
        f"Tid: {message.when_text or 'okänd'}",
        f"Status: {'oläst' if message.unread else 'läst'}",
    ]
    if message.incomplete:
        lines.append(f"Delar: {message.part_count} av {message.part_total}")
    lines.append("")
    lines.append(message.text or "")
    lines.append("")
    return "\n".join(lines)


def save_messages(info):
    """Write every message to ~/Documents/SIM-meddelanden/<iccid>/<time>/."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    folder = _documents() / "SIM-meddelanden" / _safe(info.iccid or "okant-kort", 32) / stamp
    folder.mkdir(parents=True, exist_ok=True)
    header = "\n".join(
        [
            f"Operatör: {info.operator or 'okänd'}",
            f"ICCID: {info.iccid or 'okänt'}",
            f"Läsare: {info.reader}",
            f"Sparat: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Meddelanden: {len(info.messages)}",
            "",
        ]
    )
    (folder / "kort.txt").write_text(header, encoding="utf-8")
    pieces = [header]
    for index, message in enumerate(info.messages, start=1):
        body = message_file(message)
        when = _safe(message.when_text, 20) if message.when_text else "utan-tid"
        name = f"{index:03d}_{when}_{_safe(message.direction, 16)}_{_safe(message.peer)}.txt"
        (folder / name).write_text(body, encoding="utf-8")
        pieces.append(body)
        pieces.append("-" * 40)
        pieces.append("")
    (folder / "alla.txt").write_text("\n".join(pieces), encoding="utf-8")
    return folder


def dump(pin, debug):
    info = read_all(pin, progress=lambda text: print(text, file=sys.stderr), debug=debug)
    folder = save_messages(info)
    print(f"{info.operator or 'SIM'}  {info.iccid}  {info.reader}")
    print(f"{len(info.messages)} meddelanden, {info.slots} platser")
    print(f"sparat i {folder}")
    for message in info.messages:
        mark = "oläst " if message.unread else ""
        extra = ""
        if message.incomplete:
            extra = f" ({message.part_count}/{message.part_total} delar)"
        print(f"--- {message.direction} {mark}{message.peer} {message.when_text}{extra}")
        print(message.text)


def run_gui(argv, pin, autostart):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk, Pango

    class SmsApp(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="se.erik.SimSms")
            self.initial_pin = pin
            self.autostart = autostart

        def do_activate(self):
            if self.get_windows():
                self.get_windows()[0].present()
                return
            self._build()
            self.window.present()
            threading.Thread(target=self._peek, daemon=True).start()
            if self.autostart and self.entry.get_text():
                GLib.idle_add(self._start_read)

        def _peek(self):
            GLib.idle_add(self.button.set_sensitive, False)
            try:
                _reader, iccid, operator, retries = peek()
            except SimError as exc:
                GLib.idle_add(self.info.set_text, str(exc))
            else:
                who = operator or "SIM"
                parts = [who]
                if iccid:
                    parts.append(iccid)
                if retries is not None:
                    parts.append(f"{retries} PIN-försök kvar")
                GLib.idle_add(self.info.set_text, " · ".join(parts))
            finally:
                GLib.idle_add(self.button.set_sensitive, True)

        def _build(self):
            css = Gtk.CssProvider()
            css.load_from_string(
                """
                .sms-row { padding: 10px 12px; }
                .peer { font-weight: 600; }
                .unread { font-weight: 700; }
                .message-body { font-size: 15px; }
                .peer-title { font-size: 18px; font-weight: 700; }
                """
            )
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

            self.window = Gtk.ApplicationWindow(application=self, title="SIM-meddelanden")
            self.window.set_default_size(960, 640)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            root.set_margin_top(12)
            root.set_margin_bottom(12)
            root.set_margin_start(12)
            root.set_margin_end(12)
            self.window.set_child(root)

            controls = Gtk.Box(spacing=8)
            self.entry = Gtk.Entry(
                visibility=False,
                input_purpose=Gtk.InputPurpose.PIN,
                placeholder_text="PIN",
                width_chars=8,
                max_length=8,
            )
            self.entry.set_text(self.initial_pin)
            self.entry.connect("activate", lambda *_: self._start_read())
            self.reveal = Gtk.CheckButton(label="Visa PIN")
            self.reveal.connect("toggled", lambda button: self.entry.set_visibility(button.get_active()))
            self.button = Gtk.Button(label="Läs meddelanden")
            self.button.add_css_class("suggested-action")
            self.button.connect("clicked", lambda *_: self._start_read())
            controls.append(self.entry)
            controls.append(self.reveal)
            controls.append(self.button)
            root.append(controls)

            hint = Gtk.Label(label="Ett fel PIN förbrukar ett försök. Tre fel i rad blockerar kortet.", xalign=0)
            hint.add_css_class("dim-label")
            root.append(hint)

            self.info = Gtk.Label(label="Sätt i SIM-kortet och ange PIN.", xalign=0, wrap=True, selectable=True, hexpand=True)
            root.append(self.info)
            self.saved_dir = None
            self.open_button = Gtk.Button(label="Öppna mappen")
            self.open_button.set_sensitive(False)
            self.open_button.connect("clicked", lambda *_: self._open_saved())
            controls.append(self.open_button)

            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, vexpand=True)
            self.list.add_css_class("boxed-list")
            self.list.connect("row-selected", self._on_select)
            list_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            list_scroll.set_child(self.list)
            list_scroll.set_size_request(300, -1)

            self.detail_peer = Gtk.Label(label="", xalign=0)
            self.detail_peer.add_css_class("peer-title")
            self.detail_meta = Gtk.Label(label="", xalign=0)
            self.detail_meta.add_css_class("dim-label")
            self.buffer = Gtk.TextBuffer()
            self.text = Gtk.TextView(
                buffer=self.buffer,
                editable=False,
                cursor_visible=False,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
                left_margin=2,
                right_margin=8,
                top_margin=12,
            )
            self.text.add_css_class("message-body")
            body_scroll = Gtk.ScrolledWindow(vexpand=True)
            body_scroll.set_child(self.text)

            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            right.set_margin_start(16)
            right.append(self.detail_peer)
            right.append(self.detail_meta)
            right.append(body_scroll)

            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
            paned.set_start_child(list_scroll)
            paned.set_end_child(right)
            paned.set_resize_start_child(False)
            paned.set_shrink_start_child(False)
            root.append(paned)

        def _start_read(self):
            if not self.button.get_sensitive():
                return
            pin_text = self.entry.get_text().strip()
            self.button.set_sensitive(False)
            self.entry.set_sensitive(False)
            self.info.set_text("Läser kortet…")

            def worker():
                try:
                    info = read_all(pin_text, progress=lambda text: GLib.idle_add(self.info.set_text, text))
                except SimError as exc:
                    GLib.idle_add(self._fail, str(exc))
                except Exception as exc:
                    GLib.idle_add(self._fail, str(exc))
                else:
                    GLib.idle_add(self._show, info)

            threading.Thread(target=worker, daemon=True).start()

        def _fail(self, message):
            self.info.set_text(message)
            self.button.set_sensitive(True)
            self.entry.set_sensitive(True)
            return False

        def _show(self, info):
            self.button.set_sensitive(True)
            self.entry.set_sensitive(True)
            who = info.operator or "SIM"
            iccid = info.iccid or "okänt ICCID"
            count = len(info.messages)
            noun = "meddelande" if count == 1 else "meddelanden"
            try:
                self.saved_dir = save_messages(info)
                saved = f"sparat i {self.saved_dir}"
                self.open_button.set_sensitive(True)
            except OSError as exc:
                self.saved_dir = None
                saved = f"kunde inte spara ({exc})"
            self.info.set_text(f"{who} · {iccid} · {count} {noun} · {saved}")
            self._clear()
            if not info.messages:
                self.detail_peer.set_text("")
                self.detail_meta.set_text("")
                self.buffer.set_text("Inga meddelanden på kortet.")
                return False
            for message in info.messages:
                self.list.append(self._row(message))
            first = self.list.get_row_at_index(0)
            if first is not None:
                self.list.select_row(first)
            return False

        def _clear(self):
            while True:
                row = self.list.get_row_at_index(0)
                if row is None:
                    break
                self.list.remove(row)

        def _row(self, message):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("sms-row")
            top = Gtk.Box(spacing=8)
            peer = Gtk.Label(label=message.peer or "Okänt nummer", xalign=0, hexpand=True)
            peer.add_css_class("unread" if message.unread else "peer")
            when = Gtk.Label(label=message.when_text, xalign=1)
            when.add_css_class("dim-label")
            top.append(peer)
            top.append(when)
            preview = message.text.replace("\n", " ").strip()
            if len(preview) > 90:
                preview = preview[:89] + "…"
            body = Gtk.Label(label=preview or "(tomt)", xalign=0, hexpand=True)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.add_css_class("dim-label")
            box.append(top)
            box.append(body)
            row.set_child(box)
            row.sms = message
            return row

        def _on_select(self, _list, row):
            if row is None:
                return
            message = row.sms
            self.detail_peer.set_text(message.peer or "Okänt nummer")
            bits = [message.direction]
            if message.when_text:
                bits.append(message.when_text)
            if message.unread:
                bits.append("oläst")
            if message.incomplete:
                bits.append(f"{message.part_count} av {message.part_total} delar")
            self.detail_meta.set_text(" · ".join(bits))
            self.buffer.set_text(message.text)

        def _open_saved(self):
            if self.saved_dir is not None:
                subprocess.Popen(["xdg-open", os.fspath(self.saved_dir)])

    if _script_dir() not in sys.path:
        sys.path.insert(0, _script_dir())
    app = SmsApp()
    raise SystemExit(app.run(argv))


def main():
    # Re-exec into the pcscd namespace before parsing, so --dump and the PIN
    # survive. ensure_daemon replaces this process and the child starts over.
    ensure_daemon()
    argv, args = parse_args(sys.argv)
    if args.dump:
        try:
            dump(args.pin, args.debug)
        except SimError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1)
        return
    run_gui(argv, args.pin, args.read or bool(args.pin))


if __name__ == "__main__":
    main()
