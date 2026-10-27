# Data inventory and coverage report

- Generated: `2026-10-27T11:10:58+07:00` (Asia/Ho_Chi_Minh)
- Reference date: `2026-10-27`
- Data directory exists: **False** (`data`)
- CSV files found: **0**
- Expected datasets: **15**; available **0**; missing **15**; schema/read issues **0**
- CSV rows observed across known datasets: **0**
- Funds observed across tables: **0**

> Counts below come only from files on disk. Missing is not zero, and this report does not assert live source connectivity.

## Dataset inventory

| Dataset | Status | Files | Rows | Funds | Quality | Date coverage / range / age |
|---|---:|---:|---:|---:|---|---|
| `fund_metadata` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `fund_holdings` | missing | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d), update_date: 0% (— → —; age —d) |
| `fund_bond_holdings` | missing | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d), update_date: 0% (— → —; age —d) |
| `full_holdings` | missing | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d), period_end: 0% (— → —; age —d) |
| `changes` | missing | 0 | 0 | 0 | not_applicable | observed_date: 0% (— → —; age —d) |
| `nav_history` | missing | 0 | 0 | 0 | not_applicable | date: 0% (— → —; age —d) |
| `nav_snapshots` | missing | 0 | 0 | 0 | not_applicable | date: 0% (— → —; age —d) |
| `allocation` | missing | 0 | 0 | 0 | not_applicable | report_date: 0% (— → —; age —d) |
| `industry` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `fees` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `profile` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `performance` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `source_events` | missing | 0 | 0 | 0 | not_applicable | n/a |
| `research_observations` | missing | 0 | 0 | 0 | not_applicable | observed_at: 0% (— → —; age —d), published_at: 0% (— → —; age —d), ingested_at: 0% (— → —; age —d), effective_date: 0% (— → —; age —d), observed_date: 0% (— → —; age —d) |
| `quality_reports` | missing | 0 | 0 | 0 | not_applicable | n/a |

## Quality findings

- No quality findings were computed (datasets missing or validators not applicable).

## Source connectivity and provenance

- Persisted source attempts: **0** (not_recorded).
- Outcome counts: `{}`
- Source diagnostic does not verify provider terms or permission to reuse data.

## Prioritized next steps

- **P0** — Create/restore the data archive and run a small approved collection; inventory found no existing CSV observations.
- **P1** — Run the classified source diagnostic from an environment allowed to reach Fmarket; check terms/rate limits separately.
- **P1** — Start collecting exact observed_at/ingested_at and leave unknown published_at blank; exclude timestamp-unknown records from PIT analyses.
- **P2** — Accumulate verified full holdings, fund AUM/flows, official publication times, NAV history, and benchmark/fee data before any ranking or backtest.
- **P3** — Do not interpret inferred holdings changes as trades or assert investment alpha; validate only after a point-in-time dataset is sufficiently covered.

## Qualifications

- Inventory describes files present at generation time; it does not represent current live Fmarket coverage.
- Missing datasets and unknown values are not interpreted as zero or as proof that a source has no records.
- Top-N holdings are censored. Date-only and approximate backfill observations have lower or no PIT eligibility.
- Source terms, published-time semantics, and live schema require independent verification.
