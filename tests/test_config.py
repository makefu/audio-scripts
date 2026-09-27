from pathlib import Path

import pytest

from podfetch.config import Config, ConfigError, slugify


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def load_ok(tmp_path, text):
    return Config.load(write(tmp_path, text))


MINIMAL = """
output_dir: /srv/podcasts
feeds:
  - name: Foo
    url: https://example.com/feed.xml
"""


def test_minimal_loads(tmp_path):
    cfg = load_ok(tmp_path, MINIMAL)
    assert cfg.output_dir == Path("/srv/podcasts")
    assert cfg.feeds[0].slug == "foo"
    assert cfg.feeds[0].playlist_latest == "aktuelle_folge.m3u"
    assert cfg.retries == 2


def test_missing_output_dir_names_key(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_ok(tmp_path, "feeds:\n  - name: a\n    url: https://x/y\n")
    assert any("output_dir" in e for e in ei.value.errors)


def test_non_list_feeds(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_ok(tmp_path, "output_dir: /x\nfeeds: nother\n")
    assert any("feeds" in e for e in ei.value.errors)


def test_missing_feed_name_names_path(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_ok(
            tmp_path,
            "output_dir: /x\nfeeds:\n  - url: https://a/b\n  - name: ok\n    url: https://c/d\n",
        )
    assert any("feeds[0].name" in e for e in ei.value.errors)


def test_ftp_url_rejected(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_ok(
            tmp_path, "output_dir: /x\nfeeds:\n  - name: a\n    url: ftp://host/f.xml\n"
        )
    assert any("feeds[0].url" in e for e in ei.value.errors)


def test_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = load_ok(
        tmp_path,
        "output_dir: '~/podcasts'\nfeeds:\n  - name: a\n    url: https://a/b\n",
    )
    assert cfg.output_dir == tmp_path / "home" / "podcasts"
    assert cfg.state_dir.is_absolute()


def test_relative_output_resolved_against_config_dir(tmp_path):
    cfg = load_ok(
        tmp_path, "output_dir: ./podcasts\nfeeds:\n  - name: a\n    url: https://a/b\n"
    )
    assert cfg.output_dir == tmp_path / "podcasts"


def test_default_slug_matches_existing_dirs():
    assert slugify("Die Maus zum Hören") == "die.maus.zum.hoeren"
    assert slugify("Anna und die wilden Tiere") == "anna.und.die.wilden.tiere"
    assert slugify("Eric erforscht...") == "eric.erforscht"
    assert slugify("Was ist Was Podcast") == "was.ist.was.podcast"


def test_notify_section_validated(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_ok(tmp_path, MINIMAL + "\nnotify:\n  error_throttle_minutes: -5\n")
    assert any("notify.error_throttle_minutes" in e for e in ei.value.errors)
    with pytest.raises(ConfigError) as ei:
        load_ok(tmp_path, MINIMAL + "\nnotify:\n  enabled: yes-but\n")
    assert any("notify.enabled" in e for e in ei.value.errors)


def test_feed_optional_fields_defaults(tmp_path):
    cfg = load_ok(tmp_path, MINIMAL)
    f = cfg.feeds[0]
    assert f.cover_width == 600 and f.embed_width == 350
    assert f.max_episodes is None and f.playlist_full is None and f.enabled
