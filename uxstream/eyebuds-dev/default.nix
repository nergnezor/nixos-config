# EyeBuddy app: camera, serial log and ST-Link controls in one terminal (Textual) window.
# The chart is the desktop app's original Cairo drawing (splines, spring-physics labels, the
# min/now/max table strip), rendered off-screen and shown through textual-image -- Kitty's
# graphics protocol or Sixel where the far end's terminal understands it, half-cells otherwise.
{ lib, stdenv, makeWrapper, python3, ffmpeg, v4l-utils }:
let
  python = python3.withPackages (ps: [ ps.textual ps.textual-image ps.pycairo ps.pyserial ps.pillow ]);
in
stdenv.mkDerivation {
  pname = "eyebuds-dev";
  version = "0.2.0";
  src = ./.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    install -Dm755 eyebuds_dev.py $out/bin/eyebuds-dev
    substituteInPlace $out/bin/eyebuds-dev --replace-fail "#!/usr/bin/env python3" "#!${python.interpreter}"
    wrapProgram $out/bin/eyebuds-dev --prefix PATH : ${lib.makeBinPath [ ffmpeg v4l-utils ]}
    install -Dm644 eyebuds-dev.desktop $out/share/applications/eyebuds-dev.desktop
  '';
  meta.mainProgram = "eyebuds-dev";
}
