# ECEWS SPEED KPI pipeline (FY27)

This pipeline replaces the Power BI/DAX model with Python. Each run reads DHIS2 (weekly and monthly instances), the central data repository (CDR) linelist and the NDR PBS line list. It applies the same rules as the DAX and writes one self-contained HTML dashboard. Nothing is ever written back to DHIS2 or the CDR, because access is read-only.

```
 DHIS2 weekly ─┐                                              ┌─► live\index.html   the dashboard
 DHIS2 monthly ┴─► API pull ─► data\raw\cache ─┐              ├─► archive\          dated builds, no client lists
 CDR linelist (Postgres) ─► read every run ────┼─► calculate ─┤
 NDR PBS export (inbox\) ──────────────────────┤   (kpi\)     └─► data\history\     trend + stock history
 Targets, Naomi (inbox\) ──────────────────────┘
```

Project home: `C:\Users\CalebGwaza\OneDrive - Excellence Community Education Welfare Scheme\SPEED\speed_kpi`.

## Day to day
| Task | How |
|---|---|
| See the dashboard | http://localhost:8765. The Task Scheduler task "SPEED dashboard server" starts the server silently 30 seconds after you log in. `open_dashboard.bat` opens the page, and starts the server if it isn't running. The open page reloads itself after each update. |
| Update now | `update_dashboard.bat` writes a log to `logs\`. Add `--full` to re-pull all of DHIS2, `--export` for QA CSVs, or `--refresh-cdr` to re-read the CDR line list when the CDR team reloads a period they already published (otherwise an unchanged period is taken from the saved copy). |
| Scheduled updates | Task Scheduler task "SPEED dashboard update" runs on weekdays at 07:45 and 13:45. |
| New NDR PBS export | Every week, download the **active + inactive (all status)** PBS line list from the NDR portal and drop it in `inbox\` as `pbs_all_<YYYY-MM-DD>_all_status.xlsx`. It drives every treatment number (`treatment_source: ndr`). The newest file wins, and older exports are saved to history and removed after the next run. |
| Older NDR PBS files | `python scripts\import_ndr_history.py <folder or zip>` saves dated copies of past weekly files (named like `PBS Line List_13thJuly2026.xlsx`) for earlier periods and the weekly trend lines. Default folder: Downloads. |
| Check the numbers agree | `python run_pipeline.py run --cached --export`, then `python scripts\check_consistency.py`: TX_CURR, waterfall, biometric categories, MMD / DSD and VL cross-checked by state. |
| Sign-in | People enter their email at `/login`; allowed domains and extra addresses are under `access:` in `config\settings.yaml`. Sign-ins are logged in `logsccess.log`. |
| New targets / Naomi | Drop them in `inbox\` (`FY*Target*.xlsx`, `naomi*.xlsx`). |
| Facility name mismatch | It's listed on the Data flow page. Add it under `facility_aliases` in `config\settings.yaml`. |

The Python environment lives in `%LOCALAPPDATA%\speed_kpi\.venv`, outside OneDrive so its many small files don't sync. On a new computer, the first run of either .bat builds it (`scripts\ensure_venv.bat`; needs Python 3.10+). Credentials go only in `.env` (template: `.env.example`).

## What is pulled, and what is kept
| Source | How it is read | Kept on disk |
|---|---|---|
| DHIS2 weekly + monthly | `/api/dataValueSets` per table, by month. A scheduled run re-pulls the last `refresh_months` (3), and everything is re-pulled every `full_refresh_days` (7). | `data\raw\cache\` (aggregate values only, ~20 MB). This is a speed cache; `--full` rebuilds it. |
| CDR `linelist_flat` | Postgres, weekly stacked snapshots (`period`). Each run reads the latest period, plus any earlier period not yet saved; only the columns the pipeline uses (no names), in pages with retries. State names are tidied. If the table is unavailable, the latest saved copy is used and the dashboard shows a warning. | Every period as a dated copy in `data\history\sources\` |
| CDR eac_linelist | Postgres, every run: the output of `docs\EAC_Script_Version11.sql` for every facility, loaded by the CDR team (request: `docs\CDR_request_EAC.md`). Names and DOB are never read. While the table is empty, an `eac_linelist*.csv` in `inbox\` is used instead, if present. | Not kept |
| NDR PBS line list | Excel in `inbox\` | A fast-read copy in `data\raw\cache\files\`, plus a dated copy in `data\history\sources\` |
| Targets, Naomi, FY26 targets | `inbox\`, `data\excel\`, `data\manual\` | As supplied |

Outputs:
- **`live\index.html`**: the dashboard (~18 MB). It **contains client identifiers** (ART and hospital numbers) in the biometrics, concurrence and appointment lists, so keep it within the programme team.
- **`archive\`**: one copy per build, **without** client lists, saved as a `.zip` (double-click it, then open the page inside). The last build of each day is kept for 30 days, then the last of each month for 24 months.
- **`data\history\snapshots.csv`**: facility stock figures from every live run (one set per day). It feeds the exact trend lines.
- **`data\history\sources\`**: dated copies of the linelist (`linelist_<date generated>`) and the NDR file (`ndr_<date in file name>`), only the columns the pipeline reads. **This is client-level data.** When you pick a past period, its TX_CURR, viral load and biometric figures come from the latest copy dated inside that period. With no copy, the page says "No data". After `source_keep_days` (120), only the last copy of each month is kept.
- **`logs\`**: one log per run, kept for 90 days.
- **`exports\`**: QA CSVs, only written with `--export`.

When moving to a server, copy the project folder including `data\history\`. Leave out `.venv`, `live\`, `archive\` and `logs\`.

## How the numbers are made
1. **Weekly + monthly merge** (`etl\period_merge.py`). For each facility × month × table, the monthly report is used when it exists; otherwise the facility's weeks are summed. A week belongs to the month containing its Thursday (`week_to_month_rule`). On the monthly instance, the TX_TB "(Monthly)" elements are used, because the plain ones hold half-year totals.
2. **Measures** (`kpi\measures.py`) are Python ports of the DAX (`config\expo_python_formulas.xlsx`, `docs\PowerBI_measure_export.xlsx`). The "How it's calculated" page lists every DHIS2 element and rule behind each figure.
3. **Cube** (`kpi\engine.py`): facility × sex × age band × modality × period × measure, plus the targets. The browser forms rates and totals from it for any filter or period.

Reference definitions:
- **TX_CURR (headline)** is the NDR TX_CURR: active clients with a valid unique print, plus every active client under 15 (the PBS portal route).
- **EMR TX_CURR** uses the same rule on the EMR: active, AND a valid biometric (`ValidBiometric`) OR under 15. The viral load cascade, MMD, the EMR waterfall and the unsuppressed line list all use this same cohort. "Active in EMR" (every client marked Active) is kept separately. The EMR waterfall starts from the linelist's own `ARTStatus_PreviousQuarter`, on the same rule.
- **IIT / IIT rate** come from the NDR waterfall for the fiscal year from 1 October: IIT ÷ (TX_CURR at the end of Q4 + TX_NEW).
- **Appointments**: active and IIT clients by next pharmacy appointment, for any date range. With no pickup since the appointment (as of the linelist date), a client is a missed appointment while under 28 days late and IIT once 28 or more days late.
- **Unsuppressed** = VL ≥1000 copies/mL among results in the 12 months to the linelist date, with a client line list on the Viral Load page and in AP3 (0–19).
- **EAC** follows `EAC_Script_Version11` (agreed with the clinical team): the latest EAC episode per client. Entry is VL ≥50 (with a switch to ≥1000). Sessions 1–3 are standard and 4–6 extended. The follow-up VL is the first result after the triggering VL (<50 re-suppressed, 50–999 LLV, ≥1000 unsuppressed). The cohort is picked by triggering-VL date; active clients are shown by default.
- **Sources by programme area (EMR first):**
  - AHD for clients already on ART, DSD model, TB screening among PLHIV (status, symptoms, GeneXpert / AFB / X-ray, TB treatment start) and OTZ all come from the EMR (`linelist_flat`).
  - AHD for newly initiated clients, the TB programme indicators (TB_STAT, TB_ART, TX_TB (N) vs target, TB_PREV) and TPT stay on DHIS2. The EMR's TPT fields are empty for now.
  - The AHD "already on ART" entry = unsuppressed (VL ≥1000) or returned to treatment. Then: CD4 for AHD (Indication_AHD / CD4 LFA) → CD4 <200 or WHO 3/4 → LF-LAM, GeneXpert, serum and CSF CrAg.
  - TB screening is dated by `DateofCurrent_TBStatus`, because `TB_Screening_Date` is empty.
- **Paediatric & adolescent** = under 20.
- **Community PMTCT** is included in the PMTCT headline, as in the DAX and the deck.

## Periods
The filter bar offers fiscal quarters, months and "FY to date", plus a custom From/To range.
- **DHIS2 figures** follow the dates: a week counts when its Thursday is in range, and a month counts when at least half of it is.
- **TX_NEW and IIT** follow exact event dates.
- **Stock figures** (TX_CURR, viral load, biometrics, saturation, appointments) are as of the linelist / NDR file date. A period that covers that date, or today, shows them. An earlier period uses a saved copy from `data\history\sources\` if one exists; otherwise it shows "No data".

## Scoring
- **KPI summary** (Overview): each bar is achieved ÷ the FY target. The tick marks where we should be by now (the share of the year covered by the data). The colour shows progress against the tick: ≥75% green, 50–74% amber, <50% red. Stocks (TX_CURR, TX_PVLS) are compared with the FY target directly.
- **Rates** are compared with the target itself: met = green, within 5 points = amber, otherwise red. IPI rates use the quarter milestone. Population VLS (Naomi denominator) is shown as "Monitored", not rated.

## Pages
- Overview
- IPI & Non-IPI
- HIV Testing Services, including index testing and HIV self-testing
- Saturation (Naomi)
- Current on Treatment: waterfalls, IIT, appointments, and weekly trends in a collapsible section
- Viral Load
- AP3/PMTCT: mothers, exposed infants, and AP3 (children and adolescents 0–19)
- TB
- AHD
- Biometrics
- How it's calculated
- Data flow

Every breakdown table has rows by org unit (drill down, or straight to LGA or facility), sex, age band and, where relevant, paediatric/adult or modality.

## Where things live
| File | Purpose |
|---|---|
| `config\settings.yaml` | FY, DHIS2 dataset mapping, CDR tables, refresh and history settings, RAG bands, column names, facility aliases |
| `config\indicators.yaml` | Every indicator: IPI / Non-IPI / Conventional, which 95 it serves, numerator and denominator |
| `etl\` | DHIS2 client, weekly/monthly merge, source readers |
| `kpi\` | Measures, engine, targets, Naomi loader |
| `dashboard\template.html` | The dashboard (no external libraries) |
| `run_pipeline.py` | `run` (live), `demo` (synthetic data, writes to `data\demo\out`), `discover` (lists DHIS2 datasets) |
| `test_dhis2.py` | First-time DHIS2 connection test |
| `tests\` | `python -m pytest -q` |
| `docs\` | The FY26 Power BI deck, the Power BI measure export, and the original setup brief |

## Open decisions (defaults in brackets)
1. Case-finding target: the Non-IPI sheet says 4,545, but the conventional sheet sums to 6,423 [6,423].
2. IPI "ART coverage among people who know status" is measured through linkage, TX_NEW ÷ HTS_TST_POS [linkage].
3. Knowledge of status still needs a count of diagnosed PLHIV from the NDR. Naomi gives only the denominator.
4. Month-close cut-off day [15th of the following month].
