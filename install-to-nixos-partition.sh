#!/usr/bin/env bash
# Rebuild nixos-eval on Ubuntu, copy the closure onto nvme0n1p3, run nixos-install.
# p3 = NixOS root (btrfs, label nixos), p2 = NixOS ESP (vfat, label NIXBOOT).
set -euo pipefail

NIX=/nix/var/nix/profiles/default/bin/nix
NIX_XTRA='--extra-experimental-features nix-command flakes'
ROOT=/dev/nvme0n1p3
ESP=/dev/nvme0n1p2
OUT="$HOME/nixos-eval-system"
FLAKE="$HOME/nixos-config#nixosConfigurations.nixos-eval.config.system.build.toplevel"

if [[ ! -x $NIX ]]; then
  echo "missing $NIX" >&2
  exit 1
fi
if findmnt /mnt >/dev/null || findmnt /mnt/boot >/dev/null; then
  echo "/mnt or /mnt/boot already mounted — unmount first" >&2
  exit 1
fi

cd "$HOME/nixos-config"
$NIX $NIX_XTRA build "$FLAKE" --out-link "$OUT"

sudo mount -o compress=zstd "$ROOT" /mnt
sudo mkdir -p /mnt/boot
sudo mount "$ESP" /mnt/boot

sudo $NIX $NIX_XTRA copy --to /mnt "$OUT" --no-check-sigs
sudo env PATH="$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH" \
  nixos-install --root /mnt --system "$OUT"
