# Live data entry page

Someone signed in from the SI unit chooses weekly or monthly entry, then a section (HTS, PMTCT, TB, AHD, PrEP, or all flags), a state and a week or month. The dashboard server
reads just that from DHIS2 and shows facilities reported, the figures with the same formulas as the dashboard, a
table by LGA, facilities not yet reported, and entries to check. It does not need the laptop or the full update.

## Switching it on (server)

1. Update the code from `main` and rebuild the `dashboard` image (`docker compose up -d --build dashboard`).
2. Update `live` as usual, so the page has the Live data entry tab.
3. Add these to the server's `.env`, using a read-only DHIS2 account. The dashboard service already reads that file:
   `DHIS2_WEEKLY_USER`, `DHIS2_WEEKLY_PASS`, `DHIS2_MONTHLY_USER`, `DHIS2_MONTHLY_PASS`.
4. The container needs outbound access to `speeddhis-weekly.ecews.org` and `speeddhis-monthly.ecews.org`, and
   write access to `data/` (already mounted) to keep the DHIS2 indicator formulas.
5. Restart the dashboard and check that signing in shows the Live data entry tab.

Without the logins, `/live-data` answers 404 and the tab stays hidden, so the rest of the dashboard is unaffected.

## How it behaves

- Answers are kept for 5 minutes per selection, and one DHIS2 read runs at a time. Others wait for it.
- In tests a read took 4 to 61 seconds, depending on the section and state. All states takes longer.
- On the laptop the server process peaked around 135 MB, against the 768 MB limit in `compose.yaml`.
- If DHIS2 fails, the last good answer is shown with a warning. Only the error type is logged, never logins.
- Each read is recorded in the sign-in log as `live-data`.
- Only aggregate figures are read. There are no client records.

The code is in `kpi/live.py` and the `/live-data` routes in `serve_dashboard.py`.
