"""podfetch command line interface."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

from . import ads as ads_mod
from .ads import AdCutError
from .config import Config, ConfigError
from .feed import FeedError, fetch_feed
from .notify import Notifier
from .runner import run_all


def default_config_path() -> Path | None:
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    for base in (xdg,):
        p = Path(base) / "podfetch" / "config.yaml"
        if p.exists():
            return p
    return None


def resolve_config_path(args) -> Path:
    if args.config:
        return Path(args.config)
    env = os.environ.get("PODFETCH_CONFIG")
    if env:
        return Path(env)
    p = default_config_path()
    if p is not None:
        return p
    raise ConfigError(
        [
            (
                "no config file: pass -c, set $PODFETCH_CONFIG, "
                "or create $XDG_CONFIG_HOME/podfetch/config.yaml"
            )
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="podfetch", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="download new episodes")
    f.add_argument("-c", "--config", help="path to config.yaml")
    f.add_argument(
        "--feed",
        action="append",
        metavar="NAME",
        help="only this feed name or slug (repeatable)",
    )
    f.add_argument(
        "--dry-run", action="store_true", help="report what would be downloaded"
    )
    f.add_argument(
        "--no-notify", action="store_true", help="suppress apprise notifications"
    )
    f.add_argument("--verbose", action="store_true", help="per-episode logging")
    f.add_argument(
        "--cut-ads", action="store_true", help="force ad cutting for this run"
    )
    f.add_argument("--no-cut-ads", action="store_true", help="never cut ads this run")

    s = sub.add_parser("smoketest", help="check config, dirs, apprise URLs and feeds")
    s.add_argument("-c", "--config")
    s.add_argument(
        "--send", action="store_true", help="actually send a test notification"
    )

    n = sub.add_parser("notify", help="send a manual notification")
    n.add_argument("-c", "--config")
    n.add_argument("-t", "--title", required=True)
    n.add_argument("-b", "--body", required=True)
    return ap


def cmd_fetch(args, cfg: Config) -> int:
    notifier = None if args.no_notify else Notifier(cfg)
    if args.cut_ads and args.no_cut_ads:
        print(
            "podfetch fetch: --cut-ads and --no-cut-ads are exclusive", file=sys.stderr
        )
        return 3
    results, any_aborted = run_all(
        cfg,
        notifier or _NullNotifier(),
        only=args.feed,
        dry_run=args.dry_run,
        cut_ads=True if args.cut_ads else (False if args.no_cut_ads else None),
    )
    total = sum(len(r.downloaded) for r in results)
    for r in results:
        prefix = "would download" if args.dry_run else "downloaded"
        if args.verbose or args.dry_run:
            for ep in r.downloaded:
                print(f"{prefix}: {r.feed.slug}/{ep.title}")
        for ev in r.errors:
            print(f"[!] {r.feed.name}: {ev.kind} ({ev.detail})", file=sys.stderr)
    cuts = sum(1 for r in results for c in r.ad_cuts if c.get("cut"))
    seconds = sum(
        c.get("removed_seconds", 0) for r in results for c in r.ad_cuts if c.get("cut")
    )
    if cuts:
        print(f"{cuts} episodes ad-cut, {seconds:.0f}s removed")
    elif args.cut_ads or cfg.ads.enabled:
        print("ads: no spans detected")
    print(f"{total} downloads")
    if notifier is not None:
        # errors were already routed per-feed inside run_all
        pass
    return 2 if any_aborted else 0


class _NullNotifier:
    enabled = False

    def new_episode(self, *a):
        pass

    def new_episode_summary(self, *a):
        pass

    def errors(self, events):
        pass


def cmd_smoketest(args, cfg: Config) -> int:
    passed = 0
    failed = 0

    def check(name: str, ok: bool, detail: str = "") -> bool:
        nonlocal passed, failed
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        line = f"{status} {name}"
        if detail:
            line += f": {detail}"
        print(line)
        return ok

    # 1. config loaded (we are here), feeds enabled
    feeds = [f for f in cfg.feeds if f.enabled]
    check("config", True, f"{len(feeds)} enabled feeds")

    # 2. writable output_dir + feed dirs + state_dir
    for label, d in (
        [("output_dir", cfg.output_dir)]
        + [(f"feed_dir[{f.slug}]", cfg.output_dir / f.slug) for f in feeds]
        + [("state_dir", cfg.state_dir)]
    ):
        ok, detail = _writable(d)
        check(f"writable {label}", ok, detail)

    # 3. ad-cutting binaries (only when configured)
    if cfg.ads.enabled or any(f.cut_ads for f in feeds):
        for label, resolver in (
            ("ads ffmpeg", lambda: ads_mod.ffmpeg_bin(cfg.ads)),
            ("ads fpcalc", lambda: ads_mod.fpcalc_bin(cfg.ads)),
        ):
            try:
                b = resolver()
                check(label, True, b)
            except AdCutError as exc:
                check(label, False, str(exc))

    # 3. apprise URLs
    import apprise

    ap = apprise.Apprise()
    accepted = 0
    for i, url in enumerate(cfg.notify.apprise_urls):
        ok = ap.add(url)
        check(f"apprise_urls[{i}]", ok, "accepted" if ok else "invalid URL")
        if ok:
            accepted += 1
    if args.send:
        if accepted == 0:
            check("apprise send", False, "no service accepted")
        else:
            body = f"podfetch smoketest {socket.gethostname()} {int(time.time())}"
            sent = ap.notify(title="podfetch smoketest", body=body)
            check("apprise send", bool(sent), f"notify() -> {sent!r}")

    # 4. feed fetchability
    for f in feeds:
        try:
            parsed = fetch_feed(f.url, cfg)
            enclosures = len(parsed.episodes)
            ok = enclosures > 0
            check(f"feed {f.slug}", ok, f"{len(parsed.episodes)} entries")
        except FeedError as exc:
            check(f"feed {f.slug}", False, exc.detail)

    print(f"smoketest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def _writable(d: Path) -> tuple[bool, str]:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".podfetch-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True, str(d)
    except PermissionError as exc:
        return False, f"{d}: permission denied ({exc.strerror})"
    except OSError as exc:
        return False, f"{d}: {exc}"


def cmd_notify(args, cfg: Config) -> int:
    notifier = Notifier(cfg)
    if not notifier.enabled:
        print("podfetch notify: no apprise URLs configured", file=sys.stderr)
        return 1
    return 0 if notifier.test(args.body, title=args.title) else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        path = resolve_config_path(args)
        cfg = Config.load(path)
    except ConfigError as exc:
        for err in exc.errors:
            print(f"config error: {err}", file=sys.stderr)
        if args.command == "smoketest":
            # check 1: config load failures are FAILs, not aborts
            for err in exc.errors:
                print(f"FAIL config: {err}")
            print(f"smoketest: 0 passed, {max(1, len(exc.errors))} failed")
            return 1
        return 3
    try:
        if args.command == "fetch":
            return cmd_fetch(args, cfg)
        if args.command == "smoketest":
            return cmd_smoketest(args, cfg)
        if args.command == "notify":
            return cmd_notify(args, cfg)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
