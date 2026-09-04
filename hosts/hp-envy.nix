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

  # /home is p5, a partition dedicated to it and shared with Ubuntu (same
  # disk, dual-boot -- never mounted from both at once): SSH keys, the real
  # .gitconfig, shell history, ~/projects, all of it. home.nix deliberately
  # does NOT declare xdg.configFile."niri" or programs.git -- those paths are
  # the real, live files, and home-manager writing its own version there
  # would overwrite them (same filesystem, not a copy).
  #
  # This used to be p5 mounted at /mnt/ubuntu with /mnt/ubuntu/home/erik
  # bind-mounted onto /home/erik, because p5 was Ubuntu's root and the home
  # directory sat at `home/erik` relative to it. p5 was remade as a bare home
  # partition after the resize destroyed its filesystem (see
  # PARTITION-RUNBOOK.md), so its root now *is* /home and the bind mount is
  # gone. Ubuntu mounts the same partition at /home and agrees with that
  # layout, so the old asymmetry is gone with it.
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

  # Keep the browser profile OUT of the shared home. It's the one piece of
  # shared state where version skew can actually destroy data rather than
  # just misbehave: Chromium-based profiles don't survive a downgrade, and
  # no available build matches Ubuntu's vivaldi 7.9 exactly (25.05 had 7.6,
  # unstable has 8.1) -- so whichever side runs "newer" migrates the 4.2GB
  # profile and the other side then chokes on it. A per-system profile
  # sidesteps that entirely; the cost is bookmarks/sessions not following
  # you between the two OSes.
  systemd.tmpfiles.rules = [ "d /var/lib/local-home/vivaldi 0700 erik erik - -" ];
  fileSystems."/home/erik/.config/vivaldi" = {
    device = "/var/lib/local-home/vivaldi";
    fsType = "none";
    options = [ "bind" "nofail" ];
  };

  # nixos-generate-config writes fmask=0022/dmask=0022 for the ESP, which
  # makes it world-readable — bootctl warns about it during install because
  # systemd-boot's random seed lives there. mkForce (not a merge) so these
  # replace the generated values instead of both sets ending up in the
  # option list with order-dependent behaviour.
  fileSystems."/boot".options = lib.mkForce [ "fmask=0077" "dmask=0077" ];
}
