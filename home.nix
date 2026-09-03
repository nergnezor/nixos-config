{ pkgs, ... }:
{
  home.username = "erik";
  home.homeDirectory = "/home/erik";
  home.stateVersion = "25.05"; # matches the nixpkgs/home-manager release-25.05 pin

  home.packages = with pkgs; [
    ghostty       # dropdown-term.sh spawns this specifically
    alacritty
    fuzzel
    grim
    slurp
    vivaldi       # config.kdl has an output-placement rule keyed on app-id="^vivaldi-stable$"
    vscode
    spotify
    discord
    thunderbird
    mpv
    vlc
    gimp
    git           # was pulled in via programs.git before; that module's gone
                  # now that .gitconfig comes from the shared real home
    lazygit
    gh
    jq            # vertical-monitor-stack.sh / dropdown-term.sh parse `niri msg -j` with it
    cliphist
    wl-clipboard
    bottom

    # Overlay applied in configuration.nix (nixpkgs.overlays needs to be a
    # NixOS-level option, not set here). Attribute name unverified — check
    # `nix flake show github:noctalia-dev/noctalia-shell` once online and
    # fix if it's not `noctalia-shell` on the overlay's final package set.
    noctalia-shell

    # For AngelBeach conversation/session continuity (~/.claude comes from
    # the bind-mounted real home — see configuration.nix). Attribute name
    # unverified — nixpkgs may or may not
    # carry `claude-code` on whatever nixos-unstable revision you land on;
    # check `nix search nixpkgs claude-code` once online. If it's missing,
    # fall back to the officially documented install instead:
    #   home.packages: add `nodejs`
    #   npm install -g @anthropic-ai/claude-code
    claude-code
  ];

  # No xdg.configFile."niri" and no programs.git here anymore: /home/erik is
  # now a bind mount of the real Ubuntu home (see configuration.nix), so
  # ~/.config/niri, ~/.gitconfig, ~/.claude, ~/projects, ~/.ssh — all of it
  # — are already the live Ubuntu files. Declaring them here too would mean
  # home-manager's activation tries to write its own version over the same
  # real files instead of a nix-store copy, which is destructive, not a
  # merge. (The niri/ directory still sitting in this repo is now just a
  # point-in-time reference snapshot, not wired into home.nix.)

  # Ported verbatim from ~/.config/systemd/user/mouseless.service on Ubuntu.
  systemd.user.services.mouseless = {
    Unit = {
      Description = "Mouseless keyboard mouse control";
      PartOf = [ "graphical-session.target" ];
      After = [ "graphical-session.target" "niri.service" ];
      Wants = [ "graphical-session.target" ];
    };
    Service = {
      Type = "exec";
      ExecStart = "%h/.config/niri/scripts/start-mouseless.sh";
      ExecStop = ''/bin/bash -c 'flatpak kill net.sonuscape.mouseless; pkill -f "/app/share/mouseless/src/main.pyc" || true' '';
      TimeoutStopSec = 15;
      Restart = "on-failure";
      RestartSec = 10;
      Slice = "app.slice";
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };

  home.sessionVariables = {
    XDG_CURRENT_DESKTOP = "niri";
  };
}
