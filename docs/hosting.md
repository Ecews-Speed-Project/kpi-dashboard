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

## Setting up the server (e.g. eboard.ecews.org)

GitHub holds the **code only**. Passwords, line lists and data never go there. A server that only clones the
repository can build nothing but the demo (synthetic data, `"demo": true` in `version.json`). For live data, use one of these:

### Option A: the server runs the pipeline itself
1. `git pull` to get the latest code, and repeat after every push. `python -m pip install -r requirements.txt`
2. Create `.env` on the server, copied by hand and never committed: DHIS2 weekly / monthly logins, `PG_HOST`, `PG_USER`, `PG_PASS`.
   The server must reach both DHIS2 instances and the CDR database (port 5432).
3. Copy in from the M&E computer:
   - `inbox\FY27 Targets.xlsx`, `inbox\naomi.xlsx` (the Saturation page needs Naomi);
   - the latest `inbox\pbs_all_<date>_all_status.xlsx`;
   - `data\excel\` (FY26 targets);
   - `data\history\` (saved weekly copies and trend history; without it, past periods and trends are empty).
4. Run the first build with the full DHIS2 pull: `python run_pipeline.py run --full`
5. Schedule `python run_pipeline.py run` twice a day (cron or Task Scheduler).
6. Serve with sign-in: `python serve_dashboard.py --lan --port 5100 --no-browser`, behind HTTPS.
7. Every week, the new NDR PBS file must reach the server's `inbox\`.

### Option B: this computer builds and the server only serves
The pipeline keeps running here, where the data and credentials already are. After each run, copy `live\index.html`,
`live\index.html.gz` and `live\version.json` to the server's `live\` folder (shared drive, `scp` or `robocopy`).
The server only runs `serve_dashboard.py` (Option A, step 6). No credentials or line lists are kept on the server
apart from the built dashboard.

Either way, check `http://<server>:5100/version.json`: `"demo": false` and a recent `generated_at` mean live data.

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
