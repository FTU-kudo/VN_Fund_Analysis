🇬🇧 **English** | 🇻🇳 [Tiếng Việt](README.md)

# VN_Fund_Analysis

A research pipeline for Vietnamese open-end fund disclosures and Yuanta YSradar stock snapshots. It is not an investment-advice system, a validated backtest, or a source of confirmed transactions. Availability and coverage depend on the files actually collected; missing data is never assumed to be zero.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/data_inventory.py --lang en --stdout-only
python scripts/export_dashboard.py
python -m http.server 8000 --directory dashboard
```

Open the local server URL on your own machine. The dashboard is static HTML, CSS and ES modules; it loads same-origin exported files and works under a GitHub Pages project path. `dashboard/data/` is generated and ignored by Git. Turn on **Settings → Pages → GitHub Actions** to use `.github/workflows/pages.yml`. Review the archive and publication rights before deploying; Pages is public. No raw payloads, credentials or full CSV archive are intentionally exported.

Choose **VI** or **EN**, or use `?lang=vi` / `?lang=en`. Language preference is saved locally when browser storage is available. All main dashboard views and Yuanta research tabs support English and Vietnamese. Source-provided company descriptions, news and unfamiliar accounting fields remain in their original language when no verified translation is available.

## Fmarket fund pipeline

The daily collector tracks Fmarket equity, balanced and bond/MMF open-end funds. It collects the available Top-N equity/bond holdings, allocation, industry, fees, profile and NAV, then maintains semicolon-separated CSV snapshots. Actual fund counts and coverage must be read from the current inventory, not assumed from historical documentation. Source terms, access permissions and rate limits should be independently checked before live collection.

```bash
# Live network access; dry-run does not write files or send notifications.
python scripts/run_daily.py --dry-run --limit 3 --lang en
# Select vi, en or both; default is config.toml [report].lang = "vi".
python scripts/run_daily.py --lang both
# Offline analyses of existing archive
python scripts/data_inventory.py --lang vi --stdout-only
python scripts/analyze_reaction.py --lang en --peak 2026-05-07
```

Daily reports are `reports/latest.md` and, when there are events or baselines, `reports/YYYY-MM-DD.md`. With `--lang both`, the Vietnamese report uses these paths and the English version uses `.en.md`. Telegram notifications, when configured, follow the selected language. Never add credentials to source control.

### Interpreting changes

* `BUY` / `SELL` are **inferred position increases/decreases**, based on volume adjusted by a median fund-scale proxy. They are not trade executions or order records.
* `UNCONFIRMED?` and `WEIGHT_CHANGE?` are unconfirmed; weights alone do not establish trades. `PRORATA` and `CORP_ACTION?` are heuristics, not verified transactions or corporate actions.
* Top-N disclosure is censored. `NEW_IN_TOP10` and `EXIT_TOP10` do not prove a new purchase or complete sale. A claimed full portfolio is not independently verified merely because it was imported.
* Estimated values use the reporting-period implied price, not an execution price. Missing values remain missing.

## Yuanta YSradar stock research

The scanner covers HOSE/HSX, HNX and UPCOM. Its five research pages cover overview and third-party Stock Rating, trading and foreign flow, peer correlation, quarterly financial ratios/statements and corporate articles/events. Yuanta scores are attributed third-party snapshot data, not recommendations by this project.

```bash
python scripts/scan_yuanta_universe.py --tickers ACB,SSI,VHM --workers 3
python scripts/scan_yuanta_universe.py --exchanges HOSE,HNX,UPCOM --workers 6
python scripts/scan_yuanta_universe.py --incremental --workers 6
# Offline rebuild from existing checkpoints
python scripts/scan_yuanta_universe.py --rebuild-static-only
```

The scan defaults to the full available endpoints and history starting in 2010, subject to source coverage and endpoint-specific windows. Check `--help` for module selection. `data/yuanta/` holds ignored checkpoints and Parquet tables; `dashboard/data/yuanta/` holds ignored static `universe.json`, sector summaries and one compressed bundle per ticker. The browser never calls Yuanta live; missing bundles are shown as unsynced. `.github/workflows/yuanta_scan.yml` supports scheduled/manual collection without committing the raw lake.

The peer engine uses `BANK`, `NON_BANK_FINANCIAL`, and `NON_FINANCIAL`. Real Estate (ICB 8600) is explicitly split out of ICB Level 1 Financials into the non-financial Real Estate subgroup, rather than pooled with banks and brokerages. Financial field parsers use the Vietnamese source vocabulary; the presentation glossary does not alter ingestion.

## Time, quality and comparison

`observed_date`, `effective_date`/`report_date`, `observed_at`, `published_at`, and `ingested_at` are distinct. Unknown publication timestamps are never inferred. Approximate mirror dates and date-only observations are not automatically point-in-time safe. `vnfa.temporal.select_as_of` applies availability/cutoff rules to observations and revisions; inspect its policy before conducting research.

Dashboard NAV comparison uses only exact common **source** dates and funds in the same asset category. It reports simple NAV changes, not total return, benchmark-adjusted alpha or an investment recommendation. It does not interpolate or stitch observation snapshots into source history. SLA freshness is configured in `config.toml`; datasets without a threshold remain unassessed. A successful source request does not establish success of the enclosing workflow. A status written inside a running job only covers the collector and budget steps; a completed GitHub Actions event or independently observed API result can record a historical final workflow outcome, with its observation and GitHub update times. It does not establish current data freshness.

The inventory reports files, coverage and quality findings actually present, not current live-provider availability. `scripts/diagnose_sources.py` is a live, read-only diagnostic, not proof of provider terms or completeness. Troubleshooting: check source diagnostics and workflow logs for connectivity, schema or rate-limit failures; a missing/empty dataset is not proof a fund or source has no records. Rebuild static artifacts with `scripts/export_dashboard.py` when the dashboard reports a missing manifest.

## Tests and data hygiene

```bash
pytest --cov=vnfa -q       # install pytest-cov if needed
node --test tests/test_dashboard_ui.mjs
python scripts/check_data_budget.py --data-dir data
```

Routine tests run offline. CSV schemas remain compatible with legacy semicolon-separated files. Generated exports, Yuanta lake files, secrets and large artifacts are excluded from Git. Check provider licensing and reuse terms independently: this repository does not grant rights to third-party data. See [Vietnamese README](README.md) and `docs/` for full operational notes, provenance, audit findings, staging and migration instructions.

### Checking historical NAV refresh

With `[collect].nav_refresh_existing = true` (the default), the daily collector requests history from the last stored effective date, inclusive, through the run date. Responses are validated and rows outside that window are excluded; empty or failed responses do not erase the previous history. Observation-date snapshots are not merged into effective-date history. Set `nav_refresh_existing = false` to disable refresh for existing funds. The provider may return stale or incomplete data or ignore date filters; successful execution does not establish freshness.

The `NAV history` log and `data/last_run.json` (non-dry runs) distinguish valid responses including empty ones (`n_nav_funds`), empty responses, no rows in range, failures, accepted and out-of-range rows, maximum accepted effective date, and new/changed rows. The maximum does not imply every fund is current. A live smoke test can use the daily workflow with `limit=2`, `dry_run=true` before a full main run; a dry run does not write the archive; `write_enabled=False` and new/changed rows are only projected. Do not share tokens, query strings or raw provider payloads with logs.

**Yuanta:** `scanned_ok` means a ticker task finished, not that all endpoints succeeded. Incremental/module-selected scans may overwrite older checkpoint sections after an empty/failed endpoint or when modules are omitted; they should not yet be treated as a safe refresh of the only archive. Preserve a cache/artifact backup before testing and inspect per-ticker diagnostics; a green workflow does not prove completeness.
