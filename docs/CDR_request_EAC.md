# Request to the CDR team: EAC line list for the SPEED KPI dashboard

**From:** ECEWS SPEED SI (Caleb Gwaza)
**Purpose:** show enhanced adherence counselling (EAC) on the SPEED KPI dashboard (Viral Load page), using the method the clinical team agreed in `EAC_Script_Version11.sql` (attached).

## What we need
1. **Run `EAC_Script_Version11.sql` against every SPEED facility EMR**, on the same schedule as the `linelist` refresh (weekly at minimum).
2. **Set `@endDate` to the extraction date** on each run. In the attached copy it is hard-coded to `2026-06-22`.
3. **Load the combined output into the CDR** as `public.eac_linelist`, one row per client per facility, replacing the previous load each time.
4. **Keep the column names exactly as the script outputs them.** The pipeline reads these, case-insensitive:

| Column | Used for |
|---|---|
| `Datim_Code`, `PepID`, `PatientHospitalNo` | Identifying and linking the client |
| `State`, `FacilityName`, `Sex`, `Current_Age` | Filters and breakdowns |
| `Current_Status`, `Transferred_In` | Active-client filter |
| `First_High_VL_Value`, `First_High_VL_Result_Date` | EAC entry, when no EAC is documented |
| `EAC_Triggering_High_VL_Value`, `EAC_Triggering_High_VL_Date` | The latest EAC episode |
| `EAC_Cycle_Number` | EAC cycle |
| `Session_1_Date` … `Session_3_Date`, `Session_4_Extended_Date` … `Session_6_Extended_Date` | Standard and extended EAC |
| `Total_EAC_Sessions_All_Cycles` | Sessions |
| `Followup_VL_Value`, `Followup_VL_Result_Date`, `Followup_VL_Suppression_Status` | Repeat VL and outcome |
| `Switched_To_Second_Line` | Regimen switch |
| `Date_Generated` | Data date (`@endDate`) |

5. **Please leave out `Surname`, `Firstname` and `DOB`.** The dashboard never reads them, and they shouldn't sit in the CDR for this purpose.

## Notes from the dashboard side
- The existing `public.eac_line_list` table has a different layout, and it is currently empty, so we're asking for a separate table with the script's own columns.
- Once `public.eac_linelist` has rows, the next scheduled dashboard run picks it up automatically. Nothing else needs to change.
- **Contact:** caleborjime@gmail.com
