{ lib
, buildPythonPackage
, python3
, python3Packages
, feedparser
, mutagen
, requests
, pyyaml
, pillow
, pytestCheckHook
, ffmpeg
, chromaprint
, makeWrapper
, withWhisper ? false
}:
let
  # nixpkgs ~24.05 only exposes apprise as a top-level toPythonApplication CLI;
  # build the importable library from the python-modules tree when missing.
  apprise =
    python3Packages.aprise
    or python3Packages.callPackage (python3Packages.pkgs.path + /pkgs/development/python-modules/apprise) { };

  podfetch = buildPythonPackage {
    pname = "podfetch";
    version = "1.0.0";
    pyproject = true;

    src = lib.sourceByRegex ./. [
      "^podfetch(/.*)?$"
      "^pyproject\.toml$"
      "^tests(/.*)?$"
    ];

    build-system = [ python3Packages.setuptools ];

    propagatedBuildInputs =
      [ apprise feedparser mutagen requests pyyaml pillow ]
      ++ lib.optionals withWhisper [ python3Packages.faster-whisper ];

    # ads.py shells out to ffmpeg/ffprobe/fpcalc; put them on the CLI's PATH
    buildInputs = [ ffmpeg chromaprint ];
    nativeBuildInputs = [ makeWrapper ];
    postInstall = ''
      wrapProgram $out/bin/podfetch --prefix PATH : ${lib.makeBinPath [ ffmpeg chromaprint ]}
    '';

    nativeCheckInputs = [ pytestCheckHook ffmpeg chromaprint ];

    pythonImportsCheck = [ "podfetch" ];

    passthru.tests = python3Packages.pkgs.runCommand "podfetch-pytest" {
      nativeBuildInputs = [
        (python3.withPackages (ps: [ podfetch ps.pytest ]))
        ffmpeg
        chromaprint
      ];
    } ''
      pytest ${podfetch.src}/tests -q
      touch $out
    '';

    meta = with lib; {
      description = "Podcast downloader with Sonos playlists and apprise notifications";
      homepage = "https://github.com/makefu/audio-scripts";
      license = licenses.mit;
      maintainers = with maintainers; [ ];
    };
  };
in
podfetch
