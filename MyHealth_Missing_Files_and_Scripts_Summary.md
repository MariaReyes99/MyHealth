# MyHealth Dashboard Gap Analysis

## Confirmed Existing Files
- MyHealth_OECD_Dashboard_v7 (10).html
- myhealth_data_cleansing_pipeline.py
- OECDHospitalBeds.csv
- OECD_HospitalAggregates_Discharges.csv
- OECD_HospitalLengthstay.csv
- WHO_GDHM_export.csv / .xlsx
- oecd_country_metadata.csv

## Missing or Likely Missing Scripts
### 1. Dashboard Data Builder (HIGH PRIORITY)
Purpose:
- Creates ALOS_ALL
- Creates BEDS_ALL
- Creates GDHM_ALL
- Creates COUNTRY_META
- Exports JavaScript arrays or JSON consumed by the dashboard.

Suggested name:
- build_dashboard_data.py

### 2. OECD/WHO Fetch Script
Purpose:
- Pull latest OECD datasets
- Pull WHO GDHM datasets
- Save into raw/ folder

Suggested name:
- fetch_myhealth_source_data.py

### 3. Join Validation Script
Purpose:
- Validate country matches between OECD and WHO
- Detect unmatched countries
- Detect naming inconsistencies

Suggested name:
- validate_country_joins.py

### 4. Dashboard Publish Script
Purpose:
- Insert generated datasets into HTML
- Refresh dashboard automatically

Suggested name:
- publish_dashboard.py

## Missing ETL Outputs Required By Dashboard
### Dashboard Datasets
- ALOS_ALL.csv/json
- BEDS_ALL.csv/json
- GDHM_ALL.csv/json
- COUNTRY_META.csv/json

### Metadata Outputs
- dashboard_metadata.json
- metadata_hospital_aggregates.json
- metadata_hospital_beds.json
- metadata_diagnostic_alos.json
- metadata_who_gdhm.json
- metadata_country_metadata.json

### Quality Outputs
- quality_report.json
- country_join_validation.json
- outlier_report.json
- data_dictionary.json

## Evidence Dashboard Uses Prebuilt Data
The HTML contains embedded datasets such as:
- const ALOS_ALL = [...]
- const BEDS_ALL = [...]

This indicates transformed data was generated before the dashboard was built.

## Recommended Future Architecture
Raw OECD/WHO Downloads
    -> Fetch Script
    -> ETL/Cleansing Pipeline
    -> Validation Layer
    -> Dashboard Dataset Builder
    -> Metadata Generation
    -> Dashboard HTML

## Overall Assessment
Current dashboard: ~90% complete.
Current ETL pipeline: ~60-70% complete.
Most likely missing artifact: the script that generated the embedded JavaScript datasets inside the HTML.
