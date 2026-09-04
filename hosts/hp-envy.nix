{ config, lib, pkgs, ... }:
{
  networking.hostName = "nixos-hp";

  # RTX 3060 Ti (GA104) — proprietary driver, needed for a usable niri
  # session on this machine.
  services.xserver.videoDrivers = [ "nvidia" ];
  hardware.nvidia = {
    modesetting.enable = true;
    open = false; # Ampere also supports the open kernel module (R515+); "stable" is the safer default
    package = config.boot.kernelPackages.nvidiaPackages.stable;
    nvidiaSettings = true;
  };

  # /home is p5, a partition dedicated to it. NixOS is now the only OS on
  # this machine — the Ubuntu install that used to share the disk is gone,
  # along with everything that arrangement required here: p5 mounted at
  # /mnt/ubuntu, and /mnt/ubuntu/home/erik bind-mounted onto /home/erik to
  # strip the leading `home/` that Ubuntu's root layout imposed. p5 was
  # remade as a bare home partition after the interrupted resize destroyed
  # its filesystem (see PARTITION-RUNBOOK.md), so its root simply *is*
  # /home now.
  #
  # home.nix still does NOT declare xdg.configFile."niri" or programs.git.
  # The original reason (those files being Ubuntu's live copies) is gone,
  # but the files themselves were restored from the rescue and remain the
  # working versions — home-manager declaring them would overwrite the
  # restored config with the point-in-time snapshot in this repo's niri/
  # directory. Adopt them deliberately if you want, by copying the live
  # files into the repo first.
  #
  # by-label, not by-uuid: mkfs assigns a fresh UUID every time, and this
  # partition has now been remade once. The label is set deliberately
  # (`mkfs.ext4 -L home`) and survives in the config across a future rebuild
  # of it. `nofail` stays -- a config accidentally built for the wrong host,
  # or booted before the partition exists, degrades to an empty local home
  # rather than a broken boot.
  fileSystems."/home" = {
    device = "/dev/disk/by-label/home";
    fsType = "ext4";
    options = [ "rw" "nofail" ];
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
}
