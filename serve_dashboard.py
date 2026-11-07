"""Serve live/ at http://localhost:8765 so the dashboard reloads itself after
every update (it polls live/version.json once a minute).

Sign-in: every visitor gives their official email on /login. Only addresses allowed
in settings.yaml (access: allowed_domains / partner_names / allowed_emails) get in.
The first time, they also give their first name and unit (kept in data/users.json);
after that the email is enough. The session ends after access.session_minutes without
activity: the page reports activity to /session/alive, and background checks do not
count. Only the SI unit gets the Live data entry page. Sign-ins are written to
logs/access.log. (The address is not verified by a code or password: this keeps
casual visitors out; use the organisation's single sign-on when hosting.)

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
PARTNER_NAMES = {k.lower(): n for k, n in (ACCESS.get("partner_names") or {}).items()}     # text in the email domain -> partner
EMAILS = {e.lower() for e in ACCESS.get("allowed_emails", [])}
SESSION_SECONDS = int(ACCESS.get("session_minutes", 30)) * 60
LIVE_EMAILS = {e.lower() for e in ACCESS.get("live_emails") or []}       # optional: narrows Live data entry further
UNITS = ["CSU", "Lab", "PSCM", "SI", "HSS/CQI", "CCMP/VP", "Comms/IT", "Admin", "Programs", "Mgt", "Prevention"]
USERS_FILE = ROOT / "data" / "users.json"
_USERS_LOCK = threading.Lock()
COOKIE = "speed_s2"          # renamed so sessions from before the 30-minute rule end
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


def partner_of(addr: str) -> str:
    """The partner whose name appears in the email domain (the part after @), else ''."""
    domain = addr.rsplit("@", 1)[-1].lower()
    return next((n for k, n in PARTNER_NAMES.items() if k in domain), "")


def allowed(addr: str) -> bool:
    addr = addr.strip().lower()
    return bool(EMAIL_RE.match(addr)) and (addr in EMAILS or addr.rsplit("@", 1)[1] in DOMAINS or bool(partner_of(addr)))


def users() -> dict:
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def register(addr: str, name: str, unit: str, partner: str = "") -> None:
    """Keep who someone is after their first sign-in. Written whole, then swapped in, so a crash cannot damage it."""
    with _USERS_LOCK:
        u = users()
        u[addr] = {"first_name": name, "unit": unit, "partner": partner, "first_seen": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(u, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, USERS_FILE)


def can_live(addr: str) -> bool:
    return users().get(addr, {}).get("unit") == "SI" and (not LIVE_EMAILS or addr in LIVE_EMAILS)


def clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()[:40]


def first_name(addr: str) -> str:
    """jane.doe@ecews.org -> Jane"""
    part = re.split(r"[._\-+]", addr.split("@")[0])[0]
    part = re.sub(r"\d+", "", part) or addr.split("@")[0]
    return part[:1].upper() + part[1:].lower()


def sign(addr: str) -> str:
    body = base64.urlsafe_b64encode(f"{addr}|{int(time.time()) + SESSION_SECONDS}".encode()).decode().rstrip("=")
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


LOGIN_PAGE = (ROOT / "dashboard" / "login.html").read_text(encoding="utf-8")

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
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
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

    def _login_form(self, value: str = "", error: str = "", status: int = 200, first_time: bool = False, name: str = "", unit: str = ""):
        esc = lambda x: html.escape(x, quote=True)
        idle = "idle=1" in urllib.parse.urlsplit(self.path).query
        if first_time:
            partner = partner_of(value)
            opts = "".join(f'<option value="{esc(u)}"{" selected" if u == unit else ""}>{esc(u)}</option>' for u in UNITS)
            who = (f'<label>Unit</label><div class="fixed">Partners &middot; {esc(partner)}</div>' if partner else
                   f'<label for="unit">Unit</label><select id="unit" name="unit" required><option value="">Choose your unit</option>{opts}</select>')
            fields = (f'<label>Official email</label><div class="fixed">{esc(value)}</div><input type="hidden" name="email" value="{esc(value)}">'
                      f'<label for="first_name">First name</label><input id="first_name" name="first_name" autocomplete="given-name" required maxlength="40" autofocus value="{esc(name)}">{who}')
            words = dict(eyebrow="First sign-in", heading="Welcome", lead="Tell us who you are. Next time, your email is all you will need.", button="Enter the dashboard")
        else:
            fields = (f'<label for="email">Official email</label><input id="email" name="email" type="email" autocomplete="email" required autofocus value="{esc(value)}" placeholder="name@ecews.org">')
            words = dict(eyebrow="Monitoring &amp; evaluation", heading="Sign in", lead="Enter your official email address to continue.", button="Continue")
        page = LOGIN_PAGE
        for k, v in words.items():
            page = page.replace(f"__{k.upper()}__", v)
        notice = '<div class="note">You were signed out after 30 minutes without activity.</div>' if idle and not first_time else ""
        err = f'<div class="err" role="alert">{html.escape(error)}</div>' if error else ""
        self._page(page.replace("__SLIDES__", slides()).replace("__NOTICE__", notice).replace("__FIELDS__", fields).replace("__ERROR__", err), status)

    def _session_cookie(self, addr: str) -> str:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
        return f"{COOKIE}={sign(addr)}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Lax{secure}"

    def _route(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def do_POST(self):
        self._cached = False
        if self._route() == "/session/alive":        # the page saw the person active: 30 more minutes
            addr = self._user()
            if not addr:
                return self.send_error(401)
            self.send_response(204)
            self.send_header("Set-Cookie", self._session_cookie(addr))
            self.send_header("Content-Length", "0")
            return self.end_headers()
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
            return self._login_form(addr, "This address does not have access. Use your official email, or ask the SI team to add you.", 403)
        if addr not in users():
            one = lambda k: (form.get(k) or [""])[0].strip()
            name, unit = clean_name(one("first_name")), one("unit")
            partner = partner_of(addr)
            if "first_name" not in form:                       # known only by email so far: ask who they are
                return self._login_form(addr, first_time=True)
            if not name:
                return self._login_form(addr, "Please enter your first name.", 400, True, name, unit)
            if not partner and unit not in UNITS:
                return self._login_form(addr, "Please choose your unit.", 400, True, name, unit)
            register(addr, name, "Partners" if partner else unit, partner)
            log_access(addr, ip, "registered")
        log_access(addr, ip, "signed-in")
        self._redirect("/index.html#overview", self._session_cookie(addr))

    def _live_data(self, route: str, user: str):
        send = lambda obj, status=200: self._page(json.dumps(obj), status, "application/json")
        try:
            if not can_live(user):
                return self.send_error(403)
            from kpi import live
            if not live.enabled():
                return self.send_error(404)
            if route == "/live-data/options":
                return send(live.options())
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            one = lambda k: (q.get(k) or [""])[0]
            log_access(user, self.headers.get("X-Forwarded-For", self.client_address[0]), "live-data")
            return send(live.get(one("section"), one("state"), one("period")))
        except ValueError:
            return send({"error": "That selection is not available."}, 400)
        except Exception as e:           # the reason goes to the log; the page says DHIS2 could not be reached
            print(f"live data entry failed: {type(e).__name__}", flush=True)
            return send({"error": "DHIS2 could not be reached. Try again in a few minutes."}, 502)

    def _trm(self, user: str):
        try:
            from kpi import trm_report
            state = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("state") or [""])[0]
            log_access(user, self.headers.get("X-Forwarded-For", self.client_address[0]), f"trm {state or 'all'}")
            path, name = trm_report.get(LIVE, ROOT / "dashboard" / "assets" / "trm_template.pptx", ROOT / "data" / "trm", state)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", self.extensions_map[".pptx"])
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Report-Name", name)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except ValueError:
            self.send_error(400)
        except Exception as e:           # the reason goes to the log; the page offers to try again
            print(f"TRM report failed: {type(e).__name__}", flush=True)
            self.send_error(500)

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
            where = "/login?idle=1" if "idle=1" in urllib.parse.urlsplit(self.path).query else "/login"
            self._redirect(where, f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
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
        if route.startswith("/live-data"):      # live data entry monitor: only where DHIS2 logins are set
            self._live_data(route, user)
            return True
        if route == "/reports/trm":             # built when asked for, one at a time, kept for this build
            self._trm(user)
            return True
        if route == "/whoami":
            p = users().get(user, {})
            self._page(json.dumps({"email": user, "name": p.get("first_name") or first_name(user), "unit": p.get("unit", "")}), ctype="application/json")
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
        if path.suffix in (".html", ".json") and (path.is_file() or path.with_name(path.name + ".gz").is_file()):
            return self._send_html(path, head=False)
        return super().do_GET()

    def do_HEAD(self):
        self._cached = False
        if self._gate(head=True):
            return
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        if path.suffix in (".html", ".json") and (path.is_file() or path.with_name(path.name + ".gz").is_file()):
            return self._send_html(path, head=True)
        return super().do_HEAD()

    def _send_html(self, path: Path, head: bool):
        """The dashboard page or one of its data files (data/<build>/*.json, live builds): gzip when accepted.
        The page is cached by the browser and revalidated on every visit; a data file never changes (its
        folder is named after the build), so the browser keeps it.
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
        self.send_header("Content-Type", "application/json" if path.suffix == ".json" else "text/html; charset=utf-8")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        # data/<build>/*.json never change (the folder is named after the build); the page, version.json and
        # status.json do: the browser checks those every time. "private": this browser only.
        base = Path(self.directory).resolve()
        immutable = path.suffix == ".json" and path.resolve().is_relative_to(base) and path.resolve().relative_to(base).parts[:1] == ("data",)
        self.send_header("Cache-Control", "private, max-age=31536000, immutable" if immutable else "private, no-cache")
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
