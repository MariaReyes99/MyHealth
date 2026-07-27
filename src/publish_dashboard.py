"""
publish_dashboard.py
===============================================================================
The missing "Dashboard Publish Script" identified in
MyHealth_Missing_Files_and_Scripts_Summary.md.

Takes the dashboard HTML template and the dashboard_data.json produced by
build_dashboard_data.py, and rewrites the hardcoded const declarations
(ALOS_ALL, BEDS_ALL, DIAG_ALL, DIAG_LABELS_GLOBAL, DIAG_VALS_GLOBAL,
DIGITAL_HEALTH, ALL_COUNTRIES, YEAR_MIN, YEAR_MAX) with freshly generated
values, in place, leaving everything else in the file (styling, chart logic,
ISO3 lookup, REGIONS grouping) untouched.

IMPORTANT — architecture note:
This does NOT make the dashboard fetch data live in the browser. OECD's SDMX
API does not appear to support CORS for arbitrary static-site requests, so a
genuine browser-side fetch() to sdmx.oecd.org from this HTML would most
likely fail for end users. The realistic, robust pattern for a static
dashboard like this is exactly what this script does: refresh the embedded
data from a real pipeline run whenever you want updated figures, rather than
re-fetching on every page load. Re-run fetch -> clean -> build -> publish to
refresh; the dashboard itself stays a fast, dependency-free static file.

AI ANALYST FIXES (folded in here as of this version — previously 3 separate
one-off patch scripts: enable_ai_analyst.py, enable_country_filter_
suggestions.py, fix_apply_filter_button.py. Those are no longer needed and
can be deleted; every dashboard this script publishes now includes all 3
fixes automatically):

  1. Repoints the AI Analyst's fetch() call from https://api.anthropic.com
     (which a static HTML file can never safely call directly — see
     ai_proxy_server.py for why) to a local proxy server, by default
     http://localhost:8787/api/analyze. Override with --ai-proxy-url, or
     turn it off entirely with --no-ai-proxy (leaves the original
     Anthropic URL in place, e.g. if you want to hand the file to someone
     who'll wire up their own proxy differently).

  2. Adds country-specific filter suggestions: previously the AI Analyst
     could only suggest a year range or a whole region, never a single
     country like "Türkiye" — even though that's the most common kind of
     answer (e.g. "which country has the lowest ALOS?").

  3. Auto-applies filter suggestions immediately instead of showing a
     manual "Apply Filters ->" button. The original dashboard already had
     an unused hook for this (`if(filterSuggestions.apply)
     filterSuggestions.apply();`) — it was just always disabled
     (hardcoded `apply: null`). Wiring it up also sidesteps a genuine
     pre-existing bug the button version had: building an onclick
     attribute by calling .toString() on a function loses the closure
     variables it depended on. Returning a real function directly (never
     serialized to an HTML attribute) doesn't have that problem at all.

Usage:
    python publish_dashboard.py --template MyHealth_OECD_Dashboard_v7.html \\
        --data ./build/dashboard_data.json \\
        --output MyHealth_OECD_Dashboard_published.html

    # To skip the AI Analyst proxy patch (leave the direct Anthropic URL):
    python publish_dashboard.py --template ... --data ... --output ... --no-ai-proxy
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("publish_dashboard")

ANTHROPIC_DIRECT_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_PROXY_URL = "http://localhost:8787/api/analyze"

# DASHBOARD IMPROVEMENT REVIEW (270726) follow-up: the simplified charts and
# new colour palette live entirely in the template file, not in this script
# (see the "AI ANALYST FIXES" note above for the same reasoning applied to
# the AI Analyst patches). That only actually helps every time you run the
# pipeline if the REVISED template is what gets used by default -- so this
# constant makes it the default --template, instead of a required argument
# you have to remember to specify correctly every run. Override with
# --template if you ever need to publish from a different file.
DEFAULT_TEMPLATE = Path("MyHealth_OECD_Dashboard_v7_REVISED.html")

# The AI Analyst's filter-suggestion function EXACTLY as it appears in the
# original, unpatched dashboard template. Matched verbatim so this script
# can detect and replace it reliably regardless of which template version
# you're publishing from.
ORIGINAL_EXTRACT_FILTER_SUGGESTIONS = """function extractFilterSuggestions(text, query){
  const lower = text.toLowerCase() + ' ' + query.toLowerCase();
  const suggestions = [];
  let applyFn = null;
  let filterChanged = false;
  const newFS = { country: FS.country, yf: FS.yf, yt: FS.yt, region: FS.region };

  // Detect year suggestions
  const yearMatch = lower.match(/filter.*?(\\d{4}).*?to.*?(\\d{4})|set.*?from.*?(\\d{4})/);
  if(yearMatch) {
    const y1 = parseInt(yearMatch[1]||yearMatch[3]);
    const y2 = parseInt(yearMatch[2]||FS.yt);
    if(y1>=1960 && y1<=2024){ newFS.yf=y1; filterChanged=true; suggestions.push('From: '+y1); }
    if(y2>=1960 && y2<=2024){ newFS.yt=y2; filterChanged=true; suggestions.push('To: '+y2); }
  }

  // Detect region suggestions
  if(lower.includes('europe') && FS.region!=='EU'){ newFS.region='EU'; filterChanged=true; suggestions.push('Region: Europe'); }
  if((lower.includes('asia-pacific')||lower.includes('apac')) && FS.region!=='APAC'){ newFS.region='APAC'; filterChanged=true; suggestions.push('Region: Asia-Pacific'); }
  if(lower.includes('americas') && FS.region!=='AMER'){ newFS.region='AMER'; filterChanged=true; suggestions.push('Region: Americas'); }

  if(!filterChanged) return { html:'', apply:null };

  const fsSnapshot = {...newFS};
  applyFn = ()=>{
    Object.assign(FS, fsSnapshot);
    document.getElementById('f-country').value = FS.country;
    document.getElementById('f-yf').value = FS.yf;
    document.getElementById('f-yt').value = FS.yt;
    document.getElementById('f-region').value = FS.region;
    renderActiveTab();
  };

  const html = `
    <div class="ai-filter-actions">
      <span style="font-size:11px;color:#6b7f95;font-weight:600;">💡 Suggested filter update:</span>
      ${suggestions.map(s=>`<span class="ai-filter-applied">✓ ${s}</span>`).join('')}
      <button class="ai-apply-btn" onclick="(${applyFn.toString()})()">Apply Filters →</button>
    </div>`;

  return { html, apply: null }; // Don't auto-apply — let user decide
}"""

# The final replacement: adds country detection AND auto-applies the change
# immediately (no button, no click needed) by returning a real closure as
# `apply` instead of hardcoding null — the call site in processAIQuery()
# already has `if(filterSuggestions.apply) filterSuggestions.apply();`
# waiting for exactly this, unused until now.
FIXED_EXTRACT_FILTER_SUGGESTIONS = """function extractFilterSuggestions(text, query){
  const lower = text.toLowerCase() + ' ' + query.toLowerCase();
  const suggestions = [];
  let filterChanged = false;
  let countrySuggested = false;
  const newFS = { country: FS.country, yf: FS.yf, yt: FS.yt, region: FS.region };

  // Detect year suggestions
  const yearMatch = lower.match(/filter.*?(\\d{4}).*?to.*?(\\d{4})|set.*?from.*?(\\d{4})/);
  if(yearMatch) {
    const y1 = parseInt(yearMatch[1]||yearMatch[3]);
    const y2 = parseInt(yearMatch[2]||FS.yt);
    if(y1>=1960 && y1<=2024){ newFS.yf=y1; filterChanged=true; suggestions.push('From: '+y1); }
    if(y2>=1960 && y2<=2024){ newFS.yt=y2; filterChanged=true; suggestions.push('To: '+y2); }
  }

  // Detect a specific country named in the AI's answer (e.g. "the lowest
  // ALOS is Türkiye"). Picks whichever of the known countries appears
  // EARLIEST in the raw response text, since the AI typically states the
  // headline answer first, before a supporting table that names several
  // more countries — the earliest mention reliably IS the one actually
  // being answered, not an also-mentioned comparison country further down.
  let earliestIdx = -1, matchedCountry = null;
  for(const c of ALL_COUNTRIES){
    const idx = text.indexOf(c);
    if(idx !== -1 && (earliestIdx === -1 || idx < earliestIdx)){
      earliestIdx = idx;
      matchedCountry = c;
    }
  }
  if(matchedCountry && matchedCountry !== FS.country){
    newFS.country = matchedCountry;
    newFS.region = 'ALL'; // ensure the country is a valid dropdown option regardless of the current region filter
    filterChanged = true;
    countrySuggested = true;
    suggestions.push('Country: ' + matchedCountry);
  }

  // Detect region suggestions (skipped if a specific country was already
  // matched above, since applying both would be contradictory).
  if(!countrySuggested){
    if(lower.includes('europe') && FS.region!=='EU'){ newFS.region='EU'; filterChanged=true; suggestions.push('Region: Europe'); }
    if((lower.includes('asia-pacific')||lower.includes('apac')) && FS.region!=='APAC'){ newFS.region='APAC'; filterChanged=true; suggestions.push('Region: Asia-Pacific'); }
    if(lower.includes('americas') && FS.region!=='AMER'){ newFS.region='AMER'; filterChanged=true; suggestions.push('Region: Americas'); }
  }

  if(!filterChanged) return { html:'', apply:null };

  const fsSnapshot = {...newFS};

  // Returned directly as a real closure — never serialized to an HTML
  // attribute — so it keeps working access to fsSnapshot naturally. Called
  // immediately by processAIQuery() right after this suggestion renders,
  // no button or click required.
  const applyFn = () => {
    Object.assign(FS, fsSnapshot);
    document.getElementById('f-region').value = FS.region;
    rebuildCountryOptions(FS.region, FS.country);
    document.getElementById('f-yf').value = FS.yf;
    document.getElementById('f-yt').value = FS.yt;
    renderActiveTab();
  };

  const html = `
    <div class="ai-filter-actions">
      <span style="font-size:11px;color:#2e7d5b;font-weight:600;">✅ Filters, KPIs, and charts updated automatically:</span>
      ${suggestions.map(s=>`<span class="ai-filter-applied">✓ ${s}</span>`).join('')}
    </div>`;

  return { html, apply: applyFn };
}"""

# Each entry: (const name, JSON key, whether it's a bare value like a number)
REPLACEMENTS = [
    ("ALOS_ALL", "ALOS_ALL", False),
    ("BEDS_ALL", "BEDS_ALL", False),
    ("DIAG_ALL", "DIAG_ALL", False),
    ("DIAG_LABELS_GLOBAL", "DIAG_LABELS_GLOBAL", False),
    ("DIAG_VALS_GLOBAL", "DIAG_VALS_GLOBAL", False),
    ("DIGITAL_HEALTH", "DIGITAL_HEALTH", False),
    ("ALL_COUNTRIES", "ALL_COUNTRIES", False),
    ("ANALYTICS_SCATTER", "ANALYTICS_SCATTER", False),
    ("ANALYTICS_CORR_BARS", "ANALYTICS_CORR_BARS", False),
    ("ANALYTICS_FORECAST", "ANALYTICS_FORECAST", False),
    ("ANALYTICS_PRESC", "ANALYTICS_PRESC", False),
    ("YEAR_MIN", "YEAR_MIN", True),
    ("YEAR_MAX", "YEAR_MAX", True),
]


def apply_ai_analyst_fixes(html: str, proxy_url: str | None) -> str:
    """Applies the AI Analyst fixes described at the top of this script.
    Idempotent: if the fixes are already present (e.g. publishing twice
    from an already-patched template by mistake), this is a no-op rather
    than an error."""
    if proxy_url:
        if ANTHROPIC_DIRECT_URL in html:
            html = html.replace(ANTHROPIC_DIRECT_URL, proxy_url)
            log.info("AI Analyst: repointed fetch() to local proxy (%s)", proxy_url)
        elif proxy_url not in html:
            log.warning("AI Analyst: could not find the expected Anthropic URL to patch — "
                        "template may already be modified. Skipping this fix.")

    if FIXED_EXTRACT_FILTER_SUGGESTIONS in html:
        log.info("AI Analyst: country-detection + auto-apply fix already present, skipping.")
    elif ORIGINAL_EXTRACT_FILTER_SUGGESTIONS in html:
        html = html.replace(ORIGINAL_EXTRACT_FILTER_SUGGESTIONS, FIXED_EXTRACT_FILTER_SUGGESTIONS)
        log.info("AI Analyst: added country-specific filter suggestions, auto-applied "
                 "immediately to filters/KPIs/charts (no button click needed).")
    else:
        log.warning("AI Analyst: could not find the expected extractFilterSuggestions() "
                    "function to patch — template may have a different version than "
                    "expected. Skipping this fix; the dashboard will still work, just "
                    "without the country-suggestion upgrade.")
    return html


def replace_const(html: str, const_name: str, value_json: str) -> str:
    """Replace `const NAME=<anything up to the terminating semicolon at the
    start of the next 'const ' or newline-const boundary>;` with fresh JSON.
    Matches the exact single-line style this dashboard is written in."""
    pattern = re.compile(
        r"const " + re.escape(const_name) + r"\s*=\s*.*?;(?=\s*\n|\s*const )",
        re.DOTALL,
    )
    if not pattern.search(html):
        # Fallback: some declarations aren't immediately followed by a
        # newline or another const on the same logical boundary (e.g. last
        # const before other statements) — match up to the next semicolon
        # that is followed by whitespace, which is safe because every
        # value here is a JSON array/number with no embedded raw semicolons.
        pattern = re.compile(
            r"const " + re.escape(const_name) + r"\s*=\s*.*?;",
            re.DOTALL,
        )
    new_decl = f"const {const_name}={value_json};"
    new_html, n = pattern.subn(new_decl, html, count=1)
    if n == 0:
        raise ValueError(f"Could not find 'const {const_name}=' in template — publish aborted, no partial write.")
    return new_html


def run(template_path: Path, data_path: Path, output_path: Path, ai_proxy_url: str | None = DEFAULT_PROXY_URL) -> None:
    html = template_path.read_text(encoding="utf-8")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    missing_keys = [key for _, key, _ in REPLACEMENTS if key not in data]
    if missing_keys:
        raise KeyError(f"dashboard_data.json is missing required key(s): {missing_keys}")

    for const_name, key, is_bare in REPLACEMENTS:
        value = data[key]
        value_json = json.dumps(value, ensure_ascii=False) if not is_bare else json.dumps(value)
        log.info("Replacing const %s (%s)", const_name,
                  f"{len(value)} items" if isinstance(value, list) else value)
        html = replace_const(html, const_name, value_json)

    html = apply_ai_analyst_fixes(html, ai_proxy_url)

    # Stamp a generation marker so it's visible in "view source" that this
    # is a generated, not hand-edited, file — and when it was generated.
    import datetime
    stamp = f"<!-- Published by publish_dashboard.py on {datetime.datetime.now().isoformat(timespec='seconds')} from dashboard_data.json -->\n"
    html = stamp + html

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", output_path, output_path.stat().st_size / 1024)

    # DASHBOARD IMPROVEMENT REVIEW (270726) follow-up: output_path is a
    # single stable filename that gets overwritten every run -- convenient
    # for "always open the same file in my browser", but it means a
    # previous version is gone the moment you re-run the pipeline, with no
    # trace it ever existed. This also writes a timestamped copy into an
    # archive/ folder next to output_path, purely additive -- the stable
    # file's name and location are completely unchanged, so nothing that
    # already points at it (a browser tab, a shortcut, run_myhealth_
    # pipeline.py's own default) needs to change either.
    archive_dir = output_path.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = archive_dir / f"{output_path.stem}_{archive_stamp}{output_path.suffix}"
    archive_path.write_text(html, encoding="utf-8")
    log.info("Archived a timestamped copy to %s", archive_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish freshly built data into the MyHealth dashboard HTML")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                         help=f"Dashboard HTML template to publish from (default: {DEFAULT_TEMPLATE} -- "
                              f"the simplified-charts, new-palette version from the 270726 improvement "
                              f"review; override only if you deliberately want to publish from a "
                              f"different template)")
    parser.add_argument("--data", type=Path, default=Path("./build/dashboard_data.json"))
    parser.add_argument("--output", type=Path, default=Path("./MyHealth_OECD_Dashboard_published.html"))
    parser.add_argument("--ai-proxy-url", type=str, default=DEFAULT_PROXY_URL,
                         help=f"Local AI proxy URL to repoint the AI Analyst at (default: {DEFAULT_PROXY_URL}). "
                              f"Run ai_proxy_server.py separately for this to actually work.")
    parser.add_argument("--no-ai-proxy", action="store_true",
                         help="Skip repointing the AI Analyst fetch URL (leaves the original, "
                              "non-functional direct-to-Anthropic call in place). The country-"
                              "suggestion and Apply-Filters-button fixes are still applied either way.")
    args = parser.parse_args()
    if not args.template.exists():
        raise FileNotFoundError(
            f"Template not found: {args.template}. If you haven't downloaded the "
            f"270726-improvement-review template yet, either save it as "
            f"'{DEFAULT_TEMPLATE}' in this folder, or pass --template pointing at "
            f"whichever dashboard HTML file you want to publish from."
        )
    run(args.template, args.data, args.output, ai_proxy_url=None if args.no_ai_proxy else args.ai_proxy_url)
