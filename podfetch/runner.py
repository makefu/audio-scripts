"""Per-feed orchestration: fetch → download → tag → playlist, with error events."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import ads, tags
from .config import Config, FeedConfig
from .download import (
    Archive,
    DownloadError,
    DownloadHTTPError,
    PermissionDeniedError,
    download_file,
)
from .feed import Episode, FeedError, fetch_feed
from .notify import (
    AD_CUT_FAILED,
    COVER_FAILED,
    FEED_FETCH_FAILED,
    MP3_DOWNLOAD_FAILED,
    PERMISSION_DENIED,
    TAG_FAILED,
    ErrorEvent,
)
from .sonos import sanitize_component, translit, write_m3u


@dataclass
class FeedResult:
    feed: FeedConfig
    downloaded: list[Episode] = field(default_factory=list)
    errors: list[ErrorEvent] = field(default_factory=list)
    ad_cuts: list[dict] = field(default_factory=list)
    aborted: bool = False


def episode_filename(ep: Episode) -> str:
    """`<YYYYMMDD> <title>.mp3`, transliterated + sanitized (gendownload.sh parity)."""
    base = f"{ep.date:%Y%m%d} {ep.title}"
    return sanitize_component(translit(base)) + ".mp3"


def run_feed(
    feed: FeedConfig,
    cfg: Config,
    notifier,
    session: requests.Session | None = None,
    dry_run: bool = False,
    cut_ads: bool | None = None,
) -> FeedResult:
    result = FeedResult(feed=feed)
    sess = session or requests.Session()
    feed_dir = cfg.output_dir / feed.slug
    try:
        os.makedirs(feed_dir, exist_ok=True)
    except OSError as exc:
        result.errors.append(
            ErrorEvent(
                feed.name, PERMISSION_DENIED, f"{feed_dir}: {exc.strerror or exc}"
            )
        )
        result.aborted = True
        return result

    try:
        parsed = fetch_feed(feed.url, cfg)
    except FeedError as exc:
        result.errors.append(ErrorEvent(feed.name, FEED_FETCH_FAILED, exc.detail))
        result.aborted = True
        return result

    archive = Archive(feed_dir)
    try:
        archive.load()
    except PermissionDeniedError as exc:
        result.errors.append(ErrorEvent(feed.name, PERMISSION_DENIED, str(exc)))
        result.aborted = True
        return result

    pending = [ep for ep in parsed.episodes if not archive.contains(ep.id)]
    if feed.max_episodes is not None:
        pending = pending[: feed.max_episodes]

    for ep in pending:
        file_name = episode_filename(ep)
        dest = feed_dir / file_name
        if dest.exists():
            # already on disk but not archived: mark archived, don't re-download
            if not dry_run:
                try:
                    archive.add(ep.id)
                except PermissionDeniedError as exc:
                    result.errors.append(
                        ErrorEvent(feed.name, PERMISSION_DENIED, str(exc))
                    )
            continue
        if dry_run:
            result.downloaded.append(ep)
            continue
        try:
            download_file(ep.url, dest, cfg, session=sess)
        except PermissionDeniedError as exc:
            result.errors.append(ErrorEvent(feed.name, PERMISSION_DENIED, str(exc)))
            continue
        except DownloadHTTPError as exc:
            result.errors.append(
                ErrorEvent(
                    feed.name, MP3_DOWNLOAD_FAILED, f"{exc.url}: HTTP {exc.status}"
                )
            )
            continue
        except DownloadError as exc:
            result.errors.append(ErrorEvent(feed.name, MP3_DOWNLOAD_FAILED, str(exc)))
            continue

        # ad cut: non-fatal; runs BEFORE tagging because an ffmpeg rewrap of a
        # mutagen-tagged file would fight the wholesale ID3 rewrite in tags.py
        enabled = cfg.ads.enabled if feed.cut_ads is None else feed.cut_ads
        if cut_ads is not None:
            enabled = cut_ads
        if enabled:
            try:
                report = ads.process_episode(dest, cfg.ads, feed.slug, feed_dir)
                if report is not None:
                    result.ad_cuts.append(report)
            except PermissionDeniedError as exc:
                result.errors.append(ErrorEvent(feed.name, PERMISSION_DENIED, str(exc)))
            except ads.AdCutError as exc:
                result.errors.append(ErrorEvent(feed.name, AD_CUT_FAILED, str(exc)))

        # tag + cover: non-fatal, episode still counts as downloaded
        try:
            tags.process_episode(
                dest,
                ep,
                feed.name,
                feed.cover_width,
                feed.embed_width,
                ep.cover_url,
                sess,
                timeout=cfg.timeout,
            )
        except tags.CoverError as exc:
            result.errors.append(ErrorEvent(feed.name, COVER_FAILED, str(exc)))
        except Exception as exc:  # noqa: BLE001 - tag failure is non-fatal
            result.errors.append(ErrorEvent(feed.name, TAG_FAILED, str(exc)))

        # mtime = publish date at 12:00 local, parity with `touch -t ${date}1200`
        naive = ep.date.replace(tzinfo=None)
        ts = time.mktime((naive.year, naive.month, naive.day, 12, 0, 0, 0, 1, -1))
        os.utime(dest, (ts, ts))
        try:
            archive.add(ep.id)
        except PermissionDeniedError as exc:
            result.errors.append(ErrorEvent(feed.name, PERMISSION_DENIED, str(exc)))
        result.downloaded.append(ep)

    if not dry_run:
        _write_playlists(feed, feed_dir, result)
    return result


def _write_playlists(feed: FeedConfig, feed_dir: Path, result: FeedResult) -> None:
    mp3s = sorted(p.name for p in feed_dir.glob("*.mp3"))
    if not mp3s:
        return
    if feed.playlist_latest:
        latest = mp3s[-1]
        stem = latest[: -len(".mp3")]
        image = (
            f"{stem}.cover.jpg" if (feed_dir / f"{stem}.cover.jpg").exists() else None
        )
        try:
            write_m3u(
                feed_dir / feed.playlist_latest,
                f"{stem} | {feed.name}",
                [latest],
                image=image,
            )
        except OSError as exc:
            result.errors.append(
                ErrorEvent(
                    feed.name, "playlist failed", f"{feed.playlist_latest}: {exc}"
                )
            )
    if feed.playlist_full:
        stem = mp3s[-1][: -len(".mp3")]
        image = (
            f"{stem}.cover.jpg" if (feed_dir / f"{stem}.cover.jpg").exists() else None
        )
        try:
            write_m3u(feed_dir / feed.playlist_full, feed.name, mp3s, image=image)
        except OSError as exc:
            result.errors.append(
                ErrorEvent(feed.name, "playlist failed", f"{feed.playlist_full}: {exc}")
            )


def run_all(
    cfg: Config,
    notifier,
    only: list[str] | None = None,
    dry_run: bool = False,
    cut_ads: bool | None = None,
):
    """Run enabled feeds (optionally filtered by name/slug). Returns (results, any_aborted)."""
    session = requests.Session()
    results: list[FeedResult] = []
    for feed in cfg.feeds:
        if not feed.enabled:
            continue
        if only and feed.name not in only and feed.slug not in only:
            continue
        res = run_feed(
            feed, cfg, notifier, session=session, dry_run=dry_run, cut_ads=cut_ads
        )
        results.append(res)
        if notifier.enabled and cfg.notify.new_episodes:
            n = len(res.downloaded)
            if n > cfg.notify.max_episode_messages:
                notifier.new_episode_summary(feed.name, n)
            else:
                for ep in res.downloaded:
                    notifier.new_episode(
                        feed.name, ep.title, f"{feed.slug}/{episode_filename(ep)}"
                    )
        if res.errors and notifier.enabled and cfg.notify.errors:
            notifier.errors(res.errors)
    return results, any(r.aborted for r in results)
