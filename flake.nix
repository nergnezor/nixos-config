{
  description = "erik's niri NixOS config (whole-disk on both hp-envy and nitro)";

  inputs = {
    # Floating nixos-unstable (not a release branch). Locked by flake.lock;
    # bump deliberately with `nix flake update nixpkgs` + a rebuild to check.
    #
    # Why unstable: originally so niri/ghostty would version-match an Ubuntu
    # install that shared this home directory (see git history before
    # 2026-09-07) -- both machines are whole-disk NixOS now. Kept on unstable
    # for up-to-date niri/noctalia rather than switching to a release branch.
    #
    # Was previously pinned to an exact rev after a floating nixos-unstable
    # broke an install (`libdisplay-info_0_2` removed mid-channel). Floating
    # again now that the machines are past that install window; flake.lock
    # still freezes the resolved rev until you update it on purpose.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
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

    # Provides spotify + spicetify-cli wired together as one home-manager
    # module (programs.spicetify) -- plain nixpkgs spicetify-cli patches an
    # Electron install in place, which doesn't work against a read-only
    # nix store. This builds the patched app as its own derivation instead.
    spicetify-nix = {
      url = "github:Gerg-L/spicetify-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Whole-disk partitioning for nitro (disko-nitro.nix) -- also imported
    # as a NixOS module so its fileSystems/boot config end up in the built
    # system, not just used standalone via `nix run github:...disko`.
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, noctalia-shell, spicetify-nix, disko, ... }:
    let
      homeModule = {
        home-manager.useGlobalPkgs = true;
        home-manager.useUserPackages = true;
        home-manager.extraSpecialArgs = { inherit spicetify-nix; };
        home-manager.sharedModules = [ spicetify-nix.homeManagerModules.spicetify ];
        home-manager.users.erik = import ./home.nix;
      };
      # hostModule carries everything machine-specific: hostName, GPU driver,
      # how that machine gets /home, and (2026-09-07) its own
      # hardware-configuration-<name>.nix import — see hosts/hp-envy.nix and
      # hosts/nitro.nix. Used to be one shared hardware-configuration.nix;
      # split per-host once nitro stopped being a throwaway USB comparison,
      # since a shared file meant either host's `nixos-generate-config`
      # would silently overwrite the other's UUIDs.
      mkHost = { hostModule, extraModules ? [ ] }: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./configuration.nix
          hostModule
          home-manager.nixosModules.home-manager
          homeModule
          # Binds programs.noctalia.package to THIS flake's package output
          # (its own nixpkgs), not an overlay on ours — see configuration.nix.
          noctalia-shell.nixosModules.default
        ] ++ extraModules;
      };
    in
    {
      # The main install on the HP: nvme0n1p2 (ESP) + p3 (root) + p5 (/home),
      # see PARTITION-RUNBOOK.md.
      nixosConfigurations.nixos-eval = mkHost {
        hostModule = ./hosts/hp-envy.nix;
      };
      # The Nitro, whole-disk (2026-09-07) -- Ubuntu replaced entirely, see
      # disko-nitro.nix. Named after hosts/nitro.nix's own hostName, unlike
      # hp-envy's nixos-eval/nixos-hp mismatch above.
      nixosConfigurations.nixos-nitro = mkHost {
        hostModule = ./hosts/nitro.nix;
        # disko's NixOS module turns disko-nitro.nix's disko.devices into
        # real fileSystems/boot.loader entries -- without this the build
        # fails ("fileSystems option does not specify your root file
        # system") because hardware-configuration-nitro.nix is generated
        # with --no-filesystems on the assumption disko covers it.
        extraModules = [ disko.nixosModules.disko ./disko-nitro.nix ];
      };
    };
}
