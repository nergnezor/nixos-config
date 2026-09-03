{
  description = "erik's niri evaluation NixOS config (dual-boot alongside Ubuntu, own partition/ESP)";

  inputs = {
    # Was nixos-unstable; switched after hitting a genuine transient breakage
    # there (`libdisplay-info_0_2` removed from nixpkgs before every internal
    # caller was updated to the rename — a rolling-branch-today problem, not
    # anything in this repo). A numbered stable release only gets curated
    # backports, not that kind of churn — a better fit for a machine meant
    # to be evaluated, not lived on the bleeding edge of. Tradeoff: `claude-code`
    # (home.nix) may not exist yet on this branch if it landed in nixpkgs
    # after 25.05 branched — check `nix search nixpkgs claude-code` once
    # online; the npm fallback noted in home.nix still works either way.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    home-manager = {
      # master tracks nixpkgs-unstable; paired against our stable 25.05 pin
      # it hit a hard eval error — home-manager's own modules/services-modular
      # reached for a nixpkgs lib path (lib/services/lib.nix) that doesn't
      # exist on that branch. release-25.05 is the branch actually meant to
      # pair with nixpkgs 25.05.
      url = "github:nix-community/home-manager/release-25.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Tracks upstream niri releases closely; provides the NixOS + home-manager
    # modules. Confirm this is still the right choice once nixpkgs' own
    # niri module is reachable (`nix search nixpkgs niri`) — may be redundant
    # by install time.
    #
    # NOT following the top-level nixpkgs (tried it, reverted): niri-flake and
    # noctalia-shell each lock against a specific nixpkgs revision they've
    # actually been built/tested with. Forcing them onto our newer pin broke
    # the first real install attempt — nixpkgs had removed
    # `libdisplay-info_0_2` (an old versioned alias) in the window between
    # their lock and ours. Sharing the mesa/Qt6 base across every input would
    # save some store space, but a config that fails to evaluate saves none —
    # each flake keeps its own known-working nixpkgs instead.
    niri.url = "github:sodiboo/niri-flake";
    noctalia-shell.url = "github:noctalia-dev/noctalia-shell";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, niri, noctalia-shell, disko, ... }:
    let
      homeModule = {
        home-manager.useGlobalPkgs = true;
        home-manager.useUserPackages = true;
        home-manager.users.erik = import ./home.nix;
      };
      # hostModule carries everything machine-specific: hostName, GPU driver,
      # and that machine's own Ubuntu-partition UUID for the /home share (see
      # hosts/hp-envy.nix, hosts/nitro.nix). extraModules is for disko, on
      # the USB-test targets. hardware-configuration.nix (shared filename,
      # not host-specific in this repo) gets regenerated/overwritten for
      # whichever target you're installing at the time.
      mkHost = { hostModule, extraModules ? [ ] }: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = { inherit niri noctalia-shell; };
        modules = [
          niri.nixosModules.niri
          ./configuration.nix
          hostModule
          home-manager.nixosModules.home-manager
          homeModule
        ] ++ extraModules;
      };
    in
    {
      # The real dual-boot install, on its own internal-disk partitions
      # (nvme0n1p6/p7 — see PARTITION-RUNBOOK.md).
      nixosConfigurations.nixos-eval = mkHost {
        hostModule = ./hosts/hp-envy.nix;
      };

      # Smoke-test install target: the 14.6GB external "USB DISK" stick
      # (formerly running Ventoy — see disko-usb.nix), booted from a
      # SEPARATE small USB/SD card flashed with the NixOS installer ISO.
      # Same config as nixos-eval (full app set — this target has the room)
      # minus the internal disk's PARTITION-RUNBOOK.md; disko partitions it
      # instead.
      nixosConfigurations.nixos-eval-usb = mkHost {
        hostModule = ./hosts/hp-envy.nix;
        extraModules = [ disko.nixosModules.disko ./disko-usb.nix ];
      };

      # Same USB-test idea, but for the Nitro runner (Intel Arc A750, its
      # own Ubuntu partition UUID — see hosts/nitro.nix) — plug the same
      # stick in there, re-run disko + nixos-install targeting this output
      # instead. Confirm the stick is still /dev/sda on that machine before
      # running disko (see disko-usb.nix's own caveat) — not guaranteed to
      # match just because it did on the HP box.
      nixosConfigurations.nixos-eval-nitro-usb = mkHost {
        hostModule = ./hosts/nitro.nix;
        extraModules = [ disko.nixosModules.disko ./disko-usb.nix ];
      };
    };
}
