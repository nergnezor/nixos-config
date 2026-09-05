# niri on NixOS — `nixos-config`

The NixOS configuration for `nixos-hp` (an HP ENVY TE01, nvidia RTX 3060 Ti):
a niri/Wayland session with the noctalia shell, greetd/tuigreet, and the real
app set. The same config also targets the Nitro runner (`hosts/nitro.nix`,
Intel Arc A750) for a hardware comparison.

## Status (2026-09-05)

The HP is **NixOS-only** — the Ubuntu install it used to dual-boot with is
gone, along with the shared-home arrangement that came with it. `/home` is
now a partition of its own (`nvme0n1p5`, btrfs, label `home`, `subvol=@home`);
see `PARTITION-RUNBOOK.md` for the layout and for how the interrupted resize
was recovered from. The `net.sonuscape.mouseless` flatpak is installed and its
user unit is declared in `configuration.nix`.

## Layout

| File | What it carries |
|---|---|
| `flake.nix`, `flake.lock` | The pins: an exact nixpkgs revision (**unstable**, not a release branch — the reasoning is in `flake.nix` and it matters), home-manager `master`, noctalia-shell on its own nixpkgs, disko |
| `configuration.nix` | The system: niri, greetd/tuigreet, noctalia, pipewire, steam, xrdp+Plasma over Tailscale, sshd, flatpak, xwayland-satellite, mouseless's unit and udev/tmpfiles rules, `erik` as 1000:1000, Swedish layout |
| `hosts/hp-envy.nix`, `hosts/nitro.nix` | Everything machine-specific: hostname, GPU driver, how `/home` is provided |
| `hardware-configuration.nix` | **The HP's**, by UUID. One filename for every host — regenerate it on any new machine (see below) |
| `home.nix` | The user's packages, and the `mkOutOfStoreSymlink` that makes `~/.config/niri` this repo's `niri/` |
| `niri/**` | The live niri config. Not a snapshot — `~/.config/niri` *is* this directory |
| `noctalia/settings.toml`, `noctalia/sync.sh` | Noctalia's settings, as a synced copy |
| `home/**`, `home/sync.sh` | The hand-written `$HOME` dotfiles, as synced copies |
| `rebuild.sh` | Sync live config into the repo, then `nixos-rebuild`; optionally commit/push once it built |
| `disko-usb.nix`, `install-to-nixos-partition.sh`, `USB-TEST-RUNBOOK.md`, `PARTITION-RUNBOOK.md` | Installation |

Day to day, `nrb` / `nrbc` / `nrbp` (aliases in `home/bashrc`) are `rebuild.sh`.

**Untracked `.nix` files are invisible to the flake build** — nix only sees
what git tracks, so a new module you forgot to `git add` simply does not
exist as far as the build is concerned. `rebuild.sh` warns about this.

## The three sync mechanisms, and why they differ

- **niri — a symlink.** `~/.config/niri` points into this working tree, so
  edit, save, and niri live-reloads; `git diff` shows what changed. This works
  because niri only ever *reads* its config. (It exists because the config was
  nearly lost: filesystem damage left `config.kdl` as 10240 bytes of unrelated
  data, and niri will not start without a readable config — an unreadable
  login prompt became an unloggable-in machine.)
- **noctalia — a copy** (`./noctalia/sync.sh {pull|push|diff}`). Noctalia
  *writes* `settings.toml` from its own GUI, and by replacing the file, which
  turns a symlink back into a plain file on the first tweak. So: change things
  in the GUI, then `sync.sh pull` and commit.
- **`$HOME` dotfiles — copies** (`./home/sync.sh {pull|push|diff}`):
  `.bashrc`, `.profile`, `.gitconfig`, `~/.config/ghostty/config`, and VS
  Code's `settings.json` / `keybindings.json` / extension list. Not
  home-manager: each of these already exists as a real file in `$HOME`, and
  home-manager aborts the *entire* activation rather than overwrite one —
  the failure that once left the profile with no packages at all.

`rebuild.sh` runs both `pull`s before every rebuild, so the repo does not
drift from the machine.

## Not tracked

Some of this is deliberate, some is simply not expressible here. All of it is
what a new machine still needs by hand.

- **Secrets and accounts.** `sudo tailscale up`, `gh auth login`, Steam, the
  `erik`/`root` passwords (set during install), and noctalia's Google calendar
  — `noctalia/sync.sh` scrubs `[calendar.account.<name>]` on `pull` because
  this repo is public. Nothing is lost: the OAuth credentials were never in
  `settings.toml`, so the calendar has to be re-linked in the GUI anyway.
- **SSH.** Keys, `~/.ssh/config` (it names a real host and port), and
  `authorized_keys` — which is *empty*, while sshd has `PasswordAuthentication
  = false`. There is no SSH way into a fresh machine until `ssh-copy-id` has
  run from a trusted one.
- **The flatpak remote.** `flatpak remote-add --if-not-exists sonuscape
  https://dl.sonuscape.net/flatpak/repo`, then `flatpak install sonuscape
  net.sonuscape.mouseless`. Both `--user` scope. nixpkgs has no option for
  declaring flatpak remotes at all — that needs the nix-flatpak flake, which
  is not an input here.
- **Noctalia plugins and generated themes.** `settings.toml` enables nine
  plugins (bongocat, game-launcher, claude-companion, desktop-launcher,
  git_companion, nix-monitor, nvtop, tailscale, tmux-provider) and the bar
  refers to their widgets, but `plugins/`, `plugin-cache/`,
  `community-palettes/`, `community-templates/` and `state.toml` are machine
  state, not config. The generated theme files (`~/.config/gtk-3.0/noctalia.css`,
  btop, ghostty, lazygit, qt5ct/qt6ct, the VS Code theme) are regenerated from
  `[theme.templates]`.
- **The wallpaper.** `settings.toml` points every output at
  `~/Pictures/hyperlink-dimension-al-7680x4320.jpg` (5.5 MB), which lives in
  the home directory, not here.
- **The Steam library and its per-game `.desktop` launchers** — Steam-managed
  content. Point Steam at an existing library after logging in.
- **WiVRn, Sunshine, open-tv, Heroic** (flatpaks) — real parts of the desktop,
  never carried over.

## Bringing up a new machine

1. Partition and install per `PARTITION-RUNBOOK.md`.
2. `nixos-generate-config` on the target, and put the result in
   `hardware-configuration.nix` — the tracked one is the HP's, by UUID.
3. Add a `hosts/<machine>.nix` and a `nixosConfigurations.<name>` output in
   `flake.nix`, plus the host→attr mapping in `rebuild.sh` (it only knows
   `nixos-hp` → `nixos-eval`).
4. `git add` all of it before building.
5. `mv ~/.config/niri ~/.config/niri.pre-symlink` before the first rebuild —
   home-manager will not overwrite a real directory, and aborts the whole
   activation if it finds one.
6. `nixos-rebuild switch --flake .#<name>`.
7. Then, by hand: `./noctalia/sync.sh push`, `./home/sync.sh push`, the
   flatpak remote and mouseless, the wallpaper, `sudo tailscale up`,
   `gh auth login`, SSH keys.

## History worth keeping

- `niri` comes from nixpkgs (`programs.niri.enable`), not niri-flake:
  niri-flake forced a from-source Rust build that hit a crates.io 403
  mid-install. The nixpkgs module also installs niri's *user units*, without
  which `graphical-session.target` never activates and every user service
  hanging off it stays dead.
- The nixpkgs pin is a specific unstable revision on purpose, and
  home-manager tracks `master` to match it. A floating unstable broke an
  install outright (`libdisplay-info_0_2` removed mid-flight); a release
  branch shipped a niri too old to parse this config. `flake.nix` has the
  long version.
- `noctalia-shell` deliberately does **not** follow this flake's nixpkgs, and
  its overlay is not used — both were tried and reverted. It keeps the
  nixpkgs it was built against.
- An earlier USB smoke-test install was abandoned: the stick silently dropped
  written data (`nix-store --verify` found thousands of paths missing). The
  config was never the problem — the local build against the NVMe succeeded.
