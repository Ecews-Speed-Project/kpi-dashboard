# SPEED KPI dashboard - latest shared build

Generated 2026-10-30T08:57 (data as of 2026-10-30, FY27Q1).

Full dashboard, including client-level lists (ART / hospital numbers): keep this repository private and serve it
only behind the ECEWS sign-in page (serve_dashboard.py). This branch holds just the latest build and is
replaced on every update. The page is `index.html.gz` (the gzip-compressed index.html). Serve it with
`python serve_dashboard.py --lan --port 5100 --no-browser` from `main` (ECEWS sign-in; unpacks it for any browser that
needs it), or send it with `Content-Encoding: gzip`, or unpack it first (`gunzip -k index.html.gz`). `version.json` changes
with each build and open pages reload when it does. The code is on the `main` branch.
