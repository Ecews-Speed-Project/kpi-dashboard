"""Serve live/ at http://localhost:8765 so the dashboard reloads itself after
every update (it polls live/version.json once a minute).

Sign-in: every visitor first gives their email address on /login. Only addresses
allowed in settings.yaml (access: allowed_domains / allowed_emails) get in; the
browser then keeps a signed session cookie for access.session_days. Sign-ins are
written to logs/access.log. (The address is not verified by a code or password:
this keeps casual visitors out; use the organisation's single sign-on when hosting.)

Compression: browsers that accept gzip get the precompressed live/index.html.gz
(written by every build, about 7x smaller). Caching: the browser keeps its copy
and checks it on each visit (ETag); an unchanged dashboard costs a tiny "304 Not
Modified" reply, a new build is downloaded once. version.json is never cached.

    python serve_dashboard.py          # this computer only
    python serve_dashboard.py --lan    # also reachable from the office network
                                       # at http://<this-computer's-IP>:8765
    python serve_dashboard.py --lan --port 5100 --no-browser    # on a server, another port
"""
from __future__ import annotations

import base64
import datetime as dt
import email.utils
import functools
import hashlib
import hmac
import html
import http.cookies
import http.server
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
cfg = yaml.safe_load(open(ROOT / "config" / "settings.yaml", encoding="utf-8"))
LIVE = ROOT / cfg.get("paths", {}).get("live", "live")
LOGS = ROOT / cfg.get("paths", {}).get("logs", "logs")
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ.get("DASH_PORT", 8765))
ACCESS = cfg.get("access") or {}
DOMAINS = [d.lower().lstrip("@") for d in ACCESS.get("allowed_domains", ["ecews.org"])]
EMAILS = {e.lower() for e in ACCESS.get("allowed_emails", [])}
DAYS = int(ACCESS.get("session_days", 30))
COOKIE = "speed_session"
LOGO = ROOT / "dashboard" / "assets" / "ecews_logo.png"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _secret() -> bytes:
    """Signing key for session cookies: DASH_SECRET in .env, else a random key kept in data/."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DASH_SECRET="):
                return line.split("=", 1)[1].strip().encode()
    f = ROOT / "data" / ".dash_secret"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


SECRET = _secret()


def allowed(addr: str) -> bool:
    addr = addr.strip().lower()
    return bool(EMAIL_RE.match(addr)) and (addr in EMAILS or addr.rsplit("@", 1)[1] in DOMAINS)


def first_name(addr: str) -> str:
    """jane.doe@ecews.org -> Jane"""
    part = re.split(r"[._\-+]", addr.split("@")[0])[0]
    part = re.sub(r"\d+", "", part) or addr.split("@")[0]
    return part[:1].upper() + part[1:].lower()


def sign(addr: str) -> str:
    body = base64.urlsafe_b64encode(f"{addr}|{int(time.time()) + DAYS * 86400}".encode()).decode().rstrip("=")
    return body + "." + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()


def verify(token: str) -> str | None:
    try:
        body, mac = token.rsplit(".", 1)
        if not hmac.compare_digest(mac, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()):
            return None
        addr, exp = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().rsplit("|", 1)
        return addr if int(exp) > time.time() and allowed(addr) else None
    except Exception:
        return None


def log_access(addr: str, ip: str, outcome: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "access.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}\t{outcome}\t{addr}\t{ip}\n")


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SPEED KPI Dashboard - sign in</title>
<style>
:root{--cloth:#1F3A2E;--gold:#C9A55A;--cream:#FBF5E4;--ink:#2A2418;--ink3:#6B5F49;--bad:#9A2F22}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;min-height:100vh;display:grid;place-items:center;color:var(--ink);font-family:Georgia,"EB Garamond",serif;padding:16px;
  background:#22392E;overflow:hidden}
.slides{position:fixed;inset:0;z-index:0}
.slides div{position:absolute;inset:-4%;background:center/cover no-repeat;opacity:0;transition:opacity 2.4s ease;transform:scale(1.02);filter:blur(3px) saturate(.85)}
.slides div.on{opacity:1}
@media (prefers-reduced-motion:no-preference){.slides div.on{animation:drift 16s ease-out forwards}}
@keyframes drift{from{transform:scale(1.02) translate(0,0)}to{transform:scale(1.10) translate(-1.2%,-.8%)}}
.veil{position:fixed;inset:0;z-index:1;background:linear-gradient(160deg,rgba(251,245,228,.42),rgba(31,58,46,.50) 55%,rgba(20,38,30,.72))}
.card{position:relative;z-index:2;width:100%;max-width:410px;background:rgba(251,245,228,.93);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);
  border:1px solid rgba(201,165,90,.7);border-radius:18px;padding:30px 28px 24px;box-shadow:0 30px 60px -28px rgba(0,0,0,.55);animation:rise .9s ease-out both}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.logo{background:var(--cream);border:1px solid var(--gold);border-radius:8px;padding:6px 10px}.logo img{height:30px;display:block}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:var(--cloth);background:rgba(31,58,46,.08);border-radius:20px;padding:4px 10px}
h1{font-size:27px;margin:0 0 6px;color:var(--cloth);font-weight:600}
p.lead{margin:0 0 20px;color:var(--ink3);font-style:italic;font-size:16px}
label{display:flex;align-items:center;gap:6px;font-size:12.5px;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:7px;color:var(--cloth)}
.field{position:relative}.field svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);opacity:.6}
input{width:100%;font:inherit;font-size:17px;padding:12px 12px 12px 40px;border-radius:10px;border:1px solid rgba(31,58,46,.25);background:#fff;color:var(--ink);transition:border-color .2s,box-shadow .2s}
input:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 4px rgba(201,165,90,.25)}
button{margin-top:16px;width:100%;display:flex;justify-content:center;align-items:center;gap:8px;font:inherit;font-size:16.5px;padding:12px;border:0;border-radius:10px;
  background:linear-gradient(180deg,#2B4C3C,#1F3A2E);color:var(--cream);font-weight:600;cursor:pointer;transition:transform .15s,box-shadow .2s;box-shadow:0 8px 18px -10px rgba(31,58,46,.8)}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px -10px rgba(31,58,46,.9)}
.err{margin-top:14px;background:#F6DCD6;color:var(--bad);border-radius:8px;padding:9px 11px;font-size:15px}
.foot{display:flex;align-items:center;gap:6px;margin-top:18px;color:var(--ink3);font-size:13px}
.caption{position:fixed;z-index:2;left:20px;bottom:16px;color:var(--cream);font-style:italic;font-size:14px;opacity:.9;text-shadow:0 1px 6px rgba(0,0,0,.5);max-width:70vw}
.dots{position:fixed;z-index:2;right:20px;bottom:20px;display:flex;gap:7px}.dots i{width:7px;height:7px;border-radius:50%;background:var(--cream);opacity:.4;transition:opacity .6s}.dots i.on{opacity:1}
@media (max-width:480px){.card{padding:24px 18px 20px;border-radius:14px}h1{font-size:23px}.caption{display:none}}
</style></head><body>
<div class="slides">__SLIDES__</div><div class="veil"></div>
<form class="card" method="post" action="/login">
 <div class="brand"><div class="logo"><img src="/logo.png" alt="ECEWS"></div>
  <span class="badge"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18M6 16V10M11 16V6M16 16v-4M21 16V8"/></svg>SPEED project</span></div>
 <h1>Welcome back</h1><p class="lead">Sign in with your ECEWS email to open the KPI dashboard.</p>
 <label for="email"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 6.5l8.5 6.5 8.5-6.5"/></svg>Email address</label>
 <div class="field"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1F3A2E" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8.2" r="3.8"/><path d="M4.6 20.4c.8-4 3.8-6.4 7.4-6.4s6.6 2.4 7.4 6.4"/></svg>
 <input id="email" name="email" type="email" autocomplete="email" required autofocus value="__VALUE__" placeholder="name@ecews.org"></div>
 <button type="submit">Continue <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5l7 7-7 7M20.6 12H3"/></svg></button>__ERROR__
 <div class="foot"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.8l7.6 3v5.8c0 4.8-3.2 8.2-7.6 9.6-4.4-1.4-7.6-4.8-7.6-9.6V5.8z"/></svg>For ECEWS staff and approved partners only.</div>
</form>
<div class="caption" id="cap"></div><div class="dots" id="dots"></div>
<script>
(()=>{const s=[...document.querySelectorAll(".slides div")], cap=document.getElementById("cap"), dots=document.getElementById("dots");
 if(!s.length) return; dots.innerHTML=s.map(()=>"<i></i>").join(""); const d=[...dots.children];
 let k=Math.floor(Math.random()*s.length);
 const show=()=>{s.forEach((x,i)=>x.classList.toggle("on",i===k)); d.forEach((x,i)=>x.classList.toggle("on",i===k)); cap.textContent=s[k].dataset.cap||"";};
 show(); if(s.length>1) setInterval(()=>{k=(k+1)%s.length; show();}, 9000);})();
</script></body></html>"""

PHOTOS = ROOT / "dashboard" / "assets" / "login"
CAPTIONS = {"speed-project": "The SPEED team reviewing programme data", "063A10521": "Mothers and children at the heart of PMTCT",
            "health-image-2": "Community HIV testing outreach", "slider-image-2": "HIV testing services at the facility"}


def slides() -> str:
    """Background photos for the sign-in page: every .webp / .jpg in dashboard/assets/login (add or remove files to change them)."""
    out = []
    for f in sorted(PHOTOS.glob("*.webp")) + sorted(PHOTOS.glob("*.jpg")):
        cap = next((c for k, c in CAPTIONS.items() if f.name.startswith(k)), "")
        out.append(f'<div style="background-image:url(/photos/{urllib.parse.quote(f.name)})" data-cap="{html.escape(cap, quote=True)}"></div>')
    return "".join(out)


# The dashboard is large (about 7 MB compressed, 50 MB unpacked). It is read from disk (and unpacked, for a
# client that does not accept gzip) once per build and kept in memory: every request shares that one copy.
# At most DASH_MAX_DOWNLOADS dashboards are sent at the same time; the rest wait their turn (up to two minutes,
# then "503, try again"), so a burst of reloads after an update cannot exhaust the server's memory.
_BODY: dict = {}
_BODY_LOCK = threading.Lock()
_SENDS = threading.BoundedSemaphore(int(os.environ.get("DASH_MAX_DOWNLOADS", 8)))


def dashboard_bytes(src: Path, st: os.stat_result, unpack: bool) -> bytes:
    """The bytes to send for this build, read (and unpacked) once and shared by every request."""
    import gzip
    key = (str(src), st.st_mtime_ns, st.st_size, unpack)
    with _BODY_LOCK:
        body = _BODY.get(key)
        if body is None:
            for k in [k for k in _BODY if k[0] == key[0] and k[1:3] != key[1:3]]:
                del _BODY[k]                                     # an earlier build of the same file
            raw = src.read_bytes()
            body = _BODY[key] = gzip.decompress(raw) if unpack else raw
        return body


class Quiet(http.server.SimpleHTTPRequestHandler):
    timeout = 120          # a stalled connection is dropped instead of holding a thread and a download slot
    def end_headers(self):
        if not getattr(self, "_cached", False):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ---------- sign-in ----------
    def _user(self) -> str | None:
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return verify(c[COOKIE].value) if COOKIE in c else None

    def _redirect(self, where: str, cookie: str | None = None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _page(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _login_form(self, value: str = "", error: str = "", status: int = 200):
        err = f'<div class="err">&#9888; {html.escape(error)}</div>' if error else ""
        self._page(LOGIN_PAGE.replace("__SLIDES__", slides()).replace("__VALUE__", html.escape(value, quote=True))
                   .replace("__ERROR__", err), status)

    def _route(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def do_POST(self):
        self._cached = False
        if self._route() != "/login":
            return self.send_error(404)
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
        addr = (form.get("email") or [""])[0].strip().lower()
        ip = self.headers.get("X-Forwarded-For", self.client_address[0])
        if not EMAIL_RE.match(addr):
            return self._login_form(addr, "Please enter a valid email address.", 400)
        if not allowed(addr):
            log_access(addr, ip, "denied")
            return self._login_form(addr, "This address does not have access. Use your ECEWS email, or ask the M&E team to add you.", 403)
        log_access(addr, ip, "signed-in")
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
        self._redirect("/index.html#overview",
                       f"{COOKIE}={sign(addr)}; Path=/; Max-Age={DAYS * 86400}; HttpOnly; SameSite=Lax{secure}")

    def _gate(self, head: bool) -> bool:
        """Answer sign-in routes; False when the request may go on to the dashboard files."""
        route = self._route()
        if route == "/login":
            if self._user():
                self._redirect("/index.html#overview")
            else:
                self._login_form()
            return True
        if route == "/logout":
            self._redirect("/login", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            return True
        photo = PHOTOS / urllib.parse.unquote(route[len("/photos/"):]) if route.startswith("/photos/") else None
        if photo is not None and photo.parent == PHOTOS and photo.suffix in (".webp", ".jpg") and photo.is_file():
            data = photo.read_bytes()
            self._cached = True
            self.send_response(200)
            self.send_header("Content-Type", "image/webp" if photo.suffix == ".webp" else "image/jpeg")
            self.send_header("Cache-Control", "public, max-age=604800")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)
            return True
        if route == "/logo.png" and LOGO.exists():
            data = LOGO.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)
            return True
        user = self._user()
        if not user:
            if route in ("/", "/index.html") or route.endswith(".html"):
                self._redirect("/login")
            else:
                self.send_error(401, "Sign in at /login")
            return True
        if route == "/whoami":
            self._page(json.dumps({"email": user, "name": first_name(user)}), ctype="application/json")
            return True
        return False

    # ---------- dashboard files ----------
    def do_GET(self):
        self._cached = False
        if self._gate(head=False):
            return
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.name.endswith(".gz") or path.name == ".dash_secret":
            return self.send_error(404)
        if path.suffix == ".html" and (path.is_file() or path.with_name(path.name + ".gz").is_file()):
            return self._send_html(path, head=False)
        return super().do_GET()

    def do_HEAD(self):
        self._cached = False
        if self._gate(head=True):
            return
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.suffix == ".html" and (path.is_file() or path.with_name(path.name + ".gz").is_file()):
            return self._send_html(path, head=True)
        return super().do_HEAD()

    def _send_html(self, path: Path, head: bool):
        """The dashboard: gzip when accepted, cached by the browser and revalidated on every visit.
        Works with index.html and index.html.gz side by side, or with the .gz alone (as published to the
        GitHub 'live' branch): then a browser that does not accept gzip gets it unpacked (once per build)."""
        gz = path.with_name(path.name + ".gz")
        gz_only = not path.is_file()
        accepts = "gzip" in self.headers.get("Accept-Encoding", "")
        use_gz = accepts and gz.is_file() and (gz_only or gz.stat().st_mtime >= path.stat().st_mtime - 5)
        st = (gz if gz_only else path).stat()
        etag = f'"{int(st.st_mtime)}-{st.st_size}{"-gz" if use_gz else ""}"'
        self._cached = True
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.send_header("Vary", "Accept-Encoding, Cookie")
            self.end_headers()
            return
        if not head and not _SENDS.acquire(timeout=120):
            busy = (b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="20">'
                    b'<title>SPEED dashboard</title><p style="font:16px sans-serif;margin:2em">Many people are opening '
                    b'the dashboard right now. This page tries again in 20 seconds.</p>')
            self.send_response(503)                              # too many downloads at once: the page retries itself
            self.send_header("Retry-After", "20")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(busy)))
            self.end_headers()
            self.wfile.write(busy)
            return
        try:
            self._send_body(path, gz, gz_only, use_gz, st, etag, head)
        finally:
            if not head:
                _SENDS.release()

    def _send_body(self, path, gz, gz_only, use_gz, st, etag, head):
        body = dashboard_bytes(gz if (use_gz or gz_only) else path, st if not use_gz or gz_only else gz.stat(), unpack=gz_only and not use_gz)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        self.send_header("Cache-Control", "private, no-cache")   # this browser only; check it is still current
        self.send_header("Vary", "Accept-Encoding, Cookie")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    LIVE.mkdir(parents=True, exist_ok=True)
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    show = "--no-browser" not in sys.argv          # the logon task starts it silently
    url = f"http://localhost:{PORT}/login"
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
            print(f"Office network: http://{socket.gethostbyname(socket.gethostname())}:{PORT}/login")
        print("Leave this window open. The page reloads itself after each update. Ctrl+C to stop.")
        if show:
            webbrowser.open(url)
        srv.serve_forever()


if __name__ == "__main__":
    main()
