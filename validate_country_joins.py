"""
validate_country_joins.py
===============================================================================
The missing "Join Validation Script" identified in
MyHealth_Missing_Files_and_Scripts_Summary.md.

Cross-checks country names across all cleaned datasets to catch naming
mismatches (e.g. "Turkiye" vs "Türkiye", "Korea" vs "South Korea") BEFORE
they silently produce a country that's missing from one chart but present
in another. Run this before build_dashboard_data.py in the pipeline.

Usage:
    python validate_country_joins.py --input-dir ./clean
"""

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("validate_country_joins")


def run(input_dir: Path) -> dict:
    files = {
        "hospital_aggregates": "hospital_aggregates_clean.csv",
        "hospital_beds": "hospital_beds_clean.csv",
        "diagnostic_alos": "diagnostic_alos_clean.csv",
        "who_gdhm": "who_gdhm_clean.csv",
        "country_metadata": "country_metadata_clean.csv",
    }

    country_sets = {}
    for name, filename in files.items():
        path = input_dir / filename
        if not path.exists():
            log.warning("Skipping %s — file not found", filename)
            continue
        df = pd.read_csv(path)
        if "country" not in df.columns:
            log.warning("Skipping %s — no 'country' column", filename)
            continue
        country_sets[name] = set(df["country"].dropna().unique())

    all_countries = set()
    for s in country_sets.values():
        all_countries |= s

    report = {"datasets": {}, "unmatched_by_dataset": {}, "in_no_metadata": []}

    for name, countries in country_sets.items():
        report["datasets"][name] = len(countries)
        missing_here = sorted(all_countries - countries)
        if missing_here:
            report["unmatched_by_dataset"][name] = missing_here

    if "country_metadata" in country_sets:
        no_region = sorted(all_countries - country_sets["country_metadata"])
        report["in_no_metadata"] = no_region
        if no_region:
            log.warning(
                "%d countries appear in data but have no region mapping "
                "(will default to region='Other'): %s",
                len(no_region), ", ".join(no_region),
            )

    # Fuzzy near-duplicate check (catches likely typos/encoding variants)
    near_dupes = []
    countries_list = sorted(all_countries)
    normalized = {c: c.lower().replace("'", "").replace("’", "").replace("-", " ").strip() for c in countries_list}
    seen = {}
    for c, norm in normalized.items():
        if norm in seen and seen[norm] != c:
            near_dupes.append((seen[norm], c))
        else:
            seen[norm] = c
    report["possible_near_duplicates"] = near_dupes

    log.info("Total distinct countries across all datasets: %d", len(all_countries))
    if report["unmatched_by_dataset"]:
        log.warning("Found country coverage gaps between datasets — see report.")
    else:
        log.info("All datasets have identical country coverage.")
    if near_dupes:
        log.warning("Possible near-duplicate country names: %s", near_dupes)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate country name consistency across cleaned MyHealth datasets")
    parser.add_argument("--input-dir", type=Path, default=Path("./clean"))
    parser.add_argument("--output-file", type=Path, default=Path("./clean/country_join_validation.json"))
    args = parser.parse_args()
    result = run(args.input_dir)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {args.output_file}")
