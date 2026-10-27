# Data inventory and coverage report

- Generated: `2026-10-27T15:14:56+07:00` (Asia/Ho_Chi_Minh)
- Reference date: `2026-10-27`
- Data directory exists: **True** (`data`)
- CSV files found: **89**
- Expected datasets: **15**; available **12**; missing **3**; schema/read issues **0**
- CSV rows observed across known datasets: **51980**
- Funds observed across tables: **69**

> Counts below come only from files on disk. Missing is not zero, and this report does not assert live source connectivity.

## Dataset inventory

| Dataset | Status | Freshness | Files | Rows | Funds | Quality | Date coverage / range / age |
|---|---:|---|---:|---:|---:|---|---|
| `fund_metadata` | available | not_assessed_no_sla | 1 | 69 | 69 | valid | last_seen: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d) |
| `fund_holdings` | available | within_sla (≤45d) | 46 | 407 | 46 | partial | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d), update_date: 100% (2026-10-08T00:00:00+00:00 → 2026-10-14T00:00:00+00:00; age 13d) |
| `fund_bond_holdings` | available | within_sla (≤45d) | 33 | 180 | 33 | partial | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d), update_date: 100% (2026-10-08T00:00:00+00:00 → 2026-10-14T00:00:00+00:00; age 13d) |
| `full_holdings` | missing | not_assessed_no_sla | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d), period_end: 0% (— → —; age —d) |
| `changes` | missing | not_assessed_no_sla | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d) |
| `nav_history` | available | not_assessed_no_sla | 1 | 47121 | 59 | valid | date: 100% (2004-05-20T00:00:00+00:00 → 2025-10-31T00:00:00+00:00; age 361d) |
| `nav_snapshots` | available | within_sla (≤3d) | 1 | 69 | 69 | valid | date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d) |
| `allocation` | available | stale (≤45d) | 1 | 158 | 68 | valid | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d), report_date: 100% (2026-06-01T00:00:00+00:00 → 2026-09-01T00:00:00+00:00; age 56d) |
| `industry` | available | not_assessed_no_sla | 1 | 569 | 45 | not_applicable | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d) |
| `fees` | available | within_sla (≤180d) | 1 | 671 | 69 | not_applicable | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d) |
| `profile` | available | not_assessed_no_sla | 1 | 69 | 69 | not_applicable | observed_date: 100% (2026-10-27T00:00:00+00:00 → 2026-10-27T00:00:00+00:00; age 0d) |
| `performance` | missing | not_assessed_no_sla | 0 | 0 | 0 | not_applicable | n/a |
| `source_events` | available | not_assessed_no_sla | 1 | 139 | 0 | not_applicable | n/a |
| `research_observations` | available | not_assessed_no_sla | 1 | 2251 | 69 | not_applicable | observed_at: 100% (2026-10-27T06:33:56.265573+00:00 → 2026-10-27T06:37:47.011768+00:00; age 0d), published_at: 0% (— → —; age —d), ingested_at: 100% (2026-10-27T06:37:47.325178+00:00 → 2026-10-27T06:37:47.325178+00:00; age 0d), effective_date: 36% (2025-10-28T00:00:00+00:00 → 2026-10-14T00:00:00+00:00; age 13d), observed_date: 0% (— → —; age —d) |
| `quality_reports` | available | not_assessed_no_sla | 1 | 277 | 69 | not_applicable | n/a |

## Quality findings

- `fund_holdings` — partial: censored_top_n (407), missing_volume (10), missing_asset_value (10)
- `fund_bond_holdings` — partial: censored_top_n (180)
- `nav_snapshots` — valid: effective_date_unknown (69)

## Source connectivity and provenance

- Persisted source attempts: **139** (recorded).
- Outcome counts: `{'success': 129, 'empty_response': 10}`
- Source diagnostic does not verify provider terms or permission to reuse data.
- Observation time coverage: `{'effective_date': 0.357175, 'observed_at': 1.0, 'published_at': 0.0, 'ingested_at': 1.0, 'observed_date': 0.0}`
- Verification status counts: `{'provider_returned_unverified': 2251}`
- Availability-date basis counts: `{'unknown': 2251}`
- Persisted quality finding severities: `{'warning': 787}`

## Prioritized next steps

- **P2** — Populate only datasets needed for research; preserve missingness and report per-source coverage rather than filling gaps.
- **P2** — Accumulate verified full holdings, fund AUM/flows, official publication times, NAV history, and benchmark/fee data before any ranking or backtest.
- **P3** — Do not interpret inferred holdings changes as trades or assert investment alpha; validate only after a point-in-time dataset is sufficiently covered.

## Qualifications

- Inventory describes files present at generation time; it does not represent current live Fmarket coverage.
- Missing datasets and unknown values are not interpreted as zero or as proof that a source has no records.
- Top-N holdings are censored. Date-only and approximate backfill observations have lower or no PIT eligibility.
- Source terms, published-time semantics, and live schema require independent verification.
