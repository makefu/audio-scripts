# audio-scripts

assorted scripts to manage my audio library, conveniently packaged in nix

## podfetch

Config-driven podcast downloader (`podfetch fetch | smoketest | notify`) with
Sonos-compatible ISO-8859-1/CRLF playlists, yt-dlp-compatible `archive.txt`,
apprise notifications and offline ad removal (silence-cluster + fingerprint +
optional ASR detection, ffmpeg cut engine). The legacy shell scripts in `bin/`
still work.

```console
$ nix build .#podfetch            # or .#podfetchWithWhisper for the ASR detector
$ ./result/bin/podfetch --help
$ ./result/bin/podfetch smoketest -c examples/config.yaml
```

Config resolution: `-c/--config` → `$PODFETCH_CONFIG` →
`$XDG_CONFIG_HOME/podfetch/config.yaml` → `~/.config/podfetch/config.yaml`.
`examples/config.yaml` documents every key (feeds, notify, ads).

## NixOS setup

The flake ships a module (`services.podfetch`) that installs a systemd timer
running `podfetch fetch` on an interval. It is self-sufficient: applying the
input is enough, no overlay needed on the consumer side.

### Flake input

```nix
# flake.nix of your NixOS config
{
  inputs.podfetch.url = "github:makefu/audio-scripts";
  inputs.podfetch.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { self, nixpkgs, podfetch, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./configuration.nix
        podfetch.nixosModules.podfetch
      ];
    };
  };
}
```

Without flakes: `nix-channel --add https://github.com/makefu/audio-scripts/archive/main.tar.gz podfetch`
and `imports = [ <podfetch/nixos-module.nix> ];`.

### Minimal service

`settings` is the same schema as `examples/config.yaml` (attribute set
instead of YAML):

```nix
services.podfetch = {
  enable = true;
  settings = {
    output_dir = "/var/lib/sonos/podcasts";
    notify.apprise_urls = [ "ntfy://my-secret-topic" ];
    feeds = [
      {
        name = "Die Maus zum Hören";
        url = "https://kinder.wdr.de/radio/diemaus/audio/diemaus-60/diemaus-60-106.podcast";
        slug = "die.maus.zum.hoeren";
      }
      {
        name = "Was ist Was Podcast";
        url = "https://feeds.megaphone.fm/KBBF5520541713";
        cut_ads = true;   # megaphone bakes ads into the mp3
      }
    ];
  };
};
```

### What the module wires up

| piece | value |
|---|---|
| config file | `/etc/podfetch/config.yaml` — `builtins.toJSON settings`, readable by any user |
| `state_dir` | `/var/lib/podfetch` (default-merged unless `settings` sets it); holds notify throttle state, ad-cut reports, fingerprint + transcript caches |
| service | `systemd.services.podfetch`: `Type=oneshot`, `User=cfg.user`, `StateDirectory=podfetch` (mode 0750), `NoNewPrivileges`, `ExecStart=<pkg>/bin/podfetch fetch --config /etc/podfetch/config.yaml` |
| timer | `OnBootSec=5m`, `OnUnitActiveSec=cfg.interval` (default `1h`) |
| user/group | `podfetch` system user + group, created only while `cfg.user == "podfetch"` |
| package | `pkgs.podfetch` via the module's own overlay (ffmpeg/ffprobe/fpcalc baked into the wrapper's PATH) |

### Options

- `services.podfetch.enable` — activate everything above.
- `services.podfetch.package` — defaults to `pkgs.podfetch`; set to
  `inputs.podfetch.packages.${pkgs.system}.podfetchWithWhisper` to enable the
  ASR ad-keyword detector (pair with `settings.ads.asr = true;`).
- `services.podfetch.user` — user running the fetch (default `podfetch`).
- `services.podfetch.interval` — `OnUnitActiveSec` of the timer (default `1h`).
- `services.podfetch.settings` — full podfetch config as an attribute set;
  keys mirror `examples/config.yaml` (`output_dir`, `state_dir`, `timeout`,
  `retries`, `notify.*`, `ads.*`, `feeds[]`).

### SMB/CIFS library share

The Sonos library usually lives on a forced-uid CIFS mount, which only that
uid may write. Point the service at your own user (then no `podfetch`
user/group is created) and make sure the mount allows it:

```nix
services.podfetch = {
  enable = true;
  user = "makefu"; # forced-uid CIFS mounts: fetch must run as that uid
  settings.output_dir = "/media/silent/music/kinder/podcasts";
};
```

### Ad removal on NixOS

`ads` knobs pass straight through `settings`. The package already wraps
ffmpeg + chromaprint onto the CLI's PATH, so no system packages needed:

```nix
services.podfetch.settings.ads = {
  enabled = true;
  fingerprint = true;   # cross-episode chromaprint library
  asr = false;          # needs podfetchWithWhisper + python deps
  cluster_min_break_s = 20;
};
```

### Verify after activation

```console
$ nix build ".#podfetch" -o /tmp/pf && /tmp/pf/bin/podfetch smoketest -c /etc/podfetch/config.yaml
$ systemctl list-timers podfetch.timer
$ sudo systemctl start podfetch.service && journalctl -u podfetch.service -n 50
$ ls /etc/podfetch/config.yaml /var/lib/podfetch
```

`smoketest` checks config load, dir writability, every apprise URL, and every
enabled feed; prints `PASS/FAIL` per check, exit 1 on any FAIL.
