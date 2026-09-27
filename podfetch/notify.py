"""apprise-backed notifications with per-error throttling."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Error event kinds surfaced in notifications (exact strings).
FEED_FETCH_FAILED = "feed fetch failed"
MP3_DOWNLOAD_FAILED = "mp3 download failed"
PERMISSION_DENIED = "permission denied"
COVER_FAILED = "cover failed"
TAG_FAILED = "tag failed"
CONFIG_ERROR = "config error"
STATE_FILE_ERROR = "state file error"
AD_CUT_FAILED = "ad cut failed"


@dataclass(frozen=True)
class ErrorEvent:
    feed: str
    kind: str
    detail: str


class _Sender:
    """Protocol: send(title, body) -> bool."""

    def send(self, title: str, body: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class _AppriseSender(_Sender):
    def __init__(self, urls: list[str]):
        import apprise

        self.ap = apprise.Apprise()
        self.rejected: list[str] = []
        for url in urls:
            if not self.ap.add(url):
                self.rejected.append(url)

    def send(self, title: str, body: str) -> bool:
        return bool(self.ap.notify(title=title, body=body))


class Notifier:
    """Send notifications through apprise (or an injected sender in tests).

    A dead notification endpoint must never crash a fetch run: all apprise
    exceptions are swallowed to stderr."""

    def __init__(self, cfg=None, sender: _Sender | None = None):
        self._explicit = sender is not None
        self._sender = sender
        self._notify_cfg = cfg.notify if cfg is not None else None
        self._state_path: Path | None = None
        if cfg is not None:
            self._state_path = cfg.state_dir / "notify.json"
        if (
            sender is None
            and cfg is not None
            and cfg.notify.enabled
            and cfg.notify.apprise_urls
        ):
            try:
                self._sender = _AppriseSender(cfg.notify.apprise_urls)
                for url in self._sender.rejected:  # type: ignore[union-attr]
                    print(
                        f"podfetch: ignoring invalid apprise URL: {url}",
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001 - never crash fetch on broken notifier
                print(f"podfetch: apprise init failed: {exc}", file=sys.stderr)
                self._sender = None

    @property
    def enabled(self) -> bool:
        return self._sender is not None

    def new_episode(self, feed_name: str, ep_title: str, file_name: str) -> None:
        if not self._active("new_episodes"):
            return
        self._send(
            f"podfetch: new episode — {feed_name}",
            f"New episode: {ep_title}\nFile: {file_name}",
        )

    def new_episode_summary(self, feed_name: str, count: int) -> None:
        if not self._active("new_episodes"):
            return
        self._send(
            f"podfetch: new episodes — {feed_name}", f"{count} new episodes downloaded."
        )

    def errors(self, events: list[ErrorEvent]) -> None:
        if not self._active("errors") or not events:
            return
        window = self._window_seconds()
        state = self._load_state()
        now = time.time()
        fresh: list[ErrorEvent] = []
        for ev in events:
            key = f"{ev.feed}|{ev.kind}|{ev.detail}"
            last = state.get(key)
            if isinstance(last, (int, float)) and now - last < window:
                continue
            fresh.append(ev)
            state[key] = now
        self._save_state(state)
        if not fresh:
            return
        body = "\n".join(f"{ev.feed}: {ev.kind} ({ev.detail})" for ev in fresh)
        self._send(f"podfetch: {len(fresh)} problems", body)

    def test(self, body: str, title: str = "podfetch") -> bool:
        return self._send(title, body)

    def _active(self, flag: str) -> bool:
        if self._sender is None:
            return False
        if self._explicit:
            return True
        assert self._notify_cfg is not None
        return getattr(self._notify_cfg, flag, True)

    def _send(self, title: str, body: str) -> bool:
        if self._sender is None:
            return False
        try:
            return bool(self._sender.send(title, body))
        except Exception as exc:  # noqa: BLE001 - dead endpoint must not crash run
            print(f"podfetch: notification failed: {exc}", file=sys.stderr)
            return False

    def _window_seconds(self) -> float:
        if self._notify_cfg is None:
            return 3600.0
        return float(self._notify_cfg.error_throttle_minutes) * 60.0

    def _load_state(self) -> dict:
        if self._state_path is None:
            return {}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        except OSError as exc:
            print(
                f"podfetch: state file error: {self._state_path}: {exc}",
                file=sys.stderr,
            )
            return {}

    def _save_state(self, state: dict) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            print(
                f"podfetch: state file error: {self._state_path}: {exc}",
                file=sys.stderr,
            )
