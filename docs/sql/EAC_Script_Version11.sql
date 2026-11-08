
SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SET @endDate      := '2026-06-22';
SET @artStartDate := '2009-01-01';
SET @lga := (SELECT global_property.property_value FROM global_property WHERE property = 'partner_reporting_lga_code');

DROP TABLE IF EXISTS eligible_patients_all;
DROP TABLE IF EXISTS viral_load_data_all;
DROP TABLE IF EXISTS eac_sessions_numbered_all;
DROP TABLE IF EXISTS eac_cycle_pivot_all;
DROP TABLE IF EXISTS regimen_data_all;
DROP TABLE IF EXISTS final_vl_regimen_linelist_all;


-- ============================================================
-- TABLE 1: Eligible patients on ART
-- ============================================================
CREATE TEMPORARY TABLE eligible_patients_all AS (
    SELECT DISTINCT patient.patient_id,
        pid1.identifier AS PepID,
        ( SELECT DATE(ob.value_datetime)
          FROM obs ob JOIN encounter e ON ob.encounter_id = e.encounter_id
          WHERE ob.concept_id = 159599 AND ob.person_id = patient.patient_id
          AND e.encounter_type = 25 AND ob.voided = 0 AND e.voided = 0
          AND ob.value_datetime >= @artStartDate LIMIT 1
        ) AS ART_Start_Date,
        CASE WHEN EXISTS (
            SELECT 1 FROM obs ob JOIN encounter e ON ob.encounter_id = e.encounter_id
            WHERE ob.person_id = patient.patient_id AND ob.concept_id IN (160540, 165242)
            AND ob.value_coded IN (SELECT concept_id FROM concept_name
                WHERE NAME IN ('Transfer in','Transfer in with records')
                AND locale = 'en' AND locale_preferred = 1)
            AND ob.voided = 0 AND e.voided = 0
        ) THEN 'Yes' ELSE 'No' END AS Transferred_In
    FROM patient
    LEFT JOIN patient_identifier pid1 ON (
        pid1.patient_id      = patient.patient_id
        AND pid1.identifier_type = 4
        AND pid1.voided          = 0
        AND pid1.patient_identifier_id = (
            SELECT pi2.patient_identifier_id FROM patient_identifier pi2
            WHERE pi2.patient_id = patient.patient_id AND pi2.identifier_type = 4 AND pi2.voided = 0
            ORDER BY pi2.preferred DESC, pi2.patient_identifier_id ASC LIMIT 1
        )
    )
    WHERE patient.voided = 0
    HAVING ART_Start_Date IS NOT NULL AND ART_Start_Date >= @artStartDate
);


-- ============================================================
-- TABLE 2: First qualifying VL per patient (MIN, >= 50)
-- BUG A+B FIX: MIN anchor ensures EAC sessions are not missed.
-- Follow-up VL removed from here — computed in final SELECT.
-- ============================================================
CREATE TEMPORARY TABLE viral_load_data_all AS (
    SELECT ob.person_id AS patient_id,
        ob.value_numeric AS first_vl_value,
        e.encounter_datetime AS first_vl_date,
        ( SELECT DATE(ob2.value_datetime)
          FROM obs ob2 JOIN encounter e2 ON ob2.encounter_id = e2.encounter_id
          WHERE ob2.concept_id = 159951 AND ob2.person_id = ob.person_id
          AND e2.encounter_datetime <= e.encounter_datetime AND ob2.voided = 0
          ORDER BY ob2.obs_datetime DESC LIMIT 1
        ) AS first_vl_sample_date
    FROM obs ob
    JOIN encounter e ON ob.encounter_id = e.encounter_id
    JOIN eligible_patients_all ep ON ob.person_id = ep.patient_id
    WHERE ob.concept_id = 856 AND ob.value_numeric >= 50
      AND e.encounter_type = 11 AND ob.voided = 0 AND e.voided = 0
      AND e.encounter_datetime = (
          SELECT MIN(e2.encounter_datetime)
          FROM obs ob2 JOIN encounter e2 ON ob2.encounter_id = e2.encounter_id
          WHERE ob2.concept_id = 856 AND ob2.value_numeric >= 50
            AND ob2.person_id = ob.person_id AND e2.encounter_type = 11
            AND ob2.voided = 0 AND e2.voided = 0
      )
    GROUP BY ob.person_id, ob.value_numeric, e.encounter_datetime
);


-- ============================================================
-- TABLE 3: EAC sessions — regular table (Error 1137 fix)
-- BUG B FIX: anchored to first_vl_date so old sessions are included
-- ============================================================
DROP TABLE IF EXISTS eac_sessions_numbered_all;
CREATE TABLE eac_sessions_numbered_all AS (
    SELECT
        e.patient_id,
        e.encounter_datetime AS eac_session_date,
        e.encounter_id,
        ( SELECT ob.value_numeric
          FROM obs ob JOIN encounter enc ON ob.encounter_id = enc.encounter_id
          WHERE ob.concept_id = 856 AND ob.value_numeric >= 50 AND ob.person_id = e.patient_id
          AND enc.encounter_type = 11 AND enc.encounter_datetime <= e.encounter_datetime
          AND ob.voided = 0 AND enc.voided = 0 ORDER BY enc.encounter_datetime DESC LIMIT 1
        ) AS triggering_high_vl,
        ( SELECT enc.encounter_datetime
          FROM obs ob JOIN encounter enc ON ob.encounter_id = enc.encounter_id
          WHERE ob.concept_id = 856 AND ob.value_numeric >= 50 AND ob.person_id = e.patient_id
          AND enc.encounter_type = 11 AND enc.encounter_datetime <= e.encounter_datetime
          AND ob.voided = 0 AND enc.voided = 0 ORDER BY enc.encounter_datetime DESC LIMIT 1
        ) AS triggering_high_vl_date,
        ( SELECT COUNT(*) FROM encounter e2
          WHERE e2.patient_id = e.patient_id AND e2.encounter_type = 32
          AND e2.encounter_datetime <= e.encounter_datetime AND e2.voided = 0
          AND e2.encounter_datetime >= (
              SELECT enc.encounter_datetime FROM obs ob
              JOIN encounter enc ON ob.encounter_id = enc.encounter_id
              WHERE ob.concept_id = 856 AND ob.value_numeric >= 50 AND ob.person_id = e.patient_id
              AND enc.encounter_type = 11 AND enc.encounter_datetime <= e.encounter_datetime
              AND ob.voided = 0 AND enc.voided = 0 ORDER BY enc.encounter_datetime DESC LIMIT 1
          )
        ) AS session_number_in_episode
    FROM encounter e
    JOIN eligible_patients_all ep ON e.patient_id = ep.patient_id
    JOIN viral_load_data_all vld  ON vld.patient_id = e.patient_id
    WHERE e.encounter_type = 32
      AND e.encounter_datetime >= vld.first_vl_date
      AND e.voided = 0
);


-- ============================================================
-- TABLE 4: EAC pivot — regular table, includes triggering VL value
-- ============================================================
DROP TABLE IF EXISTS eac_cycle_pivot_all;
CREATE TABLE eac_cycle_pivot_all AS (
    SELECT
        patient_id,
        triggering_high_vl_date,
        MAX(triggering_high_vl)                        AS triggering_high_vl_value,
        CEILING(session_number_in_episode / 6)         AS cycle_number,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=1 THEN eac_session_date END) AS session_1_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=1 THEN encounter_id END)     AS session_1_encounter_id,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=2 THEN eac_session_date END) AS session_2_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=2 THEN encounter_id END)     AS session_2_encounter_id,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=3 THEN eac_session_date END) AS session_3_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=3 THEN encounter_id END)     AS session_3_encounter_id,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=4 THEN eac_session_date END) AS session_4_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=4 THEN encounter_id END)     AS session_4_encounter_id,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=5 THEN eac_session_date END) AS session_5_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=5 THEN encounter_id END)     AS session_5_encounter_id,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=6 THEN eac_session_date END) AS session_6_date,
        MAX(CASE WHEN MOD(session_number_in_episode-1,6)+1=6 THEN encounter_id END)     AS session_6_encounter_id
    FROM eac_sessions_numbered_all
    GROUP BY patient_id, triggering_high_vl_date, CEILING(session_number_in_episode/6)
);


-- ============================================================
-- TABLE 5: Regimen — current only
-- ============================================================
CREATE TEMPORARY TABLE regimen_data_all AS (
    SELECT patient.patient_id,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id = ob.value_coded
          JOIN encounter e ON ob.encounter_id = e.encounter_id
          WHERE ob.concept_id IN (164506,164513,165702,164507,164514,165703)
          AND cn.locale='en' AND cn.locale_preferred=1 AND ob.person_id=patient.patient_id
          AND e.encounter_type=13 AND ob.voided=0 AND e.voided=0
          ORDER BY ob.obs_datetime DESC LIMIT 1) AS Current_Regimen,
        ( SELECT DATE(ob.obs_datetime) FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id IN (164506,164513,165702,164507,164514,165703)
          AND ob.person_id=patient.patient_id AND e.encounter_type=13
          AND ob.voided=0 AND e.voided=0 ORDER BY ob.obs_datetime DESC LIMIT 1) AS Current_Regimen_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=165708 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.person_id=patient.patient_id AND e.encounter_type=13
          AND ob.voided=0 AND e.voided=0 ORDER BY ob.obs_datetime DESC LIMIT 1) AS Current_Regimen_Line,
        ( SELECT CASE WHEN cn.name LIKE '%Second%line%' OR cn.name LIKE '%2nd%line%' THEN 'Yes' ELSE 'No' END
          FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=165708 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.person_id=patient.patient_id AND e.encounter_type=13
          AND ob.voided=0 AND e.voided=0 ORDER BY ob.obs_datetime DESC LIMIT 1) AS Switched_To_Second_Line
    FROM patient
    JOIN eligible_patients_all ep ON patient.patient_id=ep.patient_id
    WHERE patient.voided=0
);


-- ============================================================
-- TABLE 6: Final linelist — one row per patient
-- ============================================================
CREATE TABLE final_vl_regimen_linelist_all AS (
    SELECT
        'ECEWS - SPEED' AS IP,
        (SELECT state_province FROM location WHERE location_id=8 LIMIT 1) AS State,
        (SELECT NAME FROM address_hierarchy_entry WHERE user_generated_id=@lga LIMIT 1) AS LGA,
        (SELECT global_property.property_value FROM global_property WHERE property='Facility_Datim_Code' LIMIT 1) AS Datim_Code,
        (SELECT global_property.property_value FROM global_property WHERE property='Facility_Name' LIMIT 1) AS FacilityName,
        ep.patient_id,
        ep.PepID,
        pid2.identifier AS PatientHospitalNo,
        pn.family_name  AS Surname,
        pn.given_name   AS Firstname,
        person.gender   AS Sex,
        DATE_FORMAT(person.birthdate,'%d/%m/%Y')                   AS DOB,
        TIMESTAMPDIFF(YEAR,person.birthdate,CURDATE())             AS Current_Age,
        DATE_FORMAT(ep.ART_Start_Date,'%d/%m/%Y')                  AS ART_Start_Date,
        TIMESTAMPDIFF(YEAR,person.birthdate,ep.ART_Start_Date)     AS Age_At_ART_Start,
        DATEDIFF(@endDate,ep.ART_Start_Date)                       AS Days_On_ART,
        ep.Transferred_In,
        ( SELECT DATE_FORMAT(ob.value_datetime,'%d/%m/%Y') FROM obs ob
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=160534 AND ob.person_id=ep.patient_id
          AND e.encounter_type=14 AND ob.voided=0 AND e.voided=0 LIMIT 1
        ) AS Date_Transferred_In,

        -- Regimen
        rd.Current_Regimen_Line,
        rd.Current_Regimen,
        DATE_FORMAT(rd.Current_Regimen_Date,'%d/%m/%Y') AS Current_Regimen_Date,

        -- First-ever VL (any value)
        ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id AND e.encounter_type=11
          AND ob.voided=0 AND e.voided=0 ORDER BY e.encounter_datetime ASC LIMIT 1
        ) AS First_Ever_VL_Value,
        COALESCE(
            ( SELECT DATE_FORMAT(ob_s.value_datetime,'%d/%m/%Y')
              FROM obs ob_s JOIN encounter e_s ON ob_s.encounter_id=e_s.encounter_id
              WHERE ob_s.concept_id=159951 AND ob_s.person_id=ep.patient_id
              AND e_s.encounter_datetime=(
                  SELECT MIN(e2.encounter_datetime) FROM obs ob2
                  JOIN encounter e2 ON ob2.encounter_id=e2.encounter_id
                  WHERE ob2.concept_id=856 AND ob2.person_id=ep.patient_id
                  AND e2.encounter_type=11 AND ob2.voided=0 AND e2.voided=0)
              AND ob_s.voided=0 ORDER BY ob_s.obs_datetime DESC LIMIT 1),
            ( SELECT DATE_FORMAT(e.encounter_datetime,'%d/%m/%Y') FROM obs ob
              JOIN encounter e ON ob.encounter_id=e.encounter_id
              WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id AND e.encounter_type=11
              AND ob.voided=0 AND e.voided=0 ORDER BY e.encounter_datetime ASC LIMIT 1)
        ) AS First_Ever_VL_Sample_Collection_Date,
        ( SELECT DATE_FORMAT(e.encounter_datetime,'%d/%m/%Y') FROM obs ob
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id AND e.encounter_type=11
          AND ob.voided=0 AND e.voided=0 ORDER BY e.encounter_datetime ASC LIMIT 1
        ) AS First_Ever_VL_Result_Date,

        -- First qualifying VL >= 50 (the EAC entry anchor)
        vld.first_vl_value AS First_High_VL_Value,
        COALESCE(
            DATE_FORMAT(vld.first_vl_sample_date,'%d/%m/%Y'),
            DATE_FORMAT(DATE(vld.first_vl_date),'%d/%m/%Y')
        ) AS First_High_VL_Sample_Collection_Date,
        DATE_FORMAT(vld.first_vl_date,'%d/%m/%Y') AS First_High_VL_Result_Date,

        -- Most recent VL (any value) — current clinical status
        ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id AND e.encounter_type=11
          AND ob.voided=0 AND e.voided=0 ORDER BY e.encounter_datetime DESC LIMIT 1
        ) AS Most_Recent_VL_Value,
        ( SELECT DATE_FORMAT(e.encounter_datetime,'%d/%m/%Y') FROM obs ob
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id AND e.encounter_type=11
          AND ob.voided=0 AND e.voided=0 ORDER BY e.encounter_datetime DESC LIMIT 1
        ) AS Most_Recent_VL_Result_Date,

        -- EAC episode (most recent via dedup JOIN)
        DATE_FORMAT(eac_pivot.triggering_high_vl_date,'%d/%m/%Y') AS EAC_Triggering_High_VL_Date,
        eac_pivot.triggering_high_vl_value                          AS EAC_Triggering_High_VL_Value,
        IFNULL(eac_pivot.cycle_number, 0)                           AS EAC_Cycle_Number,
        DATE_FORMAT(eac_pivot.session_1_date,'%d/%m/%Y') AS Session_1_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_1_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_1_Missed_Drug_Pickups,
        DATE_FORMAT(eac_pivot.session_2_date,'%d/%m/%Y') AS Session_2_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_2_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_2_Missed_Drug_Pickups,
        DATE_FORMAT(eac_pivot.session_3_date,'%d/%m/%Y') AS Session_3_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_3_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_3_Missed_Drug_Pickups,
        DATE_FORMAT(eac_pivot.session_4_date,'%d/%m/%Y') AS Session_4_Extended_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_4_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_4_Extended_Missed_Drug_Pickups,
        DATE_FORMAT(eac_pivot.session_5_date,'%d/%m/%Y') AS Session_5_Extended_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_5_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_5_Extended_Missed_Drug_Pickups,
        DATE_FORMAT(eac_pivot.session_6_date,'%d/%m/%Y') AS Session_6_Extended_Date,
        ( SELECT cn.name FROM obs ob JOIN concept_name cn ON cn.concept_id=ob.value_coded
          WHERE ob.concept_id=166255 AND cn.locale='en' AND cn.locale_preferred=1
          AND ob.encounter_id=eac_pivot.session_6_encounter_id AND ob.voided=0 LIMIT 1
        ) AS Session_6_Extended_Missed_Drug_Pickups,
        ( SELECT COUNT(*) FROM eac_sessions_numbered_all esn WHERE esn.patient_id=ep.patient_id
        ) AS Total_EAC_Sessions_All_Cycles,

        -- Follow-up VL anchored to latest EAC triggering date (BUG A FIX)
        -- Falls back to first_vl_date for patients with no EAC sessions.
        CASE WHEN (
            SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
            WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
            AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
            AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
            ORDER BY e.encounter_datetime ASC LIMIT 1
        ) IS NOT NULL THEN 'Yes' ELSE 'No' END AS Has_Followup_VL,

        ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
          AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
          AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
          ORDER BY e.encounter_datetime ASC LIMIT 1
        ) AS Followup_VL_Value,

        ( SELECT DATE_FORMAT(e.encounter_datetime,'%d/%m/%Y') FROM obs ob
          JOIN encounter e ON ob.encounter_id=e.encounter_id
          WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
          AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
          AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
          ORDER BY e.encounter_datetime ASC LIMIT 1
        ) AS Followup_VL_Result_Date,

        -- Sample date: concept 159951 only — blank when not recorded (no fallback)
        ( SELECT DATE_FORMAT(ob2.value_datetime,'%d/%m/%Y') FROM obs ob2
          WHERE ob2.concept_id=159951 AND ob2.person_id=ep.patient_id AND ob2.voided=0
          AND ob2.encounter_id=(
              SELECT ob.encounter_id FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
              WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
              AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
              AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
              ORDER BY e.encounter_datetime ASC LIMIT 1)
          ORDER BY ob2.obs_datetime DESC LIMIT 1
        ) AS Followup_VL_Sample_Collection_Date,

        DATEDIFF(
            ( SELECT e.encounter_datetime FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
              WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
              AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
              AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
              ORDER BY e.encounter_datetime ASC LIMIT 1 ),
            COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
        ) AS Days_Between_VL_Tests,

        CASE
            WHEN ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                   WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
                   AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
                   AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
                   ORDER BY e.encounter_datetime ASC LIMIT 1 ) < 50    THEN 'Suppressed'
            WHEN ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                   WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
                   AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
                   AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
                   ORDER BY e.encounter_datetime ASC LIMIT 1 ) < 1000  THEN 'Low Level Viremia'
            WHEN ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                   WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
                   AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
                   AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
                   ORDER BY e.encounter_datetime ASC LIMIT 1 ) >= 1000 THEN 'Unsuppressed'
            ELSE NULL
        END AS Followup_VL_Suppression_Status,

        CASE
            WHEN ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                   WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
                   AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
                   AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
                   ORDER BY e.encounter_datetime ASC LIMIT 1 ) IS NULL THEN NULL
            WHEN ( SELECT ob.value_numeric FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                   WHERE ob.concept_id=856 AND ob.person_id=ep.patient_id
                   AND e.encounter_datetime > COALESCE(eac_pivot.triggering_high_vl_date, vld.first_vl_date)
                   AND e.encounter_type=11 AND ob.voided=0 AND e.voided=0
                   ORDER BY e.encounter_datetime ASC LIMIT 1 ) BETWEEN 50 AND 999 THEN 'Yes'
            ELSE 'No'
        END AS Low_Level_Viremia,

        rd.Switched_To_Second_Line,

        CASE
            WHEN EXISTS (SELECT 1 FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                WHERE ob.person_id=ep.patient_id AND ob.concept_id=165470
                AND ob.value_coded IN (SELECT concept_id FROM concept_name WHERE NAME='Death' AND locale='en' AND locale_preferred=1)
                AND e.encounter_type=15 AND ob.voided=0 AND e.voided=0) THEN 'Death'
            WHEN EXISTS (SELECT 1 FROM obs ob JOIN encounter e ON ob.encounter_id=e.encounter_id
                WHERE ob.person_id=ep.patient_id AND ob.concept_id IN (160649,165470)
                AND ob.value_coded IN (SELECT concept_id FROM concept_name WHERE NAME='Transferred out' AND locale='en' AND locale_preferred=1)
                AND ob.voided=0 AND e.voided=0) THEN 'Transferred Out'
            WHEN (SELECT DATE_ADD(MAX(IF(obs.concept_id=165708, e2.encounter_datetime, NULL)),
                     INTERVAL (MAX(IF(obs.concept_id=159368, obs.value_numeric, NULL))+28) DAY)
              FROM obs JOIN encounter e2 ON obs.encounter_id=e2.encounter_id
              WHERE obs.person_id=ep.patient_id AND obs.concept_id IN (165708,159368)
              AND e2.encounter_type=13 AND e2.form_id=27 AND obs.voided=0 AND e2.voided=0
              AND e2.encounter_datetime <= @endDate
              GROUP BY obs.person_id) >= @endDate THEN 'Active'
            ELSE 'Lost to Follow-up'
        END AS Current_Status,
        @endDate AS Date_Generated

    FROM eligible_patients_all ep
    INNER JOIN viral_load_data_all vld ON ep.patient_id = vld.patient_id
    INNER JOIN regimen_data_all rd     ON ep.patient_id = rd.patient_id
    INNER JOIN person                  ON person.person_id = ep.patient_id
    LEFT JOIN (
        SELECT patient_id, identifier FROM patient_identifier
        WHERE identifier_type=5 AND voided=0
        GROUP BY patient_id HAVING MIN(patient_identifier_id)
    ) pid2 ON pid2.patient_id = ep.patient_id
    LEFT JOIN person_name pn ON (pn.person_id=ep.patient_id AND pn.preferred=1 AND pn.voided=0)
    LEFT JOIN (
        SELECT ecp.*
        FROM eac_cycle_pivot_all ecp
        INNER JOIN (
            SELECT patient_id, MAX(triggering_high_vl_date) AS latest_date
            FROM eac_cycle_pivot_all GROUP BY patient_id
        ) mx ON ecp.patient_id=mx.patient_id AND ecp.triggering_high_vl_date=mx.latest_date
    ) eac_pivot ON eac_pivot.patient_id = ep.patient_id
    WHERE vld.first_vl_value >= 50
    GROUP BY ep.patient_id
    ORDER BY ep.patient_id
);


-- ============================================================
-- OUTPUT
-- ============================================================
SELECT * FROM final_vl_regimen_linelist_all;

SELECT 'Total Patients'        AS Metric, COUNT(DISTINCT patient_id) AS COUNT FROM final_vl_regimen_linelist_all
UNION ALL SELECT 'Transfer-In',           COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Transferred_In='Yes'
UNION ALL SELECT 'Non-Transfer-In',       COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Transferred_In='No'
UNION ALL SELECT 'With EAC Engagement',   COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE EAC_Cycle_Number > 0
UNION ALL SELECT 'No EAC Documented',     COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE EAC_Cycle_Number = 0
UNION ALL SELECT 'With Follow-up VL',     COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Has_Followup_VL='Yes'
UNION ALL SELECT 'Suppressed (<50)',       COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Followup_VL_Suppression_Status='Suppressed'
UNION ALL SELECT 'Low Level Viremia',     COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Low_Level_Viremia='Yes'
UNION ALL SELECT 'Unsuppressed (>=1000)', COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Followup_VL_Suppression_Status='Unsuppressed'
UNION ALL SELECT 'Currently Active',      COUNT(DISTINCT patient_id) FROM final_vl_regimen_linelist_all WHERE Current_Status='Active';