"""yt-dlp-compatible download archive and atomic HTTP downloads."""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

import requests

_PERMISSION_ERRNOS = frozenset({errno.EACCES, errno.EROFS, errno.EPERM})
_NO_RETRY_4XX = tuple(range(400, 500))
_RETRY_4XX = (408, 429)
_BACKOFF = (1.0, 4.0)


class DownloadError(Exception):
    pass


class DownloadHTTPError(DownloadError):
    def __init__(self, url: str, status: int):
        self.url = url
        self.status = status
        detail = f"HTTP {status}"
        if status == 403:
            detail += " (permission denied by server)"
        super().__init__(f"{url}: {detail}")


class PermissionDeniedError(DownloadError):
    def __init__(self, path):
        self.path = str(path)
        super().__init__(f"permission denied: {self.path}")


class Archive:
    """Per-feed archive.txt; yt-dlp format (`yt-dlp <id>` legacy lines, bare ids written)."""

    def __init__(self, directory):
        self.path = Path(directory) / "archive.txt"
        self._ids: set[str] = set()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno in _PERMISSION_ERRNOS:
                raise PermissionDeniedError(self.path) from exc
            raise
        ids: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("yt-dlp "):
                line = line[len("yt-dlp ") :].strip()
            if line:
                ids.add(line)
        self._ids = ids

    def contains(self, ep_id: str) -> bool:
        return ep_id in self._ids

    def add(self, ep_id: str) -> None:
        if ep_id in self._ids:
            return
        self._ids.add(ep_id)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(f"{ep_id}\n")
        except OSError as exc:
            if exc.errno in _PERMISSION_ERRNOS:
                raise PermissionDeniedError(self.path) from exc
            raise

    @property
    def ids(self) -> set[str]:
        return set(self._ids)


def download_file(url: str, dest, cfg, session: requests.Session | None = None) -> None:
    """Stream `url` to `dest` atomically via a .part file; retried per cfg.retries."""
    sess = session or requests.Session()
    attempts = max(1, int(cfg.retries) + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
        try:
            _download_once(url, dest, cfg, sess)
            return
        except PermissionDeniedError:
            raise
        except DownloadHTTPError as exc:
            if exc.status in _NO_RETRY_4XX and exc.status not in _RETRY_4XX:
                raise
            last_exc = exc
        except requests.exceptions.RequestException as exc:
            last_exc = exc
    raise (
        last_exc
        if isinstance(last_exc, DownloadError)
        else DownloadError(f"{url}: {last_exc}")
    )


def _download_once(url: str, dest, cfg, sess: requests.Session) -> None:
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with sess.get(
            url,
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.timeout,
            stream=True,
        ) as resp:
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                raise DownloadHTTPError(url, resp.status_code) from exc
            try:
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=131072):
                        _guarded(fh.write, part, chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                if exc.errno in _PERMISSION_ERRNOS:
                    raise PermissionDeniedError(part) from exc
                raise DownloadError(f"{url}: {exc}") from exc
        _guarded(os.replace, dest, part, dest)
    finally:
        _unlink_quiet(part)


def _guarded(fn, path, *args):
    try:
        return fn(*args)
    except OSError as exc:
        if exc.errno in _PERMISSION_ERRNOS:
            raise PermissionDeniedError(path) from exc
        raise


def _unlink_quiet(part: Path) -> None:
    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass
