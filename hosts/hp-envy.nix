{ config, lib, pkgs, ... }:
{
  # Split out of the shared configuration.nix (2026-09-07) now that nitro is
  # a permanent second host, not a throwaway USB comparison: a single
  # shared hardware-configuration.nix meant nitro's own
  # `nixos-generate-config` would silently overwrite the HP's UUIDs the
  # next time either machine ran `rebuild.sh --push`.
  imports = [ ../hardware-configuration-hp-envy.nix ];

  networking.hostName = "nixos-hp";

  # RTX 3060 Ti (GA104) — proprietary driver, needed for a usable niri
  # session on this machine.
  services.xserver.videoDrivers = [ "nvidia" ];
  hardware.nvidia = {
    modesetting.enable = true;
    # Without this, waking from suspend gives you a live machine with a dead
    # display: audio plays, the compositor is running and answering, both
    # monitors are powered and report their modes — and nothing is drawn.
    # Observed 2026-09-05 on the first suspend/resume of this install.
    #
    # It is what installs nvidia-{suspend,resume,hibernate}.service and sets
    # NVreg_PreserveVideoMemoryAllocations=1, i.e. what saves the driver's
    # video memory to disk on the way down and restores it on the way up.
    # Without those services the driver comes back without its display
    # allocations and there is nothing left to scan out. The tell is that
    # `systemctl is-enabled nvidia-resume.service` says not-found rather than
    # disabled: the unit is not shipped at all until this option is on.
    powerManagement.enable = true;
    open = false; # Ampere also supports the open kernel module (R515+); "stable" is the safer default
    package = config.boot.kernelPackages.nvidiaPackages.stable;
    nvidiaSettings = true;
  };
  # Without this the console is unreadable once nvidia_drm takes the display
  # over from simpledrm: the handover leaves fbcon on a framebuffer nothing
  # has set up, and the greetd/tuigreet prompt on tty1 is the first thing
  # you see it on. Enabling nvidia-drm's own fbdev gives fbcon a real target.
  boot.kernelParams = [ "nvidia_drm.fbdev=1" ];

  # /home is p5, a partition dedicated to it. NixOS is now the only OS on
  # this machine — the Ubuntu install that used to share the disk is gone,
  # along with everything that arrangement required here: p5 mounted at
  # /mnt/ubuntu, and /mnt/ubuntu/home/erik bind-mounted onto /home/erik to
  # strip the leading `home/` that Ubuntu's root layout imposed. p5 was
  # remade as a bare home partition after the interrupted resize destroyed
  # its filesystem (see PARTITION-RUNBOOK.md), so its root simply *is*
  # /home now.
  #
  # home.nix now points ~/.config/niri at this repo's niri/ directory with
  # mkOutOfStoreSymlink, after the rescue turned config.kdl into 10240 bytes
  # of unrelated data and left niri unable to start. ~/.gitconfig is still
  # undeclared and still the restored working copy.
  #
  # btrfs, not ext4, and the reason is the incident that made this partition
  # necessary: ext4 cannot shrink while mounted, which is what forced the
  # whole GParted-from-a-live-USB exercise that then got interrupted. btrfs
  # resizes online (`btrfs filesystem resize -100G /home`), takes snapshots
  # for free before anything risky, and checksums file *data* rather than
  # only metadata, so `scrub` finds silent corruption before an open() does.
  # It also matches p3.
  #
  # subvol=@home keeps snapshots out of the tree they photograph: @snapshots
  # is a sibling subvolume at the filesystem root, not a directory inside
  # @home.
  #
  # by-label, not by-uuid: mkfs assigns a fresh UUID every time, and this
  # partition has now been remade once. The label is set deliberately
  # (`mkfs.btrfs -L home`) and survives in the config across a future rebuild
  # of it. `nofail` stays -- a config accidentally built for the wrong host,
  # or booted before the partition exists, degrades to an empty local home
  # rather than a broken boot.
  fileSystems."/home" = {
    device = "/dev/disk/by-label/home";
    fsType = "btrfs";
    options = [ "rw" "nofail" "compress=zstd" "subvol=@home" ];
  };

  # nvme0n1p3 is btrfs (switched from ext4 for this) — turn on transparent
  # zstd compression, which is the whole reason for the switch: /nix/store
  # is mostly text and ELF, and compresses well. `options` is a list type in
  # the fileSystems module, so this merges with the device/fsType that
  # hardware-configuration.nix generates for "/" rather than conflicting.
  fileSystems."/".options = [ "compress=zstd" ];

  # The vivaldi profile used to be bind-mounted out of the home directory to
  # a per-system path, because Ubuntu and NixOS shared one home and their
  # vivaldi versions differed — Chromium-based profiles do not survive a
  # downgrade, so whichever side ran newer migrated the 4.2GB profile and
  # the other then choked on it. With Ubuntu gone there is no second version
  # to skew against, so the bind mount and its tmpfiles rule are removed and
  # ~/.config/vivaldi is simply the real profile again.

  # nixos-generate-config writes fmask=0022/dmask=0022 for the ESP, which
  # makes it world-readable — bootctl warns about it during install because
  # systemd-boot's random seed lives there. mkForce (not a merge) so these
  # replace the generated values instead of both sets ending up in the
  # option list with order-dependent behaviour.
  fileSystems."/boot".options = lib.mkForce [ "fmask=0077" "dmask=0077" ];

  # AngelBeach's self-hosted CI runner. It used to run as the Ubuntu install's
  # own `actions.runner.*.service` (installed by the upstream `svc.sh`,
  # outside this repo entirely) against `~/actions-runner`, registered by
  # `scripts/setup-runner.sh`. That Ubuntu install is gone (see the /home
  # comment above), and the same disk corruption that wiped niri's config.kdl
  # also hit `~/actions-runner/.runner` and `.credentials` -- both are now
  # garbage bytes, not JSON, with no working backup (`.runner_migrated`
  # survived for `.runner` but there is no equivalent for `.credentials`).
  # So there is neither a service to start it nor a valid registration for it
  # to use even if there were. This declares the service NixOS never had.
  #
  # tokenFile is a classic PAT (repo scope) dropped outside git, e.g.:
  #   echo -n 'ghp_...' | sudo tee /etc/github-runner-token >/dev/null
  #   sudo chmod 600 /etc/github-runner-token
  # A PAT (not a 1-hour registration token) is what lets the service
  # re-register itself on every restart without manual intervention.
  #
  # `name` matches the existing (now offline) GitHub Actions runner entry so
  # `replace` reclaims it instead of leaving a dead duplicate; `extraLabels`
  # supplies the `ue5` label every workflow's `runs-on:` requires alongside
  # the auto-added `self-hosted`/`Linux`/`X64`. `workDir` reuses the old
  # runner's checkout path so incremental builds keep their cache across job
  # runs (it is only wiped on a service restart, not between jobs).
  services.github-runners.angelbeach-ue5 = {
    enable = true;
    url = "https://github.com/nergnezor/AngelBeach";
    name = "erik-HP-ENVY-TE01-1xxx-ue5";
    tokenFile = "/etc/github-runner-token";
    replace = true;
    extraLabels = [ "ue5" ];
    user = "erik";
    workDir = "/home/erik/actions-runner/_work";
    serviceOverrides.ProtectHome = false;
    # actions/checkout runs with lfs:true; the service's PATH (built from
    # this list, not the interactive shell's) otherwise has no git-lfs.
    extraPackages = [ pkgs.git-lfs ];
  };
}
