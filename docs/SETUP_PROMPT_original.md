I'm setting up the ECEWS SPEED KPI dashboard pipeline (Python + one HTML page) on this computer. Please do the setup end to end and check with me only where noted.

FOLDER
- Target: C:\Users\CalebGwaza\OneDrive - Excellence Community Education Welfare Scheme\Desktop\ \Fy26\set_up
- If that exact path doesn't exist (the "\ \" part may be a typo), find the set_up folder under Fy26 on my Desktop and confirm it with me.
- I've downloaded speed_kpi.zip. Find it (Downloads or set_up) and extract its contents so that run_pipeline.py sits directly in set_up\, not in set_up\speed_kpi\.
- Read README.md first.

1. PYTHON
- Check Python 3.10+ (install it with winget if missing).
- Create the virtual environment at %LOCALAPPDATA%\speed_kpi\.venv, outside OneDrive.
- pip install -r requirements.txt into it.
- Run `python -m pytest -q`, then `python run_pipeline.py demo`.
- Open live\index.html to confirm the demo dashboard renders.

2. CREDENTIALS
- Copy .env.example to .env.
- Ask me for the DHIS2 username/password (the same login works for both instances) and the Postgres user/password for the central repository (<CDR host>:5432, database ecews).
- Put them only in .env. Never echo them, log them, or write them anywhere else.

3. DHIS2 TEST
- Run `python test_dhis2.py`, then read data/raw/dhis2_test_report.md and the catalog CSVs.
- If login or network fails, tell me exactly what failed (e.g. VPN needed) and stop there.

4. MAP DHIS2
In config\settings.yaml:
- Set level_state, level_lga and level_facility from the org-unit levels.
- Map each table under dhis2.tables (HTS_TST, HTS_TST_AGE, INDEX, HIVST, PMTCT, PMTCT_COMM, PMTCT_EID, TB, TX_TB, AHD) to its dataset UID on the weekly AND the monthly instance.

In kpi\measures.py:
- Check every data element name in DHIS_MEASURES against the dataelements catalogs. Fix names that differ.
- Note how modality, sex and age appear (data element suffix vs category option combo). parse_disagg and modality_of must understand them.

Show me the mapping table before saving if any match is ambiguous.

5. CENTRAL REPOSITORY
- Test the Postgres connection.
- Compare public.linelist columns with columns.linelist in settings.yaml and fix the mapping.
- Do the same for the PBS extract (columns.pbs_all).

6. INBOX
- Ask me where the latest pbs_all, CCMP, Naomi and targets files are, and copy them into inbox\.
- Naomi and FY27 targets are already in data\.

7. LIVE RUN
- Run update_dashboard.bat --export. Read the newest log in logs\ and fix errors until it runs clean.
- Check the "Data flow" page for unmatched facilities. Add name fixes if facility names differ between DHIS2 and the linelist.

8. RECONCILE
Select FY26Q4 in the dashboard's Period dropdown, or compute the same from exports\. Compare against last quarter's Power BI deck:
- HTS_TST 270,031
- HTS_TST_POS 4,630
- PMTCT_STAT_D 133,327
- PMTCT_STAT_N 131,197
- PMTCT_STAT_POS 1,732
- PMTCT_ART 1,898
- TX_TB_D 104,058
- TB presumptive 2,888
- TB positive results 337
- TB treatment started 327

By state, HTS_TST should be Delta 129,494, Ekiti 67,058 and Osun 73,479.

Give me a table of pipeline value vs deck value with the % difference, and explain anything off by more than 2%. TX_CURR (76,738 at FY26Q4) can't be rebuilt for a past quarter. Instead, compare today's TX_CURR with the EMR/NDR trend I know.

9. AUTO UPDATE
Ask me how often to refresh. Default: weekdays at 07:45 and 13:45. Then:
- Create a Windows Task Scheduler task "SPEED dashboard update" that runs update_dashboard.bat from set_up\ (Start in = set_up), and only when the network is available.
- Put a shortcut to open_dashboard.bat on my Desktop. It serves live\ at http://localhost:8765 and the page reloads itself after each update.

10. WRAP-UP
Give me a short summary:
- what was mapped
- reconciliation results
- anything still open: PrEP_NEW source, case-finding target 4,545 vs 6,423, RAG bands, diagnosed-PLHIV source for knowledge of status

Do not delete or move any of my existing files in Fy26 other than what's described here.
