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

`p3` is btrfs (switched from the ext4 in the GParted notes above) —
mount with `compress=zstd` so new writes match `hosts/hp-envy.nix`.

The repeatable sequence (build, copy, `nixos-install`) is
`install-to-nixos-partition.sh`. First-time extras below
(`nixos-install-tools`, `nixos-generate-config`) are only needed once.

```
sudo mount -o compress=zstd /dev/nvme0n1p3 /mnt
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

## `/home` on its own partition, keeping Ubuntu bootable

### Where things stand

p5 (1.6TiB) is today both Ubuntu's root *and* the home directory: ~760GiB of
home plus ~150GiB of Ubuntu system tree (~41GiB of that is just
`swapfile.ue` and `swap.img`). 929GiB used, ~579GiB free. There is no
unallocated space anywhere on the disk.

The mount stays as it is until the move below is done: p5 at `/mnt/ubuntu`,
with `/mnt/ubuntu/home/erik` bind-mounted onto `/home/erik`. **Mounting p5
at `/home` directly does not work** — p5's root is Ubuntu's `/`, so the home
directory on it sits at `home/erik` relative to that root, and mounting p5
at `/home` puts the real home at `/home/home/erik` while `/home/erik` comes
up empty. The bind mount is what strips that leading `home/`. It can only go
away once p5 no longer has Ubuntu's directory layout (step 5).

### The plan: move Ubuntu *out*, don't move home

Home is 760GiB and there is nowhere on this disk to put a second copy of it,
so home never moves. Ubuntu's system tree is ~110GiB once the swapfiles are
gone — small enough to relocate. Move Ubuntu to its own partition and p5 is
left as a dedicated home partition, with Ubuntu still bootable.

**Step 1 — trim Ubuntu (done 2026-09-04).** Freed ~105GiB before copying
anything, taking p5 from 929GiB used to 824GiB:

- `swapfile.ue` + `swap.img` at p5's root, ~41GiB. Ubuntu fails to swapon
  until its `/etc/fstab` line goes too — tidy that in step 3.
- `/var/lib/apport/coredump`, ~27GiB of UnrealEditor coredumps. Ubuntu never
  expires these.
- `/nix`, ~38GiB — the Nix that bootstrapped NixOS from Ubuntu (see
  `install-to-nixos-partition.sh`). Dead weight now that NixOS boots and
  rebuilds itself. Only keep it if you want to reinstall NixOS *from*
  Ubuntu again. Safe to delete from a running NixOS: that store is p5's,
  while the live one is p3's `/nix/store`.

flatpak (8.2GiB) and snapd (7.7GiB) were deliberately kept — mouseless
lives in the flatpak one.

That leaves Ubuntu's system tree at **~45GiB**, against 760GiB of home.

**Step 2 — shrink p5 and create Ubuntu's new partition.** ext4 cannot shrink
while mounted, and `/mnt/ubuntu` is mounted at boot, so this needs a **live
USB** (GParted, as in the shrink at the top of this file). Shrink p5 to
~1.45TiB and create a new **100GiB** partition in the freed space. 100GiB
is a bit over twice what Ubuntu now occupies: enough headroom for apt and
even a distro upgrade, and there is no reason to shave it closer when the
remainder returns to home in step 4 either way. Note the
number `parted` assigns — it fills the lowest free slot, which bit us last
time; check `lsblk`, don't assume it's p6.

```
sudo mkfs.ext4 -L ubuntu /dev/nvme0n1pN
```

### Step 2 went wrong: p5's filesystem is corrupt (2026-09-04)

The shrink itself worked. The filesystem on it did not survive.

Confirmed layout after the GParted run — geometry is sound, so this is
*not* a moved start sector or a filesystem that outgrew its partition:

```
p1        2048s -     206847s   100MiB   fat32  Ubuntu ESP
p5      206848s - 3178780671s   1.48TiB  ext4   Ubuntu / + home   <- shrunk
   3178780672s - 3383547903s   97.6GiB  free   <- Ubuntu's new partition goes here
p2  3383547904s - 3384596479s   512MiB   fat32  NIXBOOT
p3  3384596480s - 3907028991s   249GiB   btrfs  nixos
```

p5 spans 3 178 573 824 sectors; the filesystem wants 397 317 632 x 4KiB
blocks = 1.4801TiB. It fits, with ~16MiB to spare. `blkid` reads the
superblock and the UUID still matches `hosts/hp-envy.nix`. Note the free
space is 97.6GiB, not the 100GiB planned above — take `100%` when creating
the partition rather than naming a size.

What is broken is the content. `dumpe2fs` reports `Filesystem state: clean
with errors`, and a read-only walk of the mounted filesystem returns
`Structure needs cleaning` (EUCLEAN) and `Bad message` (EBADMSG —
`metadata_csum` mismatch) across large parts of `/home/erik`:
`.local/share`, `.local/state`, `.local/bin`, `.vscode/extensions`,
`Midswimmer`, Steam's `appcache`. `du` could only traverse 161GiB of the
~760GiB.

That spread points at the resize rather than at the deletions in step 1:
`resize2fs` relocates blocks out of the tail being removed into free space
earlier in the filesystem, so an interrupted or mis-written relocation
damages metadata all over the tree, not just at the end.

Neither OS mounts it as a result — NixOS has `nofail` on `/mnt/ubuntu` and
skips it silently (empty `/home/erik`), Ubuntu fails fsck on its own root in
the initramfs.

**Do not run `e2fsck -fy` as the first move.** At this scale `-y` answers
yes to thousands of `Clear?` prompts and empties files or dumps them into
`lost+found` as numbered inodes, with no backup to fall back on. Order:

1. **Rule out the hardware.** `smartctl -a /dev/nvme0n1`, and `dmesg` for
   `nvme`/`I/O error`/`EXT4-fs error`. Then run **memtest86+** — bad RAM
   during a several-hundred-GiB block relocation produces exactly this
   pattern of scattered checksum failures, and would also explain the
   garbled console text seen on the NixOS boot. Repairing on faulty
   hardware just writes fresh corruption.
2. **Rescue the irreplaceable subset read-only, before repairing.** Most of
   what errors is cache (Steam appcache, vscode extensions, `.local/share`)
   and worth nothing. `~/projects`, `~/.ssh`, `~/.gitconfig`, `~/.claude`
   and documents are small enough to fit in the 97.6GiB hole after p5 if
   there is no external disk. Use `rsync -aHAX --numeric-ids
   --ignore-errors` so it walks past the unreadable directories instead of
   aborting, and keep the log — what it could *not* read is the real
   damage report.
3. **Then size the damage:** `e2fsck -fn /dev/nvme0n1p5 | tee ~/fsck-p5.log`.
   Hundreds of findings means `-fy` is reasonable once the rescue is done.
   Tens of thousands means p5 is better treated as lost: `mkfs.ext4` it and
   rebuild home from the rescued subset, which makes steps 3-5 below
   simpler rather than harder.

Steps 3-5 below assume a healthy p5 and are on hold until this is resolved.

**Step 3 — copy Ubuntu across.** Still from the live USB, with both
mounted (`/mnt/old` = p5, `/mnt/new` = the new partition):

```
sudo rsync -aHAXv --numeric-ids \
  --exclude=/home --exclude=/lost+found \
  --exclude={/dev/*,/proc/*,/sys/*,/tmp/*,/run/*,/mnt/*,/media/*} \
  /mnt/old/ /mnt/new/
```

`-H -A -X --numeric-ids` matter: hardlinks, ACLs, xattrs and raw uid/gid.
Without them a copied root filesystem boots subtly broken.

Then point Ubuntu at its new home — inside `/mnt/new`:

- `/etc/fstab`: change the `/` entry to the new partition's UUID
  (`blkid`), add a `/home` entry for p5's UUID
  (`ee53b2ae-86cb-42a9-8ef6-c3e7bbd1908e`), and delete the swapfile lines.
  Note that Ubuntu mounting p5 at `/home` *is* correct for Ubuntu — its
  home is p5's `home/` and it wants exactly that directory at `/home`. The
  asymmetry with NixOS is the whole reason NixOS needs the bind mount.
- Reinstall GRUB against the new root (chroot with `/dev`, `/proc`, `/sys`
  and Ubuntu's ESP p1 bind-mounted in, then `update-grub` and
  `grub-install /dev/nvme0n1`).

**Step 4 — verify, then reclaim.** Boot Ubuntu via F9 and confirm it comes
up on the new partition (`findmnt /`) with home intact. Only then delete
Ubuntu's leftovers from p5, from a **NixOS** boot:

```
sudo find /mnt/ubuntu -mindepth 1 -maxdepth 1 ! -name home ! -name lost+found -exec rm -rf {} +
```

The `! -name home` guard is all that stands between this command and
760GiB — read it twice before pressing enter.

Then grow p5 back over the freed space (`parted resizepart` then
`resize2fs` — ext4 grows online, no live USB needed) and relabel it:
`sudo e2label /dev/nvme0n1p5 home`.

**Step 5 — flatten p5, and only then drop the bind mount.** After step 4,
p5 contains a single `home/` directory, so it still needs the bind mount.
To be rid of it, move the contents up a level so p5's root *is* the home
directory. This has to happen with p5 not mounted at `/home` — do it from a
live USB with p5 at `/mnt`:

```
sudo mv /mnt/home/erik /mnt/erik && sudo rmdir /mnt/home
```

`mv` within one filesystem is a rename, so this is instant regardless of the
760GiB. Ubuntu is gone by now, so nothing else expects the old layout. Then
`hosts/hp-envy.nix` can finally collapse to:

```nix
fileSystems."/home" = {
  device = "/dev/disk/by-uuid/ee53b2ae-86cb-42a9-8ef6-c3e7bbd1908e";
  fsType = "ext4";
  options = [ "rw" "nofail" ];
};
```

### Later, when Ubuntu really goes

Delete its partition and its 100MiB ESP p1, and drop the NVRAM entry:

```
sudo efibootmgr                 # find the "ubuntu" entry number
sudo efibootmgr -b <N> -B
```

Growing `/` (p3) is not possible by reclaiming p1: it is 100MiB and at the
wrong end of the disk, and p3 sits between p2 and p5, so growing it means
shrinking p5 from the front, which ext4 cannot do. If `/` ever needs more
room, put `/nix` on p5 instead.
