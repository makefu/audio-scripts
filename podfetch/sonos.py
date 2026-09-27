"""Sonos-compatible filename sanitization, transliteration and m3u writing."""

from __future__ import annotations

import re
import unicodedata

_FORBIDDEN = set('<>:"/\\|?*')
# control characters forbidden on SMB
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_COMPONENT = 100

_EXTM3U_HEADER = "#EXTM3U"
_EXTENC_HEADER = "#EXTENC: ISO-8859-1"


def translit(s: str) -> str:
    """Map arbitrary Unicode to a latin-1-encodable string (iconv TRANSLIT parity).
    Characters already encodable in ISO-8859-1 (Ä, ß, ...) pass through unchanged,
    as iconv does; the rest are NFKD-decomposed and stripped of combining marks."""
    out: list[str] = []
    for ch in s:
        if _is_latin1(ch):
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append("".join(c if _is_latin1(c) else "?" for c in base))
    return "".join(out)


def _is_latin1(ch: str) -> bool:
    try:
        ch.encode("iso-8859-1")
        return True
    except UnicodeEncodeError:
        return False


def sanitize_component(s: str, maxlen: int = _MAX_COMPONENT) -> str:
    """Sanitize one path component for SMB/Sonos: no forbidden chars, control chars,
    leading/trailing space or dot, NBSP; clamped to `maxlen` characters."""
    s = unicodedata.normalize("NFC", s)
    s = _CONTROL.sub("", s)
    s = s.replace("\u00a0", " ")
    s = "".join("_" if ch in _FORBIDDEN else ch for ch in s)
    s = s.strip().strip(" .").strip()
    if not s:
        s = "untitled"
    return s[:maxlen]


def write_m3u(
    path, playlist_title: str, entries: list[str], image: str | None = None
) -> None:
    """Write an m3u byte-compatible with bin/gendownload.sh:
    ISO-8859-1 (transliterated) bytes, CRLF line endings (unix2dos parity)."""
    lines = [_EXTM3U_HEADER, _EXTENC_HEADER]
    if image is not None:
        lines.append(f"#EXTIMG: {image}")
    lines.append(f"#PLAYLIST: {playlist_title}")
    lines.extend(entries)
    text = "\n".join(lines) + "\n"
    data = translit(text).encode("iso-8859-1", errors="replace")
    path.write_bytes(data.replace(b"\n", b"\r\n"))
