# niri on NixOS — parallel eval install

Dual-boot NixOS on `erik-HP-ENVY-TE01-1xxx`, own partitions, alongside the
working Ubuntu install. Goal: evaluate niri/NixOS with the real config and
the real app set, not a toy setup. Same config also targets the Nitro
runner (`hosts/nitro.nix`) for a hardware comparison (Intel Arc vs nvidia).

## Status (2026-09-03)

- Currently mid-way through the USB smoke test (`nixos-eval-usb`, see
  `USB-TEST-RUNBOOK.md`) — installing directly from this Ubuntu session
  (Nix now installed here) rather than a live-ISO boot.
- `niri/` is a verbatim copy of `~/.config/niri/` (config.kdl, autostart.kdl,
  noctalia/*.kdl, scripts/) from 2026-09-02 — but no longer wired into
  `home.nix` directly. `/home/erik` is bind-mounted from the real Ubuntu
  partition instead (see `hosts/hp-envy.nix` / `hosts/nitro.nix`), so the
  live files are used as-is; `niri/` here is just a point-in-time reference.
- `niri` itself comes straight from nixpkgs (`pkgs.niri`, substituted
  binary), not niri-flake — niri-flake forced a from-source Rust build that
  hit a crates.io 403 mid-install; nixpkgs 25.05 has the same version
  (25.08) pre-built. Lost niri-flake's `niri-session` wrapper; greetd execs
  `niri` directly instead.
- `noctalia-shell` package is `pkgs.noctalia` (its own overlay), not
  `noctalia-shell` — confirmed via `nix flake show`.
- `nixpkgs` is pinned to `nixos-25.05` (stable), not unstable — hit a
  transient unstable-branch breakage (`libdisplay-info_0_2` removed)
  during the first real install attempt. `home-manager` matched to
  `release-25.05` for the same reason (its `master` branch assumes
  unstable-shaped nixpkgs internals).

## Deliberately out of scope

- **The Steam game library and every per-game `.desktop` launcher** (Half-Life
  Alyx, Portal 2, Subnautica, the VR titles, etc.) — those are Steam-managed
  content, not config. Point Steam at the existing library after logging in
  if you want them under NixOS too.
- **WiVRn, Sunshine, open-tv, Heroic Games Launcher** (all flatpaks) — real
  parts of the desktop, but streaming/VR/launcher infra is a bigger lift than
  "evaluate niri" calls for. Add later if the eval sticks.

## Still worth double-checking

- The flatpak remote `net.sonuscape.mouseless` (mouseless) was installed
  from — check `flatpak remote-list` / `flatpak info net.sonuscape.mouseless`
  on the Ubuntu side before trying to reinstall it under NixOS.
- `claude-code` package (home.nix) — confirmed it exists on nixos-25.05, not
  yet confirmed it actually *runs* correctly once installed.

## Next steps

See `USB-TEST-RUNBOOK.md` for exactly where the smoke test stands. Once it
boots cleanly: `PARTITION-RUNBOOK.md` + `nixos-eval` (internal disk) is the
real dual-boot install, same config either way.
