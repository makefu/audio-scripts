# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-09-27

First release of the `podfetch` Python podcast-fetch suite. The RSS-download
pipeline that previously lived in `bin/gendownload.sh` / `bin/alldownload.sh`
(yt-dlp + shell) is replaced by a packaged, config-driven Python CLI. The
legacy scripts in `bin/` keep working as a fallback.

### Added

- **`podfetch` CLI** (`podfetch fetch | smoketest | notify`), YAML-config
  driven (`-c` flag, `$PODFETCH_CONFIG`, or
  `$XDG_CONFIG_HOME/podfetch/config.yaml`); exit codes: 0 ok, 2 feed aborted,
  3 config error, 130 interrupted.
- **Native downloader** (feedparser + requests, no yt-dlp): retries with
  backoff, streamed bodies capped at 8 MiB, atomic `.part` writes, episode
  mtime set to publish date.
- **Byte-compatible on-disk layout** with the shell pipeline, so the existing
  Sonos SMB library and per-feed archives keep working:
  - yt-dlp-compatible `archive.txt` (loads legacy `yt-dlp <id>` lines, writes
    bare enclosure-URL ids),
  - `<YYYYMMDD> <title>.mp3` naming (keeps `sort -n | tail -1` semantics),
  - `<base>.cover.jpg` resized ≤ cover_width,
  - `aktuelle_folge.m3u` written as ISO-8859-1 (transliterated) + CRLF with
    `#EXTENC`/`#EXTIMG`/`#PLAYLIST` headers; optional all-episodes
    `playlist_full` (purplus/genm3u parity).
- **ID3 tagging via mutagen** (replaces `mid3v2`): TPE2/TPE1/TALB = feed name,
  TIT2 = episode title, embedded ≤350 px APIC replaced wholesale; missing
  cover is non-fatal.
- **apprise notifications**: new-episode notices (summarised past
  `max_episode_messages`), single throttled error summary per run
  (`state_dir/notify.json`), first-class `permission denied` events carrying
  the offending path; dead endpoints never crash a fetch run.
- **Offline ad removal** (`podfetch.ads`, fail-open):
  - silence-cluster detector (ffmpeg `silencedetect`, Mindetect-style
    clustering with min-silences/min-break/max-sep, pad-inset), corroborated
    by a loudness-deviation gate over audible-window medians,
  - cross-episode chromaprint fingerprint library (windowed `fpcalc`,
    JSON cache, modal-Δt bucket matching) — cuts repeated ad blocks even
    without silence,
  - opt-in ASR ad-keyword detector (faster-whisper, German + English
    patterns) via `packages.podfetchWithWhisper`,
  - single-graph ffmpeg cut engine (`atrim`/`concat`, 30 ms edge fades,
    libmp3lame), >70 % removal guard raises instead of mutilating files,
  - reports, fingerprint and transcript caches live under `state_dir/ads/`,
    never in the library.
- **`smoketest` subcommand**: PASS/FAIL per check for config load, directory
  writability, every apprise URL (`--send` fires a real notification) and
  every enabled feed; exit 1 on any FAIL.
- **Nix packaging**: `nix build .#podfetch` (and `.#podfetchWithWhisper`);
  ffmpeg/ffprobe/fpcalc baked into the CLI wrapper's PATH; `passthru.tests`
  runs the 65-test pytest suite inside the sandbox; `overlays.default`.
- **NixOS module** `services.podfetch` (`nixosModules.podfetch`): systemd
  oneshot service + timer (`OnUnitActiveSec` = `interval`, default 1 h),
  config rendered to `/etc/podfetch/config.yaml`, `StateDirectory=podfetch`,
  optional custom `user` for forced-uid CIFS mounts; self-sufficient via its
  own package overlay.
- **`examples/config.yaml`**: the 12 live kid feeds from `alldownload.sh`
  plus documented `notify` and `ads` sections.
- Test suite (65 tests) against a local `http.server` fixture, real ffmpeg /
  ffprobe / fpcalc, no network mocks of the download path.

### Unchanged

- `bin/` shell scripts (abcde `doit.sh`, cover helpers, `genm3u.sh`,
  `alldownload.sh`) are untouched and remain the fallback pipeline.

[1.0.0]: https://github.com/makefu/audio-scripts/releases/tag/v1.0.0
