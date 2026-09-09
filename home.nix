{ config, lib, pkgs, spicetify-nix, ... }:
let
  spicePkgs = spicetify-nix.legacyPackages.${pkgs.stdenv.hostPlatform.system};
  uxstreamTools = import ./uxstream-tools.nix { inherit pkgs; };
  # Low-latency USB camera viewer. mpv + v4l2 beats cheese/guvcview for
  # latency; the camera on this machine tops out at 30 fps (YUYV only), so
  # "fast" here means minimal buffering, not inventing frames the sensor
  # cannot deliver. Rotation is a property (video-rotate), not a re-encode.
  usbcam = pkgs.writeShellApplication {
    name = "usbcam";
    runtimeInputs = with pkgs; [ mpv v4l-utils ];
    text = builtins.readFile ./scripts/usbcam.sh;
  };
in
{
  home.username = "erik";
  home.homeDirectory = "/home/erik";
  home.stateVersion = "25.05"; # matches the nixpkgs/home-manager release-25.05 pin

  home.packages = [
    usbcam
  ]
  ++ uxstreamTools.packages
  ++ (with pkgs; [
    kitty         # dropdown-term.sh spawns this specifically -- replaced
                  # ghostty (2026-09-07): erik switched terminals, and
                  # kitty's cursor_trail is the "flygande pekare" effect
                  # home/kitty/kitty.conf enables. ghostty's minimal config
                  # (home/ghostty/config) was itself a rebuild-from-scratch
                  # after the corruption incident -- see PARTITION-RUNBOOK.md
                  # -- so nothing but that reconstructed config is lost here.
    # alacritty
    # fuzzel
    # grim
    # slurp
    vivaldi       # config.kdl has an output-placement rule keyed on app-id="^vivaldi-stable$"
    vscode
    # AstroNvim (github:nergnezor/astronvim, its own repo -- not vendored
    # here, see the xdg.configFile."nvim" symlink below) needs a C compiler
    # for treesitter parsers, ripgrep/fd for telescope/snacks, node for
    # LSPs, and unzip for Mason zip installs (stylua, etc.).
    neovim
    neovide
    ripgrep
    fd
    gcc
    gnumake
    unzip
    wget          # Mason's cpptools downloader shells out to wget
    nodejs_22
    # discord, thunderbird, vlc, gimp stay dropped -- erik only wanted
    # steam added back for the real internal-disk install, not the rest of
    # the trimmed set. mpv is pulled in only as a runtimeInput of usbcam
    # above, not as a general media player here. Spotify itself now comes
    # from programs.spicetify below, not this list -- the spicetify-nix
    # module installs its own patched build and warns against also listing
    # pkgs.spotify here.
    git           # was pulled in via programs.git before; that module's gone
                  # now that .gitconfig comes from the shared real home
    lazygit
    gh
    jq            # vertical-monitor-stack.sh / dropdown-term.sh parse `niri msg -j` with it
    bat           # cat clone med syntax-highlighting
    qdirstat      # disk usage treemap, GUI
    gdu           # disk usage, terminal TUI
    # tmux moved to programs.tmux below -- that module installs the package
    # itself, and the resurrect/continuum plugins have to be declared next
    # to it anyway.
    nerd-fonts.jetbrains-mono # VS Code/kitty had no monospace font on this
                              # NixOS install; Ubuntu had one system-wide.
                              # kitty.conf also names it explicitly
                              # (JetBrainsMono Nerd Font) so the glyphs
                              # AstroNvim's UI depends on (icons, separators)
                              # actually render.
    nerd-fonts.fira-code      # Neovide guifont in astronvim
                              # (lua/plugins/astrocore.lua:
                              # `FiraCode_Nerd_Font:h10`). JetBrains covers
                              # kitty/VS Code; this one is what the GUI
                              # editor asks for by name.
    # cliphist
    wl-clipboard  # Claude Code shells out to `wl-paste` to read an image off
                  # the clipboard; without it Ctrl+V in the CLI finds nothing
                  # and silently pastes no image. Also what noctalia's
                  # clipboard widget uses.
    # bottom

    # claude-code: confirmed via `nix eval` that it exists as a real
    # package on nixos-25.05 (previously flagged unverified, now checked).
    claude-code
    cursor-cli    # ships the `cursor-agent` binary, not `cursor` -- confirmed
                  # via a direct `nix build` + `--version` check (unfree,
                  # already allowed by nixpkgs.config.allowUnfree above)
    github-copilot-cli # `npm i -g @github/copilot` fails on NixOS: npm
                       # tries to mkdir into the immutable nodejs store path.
                       # Use the nixpkgs package instead (provides `copilot`).
  ]);

  # Shows up in noctalia/fuzzel/etc. as "USB Camera"; always starts rotated
  # 270° (the mount orientation on this desk). CLI `usbcam` stays unrotated
  # by default so -r still means something when run by hand.
  xdg.desktopEntries.usbcam = {
    name = "USB Camera";
    genericName = "Camera";
    comment = "Low-latency USB camera (rotated 270°)";
    exec = "${lib.getExe usbcam} -r 270";
    icon = "camera-web";
    categories = [ "AudioVideo" "Video" "Photography" ];
    terminal = false;
  };

  # noctalia is installed by programs.noctalia in configuration.nix (NixOS
  # module, systemd user unit in /etc), not here — a home.packages entry
  # only put the binary in the profile; Ubuntu's shared-home unit still
  # exec'd /usr/local/bin/noctalia, which does not exist on NixOS.

  # ~/.config/niri is a symlink to this repo's niri/ directory, so the live
  # config IS the tracked one. mkOutOfStoreSymlink, not the usual
  # xdg.configFile source: that would copy the files into the nix store and
  # symlink to a read-only path, which means every keybind tweak needs a
  # rebuild and niri's own live reload stops being useful. This points at
  # the working tree instead — edit, save, niri reloads, `git diff` shows
  # what changed.
  #
  # This exists because the config was nearly lost. The filesystem damage
  # left `config.kdl` as 10240 bytes of unrelated data and emptied
  # `noctalia/` entirely, and niri will not start without a readable config
  # — which is what turned an unreadable login prompt into an unloggable-in
  # machine. What saved it was this repo's `niri/` snapshot, which was
  # sitting here by accident, described as "a point-in-time reference". Now
  # it is the source rather than a coincidence.
  #
  # A dangling link (repo moved or missing) degrades to niri's built-in
  # defaults rather than a failure to start, so it cannot lock you out the
  # way a corrupt config did.
  #
  # **Before the first rebuild after adding this, move the existing
  # directory aside** — home-manager refuses to overwrite a real
  # `~/.config/niri` and aborts the whole activation, which is the same
  # failure that once left the profile with no packages at all:
  #   mv ~/.config/niri ~/.config/niri.pre-symlink
  xdg.configFile."niri".source =
    config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/nixos-config/niri";

  # ~/.config/nvim -- AstroNvim, but tracked as its OWN repo
  # (github:nergnezor/astronvim), not vendored inside nixos-config. This
  # just declares the same plain symlink erik already has on Ubuntu
  # (`~/.config/nvim -> ~/astronvim`), so a rebuild recreates it rather
  # than leaving a fresh machine with no editor config at all.
  #
  # mkOutOfStoreSymlink again, and for the same reason as niri: AstroNvim
  # only reads its lua config at startup, and lazy-lock.json (which DOES
  # get rewritten, by lazy.nvim on plugin updates) belongs in astronvim's
  # own git history, not copied through the nix store on every edit.
  #
  # Fresh machines get ~/astronvim via home.activation.cloneAstronvim
  # below. **Before the first rebuild on a machine that already has a
  # real ~/.config/nvim**, move the old one aside -- same collision class
  # as niri and tmux above:
  #   mv ~/.config/nvim ~/.config/nvim.pre-symlink
  xdg.configFile."nvim".source =
    config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/astronvim";

  # Clone the AstroNvim config repo on first activation if missing. Kept
  # out of the nix store on purpose (writable lazy-lock.json, own git
  # history); this only ensures the symlink target exists after a fresh
  # install so Neovide/nvim are not left pointing at nothing.
  home.activation.cloneAstronvim = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    if [ ! -e "${config.home.homeDirectory}/astronvim" ]; then
      $DRY_RUN_CMD ${pkgs.git}/bin/git clone \
        https://github.com/nergnezor/astronvim \
        "${config.home.homeDirectory}/astronvim"
    fi
  '';

  # programs.git stays undeclared: ~/.gitconfig came back from the rescue
  # and is the working copy. Same reasoning as the niri config had before
  # this commit — adopt it into the repo deliberately if you want it
  # managed, rather than letting home-manager write over it.

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

  # Persistent terminal sessions -- attach over SSH (incl. from mobile, via
  # Tailscale) to watch/steer a long-running Claude Code session without
  # staying at the machine. resurrect/continuum make that survive a reboot.
  #
  # What actually comes back: sessions, windows, panes, layouts, working
  # directories, the active pane, and (capture-pane-contents) the visible
  # text in each pane. What does NOT come back is the processes -- a Claude
  # Code that was running in a pane is gone, and the way back into that
  # conversation is `claude --continue`, not tmux. Restoring the shape of
  # the workspace is the whole benefit here; do not expect more.
  #
  # No systemd user service and no `loginctl enable-linger erik`: continuum
  # restores when the tmux *server* starts, and without linger that is the
  # first time you run tmux after logging in, not boot. So the session is
  # not sitting there waiting when you SSH in -- run `tmux new -A -s main`
  # and the previous layout comes back. Add the unit + linger later if
  # having it pre-started matters.
  #
  # **Before the first rebuild after adding this, move the old config
  # aside** -- tmux reads `~/.tmux.conf` OR `$XDG_CONFIG_HOME/tmux/tmux.conf`
  # (tmux(1)), and the former wins, so a leftover ~/.tmux.conf silently
  # shadows everything below and none of it takes effect:
  #   mv ~/.tmux.conf ~/.tmux.conf.pre-hm
  programs.tmux = {
    enable = true;
    # The lone binding that was in the hand-written ~/.tmux.conf.
    extraConfig = ''
      bind-key J move-window -t 0
    '';
    # Submodule form ({ plugin; extraConfig; }), not a bare package: the
    # @-options have to be set BEFORE the plugin's run-shell line or the
    # plugin never sees them, and this form is what emits them in that
    # order.
    plugins = with pkgs.tmuxPlugins; [
      {
        plugin = resurrect;
        # Off by default; without it panes come back empty and you lose the
        # scrollback that says what the session was doing.
        extraConfig = "set -g @resurrect-capture-pane-contents 'on'";
      }
      {
        plugin = continuum;
        # continuum must come after resurrect -- it drives resurrect's save
        # and restore, and has nothing to call otherwise.
        extraConfig = ''
          set -g @continuum-restore 'on'
          set -g @continuum-save-interval '15'
        '';
      }
    ];
  };

  # Noctalia's gtk3/gtk4 templates only inject palette CSS into gtk.css.
  # That does not flip GTK3 Adwaita to its dark variant — and the file
  # chooser from xdg-desktop-portal-gtk (programs.niri.useNautilus =
  # false) is classic GTK3, so it stayed light while the shell was dark.
  # settings.ini + dconf prefer-dark is what actually switches it.
  # gtk.css / noctalia.css are left alone for Noctalia to keep owning.
  gtk = {
    enable = true;
    gtk3.extraConfig = {
      gtk-application-prefer-dark-theme = 1;
    };
    gtk4.extraConfig = {
      gtk-application-prefer-dark-theme = 1;
    };
  };

  dconf.settings = {
    "org/gnome/desktop/interface" = {
      color-scheme = "prefer-dark";
    };
  };

  # Qt apps (qdirstat, etc.) otherwise ignore Noctalia's orphaned
  # qt5ct/qt6ct color files and fall back to a light style. "adwaita"
  # follows the gsettings color-scheme above.
  qt = {
    enable = true;
    platformTheme.name = "adwaita";
    style.name = "adwaita-dark";
  };

  home.sessionVariables = {
    XDG_CURRENT_DESKTOP = "niri";
    # Belt-and-suspenders for GTK3 apps that skip settings.ini; niri's
    # environment block also sets this so dbus-activated portals see it.
    GTK_THEME = "Adwaita:dark";
  };

  # No theme/extensions picked here -- Marketplace is the in-app browser for
  # both, so pick visually from inside Spotify rather than guessing here.
  # After a rebuild: open Spotify, the Marketplace icon sits in the top bar.
  programs.spicetify = {
    enable = true;
    enabledCustomApps = with spicePkgs.apps; [ marketplace ];
  };
}
