# FY26 Q3 Performance Review deck → SPEED KPI dashboard

**Source deck:** `inbox\ECEWS_Q3_Performance Meeting_Final.pptx` (132 slides), reviewed 2026-10-14.

Each item is marked:
- **Added**: new on the dashboard from this review.
- **Already there**: was already on the dashboard.
- **No source yet**: the figure doesn't come from any data the pipeline reads. These are listed under [To check](#to-check-no-known-source-in-our-pipeline) at the end.

## 1st 95: HIV Testing Services
| Deck slide | Dashboard | Source |
|---|---|---|
| Overall 1st 95 performance (HTS_TST / POS target vs achievement by state) | **Added**: HTS page, "Testing by state against targets" (targets, % achievement, yield, TX_NEW, linkage) | DHIS2 HTS + FY targets |
| Yield and linkage by state; facility vs community contribution to HTS_POS | Yield and linkage **added** to the state table. **Facility vs community split: not yet** (see To check #1) | DHIS2 |
| Testing modalities: contribution and yield (ECEWS and by state) | **Already there**: modality bars. **Added**: each modality's share of HTS_POS. The state filter gives the state views. | DHIS2 HTS |
| Community testing modalities, age and sex | Partly: Mobile and Index modalities; age and sex charts on the HTS page | DHIS2 |
| Paediatric & adolescent testing trends (1–14, 15–24) | **Added**: "Paediatric & adolescent testing by quarter", last six quarters, tests and positives | DHIS2 HTS |
| HTS_TST_POS demographic analysis (LGA map) | **Added**: "Testing distribution by LGA" map with Testing / Positives / Both, under the age and sex charts. Click an LGA to filter to it. | DHIS2 HTS + DHIS2 LGA boundaries |
| HTS_SELF cascade by state, <15 / 15+ | **Already there**: HIVST flow. **Added**: state × <15/15+ table (kits, reactive, confirmed, linked, share of HTS_POS) | DHIS2 HTS_SELF |
| HTS_INDEX cascade, sex disaggregation | **Already there**: index flow and breakdown. **Added**: male/female table and "share of HTS_POS" step | DHIS2 HTS_INDEX + CSCS |
| HIV retesting outcome (concordance) | **No source yet** (To check #2) | |
| RTCQI: PT rounds, QC, logbook review, tester competency, SPI-RT | **No source yet** (To check #3) | |

## 2nd 95: treatment
| Deck slide | Dashboard | Source |
|---|---|---|
| TX_NEW, TX_NET_NEW, TX_CURR trend | **Already there**: weekly trends (Current on Treatment). Quarterly history builds from the saved snapshots. | Linelist / NDR snapshots |
| Treatment saturation trend | Trend builds from snapshots. Current saturation is on the Saturation page. | NDR + Naomi |
| Saturation by coarse age band and state (sex gap) | **Added**: Saturation page, "Saturation by coarse age and sex" | NDR + Naomi |
| Continuity of treatment cascade (overall, state) | **Already there**: EMR and NDR waterfalls | Linelist, NDR |
| IIT trend; IIT by age and sex | **Already there**: IIT by facility, age and sex; trend from snapshots | NDR |
| Facility vs community DSD; DSD model uptake (FBM/CBM) | **No source yet** (To check #4) | |
| MMD by coarse age and state | **Added**: Current on Treatment, "Multi-month dispensing" (<MMD3 / MMD3 / MMD4–5 / MMD6) | EMR linelist (days of ARV refill) |
| ART refill appointment compliance | Partly: appointments section (scheduled / missed / IIT). The deck's compliance definition needs confirming (To check #5). | EMR linelist |
| Mortality, facility vs community | **No source yet** for the facility/community split (To check #4) | |
| Biometrics; TX_CURR reflection (89,891 active vs 77,981 unique; adjudication / duplicate / invalid; excluded still refilling) | **Already there**: Biometrics page. **Added**: "TX_CURR reflection" card and state table | NDR PBS line list |

## 3rd 95: viral load
| Deck slide | Dashboard | Source |
|---|---|---|
| VL cascade, ECEWS and by state (coverage, suppression, LLV, undetectable) | **Already there**: Viral Load page | EMR linelist |
| VL coverage and suppression by age band | **Already there** | EMR linelist |
| DSD outcome: VLS by facility / community | **No source yet** (To check #4) | |
| VL trend by state | Builds from snapshots | |
| LIMS–NMRS deployment, GeneXpert / mPIMA sites, FMC Asaba EID throughput, SLBP audit, NDR-Lite upload | **No source yet** (To check #6) | |
| EAC cascades by cohort; EAC by age band (unsuppressed and LLV); TF Monitor | **Paused**: waiting for the CDR `eac_linelist` table (see `docs\CDR_request_EAC.md`) | |

## TB/HIV and AHD
| Deck slide | Dashboard | Source |
|---|---|---|
| TB/HIV performance (TB_STAT, positivity, TB_ART linkage by state) | **Already there**: TB page | DHIS2 TB |
| TX_TB (N) vs target (quarter and cumulative) | **Already there**: KPI summary and TB cards (period selector) | DHIS2 + targets |
| TPT uptake and TB_PREV completion trend | **Added**: TB page, "TPT uptake and TB_PREV completion by quarter" | DHIS2 TB |
| HIV/TB clinical cascade (screening → treatment) | **Already there**: TX_TB flow and TPT flows | DHIS2 |
| Portable digital X-ray (PDX) cascade | **No source yet** (To check #7) | |
| AHD performance (CD4, LF-LAM, CrAg) | **Already there**: AHD page | DHIS2 AHD_MSF |
| Pharmacy & supply chain (stock-outs, LMD, expiries, ADR) | **No source yet** (To check #8) | |

## AP3/PMTCT
| Deck slide | Dashboard | Source |
|---|---|---|
| AP3 implementation scorecard | **Already there**: AP3 cascade (0–19). AP3-specific targets aren't in the targets file (To check #9). | DHIS2 + linelist |
| Continuity of treatment cascade 0–9 and 10–19 | **Added**: NDR waterfalls for 0–9 and 10–19 in the AP3 section | NDR |
| VL cascade by age band (0–9, 10–19) by state | **Added**: AP3 section table | EMR linelist |
| Family index testing (NDARS), FIT optimisation | **No source yet** (To check #10) | |
| pALD transition, OTZ scale-up, OTZ WhatsApp, OTZ vs non-OTZ outcomes, DSD for C/ALHIV | **No source yet** (To check #11) | |
| PMTCT targets vs achievement by state (STAT, ART, EID) | **Added**: "PMTCT targets vs achievement" (with STAT_POS, EID, HEI POS) | DHIS2 PMTCT + targets |
| Maternal cascade: known vs new positives | **Added**: Known / New columns in the same table | DHIS2 PMTCT |
| EID / HEI cascade (<2 and 2–12 months) | **Already there** | DHIS2 PMTCT |
| Facility vs community PMTCT | **Already there** | DHIS2 PMTCT + Community PMTCT |
| Congregational approach (Mother's Love Party, Baby Shower) | **No source yet** (To check #12) | |
| PrEP for pregnant and breastfeeding women | **Added**: "PrEP for PBFW": offered → accepted → PrEP_NEW, pregnant / breastfeeding, PrEP_CT returning | DHIS2 PrEP |

## Other sections
| Deck slide | Dashboard | Source |
|---|---|---|
| Persons at higher risk (KP): testing, linkage, HIVST, VL, IIT per CSCC | **Added**: HTS page, "Persons at higher risk — CSCC sites" (per CSCC site: testing, linkage, TX_CURR, VL, IIT) | DHIS2 + linelist + NDR |
| Contribution to 1st/2nd/3rd 95 (NOMIS, NDR-Lite custom indicators: HIVRISKASSED, HTSLINK, FAM_INDEX_COM…) | **No source yet** (To check #13) | |
| OVC_SERV, HEI / CALHIV enrolment saturation, nutrition (CCMP) | **No source yet** (To check #14) | |
| Violence response, PEP cascade, STI screening, referrals, IPV in care | **No source yet** (To check #15) | |
| SI: PIMS / Hamster devices, NMRS 3.0, state validation, integrated DQA, aggregate concurrence | DQA coverage is an IPI card (manual CSV). **Others: no source yet** (To check #16) | |
| Sustainability: HSS activities, service integration, HRH capacity transfer, insurance enrolment, abstracts | **No source yet** (To check #17) | |
| True linkage gap (GC7 positives in TX_NEW) | **No source yet** (To check #18) | |
| KPI summary (achievement vs target, rating bands) | **Already there**: Overview KPI summary | |

## To check: no known source in our pipeline
For each item: where the figure likely comes from, and what we'd need to add it.

1. **HTS facility vs community split.** DHIS2 has two HTS datasets (Facility `llIVb031ufJ`, Community `HZLQXHypCbz`), but our pull merges them into one table. It can be added by keeping the dataset ID on each value, which needs a pipeline change and a one-off full re-pull.
2. **HIV retesting concordance.** Is there a DHIS2 element for retesting at testing points, or is it from the testing register?
3. **RTCQI** (PT, QC, logbooks, competency, SPI-RT). Lab / QA team trackers. Could come in as a small CSV in `inbox\`.
4. **DSD model / facility vs community** (DSD uptake, VLS by DSD, mortality by DSD). The CDR linelist's DSD field is empty. Which source carries the DSD model? NDR `ddd_dispensing_modality` is in an empty CDR table.
5. **Refill appointment compliance.** Need the deck's exact numerator and denominator to match it.
6. **Laboratory:** LIMS-EMR deployment, VL/EID e-results, GeneXpert / mPIMA, FMC Asaba EID, SLBP audit, NDR-Lite upload. LIMS / lab team.
7. **Portable digital X-ray (PDX) cascade.** Who keeps the PDX register?
8. **Pharmacy & supply chain** (stock-outs, LMD, emergency orders, expiries, ADR). LMIS / PSM team.
9. **AP3 targets** (HTS_TST, POS, TX_NEW, TX_CURR for children and adolescents). Not in `FY27 Targets.xlsx`.
10. **Family Index Testing (NDARS).** NDARS export?
11. **pALD transition, OTZ** (scale-up, WhatsApp, outcomes), C/ALHIV DSD. NDR line list fields or OTZ registers?
12. **Congregational approach to PMTCT.** Programme tracker.
13. **NOMIS / NDR-Lite custom indicators** (HIVRISKASSED, HTSLINK, HTS_TST_COMM, FAM_INDEX_COM, TX_LINK, CALHIV IIT/RTT). Source export?
14. **OVC / CCMP** (OVC_SERV, HEI / CALHIV enrolment, VLC/VLS among enrolled, nutrition). The CCMP file was skipped earlier; it can be wired in.
15. **GBV / violence prevention** (post-violence care, PEP, STI, referrals, IPV screening). DHIS2 GBV dataset or a programme tracker?
16. **SI:** PIMS device status, NMRS 3.0 rollout, monthly validation visits, integrated DQA, aggregate concurrence. M&E trackers.
17. **Sustainability / HSS:** governance meetings, service integration assessment, HRH absorption, insurance enrolment. HSS team tracker.
18. **True linkage gap (GC7).** Needs the GC7 positives list, so they can be separated from TX_NEW.
