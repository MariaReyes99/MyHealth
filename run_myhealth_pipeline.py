"""
run_myhealth_pipeline.py
===============================================================================
The single entry point that connects all 5 MyHealth pipeline scripts into one
system. Put this file in the same folder as the other 5 scripts and run it —
that's the whole "connect them together" step.

    Raw OECD/WHO Downloads
        -> fetch_myhealth_source_data.py       (Stage 1: fetch)
        -> myhealth_data_cleansing_pipeline.py (Stage 2: clean)
        -> validate_country_joins.py            (Stage 3: validate)
        -> build_dashboard_data.py               (Stage 4: build)
        -> publish_dashboard.py                  (Stage 5: publish)

Each stage's real function is imported and called directly (not run as a
separate process) — this is what makes it "one system" rather than five
things you run by hand: one Python process, one log, one exit code, and it
stops immediately with a clear message if a stage fails rather than quietly
running the next stage on bad or missing data.

USAGE
-----
    pip install requests pandas openpyxl

    # Full run, from scratch, downloading fresh data:
    python run_myhealth_pipeline.py --template MyHealth_OECD_Dashboard_v7.html

    # Skip the download step (you already have ./raw populated, e.g. from a
    # previous run, or you're iterating on the cleansing logic and don't
    # want to hit the OECD/WHO servers every time):
    python run_myhealth_pipeline.py --template MyHealth_OECD_Dashboard_v7.html --skip-fetch

    # Custom folders:
    python run_myhealth_pipeline.py --template dashboard.html \
        --raw-dir ./raw --clean-dir ./clean --build-dir ./build \
        --output MyHealth_OECD_Dashboard_published.html

EXIT CODES
----------
    0 = success, dashboard published
    1 = a stage failed (see the printed [STAGE X FAILED] message for which
        one and why — the traceback is still shown, this just adds a clear
        marker so it's obvious which of the 5 stages broke)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("run_myhealth_pipeline")

STAGE_NAMES = {
    1: "FETCH (fetch_myhealth_source_data.py)",
    2: "CLEAN (myhealth_data_cleansing_pipeline.py)",
    3: "VALIDATE (validate_country_joins.py)",
    4: "BUILD (build_dashboard_data.py)",
    5: "PUBLISH (publish_dashboard.py)",
}


def _fail(stage: int, exc: Exception) -> None:
    name = STAGE_NAMES.get(stage, f"STAGE {stage}")
    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"[STAGE {stage} FAILED] {name}", file=sys.stderr)
    print(f"{'=' * 70}", file=sys.stderr)
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    print(
        "\nThe pipeline stopped here on purpose — running later stages "
        "against incomplete or bad data from this one would just produce a "
        "confusing failure further down instead of a clear one right here.",
        file=sys.stderr,
    )
    sys.exit(1)


def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    raw_dir = args.raw_dir
    clean_dir = args.clean_dir
    build_dir = args.build_dir

    # -----------------------------------------------------------------
    # Stage 1: Fetch
    # -----------------------------------------------------------------
    if args.skip_fetch:
        log.info("[1/5] FETCH — skipped (--skip-fetch). Using existing files in %s", raw_dir)
        if not raw_dir.exists() or not any(raw_dir.glob("*.csv")):
            _fail(1, FileNotFoundError(
                f"--skip-fetch was set but {raw_dir} has no CSV files. "
                f"Either remove --skip-fetch or point --raw-dir at a folder "
                f"that already has the 5 raw source files."
            ))
    else:
        log.info("[1/5] FETCH — downloading OECD/WHO source data into %s", raw_dir)
        try:
            import fetch_myhealth_source_data
            fetch_myhealth_source_data.fetch_all(raw_dir)
        except Exception as exc:
            _fail(1, exc)

    # -----------------------------------------------------------------
    # Stage 2: Clean
    # -----------------------------------------------------------------
    log.info("[2/5] CLEAN — running cleansing pipeline: %s -> %s", raw_dir, clean_dir)
    try:
        import myhealth_data_cleansing_pipeline
        myhealth_data_cleansing_pipeline.run_pipeline(raw_dir, clean_dir)
    except Exception as exc:
        _fail(2, exc)

    # -----------------------------------------------------------------
    # Stage 3: Validate
    # -----------------------------------------------------------------
    log.info("[3/5] VALIDATE — checking country name consistency across datasets")
    try:
        import validate_country_joins
        report = validate_country_joins.run(clean_dir)
        report_path = clean_dir / "country_join_validation.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        if report.get("unmatched_by_dataset") and not args.ignore_validation_warnings:
            log.warning(
                "Country coverage gaps found between datasets (see %s). "
                "Continuing anyway — pass --ignore-validation-warnings to "
                "silence this, or Ctrl+C now to stop and investigate first.",
                report_path,
            )
    except Exception as exc:
        _fail(3, exc)

    # -----------------------------------------------------------------
    # Stage 4: Build
    # -----------------------------------------------------------------
    log.info("[4/5] BUILD — generating dashboard_data.json: %s -> %s", clean_dir, build_dir)
    try:
        import build_dashboard_data
        build_dashboard_data.run(clean_dir, build_dir)
    except Exception as exc:
        _fail(4, exc)

    # -----------------------------------------------------------------
    # Stage 5: Publish
    # -----------------------------------------------------------------
    log.info("[5/5] PUBLISH — injecting fresh data into %s -> %s", args.template, args.output)
    try:
        import publish_dashboard
        publish_dashboard.run(args.template, build_dir / "dashboard_data.json", args.output)
    except Exception as exc:
        _fail(5, exc)

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"PIPELINE COMPLETE in {elapsed:.1f}s")
    print(f"{'=' * 70}")
    print(f"  Raw data:        {raw_dir}")
    print(f"  Cleaned data:    {clean_dir}")
    print(f"  Dashboard data:  {build_dir / 'dashboard_data.json'}")
    print(f"  Published site:  {args.output}")
    print(f"\nOpen {args.output} in a browser to see it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the full MyHealth data pipeline: fetch -> clean -> validate -> build -> publish",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # DASHBOARD IMPROVEMENT REVIEW (270726) follow-up: defaults to the
    # simplified-charts, new-palette template so a normal run of this
    # pipeline always regenerates the improved dashboard, without anyone
    # needing to remember to pass the right --template every time (the
    # previous required=True argument made it easy to accidentally
    # publish from the old, cluttered template instead).
    parser.add_argument("--template", type=Path, default=Path("MyHealth_OECD_Dashboard_v7_REVISED.html"),
                         help="Path to the dashboard HTML template (default: the 270726 improvement "
                              "review version; override only to publish from a different template)")
    parser.add_argument("--output", type=Path, default=Path("./MyHealth_OECD_Dashboard_published.html"),
                         help="Path to write the published dashboard HTML")
    parser.add_argument("--raw-dir", type=Path, default=Path("./raw"), dest="raw_dir")
    parser.add_argument("--clean-dir", type=Path, default=Path("./clean"), dest="clean_dir")
    parser.add_argument("--build-dir", type=Path, default=Path("./build"), dest="build_dir")
    parser.add_argument("--skip-fetch", action="store_true",
                         help="Skip Stage 1 and use whatever's already in --raw-dir")
    parser.add_argument("--ignore-validation-warnings", action="store_true",
                         help="Don't pause to warn about country-coverage gaps found in Stage 3")
    args = parser.parse_args()

    if not args.template.exists():
        print(f"ERROR: Template not found: {args.template}", file=sys.stderr)
        print(
            "If you haven't downloaded the 270726-improvement-review template yet, "
            "either save it as that exact filename in this folder, or pass "
            "--template pointing at whichever dashboard HTML file you want to use.",
            file=sys.stderr,
        )
        sys.exit(1)

    for d in (args.raw_dir, args.clean_dir, args.build_dir):
        d.mkdir(parents=True, exist_ok=True)

    run(args)
