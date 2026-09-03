{
  # Test-install target: the 14.6GB "USB DISK" stick (formerly running
  # Ventoy) — device path CONFIRM via `lsblk` inside the live session before
  # running disko. NOT the 8GB SD card (that's the boot medium) and NOT
  # nvme0n1 (that's the internal disk, untouched by this config).
  disko.devices = {
    disk.main = {
      device = "/dev/sdb"; # was /dev/sda -- re-enumerated after an unplug/replug earlier in the session, confirm via `lsblk` if this ever drifts again
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
            };
          };
          root = {
            size = "100%";
            content = {
              type = "filesystem";
              format = "ext4";
              mountpoint = "/";
            };
          };
        };
      };
    };
  };
}
