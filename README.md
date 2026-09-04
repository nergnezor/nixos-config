# niri on NixOS — parallel eval install

Dual-boot NixOS on `erik-HP-ENVY-TE01-1xxx`, own partitions, alongside the
working Ubuntu install. Goal: evaluate niri/NixOS with the real config and
the real app set, not a toy setup. Same config also targets the Nitro
runner (`hosts/nitro.nix`) for a hardware comparison (Intel Arc vs nvidia).

## Status (2026-09-04)

Recovery finished. The machine boots the installed system from p2/p3/p5
directly — see `PARTITION-RUNBOOK.md` for the final layout and how the
resize disaster was recovered from. Only remaining item: installing the
`net.sonuscape.mouseless` flatpak (details in that file).

## Status (2026-09-03)

- **USB smoke test abandoned — the config is proven, the stick isn't.** The
  full 12GB closure built and copied successfully (steam, nvidia, noctalia,
  everything), but `nix-store --verify --check-contents` then found
  *thousands* of paths silently missing from the USB stick's copy despite
  the DB believing them present, and the verify run itself crashed with a
  SQLite foreign-key error partway through repairing it. Consistent with the
  repeated "device offline"/disconnect events that stick had all session —
  it's silently dropping written data, not a config problem. Moving straight
  to the internal-disk install instead: the local build (against reliable
  NVMe) already succeeded cleanly, which was the actual point of the smoke
  test.
- `actions-runner.service` confirmed still stopped (from the start of this
  session) — no restart needed before the internal-disk work.
- `PARTITION-RUNBOOK.md` updated: only the shrink/create-partitions steps
  (0-7) need live boot media now. Once `p6`/`p7` exist, install onto them
  happens from a normal running Ubuntu session — same
  build-locally-then-copy technique that worked on the USB target, using
  the internal NVMe (reliable) as the copy destination instead. Also fixed
  there: pin `nixos-install-tools` to the explicit
  `github:NixOS/nixpkgs/nixos-25.05#` URL — bare `nixpkgs#nixos-install-tools`
  resolves through the global flake registry to whatever unstable currently
  points at (hit a `26.11pre` dev snapshot with a broken chroot/bootloader
  step on the USB attempt).
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

`PARTITION-RUNBOOK.md`, steps 0-7: boot a NixOS live USB/SD (the SD card is
still flashed from the smoke test), shrink `nvme0n1p5`, create `p6`/`p7`.
Steps 8-9 (the actual install) happen back in Ubuntu, no live boot needed
for those.
