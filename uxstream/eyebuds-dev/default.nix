# EyeBuds dev app: camera, serial log and ST-Link controls in one GTK4 window.
# wrapGAppsHook4 wires up the typelibs and GStreamer plugin paths the script needs.
{ lib, stdenv, python3, wrapGAppsHook4, gobject-introspection, gtk4, libadwaita, gst_all_1 }:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ps.pyserial ]);
in
stdenv.mkDerivation {
  pname = "eyebuds-dev";
  version = "0.1.0";
  src = ./.;
  nativeBuildInputs = [ wrapGAppsHook4 gobject-introspection ];
  buildInputs = [
    gtk4
    libadwaita
    gst_all_1.gstreamer
    gst_all_1.gst-plugins-base
    gst_all_1.gst-plugins-good # v4l2src, videoflip
    gst_all_1.gst-plugins-rs # gtk4paintablesink
  ];
  dontBuild = true;
  installPhase = ''
    install -Dm755 eyebuds_dev.py $out/bin/eyebuds-dev
    substituteInPlace $out/bin/eyebuds-dev --replace-fail "#!/usr/bin/env python3" "#!${python.interpreter}"
    install -Dm644 eyebuds-dev.desktop $out/share/applications/eyebuds-dev.desktop
  '';
  meta.mainProgram = "eyebuds-dev";
}
