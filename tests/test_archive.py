from podfetch.download import Archive


def test_legacy_yt_dlp_prefix_stripped(tmp_path):
    d = tmp_path / "feed"
    d.mkdir()
    (d / "archive.txt").write_text(
        "yt-dlp https://cdn.example/ep1.mp3\nyt-dlp ep2\nhttps://cdn.example/ep3.mp3\n",
        encoding="utf-8",
    )
    a = Archive(d)
    a.load()
    assert a.contains("https://cdn.example/ep1.mp3")
    assert a.contains("ep2")
    assert a.contains("https://cdn.example/ep3.mp3")


def test_store_writes_bare_ids(tmp_path):
    d = tmp_path / "feed"
    d.mkdir()
    a = Archive(d)
    a.load()
    a.add("https://cdn.example/ep9.mp3")
    a.add("https://cdn.example/ep9.mp3")  # idempotent
    lines = (d / "archive.txt").read_text(encoding="utf-8").splitlines()
    assert lines == ["https://cdn.example/ep9.mp3"]


def test_missing_archive_tolerated(tmp_path):
    d = tmp_path / "feed"
    d.mkdir()
    a = Archive(d)
    a.load()
    assert not a.contains("x")


def test_corrupt_archive_tolerated(tmp_path):
    d = tmp_path / "feed"
    d.mkdir()
    (d / "archive.txt").write_text(
        "\x00\x01garbage\n\n  \nytdlp-ish x\n", encoding="utf-8", errors="replace"
    )
    a = Archive(d)
    a.load()  # must not raise
    assert a.contains("ytdlp-ish x")
