"""RSS/Atom feed fetching and episode extraction."""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass
from urllib.parse import urlsplit

import feedparser
import requests

MAX_BODY_BYTES = 8 * 1024 * 1024


class FeedError(Exception):
    """Base class for feed problems; `detail` is human-readable notification text."""

    def __init__(self, url: str, detail: str):
        self.url = url
        self.detail = detail
        super().__init__(f"{url}: {detail}")


class FeedNetworkError(FeedError):
    pass


class FeedParseError(FeedError):
    pass


class FeedEmptyError(FeedError):
    pass


@dataclass(frozen=True)
class Episode:
    id: str
    title: str
    url: str
    date: datetime.datetime
    cover_url: str | None


@dataclass(frozen=True)
class ParsedFeed:
    title: str
    episodes: tuple[Episode, ...]


def _http_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        return urlsplit(url).scheme in ("http", "https")
    except ValueError:
        return False


def fetch_feed(url: str, cfg) -> ParsedFeed:
    """Download and parse a feed. Raises FeedNetworkError / FeedParseError / FeedEmptyError."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.timeout,
            stream=True,
        )
        resp.raise_for_status()
        body = _read_capped(resp)
    except requests.exceptions.RequestException as exc:
        raise FeedNetworkError(url, str(exc)) from exc
    parsed = feedparser.parse(body)
    if not parsed.version:
        exc = parsed.get("bozo_exception")
        raise FeedParseError(url, str(exc) if exc else "not a recognized feed format")
    if parsed.bozo and not parsed.entries:
        raise FeedParseError(url, str(parsed.get("bozo_exception", "unparsable feed")))
    if not parsed.entries:
        raise FeedEmptyError(url, "feed contains no entries")

    feed_title = parsed.feed.get("title", url)
    episodes = []
    for entry in parsed.entries:
        ep = _episode_from_entry(entry, feed_title, parsed)
        if ep is not None:
            episodes.append(ep)
    if not episodes:
        raise FeedEmptyError(url, "feed entries contain no audio enclosures")
    return ParsedFeed(title=feed_title, episodes=tuple(episodes))


def _read_capped(resp: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise FeedParseError(
                resp.url, f"feed body larger than {MAX_BODY_BYTES} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _first_url(candidates: list) -> str | None:
    for c in candidates:
        url = None
        if isinstance(c, dict):
            url = c.get("href") or c.get("url")
        if _http_url(url):
            return url
    return None


def _episode_from_entry(entry, feed_title: str, parsed) -> Episode | None:
    url = _first_url(list(entry.get("enclosures") or []))
    if url is None:
        url = _first_url(list(entry.get("media_content") or []))
    if url is None:
        link = entry.get("link")
        url = link if _http_url(link) else None
    if url is None:
        return None
    title = entry.get("title") or feed_title
    dt = _entry_datetime(entry, parsed)
    cover = _entry_cover(entry, parsed)
    return Episode(id=url, title=str(title), url=url, date=dt, cover_url=cover)


def _entry_datetime(entry, parsed) -> datetime.datetime:
    for key in ("published_parsed", "updated_parsed"):
        v = entry.get(key)
        if v:
            return datetime.datetime.fromtimestamp(
                calendar.timegm(v), tz=datetime.timezone.utc
            )
    for key in ("published_parsed", "updated_parsed"):
        v = parsed.feed.get(key) if "feed" in parsed else None
        if v:
            return datetime.datetime.fromtimestamp(
                calendar.timegm(v), tz=datetime.timezone.utc
            )
    return datetime.datetime.now(datetime.timezone.utc)


def _entry_cover(entry, parsed) -> str | None:
    img = entry.get("itunes_image")
    if isinstance(img, list):
        img = img[0] if img else None
    url = img.get("href") if isinstance(img, dict) else None
    if _http_url(url):
        return url
    img = entry.get("image")
    url = img.get("href") if isinstance(img, dict) else None
    if _http_url(url):
        return url
    if "image" in parsed.feed:
        img = parsed.feed.image
        url = img.get("href") if isinstance(img, dict) else None
        if _http_url(url):
            return url
    return None
