from conftest import (
    FakeSender,  # noqa: F401  (import parity; not used for apprise tests)
)

from podfetch.cli import main


def write_cfg(tmp_path, body: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_fetch_exit_0_and_2(tmp_path, fixture_server, capsys):
    good = f"{fixture_server.base_url}/feed.xml"
    dead = "http://127.0.0.1:1/feed.xml"
    cfg_ok = write_cfg(
        tmp_path,
        f"output_dir: {tmp_path}/out\nfeeds:\n  - name: Good\n    url: {good}\n    slug: g\n",
    )
    assert main(["fetch", "-c", cfg_ok, "--no-notify"]) == 0

    cfg_bad = write_cfg(
        tmp_path,
        f"output_dir: {tmp_path}/out2\nfeeds:\n  - name: Good\n    url: {good}\n    slug: g\n  - name: Dead\n    url: {dead}\n    slug: d\n",
    )
    assert main(["fetch", "-c", cfg_bad, "--no-notify"]) == 2


def test_fetch_config_error_exit_3(tmp_path, capsys):
    p = write_cfg(tmp_path, "feeds: []\n")
    assert main(["fetch", "-c", p]) == 3
    assert "output_dir" in capsys.readouterr().err


def test_smoketest_broken_config_exit_1(tmp_path, capsys):
    p = write_cfg(tmp_path, "output_dir: /x\nfeeds: notalist\n")
    rc = main(["smoketest", "-c", p])
    assert rc == 1
    err = capsys.readouterr().err
    assert "feeds" in err


def test_smoketest_passes_good_config(tmp_path, fixture_server, capsys):
    p = write_cfg(
        tmp_path,
        f"output_dir: {tmp_path}/out\nstate_dir: {tmp_path}/state\nfeeds:\n  - name: T\n    url: {fixture_server.base_url}/feed.xml\n    slug: t\n",
    )
    rc = main(["smoketest", "-c", p])
    out = capsys.readouterr().out
    assert rc == 0
    assert "smoketest: " in out and "0 failed" in out
    assert any(l.startswith("PASS feed t") for l in out.splitlines())


def test_smoketest_send_posts_to_local_server(tmp_path, fixture_server, capsys):
    base = fixture_server.base_url
    p = write_cfg(
        tmp_path,
        f'output_dir: {tmp_path}/out\nstate_dir: {tmp_path}/state\nnotify:\n  enabled: true\n  apprise_urls: ["json://{base.split("//")[1]}/captured"]\nfeeds:\n  - name: T\n    url: {base}/feed.xml\n    slug: t\n',
    )
    fixture_server.captured.clear()
    rc = main(["smoketest", "-c", p, "--send"])
    out = capsys.readouterr().out
    assert rc == 0, out
    posts = [c for c in fixture_server.captured if c[0] == "/captured"]
    assert posts, "no POST captured"
    payload = posts[-1][2].decode("utf-8")
    assert "smoketest" in payload


def test_notify_subcommand_sends(tmp_path, fixture_server, capsys):
    base = fixture_server.base_url.split("//")[1]
    p = write_cfg(
        tmp_path,
        f'output_dir: {tmp_path}/out\nnotify:\n  enabled: true\n  apprise_urls: ["json://{base}/captured"]\nfeeds:\n  - name: T\n    url: {fixture_server.base_url}/feed.xml\n    slug: t\n',
    )
    fixture_server.captured.clear()
    rc = main(["notify", "-c", p, "-t", "Titel", "-b", "Nachricht"])
    assert rc == 0
    payload = fixture_server.captured[-1][2].decode("utf-8")
    assert "Nachricht" in payload and "Titel" in payload
