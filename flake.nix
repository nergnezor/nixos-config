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
    # v5 is a native binary that loads the *system* Mesa at runtime via
    # autoAddDriverRunpath (`/run/opengl-driver`). That Mesa is built
    # against THIS nixpkgs' glibc, so noctalia has to be too — otherwise
    # `eglGetDisplay` fails at login and the user unit hits start-limit
    # (confirmed 2026-10-01 on nixos-nitro: glibc 2.42 vs 2.44 after a
    # nixpkgs bump; noctalia kept running through the switch and only
    # died on reboot).
    #
    # Previously left on its own pin: the old Qt/quickshell build wanted
    # `libdisplay-info_0_2` and a wayland-protocols staging file this tree
    # didn't have. v5's package.nix depends on neither.
    noctalia-shell = {
      url = "github:noctalia-dev/noctalia-shell";
      inputs.nixpkgs.follows = "nixpkgs";
    };

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

    # Wayland locker: the desktop dissolves into a storm of its own pixels.
    # PAM service and the package are wired in the module below.
    sandlock = {
      url = "github:Macs1324/sandlock";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, noctalia-shell, spicetify-nix, disko, sandlock, ... }:
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
          # sandlock authenticates against a PAM service of its own name.
          # swayidle only runs the before-sleep hook (niri/autostart.kdl);
          # idle locking stays with noctalia, which calls sandlock.
          ({ pkgs, ... }: {
            nixpkgs.overlays = [ sandlock.overlays.default ];
            environment.systemPackages = [ pkgs.sandlock pkgs.swayidle ];
            security.pam.services.sandlock = { };
          })
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
