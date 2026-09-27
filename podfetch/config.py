"""YAML config loading with key-path-precise validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_USER_AGENT = "podfetch/1.0 (+https://github.com/makefu/audio-scripts)"
DEFAULT_STATE_DIR = "~/.local/state/podfetch"
DEFAULT_PLAYLIST_LATEST = "aktuelle_folge.m3u"

_SLUG_MAP = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "Ä": "ae",
    "Ö": "oe",
    "Ü": "ue",
}


def slugify(name: str) -> str:
    """Derive a directory slug from a feed name (German transliteration, dot-separated)."""
    s = name.lower().strip()
    for k, v in _SLUG_MAP.items():
        s = s.replace(k.lower(), v)
    return sanitize_slug(s)


def sanitize_slug(s: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", ".", s.lower())
    return s.strip(".")


class ConfigError(Exception):
    """Raised with a list of human-readable errors, each naming the offending key path."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    slug: str
    enabled: bool = True
    playlist_latest: str | None = DEFAULT_PLAYLIST_LATEST
    playlist_full: str | None = None
    cover_width: int = 600
    embed_width: int = 350
    max_episodes: int | None = None
    cut_ads: bool | None = None


@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool = True
    apprise_urls: list[str] = field(default_factory=list)
    new_episodes: bool = True
    errors: bool = True
    error_throttle_minutes: int = 60
    max_episode_messages: int = 5


@dataclass(frozen=True)
class AdsConfig:
    """Offline ad detection/removal knobs (see podfetch.ads)."""

    enabled: bool = False
    ffmpeg: str | None = None
    fpcalc: str | None = None
    silence_db: float = -45.0
    silence_min_s: float = 0.4
    cluster_max_sep_s: float = 120.0
    cluster_min_silences: int = 2
    cluster_min_break_s: float = 20.0
    pad_s: float = 0.48
    loudness_step_lu: float = 2.0
    fingerprint: bool = True
    fp_window_s: float = 10.0
    fp_hop_s: float = 5.0
    fp_similarity: float = 0.7
    fp_min_span_s: float = 10.0
    fp_max_refs: int = 8
    asr: bool = False
    asr_model: str = "base"
    asr_language: str = "de"
    asr_model_dir: str | None = None
    asr_pad_before_s: float = 5.0
    asr_pad_after_s: float = 3.0
    asr_merge_gap_s: float = 15.0
    asr_max_span_s: float = 300.0
    reencode_q: int = 4
    report_dir: Path = Path("ads")


@dataclass(frozen=True)
class Config:
    output_dir: Path
    state_dir: Path
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 30.0
    retries: int = 2
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    ads: AdsConfig = field(default_factory=AdsConfig)
    feeds: list[FeedConfig] = field(default_factory=list)

    @staticmethod
    def load(path: str | os.PathLike[str]) -> Config:
        p = Path(path)
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError([f"config file not found: {p}"])
        except yaml.YAMLError as exc:
            raise ConfigError([f"invalid YAML in {p}: {exc}"])
        base = p.parent
        return _validate(raw, base)


def _abs(v: str, base: Path) -> Path:
    p = Path(os.path.expanduser(v))
    if not p.is_absolute():
        p = base / p
    return p


def _pos_int(
    fmap: dict, where: str, key: str, default: int | None, errs: list[str]
) -> int | None:
    v = fmap.get(key, default)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        errs.append(f"{where}.{key}: must be a positive integer or null")
        return default
    return v


def _want_mapping(raw: object, where: str, errs: list[str]) -> dict:
    if not isinstance(raw, dict):
        errs.append(f"{where}: must be a mapping")
        return {}
    return raw


def _validate(raw: object, base: Path) -> Config:
    errs: list[str] = []
    top = _want_mapping(raw, "config", errs)

    def get_str(key: str, default: str | None, where: str = "config") -> str | None:
        v = top.get(key, default)
        if v is None and default is None:
            errs.append(f"{where}.{key}: required")
            return None
        if not isinstance(v, str) or not v:
            errs.append(f"{where}.{key}: must be a string")
            return default
        return v

    output_dir_s = get_str("output_dir", None)
    if output_dir_s is None:
        errs.append("output_dir: required")
    user_agent = get_str("user_agent", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT

    def get_int(
        key: str, default: int, where: str = "config", minimum: int | None = None
    ):
        v = top.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            errs.append(f"{where}.{key}: must be an integer")
            return default
        if minimum is not None and v < minimum:
            errs.append(f"{where}.{key}: must be >= {minimum}")
            return default
        return v

    timeout_v = top.get("timeout", 30)
    if (
        isinstance(timeout_v, bool)
        or not isinstance(timeout_v, (int, float))
        or timeout_v <= 0
    ):
        errs.append("timeout: must be a number > 0")
        timeout_v = 30
    retries = get_int("retries", 2, minimum=0)

    state_dir_s = top.get("state_dir", DEFAULT_STATE_DIR)
    if not isinstance(state_dir_s, str) or not state_dir_s:
        errs.append("state_dir: must be a string")
        state_dir_s = DEFAULT_STATE_DIR
    state_dir = _abs(state_dir_s, base)

    araw = top.get("ads", {})
    awhere = "ads"
    amap = _want_mapping(araw, awhere, errs)

    def a_bool(key, default):
        v = amap.get(key, default)
        if not isinstance(v, bool):
            errs.append(f"{awhere}.{key}: must be a boolean")
            return default
        return v

    def a_num(key, default, minimum=None):
        v = amap.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errs.append(f"{awhere}.{key}: must be a number")
            return default
        if minimum is not None and v < minimum:
            errs.append(f"{awhere}.{key}: must be >= {minimum}")
            return default
        return float(v)

    def a_int(key, default, minimum=None):
        v = amap.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            errs.append(f"{awhere}.{key}: must be an integer")
            return default
        if minimum is not None and v < minimum:
            errs.append(f"{awhere}.{key}: must be >= {minimum}")
            return default
        return int(v)

    def a_str(key, default):
        v = amap.get(key, default)
        if v is not None and (not isinstance(v, str) or not v):
            errs.append(f"{awhere}.{key}: must be a string or null")
            return default
        return v

    ads = AdsConfig(
        enabled=a_bool("enabled", False),
        ffmpeg=a_str("ffmpeg", None),
        fpcalc=a_str("fpcalc", None),
        silence_db=a_num("silence_db", -45.0),
        silence_min_s=a_num("silence_min_s", 0.4, minimum=0.05),
        cluster_max_sep_s=a_num("cluster_max_sep_s", 120.0, minimum=1.0),
        cluster_min_silences=a_int("cluster_min_silences", 2, minimum=1),
        cluster_min_break_s=a_num("cluster_min_break_s", 20.0, minimum=1.0),
        pad_s=a_num("pad_s", 0.48, minimum=0.0),
        loudness_step_lu=a_num("loudness_step_lu", 2.0, minimum=0.0),
        fingerprint=a_bool("fingerprint", True),
        fp_window_s=a_num("fp_window_s", 10.0, minimum=2.0),
        fp_hop_s=a_num("fp_hop_s", 5.0, minimum=0.5),
        fp_similarity=a_num("fp_similarity", 0.7, minimum=0.1),
        fp_min_span_s=a_num("fp_min_span_s", 10.0, minimum=2.0),
        fp_max_refs=a_int("fp_max_refs", 8, minimum=1),
        asr=a_bool("asr", False),
        asr_model=a_str("asr_model", "base") or "base",
        asr_language=a_str("asr_language", "de"),
        asr_model_dir=a_str("asr_model_dir", None),
        asr_pad_before_s=a_num("asr_pad_before_s", 5.0, minimum=0.0),
        asr_pad_after_s=a_num("asr_pad_after_s", 3.0, minimum=0.0),
        asr_merge_gap_s=a_num("asr_merge_gap_s", 15.0, minimum=0.0),
        asr_max_span_s=a_num("asr_max_span_s", 300.0, minimum=10.0),
        reencode_q=a_int("reencode_q", 4, minimum=0),
        report_dir=state_dir / "ads",
    )

    nraw = top.get("notify", {})
    nwhere = "notify"
    nmap = _want_mapping(nraw, nwhere, errs)

    def n_get(key, default, typ, minimum=None):
        v = nmap.get(key, default)
        ok = isinstance(v, typ)
        if ok and typ is int and isinstance(v, bool):
            ok = False
        if ok and minimum is not None:
            ok = v >= minimum
        if not ok:
            errs.append(
                f"{nwhere}.{key}: must be {typ.__name__}"
                + (f" >= {minimum}" if minimum is not None else "")
            )
            return default
        return v

    notify = NotifyConfig(
        enabled=n_get("enabled", True, bool),
        apprise_urls=n_get("apprise_urls", [], list),
        new_episodes=n_get("new_episodes", True, bool),
        errors=n_get("errors", True, bool),
        error_throttle_minutes=n_get("error_throttle_minutes", 60, int, minimum=0),
        max_episode_messages=n_get("max_episode_messages", 5, int, minimum=1),
    )
    if not all(isinstance(u, str) and u for u in notify.apprise_urls):
        errs.append("notify.apprise_urls: must be a list of strings")

    feeds: list[FeedConfig] = []
    if "feeds" not in top:
        errs.append("feeds: required")
    else:
        fraw = top["feeds"]
        if not isinstance(fraw, list) or not fraw:
            errs.append("feeds: must be a non-empty list")
        else:
            for i, f in enumerate(fraw):
                where = f"feeds[{i}]"
                fmap = _want_mapping(f, where, errs)
                if not fmap:
                    continue
                name = fmap.get("name")
                if not isinstance(name, str) or not name.strip():
                    errs.append(f"{where}.name: must be a non-empty string")
                    name = None
                url = fmap.get("url")
                if not isinstance(url, str) or not url.startswith(
                    ("http://", "https://")
                ):
                    errs.append(f"{where}.url: must be a string starting with http")
                    url = None
                slug = fmap.get("slug")
                if slug is None:
                    slug = slugify(name) if name else f"feed{i}"
                elif not isinstance(slug, str) or not slug.strip():
                    errs.append(f"{where}.slug: must be a non-empty string")
                    slug = slugify(name) if name else f"feed{i}"
                enabled = fmap.get("enabled", True)
                if not isinstance(enabled, bool):
                    errs.append(f"{where}.enabled: must be a boolean")
                    enabled = True
                playlist_latest = fmap.get("playlist_latest", DEFAULT_PLAYLIST_LATEST)
                if playlist_latest is not None and (
                    not isinstance(playlist_latest, str) or not playlist_latest
                ):
                    errs.append(f"{where}.playlist_latest: must be a string or null")
                    playlist_latest = DEFAULT_PLAYLIST_LATEST
                playlist_full = fmap.get("playlist_full")
                if playlist_full is not None and (
                    not isinstance(playlist_full, str) or not playlist_full
                ):
                    errs.append(f"{where}.playlist_full: must be a string or null")
                    playlist_full = None
                cut_ads = fmap.get("cut_ads")
                if cut_ads is not None and not isinstance(cut_ads, bool):
                    errs.append(f"{where}.cut_ads: must be a boolean or null")
                    cut_ads = None

                if name is None or url is None:
                    continue
                feeds.append(
                    FeedConfig(
                        name=name,
                        url=url,
                        slug=slug,
                        enabled=enabled,
                        playlist_latest=playlist_latest,
                        playlist_full=playlist_full,
                        cover_width=_pos_int(fmap, where, "cover_width", 600, errs),
                        embed_width=_pos_int(fmap, where, "embed_width", 350, errs),
                        max_episodes=_pos_int(fmap, where, "max_episodes", None, errs),
                        cut_ads=cut_ads,
                    )
                )

    if errs:
        raise ConfigError(errs)

    return Config(
        output_dir=_abs(output_dir_s, base),
        state_dir=state_dir,
        user_agent=user_agent,
        timeout=float(timeout_v),
        retries=int(retries),
        notify=notify,
        ads=ads,
        feeds=feeds,
    )
