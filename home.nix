{ pkgs, config, ... }:
{
  home.username = "erik";
  home.homeDirectory = "/home/erik";
  home.stateVersion = "25.11";

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
  ];

  # Whole niri config directory copied verbatim from the working Ubuntu
  # install (2026-09-02): config.kdl, autostart.kdl, noctalia/*.kdl, scripts/.
  # (dms/ and the dated backup were left behind — unreferenced by config.kdl.)
  xdg.configFile."niri" = {
    source = ./niri;
    recursive = true;
  };

  # Share Claude Code's memory/session state and the actual project
  # checkouts with the Ubuntu install (mounted at /mnt/ubuntu — see
  # configuration.nix), instead of NixOS starting with none of it. An
  # out-of-store symlink, NOT a copy: this points straight at the live
  # Ubuntu files on the shared partition, so edits from either OS are the
  # same real files. Deliberately narrow — do NOT symlink the whole home
  # directory, it would collide with the niri config etc. home-manager
  # already manages above.
  home.file.".claude".source = config.lib.file.mkOutOfStoreSymlink "/mnt/ubuntu/home/erik/.claude";
  home.file."projects".source = config.lib.file.mkOutOfStoreSymlink "/mnt/ubuntu/home/erik/projects";

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

  programs.git = {
    enable = true;
    userName = "nergnezor";
    userEmail = "erikrosengren84@gmail.com";
  };

  home.sessionVariables = {
    XDG_CURRENT_DESKTOP = "niri";
  };
}
