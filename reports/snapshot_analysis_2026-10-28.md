# Analysis of the available fund archive — 28 October 2026

This is a descriptive analysis of the existing Fmarket-derived archive, not a live market update, ranking, recommendation, transaction blotter or point-in-time backtest. The provider data has not been independently verified.

## What is available

- The catalogue contains 69 funds: 34 equity, 11 balanced and 24 bond/MMF. The latest stored Top-N equity and bond snapshots contain 587 rows across 68 distinct funds, observed on 27 October 2026. The source effective dates for those snapshots range from 8 to 14 October; observation date is not publication date.
- Holdings are Top-N disclosures, not complete portfolios. The archive contains only one holdings observation date and no `changes.csv`, so it cannot establish a holdings change or a confirmed transaction. The project has no transaction-level blotter.
- Source NAV history contains 47,121 rows for 59 funds and ends on 31 October 2025. The separate observation-date NAV snapshots cover 69 funds on 27 October 2026; their NAV effective dates are unknown. The two series are not stitched together, and the older history is not current performance.
- Asset allocations have 158 rows across 68 funds. Their source report dates range from 1 June to 1 September 2026; the latest report date exceeds the configured 45-day allocation freshness SLA as of 28 October 2026.

## Historical NAV illustration: exact common dates

For each fund with positive source NAV on **both 1 and 31 October 2025**, calculate `(NAV on 31 October / NAV on 1 October − 1) × 100`. Group by the fund category stored in `funds.csv`, then take the unweighted median across eligible funds. All calculations use only `nav.csv`; no missing observations are filled. Eligible sets differ by category.

| Category | Eligible funds / catalogue funds | Median simple NAV change |
|---|---:|---:|
| Equity | 23 / 34 | −0.83% |
| Balanced | 5 / 11 | −0.83% |
| Bond / MMF | 14 / 24 | +0.54% |

These are descriptive changes over a historical interval, not current returns, total return, benchmark-adjusted alpha or a comparison of equal fund samples. Fund NAV dates are source effective dates; original publication times are unknown, so this is not a point-in-time-safe backtest.

## Asset-allocation illustration

Among the 34 catalogue equity funds, 34 have a recorded equity allocation row. The unweighted median disclosed equity allocation is **85.07%**. Their source report dates vary between 1 June and 1 September 2026, so this is not a simultaneous or AUM-weighted market-wide allocation. It is not a measure of transactions or flows.

## Collection and verification limits

- This environment could not connect to Fmarket or Yuanta, so no new fund, market-price, valuation or company data was collected. There was no accessible Yuanta scan run in the checked GitHub Actions history.
- GitHub's API did report the historical daily workflow as completed successfully, last updated at 06:37:58 UTC on 27 October 2026 (13:37:58 ICT). That verifies the reported workflow outcome, not current source freshness or transaction data. The sanitized observation is stored in `data/workflow_status.json` with its source and observation time.
- Full holdings, verified publication times, live prices and confirmed transactions remain unavailable in this archive. Missing is not zero. Source licensing, access permissions and values should be checked before external publication or financial use.
