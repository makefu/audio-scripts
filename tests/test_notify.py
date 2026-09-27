from conftest import FakeSender

from podfetch.cli import main
from podfetch.config import Config
from podfetch.notify import PERMISSION_DENIED, ErrorEvent, Notifier


def make_cfg(tmp_path, notify_yaml=""):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"""
output_dir: {tmp_path}/out
state_dir: {tmp_path}/state
notify:
  enabled: true
  apprise_urls: []
  {notify_yaml}
feeds:
  - name: Test
    url: https://example.com/feed.xml
""",
        encoding="utf-8",
    )
    return Config.load(p)


def test_error_throttle_single_send(tmp_path):
    cfg = make_cfg(tmp_path)
    sender = FakeSender()
    n = Notifier(cfg, sender=sender)
    ev = ErrorEvent("Feed X", PERMISSION_DENIED, "/x/y: Permission denied")
    n.errors([ev])
    n.errors([ev])
    assert len(sender.msgs) == 1
    title, body = sender.msgs[0]
    assert title == "podfetch: 1 problems"
    assert "permission denied" in body and "/x/y" in body


def test_distinct_events_both_reported(tmp_path):
    cfg = make_cfg(tmp_path)
    sender = FakeSender()
    n = Notifier(cfg, sender=sender)
    n.errors(
        [
            ErrorEvent("A", "feed fetch failed", "boom"),
            ErrorEvent("B", "tag failed", "meh"),
        ]
    )
    assert len(sender.msgs) == 1
    _, body = sender.msgs[0]
    assert body.count("\n") == 1  # one line per event
    assert "feed fetch failed" in body and "tag failed" in body


def test_disabled_notify_zero_sends(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"output_dir: {tmp_path}/out\nnotify:\n  enabled: false\nfeeds:\n  - name: T\n    url: https://x/y\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    sender = FakeSender()
    # enabled=false must suppress even config-driven sends when sender not explicit
    n2 = Notifier(cfg)
    assert not n2.enabled
    n2.errors([ErrorEvent("A", "tag failed", "x")])
    assert sender.msgs == []


def test_no_urls_means_disabled(tmp_path):
    cfg = make_cfg(tmp_path)
    n = Notifier(cfg)
    assert not n.enabled


def test_invalid_apprise_url_smoketest_fail(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text(
        f"""
output_dir: {tmp_path}/out
state_dir: {tmp_path}/state
notify:
  enabled: true
  apprise_urls: ["notascheme://x"]
feeds:
  - name: T
    url: https://example.com/feed.xml
""",
        encoding="utf-8",
    )
    rc = main(["smoketest", "-c", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert any(line.startswith("FAIL apprise_urls[0]") for line in out.splitlines())
