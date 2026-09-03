# Smoke-test install — full config on the 14.6GB USB stick

Device roles (confirmed 2026-09-02):
- **Install target:** `/dev/sda`, the 14.6GB "USB DISK" stick that was
  running Ventoy — `disko-usb.nix` wipes and repartitions it. The ISO that
  was on it (`nixos-gnome-24.11...iso`) was already copied to
  `~/Downloads/` before this, so wiping it is safe.

Same full app set as the real internal-disk install (`nixos-eval-usb` uses
the same `home.nix`) — 14.6GB fits it, but tightly (~12-18GB estimated for
one generation). Run `nix-collect-garbage -d` after the first successful
`nixos-install`/`nixos-rebuild` and between any rebuild iterations; don't
expect to keep multiple generations around like the 50GB internal install.

## Method: install directly from this Ubuntu session, no live boot needed

Nix (the package manager) runs fine on any Linux, including Ubuntu — it
doesn't require actually running NixOS. `nixos-install-tools` (a normal
nixpkgs package) works the same way from a "foreign" host, same principle
`nixos-anywhere` is built on. So the whole USB install — partition, format,
build, install — happens right here; the only reboot is into the *finished*
result.

Every step below needs a real sudo password prompt, so run these yourself
(`!`-prefixed, or in your own terminal) — not something I can drive through
the sandboxed tool session.

### 1. Install Nix on this machine (one-time; official multi-user installer)

```
! sh <(curl -L https://nixos.org/nix/install) --daemon
```

Interactive — confirms before making changes, asks for sudo. Afterwards,
open a **new** terminal (or `. /etc/profile.d/nix-daemon.sh`) so `nix` is on
PATH.

### 2. Partition + format + mount the stick via disko

```
! lsblk -o NAME,SIZE,TYPE,TRAN,MODEL   # reconfirm sda is still the 14.6GB stick
! sudo nix --extra-experimental-features "nix-command flakes" \
    run github:nix-community/disko -- --mode disko ~/nixos-config/disko-usb.nix
```

Formats `/dev/sda` per `disko-usb.nix`, mounts the result at `/mnt`.

### 3. Generate hardware config

```
! sudo nix --extra-experimental-features "nix-command flakes" \
    shell nixpkgs#nixos-install-tools --command nixos-generate-config --no-filesystems --root /mnt
! cp /mnt/etc/nixos/hardware-configuration.nix ~/nixos-config/hardware-configuration.nix
```

(Package/command name for `nixos-install-tools` unverified from here — no
`nix` on this machine until step 1 runs. If `nix shell ... --command X`
doesn't resolve, `nix shell nixpkgs#nixos-install-tools` into a subshell and
run `nixos-generate-config` directly from there instead.)

### 4. Install

```
! cd ~/nixos-config && sudo nix --extra-experimental-features "nix-command flakes" \
    shell nixpkgs#nixos-install-tools --command nixos-install --root /mnt --flake .#nixos-eval-usb
```

Set a password when prompted at the end.

### 5. Reboot

`reboot`, **F9** at the HP logo, pick the USB stick.

## Fallback: live-ISO method (if step 1-4 above hits a wall)

The SD card is still flashed with the NixOS 24.11 installer ISO if the
direct-from-Ubuntu path runs into trouble — boot it (F9 → SD card reader),
get online (`nmtui`), `git clone https://github.com/nergnezor/nixos-config.git`,
then the same disko/generate-config/install commands as above, just without
the `!`/sudo-from-Ubuntu wrapping (you're already root-capable in the live
session) and reading `PARTITION-RUNBOOK.md`-style, one command at a time.

## What you're actually checking

- Does it boot to a niri session at all (nvidia driver working)?
- Does noctalia-shell render correctly?
- Spot-check a few apps ephemerally: `nix run nixpkgs#steam` etc, then
  `nix-collect-garbage -d` before the next one — same 8GB-staging idea as
  extra headroom insurance even though the full set is in `home.nix`.
- niri feel/responsiveness is **not** representative here (USB is slow) —
  save that judgment for the internal NVMe install.

If this works, `PARTITION-RUNBOOK.md` + `nixos-eval` (internal disk) is next.

## Reusing the same stick on Nitro (Intel Arc A750)

Same method — install Nix on Nitro's Ubuntu too (or run the disko/install
commands over SSH from here, targeting the stick once it's plugged into
Nitro and you're `ssh uxstream`'d in). Re-confirm `/dev/sda` before disko —
no guarantee it enumerates the same on different hardware — and swap the
flake target: `--flake .#nixos-eval-nitro-usb`. That pulls in
`hosts/nitro.nix` (Intel graphics packages, Nitro's own Ubuntu-partition
UUID) instead of HP's nvidia config.
