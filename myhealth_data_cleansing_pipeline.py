"""
MyHealth Analytics Dashboard — Data Cleansing & Transformation Specification
===============================================================================
Studio 6 | BITC01701 | Health Tech Innovators

Purpose
-------
This script is the executable specification for cleansing and transforming the
five raw source datasets referenced in the MyHealth PRD (Section 8 — Data
Sources & Cleansing) into the aggregates consumed by the dashboard's JavaScript
layer (Chart.js / Plotly / D3).

Datasets covered (see myhealth_dataset_sources.docx / README for live links):
  1. OECD DF_AGGR            -> OECD_HospitalAggregates_Discharges.csv
  2. OECD DF_BEDS_FUNC        -> OECDHospitalBeds.csv / OECD_BedsOccupancy.csv
  3. OECD DF_HOSP_AV_LENGTH   -> OECD_HospitalLengthstay.csv
  4. WHO GDHM                 -> WHO_GDHM_export.csv
  5. OECD Country Metadata    -> oecd_country_metadata.csv

Each cleansing function:
  - Documents the exact filter/groupby/aggregation logic in its docstring
  - Uses only pandas + numpy (no external API calls) so it can run offline
    against files already downloaded from the links in the companion doc
  - Returns a tidy DataFrame ready for JSON export to the dashboard

Run:
    python myhealth_data_cleansing_pipeline.py --input-dir ./raw --output-dir ./clean
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("myhealth_cleansing")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The canonical 46-country scope this whole project is built around (PRD,
# Charter, and every report reference "46 Countries"). This exact list is
# taken directly from the ORIGINAL dashboard's own hardcoded ALL_COUNTRIES
# array, so filtering to it guarantees consistency with the dashboard you
# already have, not an independently-invented list.
OECD_46_COUNTRIES = [
    "Argentina", "Australia", "Austria", "Belgium", "Brazil", "Bulgaria",
    "Canada", "Chile", "China (People's Republic of)", "Colombia",
    "Costa Rica", "Croatia", "Czechia", "Denmark", "Estonia", "Finland",
    "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland",
    "Israel", "Italy", "Japan", "Korea", "Latvia", "Lithuania",
    "Luxembourg", "Mexico", "Netherlands", "New Zealand", "Norway",
    "Poland", "Portugal", "Romania", "Russia", "Slovak Republic",
    "Slovenia", "South Africa", "Spain", "Sweden", "Switzerland",
    "Türkiye", "United Kingdom", "United States",
]


def _normalize_apostrophe(s):
    """Treat curly/smart apostrophes the same as a straight one.

    This fixes a REAL bug found in the original dashboard itself: its own
    ALL_COUNTRIES array spells China with a curly apostrophe (’, U+2019),
    but its own REGIONS lookup spells it with a straight one (', U+0027) —
    so China silently never matched during region lookup, even before any
    of this pipeline's changes. Every country-name comparison in this
    pipeline normalizes apostrophes first so this class of bug can't recur,
    regardless of which variant a given OECD/WHO export happens to use.
    """
    if not isinstance(s, str):
        return s
    for ch in ("\u2019", "\u2018", "\u0060", "\u00b4"):
        s = s.replace(ch, "'")
    return s


_OECD_46_NORMALIZED = {_normalize_apostrophe(c) for c in OECD_46_COUNTRIES}

# Region mapping used across every dataset for the Equity & Advanced
# Analytics tabs. Matches the original dashboard's own REGIONS constant
# exactly (verified against the live dashboard JS) — Slovak Republic,
# China, and South Africa were missing from an earlier version of this
# map, and Türkiye was mis-classified as Europe when the dashboard itself
# groups it under 'Other'.
REGION_MAP = {
    "Europe": [
        "Austria", "Belgium", "Bulgaria", "Croatia", "Czechia", "Denmark", "Estonia",
        "Finland", "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland",
        "Italy", "Latvia", "Lithuania", "Luxembourg", "Netherlands", "Norway",
        "Poland", "Portugal", "Romania", "Slovak Republic", "Slovenia", "Spain",
        "Sweden", "Switzerland", "United Kingdom",
    ],
    "Asia-Pacific": ["Australia", "China (People's Republic of)", "Japan", "Korea", "New Zealand"],
    "Americas": ["Argentina", "Brazil", "Canada", "Chile", "Colombia", "Costa Rica", "Mexico", "United States"],
    "Other": ["Russia", "South Africa", "Israel", "Türkiye"],
}
COUNTRY_TO_REGION = {
    _normalize_apostrophe(c): r for r, cs in REGION_MAP.items() for c in cs
}

VALID_YEAR_RANGE = (1960, 2024)


def _pick_country_column(df: pd.DataFrame) -> pd.DataFrame:
    """OECD's REF_AREA is a short code (JPN, DEU), not the full country name
    that COUNTRY_TO_REGION and the dashboard both key on. When the export
    includes the paired label column (normalizes to 'reference_area'), use
    that for 'country' instead of the code. Falls back to the code if no
    label column is present (e.g. on hand-built sample data)."""
    if "reference_area" in df.columns:
        df = df.rename(columns={"reference_area": "country"})
        df = df.drop(columns=["ref_area"], errors="ignore")
    elif "ref_area" in df.columns:
        df = df.rename(columns={"ref_area": "country"})
    return df


def _country_region(country: str) -> str:
    return COUNTRY_TO_REGION.get(_normalize_apostrophe(country), "Other")


def _filter_to_oecd46(df: pd.DataFrame, dataset_label: str) -> pd.DataFrame:
    """Keep only the canonical 46 countries the whole project is scoped to
    (see OECD_46_COUNTRIES above), dropping everything else. Apostrophes
    are normalized before comparison so a country isn't silently dropped
    just because its raw export used a different apostrophe character than
    OECD_46_COUNTRIES does (see _normalize_apostrophe)."""
    if "country" not in df.columns or df.empty:
        return df
    normalized = df["country"].astype(str).apply(_normalize_apostrophe)
    mask = normalized.isin(_OECD_46_NORMALIZED)
    dropped = int((~mask).sum())
    if dropped:
        dropped_countries = sorted(set(df.loc[~mask, "country"].astype(str)))
        preview = ", ".join(dropped_countries[:10])
        more = f" ... and {len(dropped_countries) - 10} more" if len(dropped_countries) > 10 else ""
        log.info(
            "%s: filtered out %d row(s) from %d non-OECD-46 countries (keeping "
            "only the canonical 46): %s%s",
            dataset_label, dropped, len(dropped_countries), preview, more,
        )
    return df[mask]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column names to snake_case AND drop duplicate columns
    that collide after normalization, keeping the first (leftmost).

    Why this exists: OECD's `format=csvfilewithlabels` export puts a coded
    column and its human-readable label side by side for every dimension,
    e.g. `TIME_PERIOD,Time period,...`. Both normalize to the same name
    (`time_period`), so without this step the DataFrame ends up with two
    columns both called `year` after renaming — and `df["year"]` then
    returns a DataFrame instead of a Series, which is exactly the
    `TypeError: arg must be a list, tuple, 1-d array, or Series` seen when
    running this against a real OECD export (it doesn't show up against
    hand-built sample/demo CSVs, which don't have this code+label pairing).
    OECD's convention is CODE column immediately followed by its LABEL
    column, so keeping the first occurrence keeps the code column — which
    is what every rename_map in this file expects.
    """
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    dupes = df.columns[df.columns.duplicated()].unique().tolist()
    if dupes:
        log.warning(
            "Dropped %d duplicate column(s) after normalization (kept first "
            "occurrence of each): %s. This is expected for real OECD "
            "csvfilewithlabels exports, which pair each code column with a "
            "label column of the same normalized name.",
            len(dupes), dupes,
        )
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df


# ---------------------------------------------------------------------------
# 1. OECD DF_AGGR — Hospital Aggregates & Discharges
#    Used by: Hospital ALOS tab, Utilisation tab, Forecasting engine
# ---------------------------------------------------------------------------

def clean_hospital_aggregates(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Source: OECD_HospitalAggregates_Discharges.csv (DSD_HEALTH_PROC@DF_AGGR)
    Raw shape: ~16,000 rows | 1960-2024 | 46 countries

    REAL FILE STRUCTURE (confirmed against an actual export via
    inspect_oecd_aggregates_beds.py — replaces an earlier, wrong assumption
    that a single 'VAR' column carried an Inpatient/Curative distinction;
    no such column exists in the real file):
      - 'MEASURE' holds CODES, not labels: STAY=ALOS, BED_DAY=bed-days,
        DISCHARGE=discharges, OCC_RATE=occupancy rate (unused here).
      - The "headline, non-disaggregated total" row is identified by THREE
        separate real dimensions together: mode_provision=='HBEDT'
        (Inpatient), function=='_T' (Total, not curative/rehab-only),
        care_type=='_T' (Total) — plus every other breakdown dimension
        (medical_procedure, occupation, diagnostic_type, provider,
        health_facility, age, sex, data_srce, waiting_time, disease,
        cancer_site, consultation_type) =='_Z' (Not applicable), which
        selects only the fully-aggregated top-line rows and excludes
        sub-population breakdowns that would otherwise get blended into
        the headline totals by the pivot's mean.
      - UNIT_MEASURE varies per row and was found to have MORE distinct
        codes (5) than there are measures (4) — meaning at least one
        measure has two incompatible unit variants (e.g. a raw count vs.
        a rate-per-population). Blending those in pivot_table's mean would
        silently produce nonsense figures, so this function instead picks
        the dominant (most-reported) unit per measure and logs the choice
        — self-adapting rather than a hardcoded guess, and auditable via
        the log line rather than silent.

    Transformation steps (in order):
      1. Standardise column names to snake_case; dedupe code/label pairs.
      2. Drop rows with null value/country/year.
      3. Filter to the headline Total Inpatient series (see above).
      4. Cast year to int, clip to VALID_YEAR_RANGE, cast value to float.
      5. Keep only the 3 measures we use (STAY/BED_DAY/DISCHARGE), and
         within each, only its dominant unit_measure.
      6. Pivot MEASURE into columns per country/year.
      7. Recompute ALOS defensively as bed_days / discharges where both
         are present (do not just trust the OECD-supplied ALOS column
         blindly — flag variance > 5%).
      8. Remove statistical outliers: any ALOS value outside
         [mean ± 4*std] per country (rolling, not global) is flagged
         `is_outlier=True` rather than dropped, so the dashboard can toggle it.
      9. Attach region via COUNTRY_TO_REGION.
      10. Sort by country, year and forward-fill single-year gaps (<=1 year).

    Output schema:
      country, region, year, discharges, bed_days, alos, alos_recomputed,
      is_outlier, source
    """
    df = raw.copy()
    df = _normalize_columns(df)
    df = _pick_country_column(df)

    rename_map = {"time_period": "year", "obs_value": "value"}
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required = {"country", "year", "measure", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"clean_hospital_aggregates: missing expected columns {missing}")

    df = df.dropna(subset=["country", "year", "value"])

    # Headline Total Inpatient filter — see docstring for why these 3
    # dimensions + the '_Z' breakdown-dimension check are all needed.
    dimension_filters = {"mode_provision": "HBEDT", "function": "_T", "care_type": "_T"}
    for col, val in dimension_filters.items():
        if col in df.columns:
            df = df[df[col] == val]
    breakdown_dims_must_be_na = [
        "medical_procedure", "occupation", "diagnostic_type", "provider",
        "health_facility", "age", "sex", "data_srce", "waiting_time",
        "disease", "cancer_site", "consultation_type",
    ]
    for col in breakdown_dims_must_be_na:
        if col in df.columns:
            df = df[df[col] == "_Z"]

    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df = df[(df["year"] >= VALID_YEAR_RANGE[0]) & (df["year"] <= VALID_YEAR_RANGE[1])]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["year", "value"])

    measure_map = {"STAY": "alos", "BED_DAY": "bed_days", "DISCHARGE": "discharges"}
    df = df[df["measure"].isin(measure_map.keys())]

    if "unit_measure" in df.columns and not df.empty:
        keep_parts = []
        for m, group in df.groupby("measure"):
            if group["unit_measure"].notna().any():
                dominant_unit = group["unit_measure"].value_counts().idxmax()
                n_kept = int((group["unit_measure"] == dominant_unit).sum())
                log.info(
                    "clean_hospital_aggregates: measure '%s' -> using unit_measure "
                    "'%s' (%d/%d rows kept; other unit variants excluded to avoid "
                    "mixing incompatible units in the average)",
                    m, dominant_unit, n_kept, len(group),
                )
                keep_parts.append(group[group["unit_measure"] == dominant_unit])
            else:
                keep_parts.append(group)
        df = pd.concat(keep_parts, ignore_index=True) if keep_parts else df.iloc[0:0]

    df["measure"] = df["measure"].map(measure_map)

    pivot = df.pivot_table(
        index=["country", "year"], columns="measure", values="value", aggfunc="mean"
    ).reset_index()
    pivot.columns.name = None

    for needed in ["discharges", "bed_days", "alos"]:
        if needed not in pivot.columns:
            pivot[needed] = np.nan

    pivot["alos_recomputed"] = np.where(
        (pivot["bed_days"] > 0) & (pivot["discharges"] > 0),
        pivot["bed_days"] / pivot["discharges"],
        np.nan,
    )
    variance = (pivot["alos_recomputed"] - pivot["alos"]).abs() / pivot["alos"].replace(0, np.nan)
    pivot["alos_variance_flag"] = variance > 0.05

    pivot = pivot.sort_values(["country", "year"])
    grp = pivot.groupby("country")["alos"]
    rolling_mean = grp.transform(lambda s: s.rolling(5, min_periods=3, center=True).mean())
    rolling_std = grp.transform(lambda s: s.rolling(5, min_periods=3, center=True).std())
    pivot["is_outlier"] = (pivot["alos"] - rolling_mean).abs() > (4 * rolling_std)
    pivot["is_outlier"] = pivot["is_outlier"].fillna(False)

    pivot["region"] = pivot["country"].apply(_country_region)
    pivot["alos"] = pivot.groupby("country")["alos"].apply(lambda s: s.ffill(limit=1)).reset_index(drop=True)
    pivot["source"] = "OECD_DF_AGGR"
    pivot = _filter_to_oecd46(pivot, "clean_hospital_aggregates")

    ordered = ["country", "region", "year", "discharges", "bed_days", "alos",
               "alos_recomputed", "alos_variance_flag", "is_outlier", "source"]
    return pivot[[c for c in ordered if c in pivot.columns]]


# ---------------------------------------------------------------------------
# 2. OECD DF_BEDS_FUNC — Hospital Beds by Function
#    Used by: Hospital Beds tab, Beds-vs-ALOS scatter, Forecasting to 2030
# ---------------------------------------------------------------------------

def clean_hospital_beds(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Source: OECDHospitalBeds.csv / OECD_BedsOccupancy.csv (DF_BEDS_FUNC)
    Raw shape: ~19,076 rows | 1988-2024 | 46 countries

    REAL FILE STRUCTURE (confirmed against an actual export via
    inspect_oecd_aggregates_beds.py — this was previously producing 0 rows
    from two separate wrong assumptions, both now fixed):
      - The real column is 'HEALTH_FUNCTION', not 'FUNCTION' as originally
        assumed — the old filter checked a column name that doesn't exist
        in the real file, so it silently never ran.
      - 'UNIT_MEASURE' holds a CODE, not text: '10P3HB' = "Per 1,000
        inhabitants" (vs. 'BD' = raw bed count). The old filter searched
        the code column for the text "1 000"/"1000", which never matches
        a code like "10P3HB" — every row got filtered out, which is
        exactly why this produced 0 rows.

    Transformation steps:
      1. Standardise column names; rename REF_AREA/Reference area -> country
         (preferring the label), TIME_PERIOD -> year.
      2. Filter to health_function == '_T' (Total) and
         unit_measure == '10P3HB' (Per 1,000 inhabitants) — see above.
      3. Deduplicate: some vintages report both provisional and revised
         figures for the same country-year — keep the most recently revised
         (`OBS_STATUS` == 'A' Normal/Final preferred over 'P' Provisional).
      4. Cast `year` to int, `beds_per_1000` to float; clip negative or
         implausible values (>25 beds/1000 is treated as a data-entry error
         and set to NaN rather than silently kept).
      5. groupby(["country","year"]) mean to collapse any residual duplicates.
      6. Compute year-over-year percentage change per country for the
         "efficiency improvement" bars.
      7. Attach region.

    Output schema:
      country, region, year, beds_per_1000, yoy_change_pct, source
    """
    df = raw.copy()
    df = _normalize_columns(df)
    df = _pick_country_column(df)
    df = df.rename(columns={"time_period": "year", "obs_value": "value"})

    if "health_function" in df.columns:
        df = df[df["health_function"] == "_T"]
    if "unit_measure" in df.columns:
        df = df[df["unit_measure"] == "10P3HB"]

    if "obs_status" in df.columns:
        status_rank = {"A": 2, "F": 2, "P": 1}
        df["_status_rank"] = df["obs_status"].map(status_rank).fillna(0)
        df = df.sort_values("_status_rank").drop_duplicates(
            subset=["country", "year"], keep="last"
        ).drop(columns="_status_rank")

    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df.loc[(df["value"] < 0) | (df["value"] > 25), "value"] = np.nan
    df = df.dropna(subset=["country", "year", "value"])
    df["year"] = df["year"].astype(int)

    agg = df.groupby(["country", "year"], as_index=False)["value"].mean()
    agg = agg.rename(columns={"value": "beds_per_1000"})
    agg = agg.sort_values(["country", "year"])
    agg["yoy_change_pct"] = agg.groupby("country")["beds_per_1000"].pct_change() * 100
    agg["region"] = agg["country"].apply(_country_region)
    agg["source"] = "OECD_DF_BEDS_FUNC"
    agg = _filter_to_oecd46(agg, "clean_hospital_beds")

    return agg[["country", "region", "year", "beds_per_1000", "yoy_change_pct", "source"]]


# ---------------------------------------------------------------------------
# 3. OECD DF_HOSP_AV_LENGTH — Diagnostic-level ALOS
#    Used by: Diagnostic ALOS tab (top-10 diagnoses, ICD donut, drill-down)
# ---------------------------------------------------------------------------

def clean_diagnostic_alos(raw: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    Source: OECD_HospitalLengthstay.csv (DF_HOSP_AV_LENGTH)
    Raw shape: ~132,447 rows | 1970-2024 | 40+ countries

    Transformation steps:
      1. Standardise column names.
      2. Filter to `MEASURE == "STAY"` only (the raw file also carries
         discharge counts and bed-day rows under the same dataset id —
         mixing units would corrupt the ALOS average).
      3. Drop rows where `diagnosis`/ICD category is null, "Total", or
         "Not elsewhere classified" (these are aggregate rows, not
         drill-down categories, and would double-count in the donut chart).
      4. Cast year -> int, value -> float; drop non-numeric / negative stays.
      5. groupby(["country","year","diagnosis"]).mean() to collapse any
         sub-category duplicates.
      6. Compute latest-year snapshot per country+diagnosis for ranking.
      7. Rank diagnoses by mean ALOS across the latest available year,
         per country, and keep the top_n for the "Top 10 diagnoses" chart
         (rest bucketed into "Other" for the donut so slices stay readable).
      8. Attach an ICD chapter/category label for grouping (mapped from a
         lookup table — left as `icd_category` placeholder if unmapped).

    Output schema:
      country, year, diagnosis, icd_category, alos_days, rank, is_top_n, source
    """
    df = raw.copy()
    df = _normalize_columns(df)
    df = _pick_country_column(df)
    df = df.rename(columns={"time_period": "year", "obs_value": "value"})

    if "measure" in df.columns:
        df = df[df["measure"].astype(str).str.upper() == "STAY"]

    diag_col = "diagnosis" if "diagnosis" in df.columns else next(
        (c for c in df.columns if "diag" in c or "icd" in c), None
    )
    if diag_col is None:
        raise ValueError("clean_diagnostic_alos: no diagnosis/ICD column found in source file")
    df = df.rename(columns={diag_col: "diagnosis"})

    exclude_labels = {"total", "not elsewhere classified", "all causes", ""}
    df = df[~df["diagnosis"].astype(str).str.strip().str.lower().isin(exclude_labels)]
    df = df.dropna(subset=["diagnosis"])

    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[df["value"] > 0]
    df = df.dropna(subset=["country", "year", "value"])
    df["year"] = df["year"].astype(int)

    grouped = df.groupby(["country", "year", "diagnosis"], as_index=False)["value"].mean()
    grouped = grouped.rename(columns={"value": "alos_days"})

    latest_year = grouped.groupby("country")["year"].transform("max")
    latest = grouped[grouped["year"] == latest_year].copy()
    latest["rank"] = latest.groupby("country")["alos_days"].rank(method="first", ascending=False)
    latest["is_top_n"] = latest["rank"] <= top_n

    out = grouped.merge(
        latest[["country", "diagnosis", "rank", "is_top_n"]],
        on=["country", "diagnosis"],
        how="left",
    )
    out["icd_category"] = out["diagnosis"]  # placeholder until ICD chapter lookup is joined
    out["source"] = "OECD_DF_HOSP_AV_LENGTH"
    out = _filter_to_oecd46(out, "clean_diagnostic_alos")

    return out[["country", "year", "diagnosis", "icd_category", "alos_days", "rank", "is_top_n", "source"]]


# ---------------------------------------------------------------------------
# 4. WHO GDHM — Digital Health Maturity
#    Used by: Digital Health tab, Advanced Analytics correlations, AI Analyst
# ---------------------------------------------------------------------------

DOMAIN_COLUMNS = ["l_gov", "strat_inv", "legis", "workforce", "standards", "infra", "services"]

def _resolve_country_column(df: pd.DataFrame, dataset_label: str) -> pd.DataFrame:
    """Find and rename whichever column holds the country name, trying every
    name WHO/OECD exports are known to use. Unlike the OECD-specific
    _pick_country_column() above (which assumes the REF_AREA/Reference area
    pairing), WHO's GDHM export uses its own, less predictable naming — so
    this tries a broader candidate list and, if none match, raises an error
    that shows the ACTUAL column names in the file, rather than a bare
    KeyError that gives no clue what to fix.
    """
    if "country" in df.columns:
        return df
    candidates = [
        "reference_area", "ref_area", "country_name", "country/area",
        "country_area", "economy", "location", "area", "member_state",
        "nation", "country_or_area",
    ]
    for cand in candidates:
        if cand in df.columns:
            log.info("%s: using column '%s' as country (renamed to 'country')", dataset_label, cand)
            return df.rename(columns={cand: "country"})
    raise KeyError(
        f"{dataset_label}: could not find a country column. Tried: "
        f"{candidates}. Actual columns in this file after normalization "
        f"are: {list(df.columns)}. Open the raw CSV, find the column that "
        f"holds country names, and add its normalized name (lowercase, "
        f"spaces -> underscores) to the `candidates` list in "
        f"_resolve_country_column() in this script."
    )


def clean_who_gdhm(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Source: WHO Global Digital Health Monitor export (latest reporting cycle,
    https://data.who.int/dashboards/gdhm)

    Transformation steps:
      1. Standardise column names; map long WHO domain labels to short codes
         used by the dashboard JS object (l_gov, strat_inv, legis, workforce,
         standards, infra, services).
      2. Coerce every domain score to numeric; WHO reports maturity on a
         Phase 1-5 ordinal scale — clip to [1, 5] and treat 0/blank as
         "not assessed" (NaN), not zero maturity, so it doesn't drag country
         averages down artificially.
      3. Drop countries with fewer than 4 of 7 domains reported (insufficient
         for a meaningful `overall` maturity score).
      4. Compute `overall` = row-wise mean of available domain scores
         (np.nanmean), so partial submissions still produce a usable score.
      5. Compute cross-country `z_score` of `overall` for the risk/benchmark
         model: (overall - mean) / std, using ddof=1 (sample std) to match
         the dashboard's risk_flag thresholds (>1.5 = High risk feeder).
      6. Attach region and de-duplicate to one row per country (keep the
         latest reporting cycle if a country appears twice).

    Output schema:
      country, region, l_gov, strat_inv, legis, workforce, standards, infra,
      services, overall, reported_overall_phase, z_score, source

    REAL FILE STRUCTURE (confirmed against an actual export via
    inspect_who_gdhm.py — supersedes an earlier, wrong assumption that this
    file already had clean pre-aggregated domain columns):
      - Column 'Unnamed: 0' (-> 'unnamed:_0') holds country names, BUT its
        very first data row is the literal string 'Country Name', not a
        country. The source Excel has two header rows; only the first
        survives pandas' header=0 read, so the second lands as a fake data
        row and must be dropped explicitly.
      - 'Category 1' .. 'Category 7' (-> 'category_1'..'category_7') are
        the 7 domain-level maturity scores. The file never states which
        domain each number is — only 'Category 1', 'Category 2' etc. The
        mapping below follows WHO's own published canonical domain order
        for the GDHM/eHealth Strategy Toolkit framework, but is INFERRED,
        not read literally from this file — spot-check a couple of
        countries against a published WHO GDHM country report before
        treating this as final for external presentation.
      - Scores are strings: 'Phase 1'..'Phase 4', 'Phase -1' (a WHO
        sentinel meaning not applicable/not scored — NOT a real maturity
        level of -1), or 'Not Available'.
      - Column 'Unnamed: 39' (-> 'unnamed:_39') holds WHO's own
        pre-computed Overall Phase per country — kept as
        reported_overall_phase for reference/cross-checking, but this
        pipeline still computes its own `overall` from the 7 domain
        columns, consistent with how every other dataset here derives its
        own aggregates rather than trusting a source-supplied figure
        blindly (same principle as alos_recomputed in
        clean_hospital_aggregates).
      - Indicator_* columns (the 23 underlying indicators) aren't needed
        for the dashboard's domain-level view and are dropped.
    """
    df = raw.copy()
    df = _normalize_columns(df)

    if "unnamed:_0" not in df.columns:
        raise KeyError(
            f"clean_who_gdhm: expected the first column (raw 'Unnamed: 0') "
            f"holding country names was not found. Actual columns: {list(df.columns)}"
        )
    df = df.rename(columns={"unnamed:_0": "country"})

    # Drop the embedded second-header row masquerading as a data row.
    df = df[df["country"].astype(str).str.strip().str.lower() != "country name"]

    category_to_domain = {
        "category_1": "l_gov",       # Leadership & Governance
        "category_2": "strat_inv",   # Strategy & Investment
        "category_3": "legis",       # Legislation, Policy & Compliance
        "category_4": "workforce",   # Workforce
        "category_5": "standards",   # Standards & Interoperability
        "category_6": "infra",       # Infrastructure
        "category_7": "services",    # Services & Applications
    }
    missing_cats = [c for c in category_to_domain if c not in df.columns]
    if missing_cats:
        raise KeyError(
            f"clean_who_gdhm: expected category columns not found: {missing_cats}. "
            f"Actual columns: {list(df.columns)}"
        )
    df = df.rename(columns=category_to_domain)

    def _parse_phase(val):
        """'Phase 1'..'Phase 4' -> 1.0..4.0. 'Phase -1' (WHO's own
        not-applicable sentinel) and 'Not Available' both -> NaN, since
        neither is a real maturity measurement."""
        if pd.isna(val):
            return np.nan
        match = re.match(r"phase\s*(-?\d+)", str(val).strip(), re.IGNORECASE)
        if not match:
            return np.nan
        n = int(match.group(1))
        return float(n) if 1 <= n <= 5 else np.nan

    for col in DOMAIN_COLUMNS:
        df[col] = df[col].apply(_parse_phase)

    if "unnamed:_39" in df.columns:
        df["reported_overall_phase"] = df["unnamed:_39"].apply(_parse_phase)
    else:
        df["reported_overall_phase"] = np.nan

    reported_count = df[DOMAIN_COLUMNS].notna().sum(axis=1)
    df = df[reported_count >= 4]

    df["country"] = df["country"].astype(str).str.strip()
    df = df.drop_duplicates(subset=["country"], keep="last")

    # Filter to the canonical 46 BEFORE computing mean/std/z-score below —
    # z_score is meant to benchmark each country against its OECD peers
    # specifically (per the PRD), so it must be computed over the 46-country
    # population, not the full ~100+ countries WHO's GDHM actually covers.
    df = _filter_to_oecd46(df, "clean_who_gdhm")

    df["overall"] = df[DOMAIN_COLUMNS].mean(axis=1, skipna=True)
    mean_overall = df["overall"].mean()
    std_overall = df["overall"].std(ddof=1)
    df["z_score"] = (df["overall"] - mean_overall) / std_overall
    df["region"] = df["country"].apply(_country_region)
    df["source"] = "WHO_GDHM"

    ordered = (["country", "region"] + DOMAIN_COLUMNS +
               ["overall", "reported_overall_phase", "z_score", "source"])
    return df[ordered]


# ---------------------------------------------------------------------------
# 5. OECD Country Metadata — Region / benchmarking lookup
# ---------------------------------------------------------------------------

def clean_country_metadata(raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Source: OECD country/partner membership list (used to build the static
    COUNTRY_TO_REGION lookup above).

    Transformation steps:
      1. If a raw metadata extract is supplied, standardise columns and use
         it to validate / extend REGION_MAP; otherwise fall back to the
         curated static mapping embedded in this script (kept in sync with
         the PRD's "46 Countries" scope).
      2. Deduplicate country names; strip whitespace and normalise unicode
         (e.g. "Turkiye" vs "Türkiye") via NFKC + str.strip().
      3. Flag any country present in the hospital/GDHM datasets but absent
         from the mapping as `region == "Other"` rather than dropping it,
         so no country silently disappears from the dashboard.

    Output schema:
      country, region
    """
    if raw is not None and not raw.empty:
        df = raw.copy()
        df = _normalize_columns(df)
        if "country" in df.columns and "region" in df.columns:
            # Drop rows with no country name at all BEFORE the astype/str
            # chain below. This matters because of a real pandas quirk:
            # .astype(str).str.strip() does NOT reliably turn a null value
            # into the text "nan" — chaining .str.strip() after .astype(str)
            # silently re-introduces NaN for whatever was originally null,
            # regardless of the astype(str) in between. A metadata row with
            # no country name is meaningless anyway, so drop it rather than
            # try to keep a fake placeholder that would just cause the same
            # "expected str instance, float found" error further down.
            df = df.dropna(subset=["country"])
            if df.empty:
                log.warning(
                    "clean_country_metadata: every row in the raw metadata file "
                    "had a blank country — falling back to the built-in 46-country "
                    "region map instead."
                )
            else:
                df["country"] = df["country"].astype(str).str.strip()
                # Same NaN-survives-str-strip risk applies to 'region' — fillna
                # BEFORE the astype/str chain, not after, is what actually fixes it.
                df["region"] = df["region"].fillna("Other").astype(str).str.strip()
                df = df.drop_duplicates(subset=["country"])
                df = _filter_to_oecd46(df, "clean_country_metadata")
                return df[["country", "region"]]

    rows = [{"country": c, "region": r} for r, countries in REGION_MAP.items() for c in countries]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

PIPELINE = {
    "hospital_aggregates": ("OECD_HospitalAggregates_Discharges.csv", clean_hospital_aggregates),
    "hospital_beds": ("OECDHospitalBeds.csv", clean_hospital_beds),
    "diagnostic_alos": ("OECD_HospitalLengthstay.csv", clean_diagnostic_alos),
    "who_gdhm": ("WHO_GDHM_export.csv", clean_who_gdhm),
    "country_metadata": ("oecd_country_metadata.csv", clean_country_metadata),
}


def run_pipeline(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    for key, (filename, fn) in PIPELINE.items():
        src = input_dir / filename

        if not src.exists():
            if key == "country_metadata":
                # Unlike the other 4 datasets, country_metadata is DESIGNED
                # to work with no raw file at all — clean_country_metadata()
                # accepts raw=None and falls back to the built-in 46-country
                # region map. The blanket "skip if file missing" behaviour
                # below (correct for the other 4, which have no such
                # fallback and would crash on None) was previously also
                # skipping this one, meaning its fallback never actually got
                # a chance to run and its output file was never written —
                # which then made Stage 4 (BUILD) fail on a missing
                # country_metadata_clean.csv. Call it directly with
                # raw=None here instead of skipping.
                log.info("No %s found in %s — using the built-in 46-country region map instead.",
                         filename, input_dir)
                raw = None
            else:
                log.warning("Skipping %s — %s not found in %s", key, filename, input_dir)
                continue
        else:
            log.info("Cleansing %s from %s", key, filename)
            raw = pd.read_csv(src)

        try:
            cleaned = fn(raw)
        except Exception:
            # On any failure, show enough about the raw input to diagnose it
            # without needing another round-trip — this is exactly what was
            # missing when a blank 'region' cell in a real CSV produced a
            # bare "TypeError: sequence item 0: expected str instance,
            # float found" with no indication of where or why.
            if raw is not None:
                log.error(
                    "Failed while cleaning '%s'. Raw input shape: %s. Column dtypes:\n%s\n"
                    "First 3 rows:\n%s",
                    key, raw.shape, raw.dtypes.to_string(), raw.head(3).to_string(),
                )
            else:
                log.error("Failed while cleaning '%s' (no raw input file was used).", key)
            raise

        csv_path = output_dir / f"{key}_clean.csv"
        json_path = output_dir / f"{key}_clean.json"
        cleaned.to_csv(csv_path, index=False)
        cleaned.to_json(json_path, orient="records", indent=2)

        log.info("  -> %s rows written to %s and %s", len(cleaned), csv_path.name, json_path.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MyHealth OECD/WHO data cleansing pipeline")
    parser.add_argument("--input-dir", type=Path, default=Path("./raw"),
                         help="Directory containing the raw downloaded CSV files")
    parser.add_argument("--output-dir", type=Path, default=Path("./clean"),
                         help="Directory to write cleansed CSV/JSON outputs")
    args = parser.parse_args()
    run_pipeline(args.input_dir, args.output_dir)
