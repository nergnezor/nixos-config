{
  # Test-install target: the 14.6GB "USB DISK" stick (formerly running
  # Ventoy) — device path CONFIRM via `lsblk` inside the live session before
  # running disko. NOT the 8GB SD card (that's the boot medium) and NOT
  # nvme0n1 (that's the internal disk, untouched by this config).
  disko.devices = {
    disk.main = {
      device = "/dev/sda"; # confirm via `lsblk` before running disko -- this stick's letter has flipped sda<->sdb multiple times this session on unplug/replug
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
