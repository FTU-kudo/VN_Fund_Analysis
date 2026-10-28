# Vietnam open-end fund archive: descriptive analysis

**Archive reviewed:** 28 October 2026. **Investment scope:** descriptive research only; not investment advice, a fund ranking, an estimate of alpha, or evidence of trades. Data is Fmarket-derived and has not been independently verified. Dates are source or observation dates, not verified publication dates.

## Executive findings

1. The catalogue contains **69 funds**: 34 equity, 11 balanced and 24 bond/MMF. Stored Top-N holdings cover 68 distinct funds, with 587 equity and bond rows observed on 27 October 2026. They are not complete portfolios.
2. Among all 34 equity funds, the median source-reported equity allocation is **85.07%**, with an interquartile range of **67.16%–93.71%**. This is an equal-fund statistic, not an AUM-weighted market exposure. Allocation report dates are mostly 1 September 2026, with one fund dated 1 June; the dataset's latest report date is outside the configured 45-day SLA as of 28 October.
3. The source-reported banking sector allocation has a median of **31.54%** among the **33 of 34 equity funds with a banking row**. The median is conditional on a row being reported; the missing fund's value is unknown, not zero. The provider's allocation denominator and sector definitions require independent verification. Real Estate is treated separately from Banks.
4. The latest historical source NAV date in the archive is **31 October 2025**. For the 19 equity funds with positive NAV on both 2 January and 31 October 2025, median simple NAV change was **+13.09%** and median observed-point maximum drawdown was **−19.40%**. For 12 qualifying bond/MMF funds, the comparable medians were **+4.90%** and **−0.19%**. This is an old, selected sample, not current performance or a fair risk-adjusted comparison.
5. There is only **one holdings observation date** in the archive and no portfolio change journal. No purchase, sale, fund flow, transaction or change in conviction can be inferred from these snapshots alone.

## 1. Coverage and timing

| Dataset | Coverage in archive | Relevant date / limitation |
|---|---:|---|
| Fund catalogue | 69 funds | Last seen 27 October 2026 |
| Equity Top-N holdings | 407 rows, 46 funds | Observed 27 October; source effective/update dates 8–14 October 2026 |
| Bond Top-N holdings | 180 rows, 33 funds | Observed 27 October; source effective/update dates 8–14 October 2026 |
| Distinct funds across both Top-N datasets | 68 funds | Equity and bond fund counts overlap; do not add 46 and 33 |
| Source NAV history | 47,121 rows, 59 funds | 20 May 2004 to 31 October 2025 across the archive; latest date differs by fund |
| NAV observations | 69 rows, 69 funds | Observed 27 October 2026; effective dates unknown |
| Asset allocation | 158 rows, 68 funds | 67 funds report 1 September 2026; one reports 1 June 2026 |
| Industry allocation | 569 rows, 45 funds | Observed 27 October 2026; report/publication dates not supplied in this table |

The historical NAV series and the 2026 observation snapshots have different time semantics and are **not joined**. The latest NAV date for the archive is not the last available date for every fund. No historical publication timestamp is verified, so results are not point-in-time backtest evidence.

## 2. Historical NAV changes and observed drawdowns

For funds with a positive `nav.csv` value on both exact dates, calculate simple NAV change as `(end NAV / start NAV − 1) × 100`. For the longer window, calculate each eligible fund's maximum drawdown from the sequential NAV observations in the window, then report the median across funds. Funds with gaps greater than 35 calendar days would be excluded from the drawdown calculation; none of the exact-date eligible funds in this window had such a gap. The reported drawdown is limited to observed NAV points and may miss intra-period lows.

### 2 January–31 October 2025

| Category | Eligible / catalogue funds | Median simple NAV change | Median observed-point maximum drawdown |
|---|---:|---:|---:|
| Equity | 19 / 34 | +13.09% | −19.40% |
| Balanced | 2 / 11 | +13.04% | −11.65% |
| Bond / MMF | 12 / 24 | +4.90% | −0.19% |

**Interpretation:** the eligible equity sample had a higher median NAV change but a much larger median observed drawdown than the eligible bond/MMF sample in this historical window. The balanced sample has only two funds and is not representative. Different fund membership, missing histories and observation frequencies limit comparisons. These are not verified total-return, benchmark-adjusted or risk-adjusted conclusions. No independent fee adjustment was made; NAV may already reflect some fund-level fees. NAV and distribution conventions need verification, and investor-level transaction costs are not included.

### 1–31 October 2025

| Category | Eligible / catalogue funds | Median simple NAV change | Funds with a positive change / eligible |
|---|---:|---:|---:|
| Equity | 23 / 34 | −0.83% | 6 / 23 |
| Balanced | 5 / 11 | −0.83% | 0 / 5 |
| Bond / MMF | 14 / 24 | +0.54% | 13 / 14 |

This second window has different eligible funds from the January–October window. The contrast is a description of historical observations, not a current market signal or evidence of persistent performance.

## 3. Allocation and concentration

### Source-reported asset allocation

For each category and asset class, the median is computed **only over funds with a reported row for that asset class**. A missing row is not interpreted as a zero allocation. Separate medians are not components of a single hypothetical portfolio and need not add to 100%.

| Fund category | Reported asset row | Funds with row / category | Median allocation | Interquartile range |
|---|---|---:|---:|---:|
| Equity | Equity | 34 / 34 | 85.07% | 67.16%–93.71% |
| Equity | Cash and equivalents | 34 / 34 | 13.76% | 6.29%–26.53% |
| Balanced | Equity | 11 / 11 | 45.97% | 36.55%–53.01% |
| Bond / MMF | Bonds | 23 / 24 | 64.74% | 35.38%–72.18% |
| Bond / MMF | Cash and equivalents | 23 / 24 | 34.40% | 22.68%–60.21% |

The wide interquartile ranges, particularly among bond/MMF funds, show heterogeneity **within this reported sample**. They do not establish an aggregate exposure, a change over time or a recommended allocation. The 2026 report dates are not contemporaneous with the 2025 historical NAV calculations.

### Source-reported industry allocation within equity funds

| Industry as labelled by source | Equity funds with row / 34 | Median reported percentage among those funds | Interquartile range among those funds |
|---|---:|---:|---:|
| Banks | 33 / 34 | 31.54% | 27.31%–34.05% |
| Real Estate | 29 / 34 | 7.03% | 5.12%–16.91% |
| Information technology | 28 / 34 | 3.97% | 2.17%–6.67% |

Bank exposure appears widespread among the funds for which the source supplies an industry row. **Do not treat absent industry rows as zero**, and do not sum the displayed medians: their underlying fund sets differ. These values reflect the source's reported percentages; the exact denominator, industry mapping and original reporting period need verification. Real Estate remains separate from Banks and other financial services.

### Disclosed equity Top-N positions

All 34 catalogue equity funds have equity Top-N rows. Within each fund, sum the reported weights of its stored equity rows; no missing weights are filled. Across these 34 funds, the median disclosed weight is **51.95%** of fund NAV and the interquartile range is **45.10%–54.34%**. The source supplies **5–10 rows per fund**, so the figures describe different amounts of disclosed positions and cannot measure the concentration of each entire portfolio. Weights may reflect different source update dates.

For illustration, **TCB** and **MWG** each occur in the disclosed equity Top-N lists of **24 of 34** equity funds. This is a count of distinct funds with a reported row—not shares held, AUM-weighted ownership, buying, selling, or a stock recommendation. The other ten funds for each ticker may still hold it outside their Top-N.

## 4. What cannot be concluded

- **No fund buying/selling:** one observation date and a missing change journal do not establish a change; even future Top-N changes or inferred `BUY`/`SELL` labels are not confirmed executions.
- **No full portfolio or complete sector risk:** Top-N coverage is censored and missing holdings are unknown. Do not use zero for undisclosed positions.
- **No current return, market price, valuation or alpha:** the available NAV history ends in October 2025, and the Yuanta stock bundle was not available in this environment. The 2026 NAV snapshot cannot be safely stitched onto the 2025 series.
- **No reliable publication-time or backtest claim:** effective, report and observation dates are distinct, and original publication timestamps are not verified.
- **No conclusion from workflow success alone:** the GitHub-reported daily workflow success was last updated 27 October 2026 at 13:37:58 ICT; it does not establish source freshness, data completeness or current availability.

## Method and provenance

- Inputs: `data/funds.csv`, `data/nav.csv`, `data/nav_snapshots.csv`, `data/allocation.csv`, `data/industry.csv`, `data/holdings/*.csv`, and `data/bonds/*.csv`; semicolon-delimited archive files.
- Fund category comes from the catalogue's `asset_code`. NAV observations must be finite, positive and present on both exact endpoints. Deduplicate by fund/date, retaining the last stored row; the checked archive had no duplicate NAV fund/date keys.
- Percentiles use pandas' default linear interpolation; all fund-level medians give each eligible fund equal weight. Allocation/sector percentages are source-reported fractions multiplied by 100. Coverage is stated separately for each calculation; missing values are never replaced by zero.
- For Top-N rows, group by fund and sum numeric weights using `sum(min_count=1)`; the checked 34 equity-fund Top-N snapshots had no missing reported weights. Distinct-fund ticker counts use the stored ticker, not a transaction or ownership estimate.
- No additional provider data was collected during this analysis. Source licensing and rights to reuse data require separate verification.
