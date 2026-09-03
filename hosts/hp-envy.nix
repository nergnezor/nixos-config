{ config, pkgs, ... }:
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

  # Share the WHOLE Ubuntu home directory (same disk, dual-boot — never
  # mounted from both OSes at once): SSH keys, real .gitconfig, browser
  # profiles, shell history, ~/.claude, ~/projects, all of it, automatically
  # — instead of home.nix enumerating each thing worth sharing one at a
  # time. Mount the Ubuntu partition at a neutral path first, then bind
  # /home/erik onto NixOS's actual home directory. home.nix deliberately
  # does NOT declare xdg.configFile."niri" or programs.git — those paths
  # are the real, live Ubuntu files, and home-manager writing its own
  # version there would overwrite them (same filesystem, not a copy).
  fileSystems."/mnt/ubuntu" = {
    device = "/dev/disk/by-uuid/ee53b2ae-86cb-42a9-8ef6-c3e7bbd1908e"; # nvme0n1p5, confirmed 2026-09-03
    fsType = "ext4";
    options = [ "rw" "nofail" ];
  };
  fileSystems."/home/erik" = {
    device = "/mnt/ubuntu/home/erik";
    fsType = "none"; # ignored by `mount` for a bind mount, but this nixpkgs
                      # revision requires the option to have some value
    options = [ "bind" "nofail" ];
  };
}
