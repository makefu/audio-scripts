from podfetch.sonos import sanitize_component, translit, write_m3u


def test_m3u_bytes_exact(tmp_path):
    p = tmp_path / "aktuelle_folge.m3u"
    write_m3u(
        p,
        "20240521 Föö | Die Maus",
        ["20240521 Föö.mp3"],
        image="20240521 Föö.cover.jpg",
    )
    expected = (
        b"#EXTM3U\r\n"
        b"#EXTENC: ISO-8859-1\r\n"
        b"#EXTIMG: 20240521 F\xf6\xf6.cover.jpg\r\n"
        b"#PLAYLIST: 20240521 F\xf6\xf6 | Die Maus\r\n"
        b"20240521 F\xf6\xf6.mp3\r\n"
    )
    assert p.read_bytes() == expected


def test_m3u_no_image_line_omitted(tmp_path):
    p = tmp_path / "a.m3u"
    write_m3u(p, "plist", ["x.mp3"], image=None)
    assert b"#EXTIMG" not in p.read_bytes()


def test_m3u_non_latin1_becomes_latin1_encodable(tmp_path):
    p = tmp_path / "a.m3u"
    write_m3u(
        p, "日本語 plist", ["20240521 日本語.mp3"], image="20240521 日本語.cover.jpg"
    )
    data = p.read_bytes()
    data.decode("iso-8859-1")  # must not raise
    assert b"?" in data


def test_translit_latin1_encodable():
    s = translit("ÄÖÜ ß → ok")
    s.encode("iso-8859-1")  # must not raise
    assert "ok" in s
    # latin-1-representable chars survive (iconv TRANSLIT parity)
    assert "Ä" in s and "ß" in s


def test_translit_idempotent_for_ascii():
    assert translit("plain 20240521 title") == "plain 20240521 title"


def test_sanitize_strips_forbidden():
    s = sanitize_component('bad<>:"/\\|?*name.mp3')
    assert not set('<>:"/\\|?*') & set(s)
    assert s.endswith(".mp3")


def test_sanitize_trailing_dot_and_space():
    assert not sanitize_component("name. ").endswith((" ", "."))
    assert not sanitize_component(".hidden").startswith(".")


def test_sanitize_maxlen():
    assert len(sanitize_component("x" * 150)) == 100


def test_sanitize_control_chars_and_nbsp():
    assert sanitize_component("a\x01b") == "ab"
    assert "\u00a0" not in sanitize_component("a\u00a0b")
