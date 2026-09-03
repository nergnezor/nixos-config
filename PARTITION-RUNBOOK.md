# Dual-boot partition runbook — `nvme0n1` internal disk

## Status: done (2026-09-03), actual layout differs from the original plan

Shrink was done via **GParted in a live-nix USB**, not the manual
resize2fs/parted steps below (kept for reference — still valid if you ever
need to redo this by hand). Reclaimed **~250GiB**, not the originally
planned 50GiB. The MSR partition (`p2`, 16MiB) got removed along the way —
harmless, this machine has no Windows install to need it. `efibootmgr`'s
stray "Windows Boot Manager" NVRAM entry and `/boot/efi/EFI/Microsoft/` were
also cleaned up (dead references, no actual Windows install behind them).

**Because `p2` was removed, the new partitions did NOT land on `p6`/`p7`**
as originally planned — `parted mkpart` fills the lowest free number.
Actual layout:

```
p1  100MiB    EFI System   (Ubuntu's ESP — unchanged)
p5  ~1.6TiB   ext4 /       (Ubuntu, shrunk)
p2  512MiB    vfat         (NixOS's ESP — new, label NIXBOOT)
p3  249.1GiB  ext4         (NixOS's root — new, label nixos)
```

Two separate ESPs (`p1` for Ubuntu, `p2` for NixOS) means NixOS's
`systemd-boot` never touches Ubuntu's GRUB — switch OS at boot via the
firmware boot menu (**F9** on this HP at the POST screen).

**`hosts/hp-envy.nix` was not yet updated for this** — it doesn't reference
partition numbers directly (that's `hardware-configuration.nix`, generated
fresh below), so no changes needed there for the layout itself.

## Reference: the original manual steps (not what was actually run this time)

Only relevant if redoing this by hand instead of GParted. Requires live
media — ext4 can't shrink while mounted as the running root; growing back
is fine live (`resize2fs` online-grows).

```
sudo e2fsck -f /dev/nvme0n1p5
sudo resize2fs /dev/nvme0n1p5 <target-size>M   # a bit below final partition size
sudo parted /dev/nvme0n1 unit MiB resizepart 5 <end>
sudo resize2fs /dev/nvme0n1p5                   # no size arg -- fill the shrunk partition exactly
sudo e2fsck -f /dev/nvme0n1p5                   # verify
sudo parted /dev/nvme0n1 --script mkpart ESP fat32 <start> <end> set <N> esp on mkpart primary ext4 <start> 100%
sudo mkfs.fat -F32 -n NIXBOOT /dev/nvme0n1p<N>
sudo mkfs.ext4 -L nixos /dev/nvme0n1p<M>
```

Watch for `parted`'s auto-assigned partition numbers not being what you
expect if earlier partitions were deleted (exactly what happened here) —
check with `lsblk` after `mkpart`, don't assume.

## Install from the running Ubuntu session — no live boot needed for this part

Same technique that worked on the USB smoke test — Nix and `nixos-install`
both run fine on a non-NixOS host, so the install onto `p2`/`p3` happens
right here.

```
sudo mount /dev/nvme0n1p3 /mnt
sudo mkdir -p /mnt/boot
sudo mount /dev/nvme0n1p2 /mnt/boot
```

```
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  profile install github:NixOS/nixpkgs/nixos-25.05#nixos-install-tools
```

(Skip if already installed from the USB smoke test — check with
`nix profile list`. **Use the explicit `github:NixOS/nixpkgs/nixos-25.05#...`
URL, not bare `nixpkgs#nixos-install-tools`** — the bare form resolves
through the global flake registry to whatever `nixpkgs` currently points at,
which hit a `26.11pre` unstable dev snapshot with a broken chroot/bootloader
step on the USB attempt.)

```
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  shell nixpkgs#nixos-install-tools --command nixos-generate-config --no-filesystems --root /mnt
cp /mnt/etc/nixos/hardware-configuration.nix ~/nixos-config/hardware-configuration.nix
```

Build locally first, then copy, rather than letting `nixos-install` build
directly against `--store /mnt` — worked reliably on the USB target once we
started doing it this way. **Use `~/` for the out-link, not `/tmp`** —
`/tmp` got swept by systemd-tmpfiles mid-session on the USB attempt and the
symlink (and its GC-root protection) disappeared:

```
cd ~/nixos-config
nix --extra-experimental-features "nix-command flakes" \
  build .#nixosConfigurations.nixos-eval.config.system.build.toplevel \
  --out-link ~/nixos-eval-system
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  copy --to /mnt ~/nixos-eval-system --no-check-sigs
sudo env PATH="$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH" \
  nixos-install --root /mnt --system ~/nixos-eval-system
```

(`--no-check-sigs` on the copy: needed for locally-built, unsigned output —
hit `error: ... lacks a signature by a trusted key` without it on the USB
attempt.)

Set a root/user password when prompted, then `reboot` and use the firmware
boot menu (F9) to pick NixOS instead of Ubuntu.

## If you want the 250GiB back later

Delete `p2`/`p3`, then grow `p5` back — online, no live-USB needed (ext4
online-grow is supported, unlike shrink):

```
sudo parted /dev/nvme0n1 rm 2 rm 3
sudo parted /dev/nvme0n1 resizepart 5 100%
sudo resize2fs /dev/nvme0n1p5
```
