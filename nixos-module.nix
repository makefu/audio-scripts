# systemd timer service wrapping `podfetch fetch`.
#
# Example consumer flake:
#   inputs.podfetch.url = github:makefu/audio-scripts;
#   inputs.podfetch.inputs.nixpkgs.follows = "nixpkgs";
#   ...
#   services.podfetch = {
#     enable = true;
#     user = "makefu";  # for forced-uid CIFS mounts; default system user otherwise
#     settings = {
#       output_dir = "/media/silent/music/kinder/podcasts";
#       notify.apprise_urls = [ "ntfy://my-topic" ];
#       feeds = [
#         { name = "Die Maus zum Hören"
#           url = "https://kinder.wdr.de/radio/diemaus/audio/diemaus-60/diemaus-60-106.podcast";
#           slug = "die.maus.zum.hoeren"; }
#       ];
#     };
#   };
{ config, pkgs, lib, ... }:
let
  cfg = config.services.podfetch;
in
{
  options.services.podfetch = {
    enable = lib.mkEnableOption "podfetch podcast downloader";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.podfetch;
      defaultText = lib.literalExpression "pkgs.podfetch";
      description = "The podfetch package to run.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "podfetch";
      description = "User running the fetch timer.";
    };

    interval = lib.mkOption {
      type = lib.types.str;
      default = "1h";
      example = "6h";
      description = "OnUnitActiveSec of the fetch timer.";
    };

    settings = lib.mkOption {
      type = lib.types.attrs;
      default = { };
      example = lib.literalExpression ''
        {
          output_dir = "/srv/podcasts";
          notify.apprise_urls = [ "tgram://token/chatid" ];
          feeds = [ { name = "Foo"; url = "https://example.org/feed.xml"; } ];
        }
      '';
      description = "podfetch configuration; same schema as config.yaml.";
    };
  };

  config = lib.mkIf cfg.enable {
    # self-sufficient: consumers do not need to apply overlays.default
    nixpkgs.overlays = [
      (final: prev: {
        # top-level chromaprint (C tool with fpcalc), not the broken
        # python3Packages.chromaprint pyacoustid binding
        podfetch = final.python3Packages.callPackage ./podfetch.nix {
          chromaprint = final.chromaprint;
        };
      })
    ];

    users.users.${cfg.user} = lib.mkIf (cfg.user == "podfetch") {
      isSystemUser = true;
      group = cfg.user;
    };
    users.groups.podfetch = lib.mkIf (cfg.user == "podfetch") { };

    # JSON is a YAML subset; podfetch.config loads it with yaml.safe_load
    environment.etc."podfetch/config.yaml".text = builtins.toJSON (
      { state_dir = "/var/lib/podfetch"; } // cfg.settings
    );

    systemd.services.podfetch = {
      serviceConfig = {
        Type = "oneshot";
        User = cfg.user;
        StateDirectory = "podfetch";
        StateDirectoryMode = "0750";
        NoNewPrivileges = true;
        ExecStart = "${cfg.package}/bin/podfetch fetch --config /etc/podfetch/config.yaml";
      };
    };

    systemd.timers.podfetch = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5m";
        OnUnitActiveSec = cfg.interval;
      };
    };
  };
}
