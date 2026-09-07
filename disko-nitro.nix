{
  # Whole-disk install target: the internal NVMe drive, currently all
  # Ubuntu (nvme0n1p1 EFI + nvme0n1p2 ext4 /). CONFIRM via `lsblk` before
  # running disko -- this wipes the device named here.
  #
  # btrfs with subvolumes rather than hp-envy's separate /home partition:
  # this /home has no Ubuntu history to work around (hp-envy's split came
  # from an interrupted resize disaster, see PARTITION-RUNBOOK.md), so
  # there's no reason to fix the root/home boundary at partition time
  # instead of at the subvolume level. @snapshots is a sibling of @home,
  # not a directory inside it, so snapshots never end up inside the tree
  # they photograph -- same convention as hp-envy's p5.
  disko.devices = {
    disk.main = {
      device = "/dev/nvme0n1";
      type = "disk";
      content = {
        type = "gpt";
        partitions = {
          ESP = {
            size = "512M";
            type = "EF00";
            content = {
              type = "filesystem";
              format = "vfat";
              mountpoint = "/boot";
              # nixos-generate-config's default fmask/dmask (0022) makes the
              # ESP world-readable; systemd-boot's random seed lives there.
              # Matches the mkForce override hp-envy needed after the fact --
              # set it here instead so disko gets it right from the start.
              mountOptions = [ "fmask=0077" "dmask=0077" ];
            };
          };
          root = {
            size = "100%";
            content = {
              type = "btrfs";
              extraArgs = [ "-f" ]; # confirm overwrite of the existing ext4 signature
              subvolumes = {
                "@root" = {
                  mountpoint = "/";
                  mountOptions = [ "compress=zstd" ];
                };
                "@home" = {
                  mountpoint = "/home";
                  mountOptions = [ "compress=zstd" ];
                };
                "@snapshots" = {
                  mountpoint = "/snapshots";
                  mountOptions = [ "compress=zstd" ];
                };
              };
            };
          };
        };
      };
    };
  };
}
