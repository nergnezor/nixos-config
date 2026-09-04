{ config, lib, pkgs, ... }:
{
  home.username = "erik";
  home.homeDirectory = "/home/erik";
  home.stateVersion = "25.05"; # matches the nixpkgs/home-manager release-25.05 pin

  home.packages = (with pkgs; [
    ghostty       # dropdown-term.sh spawns this specifically
    # alacritty
    # fuzzel
    # grim
    # slurp
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
    # cliphist
    # wl-clipboard
    # bottom

    # claude-code: confirmed via `nix eval` that it exists as a real
    # package on nixos-25.05 (previously flagged unverified, now checked).
    claude-code
  ]);
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

  # programs.git stays undeclared: once the rescue copy is out of the way
  # (below), set user.name / userEmail by hand or adopt a real file into
  # the repo. Do not let home-manager invent an identity.

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
  #
  # Generation 10's ~/.config/systemd/user is *not* the session killer:
  # it only contains home-manager's tray.target symlink. Do not quarantine
  # that directory.

  # niri-session re-execs itself as `exec -l $SHELL` before it ever starts
  # niri.service. bash as a login shell sources ~/.profile, and the copy
  # restored from the damaged ext4 filesystem is 239 bytes of binary
  # (NUL, a stray `(`, "syntax error near unexpected token"). The login
  # shell dies, greetd sees the session command exit in one second, niri
  # never reaches the journal. force: home-manager will not overwrite an
  # existing file otherwise.
  home.activation.quarantineCorruptProfile =
    lib.hm.dag.entryBefore [ "writeBoundary" ] ''
      if [ -e "$HOME/.profile" ] && ! sh -n "$HOME/.profile" 2>/dev/null; then
        $DRY_RUN_CMD mv "$HOME/.profile" "$HOME/.profile.corrupt-rescue"
      fi
    '';
  home.file.".profile" = {
    force = true;
    text = ''
      # Managed by home-manager (nixos-config). The copy restored from the
      # damaged ext4 filesystem was binary garbage and broke shell startup.
      if [ -n "$BASH_VERSION" ] && [ -f "$HOME/.bashrc" ]; then
        . "$HOME/.bashrc"
      fi
    '';
  };

  # Same class of leftover as .profile: `git status` dies with
  # "fatal: bad config line 1 in file /home/erik/.gitconfig". Move it
  # aside; a new identity is not invented here.
  home.activation.quarantineCorruptGitconfig =
    lib.hm.dag.entryBefore [ "writeBoundary" ] ''
      if [ -e "$HOME/.gitconfig" ] && ! ${pkgs.git}/bin/git config --file "$HOME/.gitconfig" --list >/dev/null 2>&1; then
        $DRY_RUN_CMD mv "$HOME/.gitconfig" "$HOME/.gitconfig.corrupt-rescue"
      fi
    '';

  home.sessionVariables = {
    XDG_CURRENT_DESKTOP = "niri";
  };
}
