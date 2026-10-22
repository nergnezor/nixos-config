{ lib
, stdenv
, python3
, gtk4
, gobject-introspection
, wrapGAppsHook4
, pcsclite
, ccid
, util-linux
}:

let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
in
stdenv.mkDerivation {
  pname = "sim-sms";
  version = "0.1.0";
  src = ./.;

  nativeBuildInputs = [ wrapGAppsHook4 gobject-introspection ];
  buildInputs = [ gtk4 pcsclite ];
  dontWrapGApps = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/sim-sms $out/share/applications $out/bin
    cp simcard.py app.py $out/share/sim-sms/
    cp sim-sms.desktop $out/share/applications/
    makeWrapper ${python}/bin/python3 $out/bin/sim-sms \
      --add-flags $out/share/sim-sms/app.py \
      --prefix PATH : ${lib.makeBinPath [ pcsclite util-linux ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ pcsclite ]} \
      --set PCSCLITE_HP_DROPDIR ${ccid}/pcsc/drivers \
      --set PYTHONPATH ${placeholder "out"}/share/sim-sms
    runHook postInstall
  '';

  preFixup = ''
    gappsWrapperArgsHook
    wrapProgram $out/bin/sim-sms "''${gappsWrapperArgs[@]}"
  '';

  meta = {
    description = "Read SMS messages stored on a SIM card";
    mainProgram = "sim-sms";
    platforms = lib.platforms.linux;
  };
}
