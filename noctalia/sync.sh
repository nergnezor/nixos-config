#!/usr/bin/env bash
# Sync Noctalia's shell settings between the live file and this repo.
#
# Deliberately a copy, not a symlink: noctalia's GUI writes settings.toml
# itself (settings panel, bar editor, dragging desktop widgets), and it
# rewrites the file rather than editing it in place -- an atomic
# write-temp-then-rename replaces a symlink with a plain file, so the link
# silently stops pointing at the repo after the first change made in the
# GUI. ~/.config/niri gets mkOutOfStoreSymlink in home.nix because niri
# only ever *reads* its config; noctalia writes its own, so it gets this.
#
#   ./noctalia/sync.sh pull   live -> repo   (after changing things in the GUI)
#   ./noctalia/sync.sh push   repo -> live   (restoring on a new machine)
#   ./noctalia/sync.sh diff   what differs
#
# `push` backs the live file up first and refuses while noctalia is running,
# because the running shell holds the settings in memory and writes them
# back out on the next change -- which would undo the restore.
set -euo pipefail

repo_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/settings.toml"
live_file="${XDG_STATE_HOME:-$HOME/.local/state}/noctalia/settings.toml"

# The tracked copy drops [calendar.account.<name>] -- the section is keyed on
# the Google account name, and this repo is public. Nothing is lost by it: the
# OAuth credentials live outside settings.toml anyway, so a restored machine
# has to link the calendar in the GUI regardless. Applied on both `pull` and
# `diff`, so a linked account locally does not show up as a permanent diff.
scrub() {
  awk '
    /^[[:space:]]*\[calendar\.account\./ { skip = 1; next }
    /^[[:space:]]*\[/                      { skip = 0 }
    skip && /^[[:space:]]*$/                { next }
    !skip
  ' "$1"
}

case "${1:-}" in
  pull)
    scrub "$live_file" > "$repo_file"
    echo "pulled  $live_file -> $repo_file (calendar account scrubbed)"
    git -C "$(dirname "$repo_file")" diff --stat -- "$repo_file"
    ;;
  push)
    if pgrep -x noctalia-shell >/dev/null || pgrep -f 'quickshell.*noctalia' >/dev/null; then
      echo "noctalia is running -- stop it first (systemctl --user stop noctalia)," >&2
      echo "otherwise it will write its in-memory settings back over the restore." >&2
      exit 1
    fi
    mkdir -p "$(dirname "$live_file")"
    [ -e "$live_file" ] && cp "$live_file" "$live_file.bak-$(date +%Y%m%d%H%M%S)"
    cp "$repo_file" "$live_file"
    echo "pushed  $repo_file -> $live_file"
    echo "note: the calendar account is not in the tracked copy -- link it"
    echo "      again in noctalia's settings if you used one."
    ;;
  diff)
    diff -u "$repo_file" <(scrub "$live_file") && echo "identical"
    ;;
  *)
    echo "usage: ${0##*/} {pull|push|diff}" >&2
    exit 2
    ;;
esac
