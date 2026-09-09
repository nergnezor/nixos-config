# Uxstream embedded host tools (flash + SWD debug). Kept in nixos-config for
# now — not in the embedded repo. Import from home.nix:
#
#   (import ./uxstream-tools.nix { inherit pkgs; }).packages
#
# Host OS still needs (elsewhere in this config):
#   - ST-LINK udev rules + `plugdev`
#   - programs.nix-ld + ncurses (vcpkg arm-none-eabi-gdb)
{ pkgs }:
{
  packages = with pkgs; [
    openocd
    probe-rs-tools
  ];
}
