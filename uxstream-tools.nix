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
  ];

  sessionVariables = {
    JAVA_HOME = pkgs.jdk17.home;
    LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
    ANDROID_HOME = "${homeDirectory}/Android/Sdk";
    ANDROID_SDK_ROOT = "${homeDirectory}/Android/Sdk";
  };
}
