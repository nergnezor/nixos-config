{ pkgs, noctalia-shell, ... }:
{
  home.username = "erik";
  home.homeDirectory = "/home/erik";
  home.stateVersion = "25.05"; # matches the nixpkgs/home-manager release-25.05 pin

  home.packages = (with pkgs; [
    ghostty       # dropdown-term.sh spawns this specifically
    alacritty
    fuzzel
    grim
    slurp
    vivaldi       # config.kdl has an output-placement rule keyed on app-id="^vivaldi-stable$"
    vscode
    # spotify, discord, thunderbird, mpv, vlc, gimp stay dropped -- erik only
    # wanted steam added back for the real internal-disk install, not the
    # rest of the trimmed set.
    git           # was pulled in via programs.git before; that module's gone
                  # now that .gitconfig comes from the shared real home
    lazygit
    gh
    jq            # vertical-monitor-stack.sh / dropdown-term.sh parse `niri msg -j` with it
    cliphist
    wl-clipboard
    bottom

    # claude-code: confirmed via `nix eval` that it exists as a real
    # package on nixos-25.05 (previously flagged unverified, now checked).
    claude-code
  ]) ++ [
    # Taken straight from noctalia-shell's OWN flake output (built against
    # its own nixpkgs), NOT via nixpkgs.overlays.default applied to our
    # pkgs — the overlay route builds noctalia against OUR nixpkgs (25.05)
    # and its meson build failed wanting a wayland-protocols staging file
    # (ext-background-effect-v1) that doesn't exist there. This way it
    # builds/substitutes against whatever nixpkgs noctalia-shell actually
    # locks, which has it.
    noctalia-shell.packages.${pkgs.system}.default
  ];

  # No xdg.configFile."niri" and no programs.git here anymore: /home/erik is
  # now a bind mount of the real Ubuntu home (see configuration.nix), so
  # ~/.config/niri, ~/.gitconfig, ~/.claude, ~/projects, ~/.ssh — all of it
  # — are already the live Ubuntu files. Declaring them here too would mean
  # home-manager's activation tries to write its own version over the same
  # real files instead of a nix-store copy, which is destructive, not a
  # merge. (The niri/ directory still sitting in this repo is now just a
  # point-in-time reference snapshot, not wired into home.nix.)

  # systemd.user.services.mouseless was here, ported from Ubuntu's unit --
  # removed because it writes ~/.config/systemd/user/mouseless.service,
  # the exact path Ubuntu's real unit file already occupies in the shared
  # home. home-manager refuses to clobber it ("Existing file ... is in the
  # way of ..."), which aborted the WHOLE activation on first boot -- so no
  # packages landed in the profile either, which is why noctalia was
  # missing. Same collision class as the niri config and .gitconfig above;
  # this one just got missed. systemd --user picks up Ubuntu's own unit
  # from the shared home regardless, so nothing is lost by dropping it.
  # (It won't actually run on NixOS until the mouseless flatpak is
  # installed there, same as Ubuntu's actions-runner unit doesn't.)

  home.sessionVariables = {
    XDG_CURRENT_DESKTOP = "niri";
  };
}
