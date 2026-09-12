{ config, lib, pkgs, ... }:
{
  # Split out of the shared configuration.nix (2026-09-07) now that nitro is
  # a permanent second host, not a throwaway USB comparison: a single
  # shared hardware-configuration.nix meant nitro's own
  # `nixos-generate-config` would silently overwrite the HP's UUIDs the
  # next time either machine ran `rebuild.sh --push`.
  imports = [ ../hardware-configuration-hp-envy.nix ];

  networking.hostName = "nixos-hp";

  # RTX 3060 Ti (GA104) — proprietary driver, needed for a usable niri
  # session on this machine.
  services.xserver.videoDrivers = [ "nvidia" ];
  hardware.nvidia = {
    modesetting.enable = true;
    # Without this, waking from suspend gives you a live machine with a dead
    # display: audio plays, the compositor is running and answering, both
    # monitors are powered and report their modes — and nothing is drawn.
    # Observed 2026-09-05 on the first suspend/resume of this install.
    #
    # It is what installs nvidia-{suspend,resume,hibernate}.service and sets
    # NVreg_PreserveVideoMemoryAllocations=1, i.e. what saves the driver's
    # video memory to disk on the way down and restores it on the way up.
    # Without those services the driver comes back without its display
    # allocations and there is nothing left to scan out. The tell is that
    # `systemctl is-enabled nvidia-resume.service` says not-found rather than
    # disabled: the unit is not shipped at all until this option is on.
    powerManagement.enable = true;
    open = false; # Ampere also supports the open kernel module (R515+); "stable" is the safer default
    package = config.boot.kernelPackages.nvidiaPackages.stable;
    nvidiaSettings = true;
  };
  # Without this the console is unreadable once nvidia_drm takes the display
  # over from simpledrm: the handover leaves fbcon on a framebuffer nothing
  # has set up, and the greetd/tuigreet prompt on tty1 is the first thing
  # you see it on. Enabling nvidia-drm's own fbdev gives fbcon a real target.
  boot.kernelParams = [ "nvidia_drm.fbdev=1" ];

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

  # AngelBeach's self-hosted CI runner. It used to run as the Ubuntu install's
  # own `actions.runner.*.service` (installed by the upstream `svc.sh`,
  # outside this repo entirely) against `~/actions-runner`, registered by
  # `scripts/setup-runner.sh`. That Ubuntu install is gone (see the /home
  # comment above), and the same disk corruption that wiped niri's config.kdl
  # also hit `~/actions-runner/.runner` and `.credentials` -- both are now
  # garbage bytes, not JSON, with no working backup (`.runner_migrated`
  # survived for `.runner` but there is no equivalent for `.credentials`).
  # So there is neither a service to start it nor a valid registration for it
  # to use even if there were. This declares the service NixOS never had.
  #
  # tokenFile is a classic PAT (repo scope) dropped outside git, e.g.:
  #   echo -n 'ghp_...' | sudo tee /etc/github-runner-token >/dev/null
  #   sudo chmod 600 /etc/github-runner-token
  # A PAT (not a 1-hour registration token) is what lets the service
  # re-register itself on every restart without manual intervention.
  #
  # `name` matches the existing (now offline) GitHub Actions runner entry so
  # `replace` reclaims it instead of leaving a dead duplicate; `extraLabels`
  # supplies the `ue5` label every workflow's `runs-on:` requires alongside
  # the auto-added `self-hosted`/`Linux`/`X64`. `workDir` reuses the old
  # runner's checkout path so incremental builds keep their cache across job
  # runs (it is only wiped on a service restart, not between jobs).
  services.github-runners.angelbeach-ue5 = {
    enable = true;
    url = "https://github.com/nergnezor/AngelBeach";
    name = "erik-HP-ENVY-TE01-1xxx-ue5";
    tokenFile = "/etc/github-runner-token";
    replace = true;
    extraLabels = [ "ue5" ];
    user = "erik";
    workDir = "/home/erik/actions-runner/_work";
    serviceOverrides.ProtectHome = false;
    # actions/checkout runs with lfs:true; the service's PATH (built from
    # this list, not the interactive shell's) otherwise has no git-lfs.
    # Rest of this list and extraEnvironment ported from nitro.nix
    # (2026-09-12) after a long live-debugging session got AngelBeach's
    # Android build working there -- every one of these was hit for real on
    # that machine, in this order, each only discovered by getting past the
    # previous one:
    #
    #   - util-linux (setsid): "Ensure Zen storage server is running"
    #     backgrounds zenserver with setsid so it survives past the step
    #     that launches it.
    #   - curl: that same step polls zenserver's health endpoint with curl,
    #     redirected to /dev/null so "connection refused" retries stay
    #     quiet -- which also silently swallows "curl: command not found"
    #     when curl itself isn't on PATH. Confirmed by hand on nitro: the
    #     server was actually up and answering the whole time the step
    #     spent failing.
    #   - python3: "Disable the editor-only UnrealMCP plugin for packaging"
    #     edits BeachVolleyball.uproject with a small python3 script.
    extraPackages = [ pkgs.git-lfs pkgs.util-linux pkgs.curl pkgs.python3 ];
    # RunUAT/UnrealBuildTool are .NET, and the engine's bundled
    # self-contained runtime aborts with "Couldn't find a valid ICU
    # package" on NixOS (no libicu at the path .NET's globalization code
    # expects) -- hit on nitro building the engine by hand (Setup.sh's
    # GitDependencies), and needed again here for RunUAT/Cook at CI time.
    #
    # UnrealEditor-Cmd (the engine's own compiled ELF binary, used by the
    # Cook step) dynamically links against a normal desktop-Linux library
    # set NixOS doesn't provide at the FHS paths it expects -- glib first
    # (libglib-2.0.so.0), then libnss3.so once glib was fixed (CEF/Chromium,
    # which UnrealEditor bundles for its web-browser widget and pulls in
    # even for a headless Cook), then libgbm.so.1 (a separate package from
    # mesa's default output) once CEF's own deps were satisfied. This adds
    # CEF's whole usual Linux runtime dependency set up front instead of
    # one library at a time -- the standard list Playwright/Puppeteer/
    # Electron need on NixOS for the same reason (bundled Chromium expects
    # an FHS system). Unused entries cost nothing; another 15-90min rebuild
    # for each one individually (nitro's actual experience) is the real
    # expense.
    extraEnvironment = {
      DOTNET_SYSTEM_GLOBALIZATION_INVARIANT = "1";
      LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath (with pkgs; [
        glib
        nss
        nspr
        atk
        at-spi2-atk
        at-spi2-core
        cups
        dbus
        libdrm
        gtk3
        pango
        cairo
        gdk-pixbuf
        alsa-lib
        expat
        mesa
        libgbm
        systemd # libudev.so.1
        libxkbcommon
        libx11
        libxcomposite
        libxdamage
        libxext
        libxfixes
        libxrandr
        libxcb
        libxtst
        libxi
        libxscrnsaver
        libxshmfence
        libGL # libglvnd -- also carries libEGL.so/libGLX.so/libOpenGL.so
        vulkan-loader
        wayland
      ]);
    };
    # Nix-config parity with nitro.nix stops here: the engine itself
    # (~/UnrealEngine-Angelscript, ~160G, cloned + built by hand) is not
    # tracked by either host's config, and hp-envy's copy was lost in the
    # disk corruption described above -- it needs cloning and building from
    # scratch again (`git clone --branch angelscript-master
    # git@github.com:Hazelight/UnrealEngine-Angelscript.git`, then
    # Setup.sh, GenerateProjectFiles.sh, `make UnrealEditor` with
    # DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 set) before this runner can
    # actually build anything. Likewise the Android SDK under ~/Android/Sdk
    # needs the matching NDK (27.2.12479018), platforms;android-36 and
    # build-tools;36.0.0 installed via sdkmanager, and a real (non-nix-
    # wrapped) cmdline-tools/latest -- nitro's had a broken nix-wrapped
    # sdkmanager whose launcher script hardcoded ANDROID_HOME to a
    # read-only /nix/store path, so it may be worth checking hp-envy's for
    # the same fault before assuming its sdkmanager works.
  };
}
