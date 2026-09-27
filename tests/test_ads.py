"""Ad detection/cut tests: real ffmpeg on generated audio, no mocks of the pipeline."""

from __future__ import annotations

import dataclasses
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from podfetch import ads
from podfetch.ads import (
    AdCutError,
    AdSpan,
    _cluster_spans,
    _kept_ranges,
    _merge_spans,
    _parse_fpcalc,
    _shared_spans,
    _silence_events,
)
from podfetch.config import AdsConfig

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_FPCALC = shutil.which("fpcalc") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = str(Path(FFMPEG).parent / "ffprobe")


def make_tone(path: Path, freq: int, seconds: float, volume: float = 0.4) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}",
            "-filter:a",
            f"volume={volume}",
            str(path),
        ],
        check=True,
    )


@pytest.fixture()
def cfg(tmp_path: Path) -> AdsConfig:
    return AdsConfig(enabled=True, report_dir=tmp_path / "state" / "ads")


# ---------------------------------------------------------------------------
# pure logic


def test_cluster_requires_count_and_length():
    cfg = AdsConfig(cluster_min_silences=2, cluster_min_break_s=10.0, pad_s=0.5)
    events = [(100.0, 102.0), (115.0, 117.0)]
    spans = _cluster_spans(events, cfg)
    assert len(spans) == 1
    assert spans[0].start == pytest.approx(100.5)
    assert spans[0].end == pytest.approx(117.0 - 0.5)


def test_cluster_rejects_single_or_short():
    cfg = AdsConfig(cluster_min_silences=2, cluster_min_break_s=10.0, pad_s=0.5)
    assert _cluster_spans([(100.0, 101.0)], cfg) == []  # one silence
    assert _cluster_spans([(100.0, 100.2), (102.0, 102.2)], cfg) == []  # too short
    # too far apart: two singleton clusters
    assert _cluster_spans([(100.0, 101.0), (400.0, 441.0)], cfg) == []


def test_cluster_max_sep_splits():
    cfg = AdsConfig(
        cluster_min_silences=2,
        cluster_min_break_s=1.0,
        cluster_max_sep_s=120.0,
        pad_s=0.0,
    )
    events = [(0.0, 1.0), (2.0, 3.0), (500.0, 501.0), (502.0, 503.0)]
    assert len(_cluster_spans(events, cfg)) == 2


def test_merge_spans_prefers_specific_source():
    a = [
        AdSpan(10, 20, "silence"),
        AdSpan(12, 25, "fingerprint"),
        AdSpan(60, 70, "asr"),
    ]
    m = _merge_spans(a)
    assert len(m) == 2
    assert m[0].source == "fingerprint"
    assert (m[0].start, m[0].end) == (10, 25)
    assert m[1].source == "asr"


def test_kept_ranges_cuts_spans():
    kept = _kept_ranges(
        100.0, [AdSpan(10, 20, "fingerprint"), AdSpan(99.8, 99.9, "asr")]
    )
    assert kept[0] == (0.0, 10.0)
    assert kept[1][0] == pytest.approx(20.0)


def test_parse_fpcalc_both_encodings():
    assert _parse_fpcalc("FINGERPRINT=1,2,3") == [1, 2, 3]
    import base64

    raw = (1).to_bytes(4, "big") + (258).to_bytes(4, "big")
    assert _parse_fpcalc(
        "DURATION=10\nFINGERPRINT=" + base64.b64encode(raw).decode()
    ) == [1, 258]


def test_shared_spans_finds_offset_repetition():
    win, hop = 10.0, 5.0
    rng = random.Random(1234)

    def w() -> list[int]:
        return [rng.getrandbits(32) for _ in range(8)]

    a = [w() for _ in range(40)]
    b = [w() for _ in range(40)]
    shared = [w() for _ in range(5)]
    for k in range(5):  # a windows 6..10 == b windows 10..14 (offset +4)
        a[6 + k] = shared[k]
        b[10 + k] = shared[k]
    spans = _shared_spans(a, b, hop, win, sim_thresh=0.7, min_span=10.0)
    assert spans == [(30.0, 60.0)]  # query-time coordinates


# ---------------------------------------------------------------------------
# ffmpeg-backed behaviour (real binaries, realistic audio)


@needs_ffmpeg
def test_silence_events_and_gating(tmp_path: Path):
    cfg = AdsConfig(cluster_min_break_s=5.0)
    # content | gap | LOUD | gap | LOUD | gap | content (same level as head/tail)
    segs = [
        (tmp_path / "a0.wav", 440, 3, 0.4),
        (tmp_path / "g0.wav", 440, 0.6, 0.0),
        (tmp_path / "a1.wav", 500, 3, 0.9),
        (tmp_path / "g1.wav", 440, 0.6, 0.0),
        (tmp_path / "a2.wav", 500, 3, 0.9),
        (tmp_path / "g2.wav", 440, 0.6, 0.0),
        (tmp_path / "tail.wav", 440, 4, 0.4),
    ]
    for p, freq, dur, vol in segs:
        make_tone(p, freq, dur, vol)
    inputs = []
    for p, _, _, _ in segs:
        inputs += ["-i", str(p)]
    concat = "".join(f"[{i}:a]" for i in range(len(segs)))
    out = tmp_path / "mix.wav"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            f"{concat}concat=n={len(segs)}:v=0:a=1[a]",
            "-map",
            "[a]",
            str(out),
        ],
        check=True,
    )
    events = _silence_events(cfg, FFMPEG, out)
    assert len(events) == 3
    spans = _cluster_spans(events, cfg)
    assert len(spans) == 1
    assert spans[0].start == pytest.approx(3.48, abs=0.3)
    assert spans[0].end == pytest.approx(10.32, abs=0.3)
    pcm = ads._decode_pcm(FFMPEG, out)
    times, db = ads._rms_curve(pcm, 8000)
    assert times and db
    # loud cluster vs content-level baseline → gate accepts
    assert all(ads._loudness_ok(sp, times, db, cfg, events) for sp in spans)
    # span over content-level silence-free audio → gate rejects (no deviation)
    assert not ads._loudness_ok(AdSpan(11.5, 13.5, "silence"), times, db, cfg, events)


@needs_ffmpeg
def test_cut_rewrites_file(tmp_path: Path, cfg: AdsConfig):
    src = tmp_path / "ep.mp3"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=30",
            "-af",
            "volume=0.3",
            "-c:a",
            "libmp3lame",
            str(src),
        ],
        check=True,
    )
    before = src.stat().st_size
    data = src.read_bytes()
    assert data[:3] != b"ID3" or True
    assert ads.cut(src, [AdSpan(10.0, 20.0, "asr")], cfg) is True
    after = src.stat().st_size
    assert after < before * 0.75
    dur = float(
        subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert 19.0 <= dur <= 22.0
    assert not (tmp_path / "ep.cut.mp3").exists()


@needs_ffmpeg
def test_cut_rejects_suspicious_mass_removal(tmp_path: Path, cfg: AdsConfig):
    src = tmp_path / "big.mp3"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=60",
            "-c:a",
            "libmp3lame",
            str(src),
        ],
        check=True,
    )
    with pytest.raises(AdCutError, match="suspicious"):
        ads.cut(src, [AdSpan(5.0, 58.0, "silence")], cfg)
    assert src.stat().st_size > 100000  # file untouched


@needs_ffmpeg
def test_cut_noop_when_nothing_removed(tmp_path: Path, cfg: AdsConfig):
    src = tmp_path / "full.mp3"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=12",
            "-c:a",
            "libmp3lame",
            str(src),
        ],
        check=True,
    )
    before = src.stat().st_size
    assert ads.cut(src, [AdSpan(30.0, 40.0, "asr")], cfg) is False
    assert src.stat().st_size == before


@needs_ffmpeg
def test_process_episode_end_to_end(tmp_path: Path):
    """Loud/silent ad break gets cut end-to-end; report under state dir only."""
    cfg = AdsConfig(
        enabled=True,
        fingerprint=False,
        cluster_min_break_s=10.0,
        report_dir=tmp_path / "state" / "ads",
    )
    feed_dir = tmp_path / "feed"
    feed_dir.mkdir()
    # content 10s | gap | AD 8s loud | gap | AD 8s loud | gap | content 10s
    segs = [
        (tmp_path / "c0.wav", 440, 10, 0.4),
        (tmp_path / "g0.wav", 440, 0.7, 0.0),
        (tmp_path / "ad1.wav", 1000, 8, 0.9),
        (tmp_path / "g1.wav", 440, 0.7, 0.0),
        (tmp_path / "ad2.wav", 1300, 8, 0.9),
        (tmp_path / "g2.wav", 440, 0.7, 0.0),
        (tmp_path / "c1.wav", 440, 10, 0.4),
    ]
    for p, freq, dur, vol in segs:
        make_tone(p, freq, dur, vol)
    inputs = []
    for p, _, _, _ in segs:
        inputs += ["-i", str(p)]
    concat = "".join(f"[{i}:a]" for i in range(len(segs)))
    wav = tmp_path / "mix.wav"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            f"{concat}concat=n={len(segs)}:v=0:a=1[a]",
            "-map",
            "[a]",
            str(wav),
        ],
        check=True,
    )
    mp3 = feed_dir / "20250101 ep.mp3"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(mp3),
        ],
        check=True,
    )

    report = ads.process_episode(mp3, cfg, "testfeed", feed_dir)
    assert report is not None
    assert report["cut"] is True
    assert len(report["spans"]) == 1
    rep = tmp_path / "state" / "ads" / "testfeed" / "20250101 ep.mp3.ads.json"
    assert rep.is_file()
    assert json.loads(rep.read_text())["cut"] is True
    # state dir isolated from feed dir
    assert list(feed_dir.iterdir()) == [mp3]


@needs_ffmpeg
@pytest.mark.skipif(not HAS_FPCALC, reason="fpcalc not on PATH")
def test_fingerprint_library_shared_spans(tmp_path: Path):
    """Identical noise 'ad' section in two episodes → library reports the span."""
    cfg = AdsConfig(
        enabled=True, fingerprint=True, fp_min_span_s=8.0, report_dir=tmp_path / "state"
    )

    def noise(name: Path, seed: int, dur: float) -> Path:
        p = tmp_path / name
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anoisesrc=colour=pink:seed={seed}:duration={dur}",
                "-filter:a",
                "volume=0.5",
                str(p),
            ],
            check=True,
        )
        return p

    ad = noise("ad.wav", 7, 20)
    for name, head_seed, tail_seed in (("ep1.mp3", 42, 101), ("ep2.mp3", 43, 102)):
        head = noise(Path(f"h{name}.wav"), head_seed, 12)
        tail = noise(Path(f"t{name}.wav"), tail_seed, 8)
        p = tmp_path / name
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-y",
                "-i",
                str(head),
                "-i",
                str(ad),
                "-i",
                str(tail),
                "-filter_complex",
                "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
                "-map",
                "[a]",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(p),
            ],
            check=True,
        )
    ffmpeg = ads.ffmpeg_bin(cfg)
    fpcalc = ads.fpcalc_bin(cfg)
    lib = ads.FingerprintLibrary(cfg, ffmpeg, fpcalc, tmp_path / "fp")
    spans = lib.shared_spans(tmp_path / "ep1.mp3", [tmp_path / "ep2.mp3"])
    assert spans, "shared 20s ad section must be found"
    assert all(s.source == "fingerprint" for s in spans)
    # span overlaps the ad core (ad occupies 12..32s in ep1)
    assert any(min(s.end, 32.0) - max(s.start, 12.0) >= 8.0 for s in spans)
    # cache written
    assert list((tmp_path / "fp").glob("*.json"))


def test_binary_resolution_env_and_missing(tmp_path: Path):
    import os

    old = os.environ.pop("PODFETCH_FFMPEG", None)
    try:
        if HAS_FFMPEG:
            assert ads.ffmpeg_bin(AdsConfig()) == FFMPEG
            # explicit config path wins over PATH
            cfg = dataclasses.replace(AdsConfig(), ffmpeg=FFMPEG)
            assert ads.ffmpeg_bin(cfg) == FFMPEG
        # non-existent config path falls through to PATH discovery
        cfg = dataclasses.replace(AdsConfig(), ffmpeg=str(tmp_path / "nope"))
        assert ads.ffmpeg_bin(cfg) == FFMPEG
    finally:
        if old:
            os.environ["PODFETCH_FFMPEG"] = old


def test_ffmpeg_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.delenv("PODFETCH_FFMPEG", raising=False)
    with pytest.raises(AdCutError, match="ffmpeg not found"):
        ads.ffmpeg_bin(AdsConfig())


def test_asr_span_merge(tmp_path: Path):
    cfg = AdsConfig(
        asr=True,
        asr_pad_before_s=5,
        asr_pad_after_s=3,
        asr_merge_gap_s=15,
        fp_min_span_s=4.0,
        asr_max_span_s=300,
    )
    segs = [
        {"s": 100.0, "e": 105.0, "t": "Diese Folge wird unterstützt von Acme."},
        {
            "s": 110.0,
            "e": 114.0,
            "t": "Besucht acme.example und spart mit Gutschein SPAREN.",
        },
        {"s": 300.0, "e": 304.0, "t": "Der Gutschein CODE gilt nur heute."},
        {"s": 400.0, "e": 404.0, "t": "Und danach geht es weiter mit der Geschichte."},
    ]
    spans = ads._asr_spans(segs, cfg)
    assert len(spans) == 2
    first = spans[0]
    assert first.start <= 95.0 and first.end >= 117.0  # padded + merged
    assert spans[1].start <= 295.0
    assert all(sp.source == "asr" for sp in spans)


def test_asr_keyword_patterns_negative():
    segs = [{"s": 0.0, "e": 4.0, "t": "Der Dinosaurier war ein riesiges Tier."}]
    spans = ads._asr_spans(segs, AdsConfig(fp_min_span_s=4.0))
    assert spans == []
