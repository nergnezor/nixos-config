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
#   ./noctalia/sync.sh merge  repo -> live, keeping this machine's scrubbed lines
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
#
# It also drops the `timeout` and `enabled` keys inside each
# [idle.behavior.*] table (the `action` key stays tracked). nitro is erik's
# work laptop and hp is not, so they're meant to idle out on different
# schedules AND have different behaviors enabled (e.g. lock on nitro, not on
# hp) -- tracking those as one shared value meant a pull on one machine
# followed by a push (or a later pull-and-commit) on the other silently
# overwrote its setting with the other machine's. Same deal as the calendar
# account: set these again in Noctalia's settings panel after a push.
# With a second argument of 1, prints only the machine-local lines instead,
# each group under its table header, so `merge` can put them back.
scrub() {
  awk -v local_only="${2:-0}" '
    /^[[:space:]]*\[/ {
      hdr     = $0
      shown   = 0
      skip    = ($0 ~ /^[[:space:]]*\[calendar\.account\./)
      in_idle = ($0 ~ /^[[:space:]]*\[idle\.behavior\./)
    }
    {
      machine_local = skip || (in_idle && /^[[:space:]]*(timeout|enabled)[[:space:]]*=/)
      # The daily-wallpaper plugin rewrites its paths every day, so they are
      # dropped but never put back.
      drop = machine_local || /^[[:space:]]*path[[:space:]]*=.*\/daily-wallpaper\//
    }
    !local_only && !drop { print; next }
    local_only && machine_local && !/^[[:space:]]*$/ {
      if (!shown && $0 != hdr) print hdr
      shown = 1
      print
    }
  ' "$1"
}

# Prints the repo file with the live file's scrubbed lines put back at the end
# of their tables. A key the repo table already has wins. Tables the repo
# lacks, like [calendar.account.*], go at the end of the file.
merge() {
  awk '
    function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
    function key(s) { sub(/=.*/, "", s); return trim(s) }
    function flush(   n, i, ls) {
      if (!(cur in body) || (cur in used)) return
      n = split(body[cur], ls, "\n")
      for (i = 1; i < n; i++) if (!(key(ls[i]) in seen)) print ls[i]
      used[cur] = 1
    }
    NR == FNR {
      if (/^[[:space:]]*\[/) { h = trim($0); order[++n] = h; head[h] = $0 }
      else body[h] = body[h] $0 "\n"
      next
    }
    /^[[:space:]]*\[/ { flush(); cur = trim($0); split("", seen); print; next }
    /=/ { seen[key($0)] = 1 }
    { print }
    END {
      flush()
      for (i = 1; i <= n; i++)
        if (!(order[i] in used)) printf "\n%s\n%s", head[order[i]], body[order[i]]
    }
  ' <(scrub "$live_file" 1) "$repo_file"
}

case "${1:-}" in
  pull)
    scrub "$live_file" > "$repo_file"
    echo "pulled  $live_file -> $repo_file (calendar account, idle timeout + daily wallpaper scrubbed)"
    git -C "$(dirname "$repo_file")" diff --stat -- "$repo_file"
    ;;
  push)
    if grep -q '^<<<<<<< \|^>>>>>>> ' "$repo_file"; then
      echo "$repo_file has conflict markers -- resolve them first" >&2
      exit 1
    fi
    if pgrep -x noctalia >/dev/null || pgrep -x noctalia-shell >/dev/null || pgrep -f 'quickshell.*noctalia' >/dev/null; then
      echo "noctalia is running -- stop it first (systemctl --user stop noctalia)," >&2
      echo "otherwise it will write its in-memory settings back over the restore." >&2
      exit 1
    fi
    mkdir -p "$(dirname "$live_file")"
    [ -e "$live_file" ] && cp "$live_file" "$live_file.bak-$(date +%Y%m%d%H%M%S)"
    cp "$repo_file" "$live_file"
    echo "pushed  $repo_file -> $live_file"
    echo "note: the calendar account and idle timeout are not in the tracked"
    echo "      copy -- set them again in noctalia's settings if needed."
    ;;
  diff)
    diff -u "$repo_file" <(scrub "$live_file") && echo "identical"
    ;;
  scrubbed)
    scrub "$live_file"
    ;;
  merge)
    merged="$(merge)"
    [ -e "$live_file" ] && cp "$live_file" "$live_file.bak-$(date +%Y%m%d%H%M%S)"
    printf '%s\n' "$merged" > "$live_file"
    echo "merged  $repo_file -> $live_file (kept this machine's idle and calendar lines)"
    ;;
  *)
    echo "usage: ${0##*/} {pull|push|merge|diff|scrubbed}" >&2
    exit 2
    ;;
esac
