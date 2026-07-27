"""
build_dashboard_data.py
===============================================================================
The missing "Dashboard Dataset Builder" identified in
MyHealth_Missing_Files_and_Scripts_Summary.md.

Reads the /clean output of myhealth_data_cleansing_pipeline.py and produces
dashboard_data.json containing exactly the arrays the dashboard's JS expects
(reverse-engineered from the ALOS_ALL / BEDS_ALL / DIAG_ALL / DIGITAL_HEALTH /
ALL_COUNTRIES consts already in MyHealth_OECD_Dashboard_v7.html), plus
metadata and quality-check JSON outputs also called for in that gap analysis.

This is the piece that was missing: previously the ALOS_ALL-style arrays were
apparently generated once by hand/ad hoc and pasted into the HTML. This
script makes that step repeatable and driven by the real cleansing pipeline
output, not a one-off manual export.

Usage:
    python build_dashboard_data.py --input-dir ./clean --output-dir ./build
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("build_dashboard_data")


def _nan_to_none(v):
    """JSON has no NaN; the dashboard's own DIGITAL_HEALTH array uses null for
    unreported values, so mirror that convention exactly rather than 0."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except TypeError:
        pass
    return v


def build_alos_all(df: pd.DataFrame) -> list:
    """ALOS_ALL: [{country, year, alos}] — one row per country-year, matching
    the dashboard's exact key names. Sourced from hospital_aggregates_clean.csv."""
    out = []
    for _, r in df.iterrows():
        if pd.isna(r.get("alos")):
            continue
        out.append({
            "country": r["country"],
            "year": int(r["year"]),
            "alos": round(float(r["alos"]), 4),
        })
    return out


def build_beds_all(df: pd.DataFrame) -> list:
    """BEDS_ALL: [{country, year, beds_per1k}]. Sourced from hospital_beds_clean.csv."""
    out = []
    for _, r in df.iterrows():
        if pd.isna(r.get("beds_per_1000")):
            continue
        out.append({
            "country": r["country"],
            "year": int(r["year"]),
            "beds_per1k": round(float(r["beds_per_1000"]), 4),
        })
    return out


def build_diag_all(df: pd.DataFrame) -> tuple:
    """DIAG_ALL: [{country, diag, year, alos}], plus the two GLOBAL summary
    arrays (DIAG_LABELS_GLOBAL, DIAG_VALS_GLOBAL) the donut chart uses,
    computed as the global mean ALOS per diagnosis across all countries/years
    in the current data, ranked descending exactly like the existing chart."""
    out = []
    for _, r in df.iterrows():
        if pd.isna(r.get("alos_days")):
            continue
        out.append({
            "country": r["country"],
            "diag": r["diagnosis"],
            "year": int(r["year"]),
            "alos": round(float(r["alos_days"]), 4),
        })
    global_means = (
        df.groupby("diagnosis")["alos_days"].mean().sort_values(ascending=False)
    )
    diag_labels_global = list(global_means.index)
    diag_vals_global = [round(float(v), 1) for v in global_means.values]
    return out, diag_labels_global, diag_vals_global


def build_digital_health(df: pd.DataFrame) -> list:
    """DIGITAL_HEALTH: [{country, l_gov, strat_inv, legis, workforce,
    standards, infra, services, overall}] — nulls preserved for unreported
    domains/countries exactly as the existing dashboard does, not fabricated."""
    domain_cols = ["l_gov", "strat_inv", "legis", "workforce", "standards", "infra", "services"]
    out = []
    for _, r in df.iterrows():
        row = {"country": r["country"]}
        for c in domain_cols:
            val = r.get(c)
            row[c] = _nan_to_none(round(val, 0) if pd.notna(val) else None)
        overall = r.get("overall")
        row["overall"] = _nan_to_none(round(overall, 0) if pd.notna(overall) else None)
        out.append(row)
    return out


def build_analytics_scatter(alos_all: list, beds_all: list) -> list:
    """ANALYTICS_SCATTER: [{country, year, beds_per1k, alos}] — inner join of
    ALOS_ALL and BEDS_ALL on (country, year), used by the Beds-vs-ALOS
    correlation scatter. Previously a separately hardcoded array; now
    derived directly from the same two arrays above so it can never drift
    out of sync with them."""
    beds_lookup = {(r["country"], r["year"]): r["beds_per1k"] for r in beds_all}
    out = []
    for r in alos_all:
        key = (r["country"], r["year"])
        if key in beds_lookup:
            out.append({"country": r["country"], "year": r["year"],
                        "beds_per1k": beds_lookup[key], "alos": r["alos"]})
    return out


def build_analytics_corr_bars(scatter: list, min_points: int = 3) -> list:
    """ANALYTICS_CORR_BARS: [{country, r, n}] — per-country Pearson
    correlation between beds_per1k and alos across all overlapping years.
    Countries with fewer than min_points overlapping observations are
    excluded (a 2-point correlation is not meaningful)."""
    df = pd.DataFrame(scatter)
    out = []
    if df.empty:
        return out
    for country, g in df.groupby("country"):
        if len(g) < min_points:
            continue
        r = g["beds_per1k"].corr(g["alos"])
        if pd.isna(r):
            continue
        out.append({"country": country, "r": round(float(r), 6), "n": int(len(g))})
    out.sort(key=lambda x: abs(x["r"]), reverse=True)
    return out


def build_analytics_forecast(alos_all: list, target_years=(2027, 2030), min_points: int = 3) -> list:
    """ANALYTICS_FORECAST: [{country, latest_year, latest_alos, slope_per_year,
    pred_2027, pred_2030, r_squared, trend, pct_change_to_2030}] — per-country
    OLS regression of ALOS against year. Countries with fewer than
    min_points years of data are excluded rather than given an unreliable
    single/two-point 'forecast'."""
    df = pd.DataFrame(alos_all)
    out = []
    if df.empty:
        return out
    for country, g in df.groupby("country"):
        g = g.sort_values("year")
        if len(g) < min_points:
            continue
        x = g["year"].values.astype(float)
        y = g["alos"].values.astype(float)
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        latest_year = int(g["year"].max())
        latest_alos = float(g[g["year"] == latest_year]["alos"].iloc[0])
        pred = {yr: round(float(slope * yr + intercept), 2) for yr in target_years}
        pred_2030 = pred.get(2030)
        pct_change = round((pred_2030 - latest_alos) / latest_alos * 100, 1) if latest_alos else None
        out.append({
            "country": country,
            "latest_year": latest_year,
            "latest_alos": round(latest_alos, 2),
            "slope_per_year": round(float(slope), 4),
            "pred_2027": pred.get(2027),
            "pred_2030": pred_2030,
            "r_squared": round(float(r_squared), 3),
            "trend": "Declining (improving)" if slope < 0 else "Increasing (worsening)",
            "pct_change_to_2030": pct_change,
        })
    return out


def build_analytics_presc(alos_all: list, beds_all: list) -> list:
    """ANALYTICS_PRESC: [{country, alos, beds_per1k, risk_flag,
    efficiency_gap_days, z_score}] — latest-year snapshot per country.
    Risk thresholds match the ones VERIFIED in the original dashboard's own
    JS during the earlier code-review pass: z>1.5 High Risk, z>0.5 Medium
    Risk, else Low Risk. efficiency_gap_days = country's latest ALOS minus
    the best-in-class (lowest) latest ALOS across all countries."""
    alos_df = pd.DataFrame(alos_all)
    beds_df = pd.DataFrame(beds_all)
    if alos_df.empty:
        return []

    latest_alos = alos_df.sort_values("year").groupby("country").tail(1).set_index("country")["alos"]
    latest_beds = beds_df.sort_values("year").groupby("country").tail(1).set_index("country")["beds_per1k"] if not beds_df.empty else pd.Series(dtype=float)

    mean_alos = latest_alos.mean()
    std_alos = latest_alos.std(ddof=1)
    best_in_class = latest_alos.min()

    out = []
    for country, alos in latest_alos.items():
        z = (alos - mean_alos) / std_alos if std_alos else 0.0
        if z > 1.5:
            risk_flag = "High Risk"
        elif z > 0.5:
            risk_flag = "Medium Risk"
        else:
            risk_flag = "Low Risk"
        out.append({
            "country": country,
            "alos": round(float(alos), 2),
            "beds_per1k": round(float(latest_beds.get(country)), 2) if country in latest_beds.index else None,
            "risk_flag": risk_flag,
            "efficiency_gap_days": round(float(alos - best_in_class), 2),
            "z_score": round(float(z), 2),
        })
    out.sort(key=lambda x: x["z_score"], reverse=True)
    return out


def build_all_countries(*dfs: pd.DataFrame) -> list:
    """Union of every country appearing in any cleaned dataset, sorted —
    matches how ALL_COUNTRIES is used to populate the country filter."""
    countries = set()
    for df in dfs:
        if "country" in df.columns:
            countries.update(df["country"].dropna().unique().tolist())
    return sorted(countries)


def run(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    required = {
        "hospital_aggregates": input_dir / "hospital_aggregates_clean.csv",
        "hospital_beds": input_dir / "hospital_beds_clean.csv",
        "diagnostic_alos": input_dir / "diagnostic_alos_clean.csv",
        "who_gdhm": input_dir / "who_gdhm_clean.csv",
        "country_metadata": input_dir / "country_metadata_clean.csv",
    }
    missing = [str(p) for p in required.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing cleaned input file(s): {', '.join(missing)}. "
            f"Run myhealth_data_cleansing_pipeline.py first."
        )

    agg = pd.read_csv(required["hospital_aggregates"])
    beds = pd.read_csv(required["hospital_beds"])
    diag = pd.read_csv(required["diagnostic_alos"])
    gdhm = pd.read_csv(required["who_gdhm"])
    meta = pd.read_csv(required["country_metadata"])

    log.info("Building ALOS_ALL ...")
    alos_all = build_alos_all(agg)
    log.info("Building BEDS_ALL ...")
    beds_all = build_beds_all(beds)
    log.info("Building DIAG_ALL + global summary ...")
    diag_all, diag_labels_global, diag_vals_global = build_diag_all(diag)
    log.info("Building DIGITAL_HEALTH ...")
    digital_health = build_digital_health(gdhm)
    log.info("Building ALL_COUNTRIES ...")
    all_countries = build_all_countries(agg, beds, diag, gdhm)

    log.info("Building ANALYTICS_SCATTER ...")
    analytics_scatter = build_analytics_scatter(alos_all, beds_all)
    log.info("Building ANALYTICS_CORR_BARS ...")
    analytics_corr_bars = build_analytics_corr_bars(analytics_scatter)
    log.info("Building ANALYTICS_FORECAST ...")
    analytics_forecast = build_analytics_forecast(alos_all)
    log.info("Building ANALYTICS_PRESC ...")
    analytics_presc = build_analytics_presc(alos_all, beds_all)

    year_min = int(min(agg["year"].min(), beds["year"].min(), diag["year"].min()))
    year_max = int(max(agg["year"].max(), beds["year"].max(), diag["year"].max()))

    dashboard_data = {
        "ALOS_ALL": alos_all,
        "BEDS_ALL": beds_all,
        "DIAG_ALL": diag_all,
        "DIAG_LABELS_GLOBAL": diag_labels_global,
        "DIAG_VALS_GLOBAL": diag_vals_global,
        "DIGITAL_HEALTH": digital_health,
        "ALL_COUNTRIES": all_countries,
        "ANALYTICS_SCATTER": analytics_scatter,
        "ANALYTICS_CORR_BARS": analytics_corr_bars,
        "ANALYTICS_FORECAST": analytics_forecast,
        "ANALYTICS_PRESC": analytics_presc,
        "YEAR_MIN": year_min,
        "YEAR_MAX": year_max,
    }
    data_path = output_dir / "dashboard_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, ensure_ascii=False)
    log.info("Wrote %s (%d ALOS rows, %d beds rows, %d diag rows, %d countries)",
              data_path.name, len(alos_all), len(beds_all), len(diag_all), len(all_countries))

    # -----------------------------------------------------------------
    # Metadata outputs (per the gap-analysis doc's requested structure)
    # -----------------------------------------------------------------
    metadata = {
        "generated_from": {
            "hospital_aggregates_rows": len(agg),
            "hospital_beds_rows": len(beds),
            "diagnostic_alos_rows": len(diag),
            "who_gdhm_rows": len(gdhm),
            "country_metadata_rows": len(meta),
        },
        "year_range": [year_min, year_max],
        "country_count": len(all_countries),
        "sources": {
            "hospital_aggregates": "OECD DF_AGGR",
            "hospital_beds": "OECD DF_BEDS_FUNC",
            "diagnostic_alos": "OECD DF_HOSP_AV_LENGTH",
            "who_gdhm": "WHO GDHM",
        },
    }
    with open(output_dir / "dashboard_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # -----------------------------------------------------------------
    # Quality output: outlier + variance flags carried over from cleansing
    # -----------------------------------------------------------------
    quality = {
        "hospital_aggregates_outliers": int(agg.get("is_outlier", pd.Series(dtype=bool)).sum()) if "is_outlier" in agg.columns else None,
        "hospital_aggregates_variance_flags": int(agg.get("alos_variance_flag", pd.Series(dtype=bool)).sum()) if "alos_variance_flag" in agg.columns else None,
        "who_gdhm_countries_with_full_reporting": int(gdhm[[c for c in ["l_gov","strat_inv","legis","workforce","standards","infra","services"] if c in gdhm.columns]].notna().all(axis=1).sum()) if len(gdhm) else 0,
    }
    with open(output_dir / "quality_report.json", "w", encoding="utf-8") as f:
        json.dump(quality, f, indent=2)

    log.info("Wrote dashboard_metadata.json and quality_report.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build dashboard_data.json from cleaned MyHealth datasets")
    parser.add_argument("--input-dir", type=Path, default=Path("./clean"))
    parser.add_argument("--output-dir", type=Path, default=Path("./build"))
    args = parser.parse_args()
    run(args.input_dir, args.output_dir)
