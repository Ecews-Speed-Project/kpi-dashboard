# Hosting the dashboard: compression and caching

Every build writes two files to `live\`:

| File | Size (Oct 2026) | Purpose |
|---|---|---|
| `index.html` | ~26 MB | the dashboard |
| `index.html.gz` | ~3.4 MB | the same file, precompressed, for the web server |
| `version.json` | tiny | open pages check it every minute and reload after a new build |

**The rules, whatever the server:**
- **Compression:** send `index.html.gz` with `Content-Encoding: gzip` to browsers that accept gzip, which is all modern browsers.
- **Caching of the dashboard:** use `Cache-Control: no-cache` plus an `ETag`. The browser keeps a copy and checks it on each visit. An unchanged dashboard costs a tiny `304 Not Modified`; a new build is downloaded once.
- **`version.json`:** use `Cache-Control: no-store`. It must never be cached.
- **Access:** serve over HTTPS behind a login, because `index.html` contains client-level ART and hospital numbers.

## Sign-in

`serve_dashboard.py` asks for an email address before showing the dashboard:
- **Who gets in:** only addresses allowed under `access:` in `config/settings.yaml` (`allowed_domains`, `allowed_emails`).
- **Sessions:** the browser stays signed in for `session_days` (30).
- **Log:** every sign-in or refusal is written to `logs/access.log`.
- **Background photos:** every `.webp` / `.jpg` in `dashboard/assets/login`. Add or remove files to change them.
- **Signing key:** session cookies are signed with `DASH_SECRET` from `.env`, or a random key kept in `data/.dash_secret`.

The address is not verified with a code or a password. This keeps out people who don't know a staff address, not someone who types one in. For stronger protection on the hosting server, put the dashboard behind ECEWS's Microsoft 365 sign-in (Entra ID) or send a one-time code by email.

## serve_dashboard.py (this project's server)

Compression and caching are already built in. Run it with:

    python serve_dashboard.py --lan --no-browser

## nginx

    location / {
        root /srv/speed_kpi/live;
        gzip_static on;                       # serves index.html.gz when the browser accepts gzip
        gzip on; gzip_types application/json; # anything else
        etag on;
        add_header Cache-Control "no-cache";
    }
    location = /version.json {
        root /srv/speed_kpi/live;
        add_header Cache-Control "no-store";
    }

## IIS (Windows Server)

Turn on **Static Content Compression** under Server Roles → Web Server → Performance. Then put this `web.config` in the site folder. IIS compresses and caches the compressed copy itself, so `index.html.gz` is not needed there.

    <configuration>
      <system.webServer>
        <urlCompression doStaticCompression="true" />
        <staticContent>
          <clientCache cacheControlMode="UseMaxAge" cacheControlMaxAge="0.00:00:00" cacheControlCustom="no-cache" />
        </staticContent>
        <httpCompression>
          <staticTypes><add mimeType="text/html" enabled="true" /></staticTypes>
        </httpCompression>
      </system.webServer>
      <location path="version.json">
        <system.webServer><staticContent><clientCache cacheControlMode="DisableCache" /></staticContent></system.webServer>
      </location>
    </configuration>
