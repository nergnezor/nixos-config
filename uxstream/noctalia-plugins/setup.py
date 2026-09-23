#!/usr/bin/env python3
"""Make noctalia carry the ST-Link plugin, without hand-registering it on every machine.

Registers this directory as a plugin source, enables the plugin and puts its widget in the bar.
Safe to run repeatedly, and safe to run while noctalia is up: the file is reloaded afterwards.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomli_w
import tomllib

PLUGIN = "erik/stlink"
WIDGET = "stlink"
SOURCE = "local"
SETTINGS = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "noctalia/settings.toml"


def main():
    source_dir = Path(sys.argv[1]).resolve()
    if not SETTINGS.exists():
        print(f"no {SETTINGS} yet, run noctalia once first", file=sys.stderr)
        return 0
    settings = tomllib.loads(SETTINGS.read_text())
    changed = False

    sources = settings.setdefault("plugin_sources", {})
    entry = sources.setdefault(SOURCE, {})
    if entry.get("location") != str(source_dir) or entry.get("kind") != "path":
        entry.update(kind="path", location=str(source_dir))
        changed = True

    enabled = settings.setdefault("plugins", {}).setdefault("enabled", [])
    if PLUGIN not in enabled:
        enabled.append(PLUGIN)
        changed = True

    widgets = settings.setdefault("widget", {})
    if widgets.get(WIDGET, {}).get("type") != f"{PLUGIN}:status":
        widgets[WIDGET] = {"type": f"{PLUGIN}:status"}
        changed = True

    # Appended rather than placed: where it sits in the bar is the user's to arrange.
    bar = settings.setdefault("bar", {}).setdefault("default", {})
    start = bar.setdefault("start", [])
    if not any(WIDGET in section for section in (start, bar.get("center", []), bar.get("end", []))):
        start.append(WIDGET)
        changed = True

    if not changed:
        return 0
    shutil.copy(SETTINGS, SETTINGS.with_suffix(".toml.bak-stlink-setup"))
    SETTINGS.write_text(tomli_w.dumps(settings))
    if shutil.which("noctalia"):
        subprocess.run(["noctalia", "msg", "config-reload"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"registered {PLUGIN} from {source_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
