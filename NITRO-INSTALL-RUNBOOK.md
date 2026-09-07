# Nitro install — whole-disk, replacing Ubuntu

Target: `/dev/nvme0n1` on the Acer Nitro N50-640 (Intel Arc A750). Unlike
the HP, this disk is Ubuntu **end to end** right now (`nvme0n1p1` ESP +
`nvme0n1p2` ext4 `/`, no free space) — there is no separate untouched
partition to install onto the way `install-to-nixos-partition.nix` did on
the HP. `disko-nitro.nix` wipes and repartitions the whole thing, so this
cannot run from the live Ubuntu session that's mounted on top of it.

**Before any of this:** the SSH/GPG backup (encrypted, pushed to the
private `nergnezor/machine-backup` repo) must already be done — this disk
is about to lose everything on it, Ubuntu included.

## 1. Get into a live NixOS environment — kexec, no USB needed

`kexec-tools` is already installed on this Ubuntu, and Secure Boot is off,
so there's no need to burn a USB stick: kexec straight from the running
Ubuntu into the official netboot installer, itself built for exactly this
(`nixos-kexec` in its own store path).

**Save/close everything first** — this terminal and every other session on
the machine dies the instant `kexec -e` runs, no graceful shutdown.

```
cd /tmp
sudo curl -L -o bzImage https://github.com/nix-community/nixos-images/releases/download/nixos-unstable/bzImage-x86_64-linux
sudo curl -L -o initrd https://github.com/nix-community/nixos-images/releases/download/nixos-unstable/initrd-x86_64-linux

sudo kexec -l bzImage --initrd=initrd --command-line="init=/nix/store/c70z7ifz84nkzz26py3xgy7yqp6y669y-nixos-system-nixos-kexec-26.11pre708350.gfedcba/init nohibernate root=fstab loglevel=4 lsm=landlock,yama,bpf"
sudo kexec -e
```

You land in a live NixOS shell as `root`, with `nix` already on `PATH` —
no Ubuntu-hosted Nix-install step needed here, unlike `USB-TEST-RUNBOOK.md`.

## 2. Get online and pull the repo

The netboot image brings up networking (DHCP) itself; confirm with
`ip a` / `ping -c1 nixos.org`. If the wired NIC didn't come up on its own,
`systemctl start NetworkManager` then `nmtui`.

```
git clone https://github.com/nergnezor/nixos-config /root/nixos-config
cd /root/nixos-config
```

## 3. Partition + format + mount via disko

```
lsblk -o NAME,SIZE,TYPE,TRAN,MODEL   # reconfirm nvme0n1 is the 1.8TB internal drive
nix --extra-experimental-features "nix-command flakes" \
  run github:nix-community/disko -- --mode disko /root/nixos-config/disko-nitro.nix
```

Formats and mounts the whole disk at `/mnt` per `disko-nitro.nix`: ESP at
`/mnt/boot`, btrfs `@root` at `/mnt`, `@home` at `/mnt/home`, `@snapshots`
at `/mnt/snapshots`.

## 4. Generate this host's own hardware config

**Its own filename, not `hardware-configuration.nix`** — that name is
HP's now (`hardware-configuration-hp-envy.nix`), and `hosts/nitro.nix`
imports `../hardware-configuration-nitro.nix` specifically so nitro's
`nixos-generate-config` can never overwrite HP's UUIDs.

```
nixos-generate-config --no-filesystems --root /mnt
cp /mnt/etc/nixos/hardware-configuration.nix /root/nixos-config/hardware-configuration-nitro.nix
```

## 5. Build, copy, install

Same order as `install-to-nixos-partition.sh` on the HP — build locally
first rather than letting `nixos-install` build directly against
`--store /mnt`, and `--no-check-sigs` on the copy since this is an
unsigned local build:

```
cd /root/nixos-config
nix --extra-experimental-features "nix-command flakes" \
  build .#nixosConfigurations.nixos-nitro.config.system.build.toplevel \
  --out-link /root/nixos-nitro-system

nix --extra-experimental-features "nix-command flakes" \
  copy --to /mnt /root/nixos-nitro-system --no-check-sigs

nixos-install --root /mnt --system /root/nixos-nitro-system
```

Set the `erik`/`root` passwords when prompted — **letters and digits
only**, same caveat as the HP had: the live console is US-layout, the
generation you just built sets `console.keyMap = "sv-latin1"`, and
anything else lands on a different key at the greetd/tuigreet prompt with
no hint why.

## 6. Reboot

```
reboot
```

No F9 boot-picker needed — disko wiped the GPT table, so there is no
Ubuntu entry left to choose around; systemd-boot is the only thing on the
disk now.

## 7. After first boot — restore what isn't declared

Log in as `erik`, then:

```
gh repo clone nergnezor/machine-backup ~/machine-backup
cd ~/machine-backup
gpg -d secrets.tar.gz.gpg > secrets.tar.gz
tar -xzf secrets.tar.gz -C ~
chmod 700 ~/.ssh ~/.gnupg
chmod 600 ~/.ssh/id_ed25519_uxstream ~/.ssh/authorized_keys

git clone https://github.com/nergnezor/astronvim ~/astronvim
git clone https://github.com/nergnezor/nixos-config ~/nixos-config   # if /root's clone isn't reused

./noctalia/sync.sh push
./home/sync.sh push       # picks gitconfig.nixos-nitro automatically, by hostname

flatpak remote-add --if-not-exists sonuscape https://dl.sonuscape.net/flatpak/repo
flatpak install sonuscape net.sonuscape.mouseless

sudo tailscale up
gh auth login
```

Then, from `~/nixos-config`: `git add hardware-configuration-nitro.nix`
and commit/push it (`./rebuild.sh --push` does this after a successful
rebuild) — it doesn't exist in the repo yet at the point this runbook was
written, only generated locally in step 4 above.

Once confirmed everything survived (network, niri/noctalia, kitty,
AstroNvim, tmux resurrect), delete `nergnezor/machine-backup` — its job is
done.
