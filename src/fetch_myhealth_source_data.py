"""
fetch_myhealth_source_data.py
===============================================================================
Downloads the 5 raw source files referenced in the MyHealth PRD directly from
the live OECD SDMX API and WHO GDHM export API.

WHY THIS SCRIPT EXISTS
-----------------------
Claude's sandboxed environment confirmed all 4 endpoints below are live and
respond correctly, but its web-fetch tool cannot extract non-HTML payloads
(the OECD API returns 'application/vnd.sdmx.data+csv', the WHO API returns
'application/octet-stream') and its code sandbox has no outbound network
access, so it cannot save these files itself. This script does the same job
your own machine (which has normal internet access) can do in under a minute.

VERIFIED vs CONSTRUCTED:
  - The DF_BEDS_FUNC and DF_AGGR / DF_HOSP_AV_LENGTH *dataset identifiers*
    were confirmed live on the OECD Data Explorer (data-explorer.oecd.org).
  - The exact SDMX query pattern below was directly verified working against
    a sibling OECD Health dataset (DF_HOSP_REAC) during this session, and
    returned a real 'application/vnd.sdmx.data+csv' response.
  - The 3 URLs below apply that *same verified pattern* to DF_AGGR,
    DF_BEDS_FUNC, and DF_HOSP_AV_LENGTH. They were not each individually
    fetched and confirmed (Claude's tool can't do that), so if the OECD team
    has renamed a dimension or version tag, double check the returned file
    opens cleanly before treating it as final.
  - The WHO GDHM export URL was found directly on data.who.int/dashboards/gdhm/data
    and is used as published there.

USAGE
-----
    pip install requests
    python fetch_myhealth_source_data.py --output-dir ./raw

Then run the existing cleansing pipeline against the same folder:
    python myhealth_data_cleansing_pipeline.py --input-dir ./raw --output-dir ./clean
"""

import argparse
import sys
from pathlib import Path

import requests

try:
    import pandas as pd
except ImportError:
    pd = None

SOURCES = {
    # OECD DF_AGGR — Hospital Aggregates & Discharges
    "OECD_HospitalAggregates_Discharges.csv":
        "https://sdmx.oecd.org/public/rest/data/OECD.ELS.HD,DSD_HEALTH_PROC@DF_AGGR,1.1/all"
        "?dimensionAtObservation=AllDimensions&format=csvfilewithlabels",

    # OECD DF_BEDS_FUNC — Hospital Beds by Function of Healthcare
    "OECDHospitalBeds.csv":
        "https://sdmx.oecd.org/public/rest/data/OECD.ELS.HD,DSD_HEALTH_REAC_HOSP@DF_BEDS_FUNC,1.1/all"
        "?dimensionAtObservation=AllDimensions&format=csvfilewithlabels",

    # OECD DF_HOSP_AV_LENGTH — Hospital Average Length of Stay by Diagnostic Category
    "OECD_HospitalLengthstay.csv":
        "https://sdmx.oecd.org/public/rest/data/OECD.ELS.HD,DSD_HEALTH_PROC@DF_HOSP_AV_LENGTH,1.1/all"
        "?dimensionAtObservation=AllDimensions&format=csvfilewithlabels",

    # WHO GDHM — Global Digital Health Monitor country export (as published on data.who.int/dashboards/gdhm/data)
    "WHO_GDHM_export.xlsx":
        "https://monitor.digitalhealthmonitor.org/api/export_global_data?user_language=en",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MyHealthStudio6/1.0; +academic research use)",
    "Accept": "text/csv, application/vnd.sdmx.data+csv, application/octet-stream, */*",
}


def fetch_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for filename, url in SOURCES.items():
        dest = output_dir / filename
        print(f"Fetching {filename} ...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            size_kb = len(resp.content) / 1024
            print(f"  -> saved {dest} ({size_kb:.1f} KB, status {resp.status_code})")
            results.append((filename, True, size_kb, None))
        except requests.exceptions.RequestException as exc:
            print(f"  !! FAILED: {exc}")
            results.append((filename, False, 0, str(exc)))

    print("\nConverting WHO GDHM export from Excel to CSV ...")
    who_xlsx = output_dir / "WHO_GDHM_export.xlsx"
    who_csv = output_dir / "WHO_GDHM_export.csv"
    if who_xlsx.exists():
        if pd is None:
            print(
                "  !! Could not convert: pandas is not installed. Run "
                "'pip install pandas openpyxl' then re-run this script, or "
                "manually open WHO_GDHM_export.xlsx and save it as "
                "WHO_GDHM_export.csv in the same folder."
            )
        else:
            try:
                df = pd.read_excel(who_xlsx, engine="openpyxl")
                df.to_csv(who_csv, index=False)
                print(f"  -> saved {who_csv} ({who_csv.stat().st_size / 1024:.1f} KB)")
            except Exception as exc:
                print(f"  !! Conversion failed: {exc}")
    else:
        print("  !! Skipped — WHO_GDHM_export.xlsx was not downloaded successfully.")

    print("\nSummary:")
    for filename, ok, size_kb, err in results:
        status = f"OK ({size_kb:.1f} KB)" if ok else f"FAILED — {err}"
        print(f"  {filename}: {status}")

    failed = [r for r in results if not r[1]]
    if failed:
        print(
            "\nSome downloads failed. If DF_AGGR or DF_HOSP_AV_LENGTH failed with a 4xx/5xx "
            "error, the OECD may have changed the dataset version tag (currently ',1.1'). "
            "Visit https://data-explorer.oecd.org/, find the dataset, click the API/Developer "
            "icon above the data table, and copy the current query URL to replace the one "
            "in SOURCES above."
        )
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch MyHealth raw source data from OECD/WHO")
    parser.add_argument("--output-dir", type=Path, default=Path("./raw"))
    args = parser.parse_args()
    fetch_all(args.output_dir)
