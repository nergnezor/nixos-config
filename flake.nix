{
  description = "erik's niri evaluation NixOS config (dual-boot alongside Ubuntu, own partition/ESP)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Tracks upstream niri releases closely; provides the NixOS + home-manager
    # modules. Confirm this is still the right choice once nixpkgs' own
    # niri module is reachable (`nix search nixpkgs niri`) — may be redundant
    # by install time.
    niri = {
      url = "github:sodiboo/niri-flake";
      # Without this, niri-flake's own mesa/wayland-stack deps could resolve
      # to a DIFFERENT nixpkgs revision than everything else here, and Nix
      # would store two non-identical copies instead of deduplicating one —
      # this is what actually makes packages NOT share a library in Nix.
      inputs.nixpkgs.follows = "nixpkgs";
    };
    noctalia-shell = {
      url = "github:noctalia-dev/noctalia-shell";
      inputs.nixpkgs.follows = "nixpkgs"; # same reasoning — shared Qt6/quickshell stack
    };
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, niri, noctalia-shell, disko, ... }:
    let
      homeModule = {
        home-manager.useGlobalPackages = true;
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
