import os
import time

from conftest import FakeSender

from podfetch.config import Config
from podfetch.notify import Notifier
from podfetch.runner import run_all, run_feed


def make_cfg(tmp_path, fixture_server, apprise_urls="[]", extra_feed_yaml=""):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"""
output_dir: {tmp_path}/out
state_dir: {tmp_path}/state
notify:
  enabled: true
  apprise_urls: {apprise_urls}
feeds:
  - name: Testpodcast
    url: {fixture_server.base_url}/feed.xml
    slug: testpod
{extra_feed_yaml}
""",
        encoding="utf-8",
    )
    return Config.load(p)


def test_full_fetch_contract(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    sender = FakeSender()
    notifier = Notifier(cfg, sender=sender)
    results, aborted = run_all(cfg, notifier)
    assert not aborted
    res = results[0]
    assert not res.errors
    assert len(res.downloaded) == 2

    feed_dir = cfg.output_dir / "testpod"
    f1 = feed_dir / "20240521 Folge Eins.mp3"
    f2 = feed_dir / "20240522 Fölge Zwoä.mp3"
    assert f1.exists() and f2.exists()

    # mtime = publish date at 12:00 local (touch -t YYYYMMDD1200 parity)
    for f, (y, mo, d) in [(f1, (2024, 5, 21)), (f2, (2024, 5, 22))]:
        expected = time.mktime((y, mo, d, 12, 0, 0, 0, 1, -1))
        assert abs(os.stat(f).st_mtime - expected) < 2

    # cover written next to mp3, ≤600px JPEG
    cover = feed_dir / "20240521 Folge Eins.cover.jpg"
    assert cover.exists()
    from PIL import Image

    with Image.open(cover) as im:
        assert im.format == "JPEG" and max(im.size) <= 600

    # archive.txt contains both enclosure URLs as bare ids
    archive = (feed_dir / "archive.txt").read_text(encoding="utf-8").splitlines()
    base = fixture_server.base_url
    assert sorted(archive) == sorted([f"{base}/ep1.mp3", f"{base}/ep2.mp3"])

    # byte-exact latest-episode playlist
    m3u = (feed_dir / "aktuelle_folge.m3u").read_bytes()
    expected = (
        b"#EXTM3U\r\n"
        b"#EXTENC: ISO-8859-1\r\n"
        b"#EXTIMG: 20240522 F\xf6lge Zwo\xe4.cover.jpg\r\n"
        b"#PLAYLIST: 20240522 F\xf6lge Zwo\xe4 | Testpodcast\r\n"
        b"20240522 F\xf6lge Zwo\xe4.mp3\r\n"
    )
    assert m3u == expected

    # ID3 frames present
    from mutagen.id3 import ID3

    tags = ID3(f1)
    assert str(tags["TPE2"].text[0]) == "Testpodcast"
    assert str(tags["TALB"].text[0]) == "Testpodcast"
    assert str(tags["TIT2"].text[0]) == "Folge Eins"
    assert len(tags.getall("APIC")) == 1
    assert tags.getall("APIC")[0].mime == "image/jpeg"
    assert tags.getall("APIC")[0].desc == ""

    # new-episode notifications (2 episodes ≤ max_episode_messages)
    assert sum(1 for t, _ in sender.msgs if t.startswith("podfetch: new episode")) == 2


def test_rerun_downloads_nothing(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    run_all(cfg, Notifier(cfg, sender=FakeSender()))
    results, _ = run_all(cfg, Notifier(cfg, sender=FakeSender()))
    assert sum(len(r.downloaded) for r in results) == 0


def test_existing_file_without_archive_still_skipped(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    run_all(cfg, Notifier(cfg, sender=FakeSender()))
    feed_dir = cfg.output_dir / "testpod"
    # delete one mp3 but keep its archive id → runner must not re-download
    (feed_dir / "20240521 Folge Eins.mp3").unlink()
    results, _ = run_all(cfg, Notifier(cfg, sender=FakeSender()))
    assert sum(len(r.downloaded) for r in results) == 0


def test_403_download_reports_error_event(tmp_path, fixture_server):
    # feed whose enclosure URL answers 403
    fixture_server.bodies["f403.xml"] = (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>FourOThree</title>'
        f"<item><title>blocked</title>"
        f'<enclosure url="{fixture_server.base_url}/forbidden" type="audio/mpeg"/>'
        f"</item></channel></rss>"
    ).encode()
    p = tmp_path / "cfg403.yaml"
    p.write_text(
        f"output_dir: {tmp_path}/out\nfeeds:\n  - name: Blocked\n    url: {fixture_server.base_url}/f403.xml\n    slug: b403\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    res = run_feed(cfg.feeds[0], cfg, Notifier(cfg, sender=FakeSender()))
    kinds = [e.kind for e in res.errors]
    assert "mp3 download failed" in kinds
    assert any("403" in e.detail for e in res.errors)


def test_readonly_dir_permission_event(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    feed_dir = cfg.output_dir / "testpod"
    feed_dir.mkdir(parents=True)
    os.chmod(feed_dir, 0o500)
    try:
        res = run_feed(cfg.feeds[0], cfg, Notifier(cfg, sender=FakeSender()))
        assert res.errors
        perm = [e for e in res.errors if e.kind == "permission denied"]
        assert perm, res.errors
        assert (
            str(feed_dir) in perm[0].detail
            or "/archive.txt" in perm[0].detail
            or ".mp3" in perm[0].detail
        )
    finally:
        os.chmod(feed_dir, 0o700)


def test_dry_run_writes_nothing(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    results, _ = run_all(cfg, Notifier(cfg, sender=FakeSender()), dry_run=True)
    assert sum(len(r.downloaded) for r in results) == 2
    feed_dir = cfg.output_dir / "testpod"
    assert not list(feed_dir.glob("*.mp3"))
    assert not (feed_dir / "archive.txt").exists()


def test_only_filter_by_slug(tmp_path, fixture_server):
    cfg = make_cfg(tmp_path, fixture_server)
    results, _ = run_all(cfg, Notifier(cfg, sender=FakeSender()), only=["nosuch"])
    assert results == []


def test_feed_fetch_failure_event(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        f"output_dir: {tmp_path}/out\nfeeds:\n  - name: Dead\n    url: http://127.0.0.1:1/feed.xml\n    slug: dead\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    sender = FakeSender()
    results, aborted = run_all(cfg, Notifier(cfg, sender=sender))
    assert aborted
    assert results[0].errors[0].kind == "feed fetch failed"
    # error summary was sent
    assert any("problems" in t for t, _ in sender.msgs)
    assert any("feed fetch failed" in b for _, b in sender.msgs)
