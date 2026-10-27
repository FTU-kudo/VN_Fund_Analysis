import test from 'node:test';
import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';

import {
  compareNavSeries,
  filterEvents,
  filterFunds,
  formatFreshness,
  formatMissingRate,
  loadDashboardData,
  normalizeDataset,
  paginate,
  resolveCompareState,
  resolveDataUrl,
  sanitizeCompareSelection,
  signedPercentFraction,
  signedPercentPoints,
  sortRows,
  summarizeFundFacts,
} from '../dashboard/app.mjs';
import {
  clearYuantaCache,
  loadYuantaStockBundle,
  loadYuantaUniverse,
  renderYuantaStockTabs,
  resolveYuantaUrl,
} from '../dashboard/yuanta_view.mjs';

test('dataset envelopes distinguish malformed, empty, missing, and unknown counts', () => {
  assert.equal(normalizeDataset(null, 'funds').status, 'malformed');
  assert.deepEqual(normalizeDataset({ status: 'empty', record_count: 0, records: [] }, 'funds').records, []);
  const missing = normalizeDataset({ status: 'missing', record_count: null, records: [] }, 'nav');
  assert.equal(missing.record_count, null);
  assert.equal(missing.status, 'missing');
  const sanitized = normalizeDataset({ status: 'available', record_count: 3, records: [null, { fund: 'F1' }, []] });
  assert.equal(sanitized.records.length, 1);
  assert.equal(sanitized.rejected_record_count, 2);
  assert.equal(sanitized.status, 'partial');
  assert.equal(sanitized.count_mismatch, true);
  assert.equal(normalizeDataset({ records: [null] }).status, 'malformed');
});

test('static artifact URLs stay under the project-site path', () => {
  const url = new URL(resolveDataUrl('manifest.json', 'https://example.test/VN_Fund_Analysis/app.mjs'));
  assert.equal(url.pathname, '/VN_Fund_Analysis/data/manifest.json');
});

test('loader rejects malformed external filenames without fetching them', async () => {
  const requests = [];
  const fetcher = async (url) => {
    requests.push(String(url));
    if (String(url).endsWith('/manifest.json')) {
      return { ok: true, json: async () => ({ schema_version: '1.0.0', datasets: {
        funds: { file: 'https://untrusted.invalid/secret.json' },
      } }) };
    }
    throw new Error('unexpected dataset fetch');
  };
  const loaded = await loadDashboardData(fetcher, 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(loaded.datasets.funds.status, 'malformed');
  assert.equal(requests.length, 1);
});

test('loader distinguishes malformed JSON and incompatible artifact schemas', async () => {
  const manifest = {
    schema_version: '1.0.0',
    datasets: { funds: { file: 'funds.json', schema_version: '1.0.0', status: 'available', record_count: 0 } },
  };
  const malformedData = await loadDashboardData(async (url) => {
    if (String(url).endsWith('/manifest.json')) return { ok: true, json: async () => manifest };
    return { ok: true, json: async () => { throw new SyntaxError('invalid JSON'); } };
  }, 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(malformedData.datasets.funds.status, 'malformed');

  const incompatibleData = await loadDashboardData(async (url) => {
    if (String(url).endsWith('/manifest.json')) return { ok: true, json: async () => manifest };
    return { ok: true, json: async () => ({ schema_version: '2.0.0', dataset: 'funds', status: 'available', record_count: 0, records: [] }) };
  }, 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(incompatibleData.datasets.funds.status, 'schema_mismatch');

  await assert.rejects(
    loadDashboardData(async () => ({ ok: true, json: async () => ({ schema_version: '2.0.0', datasets: {} }) }),
      'https://example.test/VN_Fund_Analysis/app.mjs'),
    /manifest_unsupported_schema/,
  );
});

test('fund filters apply search, category, and data coverage without coercing nulls', () => {
  const rows = [
    { fund_id: 'F1', name: 'Alpha', asset_code: 'STOCK', latest_holdings_count: 4, data_quality_status: 'partial' },
    { fund_id: 'F2', name: 'Beta', asset_code: 'BOND', latest_holdings_count: null, data_quality_status: 'unknown' },
  ];
  assert.deepEqual(filterFunds(rows, { query: 'alpha', category: 'STOCK' }).map((row) => row.fund_id), ['F1']);
  assert.deepEqual(filterFunds(rows, { dataStatus: 'without-holdings' }).map((row) => row.fund_id), ['F2']);
  assert.deepEqual(filterFunds(rows, { dataStatus: 'partial' }).map((row) => row.fund_id), ['F1']);
});

test('event date filters exclude unknown dates and preserve evidence filters', () => {
  const rows = [
    { ticker: 'AAA', observed_date: '2026-01-01', evidence_status: 'unconfirmed' },
    { ticker: 'BBB', observed_date: null, evidence_status: 'unconfirmed' },
    { ticker: 'CCC', observed_date: '2026-02-01', evidence_status: 'suspected' },
  ];
  assert.deepEqual(filterEvents(rows, { from: '2026-01-01', evidence: 'unconfirmed' }).map((row) => row.ticker), ['AAA']);
});

test('sorting is stable and keeps missing values at the end in either direction', () => {
  const rows = [{ id: 'a', n: 2 }, { id: 'b', n: null }, { id: 'c', n: 1 }, { id: 'd', n: 1 }];
  assert.deepEqual(sortRows(rows, 'n', 'asc').map((row) => row.id), ['c', 'd', 'a', 'b']);
  assert.deepEqual(sortRows(rows, 'n', 'desc').map((row) => row.id), ['a', 'c', 'd', 'b']);
});

test('pagination clamps invalid and out-of-range pages', () => {
  const rows = Array.from({ length: 31 }, (_, index) => index);
  assert.deepEqual(paginate(rows, 99, 15), {
    rows: [30], page: 3, pageSize: 15, totalPages: 3, total: 31,
  });
  assert.equal(paginate([], 0, 15).page, 1);
});

test('NAV comparison uses exact common positive history dates and no snapshots', () => {
  const records = [
    { fund: 'A', date: '2026-01-01', nav: 100, series_type: 'nav_history' },
    { fund: 'A', date: '2026-01-02', nav: 105, series_type: 'nav_history' },
    { fund: 'A', date: '2026-01-03', nav: 110, series_type: 'nav_snapshot' },
    { fund: 'B', date: '2026-01-01', nav: 200, series_type: 'nav_history' },
    { fund: 'B', date: '2026-01-02', nav: 190, series_type: 'nav_history' },
    { fund: 'B', date: '2026-01-04', nav: 180, series_type: 'nav_history' },
    { fund: 'B', date: '2026-01-05', nav: 0, series_type: 'nav_history' },
  ];
  const result = compareNavSeries(records, 'A', 'B');
  assert.equal(result.status, 'available');
  assert.deepEqual(result.dates, ['2026-01-01', '2026-01-02']);
  assert.ok(Math.abs(result.changeA - 5) < 1e-9);
  assert.ok(Math.abs(result.changeB + 5) < 1e-9);
  assert.match(result.basis, /not total return/);
  assert.equal(compareNavSeries(records, 'A', 'A').status, 'select_two_funds');
  const duplicate = [...records, { fund: 'A', date: '2026-01-01', nav: 101, series_type: 'nav_history' }];
  assert.equal(compareNavSeries(duplicate, 'A', 'B').status, 'duplicate_source_dates');
});

test('compare category change clears stale out-of-category funds while preserving compatible funds', () => {
  const funds = [
    { fund_id: 'DCDS', name: 'DC Dynamic', asset_code: 'STOCK' },
    { fund_id: 'VESAF', name: 'VinaCapital', asset_code: 'STOCK' },
    { fund_id: 'TCBF', name: 'Techcom Bond', asset_code: 'BOND' },
    { fund_id: 'DCBF', name: 'DC Bond', asset_code: 'BOND' },
  ];
  const nav = [
    { fund: 'DCDS', date: '2026-01-01', nav: 100, series_type: 'nav_history' },
    { fund: 'DCDS', date: '2026-01-02', nav: 102, series_type: 'nav_history' },
    { fund: 'VESAF', date: '2026-01-01', nav: 200, series_type: 'nav_history' },
    { fund: 'VESAF', date: '2026-01-02', nav: 204, series_type: 'nav_history' },
  ];
  // Direct out-of-category URL parameters must refuse cross-category compare cleanly.
  const staleUrlState = resolveCompareState(funds, nav, { category: 'BOND', a: 'DCDS', b: 'VESAF' });
  assert.equal(staleUrlState.result.status, 'selection_outside_category');
  assert.deepEqual(staleUrlState.candidates.map((f) => f.fund_id), ['TCBF', 'DCBF']);

  // Switching category from STOCK to BOND clears DCDS/VESAF immediately.
  assert.deepEqual(sanitizeCompareSelection(funds, 'BOND', 'DCDS', 'VESAF'), {
    category: 'BOND', a: '', b: '',
  });
  // Staying within STOCK keeps DCDS/VESAF intact.
  assert.deepEqual(sanitizeCompareSelection(funds, 'STOCK', 'DCDS', 'VESAF'), {
    category: 'STOCK', a: 'DCDS', b: 'VESAF',
  });
  const okState = resolveCompareState(funds, nav, { category: 'STOCK', a: 'DCDS', b: 'VESAF' });
  assert.equal(okState.result.status, 'available');
});

test('freshness SLA formatter separates within_sla, stale, missing, empty, schema_mismatch, and no_sla', () => {
  assert.equal(formatFreshness({ freshness_assessment: 'within_sla', age_days_at_reference: 13, sla_days: 45 }).tone, 'ok');
  assert.match(formatFreshness({ freshness_assessment: 'stale', age_days_at_reference: 56, sla_days: 45 }).label, /Quá hạn SLA/);
  assert.equal(formatFreshness({ freshness_assessment: 'unavailable_missing' }).label, 'Chưa có tệp');
  assert.equal(formatFreshness({ freshness_assessment: 'empty_dataset' }).label, 'Tệp rỗng');
  assert.equal(formatFreshness({ freshness_assessment: 'schema_mismatch' }).tone, 'error');
  assert.equal(formatFreshness({ freshness_assessment: 'not_assessed_no_sla', age_days_at_reference: 361 }).label, 'Chưa đặt SLA');
});

test('missing-rate, signed vi-VN deltas, and fund_facts grouping preserve semantics without coercing nulls', () => {
  assert.equal(formatMissingRate(null, 100).value, 'Chưa có dữ liệu');
  const pub = formatMissingRate(2251, 2251);
  assert.match(pub.value, /2\.251 \/ 2\.251 \(100%\)/);

  assert.equal(signedPercentFraction(null), '—');
  assert.match(signedPercentFraction(0.025), /^\+2,5%$/);
  assert.match(signedPercentFraction(-0.025), /^−2,5%$/);
  assert.equal(signedPercentPoints(0), '0%');
  assert.match(signedPercentPoints(-5.25), /^−5,25%$/);

  const grouped = summarizeFundFacts([
    { fund: 'DCDS', fact_type: 'allocation', asset_code: 'STOCK', pct: 0.85 },
    { fund: 'DCDS', fact_type: 'industry', industry: 'Ngân hàng', pct: 0.31 },
    { fund: 'DCDS', fact_type: 'fees', type: 'SELL', fee: 1.5 },
    { fund: 'DCDS', fact_type: 'profile', management_fee_pct: 1.9 },
    { fund: 'VESAF', fact_type: 'profile', management_fee_pct: 1.75 },
  ], 'DCDS');
  assert.equal(grouped.allocation.length, 1);
  assert.equal(grouped.industry.length, 1);
  assert.equal(grouped.fees.length, 1);
  assert.equal(grouped.profile.management_fee_pct, 1.9);
  assert.equal(grouped.performance.length, 0);
});

test('Yuanta loader decompresses .json.gz bundles, falls back to .json, and handles unsynced symbols cleanly', async () => {
  clearYuantaCache();
  assert.throws(() => resolveYuantaUrl('../secret.json', 'https://example.test/VN_Fund_Analysis/app.mjs'), /invalid_yuanta_path/);
  const okUrl = new URL(resolveYuantaUrl('stocks/ACB.json.gz', 'https://example.test/VN_Fund_Analysis/app.mjs'));
  assert.equal(okUrl.pathname, '/VN_Fund_Analysis/data/yuanta/stocks/ACB.json.gz');

  const sampleBundle = {
    schema_version: '1.0.0',
    symbol: 'VHM',
    scanned_at: '2026-10-27T08:00:00Z',
    master: {
      symbol: 'VHM',
      exchange: 'HOSE',
      company_name: 'CTCP Vinhomes',
      sector_name: 'Bất động sản',
      icb_l1: 'Tài chính',
      icb_l2: 'Bất động sản',
      macro_group: 'NON_FINANCIAL',
      macro_group_label: 'Phi tài chính',
      sub_group: 'REAL_ESTATE',
      sub_group_label: 'Bất động sản (tách khỏi Tài chính L1)',
      split_from_financial_l1: true,
      last_price: 42500,
      ref_price: 42000,
      pe: 7.9,
      pb: 1.35,
      roe: 18.5,
      roa: 6.4,
      stock_rating: 78.5,
      fundamental_score: 82,
      technical_score: 75,
    },
    page1_overview: {
      profile_info: { CompanyName: 'CTCP Vinhomes', Overview: 'Phát triển khu đô thị', Leaders: [{ Name: 'Phạm Thiếu Hoa', Position: 'Chủ tịch HĐQT' }] },
      stock_rating_info: { StockRating: 78.5, Fundamental: 82, RelativeStrength: 75 },
      stock_info: { MatchPrice: 42500, RefPrice: 42000, PE: 7.9, PB: 1.35, ROE: 18.5 },
      shareholders: [{ Name: 'Tập đoàn Vingroup', Quantity: 2900000000, OwnershipPercentage: 66.6 }],
      last_bid_offer: { BidPrice1: 42450, BidVol1: 12000, OfferPrice1: 42500, OfferVol1: 15000 },
      list_transaction: [{ TradingTime: '14:45:00', MatchPrice: 42500, MatchVolume: 50000, Side: 'B' }],
      peers: { IndustryPE: 12.1, Peers: [{ StockCode: 'KDH', CompanyName: 'Khang Điền', ClosePrice: 35000, PE: 12.4, PB: 1.65, ROE: 15.2 }] },
    },
    page2_trading: {
      daily: [
        { trading_date: '2026-10-24', open: 41.8, high: 42.2, low: 41.5, close: 42.0, pct_change: 0.5, volume: 6000000, foreign_buy_vol: 500000, foreign_sell_vol: 200000, foreign_net_vol: 300000, foreign_owned_rate: 16.3 },
        { trading_date: '2026-10-27', open: 42.0, high: 42.8, low: 41.9, close: 42.5, pct_change: 1.19, volume: 8500000, foreign_buy_vol: 900000, foreign_sell_vol: 300000, foreign_net_vol: 600000, foreign_owned_rate: 16.4 },
      ],
      foreign_charts: {
        ngay_giao_dich: [{ TradingDate: '2026-10-27', NetVal: 25500000000 }],
        gtgd_binh_quan_chart: [{ TradingDate: '2026-10-27', GTGD: 21000000000 }],
        ty_le_von_hoa_chart: [{ TradingDate: '2026-10-27', TyLeVonHoa: 16.4 }],
      },
    },
    page3_sector_correlation: {
      macro_group: 'NON_FINANCIAL',
      macro_group_label: 'Phi tài chính',
      sub_group: 'REAL_ESTATE',
      sub_group_label: 'Bất động sản (tách khỏi Tài chính L1)',
      peer_group_key: 'REAL_ESTATE',
      split_from_financial_l1: true,
      latest_period: '2026_Q3',
      peer_universe_size: 18,
      metrics: [
        { key: 'roe', label: 'ROE (%)', unit: '%', value: 18.5, peer_median: 11.2, peer_count: 18, macro_median: 12.4, macro_count: 420, favorable_percentile: 82.0 },
        { key: 'roa', label: 'ROA (%)', unit: '%', value: 6.4, peer_median: 3.8, peer_count: 18, macro_median: 5.1, macro_count: 420, favorable_percentile: 76.0 },
        { key: 'pe', label: 'P/E (x)', unit: 'x', value: 7.9, peer_median: 14.2, peer_count: 18, macro_median: 13.8, macro_count: 420, favorable_percentile: 79.0 },
      ],
    },
    page4_financials: {
      macro_group: 'NON_FINANCIAL',
      macro_group_label: 'Phi tài chính',
      cstc: {
        periods: ['2026_Q3', '2026_Q2', '2010_Q1'],
        rows: [{ component: 'Chỉ số sinh lời', field_name: 'ROE', unit: '%', values: { '2010_Q1': 14.1, '2026_Q2': 18.0, '2026_Q3': 18.5 } }],
      },
      bctt: {
        periods: ['2026_Q3', '2026_Q2', '2010_Q1'],
        rows: [{ component: 'KQKD', field_name: 'Doanh thu thuần về bán hàng và cung cấp dịch vụ', unit: 'Triệu đồng', values: { '2010_Q1': 1200000, '2026_Q2': 28500000, '2026_Q3': 31200000 } }],
      },
      charts: [
        { title: 'Doanh thu thuần & LNST', chart_type: 'bar_line', series: [{ label: 'LNST', points: [{ period: '2026_Q2', value: 8100000 }, { period: '2026_Q3', value: 9200000 }] }] },
      ],
    },
    page5_events: {
      record_count: 1,
      articles: [{ article_id: 'VHM-1', published_at: '2026-10-15T08:00:00', published_date: '2026-10-15', title: 'VHM: Nghị quyết chi trả cổ tức', category: 'Cổ tức', url: 'https://ysradar.yuanta.com.vn/news/1' }],
    },
  };

  const gzBytes = gzipSync(Buffer.from(JSON.stringify(sampleBundle), 'utf-8'));
  const loadedGz = await loadYuantaStockBundle('VHM', async (url) => {
    assert.match(String(url), /VHM\.json\.gz$/);
    return new Response(gzBytes, { status: 200, headers: { 'Content-Type': 'application/octet-stream' } });
  }, 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(loadedGz.status, 'available');
  assert.equal(loadedGz.bundle.symbol, 'VHM');

  // Render all 5 Yuanta tabs and check real-estate separation + 2010_Q1 + SVG charts
  for (const stab of ['overview', 'trading', 'correlation', 'financials', 'events']) {
    const out = renderYuantaStockTabs('VHM', stab, loadedGz, new URLSearchParams());
    assert.ok(out.nav.includes(`stab=${stab}`));
    assert.ok(out.body && out.body.length > 100);
  }
  const corrView = renderYuantaStockTabs('VHM', 'correlation', loadedGz, new URLSearchParams()).body;
  assert.match(corrView, /Đã tách nhóm ngành trọng yếu: Bất động sản/);
  assert.match(corrView, /Phi tài chính/);
  assert.match(corrView, /<svg/);

  const finView = renderYuantaStockTabs('VHM', 'financials', loadedGz, new URLSearchParams()).body;
  assert.match(finView, /2010_Q1/);
  assert.match(finView, /<svg/);

  // Fallback to plain .json when .json.gz returns 404
  const loadedPlain = await loadYuantaStockBundle('ACB', async (url) => {
    if (String(url).endsWith('.json.gz')) return new Response('missing', { status: 404 });
    return new Response(JSON.stringify({ ...sampleBundle, symbol: 'ACB' }), { status: 200 });
  }, 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(loadedPlain.status, 'available');
  assert.equal(loadedPlain.bundle.symbol, 'ACB');

  // Unsynced symbol displays honest notice without fabricated numbers
  const unsynced = await loadYuantaStockBundle('ZZZ', async () => new Response('missing', { status: 404 }), 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(unsynced.status, 'not_synced');
  const unsyncedHtml = renderYuantaStockTabs('ZZZ', 'overview', unsynced, new URLSearchParams()).body;
  assert.match(unsyncedHtml, /scan_yuanta_universe\.py/);

  const uniMissing = await loadYuantaUniverse(async () => new Response('missing', { status: 404 }), 'https://example.test/VN_Fund_Analysis/app.mjs');
  assert.equal(uniMissing.status, 'not_synced');
});
