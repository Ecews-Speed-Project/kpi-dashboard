"""Serve live/ at http://localhost:8765 so the dashboard reloads itself after
every update (it polls live/version.json once a minute).

Compression: browsers that accept gzip get the precompressed live/index.html.gz
(written by every build, about 7x smaller). Caching: the browser keeps its copy
and checks it on each visit (ETag); an unchanged dashboard costs a tiny "304 Not
Modified" reply, a new build is downloaded once. version.json is never cached.

    python serve_dashboard.py          # this computer only
    python serve_dashboard.py --lan    # also reachable from the office network
                                       # at http://<this-computer's-IP>:8765
"""
from __future__ import annotations

import email.utils
import functools
import http.server
import socket
import sys
import webbrowser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
cfg = yaml.safe_load(open(ROOT / "config" / "settings.yaml", encoding="utf-8"))
LIVE = ROOT / cfg.get("paths", {}).get("live", "live")
PORT = 8765


class Quiet(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        if not getattr(self, "_cached", False):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        self._cached = False
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.suffix == ".html" and path.is_file():
            return self._send_html(path, head=False)
        return super().do_GET()

    def do_HEAD(self):
        self._cached = False
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.suffix == ".html" and path.is_file():
            return self._send_html(path, head=True)
        return super().do_HEAD()

    def _send_html(self, path: Path, head: bool):
        """The dashboard: gzip when accepted, cached by the browser and revalidated on every visit."""
        gz = path.with_name(path.name + ".gz")
        use_gz = "gzip" in self.headers.get("Accept-Encoding", "") and gz.is_file()             and gz.stat().st_mtime >= path.stat().st_mtime - 5
        st = path.stat()
        etag = f'"{int(st.st_mtime)}-{st.st_size}{"-gz" if use_gz else ""}"'
        self._cached = True
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        body = (gz if use_gz else path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        self.send_header("Cache-Control", "no-cache")     # keep a copy, but check it is still current
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    LIVE.mkdir(parents=True, exist_ok=True)
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    show = "--no-browser" not in sys.argv          # the logon task starts it silently
    url = f"http://localhost:{PORT}/index.html"
    handler = functools.partial(Quiet, directory=str(LIVE))
    try:
        srv = http.server.ThreadingHTTPServer((host, PORT), handler)
    except OSError:                                 # already running (e.g. started at logon): just open it
        if show:
            webbrowser.open(url)
        return
    with srv:
        print(f"Serving {LIVE} at {url}")
        if host == "0.0.0.0":
            print(f"Office network: http://{socket.gethostbyname(socket.gethostname())}:{PORT}/index.html")
        print("Leave this window open. The page reloads itself after each update. Ctrl+C to stop.")
        if show:
            webbrowser.open(url)
        srv.serve_forever()


if __name__ == "__main__":
    main()
