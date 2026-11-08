-- ============================================================================
-- linelist_flat data-quality checks (CDR, public.linelist_flat)
-- Open in DBeaver: File > Open File..., then put the cursor in a query and press Ctrl+Enter.
-- Each check has a SUMMARY query, then a LINE LIST query (the clients behind it).
-- All checks use the latest weekly period unless the check is about periods.
-- Column names have capitals, so they must stay in double quotes.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0. Periods loaded (weekly snapshots) and duplicate clients within a period
-- ----------------------------------------------------------------------------
SELECT period,
       count(*)                                   AS records,
       count(DISTINCT "Unique_Id")                AS unique_clients,
       count(*) - count(DISTINCT "Unique_Id")     AS duplicate_rows
FROM public.linelist_flat
GROUP BY period ORDER BY period;

-- Line list: clients appearing more than once in the latest period
SELECT "Unique_Id", count(*) AS times, string_agg(DISTINCT "FacilityName", ' | ') AS facilities
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
GROUP BY "Unique_Id" HAVING count(*) > 1 ORDER BY times DESC;


-- ----------------------------------------------------------------------------
-- 1. Blank previous-quarter status (ARTStatus_PreviousQuarter)
-- ----------------------------------------------------------------------------
SELECT initcap(btrim("State", ' ' || chr(160))) AS state,
       coalesce("CurrentARTStatus", '(blank)') AS current_status,
       count(*) AS clients
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND nullif(trim(coalesce("ARTStatus_PreviousQuarter", '')), '') IS NULL
GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- Line list
SELECT initcap(btrim("State", ' ' || chr(160))) AS state, "LGA", "Datim_Code", "FacilityName", "Unique_Id", "PepID", "PatientHospitalNo",
       "Sex", "Current_Age", "ARTStartDate", "CurrentARTStatus", "ARTStatus_PreviousQuarter",
       "Pharmacy_LastPickupdate", "Pharmacy_LastPickupdate_PreviousQuarter", "DaysOfARVRefill", "ValidBiometric", period
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND nullif(trim(coalesce("ARTStatus_PreviousQuarter", '')), '') IS NULL
ORDER BY state, "FacilityName";


-- ----------------------------------------------------------------------------
-- 2. Blank current ART status (CurrentARTStatus)
-- ----------------------------------------------------------------------------
SELECT initcap(btrim("State", ' ' || chr(160))) AS state, count(*) AS clients
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND nullif(trim(coalesce("CurrentARTStatus", '')), '') IS NULL
GROUP BY 1 ORDER BY 1;

-- Line list
SELECT initcap(btrim("State", ' ' || chr(160))) AS state, "FacilityName", "Unique_Id", "PepID", "PatientHospitalNo", "Sex", "Current_Age",
       "ARTStartDate", "ARTStatus_PreviousQuarter", "Pharmacy_LastPickupdate", "Outcomes", "Outcomes_Date", period
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND nullif(trim(coalesce("CurrentARTStatus", '')), '') IS NULL
ORDER BY state, "FacilityName";


-- ----------------------------------------------------------------------------
-- 3. Date_Generated later than the period it belongs to (future dates)
-- ----------------------------------------------------------------------------
SELECT period, "Date_Generated", count(*) AS records
FROM public.linelist_flat
WHERE "Date_Generated"::date > period::date
GROUP BY 1, 2 ORDER BY 1, 2;

-- Line list
SELECT period, "Date_Generated", initcap(btrim("State", ' ' || chr(160))) AS state, "FacilityName", "Unique_Id", "PepID", "CurrentARTStatus"
FROM public.linelist_flat
WHERE "Date_Generated"::date > period::date
ORDER BY period, "Date_Generated", "FacilityName";


-- ----------------------------------------------------------------------------
-- 4. Spelling variants: state names and status labels
-- ----------------------------------------------------------------------------
SELECT 'State' AS field, "State" AS value_as_entered, count(*) AS records
FROM public.linelist_flat WHERE period = (SELECT max(period) FROM public.linelist_flat) GROUP BY 2
UNION ALL
SELECT 'CurrentARTStatus', coalesce("CurrentARTStatus", '(blank)'), count(*)
FROM public.linelist_flat WHERE period = (SELECT max(period) FROM public.linelist_flat) GROUP BY 2
UNION ALL
SELECT 'ARTStatus_PreviousQuarter', coalesce("ARTStatus_PreviousQuarter", '(blank)'), count(*)
FROM public.linelist_flat WHERE period = (SELECT max(period) FROM public.linelist_flat) GROUP BY 2
ORDER BY 1, 3 DESC;


-- ----------------------------------------------------------------------------
-- 5. Active but NOT in unique TX_CURR (aged 15+ without a valid biometric)
--    Unique TX_CURR = Active AND (ValidBiometric = Yes OR age < 15), as the NDR rule
-- ----------------------------------------------------------------------------
SELECT initcap(btrim("State", ' ' || chr(160))) AS state,
       count(*) AS active,
       count(*) FILTER (WHERE lower(trim(coalesce("ValidBiometric", ''))) = 'yes'
                          OR nullif(regexp_replace("Current_Age"::text, '[^0-9.]', '', 'g'), '')::numeric < 15) AS tx_curr_unique,
       count(*) FILTER (WHERE NOT (lower(trim(coalesce("ValidBiometric", ''))) = 'yes'
                          OR coalesce(nullif(regexp_replace("Current_Age"::text, '[^0-9.]', '', 'g'), '')::numeric, 99) < 15)) AS active_not_counted
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND lower(trim("CurrentARTStatus")) IN ('active', 'actives')
GROUP BY 1 ORDER BY 1;

-- Line list (with PBS / recapture details for follow-up)
SELECT initcap(btrim("State", ' ' || chr(160))) AS state, "FacilityName", "Unique_Id", "PepID", "PatientHospitalNo", "Sex", "Current_Age",
       "ValidBiometric", "PBS", "PBSDateCreated", "recapture", "RecapturedDate", "Pharmacy_LastPickupdate", period
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND lower(trim("CurrentARTStatus")) IN ('active', 'actives')
  AND NOT (lower(trim(coalesce("ValidBiometric", ''))) = 'yes'
           OR coalesce(nullif(regexp_replace("Current_Age"::text, '[^0-9.]', '', 'g'), '')::numeric, 99) < 15)
ORDER BY state, "FacilityName";


-- ----------------------------------------------------------------------------
-- 6. Empty fields among active clients (TPT, TB screening date, DSD model, ...)
-- ----------------------------------------------------------------------------
SELECT count(*) AS active_clients,
       count(*) FILTER (WHERE nullif(trim(coalesce("Is_Patient_Eligible_For_TPT", '')), '') IS NULL) AS no_tpt_eligibility,
       count(*) FILTER (WHERE "TPT_Startdate" IS NULL)                                          AS no_tpt_start,
       count(*) FILTER (WHERE nullif(trim(coalesce("TPTOutcome", '')), '') IS NULL)               AS no_tpt_outcome,
       count(*) FILTER (WHERE "TB_Screening_Date" IS NULL)                                      AS no_tb_screening_date,
       count(*) FILTER (WHERE "DateofCurrent_TBStatus" IS NULL)                                 AS no_tb_status_date,
       count(*) FILTER (WHERE nullif(trim(coalesce("DSD_Model", '')), '') IS NULL)                AS no_dsd_model,
       count(*) FILTER (WHERE nullif(trim(coalesce("ValidBiometric", '')), '') IS NULL)           AS no_valid_biometric_value,
       count(*) FILTER (WHERE "ARTStartDate" IS NULL)                                           AS no_art_start,
       count(*) FILTER (WHERE "Pharmacy_LastPickupdate" IS NULL)                                AS no_last_pickup
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND lower(trim("CurrentARTStatus")) IN ('active', 'actives');

-- Same, by state, for DSD model (40% blank)
SELECT initcap(btrim("State", ' ' || chr(160))) AS state, count(*) AS active,
       count(*) FILTER (WHERE nullif(trim(coalesce("DSD_Model", '')), '') IS NULL) AS no_dsd_model
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat)
  AND lower(trim("CurrentARTStatus")) IN ('active', 'actives')
GROUP BY 1 ORDER BY 1;


-- ----------------------------------------------------------------------------
-- 7. Latest dates in the EMR fields (stale fields stop early, e.g. TB treatment start)
-- ----------------------------------------------------------------------------
SELECT max("Pharmacy_LastPickupdate")  AS last_pickup,
       max("DateofCurrentViralLoad")   AS last_vl_result,
       max("DateofCurrent_TBStatus")   AS last_tb_status,
       max("TBTreatmentStartDate")     AS last_tb_treatment_start,
       max("FirstCD4Date")             AS last_cd4,
       max("Date_Enrolled_Into_OTZ")   AS last_otz_enrolment,
       max("ARTStartDate")             AS last_art_start
FROM public.linelist_flat
WHERE period = (SELECT max(period) FROM public.linelist_flat);
