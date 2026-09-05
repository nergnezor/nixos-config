#!/usr/bin/env bash
# Sync the hand-written dotfiles in $HOME between the live files and this repo.
#
#   ./home/sync.sh pull   live -> repo   (after editing one of them)
#   ./home/sync.sh push   repo -> live   (restoring on a new machine)
#   ./home/sync.sh diff   what differs
#
# Why a sync script and not home-manager: every file here already exists in
# $HOME, and home-manager refuses to overwrite an existing real file --
# it aborts the ENTIRE activation on the collision, which is the failure
# that once left this profile with no packages at all (see home.nix, and
# the mouseless.service story in configuration.nix). ~/.config/niri escapes
# that with mkOutOfStoreSymlink because it was moved aside first and niri
# only ever reads it; these are files other programs write, or that the
# rescue restored, so they get copied instead -- same call as
# noctalia/sync.sh.
#
# `push` backs up whatever it replaces, so a restore that turns out wrong is
# recoverable.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}"

# repo-relative path : live path
files=(
  "bashrc:$HOME/.bashrc"
  "profile:$HOME/.profile"
  "gitconfig:$HOME/.gitconfig"
  "ghostty/config:$cfg/ghostty/config"
  "vscode/settings.json:$cfg/Code/User/settings.json"
  "vscode/keybindings.json:$cfg/Code/User/keybindings.json"
)
# Deliberately NOT synced:
#   ~/.ssh/*                     keys, and config names a real host:port --
#                                this repo is public
#   ~/.config/ghostty/config.ghostty, themes/*, and every other generated
#                                theme file (gtk, btop, lazygit, qt, vscode)
#                                -- noctalia writes those from
#                                [theme.templates], see noctalia/sync.sh
#   ~/.local/state/noctalia/*    noctalia/sync.sh owns that one

# ~/.gitconfig's gh credential helper is written by `gh auth login` as an
# absolute /nix/store path, which is pinned to one gh build and means
# nothing on another machine. Store the plain command instead; gh is in
# home.packages, so it is on PATH wherever this config is deployed.
scrub_gitconfig() {
  sed -E 's#!/nix/store/[^ ]*/(\.gh-wrapped|gh) auth git-credential#!gh auth git-credential#' "$1"
}

scrubbed() { # <repo-relative> <live path>
  case "$1" in
    gitconfig) scrub_gitconfig "$2" ;;
    *)         cat "$2" ;;
  esac
}

case "${1:-}" in
  pull)
    for pair in "${files[@]}"; do
      rel="${pair%%:*}"; live="${pair#*:}"
      if [ ! -e "$live" ]; then echo "skip    $live (missing)"; continue; fi
      mkdir -p "$repo/$(dirname "$rel")"
      scrubbed "$rel" "$live" > "$repo/$rel"
      echo "pulled  $live"
    done
    # Extensions are not files to copy -- ask VS Code what is installed.
    if command -v code >/dev/null; then
      code --list-extensions | sort > "$repo/vscode/extensions.txt"
      echo "pulled  $(wc -l < "$repo/vscode/extensions.txt") VS Code extensions"
    else
      echo "skip    VS Code extensions (code not on PATH)"
    fi
    git -C "$repo" diff --stat -- "$repo" || true
    ;;
  push)
    for pair in "${files[@]}"; do
      rel="${pair%%:*}"; live="${pair#*:}"
      [ -e "$repo/$rel" ] || { echo "skip    $rel (not in repo)"; continue; }
      mkdir -p "$(dirname "$live")"
      [ -e "$live" ] && cp "$live" "$live.bak-$(date +%Y%m%d%H%M%S)"
      cp "$repo/$rel" "$live"
      echo "pushed  $live"
    done
    if [ -s "$repo/vscode/extensions.txt" ] && command -v code >/dev/null; then
      installed="$(code --list-extensions | tr 'A-Z' 'a-z' | sort)"
      while read -r ext; do
        [ -n "$ext" ] || continue
        if grep -qxF "$(echo "$ext" | tr 'A-Z' 'a-z')" <<<"$installed"; then continue; fi
        echo "  installing $ext"
        code --install-extension "$ext" --force >/dev/null || echo "  !! failed: $ext"
      done < "$repo/vscode/extensions.txt"
    fi
    echo
    echo "note: the gh credential helper in ~/.gitconfig is the generic"
    echo "      \`gh auth git-credential\` form -- run \`gh auth login\` to make"
    echo "      it actually work. SSH keys and ~/.ssh/config are not tracked."
    ;;
  diff)
    rc=0
    for pair in "${files[@]}"; do
      rel="${pair%%:*}"; live="${pair#*:}"
      if [ ! -e "$live" ]; then echo "== $rel: live file missing"; rc=1; continue; fi
      if ! diff -u --label "repo/$rel" --label "$live" \
             "$repo/$rel" <(scrubbed "$rel" "$live"); then rc=1; fi
    done
    if command -v code >/dev/null; then
      diff -u --label repo/vscode/extensions.txt --label "installed" \
        "$repo/vscode/extensions.txt" <(code --list-extensions | sort) || rc=1
    fi
    [ "$rc" = 0 ] && echo "identical"
    exit 0
    ;;
  *)
    echo "usage: ${0##*/} {pull|push|diff}" >&2
    exit 2
    ;;
esac
