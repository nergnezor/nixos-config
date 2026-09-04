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
  # Without this the console is unreadable once nvidia_drm takes the display
  # over from simpledrm: the handover leaves fbcon on a framebuffer nothing
  # has set up, and the greetd/tuigreet prompt on tty1 is the first thing
  # you see it on. Enabling nvidia-drm's own fbdev gives fbcon a real target.
  boot.kernelParams = [ "nvidia_drm.fbdev=1" ];
  # fbdev=1 is not enough by itself. Generation 10's journal: simpledrm owns
  # fb0 at 10:08:06, greetd starts on that console, then nvidia-drm loads at
  # 10:08:13, deactivates the VGA console, and fbcon migrates onto
  # nvidia-drmdrmfb — which is exactly when every glyph turns into garbage.
  # Putting the nvidia modules in the initrd means fbcon's first (and only)
  # framebuffer is nvidia's, so the handover never happens.
  boot.initrd.kernelModules = [ "nvidia" "nvidia_modeset" "nvidia_drm" ];

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
}
