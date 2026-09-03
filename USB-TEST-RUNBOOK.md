# Smoke-test install — full config on the 14.6GB USB stick

Device roles (confirmed 2026-09-02):
- **Boot medium:** the SD card in the built-in reader, `/dev/mmcblk0` on this
  Ubuntu install (7.4GiB actual, "8GB" nominal). Gets the installer ISO
  written to it via `dd`.
- **Install target:** `/dev/sda`, the 14.6GB "USB DISK" stick that was
  running Ventoy — `disko-usb.nix` wipes and repartitions it. The ISO that
  was on it (`nixos-gnome-24.11...iso`) has already been copied to
  `~/Downloads/` before this, so wiping it is safe.

Same full app set as the real internal-disk install (`nixos-eval-usb` uses
the same `home.nix`) — 14.6GB fits it, but tightly (~12-18GB estimated for
one generation). Run `nix-collect-garbage -d` after the first successful
`nixos-install`/`nixos-rebuild` and between any rebuild iterations; don't
expect to keep multiple generations around like the 50GB internal install.

## 1. Write the ISO to the SD card (run yourself, needs sudo)

```
! lsblk -o NAME,SIZE,TYPE,TRAN,MODEL   # reconfirm mmcblk0 is still the SD card, not something else
! sudo dd if=~/Downloads/nixos-gnome-24.11.717196.9684b53175fc-x86_64-linux.iso of=/dev/mmcblk0 bs=4M status=progress conv=fsync
```

## 2. Boot it

Reboot, **F9** at the HP logo, pick the SD card reader as the boot device.

## 3. In the live NixOS session

```
lsblk -o NAME,SIZE,TYPE,TRAN,MODEL
```
Confirm the 14.6GB stick is still `/dev/sda` (not the boot SD card, not
`nvme0n1`). If it enumerated as something else, edit `disko-usb.nix`'s
`device` line before the next step.

```
nmtui   # or the network icon, get online
git clone https://github.com/nergnezor/nixos-config.git
cd nixos-config
```

## 4. Partition + format + mount via disko

```
sudo nix --extra-experimental-features "nix-command flakes" \
  run github:nix-community/disko -- --mode disko ./disko-usb.nix
```

This formats `/dev/sda` per `disko-usb.nix` and mounts it at `/mnt`.

## 5. Generate hardware config, install

```
sudo nixos-generate-config --no-filesystems --root /mnt
cp /mnt/etc/nixos/hardware-configuration.nix ./hardware-configuration.nix
sudo nixos-install --flake .#nixos-eval-usb
```

Set a password when prompted, `reboot`, remove the SD card, F9 → pick the
USB stick this time.

## What you're actually checking

- Does it boot to a niri session at all (nvidia driver working)?
- Does noctalia-shell render correctly?
- Spot-check a few apps ephemerally: `nix run nixpkgs#steam` etc, then
  `nix-collect-garbage -d` before the next one (see chat — same 8GB-staging
  idea applies here as extra headroom insurance even though the full set is
  in `home.nix`).
- niri feel/responsiveness is **not** representative here (USB is slow) —
  save that judgment for the internal NVMe install.

If this works, `PARTITION-RUNBOOK.md` + `nixos-eval` (internal disk) is next.

## Reusing the same stick on Nitro (Intel Arc A750)

Same steps, with two changes: re-confirm `/dev/sda` in step 3 (no guarantee
it enumerates the same on different hardware), and swap the flake target in
step 5 — `sudo nixos-install --flake .#nixos-eval-nitro-usb`. That target
pulls in `hosts/nitro.nix` instead (Intel graphics packages, Nitro's own
Ubuntu-partition UUID for the `/home/erik` share) rather than HP's nvidia
config — confirmed via SSH 2026-09-03, not re-verified against nixos-unstable
itself.
