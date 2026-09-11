# Uxstream host tools (flash/SWD + Android router build). Kept in nixos-config
# for now — not in the embedded repo. Import from home.nix:
#
#   uxstreamTools = import ./uxstream-tools.nix { inherit pkgs; homeDirectory = config.home.homeDirectory; };
#   home.packages = uxstreamTools.packages ++ ...;
#   home.sessionVariables = { ... } // uxstreamTools.sessionVariables;
#
# Host OS still needs (elsewhere in this config):
#   - ST-LINK udev rules + `plugdev`
#   - programs.nix-ld + ncurses (vcpkg arm-none-eabi-gdb)
#
# Android SDK itself stays under ~/Android/Sdk (writable; Gradle installs
# extras there). Do not put the full SDK in the nix store — it is read-only
# and assembleDebug fails when AGP wants another build-tools package.
#
# Rust/cargo now comes from nix (home.packages: rustc, cargo, rust-analyzer).
# cargo-ndk for Android builds also installs from nix when needed.
{ pkgs, homeDirectory }:
{
  packages = with pkgs; [
    openocd
    probe-rs-tools
    picocom # UART console (eyebuds USART1 @ 2 Mbaud: picocom -b 2000000 /dev/ttyACM0)
    jdk17
    android-tools # adb / fastboot without depending on ~/Android/Sdk being on PATH
    cargo-ndk # Android NDK build tool for Rust/cargo
    llvmPackages.libclang # bindgen for rust_lwip when cargo-ndk builds the JNI lib
    pkg-config
    dbus.dev # dbus-1.pc + headers for libdbus-sys (companion_router BLE deps)
    systemd.dev # libudev.pc + headers for libudev-sys (hw-benchmark USB/device detection)
    # Matches the Linux deps installed by .github/workflows/build-linux-router.yml
    # (apt: libgstreamer*-dev, gstreamer1.0-plugins-*, libgstrtspserver-1.0-dev,
    # libges-1.0-dev, libxkbcommon-dev, libinput-dev) so `cargo build`/`cargo test`
    # for router/linux works locally the same as in CI.
    gst_all_1.gstreamer
    gst_all_1.gst-plugins-base
    gst_all_1.gst-plugins-good
    gst_all_1.gst-plugins-bad
    gst_all_1.gst-plugins-ugly
    gst_all_1.gst-libav
    gst_all_1.gst-rtsp-server
    gst_all_1.gst-editing-services
    libxkbcommon
    libinput.out # libinput's default outputsToInstall is just the CLI ("bin"); need the .so
    glib.dev # glib-2.0.pc, transitively required by gstreamer-1.0.pc
  ];

  sessionVariables = {
    JAVA_HOME = pkgs.jdk17.home;
    LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
    ANDROID_HOME = "${homeDirectory}/Android/Sdk";
    ANDROID_SDK_ROOT = "${homeDirectory}/Android/Sdk";
    # Home-manager packages don't wire up pkg-config search paths the way
    # nix-shell buildInputs do -- point it at the profile explicitly.
    PKG_CONFIG_PATH = "${homeDirectory}/.nix-profile/lib/pkgconfig:${homeDirectory}/.nix-profile/share/pkgconfig";
    # Some -sys crates' .pc files (e.g. xkbcommon, libinput) list only "-llib"
    # with no "-L", relying on a standard linker search path that the profile
    # isn't on -- point the linker at it directly.
    LIBRARY_PATH = "${homeDirectory}/.nix-profile/lib";
    # bindgen invokes libclang directly, bypassing the gcc wrapper that
    # normally points NixOS builds at glibc's headers -- without this,
    # bindgen can't find things like endian.h (needed by rust_lwip).
    BINDGEN_EXTRA_CLANG_ARGS = "-isystem ${pkgs.glibc.dev}/include";
  };
}
