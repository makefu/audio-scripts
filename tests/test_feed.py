import datetime

import pytest

from podfetch.config import Config
from podfetch.feed import (
    FeedEmptyError,
    FeedNetworkError,
    FeedParseError,
    fetch_feed,
)


@pytest.fixture()
def cfg(fixture_server, tmp_path):
    return _write_cfg(tmp_path, fixture_server)


def _write_cfg(tmp_path, fixture_server, feed_url=None):
    p = tmp_path / "config.yaml"
    url = feed_url or f"{fixture_server.base_url}/feed.xml"
    p.write_text(
        f"output_dir: {tmp_path}/out\nfeeds:\n  - name: Test\n    url: {url}\n",
        encoding="utf-8",
    )
    return Config.load(p)


def test_fixture_feed_parses(cfg, fixture_server):
    parsed = fetch_feed(cfg.feeds[0].url, cfg)
    assert parsed.title == "Testpodcast"
    assert len(parsed.episodes) == 2
    ep1, ep2 = parsed.episodes
    base = fixture_server.base_url
    assert ep1.id == f"{base}/ep1.mp3"
    assert ep1.url == f"{base}/ep1.mp3"
    assert ep1.title == "Folge Eins"
    assert ep1.date == datetime.datetime(
        2024, 5, 21, 8, 0, tzinfo=datetime.timezone.utc
    )
    assert ep1.cover_url == f"{base}/cover.png"
    assert ep2.id == f"{base}/ep2.mp3"
    # entry without itunes:image falls back to feed-level image
    assert ep2.cover_url == f"{base}/cover.png"
    assert ep2.title == "Fölge Zwoä"


def test_404_is_network_error(fixture_server, tmp_path):
    cfg = _write_cfg(tmp_path, fixture_server, f"{fixture_server.base_url}/missing.xml")
    with pytest.raises(FeedNetworkError) as ei:
        fetch_feed(cfg.feeds[0].url, cfg)
    assert "404" in str(ei.value)


def test_html_body_is_parse_error(fixture_server, tmp_path):
    fixture_server.bodies["html.xml"] = b"<html><body>not a feed</body></html>"
    cfg = _write_cfg(tmp_path, fixture_server, f"{fixture_server.base_url}/html.xml")
    with pytest.raises(FeedParseError):
        fetch_feed(cfg.feeds[0].url, cfg)


def test_empty_entries_is_empty_error(fixture_server, tmp_path):
    fixture_server.bodies["empty.xml"] = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>E</title></channel></rss>'
    )
    cfg = _write_cfg(tmp_path, fixture_server, f"{fixture_server.base_url}/empty.xml")
    with pytest.raises(FeedEmptyError):
        fetch_feed(cfg.feeds[0].url, cfg)


def test_entries_without_enclosure_is_empty_error(fixture_server, tmp_path):
    fixture_server.bodies["noenc.xml"] = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>E</title>'
        b"<item><title>only text</title></item></channel></rss>"
    )
    cfg = _write_cfg(tmp_path, fixture_server, f"{fixture_server.base_url}/noenc.xml")
    with pytest.raises(FeedEmptyError):
        fetch_feed(cfg.feeds[0].url, cfg)


def test_unreachable_host_is_network_error(tmp_path):
    cfg = _write_cfg(tmp_path, tmp_path, "http://127.0.0.1:1/feed.xml")
    with pytest.raises(FeedNetworkError):
        fetch_feed(cfg.feeds[0].url, cfg)
