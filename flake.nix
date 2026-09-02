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
    niri.url = "github:sodiboo/niri-flake";
    noctalia-shell.url = "github:noctalia-dev/noctalia-shell";
  };

  outputs = { self, nixpkgs, home-manager, niri, noctalia-shell, ... }: {
    nixosConfigurations.nixos-eval = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      specialArgs = { inherit niri noctalia-shell; };
      modules = [
        niri.nixosModules.niri
        ./configuration.nix
        home-manager.nixosModules.home-manager
        {
          home-manager.useGlobalPackages = true;
          home-manager.useUserPackages = true;
          home-manager.users.erik = import ./home.nix;
        }
      ];
    };
  };
}
