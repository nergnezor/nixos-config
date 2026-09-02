# niri on NixOS — parallel eval install

Dual-boot NixOS on this machine (`erik-HP-ENVY-TE01-1xxx`), own partitions,
alongside the working Ubuntu install. Goal: evaluate niri/NixOS with the real
config and the real app set, not a toy setup.

## Status (2026-09-02)

- `actions-runner.service` (CI runner) **stopped** before starting this —
  restart with `systemctl --user start actions-runner` once back in Ubuntu.
- Partition math for reclaiming 50GiB from `nvme0n1p5` is computed and
  verified against this disk's actual sysfs geometry — see
  `PARTITION-RUNBOOK.md`. Not yet executed (needs a live USB boot).
- `niri/` here is a verbatim copy of `~/.config/niri/` (config.kdl,
  autostart.kdl, noctalia/*.kdl, scripts/) as of today, minus `dms/` (an
  older, unreferenced shell config) and the dated backup file.
- App list in `home.nix` and the mouseless udev/tmpfiles/systemd wiring in
  `configuration.nix`/`home.nix` were read off the live Ubuntu system, not
  guessed.

## Deliberately out of scope

- **The Steam game library and every per-game `.desktop` launcher** (Half-Life
  Alyx, Portal 2, Subnautica, the VR titles, etc.) — those are Steam-managed
  content, not config. Point Steam at the existing library after logging in
  if you want them under NixOS too.
- **WiVRn, Sunshine, open-tv, Heroic Games Launcher** (all flatpaks) — real
  parts of the desktop, but streaming/VR/launcher infra is a bigger lift than
  "evaluate niri" calls for. Add later if the eval sticks.

## Unverified — confirm once you have `nix search`/`nix flake show` on the live ISO

This machine has no `nix` installed, so none of this has been built or
flake-checked. Things to double-check with network access before
`nixos-install`:

- `programs.niri.enable` — from `niri-flake` (sodiboo). Confirm the module's
  actual option surface; nixpkgs may have grown its own niri module by now
  too, in which case simplify.
- `noctalia-shell.overlays.default` — the overlay exists (seen in
  `~/noctalia-shell/flake.nix`), but the exact package attribute it adds
  (assumed `noctalia-shell`) isn't confirmed.
- The flatpak remote `net.sonuscape.mouseless` (mouseless) was installed
  from — check `flatpak remote-list` / `flatpak info net.sonuscape.mouseless`
  on the Ubuntu side before trying to reinstall it under NixOS.
- `system.stateVersion` / `home.stateVersion` are set to `"25.11"` as a
  placeholder — match to whatever the installer ISO's actual release is.

## Next steps

1. Download a NixOS minimal/graphical installer ISO, write it to a USB stick.
2. Boot it on this machine, follow `PARTITION-RUNBOOK.md` start to finish.
3. Once partitioned and mounted at `/mnt`, `git clone`/`scp` this whole
   `nixos-config/` directory onto the live environment (or just copy it via
   the USB stick alongside the ISO).
4. Resolve the "unverified" items above with actual `nix` commands.
5. `nixos-install --flake .#nixos-eval`, reboot, pick NixOS via the firmware
   boot menu (F9).
