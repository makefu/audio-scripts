"""Offline ad detection + removal: silence clusters, loudness, fingerprints, ASR.

Evidence-driven design (measured on the live feed corpus 2026-09-27):
- German public/megaphone feeds carry no ad markers (no gads/psql/psc, no CHAP frames).
- silencedetect alone finds nothing on DAI feeds; loudness is unimodal; byte-hash and
  chromaprint cross-episode matching find zero shared spans when ads are episode-unique.
Therefore no single cheap signal may trigger a cut: silence clusters must be
corroborated by a loudness deviation, while fingerprint-shared spans and ASR ad
phrases cut directly. Everything fails open (no parse/no evidence => no cut).
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AdsConfig
from .download import PermissionDeniedError


class AdCutError(Exception):
    """Ad detection/cut failed; the episode still counts as downloaded."""


@dataclass(frozen=True)
class AdSpan:
    start: float
    end: float
    source: str  # "silence" | "fingerprint" | "asr"

    def key(self) -> float:
        return self.start


# ---------------------------------------------------------------------------
# external binaries


def ffmpeg_bin(cfg: AdsConfig) -> str:
    if cfg.ffmpeg and Path(cfg.ffmpeg).is_file():
        return cfg.ffmpeg
    found = os.environ.get("PODFETCH_FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise AdCutError("ffmpeg not found (set ads.ffmpeg or PATH)")
    return found


def _sibling(ffmpeg: str, tool: str) -> str:
    side = Path(ffmpeg).parent / tool
    return str(side) if side.is_file() else (shutil.which(tool) or tool)


def _ffprobe(ffmpeg: str) -> str:
    return _sibling(ffmpeg, "ffprobe")


def fpcalc_bin(cfg: AdsConfig) -> str:
    if cfg.fpcalc and Path(cfg.fpcalc).is_file():
        return cfg.fpcalc
    found = os.environ.get("PODFETCH_FPCALC") or shutil.which("fpcalc")
    if not found:
        raise AdCutError("fpcalc not found (set ads.fpcalc or PATH)")
    return found


def media_duration(path: Path, ffmpeg: str) -> float:
    out = subprocess.run(
        [
            _ffprobe(ffmpeg),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError as exc:  # ffprobe printed junk
        raise AdCutError(f"cannot read duration of {path}") from exc


def _run_ffmpeg(argv: list[str], what: str) -> str:
    """Run ffmpeg capturing stderr (where filter logs live); return stderr."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EROFS, errno.EPERM):
            raise PermissionDeniedError(argv[-1]) from exc
        raise AdCutError(f"{what}: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-400:]
        raise AdCutError(f"{what} failed (rc={proc.returncode}): {tail}")
    return proc.stderr or ""


# ---------------------------------------------------------------------------
# ffmpeg decode helper (shared by loudness + fingerprint paths)


def _decode_pcm(ffmpeg: str, path: Path, rate: int = 8000) -> bytes:
    """Mono s16le PCM at `rate` Hz via one ffmpeg pass."""
    proc = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return proc.stdout


def _rms_curve(
    pcm: bytes, rate: int, hop: float = 0.05
) -> tuple[list[float], list[float]]:
    """Short-term RMS dBFS at `hop` resolution. numpy when present, array otherwise."""
    try:
        import numpy as np

        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        n = max(1, int(rate * hop))
        m = (len(x) // n) * n
        if m == 0:
            return [], []
        blocks = x[:m].reshape(-1, n)
        rms = np.sqrt(np.mean(blocks * blocks, axis=1)) / 32768.0
        db = 20.0 * np.log10(np.maximum(rms, 1e-10))
        return list(np.arange(len(db)) * hop + n / (2 * rate)), [float(v) for v in db]
    except ImportError:
        import array

        a = array.array("h")
        a.frombytes(pcm[: (len(pcm) // 2) * 2])
        n = max(1, int(rate * hop))
        times, db = [], []
        import math

        for i, start in enumerate(range(0, len(a) - n + 1, n)):
            s = 0
            for smp in a[start : start + n]:
                s += smp * smp
            rms = math.sqrt(s / n) / 32768.0
            times.append(i * hop + n / (2 * rate))
            db.append(20.0 * math.log10(max(rms, 1e-10)))
        return times, db


# ---------------------------------------------------------------------------
# detector 1: silence clusters (ta264/mythcommflag-silence state machine)

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(
    r"silence_end:\s*(-?[\d.]+)(?:\s*\|\s*silence_duration:\s*([\d.]+))?"
)


def _silence_events(
    cfg: AdsConfig, ffmpeg: str, path: Path
) -> list[tuple[float, float]]:
    stderr = _run_ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={cfg.silence_db}dB:d={cfg.silence_min_s}",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        "silencedetect",
    )
    duration = media_duration(path, ffmpeg)
    events: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            if open_start is None:
                open_start = float(m.group(1))
            continue
        m = _SILENCE_END.search(line)
        if m and open_start is not None:
            events.append((open_start, float(m.group(1))))
            open_start = None
    if open_start is not None:
        events.append((open_start, duration))  # silence at EOF
    return events


def _cluster_spans(events: list[tuple[float, float]], cfg: AdsConfig) -> list[AdSpan]:
    """Group silences into break clusters; accept per mindetect/min_break, inset by pad."""
    spans: list[AdSpan] = []
    cluster: list[tuple[float, float]] = []
    for ev in events:
        if cluster and ev[0] - cluster[-1][1] <= cfg.cluster_max_sep_s:
            cluster.append(ev)
        else:
            if cluster:
                spans.extend(_accept_cluster(cluster, cfg))
            cluster = [ev]
    if cluster:
        spans.extend(_accept_cluster(cluster, cfg))
    return spans


def _accept_cluster(cluster: list[tuple[float, float]], cfg: AdsConfig) -> list[AdSpan]:
    start = cluster[0][0]
    end = cluster[-1][1]
    if (
        len(cluster) >= cfg.cluster_min_silences
        and end - start >= cfg.cluster_min_break_s
    ):
        s, e = start + cfg.pad_s, end - cfg.pad_s
        if e > s:
            return [AdSpan(s, e, "silence")]
    return []


# ---------------------------------------------------------------------------
# detector 2 (corroboration): loudness deviation


def _median(v: list[float]) -> float:
    s = sorted(v)
    n = len(s)
    return (
        0.0 if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0)
    )


def _loudness_ok(
    span: AdSpan,
    times: list[float],
    db: list[float],
    cfg: AdsConfig,
    events: list[tuple[float, float]] | None = None,
) -> bool:
    """Gate a silence-cluster span on a loudness deviation.

    Baseline = median over the whole file's audible windows (silence-event
    frames excluded — they would skew every median). Ads are a small
    minority of an episode, so the global median is the content level; the
    span's non-silence content must sit >= loudness_step_lu away from it
    (MinusPod splice heuristic). Quiet passages near the median get rejected.
    """
    events = events or []

    def silent(t: float) -> bool:
        return any(s <= t <= e for s, e in events)

    inside = [
        d for t, d in zip(times, db) if span.start <= t <= span.end and not silent(t)
    ]
    audible = [d for t, d in zip(times, db) if not silent(t)]
    if not inside or not audible:
        return False
    import statistics

    return abs(statistics.fmean(inside) - _median(audible)) >= cfg.loudness_step_lu


# ---------------------------------------------------------------------------
# detector 3: cross-episode chromaprint fingerprint library

_FP_VERSION = 1


def _fingerprint_windows(
    cfg: AdsConfig, ffmpeg: str, fpcalc: str, path: Path
) -> list[list[int]]:
    """Windowed fingerprints: decode whole file per WIN window, pipe raw PCM to fpcalc."""
    win, hop = cfg.fp_window_s, cfg.fp_hop_s
    duration = media_duration(path, ffmpeg)
    out: list[list[int]] = []
    t = 0.0
    while t < max(0.0, duration - 4):
        proc = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-ss",
                str(t),
                "-t",
                str(win),
                "-i",
                str(path),
                "-ac",
                "2",
                "-ar",
                "44100",
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        fp = subprocess.run(
            [
                fpcalc,
                "-raw",
                "-format",
                "s16le",
                "-rate",
                "44100",
                "-channels",
                "2",
                "-length",
                "200",
                "-",
            ],
            input=proc.stdout,
            capture_output=True,
            check=True,
        )
        out.append(_parse_fpcalc(fp.stdout.decode("utf-8", "replace")))
        t += hop
    return out


def _parse_fpcalc(text: str) -> list[int]:
    payload = text.strip().split("FINGERPRINT=")[-1].strip()
    if not payload:
        return []
    if payload[0].isdigit() and "," in payload:
        return [int(x) for x in payload.split(",")]
    raw = base64.b64decode(payload)
    return [int.from_bytes(raw[i : i + 4], "big") for i in range(0, len(raw), 4)]


def _word_sim(a: list[int], b: list[int]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    same = sum(1 for x, y in zip(a, b) if (x ^ y).bit_count() <= 6)
    return same / n


def _shared_spans(
    a: list[list[int]],
    b: list[list[int]],
    hop: float,
    win: float,
    sim_thresh: float,
    min_span: float,
) -> list[tuple[float, float]]:
    """Repeated spans between two windowed fingerprints, as (tA_start, tA_end).

    Cluster matching window pairs by Δt offset (modal-bucket method of
    dejavu/comdet); consecutive query windows inside a bucket form a span.
    """
    from collections import defaultdict

    buckets: dict[int, list[int]] = defaultdict(list)
    for ia, wa in enumerate(a):
        for ib, wb in enumerate(b):
            if _word_sim(wa, wb) >= sim_thresh:
                buckets[ib - ia].append(ia)
    spans: list[tuple[float, float]] = []
    for idxs in buckets.values():
        idxs = sorted(set(idxs))
        run_start = prev = idxs[0]
        for i in idxs[1:] + [None]:  # type: ignore[list-item]
            if i is not None and i == prev + 1:
                prev = i
                continue
            s = run_start * hop
            e = prev * hop + win
            if e - s >= min_span:
                spans.append((s, e))
            if i is not None:
                run_start = prev = i
    return spans


class FingerprintLibrary:
    """Per-feed windowed fingerprint cache in state_dir/ads/fp (JSON sidecars)."""

    def __init__(self, cfg: AdsConfig, ffmpeg: str, fpcalc: str, cache_dir: Path):
        self.cfg = cfg
        self.ffmpeg = ffmpeg
        self.fpcalc = fpcalc
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _entry(self, mp3: Path) -> list[list[int]] | None:
        st = mp3.stat()
        key = hashlib.sha1(f"{mp3.name}:{st.st_size}".encode()).hexdigest()[:16]
        fp = self.dir / f"{key}.json"
        if fp.is_file():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if data.get("v") == _FP_VERSION and data.get("size") == st.st_size:
                    return data["windows"]
            except (ValueError, KeyError):
                pass
        windows = _fingerprint_windows(self.cfg, self.ffmpeg, self.fpcalc, mp3)
        try:
            fp.write_text(
                json.dumps({"v": _FP_VERSION, "size": st.st_size, "windows": windows}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return windows

    def shared_spans(self, target: Path, refs: list[Path]) -> list[AdSpan]:
        cfg = self.cfg
        tgt = self._entry(target)
        spans: list[AdSpan] = []
        for ref in refs[: cfg.fp_max_refs]:
            if ref == target:
                continue
            other = self._entry(ref)
            for s, e in _shared_spans(
                tgt,
                other,
                cfg.fp_hop_s,
                cfg.fp_window_s,
                cfg.fp_similarity,
                cfg.fp_min_span_s,
            ):
                spans.append(AdSpan(s, e, "fingerprint"))
        return spans


# ---------------------------------------------------------------------------
# detector 4 (opt-in): ASR keyword spans


_AD_PATTERNS = [
    # German
    r"\bwerbung\b",
    r"gesponsert",
    r"(wurde|wird).{0,40}(uns )?(ermöglicht|unterstützt)",
    r"unterstützung von",
    r"\bgutschein\b",
    r"\brabatt(code)?\b",
    r"gewinnspiel",
    r"gewinn(en)? (mit|bei)",
    r"(besuchen|bestell|schaut|stöbert) (sie|ihr|dich|mal)",
    r"neue (folge|staffel)",
    r"\babo\b",
    r"kostenlos testen",
    r"probehören",
    r"(heute |in dieser folge )?(ge)sponsert von",
    r"podcast wird präsentiert",
    r"gleich nach der werbung",
    r"nach einer kurzen",
    r"werbe(block|pause)",
    # English fallback
    r"\bbrought to you by\b",
    r"\bsponsored by\b",
    r"\bpromo(tion)? code\b",
    r"\bcoupon code\b",
    r"\bfree trial\b",
    r"\ba word from our\b",
]
_ADLRE = [re.compile(p, re.IGNORECASE) for p in _AD_PATTERNS]


class Transcriber:
    """faster-whisper wrapper; lazy import so the dep stays optional."""

    def __init__(self, cfg: AdsConfig):
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise AdCutError(
                "ads.asr=true but faster-whisper is not installed "
                "(use packages.podfetchWithWhisper)"
            ) from exc
        self.cfg = cfg
        kwargs = {"device": "cpu", "compute_type": "int8"}
        if cfg.asr_model_dir:
            kwargs["download_root"] = cfg.asr_model_dir
        self._model = WhisperModel(cfg.asr_model, **kwargs)

    def spans(self, mp3: Path, cache_dir: Path) -> list[AdSpan]:
        st = mp3.stat()
        key = hashlib.sha1(f"{mp3.name}:{st.st_size}".encode()).hexdigest()[:16]
        cache = cache_dir / f"{key}.transcript.json"
        segs: list[dict]
        if cache.is_file():
            try:
                segs = json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                segs = []
        if not segs:
            result = self._model.transcribe(
                str(mp3), language=self.cfg.asr_language or None, vad_filter=True
            )
            seg_iter = result[0] if isinstance(result, tuple) else result
            segs = [
                {"s": round(s.start, 2), "e": round(s.end, 2), "t": s.text}
                for s in seg_iter
            ]
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return _asr_spans(segs, self.cfg)


def _asr_spans(segs: list[dict], cfg: AdsConfig) -> list[AdSpan]:
    hits = [
        (s["s"], s["e"]) for s in segs if any(r.search(s.get("t", "")) for r in _ADLRE)
    ]
    spans: list[AdSpan] = []
    for s, e in hits:
        s = max(0.0, s - cfg.asr_pad_before_s)
        e = e + cfg.asr_pad_after_s
        if spans and s - spans[-1][1] <= cfg.asr_merge_gap_s:
            ps, pe = spans.pop()
            spans.append((ps, max(pe, e)))
        else:
            spans.append((s, e))
    return [
        AdSpan(s, e, "asr")
        for s, e in spans
        if cfg.fp_min_span_s <= e - s <= cfg.asr_max_span_s
    ]


# ---------------------------------------------------------------------------
# gating + cutting


def _merge_spans(spans: list[AdSpan], gap: float = 2.0) -> list[AdSpan]:
    """Merge overlapping/near spans; keep the strongest label (non-silence wins)."""
    out: list[AdSpan] = []
    for sp in sorted(spans, key=AdSpan.key):
        if out and sp.start - out[-1].end <= gap:
            ps, pe, src = out[-1].start, max(out[-1].end, sp.end), out[-1].source
            if sp.source != "silence":
                src = sp.source
            out[-1] = AdSpan(ps, pe, src)
        else:
            out.append(sp)
    return [sp for sp in out if sp.end - sp.start >= 4.0]


def _snap(sp: AdSpan, events: list[tuple[float, float]], win: float = 0.75) -> AdSpan:
    """Snap cut boundaries to the nearest silence edge (clean splices)."""
    s, e = sp.start, sp.end
    for es, ee in events:
        for edge in (es, ee):
            if abs(edge - s) <= win:
                s = edge
            if abs(edge - e) <= win:
                e = edge
    return AdSpan(min(s, e), max(s, e), sp.source)


def detect(
    mp3: Path,
    cfg: AdsConfig,
    lib: FingerprintLibrary | None = None,
    refs: list[Path] | None = None,
    transcriber: Transcriber | None = None,
    cache_dir: Path | None = None,
) -> list[AdSpan]:
    """All enabled detectors, fail-open, gated spans ready to cut."""
    ffmpeg = ffmpeg_bin(cfg)
    events = _silence_events(cfg, ffmpeg, mp3)
    spans = _merge_spans(_cluster_spans(events, cfg))

    silence_only = [sp for sp in spans if sp.source == "silence"]
    if silence_only:
        pcm = _decode_pcm(ffmpeg, mp3)
        times, db = _rms_curve(pcm, 8000)
        spans = [sp for sp in spans if sp.source != "silence"] + [
            sp for sp in silence_only if _loudness_ok(sp, times, db, cfg, events)
        ]

    if lib is not None and refs:
        spans.extend(lib.shared_spans(mp3, refs))
    if transcriber is not None:
        spans.extend(
            transcriber.spans(mp3, cache_dir or (cfg.report_dir / "transcripts"))
        )

    return _merge_spans([_snap(sp, events) for sp in spans])


def _kept_ranges(duration: float, spans: list[AdSpan]) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    pos = 0.0
    for sp in sorted(spans, key=AdSpan.key):
        s, e = max(0.0, sp.start), min(duration, sp.end)
        if s > pos + 0.5:
            kept.append((pos, s))
        pos = max(pos, e)
    if duration - pos > 0.5:
        kept.append((pos, duration))
    return kept


def cut(mp3: Path, spans: list[AdSpan], cfg: AdsConfig) -> bool:
    """Rewrite `mp3` without `spans` (single ffmpeg graph, 30 ms edge fades)."""
    ffmpeg = ffmpeg_bin(cfg)
    duration = media_duration(mp3, ffmpeg)
    kept = _kept_ranges(duration, spans)
    if not kept:
        return False
    total_kept = sum(e - s for s, e in kept)
    if total_kept < 0.3 * duration:
        # suspicious: detectors want to drop >70% of the episode — fail open
        raise AdCutError(
            f"suspicious cut: only {total_kept:.0f}s of {duration:.0f}s would remain"
        )
    if len(kept) == 1 and kept[0][0] <= 0.1 and kept[0][1] >= duration - 0.1:
        return False  # nothing actually removed

    fade = 0.03
    parts: list[str] = []
    labels: list[str] = []
    for i, (s, e) in enumerate(kept):
        d = e - s
        graph = f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS"
        if i > 0:
            graph += f",afade=t=in:d={fade}"
        if i < len(kept) - 1:
            graph += f",afade=t=out:st={max(0.0, d - fade):.3f}:d={fade}"
        parts.append(graph + f"[s{i}]")
        labels.append(f"[s{i}]")
    graph = ";".join(parts) + ";"
    graph += "".join(labels) + f"concat=n={len(kept)}:v=0:a=1[out]"

    tmp = mp3.with_suffix(".cut.mp3")
    argv = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(mp3),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        str(cfg.reencode_q),
        "-ar",
        "44100",
        str(tmp),
    ]
    try:
        _run_ffmpeg(argv, "ad cut")
        os.replace(tmp, mp3)
    except OSError as exc:
        raise AdCutError(f"cannot replace {mp3}: {exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# orchestration entry point used by runner


def process_episode(
    mp3: Path, cfg: AdsConfig, feed_slug: str, feed_dir: Path
) -> dict | None:
    """Detect + cut in place; write JSON report under state_dir/ads/<slug>/.

    Returns the report dict when anything was detected, else None.
    Raises AdCutError for real failures (missing binaries, corrupt audio).
    """
    ffmpeg = ffmpeg_bin(cfg)
    report_dir = cfg.report_dir / feed_slug
    report_dir.mkdir(parents=True, exist_ok=True)
    fp_dir = cfg.report_dir / "fp" / feed_slug

    lib = (
        FingerprintLibrary(cfg, ffmpeg, fpcalc_bin(cfg), fp_dir)
        if cfg.fingerprint
        else None
    )
    refs: list[Path] = []
    if lib is not None:
        refs = sorted(p for p in feed_dir.glob("*.mp3") if p != mp3)
    transcriber = Transcriber(cfg) if cfg.asr else None

    spans = detect(
        mp3,
        cfg,
        lib=lib,
        refs=refs,
        transcriber=transcriber,
        cache_dir=report_dir / "transcripts",
    )
    removed = cut(mp3, spans, cfg) if spans else False
    report = {
        "file": mp3.name,
        "spans": [[round(s.start, 3), round(s.end, 3), s.source] for s in spans],
        "cut": removed,
        "removed_seconds": round(sum(s.end - s.start for s in spans), 1)
        if removed
        else 0,
    }
    try:
        (report_dir / f"{mp3.name}.ads.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
    except OSError:
        pass  # reporting is best-effort
    return report if spans else None
