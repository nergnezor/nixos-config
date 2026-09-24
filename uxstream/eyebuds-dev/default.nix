# EyeBuddy app: camera, serial log and ST-Link controls in one terminal (Textual) window.
# plotext is pinned to 5.3.2 in nixpkgs already, which matters here: 6.0+ ships a compiled
# C++ kernel that needs libstdc++ off the FHS, and this pure-Python build is the one that loads.
{ lib, stdenv, makeWrapper, python3, ffmpeg, v4l-utils }:
let
  python = python3.withPackages (ps: [ ps.textual ps.textual-image ps.plotext ps.pyserial ps.pillow ]);
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
