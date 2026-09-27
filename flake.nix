{
  inputs = {
    utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, utils }:
    (utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        # python3Packages.chromaprint is the (broken, m2r-dependent) pyacoustid
        # binding; podfetch only shells out to the C tool's fpcalc binary.
        podfetch = pkgs.python3Packages.callPackage ./podfetch.nix {
          chromaprint = pkgs.chromaprint;
        };
      in
      {
        packages.default = pkgs.stdenv.mkDerivation rec {
          pname = "audio-tools";
          version = "2024-05-21";
          src = ./.;
          buildInputs = with pkgs; [ 
            yt-dlp id3v2 dos2unix imagemagick abcde glyr id3lib lame 
            python3.pkgs.mutagen jq iconv
          ];
          nativeBuildInputs = [ pkgs.makeWrapper ];
          installPhase = ''
            mkdir -p $out/bin
            for i in "$src/bin/"*;do
              install -m0755 "$i" -t "$out/bin"
              wrapProgram "$out/bin/$(basename "$i")" \
                --prefix PATH : ${pkgs.lib.makeBinPath buildInputs}
            done
          '';
        };
        packages.podfetch = podfetch;
        packages.podfetchWithWhisper =
          pkgs.python3Packages.callPackage ./podfetch.nix {
            withWhisper = true;
            chromaprint = pkgs.chromaprint;
          };
        devShell = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.default ];
          packages = [ podfetch pkgs.ffmpeg pkgs.chromaprint pkgs.python3 ];
          buildInputs = with pkgs; [ ];
        };
      }
    )) // {
      overlays.default = final: prev: {
        podfetch = final.python3Packages.callPackage ./podfetch.nix {
          chromaprint = final.chromaprint;
        };
      };
      nixosModules.podfetch = ./nixos-module.nix;
      nixosModule = self.nixosModules.podfetch;
    };
}
