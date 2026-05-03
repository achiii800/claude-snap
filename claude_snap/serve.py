"""
Local-mode HTTP server for `claude-snap chat`.

Spawns a localhost HTTP server that:
  - Serves the bundled PWA assets from claude_snap/web/.
  - Rewrites the served index.html so the page knows it's in local mode
    (CSP relaxed to allow same-origin fetches; upgrade-insecure-requests
    stripped so HTTP localhost works).
  - Exposes /api/messages — proxies to api.anthropic.com using the
    ANTHROPIC_API_KEY env var. The key never reaches the browser.
  - Exposes /api/session — returns the user-supplied snap.jsonl content
    so the page can autoload it without the user re-uploading anything.
  - Binds to 127.0.0.1 only. Refuses requests with a Host header that
    isn't localhost / 127.0.0.1.

The trust model is: you ran `claude-snap chat`, you control this process,
the credential lives in your shell env. The browser only ever talks to
your own localhost.
"""

from __future__ import annotations
import http.server
import json
import os
import re
import socket
import socketserver
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
PROXY_TIMEOUT_S = 120
MAX_REQUEST_BYTES = 16 * 1024 * 1024   # cap incoming POSTs at 16 MiB
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def _web_dir() -> Path:
    """Locate the bundled PWA assets. Works in both source layout and
    installed wheel — relies on the package data being shipped alongside."""
    return Path(__file__).resolve().parent / "web"


def _rewrite_index_html(raw: str) -> str:
    """
    Adapt the hosted index.html for local mode:
      - Strip `upgrade-insecure-requests` so http://localhost works.
      - Add 'self' to connect-src so /api/* fetches are allowed.
    Falls back to original CSP if the regex doesn't match (defensive).
    """
    csp_pattern = re.compile(
        r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(")',
        re.IGNORECASE,
    )

    def repl(m: re.Match) -> str:
        prefix, csp, suffix = m.group(1), m.group(2), m.group(3)
        # Strip upgrade-insecure-requests (with or without trailing semicolon).
        csp = re.sub(r'\s*upgrade-insecure-requests\s*;?', '', csp).strip()
        # Add 'self' to connect-src.
        if "connect-src" in csp:
            csp = re.sub(
                r"connect-src\s+([^;]*)",
                lambda mm: "connect-src 'self' " + mm.group(1).strip(),
                csp,
                count=1,
            )
        else:
            csp = csp.rstrip("; ") + "; connect-src 'self'"
        # Ensure single trailing semicolon stripped for cleanliness.
        csp = re.sub(r"\s+", " ", csp).strip().rstrip(";")
        return prefix + csp + suffix

    return csp_pattern.sub(repl, raw, count=1)


class _Handler(http.server.SimpleHTTPRequestHandler):
    """
    Custom request handler. State is shared via class-level attributes
    set before serve_forever() is called.
    """

    web_dir: Path = _web_dir()
    api_key: str = ""
    session_text: Optional[str] = None
    session_filename: str = "session.snap.jsonl"

    # ---------- helpers ----------

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "").lower()
        # Strip port.
        if ":" in host and not host.startswith("["):
            host = host.rsplit(":", 1)[0]
        elif host.startswith("["):
            # IPv6: [::1]:port
            host = host.split("]")[0] + "]"
        return host in ALLOWED_HOSTS

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "not_found"})
            return
        ext = path.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png":  "image/png",
            ".md":   "text/markdown; charset=utf-8",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- routing ----------

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if not self._host_ok():
            self._send_json(403, {"error": "forbidden_host"})
            return

        path = self.path.split("?", 1)[0]

        if path == "/" or path == "/index.html":
            try:
                raw = (self.web_dir / "index.html").read_text(encoding="utf-8")
            except OSError:
                self._send_json(500, {"error": "missing_index"})
                return
            self._send_text(200, _rewrite_index_html(raw),
                            "text/html; charset=utf-8")
            return

        if path == "/api/session":
            if self.session_text is None:
                self._send_json(404, {"error": "no_session_loaded"})
                return
            self._send_text(
                200,
                self.session_text,
                "application/x-ndjson; charset=utf-8",
            )
            return

        if path == "/api/health":
            self._send_json(200, {"ok": True, "mode": "local"})
            return

        # Static asset under /web/...  Reject anything that escapes the dir.
        rel = path.lstrip("/")
        if not rel:
            self._send_json(404, {"error": "not_found"})
            return
        candidate = (self.web_dir / rel).resolve()
        try:
            candidate.relative_to(self.web_dir.resolve())
        except ValueError:
            self._send_json(403, {"error": "forbidden_path"})
            return
        if not candidate.is_file():
            self._send_json(404, {"error": "not_found"})
            return
        self._send_file(candidate)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "forbidden_host"})
            return

        if self.path != "/api/messages":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "bad_content_length"})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "request_too_large"})
            return

        try:
            raw = self.rfile.read(length)
        except OSError:
            self._send_json(400, {"error": "read_failed"})
            return

        # Validate the body parses as JSON; pass through to upstream verbatim.
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=PROXY_TIMEOUT_S) as resp:
                body = resp.read()
                status = resp.status
                ctype = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body = e.read()
            status = e.code
            ctype = e.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as e:
            self._send_json(502, {"error": "upstream_unreachable", "detail": str(e.reason)})
            return
        except TimeoutError:
            self._send_json(504, {"error": "upstream_timeout"})
            return

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Quieter logging — the default writes a line per request to stderr.
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if os.environ.get("CLAUDE_SNAP_VERBOSE") == "1":
            super().log_message(format, *args)


def _pick_port() -> int:
    """Bind to 127.0.0.1:0 to let the OS pick a free port, then close."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LocalhostOnlyServer(socketserver.TCPServer):
    allow_reuse_address = True
    # Bind explicitly to 127.0.0.1, never 0.0.0.0.
    address_family = socket.AF_INET


def serve(
    snap_path: Optional[str] = None,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    """
    Run the local chat server until interrupted. Returns 0 on clean exit.

    Args:
      snap_path:    optional path to a .snap.jsonl (or raw .jsonl); if
                    given, the page autoloads it via /api/session.
      port:         0 picks a random free port (recommended). A specific
                    port can be passed but if it's busy, serve() raises.
      open_browser: if True, opens the user's default browser to the URL.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.stderr.write(
            "claude-snap chat: ANTHROPIC_API_KEY is not set.\n"
            "  set it in your shell:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  get a key at:           https://console.anthropic.com/\n"
        )
        return 2

    web_dir = _web_dir()
    if not (web_dir / "index.html").is_file():
        sys.stderr.write(
            "claude-snap chat: PWA assets not found at "
            f"{web_dir}. Reinstall claude-snap (this is a packaging bug).\n"
        )
        return 3

    session_text: Optional[str] = None
    session_filename = "session.snap.jsonl"
    if snap_path:
        p = Path(snap_path).expanduser()
        if not p.is_file():
            sys.stderr.write(f"claude-snap chat: file not found: {p}\n")
            return 4
        try:
            session_text = p.read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"claude-snap chat: failed to read {p}: {e}\n")
            return 5
        session_filename = p.name

    bind_port = port if port > 0 else _pick_port()

    _Handler.web_dir = web_dir
    _Handler.api_key = api_key
    _Handler.session_text = session_text
    _Handler.session_filename = session_filename

    server = _LocalhostOnlyServer(("127.0.0.1", bind_port), _Handler)
    url = f"http://127.0.0.1:{bind_port}/"

    print(f"claude-snap chat — local server at {url}")
    print(f"  API key: from $ANTHROPIC_API_KEY (browser never sees it)")
    if session_text is not None:
        print(f"  session: {snap_path} (autoload via /api/session)")
    print(f"  press Ctrl+C to stop")

    if open_browser:
        # Run in a thread so it doesn't block on first launch.
        threading.Thread(
            target=lambda: webbrowser.open(url), daemon=True
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nclaude-snap chat — stopped.")
    finally:
        server.server_close()

    return 0
