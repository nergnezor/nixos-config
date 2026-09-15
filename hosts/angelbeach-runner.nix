{ pkgs, lib, ... }:
{
  # AngelBeach's self-hosted CI runner (GitHub Actions, UE5 build/package/
  # publish workflows) — shared between hosts/nitro.nix and hosts/hp-envy.nix
  # since both machines run the exact same workflows against the `ue5`
  # label and need identical dependencies to do it. Split out 2026-09-12
  # after an initial straight copy-paste from nitro.nix into hp-envy.nix
  # dropped python3Packages.pip along the way — a shared file makes that
  # class of drift impossible instead of relying on remembering to keep two
  # copies in sync. Each host's own .nix file imports this and sets only
  # `name` (must match that machine's existing GitHub Actions runner entry
  # so `replace` reclaims it instead of leaving a dead duplicate), and
  # optionally overrides `enable` (see hosts/hp-envy.nix, 2026-09-12: GitHub
  # Actions has no priority between two self-hosted runners sharing one
  # label, so nitro-as-primary is enforced by only running one of them at a
  # time rather than by label tricks).
  services.github-runners.angelbeach-ue5 = {
    # mkDefault, not a plain `true`: hosts/hp-envy.nix overrides this to
    # false without needing lib.mkForce.
    enable = lib.mkDefault true;
    url = "https://github.com/nergnezor/AngelBeach";
    tokenFile = "/etc/github-runner-token";
    replace = true;
    extraLabels = [ "ue5" ];
    user = "erik";
    # Default workDir falls back to the systemd RuntimeDirectory, which
    # lives on /run -- a tmpfs capped at boot.runSize (25% of RAM) regardless
    # of how much real disk is free. Confirmed by hand on nitro: a full
    # Android package run got all the way to the final
    # ueBuildUniversalAPKSRelease step before Gradle's bundletool died with
    # "No space left on device" against a `df` showing 1.7T free on /home --
    # /run itself was the thing that had filled up.
    workDir = "/home/erik/actions-runner/_work";
    # The module's default hardening hides all of /home, which breaks
    # build-linux.yml's lookup of `$HOME/UnrealEngine-Angelscript` and the
    # CHDIR into workDir above. Confirmed by hand on nitro, 2026-09-07.
    serviceOverrides.ProtectHome = false;

    # Every one of these was hit for real on nitro getting AngelBeach's
    # Android build working (2026-09-12), each only discovered by getting
    # past the previous one:
    #   - git-lfs: actions/checkout runs with lfs:true; the service's PATH
    #     (built from this list, not the interactive shell's) otherwise has
    #     none.
    #   - util-linux (setsid): build-android.yml's "Ensure Zen storage
    #     server is running" step backgrounds zenserver with setsid so it
    #     survives past the step that launches it.
    #   - curl: that same step polls zenserver with curl, redirected to
    #     /dev/null so "connection refused" retries stay quiet -- which
    #     also silently swallows "curl: command not found" when curl itself
    #     isn't on PATH. Confirmed by hand: zenserver came up and answered
    #     instantly on localhost:8558 from outside the service's sandbox
    #     the whole time the step spent failing, so this (not a slow/stuck
    #     server) was the actual cause of every "ERROR: Zen server failed
    #     to start" above.
    #   - python3: "Disable the editor-only UnrealMCP plugin for packaging"
    #     edits BeachVolleyball.uproject with a small python3 script.
    #   - python3Packages.pip: publish-play-internal's "Install uploader
    #     dependencies" step runs `pip install --user --break-system-packages
    #     ...` to get the Play upload script's google-api dependencies --
    #     nix's python3 (unlike Debian/Ubuntu's) doesn't bundle a pip binary
    #     at all.
    extraPackages = [ pkgs.git-lfs pkgs.util-linux pkgs.curl pkgs.python3 pkgs.python3Packages.pip ];

    # RunUAT/UnrealBuildTool are .NET, and the engine's bundled
    # self-contained runtime aborts with "Couldn't find a valid ICU
    # package" on NixOS (no libicu at the path .NET's globalization code
    # expects) -- hit for real doing the initial engine build by hand on
    # nitro (Setup.sh's GitDependencies), and needed again for RunUAT/Cook
    # at CI time.
    #
    # UnrealEditor-Cmd (the engine's own compiled ELF binary, used by the
    # Cook step) dynamically links against a normal desktop-Linux library
    # set NixOS doesn't provide at the FHS paths it expects -- glib first
    # (libglib-2.0.so.0: "Package for Android" got all the way through a
    # from-scratch engine+game compile, 1h33m, before Cook failed instantly
    # on this), then libnss3.so once glib was fixed (CEF/Chromium, which
    # UnrealEditor bundles for its web-browser widget and pulls in even for
    # a headless Cook), then libgbm.so.1 (a separate package from mesa's
    # default output) once CEF's own deps were satisfied. This adds CEF's
    # whole usual Linux runtime dependency set up front instead of one
    # library at a time -- the standard list Playwright/Puppeteer/Electron
    # need on NixOS for the same reason (bundled Chromium expects an FHS
    # system). Unused entries cost nothing; another 15-90min rebuild for
    # each one individually (nitro's actual experience -- see each host's
    # RuntimeDirectory-wipe warning) is the real expense.
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
        libgbm # mesa's "out" output doesn't carry libgbm.so.1, a separate
               # package does -- confirmed as the very next missing lib
               # after nss3, once CEF's own deps above were satisfied.
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
  };
}
