"""Shared fixtures: local HTTP server serving a real RSS feed, mp3s and covers."""

from __future__ import annotations

import http.server
import io
import re
import socketserver
import threading

import pytest
from PIL import Image

# MPEG-1 Layer III 64kbps 44.1kHz mono frame: 208 bytes; 6 frames ≈ tiny valid mp3
_MP3_FRAME = bytes([0xFF, 0xFB, 0x50, 0xC0]) + b"\x00" * 204
MP3_BYTES = _MP3_FRAME * 6

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Testpodcast</title>
  <itunes:image href="{base}/cover.png"/>
  <item>
    <title>Folge Eins</title>
    <pubDate>Tue, 21 May 2024 08:00:00 +0000</pubDate>
    <enclosure url="{base}/ep1.mp3" type="audio/mpeg" length="1248"/>
    <itunes:image href="{base}/cover.png"/>
  </item>
  <item>
    <title>Fölge Zwoä</title>
    <pubDate>Wed, 22 May 2024 08:00:00 +0000</pubDate>
    <enclosure url="{base}/ep2.mp3" type="audio/mpeg" length="1248"/>
  </item>
</channel>
</rss>
"""


def make_feed_xml(base: str) -> bytes:
    return FEED_XML.format(base=base).encode("utf-8")


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    """Serves fixture files plus control paths: /status/<code>, /forbidden, /captured (POST)."""

    server: FixtureServer

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        m = re.fullmatch(r"/status/(\d+)", path)
        if m:
            code = int(m.group(1))
            self.send_error(code, "forced")
            return
        if path == "/forbidden":
            self.send_error(403, "forbidden")
            return
        body = self.server.bodies.get(path.lstrip("/"))
        if body is None:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        self.server.captured.append((self.path, dict(self.headers), payload))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class FixtureServer(socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture(scope="session")
def fixture_server():
    """Session-wide HTTP server on 127.0.0.1:0. URL base available as .base_url."""
    bodies: dict[str, bytes] = {
        "ep1.mp3": MP3_BYTES,
        "ep2.mp3": _MP3_FRAME * 8,
    }
    srv = FixtureServer(("127.0.0.1", 0), FixtureHandler)
    srv.bodies = bodies  # type: ignore[attr-defined]
    srv.captured = []  # type: ignore[attr-defined]
    host, port = srv.server_address
    base = f"http://{host}:{port}"
    buf = io.BytesIO()
    Image.new("RGB", (120, 120), "red").save(buf, format="PNG")
    bodies["cover.png"] = buf.getvalue()
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), "blue").save(buf, format="JPEG")
    bodies["cover.jpg"] = buf.getvalue()
    bodies["feed.xml"] = make_feed_xml(base)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base_url = base  # type: ignore[attr-defined]
    yield srv
    srv.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def captured(fixture_server):
    fixture_server.captured.clear()  # type: ignore[attr-defined]
    yield fixture_server.captured  # type: ignore[attr-defined]


class FakeSender:
    """Collects (title, body) tuples; injected as Notifier(sender=...)."""

    def __init__(self):
        self.msgs: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> bool:
        self.msgs.append((title, body))
        return True
