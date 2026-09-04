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

  # p5 IS the home partition. It is still Ubuntu's root filesystem too, but
  # mounting it at /home rather than at a neutral path lines its own
  # /home/erik up with NixOS's /home/erik directly — no bind mount, and no
  # second mount point to unwind on the day Ubuntu goes away. Ubuntu's other
  # top-level directories simply sit unused beside /home until then (see
  # PARTITION-RUNBOOK.md, "Retiring Ubuntu").
  #
  # Sharing the whole home rather than enumerating pieces is deliberate: SSH
  # keys, real .gitconfig, shell history, ~/.claude, ~/projects, all of it.
  # home.nix therefore does NOT declare xdg.configFile."niri" or
  # programs.git — those paths are the real, live files, and home-manager
  # writing its own version there would overwrite them, not shadow them.
  fileSystems."/home" = {
    device = "/dev/disk/by-uuid/ee53b2ae-86cb-42a9-8ef6-c3e7bbd1908e"; # nvme0n1p5, confirmed 2026-09-03
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
