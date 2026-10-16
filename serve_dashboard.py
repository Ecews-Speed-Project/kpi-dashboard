"""Serve live/ at http://localhost:8765 so the dashboard reloads itself after
every update (it polls live/version.json once a minute).

    python serve_dashboard.py          # this computer only
    python serve_dashboard.py --lan    # also reachable from the office network
                                       # at http://<this-computer's-IP>:8765
"""
from __future__ import annotations

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
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

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
