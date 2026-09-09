#!/usr/bin/env bash
# Pull remote, sync the live config into this repo, then rebuild NixOS from it.
#
#   ./rebuild.sh                 pull, sync, then nixos-rebuild switch
#   ./rebuild.sh test            pull, sync, then nixos-rebuild test (no boot entry)
#   ./rebuild.sh boot|build      the other nixos-rebuild actions
#   ./rebuild.sh --update        bump nixpkgs in flake.lock first (deliberate)
#   ./rebuild.sh --commit        ...and commit the synced config, if it built
#   ./rebuild.sh --push          ...and push (implies --commit)
#
# Commit/push happen AFTER a successful rebuild on purpose -- a config that
# doesn't build shouldn't land in history. Flake updates are opt-in for the
# same reason: a normal rebuild should not silently drag in a new nixpkgs.
# git pull runs first so another machine's pushed config is in place before
# the live sync overlays machine-local state on top.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo"

action="switch"
do_commit=0
do_push=0
do_update=0
for arg in "$@"; do
  case "$arg" in
    switch|test|boot|build|dry-activate) action="$arg" ;;
    --update|-u)                         do_update=1 ;;
    --commit|-c)                         do_commit=1 ;;
    --push|-p)                           do_commit=1; do_push=1 ;;
    *) echo "usage: ${0##*/} [switch|test|boot|build|dry-activate] [--update] [--commit] [--push]" >&2; exit 2 ;;
  esac
done

# The flake output is NOT named after the host: nixos-hp is built from
# nixosConfigurations.nixos-eval (flake.nix). Getting this wrong is the
# "does not provide attribute" error.
attr="${NIXOS_ATTR:-}"
if [ -z "$attr" ]; then
  case "$(hostname)" in
    nixos-hp) attr="nixos-eval" ;;
    nixos-nitro) attr="nixos-nitro" ;;
    *) echo "no flake output mapped for host '$(hostname)' -- set NIXOS_ATTR=..." >&2
       echo "available: $(nix flake show --json 2>/dev/null | jq -r '.nixosConfigurations|keys|join(\", \")' 2>/dev/null || echo '(see flake.nix)')" >&2
       exit 1 ;;
  esac
fi

echo "==> git pull"
git pull --ff-only

echo "==> syncing live config into $repo"
./noctalia/sync.sh pull
./home/sync.sh pull
# ~/.config/niri is an mkOutOfStoreSymlink into this repo (home.nix), so the
# niri config needs no sync step -- the live files ARE the tracked ones.

# A dirty tree builds fine (nix uses the working copy of tracked files), but
# UNTRACKED files are silently excluded from the flake -- a new .nix file you
# forgot to `git add` just doesn't exist as far as the build is concerned.
untracked="$(git ls-files --others --exclude-standard)"
if [ -n "$untracked" ]; then
  echo "!!  untracked files -- these are INVISIBLE to the flake build:" >&2
  printf '      %s\n' $untracked >&2
  echo "    git add them first if the build is supposed to see them." >&2
fi

git status --short || true

if [ "$do_update" = 1 ]; then
  echo "==> nix flake update nixpkgs"
  nix flake update nixpkgs
fi

echo "==> nixos-rebuild $action --flake .#$attr"
sudo nixos-rebuild "$action" --flake ".#$attr"

if [ "$do_commit" = 1 ]; then
  if git diff --quiet && git diff --cached --quiet; then
    echo "==> nothing to commit"
  else
    git add -A
    git commit -m "Sync config ($(date +%Y-%m-%d), rebuilt $action on $(hostname))"
    echo "==> committed"
  fi
  if [ "$do_push" = 1 ]; then
    git push
    echo "==> pushed"
  fi
fi
