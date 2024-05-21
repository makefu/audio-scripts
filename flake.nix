{
  inputs = {
    utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, utils }: utils.lib.eachDefaultSystem (system:
    let
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      packages.default = pkgs.stdenv.mkDerivation rec {
        pname = "audio-tools";
        version = "2024-05-21";
        src = ./.;
        buildInputs = with pkgs; [ 
          yt-dlp id3v2 dos2unix imagemagick abcde glyr id3lib lame 
          python3.pkgs.mutagen
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
      devShell = pkgs.mkShell {
        inputsFrom = [ self.packages.${system}.default ];
        buildInputs = with pkgs; [ ];
      };
    }
  );
}
