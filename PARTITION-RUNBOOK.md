# Dual-boot partition runbook — reclaim 50GiB from `nvme0n1p5` for NixOS

Run this from a **NixOS minimal/graphical installer live USB** (boots to a root
shell or a live desktop with a terminal). Do NOT run any of this from the
running Ubuntu system — ext4 can't shrink while mounted as `/`.

Exact numbers below were computed from this machine's actual partition table
(`erik-HP-ENVY-TE01-1xxx`, `/dev/nvme0n1`, 1.8T, GPT/UEFI) on 2026-09-02:

```
p1  100MiB   EFI System        (Ubuntu's ESP — DO NOT TOUCH)
p2   16MiB   Microsoft reserved(DO NOT TOUCH)
p5  ~1.8TiB  ext4 / (Ubuntu)   (start=117MiB) — SHRINK this
```

Target layout after this runbook:

```
p1  100MiB   EFI System        (unchanged, Ubuntu's)
p2   16MiB   MSR                (unchanged)
p5  1812.90GiB ext4 /           (shrunk, end=1856529MiB)
p6  512MiB   EFI System        (new — NixOS's own ESP)
p7  ~49.5GiB (rest to 100%)     (new — NixOS root, format during nixos-install)
```

Two separate ESPs means NixOS's `systemd-boot` never touches Ubuntu's GRUB —
switch OS at boot via the firmware boot menu (**F9** on this HP at the POST
screen).

## 0. Sanity check before touching anything

```
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT
```

Confirm you see `nvme0n1` with `p1` (100M, fat32), `p2` (16M), `p5` (~1.8T,
ext4), and that `p5` is **not mounted** (live USB, so it shouldn't be). If the
device name or sizes don't match the table above, STOP — the numbers below
are only valid for this exact disk.

## 1. Filesystem check (must pass clean before shrinking)

```
sudo e2fsck -f /dev/nvme0n1p5
```

If this reports errors it can't fix automatically, stop and investigate —
don't shrink an unhealthy filesystem.

## 2. Shrink the filesystem (a bit below the final partition size, safety margin)

```
sudo resize2fs /dev/nvme0n1p5 1856400M
```

Takes a few minutes — 880G used but only the tail 50G region needs
relocating, most of the filesystem is untouched.

## 3. Shrink the partition to match

```
sudo parted /dev/nvme0n1 unit MiB resizepart 5 1856529
```

`resizepart` asks to confirm — it may warn the new end is inside the
filesystem's old bounds; that's expected since we shrunk the fs first.

## 4. Grow the filesystem back to fill the new (smaller) partition exactly

```
sudo resize2fs /dev/nvme0n1p5
```

(No size argument — fills whatever the partition now is, closing the 129MiB
safety margin from step 2.)

## 5. Verify Ubuntu's partition is intact

```
sudo e2fsck -f /dev/nvme0n1p5
sudo parted /dev/nvme0n1 unit MiB print
```

`p5` should now show size ≈ `1856412MiB` (1812.9GiB), end `1856529MiB`, and
free space after it to the end of the disk. **Do not reboot into Ubuntu yet**
to double check — finish creating p6/p7 first so you don't have to re-run a
live USB session twice.

## 6. Create the two new partitions for NixOS

```
sudo parted /dev/nvme0n1 --script \
  mkpart ESP fat32 1856529MiB 1857041MiB \
  set 6 esp on \
  mkpart primary ext4 1857041MiB 100%
```

This creates `p6` (512MiB, ESP, boot flag set) and `p7` (rest, ~49.5GiB,
unformatted).

## 7. Format the new partitions

```
sudo mkfs.fat -F32 -n NIXBOOT /dev/nvme0n1p6
sudo mkfs.ext4 -L nixos /dev/nvme0n1p7
```

## 8. Reboot back to Ubuntu — the risky part is done

Steps 0-7 are the only ones that actually need live media (ext4 can't shrink
while mounted as the running root). `p6`/`p7` now exist as real, formatted
partitions on disk — they persist regardless of which OS boots next.
`reboot`, remove the live-boot media, let it come up as Ubuntu normally.

## 9. Install from the running Ubuntu session — no live boot needed for this part

Learned the hard way on the USB-stick smoke test: Nix and `nixos-install`
both run fine on a non-NixOS host (same premise `nixos-anywhere` is built
on), so the actual install onto `p6`/`p7` happens right here, same as the
USB target did — just onto reliable internal NVMe instead of a USB stick
this time. Needs `nix` installed here already (it is, from the smoke test —
`/nix/var/nix/profiles/default/bin/nix`).

```
sudo mount /dev/nvme0n1p7 /mnt
sudo mkdir -p /mnt/boot
sudo mount /dev/nvme0n1p6 /mnt/boot
```

```
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  profile install github:NixOS/nixpkgs/nixos-25.05#nixos-install-tools
```

**Use the explicit `github:NixOS/nixpkgs/nixos-25.05#...` URL, not bare
`nixpkgs#nixos-install-tools`** — the bare form resolves through the global
flake registry to whatever `nixpkgs` currently points at (hit a `26.11pre`
unstable dev snapshot on the USB attempt, whose `nixos-install` had a broken
chroot/bootloader step). Pinning to the same nixos-25.05 this config
actually uses avoids that mismatch.

```
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  shell nixpkgs#nixos-install-tools --command nixos-generate-config --no-filesystems --root /mnt
cp /mnt/etc/nixos/hardware-configuration.nix ~/nixos-config/hardware-configuration.nix
```

Prefer building locally first, then copying, rather than letting
`nixos-install` build directly against `--store /mnt` — even onto the fast
internal NVMe there's no reason to give that up, and it's what worked
reliably on the USB target once we started doing it this way:

```
cd ~/nixos-config
sudo env PATH="$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH" \
  nix --extra-experimental-features "nix-command flakes" \
  build .#nixosConfigurations.nixos-eval.config.system.build.toplevel \
  --out-link /tmp/nixos-eval-system
sudo /nix/var/nix/profiles/default/bin/nix --extra-experimental-features "nix-command flakes" \
  copy --to /mnt /tmp/nixos-eval-system
sudo env PATH="$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH" \
  nixos-install --root /mnt --system /tmp/nixos-eval-system
```

Set a root/user password when prompted, then `reboot` and use the firmware
boot menu (F9) to pick NixOS instead of Ubuntu.

## After you're done evaluating (or if you abandon it)

Nothing here is destructive to undo: boot Ubuntu, and if you want the 50GiB
back, delete `p6`/`p7` and `resizepart 5 100%` (grow, not shrink — much
safer, no live-USB needed... actually growing also needs the fs unmounted for
ext4? No — **ext4 online grow is supported**, so growing p5 back can be done
live from Ubuntu with `sudo parted /dev/nvme0n1 resizepart 5 100%` followed
by `sudo resize2fs /dev/nvme0n1p5`, no reboot required).
