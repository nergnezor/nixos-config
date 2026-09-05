{
  description = "erik's niri evaluation NixOS config (dual-boot alongside Ubuntu, own partition/ESP)";

  inputs = {
    # A PINNED unstable revision, not a release branch and not a floating
    # branch either.
    #
    # Why unstable: this system shares one home directory with an Ubuntu
    # 26.04 install, so anything that reads config or profile data from it
    # has to understand what Ubuntu's (newer) build writes. niri made that
    # concrete -- 25.05 ships niri 25.08, which can't parse the 26.04 config
    # schema (recent-windows, gestures/hot-corners, config-notification,
    # overview/workspace-shadow) and silently fell back to defaults: the
    # "failed to read niri config" on first boot. ghostty had the same shape
    # of gap (1.1.3 vs Ubuntu's 1.3.1). Version parity is a functional
    # requirement here, not a preference.
    #
    # Why pinned: a floating nixos-unstable is what broke an earlier install
    # attempt outright (`libdisplay-info_0_2` removed before every internal
    # caller was updated). This exact revision is verified working -- it's
    # the one that produced niri 26.04 as a cached binary. Bump it
    # deliberately, with a rebuild to check, rather than drifting.
    nixpkgs.url = "github:NixOS/nixpkgs/3ed67ec0a4d3c7ab4ae1f04f8ee8df07bfa506a2";
    home-manager = {
      # master is the branch meant to pair with unstable. Pairing it with a
      # release branch is what produced the earlier hard eval error
      # (home-manager's modules/services-modular reaching for a nixpkgs
      # lib/services/lib.nix that only exists on the other side).
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    #
    # noctalia-shell is NOT following the top-level nixpkgs (tried it,
    # reverted): it locks against a specific nixpkgs revision it's actually
    # been built/tested with. Forcing it onto our newer pin broke the first
    # real install attempt — nixpkgs had removed `libdisplay-info_0_2` (an old
    # versioned alias) in the window between its lock and ours. Sharing the
    # Qt6 base would save some store space, but a config that fails to
    # evaluate saves none — it keeps its own known-working nixpkgs instead.
    noctalia-shell.url = "github:noctalia-dev/noctalia-shell";
  };

  outputs = { self, nixpkgs, home-manager, noctalia-shell, ... }:
    let
      homeModule = {
        home-manager.useGlobalPkgs = true;
        home-manager.useUserPackages = true;
        home-manager.users.erik = import ./home.nix;
      };
      # hostModule carries everything machine-specific: hostName, GPU driver,
      # and how that machine gets /home — hp-envy has its own partition,
      # see hosts/hp-envy.nix. hardware-configuration.nix (shared filename,
      # not host-specific in this repo) gets regenerated/overwritten for
      # whichever target you're installing.
      mkHost = { hostModule }: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./configuration.nix
          hostModule
          home-manager.nixosModules.home-manager
          homeModule
          # Binds programs.noctalia.package to THIS flake's package output
          # (its own nixpkgs), not an overlay on ours — see configuration.nix.
          noctalia-shell.nixosModules.default
        ];
      };
    in
    {
      # The main install on the HP: nvme0n1p2 (ESP) + p3 (root) + p5 (/home),
      # see PARTITION-RUNBOOK.md.
      nixosConfigurations.nixos-eval = mkHost {
        hostModule = ./hosts/hp-envy.nix;
      };
    };
}
