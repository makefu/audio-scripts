"""ID3 tagging and cover-art processing (mutagen replacement for mid3v2)."""

from __future__ import annotations

import io
from pathlib import Path

import requests
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2
from PIL import Image

JPEG_QUALITY = 82


class CoverError(Exception):
    pass


class TagError(Exception):
    pass


def process_episode(
    mp3_path,
    ep,
    feed_name: str,
    cover_width: int,
    embed_width: int,
    cover_url: str | None,
    session: requests.Session | None,
    timeout: float = 30.0,
) -> None:
    """Write `<base>.cover.jpg` and ID3 frames. Cover failures raise CoverError
    (caller treats as non-fatal warning); tag failures raise TagError.

    ID3 frames mirror bin/gendownload.sh mid3v2 call: TPE2/TPE1/TALB=feed name,
    TIT2=episode title, APIC replaced wholesale."""
    mp3_path = Path(mp3_path)
    cover_bytes: bytes | None = None
    if cover_url:
        try:
            raw = _fetch(cover_url, session, timeout)
            cover_bytes = _resize_jpeg(raw, cover_width)
        except (requests.exceptions.RequestException, OSError, ValueError) as exc:
            raise CoverError(f"{cover_url}: {exc}") from exc
        mp3_path.with_name(mp3_path.stem + ".cover.jpg").write_bytes(cover_bytes)

    tags = _load_tags(mp3_path)
    for frame in (
        TPE2(encoding=3, text=feed_name),
        TPE1(encoding=3, text=feed_name),
        TALB(encoding=3, text=feed_name),
        TIT2(encoding=3, text=ep.title),
    ):
        for old in [f for f in tags.getall(frame.FrameID)]:
            del tags[old]
        tags.add(frame)
    for frame in list(tags.getall("APIC")):
        del tags[frame]
    if cover_bytes is not None:
        try:
            embedded = _resize_jpeg(cover_bytes, embed_width)
        except (OSError, ValueError) as exc:
            raise CoverError(f"embedded cover: {exc}") from exc
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=embedded))
    try:
        tags.save(v1=0)
    except Exception:  # noqa: BLE001 - fall back to v2.3 on any v2.4 write error
        # v2.3 fallback on v2.4 write failures (old-file edge cases)
        tags.save(v1=0, v2_version=3)


def _fetch(url: str, session: requests.Session | None, timeout: float) -> bytes:
    sess = session or requests.Session()
    resp = sess.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _resize_jpeg(raw: bytes, max_dim: int) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def _load_tags(mp3_path: Path) -> ID3:
    try:
        return ID3(mp3_path)
    except Exception:  # noqa: BLE001 - any header error means: create a tag
        from mutagen.mp3 import MP3

        # freshly downloaded mp3s have no ID3 header; allocate and persist one first
        audio = MP3(str(mp3_path))
        audio.add_tags()
        audio.save()
        return ID3(mp3_path)
