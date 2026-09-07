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

  # Whole-disk install (2026-09-07) — Ubuntu is gone, disko-nitro.nix
  # partitions the entire nvme0n1 for NixOS alone. No /mnt/ubuntu, no bind
  # mount: unlike hp-envy's history, this /home was never shared with
  # another OS, so there is no inherited layout to work around.
}
