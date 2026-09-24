# EyeBuddy app: camera, serial log and ST-Link controls in one terminal (Textual) window.
# The chart is the desktop app's original Cairo drawing (splines, spring-physics labels, the
# min/now/max table strip), rendered off-screen and shown through textual-image -- Kitty's
# graphics protocol or Sixel where the far end's terminal understands it, half-cells otherwise.
# The ST-Link is driven directly (openocd for halt/resume/reset, build-flash.sh -- shared with
# the noctalia bar plugin -- for building and flashing), so no desktop session is needed.
{ lib, stdenv, makeWrapper, python3, ffmpeg, v4l-utils, android-tools, openocd, probe-rs-tools
, jq, util-linux, procps, less }:
let
  python = python3.withPackages (ps: [ ps.textual ps.textual-image ps.pycairo ps.pyserial ps.pillow ]);
in
stdenv.mkDerivation {
  pname = "eyebuds-dev";
  version = "0.3.0";
  src = ./.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    install -Dm755 eyebuds_dev.py $out/bin/eyebuds-dev
    install -Dm755 ${../noctalia-plugins/stlink/build-flash.sh} $out/share/eyebuds-dev/build-flash.sh
    substituteInPlace $out/bin/eyebuds-dev --replace-fail "#!/usr/bin/env python3" "#!${python.interpreter}"
    # Suffixed, so a toolchain already on the user's PATH (make, cmake, the ARM gcc) stays first.
    wrapProgram $out/bin/eyebuds-dev \
      --prefix PATH : ${lib.makeBinPath [ ffmpeg v4l-utils android-tools ]} \
      --suffix PATH : ${lib.makeBinPath [ openocd probe-rs-tools jq util-linux procps less ]} \
      --set-default EYEBUDDY_BUILD_FLASH $out/share/eyebuds-dev/build-flash.sh
    install -Dm644 eyebuds-dev.desktop $out/share/applications/eyebuds-dev.desktop
  '';
  meta.mainProgram = "eyebuds-dev";
}
