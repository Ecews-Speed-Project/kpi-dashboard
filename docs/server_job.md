# Running the SPEED dashboard on the server

The server runs two containers from this repository (`compose.yaml`):

| Service | What it does |
|---|---|
| `dashboard` | `serve_dashboard.py` on port 5100: the ECEWS e-mail sign-in, the page and its data files |
| `job` | `scripts/scheduler.py`: on a schedule, pulls DHIS2, the CDR line list and the newest NDR file, recalculates everything and writes the dashboard to `live/` |

```
DHIS2 weekly + monthly ─┐
CDR Postgres (linelist_flat) ─┼─► job (06:30, 12:30 Mon–Fri) ─► live/index.html + live/data/<build>/*.json ─► dashboard ─► staff
NDR PBS file (inbox/) ─┘                       │
                                               └─ fails? the last good dashboard stays, status.json says so, optional e-mail
```

No laptop and no GitHub publishing are involved. Only code comes from GitHub (`main`).

## Server needs

- Docker with Compose.
- Memory: the `job` container peaks around **4 GB for a few minutes** per run (`mem_limit: 5g`); idle the rest of the day. The `dashboard` container stays small (`mem_limit: 768m`).
- Disk: a few GB for `data/` (history used for trends and past months) and `archive/`.
- Network from the server to:
  - `https://speeddhis-weekly.ecews.org` and `https://speeddhis-monthly.ecews.org` (HTTPS)
  - the CDR Postgres host (`PG_HOST`), port 5432
- Time zone: `Africa/Lagos` is set in the image.

## First-time setup

1. Clone the repository on the server (branch `main`) and go to its folder.
2. Create `.env` from `.env.example` and fill in the DHIS2 and CDR credentials. Optionally add the `ALERT_*` mail settings so a failed update sends an e-mail. `.env` never goes into git or into the image.
3. Copy the starting data from the M&E laptop (once, over a secure channel; it holds client-level records):
   - `data/history/` — trend history and the dated copies of past line lists (needed for trends and past periods)
   - `data/raw/cache/` — saved DHIS2 pull (otherwise the first run pulls the whole year, which takes longer)
   - `data/excel/`, `data/manual/` — FY26 targets and manual inputs
   - `data/.dash_secret` — keeps everyone signed in across the move (or set `DASH_SECRET`)
   - `inbox/` — the latest NDR PBS file (`pbs_all_YYYY-MM-DD_all_status.xlsx`), `FY27 Targets.xlsx`, `naomi.xlsx`
4. The containers run as user id 1000: `sudo chown -R 1000:1000 live data inbox logs archive`.
5. Build and start: `docker compose up -d --build`.
6. Run the first update now instead of waiting: `docker compose run --rm job python scripts/scheduler.py --now` (about 15–20 minutes). Then open `http://<server>:5100`.

## Every week

- **NDR file:** put the new PBS line list (active + inactive) into `inbox/` named `pbs_all_YYYY-MM-DD_all_status.xlsx`. The next run uses it; the previous one is kept in `data/history/sources/`. The dashboard shows how old the NDR data is. *(If the NDR offers an API or a scheduled export, the job can fetch it instead.)*
- Nothing else: DHIS2 and the CDR are pulled automatically.

## Watching it

- `live/status.json`: the last run's outcome (`ok`, start and finish times, minutes, and the error if it failed). When a run fails, the dashboard shows a short notice that the figures are older than they should be.
- `logs/run_<date>_<time>.log`: one log per run. `docker compose logs -f job` shows the schedule and each result.
- A failed run is retried once after 20 minutes (`RETRY_MINUTES`). The page is only replaced when a run finishes, so staff never see a half-built dashboard.
- `docker stats` shows the memory of each container during a run.

## Changing things

- **Run times:** `SCHEDULE` and `DAYS` in `compose.yaml` (e.g. `"06:30,12:30"`, `"mon-fri"`), then `docker compose up -d`.
- **Code changes** (pages, charts, calculations): made and tested on the M&E side and pushed to `main`. On the server: `git pull && docker compose up -d --build`. The next run uses the new code; the page itself updates at once.
- **Settings** (sources, facility name fixes, targets): `config/settings.yaml`, through git like code.

## Moving over from the laptop

1. Run the server job next to the laptop's scheduled update for about a week and compare the figures (the "data as of" line and the main totals).
2. When they match, point staff at the server's dashboard.
3. Then turn off the laptop's Windows task "SPEED dashboard update" and set `publish.git_branch` to empty in `config/settings.yaml` (no more `live` branch).
