# MyHealth Dashboard — Data & AI Pipeline

Studio 6 · BITC01701 · Health Tech Innovators

Full architecture: real OECD/WHO data in, real Claude AI Analyst out, no hardcoding anywhere.

```
Raw OECD/WHO Downloads
    -> fetch_myhealth_source_data.py       (download real data)
    -> myhealth_data_cleansing_pipeline.py  (clean + transform)
    -> validate_country_joins.py            (catch naming mismatches)
    -> build_dashboard_data.py              (build dashboard_data.json)
    -> publish_dashboard.py                 (inject data + AI Analyst fixes)

Separately, for a working AI Analyst chat box:
    ai_proxy_server.py                      (local server holding your API key)
```

`run_myhealth_pipeline.py` runs the first 5 steps as one command.

## Quick start

```powershell
pip install requests pandas openpyxl python-dotenv

# 1. Get real data + build the dashboard (one command)
python run_myhealth_pipeline.py --template "MyHealth_OECD_Dashboard_v7 (10).html"

# 2. (Optional but recommended) Enable the live AI Analyst
#    a) Get a key: console.anthropic.com/settings/keys
#    b) Create a .env file next to ai_proxy_server.py containing:
#       ANTHROPIC_API_KEY=sk-ant-...
#    c) In a SEPARATE terminal, leave this running the whole time you use the dashboard:
python ai_proxy_server.py

# 3. Open MyHealth_OECD_Dashboard_published.html in your browser
```

Use `--skip-fetch` on step 1 if you already have files in `./raw` and don't want to re-download.

## What each script does

| Script | Role |
|---|---|
| `fetch_myhealth_source_data.py` | Downloads the 5 raw files from live OECD SDMX + WHO GDHM endpoints |
| `myhealth_data_cleansing_pipeline.py` | Cleans, filters to the canonical 46 OECD countries, computes ALOS/beds/diagnostic aggregates |
| `validate_country_joins.py` | Checks country-name consistency across all 5 cleaned datasets |
| `build_dashboard_data.py` | Builds `dashboard_data.json` — all 13 data structures the dashboard needs, including derived analytics (forecasts, correlations, risk scores) |
| `publish_dashboard.py` | Injects that data into the HTML template, **and** applies 2 standing AI Analyst fixes automatically (see below) |
| `run_myhealth_pipeline.py` | Runs the 5 scripts above as one command with clear per-stage error handling |
| `ai_proxy_server.py` | A small local server that holds your real Anthropic API key so the AI Analyst can make genuine Claude calls — kept separate because it's a long-running server, not a one-time publish step |

## AI Analyst — how it actually works

The dashboard's AI Analyst cannot safely call Anthropic's API directly from a static HTML file (there's nowhere safe to put a real API key in a file anyone can view-source). `publish_dashboard.py` automatically:

1. Repoints the AI Analyst's fetch call to `http://localhost:8787/api/analyze` instead of `api.anthropic.com` directly (override with `--ai-proxy-url`, or skip with `--no-ai-proxy`)
2. Adds country-specific filter detection — the AI can now recognise a named country (e.g. "Türkiye has the lowest ALOS") in its own answer, not just a year range or region
3. **Auto-applies** that filter suggestion immediately — filters, KPI cards, charts, and insight text all update the moment the AI answers, no button click required

For the AI Analyst to give real (not fallback) answers, `ai_proxy_server.py` must be running in a separate terminal the whole time, with a real `ANTHROPIC_API_KEY` loaded (via `.env` file or environment variable — see the script's own docstring for exact setup). Without it running, questions still get answered, just via a deterministic local fallback instead of live Claude — the dashboard tells you which one you're getting.

**This only works on your own machine.** If you hand the published HTML to someone else, their browser will look for the proxy on *their* localhost, find nothing, and fall back automatically — that's expected, not a bug.

## Known real bugs found and fixed along the way

Worth knowing about since they explain some of the design choices in the scripts:

- **Duplicate columns**: OECD's real `csvfilewithlabels` export pairs every coded column with a label column of the same normalized name (e.g. `TIME_PERIOD` + `Time period` both become `time_period`), which silently broke `pd.to_numeric()`. Fixed with column deduplication + a preference for the human-readable label over the raw code where it matters (e.g. country names).
- **Measure codes, not labels**: OECD's `MEASURE` column holds codes (`STAY`, `BED_DAY`, `DISCHARGE`), not descriptive text — this produced entirely empty output for hospital aggregates and beds until the code was corrected to match.
- **Unit mismatches**: `UNIT_MEASURE` sometimes has more distinct codes than there are measures — meaning at least one measure has two incompatible unit variants (e.g. raw count vs. rate). The pipeline now auto-picks the dominant (most-reported) unit per measure and logs the choice, rather than silently averaging incompatible units together.
- **WHO GDHM's real shape**: not a clean per-domain table — it's a 23-indicator export with an embedded fake header row and `Category 1`–`7` columns whose domain meaning isn't stated in the file itself (inferred from WHO's published framework order — worth spot-checking against a WHO country report if precision matters for external presentation).
- **A pre-existing bug in the original dashboard**: the "Apply Filters" button used to be built by calling `.toString()` on a function, which silently threw `fsSnapshot is not defined` and did nothing when clicked — invisible until the AI Analyst actually had a live connection to trigger it for the first time. Now resolved by auto-applying directly instead of using a button at all.

## Known limitations

- **Digital Health Adoption tab has sparse coverage**: only ~11 of the 46 OECD countries have usable WHO GDHM domain data (confirmed against real data). Filtering to a country outside that set will correctly show "No data" on that tab — not a bug, a genuine reporting gap in WHO's own data.
- **No live browser-side fetch to OECD/WHO**: their APIs don't appear to support CORS for arbitrary static sites, so the dashboard stays a fast, dependency-free static file refreshed by re-running the pipeline, rather than fetching live on every page load.
- **46-country scope**: matches the PRD's stated project scope exactly, using the same list already embedded in the original dashboard's own `ALL_COUNTRIES` array. Raw OECD/WHO data covers more countries than this; anything outside the 46 is intentionally filtered out during cleansing, with a log line showing exactly what was dropped.
