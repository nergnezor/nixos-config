# Partition runbook — `nvme0n1` internal disk

## Where things stand (2026-09-04)

This machine is **NixOS-only**. Ubuntu is gone, and the home directory was
rebuilt from a rescue after an interrupted `resize2fs` destroyed the
filesystem it lived on. The narrative of that is further down; this section
is now a record of how the recovery finished, not a resume point.

Current layout — Ubuntu's old ESP is gone, nothing on the disk references it
anymore:

```
p2  512MiB    vfat    /boot        label NIXBOOT, systemd-boot
p3  249.1GiB  btrfs   /            label nixos, compress=zstd
p5  1.58TiB   btrfs   /home        label "home", subvols @home + @snapshots
```

**Done:**

- p5 remade as btrfs, `/home` restored from `/mnt/nixos/rescue3` (44GB,
  30288 files) into `@home/erik`, owned 1000:1000
- nested subvolumes for `erik/.cache`, `erik/Downloads`,
  `erik/.local/share/Steam` so snapshots of `@home` stay small
- `hosts/hp-envy.nix` collapsed to one `fileSystems."/home"` on
  `/dev/disk/by-label/home` — no `/mnt/ubuntu`, no bind mount, no vivaldi
  bind
- Swedish console keymap and a console font added (the login was unusable
  without them), generation limit of 5 on the 512MiB ESP, 3s boot timeout
- new generation built from the live USB via
  `nixos-enter --root /mnt/nixroot -- nixos-rebuild boot --flake
  /home/erik/nixos-config#nixos-eval`, password set
- booted the installed system from p2 and confirmed `/home`, `/boot`, `/`
  all mount correctly and `~/.config/niri` resolves through the
  out-of-store symlink into the tracked `niri/` — `nvidia_drm.fbdev=1` was
  already in `hosts/hp-envy.nix`, so the console was never garbled on this
  boot
- Ubuntu's EFI leftovers removed: `Boot0000` ("Ubuntu") and the `UEFI OS`
  entry pointing at p1's GUID were deleted (identify by matching the
  device-path GUID against `blkid`/`lsblk -o PARTUUID`, not by assuming
  entry numbers — they had already shifted from what an earlier revision of
  this file recorded), then `parted ... rm 1` removed the partition itself.
  **`efibootmgr` and `parted` are not installed anywhere on this system**
  (every earlier use was from the live USB, which bundles them) — ran both
  ad hoc: `sudo nix --extra-experimental-features "nix-command flakes" run
  github:NixOS/nixpkgs/nixos-25.05#efibootmgr -- <args>`, same pattern for
  `parted`. `sudo <tool>` alone fails with "command not found": `sudo`'s
  `secure_path` doesn't include the Nix profile paths erik's own shell has.
- first snapshot taken: `@snapshots/home-2026-09-04`. Reachable path was
  `sudo mount -o subvolid=5 /dev/nvme0n1p5 /mnt/p5top` first — `@snapshots`
  is a sibling of `@home` on p5, not a path under the `/home` mountpoint
  itself.

**Still to do:**

1. `flatpak --user remote-add --if-not-exists sonuscape
   https://dl.sonuscape.net/flatpak/sonuscape.flatpakrepo && flatpak --user
   install sonuscape net.sonuscape.mouseless` — it used to come from
   Ubuntu's flatpak. Not on Flathub; ships from its own repo (confirmed
   against https://mouseless.click/docs/getting_started.html#linux).
   Along the way, `~/.local/share/flatpak` (the *user* flatpak install) was
   found corrupt — `repo/config` was binary garbage, not the GKeyFile it
   claims to be, a leftover from the rescued Ubuntu home that predates even
   the resize incident. It broke every `flatpak` command with `opening
   repo: Invalid UTF-8`. Moved aside to
   `~/.local/share/flatpak.broken-rescue-leftover` (not deleted) and
   flathub re-added; safe to delete the `.broken-rescue-leftover` copy once
   mouseless is confirmed working. The *system* flatpak install
   (`services.flatpak.enable`, `/var/lib/flatpak`) was unaffected.
2. Decide when to delete the rescue copies at `/mnt/nixos/rescue2` and
   `/mnt/nixos/rescue3` (see below) — only once confident nothing else is
   missing.

**Pattern to watch for: scattered silent content corruption in rescued
files.** Beyond the flatpak repo config above, testing every app in
`home.nix`/`configuration.nix` after the first real boot (2026-09-04) turned
up three more casualties, all with the same signature as the original
`config.kdl` damage described earlier in this file — correct metadata
(size, timestamp) but content replaced by unrelated binary garbage
(sometimes literal x86 machine code fragments), so nothing flags it until
the file is actually read:

- `~/.local/share/icons/hicolor/icon-theme.cache` — every GTK4 app
  (ghostty, zenity, xdg-desktop-portal-gnome) segfaulted on launch,
  100% reproducible, because `gtk_icon_theme_has_icon` reads this cache on
  startup. Deleted; GTK4 regenerates it on demand, it's a pure cache.
- `~/.local/share/Steam/steam.sh` — `Exec format error`. Replaced from
  Steam's own `bootstrap.tar.xz` (already present, re-downloaded earlier).
- `~/.local/share/Steam/ubuntu12_32/steam-runtime.tar.xz` — `xz -t` failed
  ("File format not recognized"); its `.checksum` sidecar was also
  corrupted (all-zero bytes). This file is mandatory — `steam.sh` hard-exits
  without it, no `STEAM_RUNTIME=0` escape hatch — and steam.sh does not
  redownload it itself if missing, only on checksum mismatch against a
  present-but-wrong archive. Ended up doing a full reset of
  `~/.local/share/Steam` (kept `userdata/` and `config/`, moved the rest
  aside, let a fresh `steam` launch reinstall everything) rather than
  hunting file-by-file — faster once more than one corrupt file turned up
  in the same tree, and Steam's own installer is the only legitimate
  source for that archive (no working public direct-download URL found).

If something else acts inexplicably broken later — crashes on startup,
"unrecognized format", garbled output — suspect this class of damage before
anything else and check the file's content, not just that it exists.

Also worth knowing for next time: `~/.local/share/Steam` needs `DISPLAY`
set (`xwayland-satellite` runs on `:0`, per `configuration.nix`) when
launched from a shell that isn't a child of the niri session and so never
picked up the env var niri's systemd/dbus activation environment carries.

**What was lost**, for when something turns up missing: `~/projects` (all on
GitHub), `~/Documents` (the one thing with no copy anywhere), `Midswimmer`,
`midswimmer1`, `godot4`, `jobb`, `jdk17`, `gxloops`,
`UnrealEngine-Angelscript`, `~/.claude`, and the tool caches — `.cargo`,
`.rustup`, `.gradle`, `.sdkman`, `.vscode`, `.epic`. Steam game *installs*
were never rescued (excluded to save space) but re-download; saves under
`.local/share/Steam/userdata`, `.wine` and `.var` did come across.

The rescue copies are still on p3 at `/mnt/nixos/rescue2` and
`/mnt/nixos/rescue3` — mounted at `/rescue2`/`/rescue3` paths relative to
p3's root, not under `/home`. Delete them only once you are confident
nothing else is missing.

## History: how the filesystem was lost, and what got it back

### The original shrink (2026-09-03)

Done via **GParted in a live-nix USB**, not the manual resize2fs/parted steps
below (kept for reference — still valid if you ever need to redo this by
hand). Reclaimed ~250GiB. The MSR partition got removed along the way —
harmless, this machine has no Windows install. `efibootmgr`'s stray "Windows
Boot Manager" NVRAM entry and `/boot/efi/EFI/Microsoft/` were cleaned up too
(dead references, no actual Windows behind them).

`parted mkpart` fills the lowest free partition number, so the new
partitions landed on `p2`/`p3` rather than the `p6`/`p7` originally planned
— check `lsblk` after `mkpart`, don't assume.

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

What is broken is the content. **Cause: an interrupted `resize2fs`.**
`resize2fs` relocates blocks out of the tail being removed and rewrites the
block bitmap as it goes, so killing it midway leaves the bookkeeping
half-written across the whole filesystem rather than damage confined to the
end. `dmesg` agrees — `initial error at ext4_validate_block_bitmap`, the
first structure a shrink touches.

**The drive is not at fault.** SMART reports `Media and Data Integrity
Errors: 0` — the counter for reads that failed ECC, i.e. exactly what a
hardware cause would show — with `Critical Warning: 0x00` and 2% wear. No
memtest needed either; the interrupted resize accounts for all of it. (The
garbled console on the NixOS boot is unrelated, see the keymap/font notes in
`configuration.nix`.)

Worth fixing separately: `Temperature Sensor 1: 90 C` against an 80 C
critical composite threshold, with 3 minutes already logged above the
warning threshold. A full `e2fsck` here is hours of sustained I/O — give the
machine airflow first so it does not throttle or trip mid-repair.

Symptoms as observed: `Filesystem state: clean with errors`, and a
read-only walk returning `Structure needs cleaning` (EUCLEAN) and `Bad
message` (EBADMSG — `metadata_csum` mismatch) across `/home/erik`:
`.local/share`, `.local/state`, `.local/bin`, `.vscode/extensions`,
`Midswimmer`, Steam's `appcache`. `du` traversed only 161GiB of ~760GiB.

Neither OS mounts it as a result — NixOS has `nofail` on `/mnt/ubuntu` and
skips it silently (empty `/home/erik`), Ubuntu fails fsck on its own root in
the initramfs.

`error count since last fsck: 7679` overstates the damage: it counts *hits*,
not distinct objects, and every one of those dmesg lines is `comm du` —
generated by the read-only walk above, against a read-only mount, so nothing
new was written. A directory re-walked ten times counts ten times. A large
share are also the mildest kind, `No space for directory leaf checksum.
Please run e2fsck -D.`, which is not data loss at all and names its own fix.
The serious ones are `bad extra_isize` and `invalid magic` on extent headers
— inodes whose table blocks were mid-relocation at the moment of the abort.

### Do not boot NixOS while p5 is damaged

`hosts/hp-envy.nix` mounts p5 at `/mnt/ubuntu` **rw** with `nofail`, and p5's
superblock carries `Errors behavior: Continue`. Booting the p3 install (or
the old disko USB stick, which carries the same host module) therefore
mounts the damaged filesystem writable and keeps writing through errors
rather than dropping to read-only — a good way to turn a repairable mess
into an unrepairable one. Stay in the live ISO until `e2fsck` completes.

A cheap safety net meanwhile, one superblock field that `e2fsck` rewrites
anyway:

```
sudo tune2fs -e remount-ro /dev/nvme0n1p5
```

### Recovery order

An interrupted resize is what `e2fsck` exists for: the data blocks are still
where they belong, it is the accounting that is half-written. Better odds
than the raw error count suggests — but repair is still one-way, so:

1. **Rescue the irreplaceable subset read-only first.** Most of what errors
   is cache (Steam appcache, vscode extensions, `.local/share`) and worth
   nothing. `~/projects`, `~/.ssh`, `~/.gitconfig`, `~/.claude` and
   documents fit in the 97.6GiB hole after p5 if there is no external disk.
   `rsync -aHAX --numeric-ids --ignore-errors` walks past the unreadable
   directories instead of aborting; keep the log, since what it could *not*
   read is the real damage report.
2. **Repair, reversibly.** `e2fsck -z` writes an undo file, so the whole
   repair can be rolled back with `e2undo` if it makes things worse. It must
   live on a different filesystem than the one being repaired — `/root` is
   on p3:

   ```
   sudo e2fsck -fy -C 0 -z /root/p5-fsck-undo /dev/nvme0n1p5 2>&1 | tee /root/fsck-p5-repair.log
   sudo e2undo /root/p5-fsck-undo /dev/nvme0n1p5     # only if it went badly
   ```
3. **Second pass for the directories,** which is what the kernel asked for,
   then verify:

   ```
   sudo e2fsck -fyD /dev/nvme0n1p5
   sudo e2fsck -fn  /dev/nvme0n1p5     # expect a clean run
   ```
4. **Read `lost+found` and the repair log** before trusting the result. If
   what landed there is unacceptable, p5 is better treated as lost: reformat
   and rebuild home from the rescued subset, which makes steps 3-5 below
   simpler rather than harder.

### First repair attempt aborted (2026-09-04)

Two lessons, both worth not repeating.

**Never write the log or the undo file to the live USB's `/root`.** The
NixOS live ISO keeps its root filesystem in RAM, so `tee /root/...` and
`e2fsck -z /root/...` both wrote to tmpfs: the log dies at reboot, and the
undo file for a 1.5TiB filesystem ran the tmpfs out of space, which is what
`while force-closing undo file` reports. A half-written undo file that could
not be closed is not trustworthy, so `e2undo` stopped being an option.
Mount p3 (`/mnt/nixos`) or an external disk and write there instead.

**Rescue really does have to come first.** The repair was started before the
read-only rsync, and `e2fsck` then aborted partway through pass 2:

```
ext2fs_read_inode: Inode checksum does not match inode while reading
inode 16466339 in check_filetype
e2fsck: aborted
***** FILE SYSTEM WAS MODIFIED *****
```

which leaves the filesystem half-repaired — worse than either untouched or
fully checked. It has to be run to completion.

**The damage is worse than the error count suggested.** A contiguous run of
directory inodes (5132823, 5132833, 5132834, 5132838, 5132846, 5132860,
5132868 ... 5133009) all report `block #0 ... directory corrupted` with
missing `.`/`..`, and one entry has a raw-binary name pointing at an inode
`in group 2010 where _INODE_UNINIT is set`. That is file content sitting
where an inode table belongs: the interrupted resize wrote data blocks over
metadata in that region. `e2fsck` cannot reconstruct that — it can only
orphan the children into `lost+found` under numeric names.

Revised order from here:

0. **Do not create Ubuntu's partition in the freed space yet, and do not
   use that space as the rescue target** (an earlier revision of this file
   said to — wrong). Those ~97.6GiB are the tail of the *old* filesystem.
   `resize2fs` relocates in-use blocks out of the region it is removing but
   never erases the originals, so that space still holds a copy of whatever
   lived in the last 97.6GiB before the shrink. `mkfs` there destroys it
   permanently. It is a long shot — the damage is in the inode tables
   around inodes 5.1M-16M, not in the tail — but if `e2fsck` destroys files
   with no other copy, carving that region with `photorec`/`ext4magic` is
   the only thing left to try, and preserving the option costs nothing.
   The partition belongs to step 2 below, which is blocked on p5 either way;
   create it once p5's fate is settled, so the final layout gets drawn once
   rather than in instalments.

   When it is time, both ends of the hole are already 1MiB-aligned
   (3178780672 / 2048 = 1552139, 3383547904 / 2048 = 1652123), so name the
   sectors outright rather than letting a GUI round them:

   ```
   sudo parted /dev/nvme0n1 mkpart ubuntu ext4 3178780672s 3383547903s
   sudo parted /dev/nvme0n1 unit s print     # read the number off -- p4 is the free slot, not p6
   ```

   Note also that GParted queues operations and does nothing until Apply is
   pressed, and that on a live USB it is easy to act on the stick instead of
   the internal disk — verify with `parted`, and check `lsblk` across *all*
   disks.
1. `mount -o compress=zstd /dev/nvme0n1p3 /mnt/nixos` — somewhere real to
   write logs and hold the rescue. p3 is btrfs, entirely separate from p5,
   and 249GiB against a NixOS install that uses a fraction of it. An
   external disk is better still, since it touches the internal drive not at
   all.
2. Rescue read-only *before* touching it again — but expect the kernel to
   block most of it. The first attempt got 7 files out of 11: the kernel
   refuses `readdir` on any directory whose checksum fails, so `~/projects`
   and `~/Documents/Unreal Projects` returned `Bad message` and `~/.claude`
   could not even be `stat`ed. Only `.ssh` and `.gitconfig` came across.
   (Also: `sudo rsync ... | tee` writes the log as the *unprivileged* user
   and fails on a root-owned destination — pipe to `sudo tee`.)

   So try `debugfs` next, which reads the on-disk structures directly and
   walks past the checksum failures the kernel enforces. It is the last
   read-only chance to get files out *with their names*, and it opens the
   device read-only without `-w`, so it cannot make anything worse:

   **`-c` is not optional here.** Plain `debugfs` reads the allocation
   bitmaps when it opens the filesystem, and the block bitmap is one of the
   structures the resize wrecked, so it fails before running the command at
   all: `Block bitmap checksum does not match bitmap while reading
   allocation bitmaps` / `Filesystem not open`. Catastrophic mode skips the
   bitmap load, which reading files does not need — inodes and extent trees
   are separate structures. `-c` does not imply `-w`; it stays read-only.

   ```
   sudo debugfs -c -R "ls -l /home/erik" /dev/nvme0n1p5
   sudo debugfs -c -R "rdump /home/erik/projects /mnt/nixos/rescue" /dev/nvme0n1p5 \
     2>&1 | sudo tee /mnt/nixos/debugfs-projects.log
   ```

   What is broken is the directory — the list of names. The inodes and data
   blocks behind them are a separate matter, and `e2fsck` pass 4 will find
   them unattached and drop them in `lost+found` under numeric names. For
   git repositories that is survivable even so: objects are
   content-addressed, so `git fsck` / `git unpack-objects` against
   `lost+found` can rebuild history without any directory names at all.

   `rdump` refuses to write into a directory that already exists and aborts
   on the spot (`rdump: File exists while making directory ...`) — and the
   failed rsync leaves empty `projects/`/`Documents/` behind, so dump to a
   *fresh* destination (`/mnt/nixos/rescue2`) rather than the one rsync
   touched.

   **The damage has a clean boundary, visible in `debugfs -c -R "ls -l
   /home/erik"`.** Sort the entries by inode number:

   | inode range | state |
   |---|---|
   | <= 5 796 435 | intact — uid/gid 1000, sane modes and dates |
   | ~5.1-5.8M | inode fine, directory *block* checksums bad (what the kernel trips on) |
   | >= 12 337 752 | destroyed — file content sitting in the inode table |

   which matches the fsck output exactly: `bad extra_isize` and
   `_INODE_UNINIT` were all inodes 12-16M, while the mild "No space for
   directory leaf checksum" ones sat around 5.1-5.4M. A destroyed entry is
   obvious on sight — nonsense mode, negative uid/gid, a date in 1981 or
   2084.

   Since lower inode numbers mean older files, most of the home directory
   falls in the healthy range. `~/projects` is inode 5276636 with mode
   40775, uid 1000 and a sane mtime: the directory itself is fine, only its
   data block's checksum failed, which is exactly what `-c` walks past.

   Written off (high inodes): `.claude`, `.cargo`, `.rustup`, `.gradle`,
   `.sdkman`, `.mono`, `.java`, `.android`, `Android`, `jdk21`, `.epic`,
   `UnrealEngine`, `noctalia-shell`, `ladybird`, `Games`, `.zen`,
   `.vscode-oss`, `.vscode-shared`, `.antigravity-ide`, `xz2c-firmware`,
   `.vcpkg`, `.nix-defexpr`, `nixos-config`. Nearly all of it is tool cache
   that reinstalls itself; `nixos-config` is on GitHub, and `.claude.json`
   (inode 154587) survives even though `.claude/` does not.

   The original rsync, for what it can still reach:
   `mount -o ro,noload /dev/nvme0n1p5 /mnt/p5check` (`noload` skips journal
   replay, which is what you want on a half-repaired filesystem), then rsync
   `~/projects`, `~/.ssh`, `~/.gitconfig`, `~/.claude`, documents to
   `/mnt/nixos/rescue`.
3. Finish the check: `e2fsck -fy -C 0 /dev/nvme0n1p5 | tee
   /mnt/nixos/fsck-p5-2.log`. No `-z` this time — undo could now only roll
   back to the half-repaired state, so it buys nothing.
4. If it aborts on the same inode-checksum error, take the checksums out of
   the fatal path and rerun: `tune2fs -O ^metadata_csum /dev/nvme0n1p5`.
   Re-enable later with `tune2fs -O metadata_csum` + `e2fsck -fD` if the
   filesystem survives.
5. Then judge `lost+found`. A large one with a flattened tree means p5 is
   better reformatted from the rescued subset — which makes steps 3-5 below
   *simpler*, since Ubuntu is moving off p5 anyway and p5 is meant to end up
   as a bare home partition.

### Decision: extract, then reformat p5 (2026-09-04)

`~/projects` is on GitHub, so nothing on p5 justifies fighting to save the
*filesystem* — only to get **files** out of it. That inverts one earlier
warning: once reformatting is the plan, **`e2fsck` has no downside**.
Everything it was cautioned against — clearing inodes, filling `lost+found`,
making the damage permanent — stops mattering when the filesystem is going
away regardless. It becomes just a tool for making more files readable, run
last.

It also lands better than the original plan. Step 3 below rsyncs Ubuntu's
system tree from p5 onto a new partition, but that tree lives in the same
broken filesystem and its files sit in the same high-inode range that was
destroyed. A clean Ubuntu install on the new partition is more honest than a
copy of a damaged root. And a freshly-made p5 makes step 5 free: with no
Ubuntu directory layout left on it, `hosts/hp-envy.nix` can collapse
straight to the simple `fileSystems."/home"` form, bind mount and all its
caveats gone.

Order:

1. Read-only `debugfs -c ... rdump` of everything still dumpable, into
   `/mnt/nixos/rescue2`. Costs nothing, do it first. Note `.gnupg` (inode
   131965) — GPG private keys cannot be re-cloned — and `.config` (131107),
   the real niri config that `home.nix` deliberately does not declare.
   What step 1 actually yielded: clean, no errors at all — `.gnupg` (the one
   thing that could not be re-fetched from anywhere), `.ssh`, `.steam`,
   `hotel` 565M, `Library`, `Desktop`. Partial — `Downloads` 28G, `Music`
   3.4G, `midswimmer` 554M, `blade` 6.3M, `Pictures` 38M, `musicgame`,
   `actions-runner`, `.config`, `.cursor`. Nothing at all — `projects`,
   `Documents`, `Midswimmer`, `midswimmer1`, `godot4`, `jobb`, `jdk17`,
   where the checksum error hit the *top* level so `rdump` gave up before
   entering. (`du -sh rescue2/*` does not match dotfile directories; use
   `rescue2/.[!.]*` to see the rest.)

2. `tune2fs -O ^metadata_csum /dev/nvme0n1p5`. Every remaining failure is
   `Directory block checksum does not match` — `debugfs -c` skips the
   allocation bitmaps but still verifies *directory block* checksums, and
   that is what stopped `~/projects` (0 bytes out). Clearing the feature is
   what gets past it.

   With the feature gone the **kernel** stops rejecting those directories
   too, so the rest of the extraction is a plain mount and rsync rather than
   `rdump` — and rsync, unlike `rdump`, does not abort on a destination
   directory that already exists:

   ```
   sudo mount -o ro,noload /dev/nvme0n1p5 /mnt/p5check
   sudo rsync -aHAX --numeric-ids --ignore-errors --info=progress2 \
     --exclude='.cache' --exclude='.local/share/Steam/steamapps' \
     /mnt/p5check/home/erik/ /mnt/nixos/rescue3/ 2>&1 | sudo tee /mnt/nixos/rescue3.log
   ```

   The excludes keep p3's 236GiB from filling — `Downloads` alone was 28G,
   and steamapps re-downloads.

   **`tune2fs` refuses this** — "This operation requires a freshly checked
   filesystem. Please run e2fsck -f" — which is circular, since `e2fsck`
   aborts on the very checksums the flag governs. That check is a safeguard
   for a filesystem you intend to keep using, and p5 is being reformatted,
   so go around it with `debugfs` writing the superblock feature bit
   directly (`-c` for the same unreadable-bitmap reason as before, `-w` to
   write):

   ```
   sudo debugfs -w -c -R "feature -metadata_csum" /dev/nvme0n1p5
   sudo debugfs -c    -R "features" /dev/nvme0n1p5     # verify it is gone
   ```

   **Result: 127GB and 30541 files, against 7 files on the first attempt.**
   `projects`, `Documents`, `Midswimmer`, `godot4`, `jobb` and `jdk17` all
   came across — no top-level errors left for any of them. What still fails
   is `Structure needs cleaning` on individual entries, which is the
   destroyed high-inode range; there is nothing there for `e2fsck` to
   recover either, so extraction is effectively complete at this point.

   Worth doing before moving on, now that `projects` is readable: check each
   repo for work that never reached GitHub, which is the only thing this
   whole episode could actually have cost.

   ```
   for g in /mnt/nixos/rescue3/projects/*/.git; do
     d=$(dirname "$g"); echo "=== $(basename "$d")"
     git -C "$d" log --oneline -1
     git -C "$d" status --porcelain -uno | head -5
   done
   ```
3. `e2fsck -fy -C 0 /dev/nvme0n1p5`, log on p3, run to completion.
4. Mount read-only and rsync the remainder, `lost+found` included.
5. Only then draw the final layout: create the partition in the freed space,
   mkfs both, install Ubuntu clean, restore home from the rescue. Until step
   4 is done that space still holds the old filesystem's tail, and
   `photorec` against it remains the last resort.

### The rebuild, once extraction is done

Steps 3-5 below are superseded by this — p5 is being remade rather than
kept, which makes the whole "move Ubuntu out, keep home in place" dance
unnecessary.

**Decision: no Ubuntu.** Its value was the accumulated installation — the
config, the tooling, years of state — and that is what the resize destroyed.
A freshly installed Ubuntu offers nothing NixOS does not, so the hp-envy
becomes NixOS-only and the freed space goes to `/home` instead of to a new
partition.

**Last call on p5.** After the mkfs below there is nothing left to retrieve,
and the freed tail (with its `photorec` option) goes too.

```
sudo parted /dev/nvme0n1 resizepart 5 3383547903s
sudo umount /mnt/p5check          # mkfs refuses while it is mounted
sudo partprobe /dev/nvme0n1       # or mkfs formats only the old extent
```

`/home` ends up at 1.58TiB. Final layout: p1 (Ubuntu's old ESP, removable) ·
p5 home · p2 NIXBOOT · p3 NixOS.

**And make it btrfs, not ext4.** The whole incident began with ext4 being
unable to shrink while mounted, which is what forced the live-USB GParted
run that then got interrupted. btrfs resizes online — `btrfs filesystem
resize -100G /home`, on a running system, no live media — and adds cheap
snapshots before anything risky plus checksums on file data rather than only
metadata. The cost is more machinery and less pleasant ENOSPC behaviour,
neither of which bites at 1.58TiB.

```
sudo mkfs.btrfs -L home /dev/nvme0n1p5
sudo mount /dev/nvme0n1p5 /mnt/newhome
sudo btrfs subvolume create /mnt/newhome/@home
sudo btrfs subvolume create /mnt/newhome/@snapshots
sudo umount /mnt/newhome
```

`@snapshots` is a sibling of `@home`, not a directory inside it, so
snapshots never end up inside the tree they photograph.

Restore home. A freshly-made p5 means its root **is** the home directory —
no `home/` level, so no bind mount:

```
sudo mount -o compress=zstd,subvol=@home /dev/nvme0n1p5 /mnt/newhome
sudo mkdir -p /mnt/newhome/erik
sudo rsync -aHAX --numeric-ids /mnt/nixos/rescue3/ /mnt/newhome/erik/
sudo chown -R 1000:1000 /mnt/newhome/erik
```

Optional refinement, worth doing *before* the rsync: make
`erik/.cache`, `erik/Downloads` and `erik/.local/share/Steam` subvolumes of
their own. A nested subvolume is not included in its parent's snapshot, so
snapshots of `@home` stay small and cover only what matters.

Once NixOS boots cleanly from p2, Ubuntu's leftovers can go — **after**, not
before:

```
sudo parted /dev/nvme0n1 rm 1
sudo efibootmgr            # find the "ubuntu" entry number
sudo efibootmgr -b <N> -B
```

p1 is 100MiB and sits *before* p5, so its space cannot be reclaimed by
growing home (ext4 cannot grow at the front). Not worth caring about.

`hosts/hp-envy.nix` is already updated for it: a single
`fileSystems."/home"` on `/dev/disk/by-label/home`, with `/mnt/ubuntu` and
the `/home/erik` bind mount both gone. It uses the label rather than a UUID
because mkfs issues a fresh UUID each time and this partition has now been
remade once.

### Building the new generation from the live USB

Do not boot the existing generation first: it still looks for p5 as **ext4**
at `/mnt/ubuntu`, finds nothing (p5 is btrfs now), skips it via `nofail`, and
leaves you at an empty home with the same unreadable-login problem. Build the
new one from the live session instead.

```
sudo mkdir -p /mnt/nixroot
sudo mount -o compress=zstd /dev/nvme0n1p3 /mnt/nixroot &&
sudo mount /dev/nvme0n1p2 /mnt/nixroot/boot &&
sudo mount -t btrfs -o compress=zstd,subvol=@home /dev/nvme0n1p5 /mnt/nixroot/home
```

**`mount` needs `-t btrfs` explicitly here.** Without it libblkid guesses
from a cache that still remembers the ext4 with the same label on the same
device, tries ext4, and fails with `VFS: Can't find ext4 filesystem`.

```
sudo git clone https://github.com/nergnezor/nixos-config /mnt/nixroot/home/erik/nixos-config
sudo chown -R 1000:1000 /mnt/nixroot/home/erik/nixos-config
sudo nixos-enter --root /mnt/nixroot -- nixos-rebuild dry-build --flake /home/erik/nixos-config#nixos-eval
sudo nixos-enter --root /mnt/nixroot -- nixos-rebuild boot     --flake /home/erik/nixos-config#nixos-eval
sudo nixos-enter --root /mnt/nixroot -- passwd erik
```

The flake output is `nixos-eval`, **not** `nixos-hp` — that is the hostName
inside `hosts/hp-envy.nix`, not the attribute name in `flake.nix`.

Set that password with **letters and digits only**. The live console is US
and the generation you are building sets `console.keyMap = "sv-latin1"`;
alphanumerics land on the same keys under both, nothing else does. Change it
to something real once logged in.

Afterwards, once NixOS boots and `findmnt /home` looks right, Ubuntu's
leftovers go and the first snapshot gets taken:

```
sudo btrfs subvolume snapshot -r /home /home/../@snapshots/home-$(date +%F)
sudo parted /dev/nvme0n1 rm 1
sudo efibootmgr             # find the "ubuntu" entry
sudo efibootmgr -b <N> -B
```


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
