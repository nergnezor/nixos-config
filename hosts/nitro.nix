{ pkgs, ... }:
{
  networking.hostName = "nixos-nitro";

  # Intel Arc A750 (DG2, discrete) — confirmed via lspci over SSH, 2026-09-03.
  # No nvidia.* needed; Mesa's iris/anv drivers handle it through the
  # `hardware.graphics.enable` already set in configuration.nix. Adding the
  # media driver for hardware video decode/encode, standard recommendation
  # for modern Intel GPUs (both iHD and legacy i965 variants, harmless to
  # include both).
  hardware.graphics.extraPackages = with pkgs; [ intel-media-driver vaapiIntel ];

  # Same idea as hosts/hp-envy.nix, this machine's own Ubuntu partition —
  # confirmed via `lsblk`/`id` over SSH (erik-Nitro-N50-640), 2026-09-03:
  # Ubuntu 25.10, single ext4 partition (no separate /home), uid 1000
  # (already the default in configuration.nix, matches without an override).
  fileSystems."/mnt/ubuntu" = {
    device = "/dev/disk/by-uuid/c37c0388-5e1b-4064-aa07-dc723e9271e4"; # nvme0n1p2
    fsType = "ext4";
    options = [ "rw" "nofail" ];
  };
  fileSystems."/home/erik" = {
    device = "/mnt/ubuntu/home/erik";
    options = [ "bind" "nofail" ];
  };
}
