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
# Rust/cargo-ndk stay on rustup (~/.cargo), not nix — avoids fighting the
# toolchain used for aarch64-linux-android.
{ pkgs, homeDirectory }:
{
  packages = with pkgs; [
    openocd
    probe-rs-tools
    jdk17
    android-tools # adb / fastboot without depending on ~/Android/Sdk being on PATH
    llvmPackages.libclang # bindgen for rust_lwip when cargo-ndk builds the JNI lib
  ];

  sessionVariables = {
    JAVA_HOME = pkgs.jdk17.home;
    LIBCLANG_PATH = "${pkgs.llvmPackages.libclang.lib}/lib";
    ANDROID_HOME = "${homeDirectory}/Android/Sdk";
    ANDROID_SDK_ROOT = "${homeDirectory}/Android/Sdk";
  };
}
