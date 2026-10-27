import test from 'node:test';
import assert from 'node:assert/strict';

import {
  compareNavSeries,
  filterEvents,
  filterFunds,
  loadDashboardData,
  normalizeDataset,
  paginate,
  resolveDataUrl,
  sortRows,
} from '../dashboard/app.mjs';

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
