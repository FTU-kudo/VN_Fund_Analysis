import { getLang, setLang, t, formatDate, formatNumber, formatInteger, formatPercentFraction, formatSignedPercentFraction, formatSignedPercentPoints, translateMacroGroup, translateSubGroup } from './i18n.mjs';

import {
  clearYuantaCache,
  loadYuantaStockBundle,
  loadYuantaUniverse,
  renderYuantaStockTabs,
} from './yuanta_view.mjs';

const DATASET_NAMES = [
  'funds', 'holdings', 'nav_history', 'portfolio_events', 'fund_facts',
  'data_quality', 'data_quality_summary', 'source_events', 'pipeline_status',
];
const SUPPORTED_SCHEMA_MAJOR = 1;
const PAGE_TITLES = {
  overview: 'Tổng quan', funds: 'Quỹ đầu tư', holdings: 'Danh mục & thay đổi',
  stocks: 'Nghiên cứu mã', compare: 'So sánh quỹ', quality: 'Chất lượng dữ liệu',
  pipeline: 'Sức khỏe pipeline', methodology: 'Phương pháp',
};
const COLLATOR = new Intl.Collator('vi', { numeric: true, sensitivity: 'base' });

export function normalizeDataset(value, name = 'unknown') {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !Array.isArray(value.records)) {
    return { dataset: name, status: 'malformed', record_count: null, records: [], error: 'invalid_dataset_envelope' };
  }
  const validRecords = value.records.filter((record) => record && typeof record === 'object' && !Array.isArray(record));
  const rejectedRecordCount = value.records.length - validRecords.length;
  const reportedCount = Number.isSafeInteger(value.record_count) && value.record_count >= 0 ? value.record_count : null;
  const countMismatch = reportedCount !== null && reportedCount !== validRecords.length;
  const reportedStatus = typeof value.status === 'string' ? value.status : 'unknown';
  const status = rejectedRecordCount || countMismatch
    ? (['available', 'empty', 'partial'].includes(reportedStatus) ? 'partial' : 'malformed')
    : reportedStatus;
  return {
    ...value,
    dataset: typeof value.dataset === 'string' ? value.dataset : name,
    status,
    record_count: reportedCount,
    records: validRecords,
    rejected_record_count: rejectedRecordCount,
    count_mismatch: countMismatch,
  };
}

function schemaMajor(version) {
  if (typeof version !== 'string') return null;
  const parts = version.split('.');
  if (parts.length !== 3 || parts.some((part) => !part || [...part].some((char) => char < '0' || char > '9'))) return null;
  return Number(parts[0]);
}

export function resolveDataUrl(file, moduleUrl = import.meta.url) {
  return new URL(`./data/${encodeURIComponent(file)}`, moduleUrl).href;
}

export async function loadDashboardData(fetcher = globalThis.fetch, moduleUrl = import.meta.url) {
  if (typeof fetcher !== 'function') throw new Error('fetch_unavailable');
  const manifestUrl = resolveDataUrl('manifest.json', moduleUrl);
  const manifestResponse = await fetcher(manifestUrl, { cache: 'no-store' });
  if (!manifestResponse || !manifestResponse.ok) throw new Error('manifest_unavailable');
  let manifest;
  try {
    manifest = await manifestResponse.json();
  } catch {
    throw new Error('manifest_malformed');
  }
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)
      || !manifest.datasets || typeof manifest.datasets !== 'object' || Array.isArray(manifest.datasets)) {
    throw new Error('manifest_malformed');
  }
  const manifestMajor = schemaMajor(manifest.schema_version);
  if (manifestMajor !== SUPPORTED_SCHEMA_MAJOR) throw new Error('manifest_unsupported_schema');
  const datasets = {};
  await Promise.all(DATASET_NAMES.map(async (name) => {
    const meta = manifest.datasets[name];
    if (!meta || typeof meta.file !== 'string' || !/^[A-Za-z0-9_-]+\.json$/.test(meta.file)) {
      datasets[name] = normalizeDataset(null, name);
      return;
    }
    if (schemaMajor(meta.schema_version) !== manifestMajor) {
      datasets[name] = { dataset: name, status: 'schema_mismatch', record_count: null, records: [], error: 'dataset_schema_mismatch' };
      return;
    }
    let response;
    try {
      response = await fetcher(new URL(meta.file, manifestUrl).href, { cache: 'no-store' });
    } catch {
      datasets[name] = { dataset: name, status: 'unavailable', record_count: null, records: [], error: 'dataset_fetch_failed' };
      return;
    }
    if (!response || !response.ok) {
      datasets[name] = { dataset: name, status: 'unavailable', record_count: null, records: [], error: 'dataset_fetch_failed' };
      return;
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      datasets[name] = { dataset: name, status: 'malformed', record_count: null, records: [], error: 'dataset_json_invalid' };
      return;
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)
        || payload.dataset !== name || schemaMajor(payload.schema_version) !== manifestMajor) {
      datasets[name] = { dataset: name, status: 'schema_mismatch', record_count: null, records: [], error: 'dataset_schema_mismatch' };
      return;
    }
    if ((meta.status && payload.status !== meta.status)
        || (Object.hasOwn(meta, 'record_count') && payload.record_count !== meta.record_count)) {
      datasets[name] = { dataset: name, status: 'schema_mismatch', record_count: null, records: [], error: 'manifest_dataset_mismatch' };
      return;
    }
    datasets[name] = normalizeDataset(payload, name);
  }));
  return { manifest, datasets };
}

export function filterFunds(rows, { query = '', category = '', dataStatus = '' } = {}) {
  const term = query.trim().toLocaleLowerCase('vi');
  return rows.filter((row) => {
    const haystack = [row.fund_id, row.name, row.management_company, row.asset_code].filter(Boolean).join(' ').toLocaleLowerCase('vi');
    if (term && !haystack.includes(term)) return false;
    if (category && row.asset_code !== category) return false;
    if (dataStatus === 'with-holdings' && row.latest_holdings_count == null) return false;
    if (dataStatus === 'without-holdings' && row.latest_holdings_count != null) return false;
    if (dataStatus === 'partial' && row.data_quality_status !== 'partial') return false;
    return true;
  });
}

export function filterEvents(rows, { fund = '', ticker = '', classification = '', from = '', to = '', evidence = '' } = {}) {
  const tickerTerm = ticker.trim().toLocaleUpperCase('vi');
  return rows.filter((row) => {
    if (fund && row.fund !== fund) return false;
    if (tickerTerm && !(row.ticker || '').toLocaleUpperCase('vi').includes(tickerTerm)) return false;
    if (classification && row.classification !== classification) return false;
    if (from && (!row.observed_date || row.observed_date < from)) return false;
    if (to && (!row.observed_date || row.observed_date > to)) return false;
    if (evidence && row.evidence_status !== evidence) return false;
    return true;
  });
}

export function sortRows(rows, key, direction = 'asc') {
  const sign = direction === 'desc' ? -1 : 1;
  return rows.map((row, index) => ({ row, index })).sort((a, b) => {
    const av = a.row?.[key];
    const bv = b.row?.[key];
    const aMissing = av === null || av === undefined || av === '' || (typeof av === 'number' && !Number.isFinite(av));
    const bMissing = bv === null || bv === undefined || bv === '' || (typeof bv === 'number' && !Number.isFinite(bv));
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (aMissing && bMissing) return a.index - b.index;
    let comparison;
    if (typeof av === 'number' && typeof bv === 'number') comparison = av - bv;
    else comparison = COLLATOR.compare(String(av), String(bv));
    return comparison === 0 ? a.index - b.index : comparison * sign;
  }).map((entry) => entry.row);
}

export function paginate(rows, page = 1, pageSize = 15) {
  const size = Math.max(1, Math.floor(pageSize));
  const totalPages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(Math.max(1, Math.floor(page) || 1), totalPages);
  const start = (current - 1) * size;
  return { rows: rows.slice(start, start + size), page: current, pageSize: size, totalPages, total: rows.length };
}

export function compareNavSeries(records, fundA, fundB) {
  if (!fundA || !fundB || fundA === fundB) return { status: 'select_two_funds', dates: [], seriesA: [], seriesB: [] };
  const sourceRows = records.filter((row) => row.series_type === 'nav_history' && [fundA, fundB].includes(row.fund));
  const seen = new Set();
  for (const row of sourceRows) {
    if (!row.date) continue;
    const key = `${row.fund}\u0000${row.date}`;
    if (seen.has(key)) return { status: 'duplicate_source_dates', dates: [], seriesA: [], seriesB: [] };
    seen.add(key);
  }
  const nav = (fund) => new Map(sourceRows.filter((row) => row.fund === fund && Number.isFinite(row.nav) && row.nav > 0 && row.date)
    .map((row) => [row.date, row.nav]));
  const a = nav(fundA);
  const b = nav(fundB);
  const dates = [...a.keys()].filter((date) => b.has(date)).sort();
  if (dates.length < 2) return { status: 'insufficient_common_dates', dates, seriesA: [], seriesB: [] };
  const start = dates[0];
  const end = dates[dates.length - 1];
  const startA = a.get(start); const endA = a.get(end);
  const startB = b.get(start); const endB = b.get(end);
  return {
    status: 'available', dates,
    seriesA: dates.map((date) => ({ date, nav: a.get(date), normalized: a.get(date) / startA * 100 })),
    seriesB: dates.map((date) => ({ date, nav: b.get(date), normalized: b.get(date) / startB * 100 })),
    start, end,
    changeA: (endA / startA - 1) * 100,
    changeB: (endB / startB - 1) * 100,
    basis: 'same exact NAV source dates; simple NAV change only, not total return or a recommendation',
  };
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function records(data, key) { return data.datasets[key]?.records || []; }
function envelope(data, key) { return data.datasets[key] || { status: 'unavailable', record_count: null, records: [] }; }
function statusText(status, lang = getLang()) {
  if (lang === 'en') return ({available:'Available', partial:'Partial', stale:'Possibly stale', empty:'Empty', missing:'Missing', unreadable:'Unreadable', schema_mismatch:'Schema mismatch', unsafe_path:'Unsafe path', unavailable:'Unavailable', malformed:'Malformed', valid:'Valid', invalid:'Invalid', unknown:'Unknown', not_applicable:'Not applicable'})[status] || status || 'Unknown';
  return ({ available: 'Có dữ liệu', partial: 'Một phần', stale: 'Có thể cũ', empty: 'Tệp rỗng', missing: 'Chưa có',
    unreadable: 'Không đọc được', schema_mismatch: 'Sai schema', unsafe_path: 'Đường dẫn không an toàn',
    unavailable: 'Chưa khả dụng', malformed: 'Dữ liệu sai định dạng', valid: 'Hợp lệ', invalid: 'Không hợp lệ',
    unknown: 'Chưa xác định', not_applicable: 'Chưa áp dụng' })[status] || status || 'Chưa xác định';
}
function statusTone(status) {
  if (['invalid', 'unreadable', 'schema_mismatch', 'unsafe_path', 'malformed'].includes(status)) return 'error';
  if (['partial', 'stale', 'warning', 'issues_found'].includes(status)) return 'warning';
  if (['available', 'valid'].includes(status)) return 'ok';
  return 'neutral';
}
function datasetStatusNotice(dataset, label) {
  const status = dataset?.status || 'unavailable';
  const rejectedRows = Number.isSafeInteger(dataset?.rejected_record_count) ? dataset.rejected_record_count : 0;
  const countMismatch = dataset?.count_mismatch === true;
  if (getLang() === 'en') {
    const messages = {
      missing: ['Data missing', `${label} is absent from the archive; this does not prove that the source returned no records.`, 'info'],
      empty: ['Empty source file', `${label} has a file but no records were read in this snapshot.`, 'info'],
      partial: ['Incomplete data', rejectedRows ? `${label}: ${integer(rejectedRows)} malformed records were excluded.` : countMismatch ? `${label}: declared record count does not match valid records.` : `${label} has partial coverage or a bounded window.`, 'warning'],
      stale: ['Data may be stale', `${label} was flagged stale; review dates and SLA.`, 'warning'],
      unreadable: ['Unreadable data', `${label} cannot be read; missing values are not zero.`, 'error'],
      schema_mismatch: ['Schema mismatch', `${label} does not match the expected schema.`, 'error'],
      unsafe_path: ['Unsafe path', `${label} was not exported due to an unsafe path.`, 'error'],
      malformed: ['Malformed envelope', `${label} does not have a valid data structure.`, 'error'],
      unavailable: ['Artifact unavailable', `${label} could not be loaded; retry or check the Pages build.`, 'error'],
    };
    const message = messages[status];
    return message ? notice(message[0], message[1], message[2]) : '';
  }
  const messages = {
    missing: ['Chưa có dữ liệu', `${label} chưa có trong archive; đây không phải bằng chứng nguồn trả về rỗng.`, 'info'],
    empty: ['Tệp nguồn rỗng', `${label} có tệp nhưng không có bản ghi được đọc trong snapshot này.`, 'info'],
    partial: ['Dữ liệu chưa đầy đủ', rejectedRows > 0
      ? `${label}: ${integer(rejectedRows)} bản ghi sai cấu trúc đã bị loại khỏi giao diện.`
      : countMismatch ? `${label} có số bản ghi khai báo không khớp số bản ghi hợp lệ; hãy kiểm tra artifact.`
        : `${label} chỉ có một phần dữ liệu hoặc bị giới hạn theo coverage/window.`, 'warning'],
    stale: ['Dữ liệu có thể cũ', `${label} được gắn stale bởi policy nguồn; hãy kiểm tra ngày và SLA trước khi sử dụng.`, 'warning'],
    unreadable: ['Không đọc được dữ liệu', `${label} tồn tại nhưng không đọc được; không tự thay bằng giá trị 0.`, 'error'],
    schema_mismatch: ['Schema không khớp', `${label} không khớp schema dự kiến; cần xử lý migration tường minh.`, 'error'],
    unsafe_path: ['Đường dẫn không an toàn', `${label} không được export vì đường dẫn không an toàn.`, 'error'],
    malformed: ['Envelope sai định dạng', `${label} không có cấu trúc dữ liệu hợp lệ.`, 'error'],
    unavailable: ['Không tải được artifact', `${label} JSON tĩnh không khả dụng; thử tải lại hoặc kiểm tra Pages build.`, 'error'],
  };
  const message = messages[status];
  return message ? notice(message[0], message[1], message[2]) : '';
}

function badge(text, tone = 'neutral', title = '') {
  const symbol = tone === 'error' ? '!' : tone === 'warning' ? '·' : tone === 'ok' ? '✓' : '—';
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  return `<span class="table-status ${tone === 'ok' ? '' : tone}"${titleAttr}><span class="status-symbol" aria-hidden="true">${symbol}</span>${esc(text)}</span>`;
}
function topNCoverageBadge(coverageType, lang = getLang()) {
  if (lang === 'en') {
    if (coverageType === 'top_n_partial_censored') return badge('Top-N', 'warning', 'Only Top-N holdings are disclosed; undisclosed positions are unknown, not proof of full exit');
    if (coverageType === 'source_claimed_full_not_independently_verified') return badge('Full (source claim)', 'neutral', 'Source claims a complete portfolio; not independently verified');
    return badge('Coverage unknown', 'neutral');
  }
  if (coverageType === 'top_n_partial_censored') {
    return badge('Top-N', 'warning', 'Nguồn chỉ công bố Top-N; mã ngoài Top-N là chưa biết, không phải bằng chứng bán hết');
  }
  if (coverageType === 'source_claimed_full_not_independently_verified') {
    return badge('Full (nguồn)', 'neutral', 'Nguồn khai báo danh mục đầy đủ; chưa xác minh độc lập');
  }
  return badge('Chưa rõ độ phủ', 'neutral');
}
function number(value, digits = 2) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  return formatNumber(value, digits, getLang());
}
function integer(value) { return formatInteger(value, getLang()); }
function percentFraction(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return formatPercentFraction(value, digits, getLang());
}
function percentPoints(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return `${number(value, digits)}%`;
}
export function signedPercentFraction(value, digits = 2, lang = 'vi') {
  return formatSignedPercentFraction(value, digits, lang);
}
export function signedPercentPoints(value, digits = 2, lang = 'vi') {
  return formatSignedPercentPoints(value, digits, lang);
}
export function formatMissingRate(missingCount, totalCount, lang = 'vi') {
  if (lang === 'en') {
    if (missingCount == null || missingCount === '' || !Number.isFinite(Number(missingCount))) return {value:'Not available', basis:'No observation sample available'};
    const miss = Number(missingCount);
    if (totalCount == null || totalCount === '' || !Number.isFinite(Number(totalCount)) || Number(totalCount) <= 0) return {value:formatInteger(miss, lang), basis:'Total observation count is zero or unknown'};
    const total = Number(totalCount);
    return {value:`${formatInteger(miss, lang)} / ${formatInteger(total, lang)} (${formatPercentFraction(miss / total, 1, lang)})`, basis:`Missing among ${formatInteger(total, lang)} sidecar records; unknown remains unknown`};
  }
  if (missingCount === null || missingCount === undefined || !Number.isFinite(Number(missingCount))) {
    return { value: 'Chưa có dữ liệu', basis: 'Chưa có mẫu quan sát để tính tỷ lệ thiếu' };
  }
  const miss = Number(missingCount);
  if (totalCount === null || totalCount === undefined || !Number.isFinite(Number(totalCount)) || Number(totalCount) <= 0) {
    return { value: integer(miss), basis: 'Tổng số bản ghi quan sát bằng 0 hoặc chưa xác định' };
  }
  const total = Number(totalCount);
  const rate = percentFraction(miss / total, 1);
  return {
    value: `${integer(miss)} / ${integer(total)} (${rate})`,
    basis: `Thiếu trên tổng ${integer(total)} bản ghi sidecar; unknown giữ là unknown`,
  };
}
export function formatFreshness(item = {}, lang = 'vi') {
  if (lang === 'en') {
    const assessment = item?.freshness_assessment || 'not_assessed_no_sla';
    const age = item?.age_days_at_reference; const sla = item?.sla_days;
    const ageLabel = age == null ? null : `${formatInteger(age, lang)} days`;
    switch (assessment) {
      case 'within_sla': return {label: sla != null ? `Within SLA (≤${sla}d)` : 'Within SLA', tone:'ok', detail: ageLabel ? `${ageLabel} · SLA ≤${sla} days` : 'Within SLA'};
      case 'stale': return {label: sla != null ? `Exceeds SLA (>${sla}d)` : 'Exceeds SLA', tone:'warning', detail: ageLabel ? `${ageLabel} · exceeds SLA ${sla} days` : 'Exceeds SLA'};
      case 'unavailable_missing': return {label:'File missing', tone:'neutral', detail:'Dataset absent from archive'};
      case 'empty_dataset': return {label:'Empty file', tone:'neutral', detail:'File has a header but zero rows'};
      case 'schema_mismatch': return {label:'Schema mismatch', tone:'error', detail:'Age not computed due to schema or read error'};
      case 'unknown_no_dates': return {label:'Unknown date', tone:'neutral', detail:'No valid date column'};
      default: return {label:'No SLA configured', tone:'neutral', detail:ageLabel ? `${ageLabel} · no SLA` : 'No SLA configured'};
    }
  }
  const assessment = item?.freshness_assessment || 'not_assessed_no_sla';
  const age = item?.age_days_at_reference;
  const sla = item?.sla_days;
  const ageLabel = age == null ? null : `${integer(age)} ngày`;
  switch (assessment) {
    case 'within_sla':
      return { label: sla != null ? `Đạt SLA (≤${sla}d)` : 'Đạt SLA', tone: 'ok', detail: ageLabel ? `${ageLabel} · SLA ≤${sla} ngày` : 'Đạt SLA' };
    case 'stale':
      return { label: sla != null ? `Quá hạn SLA (>${sla}d)` : 'Quá hạn SLA', tone: 'warning', detail: ageLabel ? `${ageLabel} · vượt SLA ${sla} ngày` : 'Quá hạn SLA' };
    case 'unavailable_missing':
      return { label: 'Chưa có tệp', tone: 'neutral', detail: 'Dataset thiếu trong archive' };
    case 'empty_dataset':
      return { label: 'Tệp rỗng', tone: 'neutral', detail: 'Tệp có header nhưng 0 dòng' };
    case 'schema_mismatch':
      return { label: 'Sai schema', tone: 'error', detail: 'Không tính tuổi do lỗi schema/đọc tệp' };
    case 'unknown_no_dates':
      return { label: 'Chưa rõ ngày', tone: 'neutral', detail: 'Không có cột ngày hợp lệ' };
    default:
      return { label: 'Chưa đặt SLA', tone: 'neutral', detail: ageLabel ? `${ageLabel} · không áp SLA` : 'Chưa đặt SLA' };
  }
}
export function sanitizeCompareSelection(funds, nextCategory, fundA, fundB) {
  const fundMap = new Map((funds || []).map((fund) => [fund.fund_id, fund]));
  const keep = (fundId) => {
    if (!fundId) return '';
    const record = fundMap.get(fundId);
    if (!record || !record.asset_code) return '';
    if (nextCategory && record.asset_code !== nextCategory) return '';
    return fundId;
  };
  return { category: nextCategory || '', a: keep(fundA), b: keep(fundB) };
}
export function summarizeFundFacts(factRows, fundId) {
  const mine = (factRows || []).filter((row) => row && row.fund === fundId);
  return {
    allocation: mine.filter((row) => row.fact_type === 'allocation'),
    industry: mine.filter((row) => row.fact_type === 'industry'),
    fees: mine.filter((row) => row.fact_type === 'fees'),
    profile: mine.find((row) => row.fact_type === 'profile') || null,
    performance: mine.filter((row) => row.fact_type === 'performance'),
  };
}
function dateText(value) {
  return formatDate(value, getLang());
}
function dateTimeText(value) { return value ? dateText(value) : t(getLang(), 'not_recorded'); }
function categoryText(code, lang = getLang()) {
  if (lang === 'en') return ({STOCK:'Equity', BALANCED:'Balanced', BOND:'Bond / MMF'})[code] || (code ? `Other · ${code}` : 'Unclassified');
  return ({ STOCK: 'Cổ phiếu', BALANCED: 'Cân bằng', BOND: 'Trái phiếu / MMF' })[code] || (code ? `Khác · ${code}` : 'Chưa phân loại');
}
function sourceLink(value) {
  if (!value) return `<span class="muted">${esc(t(getLang(), 'no_source_ref'))}</span>`;
  try {
    const url = new URL(value, window.location.href);
    if (['https:', 'http:'].includes(url.protocol) && url.origin !== window.location.origin) {
      return `<a class="small-link" href="${esc(url.href)}" target="_blank" rel="noreferrer">${esc(t(getLang(), 'open_source'))}</a>`;
    }
  } catch { /* show the sanitized source as text below */ }
  return `<code class="source-code">${esc(value)}</code>`;
}
function metric(label, value, basis, icon = '·') {
  return `<article class="metric-card"><div class="metric-label"><span>${esc(label)}</span><span class="metric-icon" aria-hidden="true">${esc(icon)}</span></div><div class="metric-value">${esc(value)}</div><div class="metric-basis">${esc(basis)}</div></article>`;
}
function notice(title, text, tone = '') {
  return `<div class="notice ${tone}"><span class="notice-icon" aria-hidden="true">${tone === 'error' ? '!' : 'i'}</span><div><strong>${esc(title)}</strong><p>${esc(text)}</p></div></div>`;
}
function pageIntro(kicker, title, copy, extra = '') {
  return `<div class="page-intro"><div><div class="page-kicker">${esc(kicker)}</div><h2>${esc(title)}</h2><p>${esc(copy)}</p></div>${extra}</div>`;
}
function emptyPanel(title, copy, icon = '∅') {
  return `<div class="panel-empty"><div><div class="empty-mark" aria-hidden="true">${esc(icon)}</div><strong>${esc(title)}</strong><p>${esc(copy)}</p></div></div>`;
}
function panel(title, description, content, className = '') {
  return `<section class="panel ${className}"><div class="panel-header"><div><h3>${esc(title)}</h3><p>${esc(description)}</p></div></div>${content}</section>`;
}
function assetCount(envelopeValue) {
  if (envelopeValue.record_count === null) return 'Chưa có dữ liệu';
  return integer(envelopeValue.record_count);
}
function queryParams() { return new URLSearchParams(window.location.search); }
function updateParams(values, { replace = false } = {}) {
  const params = queryParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined || value === '') params.delete(key);
    else params.set(key, String(value));
  }
  const url = `${window.location.pathname}?${params.toString()}${window.location.hash}`;
  if (replace) history.replaceState({}, '', url); else history.pushState({}, '', url);
  renderCurrent();
}
function tableHtml(headers, body, options = {}) {
  const { sort = '', direction = 'asc', className = '' } = options;
  const head = headers.map((header) => {
    const sorted = sort === header.key;
    const aria = sorted ? (direction === 'asc' ? 'ascending' : 'descending') : 'none';
    const arrow = sorted ? (direction === 'asc' ? ' ↑' : ' ↓') : '';
    const label = header.sortable === false ? esc(header.label) : `<button type="button" data-sort="${esc(header.key)}" aria-label="${esc(t(getLang(), 'sort_by', {label: header.label}))}">${esc(header.label)}${arrow}</button>`;
    return `<th class="${header.numeric ? 'numeric' : ''}" aria-sort="${aria}">${label}</th>`;
  }).join('');
  return `<div class="table-wrap ${className}"><table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${headers.length}" class="no-results">${esc(t(getLang(), 'no_matching_records'))}</td></tr>`}</tbody></table></div>`;
}
function paginationHtml(result) {
  const start = result.total === 0 ? 0 : (result.page - 1) * result.pageSize + 1;
  const end = Math.min(result.page * result.pageSize, result.total);
  const lang = getLang();
  return `<div class="pagination"><span>${esc(t(lang, 'pagination_showing', {start, end, total: integer(result.total)}))}</span><div class="pagination-actions"><button class="button" type="button" data-page="${result.page - 1}" ${result.page <= 1 ? 'disabled' : ''}>${esc(t(lang, 'pagination_prev'))}</button><span class="pagination-current">${esc(t(lang, 'pagination_page', {page: result.page, totalPages: result.totalPages}))}</span><button class="button" type="button" data-page="${result.page + 1}" ${result.page >= result.totalPages ? 'disabled' : ''}>${esc(t(lang, 'pagination_next'))}</button></div></div>`;
}
function applySortPage(rows, params, defaultSort, pageSize = 15, defaultDirection = 'asc') {
  const key = params.get('sort') || defaultSort;
  const direction = params.get('dir') || defaultDirection;
  const sorted = sortRows(rows, key, direction);
  const page = paginate(sorted, Number(params.get('page') || 1), pageSize);
  return { ...page, key, direction };
}
function currentPage() { return queryParams().get('view') || 'overview'; }
function setPageChrome(view) {
  const lang = getLang();
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach((element) => { element.textContent = t(lang, element.dataset.i18n); });
  document.querySelectorAll('[data-i18n-aria]').forEach((element) => { element.setAttribute('aria-label', t(lang, element.dataset.i18nAria)); });
  document.querySelectorAll('[data-i18n-title]').forEach((element) => { element.title = t(lang, element.dataset.i18nTitle); });
  document.querySelectorAll('[data-lang-switch]').forEach((element) => { element.setAttribute('aria-pressed', String(element.dataset.langSwitch === lang)); });
  const description = document.querySelector('meta[name="description"]');
  if (description) description.content = t(lang, 'meta_description');
  const title = t(lang, `nav_${PAGE_TITLES[view] ? view : 'overview'}`);
  document.title = `${title} — VN Fund Research`;
  document.getElementById('page-title').textContent = title;
  document.querySelectorAll('[data-view]').forEach((link) => {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

export function renderOverviewEnglish(data) {
  const funds = envelope(data, 'funds'), holdings = envelope(data, 'holdings'), events = envelope(data, 'portfolio_events');
  const nav = records(data, 'nav_history'), quality = envelope(data, 'data_quality_summary').summary || {}, pipeline = envelope(data, 'pipeline_status').summary || {};
  const snapshots = nav.filter(row => row.series_type === 'nav_snapshot' && row.date);
  const history = nav.filter(row => row.series_type === 'nav_history' && row.date);
  const observed = snapshots.map(row => row.date).sort().at(-1), sourceDate = history.map(row => row.date).sort().at(-1);
  const latest = observed || sourceDate;
  const basis = observed ? 'Observation date; effective date unknown' : sourceDate ? 'Source NAV date; publication time unknown' : 'No recorded NAV date';
  const fundCount = new Set(records(data, 'holdings').map(row => row.fund).filter(Boolean)).size;
  const stale = (quality.datasets || []).filter(item => item.freshness_assessment === 'stale');
  const failed = Object.values(data.datasets || {}).filter(item => ['unavailable','malformed'].includes(item.status));
  const banner = failed.length ? notice('Some static artifacts are unavailable', `${failed.map(item => item.dataset).join(', ')} could not be loaded or parsed.`, 'error') : !data.manifest?.source_coverage?.data_directory_exists ? notice('Data archive unavailable', 'data/ was missing at export; no illustrative values are generated.', 'warning') : stale.length ? notice('Datasets exceed freshness SLA', `${stale.map(item => `${item.dataset} (${item.age_days_at_reference}d > ${item.sla_days}d)`).join(', ')}. Source reporting may lag observation.`, 'warning') : notice('Interpret freshness carefully', 'Observation and effective dates remain separate. Publication time is not inferred.', '');
  const rows = (quality.datasets || []).slice(0,8).map(item => {const fresh = formatFreshness(item,'en'); return `<tr><td><span class="mono">${esc(item.dataset)}</span></td><td>${badge(statusText(item.status,'en'),statusTone(item.status))}</td><td class="numeric">${item.observed_rows == null ? 'Not available' : integer(item.observed_rows)}</td><td>${item.latest_date ? esc(dateText(item.latest_date)) : 'Not recorded'}</td><td>${badge(fresh.label,fresh.tone,fresh.detail)}</td></tr>`;}).join('');
  const table = rows ? tableHtml([{key:'dataset',label:'Dataset',sortable:false},{key:'status',label:'Status',sortable:false},{key:'observed_rows',label:'Rows read',numeric:true,sortable:false},{key:'latest_date',label:'Latest date',sortable:false},{key:'freshness_assessment',label:'Freshness',sortable:false}],rows) : emptyPanel('No inventory','Dataset metadata is not available.','⌁');
  const warningCount = envelope(data, 'data_quality_summary').record_count == null ? 'Not available' : integer((quality.issues || []).filter(issue => issue.severity === 'warning').length);
  const activity = pipeline.latest_source_request?.completed_at ? dateTimeText(pipeline.latest_source_request.completed_at) : 'Not recorded';
  return `${pageIntro('OVERVIEW','Fund research data','Explore saved data, provenance and limitations. This research tool does not rank funds or provide investment recommendations.')}${banner}<div class="freshness-strip"><div class="freshness-main"><span class="freshness-mark" aria-hidden="true">◷</span><div><strong>Data freshness</strong><small>${esc(basis)}</small></div></div><div class="freshness-date">${esc(latest ? dateText(latest) : 'Not available')}</div></div><div class="metric-grid">
  ${metric('Funds in catalogue', funds.record_count == null ? 'Not available' : integer(funds.record_count), 'Observed fund codes in funds.csv','▤')}
  ${metric('Funds with holdings snapshots', holdings.record_count == null ? 'Not available' : integer(fundCount), 'Latest saved snapshot; Top-N is not a complete portfolio','⌗')}
  ${metric('Latest NAV date', latest ? dateText(latest) : 'Not available', basis,'◷')}
  ${metric('Latest holdings snapshot rows', holdings.record_count == null ? 'Not available' : integer(holdings.record_count), 'Latest snapshot per fund and source scope','≡')}
  ${metric('Portfolio events in export', events.record_count == null ? 'Not available' : integer(events.record_count), events.status === 'missing' ? 'Changes require comparable snapshots and a persisted event journal; missing is not zero' : 'Observed or inferred changes, not confirmed orders','⇄')}
  ${metric('Quality warnings', warningCount, 'Inventory findings are not proof of pipeline failure','◎')}
  ${metric('Last verified workflow outcome', ['success','failure','cancelled','skipped'].includes(pipeline.overall_workflow_status) ? pipeline.overall_workflow_status : 'Unverified', pipeline.workflow_status_record?.status_scope === 'final_workflow' ? `GitHub update: ${dateTimeText(pipeline.workflow_status_record.status_updated_at)}` : `Latest request: ${activity}`,'⌁')}
  ${metric('Confirmed transactions','Not available','No transaction-level blotter in this project','—')}
  </div><div class="overview-grid">${panel('Dataset status','Inventory from existing files; missing does not mean zero.',table)}<div class="stacked-panels">${panel('Research shortcuts','Open a research view, not a trading signal.', '<div class="shortcut-list"><a href="?view=funds" data-route>Explore funds <span>→</span></a><a href="?view=holdings" data-route>Portfolio events <span>→</span></a><a href="?view=stocks" data-route>Stock research <span>→</span></a><a href="?view=quality" data-route>Data quality <span>→</span></a></div>')}${panel('Current limitations','What the dashboard cannot conclude.', '<ul class="compact-list"><li>Holdings changes do not prove transactions.</li><li>Provider and mirror data are not independently verified.</li><li>Workflow outcomes require sanitized status records.</li><li>Yuanta stock data is available only when a static snapshot has been synced.</li></ul>')}</div></div>`;
}

function renderOverview(data) {
  if (getLang() === 'en') return renderOverviewEnglish(data);
  const funds = envelope(data, 'funds');
  const holdings = envelope(data, 'holdings');
  const nav = records(data, 'nav_history');
  const events = envelope(data, 'portfolio_events');
  const quality = envelope(data, 'data_quality_summary').summary || {};
  const pipeline = envelope(data, 'pipeline_status').summary || {};
  const currentFundCount = new Set(records(data, 'holdings').map((row) => row.fund).filter(Boolean)).size;
  const snapshotRows = nav.filter((row) => row.series_type === 'nav_snapshot' && row.date);
  const latestObservation = snapshotRows.map((row) => row.date).sort().at(-1) || null;
  const sourceNavRows = nav.filter((row) => row.series_type === 'nav_history' && row.date);
  const latestNavSource = sourceNavRows.map((row) => row.date).sort().at(-1) || null;
  const latestNavDate = latestObservation || latestNavSource;
  const latestNavBasis = latestObservation ? 'Ngày quan sát; ngày hiệu lực chưa biết' : latestNavSource ? 'Ngày theo chuỗi nguồn; publication time chưa biết' : 'Ngày quan sát chưa được ghi nhận';
  const warnings = (quality.issues || []).filter((issue) => issue.severity === 'warning').length;
  const warningValue = envelope(data, 'data_quality_summary').record_count === null ? 'Chưa có dữ liệu' : integer(warnings);
  const tracked = funds.record_count;
  const statusNote = data.manifest?.source_coverage?.data_directory_exists
    ? 'Các con số dưới đây chỉ phản ánh tệp trong archive tại thời điểm export; không đại diện độ phủ live.'
    : 'Chưa có thư mục data/ trong repository. Không có dữ liệu sản xuất để tính metrics hoặc đưa ra kết quả đầu tư.';
  const freshness = latestNavDate
    ? `${dateText(latestNavDate)} · ${latestNavBasis}`
    : 'Chưa có quan sát NAV trong archive';
  const slaThresholds = quality.freshness_sla_thresholds_days || {};
  const hasSla = Object.keys(slaThresholds).length > 0;
  const staleDatasets = (quality.datasets || []).filter((item) => item.freshness_assessment === 'stale');
  const fetchFailures = Object.values(data.datasets || {}).filter((dataset) => ['unavailable', 'malformed'].includes(dataset.status));
  const overviewBanner = fetchFailures.length
    ? notice('Một số artifact tĩnh không khả dụng', `${fetchFailures.map((item) => item.dataset).join(', ')} không tải/parse được; các view liên quan có thể thiếu dữ liệu.`, 'error')
    : !data.manifest?.source_coverage?.data_directory_exists
      ? notice('Archive dữ liệu chưa có', 'data/ không tồn tại trong checkout tại lần export. Bảng điều khiển đang hiển thị trạng thái thiếu dữ liệu, không tạo số liệu minh họa.', 'warning')
      : staleDatasets.length
        ? notice('Phát hiện bộ dữ liệu vượt ngưỡng SLA độ mới', `${staleDatasets.map((d) => `${d.dataset} (${d.age_days_at_reference}d > ${d.sla_days}d)`).join(', ')}. Kỳ công bố nguồn có thể trễ hơn ngày collector quan sát.`, 'warning')
        : notice('Tính mới dữ liệu cần được diễn giải thận trọng', hasSla ? 'SLA độ mới đang bật theo cấu hình dự án; ngày quan sát và ngày hiệu lực vẫn tách riêng, không suy publication time từ effective date.' : 'Tuổi quan sát được hiển thị nhưng chưa có SLA để gắn nhãn stale. Ngày quan sát và ngày công bố được tách riêng; không suy publication time từ effective date.', '');
  const datasetRows = (quality.datasets || []).slice(0, 8).map((item) => {
    const fresh = formatFreshness(item, getLang());
    return `<tr><td><span class="mono">${esc(item.dataset)}</span></td><td>${badge(statusText(item.status), statusTone(item.status))}</td><td class="numeric">${item.observed_rows == null ? 'Chưa có dữ liệu' : integer(item.observed_rows)}</td><td>${item.latest_date ? dateText(item.latest_date) : 'Chưa ghi nhận'}</td><td>${badge(fresh.label, fresh.tone, fresh.detail)}</td></tr>`;
  }).join('');
  const datasetsPanel = panel('Tình trạng các bộ dữ liệu', 'Kiểm kê từ tệp thực tế; “thiếu” không đồng nghĩa với giá trị bằng 0.', datasetRows
    ? tableHtml([{ key: 'dataset', label: 'Dataset', sortable: false }, { key: 'status', label: 'Trạng thái', sortable: false }, { key: 'observed_rows', label: 'Số dòng đã đọc', numeric: true, sortable: false }, { key: 'latest_date', label: 'Ngày cuối', sortable: false }, { key: 'freshness_assessment', label: 'Freshness', sortable: false }], datasetRows)
    : emptyPanel('Chưa có inventory', 'Manifest không có metadata dataset.', '⌁'));
  const wfStatusMap = { success: 'Thành công', failure: 'Thất bại', partial: 'Một phần', cancelled: 'Đã hủy', skipped: 'Bỏ qua' };
  const pipelineStatus = wfStatusMap[pipeline.overall_workflow_status] || 'Chưa xác minh';
  const activity = pipeline.latest_source_request?.completed_at ? dateTimeText(pipeline.latest_source_request.completed_at) : 'Chưa ghi nhận request';
  const freshnessSubtitle = hasSla
    ? `${latestNavBasis} · ${staleDatasets.length ? `${integer(staleDatasets.length)} dataset vượt SLA` : 'các dataset có SLA đều trong ngưỡng'}`
    : `${latestNavBasis} · stale chưa được đánh giá vì chưa cấu hình SLA`;
  return `${pageIntro('TỔNG QUAN', 'Nền dữ liệu nghiên cứu quỹ', 'Theo dõi dữ liệu đã lưu, nguồn gốc và giới hạn diễn giải. Đây là công cụ nghiên cứu; không xếp hạng hay đưa khuyến nghị mua/bán.')}
    ${overviewBanner}
    <div class="freshness-strip"><div class="freshness-main"><span class="freshness-mark" aria-hidden="true">◷</span><div><strong>Độ mới của dữ liệu</strong><small>${esc(freshnessSubtitle)}</small></div></div><div class="freshness-date">${esc(latestNavDate ? dateText(latestNavDate) : 'Chưa có dữ liệu')}</div></div>
    <div class="metric-grid">
      ${metric('Quỹ có trong catalogue', tracked == null ? 'Chưa có dữ liệu' : integer(tracked), 'Căn cứ: funds.csv / mã quỹ thực sự quan sát', '▤')}
      ${metric('Quỹ có snapshot danh mục', holdings.record_count == null ? 'Chưa có dữ liệu' : integer(currentFundCount), 'Snapshot cuối đã lưu; Top-N không phải danh mục đầy đủ', '⌗')}
      ${metric('Ngày NAV quan sát gần nhất', latestNavDate ? dateText(latestNavDate) : 'Chưa có dữ liệu', latestNavBasis, '◷')}
      ${metric('Dòng holdings snapshot gần nhất', holdings.record_count == null ? 'Chưa có dữ liệu' : integer(holdings.record_count), 'Chỉ snapshot mới nhất theo fund/source scope', '≡')}
      ${metric('Portfolio events trong export', events.record_count == null ? 'Chưa có dữ liệu' : integer(events.record_count), events.status === 'missing' ? 'Cần snapshot so sánh và journal được lưu; thiếu không có nghĩa là không giao dịch' : 'Quan sát/suy luận; không phải nhật ký lệnh xác nhận', '⇄')}
      ${metric('Cảnh báo chất lượng', warningValue, 'Finding ghi nhận trong inventory; không đồng nghĩa pipeline thất bại', '◎')}
      ${metric('Kết quả workflow đã xác minh', pipelineStatus, pipeline.workflow_status_record?.status_scope === 'final_workflow' ? `GitHub cập nhật: ${dateTimeText(pipeline.workflow_status_record.status_updated_at)}` : `Request gần nhất: ${activity}`, '⌁')}
      ${metric('Giao dịch đã xác nhận', 'Chưa có dữ liệu', 'Không có transaction-level blotter trong project hiện tại', '—')}
    </div>
    <div class="overview-grid">
      ${datasetsPanel}
      <div class="stacked-panels">
        ${panel('Lối tắt nghiên cứu', 'Mở một view dữ liệu, không phải tín hiệu giao dịch.', `<div class="shortcut-list"><a href="?view=funds" data-route>Khám phá quỹ <span>→</span></a><a href="?view=holdings" data-route>Xem sự kiện danh mục <span>→</span></a><a href="?view=stocks" data-route>Nghiên cứu theo mã <span>→</span></a><a href="?view=quality" data-route>Kiểm tra chất lượng dữ liệu <span>→</span></a></div>`)}
        ${panel('Giới hạn hiện tại', 'Những gì dashboard không thể kết luận.', `<ul class="compact-list"><li>Dữ liệu công ty, định giá và thị trường chỉ có khi snapshot Yuanta được đồng bộ.</li><li>Thay đổi holdings không chứng minh giao dịch.</li><li>Fmarket/mirror chưa được xác minh độc lập.</li><li>Kết quả workflow được xác minh (nếu có) là lịch sử; không bảo đảm dữ liệu hiện tại.</li></ul>`)}
      </div>
    </div>`;
}

export function renderFundsEnglish(data, params) {
  const fundEnvelope = envelope(data, 'funds');
  const funds = records(data, 'funds');
  const q = params.get('q') || '', category = params.get('category') || '', status = params.get('status') || '';
  const filtered = filterFunds(funds, {query:q, category, dataStatus:status});
  const sorted = applySortPage(filtered, params, 'fund_id');
  const body = sorted.rows.map(fund => {
    const query = new URLSearchParams({view:'funds',fund:fund.fund_id,lang:'en'});
    return `<tr><td><a class="fund-link mono" href="?${esc(query)}" data-route>${esc(fund.fund_id)}</a><span class="table-sub">${esc(fund.name || 'Name unavailable')}</span></td><td>${esc(fund.management_company || 'Not available')}</td><td>${esc(categoryText(fund.asset_code,'en'))}</td><td class="numeric">${fund.latest_nav_observation == null ? '—' : number(fund.latest_nav_observation,0)}<span class="table-sub">${esc(fund.latest_nav_observation_date ? dateText(fund.latest_nav_observation_date) : 'NAV date unknown')}</span></td><td>${esc(fund.latest_holdings_date ? dateText(fund.latest_holdings_date) : 'No snapshot')}</td><td>${topNCoverageBadge(fund.holdings_coverage,'en')}</td></tr>`;
  }).join('');
  const categories = [...new Set(funds.map(row => row.asset_code).filter(Boolean))].sort(COLLATOR.compare);
  const filters = `<form class="control-bar" data-filter-form><div class="field grow"><label for="fund-query">Search funds</label><input id="fund-query" name="q" type="search" value="${esc(q)}" placeholder="Fund code, name or management company"></div><div class="field"><label for="fund-category">Asset category</label><select id="fund-category" name="category"><option value="">All categories</option>${categories.map(item => `<option value="${esc(item)}" ${item === category ? 'selected' : ''}>${esc(categoryText(item,'en'))}</option>`).join('')}</select></div><div class="field"><label for="fund-status">Coverage</label><select id="fund-status" name="status"><option value="">All statuses</option><option value="with-holdings" ${status === 'with-holdings' ? 'selected' : ''}>Holdings snapshot available</option><option value="without-holdings" ${status === 'without-holdings' ? 'selected' : ''}>No holdings snapshot</option><option value="partial" ${status === 'partial' ? 'selected' : ''}>Top-N / partial</option></select></div><button class="button primary" type="submit">Filter</button><button class="button" type="button" data-action="reset-filters">Reset</button><span class="toolbar-note">${esc(fundEnvelope.status === 'missing' ? 'Fund catalogue unavailable' : `${integer(filtered.length)} results`)}</span></form>`;
  const content = fundEnvelope.status === 'missing' && !funds.length ? emptyPanel('Fund data unavailable', 'funds.csv is absent from the archive. No example or fabricated funds are displayed.', '▤') : tableHtml([{key:'fund_id',label:'Fund / code'},{key:'management_company',label:'Management company'},{key:'asset_code',label:'Category'},{key:'latest_nav_observation',label:'Latest observed NAV',numeric:true},{key:'latest_holdings_date',label:'Snapshot date'},{key:'holdings_coverage',label:'Coverage / warnings',sortable:false}], body,{sort:sorted.key,direction:sorted.direction}) + paginationHtml(sorted);
  return `${pageIntro('FUND EXPLORER','Funds','Explore saved fund metadata. Source NAV history and observed snapshots have distinct date semantics; unknown publication times remain unknown.')}${datasetStatusNotice(fundEnvelope,'Fund catalogue')}${filters}${panel('Funds',`${funds.length} funds in this export · each value has its own date and basis.`,content)}${params.get('fund') ? renderFundDetailEnglish(data,params.get('fund')) : ''}`;
}

// Known source labels only; unknown provider-authored text remains unchanged.
const SOURCE_TERMS_EN = {
  'Cổ phiếu':'Equity', 'Trái phiếu':'Bonds', 'Tiền và tương đương tiền':'Cash and cash equivalents', 'Khác':'Other',
  'Bán buôn':'Wholesale', 'Bán lẻ':'Retail', 'Bảo hiểm':'Insurance', 'Bất động sản':'Real Estate',
  'Chăm sóc sức khỏe':'Health care', 'Chế biến thủy sản':'Seafood processing', 'Chứng khoán':'Securities',
  'Công nghệ và thông tin':'Information technology', 'Dịch vụ lưu trú, ăn uống, giải trí':'Hospitality, food and entertainment services',
  'Dịch vụ tư vấn, hỗ trợ':'Consulting and support services', 'Dịch vụ viễn thông':'Telecommunications services',
  'Khai khoáng':'Mining', 'Ngân hàng':'Banks', 'Nông - Lâm - Ngư':'Agriculture, forestry and fisheries',
  'Sản phẩm cao su':'Rubber products', 'Sản xuất':'Manufacturing', 'Sản xuất Hàng gia dụng':'Household goods manufacturing',
  'Sản xuất Nhựa - Hóa chất':'Plastics and chemicals manufacturing', 'Sản xuất Phụ trợ':'Supporting manufacturing',
  'Sản xuất Thiết bị, máy móc':'Machinery and equipment manufacturing', 'Thiết bị điện':'Electrical equipment',
  'Thực phẩm - Đồ uống':'Food and beverages', 'Tiện ích':'Utilities', 'Tài chính khác':'Other financial services',
  'Vận tải - Kho bãi':'Transport and warehousing', 'Vật liệu xây dựng':'Construction materials', 'Xây dựng':'Construction',
  'Cao':'High', 'Thấp':'Low', 'Thấp - Trung bình':'Low–medium', 'Trung bình':'Medium', 'Trung bình-Cao':'Medium–high',
  'Linh hoạt':'Flexible', 'MONEY':'MONEY', 'MONTH':'MONTH',
};
function sourceTermEn(value, fallback = 'Unclassified') { return SOURCE_TERMS_EN[value] || value || fallback; }

export function renderFundDetailEnglish(data, fundId) {
  const fund = records(data,'funds').find(item => item.fund_id === fundId);
  if (!fund) return `<section class="panel fund-detail-panel">${notice('Fund not found', `Fund ${fundId} is not present in this export.`, 'warning')}<a href="?view=funds&lang=en" data-route class="small-link">← Back to funds</a></section>`;
  const nav = records(data,'nav_history').filter(row => row.fund === fundId);
  const holdings = records(data,'holdings').filter(row => row.fund === fundId);
  const events = records(data,'portfolio_events').filter(row => row.fund === fundId).sort((a,b) => (b.observed_date || '').localeCompare(a.observed_date || '')).slice(0,12);
  const facts = summarizeFundFacts(records(data,'fund_facts'),fundId);
  const empty = 'Not available';
  const allocations = facts.allocation.length ? tableHtml([{key:'asset_name',label:'Asset class',sortable:false},{key:'pct',label:'Weight',numeric:true,sortable:false},{key:'report_date',label:'Report date',sortable:false}],facts.allocation.map(row => `<tr><td>${esc(sourceTermEn(row.asset_name, row.asset_code || 'Unclassified'))}</td><td class="numeric">${esc(percentFraction(row.pct))}</td><td>${esc(row.report_date ? dateText(row.report_date) : 'Unknown')}</td></tr>`).join('')) : emptyPanel('No asset allocation','No allocation.csv records for this fund.','◫');
  const industries = facts.industry.length ? tableHtml([{key:'industry',label:'Industry (source)',sortable:false},{key:'pct',label:'Weight',numeric:true,sortable:false},{key:'observed_date',label:'Observed date',sortable:false}],facts.industry.slice().sort((a,b) => (Number(b.pct)||0)-(Number(a.pct)||0)).map(row => `<tr><td>${esc(sourceTermEn(row.industry))}</td><td class="numeric">${esc(percentFraction(row.pct))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td></tr>`).join('')) : emptyPanel('No industry allocation','No industry.csv records for this fund.','▤');
  const fees = facts.fees.length ? tableHtml([{key:'type',label:'Type / programme',sortable:false},{key:'tier',label:'Tier / conditions',sortable:false},{key:'fee',label:'Published fee',numeric:true,sortable:false}],facts.fees.map(row => {
    const tier = row.begin != null || row.end != null ? `${row.begin != null ? integer(row.begin) : '—'} → ${row.end != null ? integer(row.end) : '∞'} ${row.unit || ''}`.trim() : 'Fixed / untiered';
    return `<tr><td><span class="mono">${esc(row.program ? `${row.type || 'UNKNOWN'} · ${sourceTermEn(row.program)}` : (row.type || 'UNKNOWN'))}</span></td><td>${esc(tier)}</td><td class="numeric">${esc(percentPoints(row.fee))}</td></tr>`;
  }).join('')) : emptyPanel('No published fees','No fees.csv records for this fund.','—');
  const performance = facts.performance.length ? `<div class="table-wrap"><table><thead><tr><th>Source-reported period</th><th>Value</th><th>Limitation</th></tr></thead><tbody>${facts.performance.flatMap(row => Object.entries(row).filter(([key,value]) => key.startsWith('ret_') && value != null).map(([key,value]) => `<tr><td>${esc(key.replaceAll('_',' ').toUpperCase())}</td><td>${esc(number(value,3))}</td><td>Source units / method not independently verified</td></tr>`)).join('') || '<tr><td colspan="3" class="no-results">No reported periods.</td></tr>'}</tbody></table></div>` : emptyPanel('Performance unavailable','No performance table in the archive; do not infer performance without appropriate NAV.','∅');
  const holdingBody = holdings.map(row => `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}&lang=en" data-route>${esc(row.ticker)}</a></td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td>${topNCoverageBadge(row.coverage_type,'en')}</td><td>${sourceLink(row.source_reference)}</td></tr>`).join('');
  const eventBody = events.map(row => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}&lang=en" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(row.display_label_en || eventTypeLabel(row.classification,'en'),'warning')}</td><td>${esc(row.flag || 'No flag')}</td></tr>`).join('');
  const profile = facts.profile;
  const info = `<dl class="detail-list"><div><dt>NAV observation date</dt><dd>${esc(fund.latest_nav_observation_date ? dateText(fund.latest_nav_observation_date) : empty)}</dd></div><div><dt>Observed NAV</dt><dd>${esc(fund.latest_nav_observation == null ? empty : number(fund.latest_nav_observation,2))}</dd></div><div><dt>Source NAV date</dt><dd>${esc(fund.latest_nav_source_date ? dateText(fund.latest_nav_source_date) : empty)}</dd></div><div><dt>Latest holdings snapshot</dt><dd>${esc(fund.latest_holdings_date ? dateText(fund.latest_holdings_date) : empty)}</dd></div><div><dt>Management fee (source)</dt><dd>${esc(profile?.management_fee_pct != null ? `${percentPoints(profile.management_fee_pct)} / year` : empty)}</dd></div><div><dt>First issue date</dt><dd>${esc(profile?.first_issue_date ? dateText(profile.first_issue_date) : empty)}</dd></div><div><dt>Risk level (source)</dt><dd>${esc(sourceTermEn(profile?.risk_level, empty))}</dd></div><div><dt>Publication time</dt><dd>No verified data</dd></div></dl>`;
  return `<section class="fund-detail-panel" id="fund-detail"><div class="detail-heading"><a class="back-link" href="?view=funds&lang=en" data-route>← Fund list</a><span class="fund-avatar" aria-hidden="true">${esc(fund.fund_id.slice(0,3))}</span><div><h2>${esc(fund.name || fund.fund_id)}</h2><p><span class="mono">${esc(fund.fund_id)}</span> · ${esc(fund.management_company || 'Management company unavailable')} · ${esc(categoryText(fund.asset_code,'en'))}</p></div></div>
    ${fund.holdings_coverage ? notice('Holdings coverage is limited',fund.holdings_coverage === 'top_n_partial_censored' ? 'Only Top-N holdings are disclosed; undisclosed positions are unknown, not proof of full sale.' : 'The source claims a complete portfolio, but it has not been independently verified.','warning') : ''}
    <div class="detail-grid">${panel('NAV over time','Source NAV and observation snapshots are shown separately; gaps are not interpolated.',renderNavChart(nav,'en'))}${panel('Metadata and source profile','Observation dates do not establish publication or effective dates; fees are as reported.',info)}</div>
    <div class="section-heading"><div><h3>Exported holdings snapshot</h3><p>Only the latest snapshot per source scope; missing weights remain unknown.</p></div></div>${holdings.length ? tableHtml([{key:'ticker',label:'Ticker',sortable:false},{key:'weight',label:'Weight',numeric:true,sortable:false},{key:'volume',label:'Volume',numeric:true,sortable:false},{key:'observed_date',label:'Snapshot date',sortable:false},{key:'coverage_type',label:'Coverage',sortable:false},{key:'source_reference',label:'Source',sortable:false}],holdingBody) : emptyPanel('No holdings snapshot','No holdings records for this fund in the export.','≡')}
    <div class="detail-grid detail-subgrid">${panel('Asset allocation','Report period and portfolio weights as stored.',allocations)}${panel('Industry allocation (source)','Only industries disclosed by Fmarket in the latest snapshot; unrecognized source terms remain in their original language.',industries)}</div><div class="detail-grid detail-subgrid">${panel('Transaction / management fees (source)','Fee points as recorded in fees.csv; consult the prospectus before trading.',fees)}${panel('Source-reported performance','Only supplied fields are shown; do not compare without common periods or known units.',performance)}</div>
    <div class="section-heading"><div><h3>Recent portfolio events</h3><p>Observed / inferred events are not confirmed trades.</p></div><a class="small-link" href="?view=holdings&fund=${encodeURIComponent(fundId)}&lang=en" data-route>View all →</a></div>${events.length ? tableHtml([{key:'observed_date',label:'Date',sortable:false},{key:'ticker',label:'Ticker',sortable:false},{key:'display_label',label:'Classification',sortable:false},{key:'flag',label:'Flag',sortable:false}],eventBody) : emptyPanel('No event journal','No recorded portfolio changes for this fund.','⇄')}</section>`;
}

function renderFunds(data, params) {
  if (getLang(params) === 'en') return renderFundsEnglish(data, params);
  const fundEnvelope = envelope(data, 'funds');
  const fundRows = records(data, 'funds');
  const q = params.get('q') || '';
  const category = params.get('category') || '';
  const dataStatus = params.get('status') || '';
  const filtered = filterFunds(fundRows, { query: q, category, dataStatus });
  const sorted = applySortPage(filtered, params, 'fund_id');
  const body = sorted.rows.map((fund) => {
    const query = new URLSearchParams({ view: 'funds', fund: fund.fund_id });
    return `<tr><td><a class="fund-link mono" href="?${esc(query.toString())}" data-route>${esc(fund.fund_id)}</a><span class="table-sub">${esc(fund.name || 'Chưa có tên')}</span></td><td>${esc(fund.management_company || 'Chưa có dữ liệu')}</td><td>${esc(categoryText(fund.asset_code))}</td><td class="numeric">${fund.latest_nav_observation == null ? '—' : number(fund.latest_nav_observation, 0)}<span class="table-sub">${esc(fund.latest_nav_observation_date ? dateText(fund.latest_nav_observation_date) : 'Ngày NAV chưa biết')}</span></td><td>${fund.latest_holdings_date ? dateText(fund.latest_holdings_date) : 'Chưa có snapshot'}</td><td>${topNCoverageBadge(fund.holdings_coverage)}</td></tr>`;
  }).join('');
  const categories = [...new Set(fundRows.map((row) => row.asset_code).filter(Boolean))].sort(COLLATOR.compare);
  const filterBar = `<form class="control-bar" data-filter-form><div class="field grow"><label for="fund-query">Tìm quỹ</label><input id="fund-query" name="q" type="search" value="${esc(q)}" placeholder="Mã quỹ, tên quỹ hoặc công ty quản lý"></div><div class="field"><label for="fund-category">Nhóm tài sản</label><select id="fund-category" name="category"><option value="">Tất cả nhóm</option>${categories.map((item) => `<option value="${esc(item)}" ${item === category ? 'selected' : ''}>${esc(categoryText(item))}</option>`).join('')}</select></div><div class="field"><label for="fund-status">Độ phủ</label><select id="fund-status" name="status"><option value="">Tất cả trạng thái</option><option value="with-holdings" ${dataStatus === 'with-holdings' ? 'selected' : ''}>Có snapshot danh mục</option><option value="without-holdings" ${dataStatus === 'without-holdings' ? 'selected' : ''}>Chưa có snapshot</option><option value="partial" ${dataStatus === 'partial' ? 'selected' : ''}>Top-N / một phần</option></select></div><button class="button primary" type="submit">Lọc</button><button class="button" type="button" data-action="reset-filters">Đặt lại</button><span class="toolbar-note">${esc(fundEnvelope.status === 'missing' ? 'Nguồn catalogue chưa có' : `${integer(filtered.length)} kết quả`)}</span></form>`;
  const content = fundEnvelope.status === 'missing' && !fundRows.length
    ? emptyPanel('Chưa có dữ liệu quỹ', 'Catalogue funds.csv chưa có trong archive. Không hiển thị quỹ mẫu hoặc số liệu giả.', '▤')
    : tableHtml([{ key: 'fund_id', label: 'Quỹ / mã' }, { key: 'management_company', label: 'Công ty quản lý' },
        { key: 'asset_code', label: 'Nhóm' }, { key: 'latest_nav_observation', label: 'NAV gần nhất', numeric: true },
        { key: 'latest_holdings_date', label: 'Ngày snapshot' }, { key: 'holdings_coverage', label: 'Độ phủ / cảnh báo', sortable: false }],
        body, { sort: sorted.key, direction: sorted.direction }) + paginationHtml(sorted);
  return `${pageIntro('FUND EXPLORER', 'Quỹ đầu tư', 'Tìm và so sánh metadata quỹ đã lưu. NAV history và holdings được tách theo semantics; publication time chưa biết sẽ được giữ là chưa biết.')}${datasetStatusNotice(fundEnvelope, 'Catalogue quỹ')}${filterBar}${panel('Danh sách quỹ', `${fundRows.length ? `${fundRows.length} quỹ trong export` : 'Chưa có quỹ trong export'} · mỗi giá trị có ngày/basis riêng.`, content)}${params.get('fund') ? renderFundDetail(data, params.get('fund')) : ''}`;
}

function renderFundDetail(data, fundId) {
  const fund = records(data, 'funds').find((item) => item.fund_id === fundId);
  if (!fund) return `<section class="panel fund-detail-panel">${notice('Không tìm thấy quỹ', `Mã ${fundId} không có trong export hiện tại.`, 'warning')}<a href="?view=funds" data-route class="small-link">← Trở lại danh sách quỹ</a></section>`;
  const nav = records(data, 'nav_history').filter((row) => row.fund === fundId);
  const holdings = records(data, 'holdings').filter((row) => row.fund === fundId);
  const events = records(data, 'portfolio_events').filter((row) => row.fund === fundId).sort((a, b) => (b.observed_date || '').localeCompare(a.observed_date || '')).slice(0, 12);
  const factGroup = summarizeFundFacts(records(data, 'fund_facts'), fundId);
  const allocations = factGroup.allocation;
  const industries = factGroup.industry;
  const fees = factGroup.fees;
  const profile = factGroup.profile;
  const performance = factGroup.performance;
  const allocationContent = allocations.length ? tableHtml([
    { key: 'asset_name', label: 'Loại tài sản', sortable: false }, { key: 'pct', label: 'Tỷ trọng', numeric: true, sortable: false }, { key: 'report_date', label: 'Ngày báo cáo', sortable: false },
  ], allocations.map((row) => `<tr><td>${esc(row.asset_name || row.asset_code || 'Chưa phân loại')}</td><td class="numeric">${esc(percentFraction(row.pct))}</td><td>${esc(row.report_date ? dateText(row.report_date) : 'Chưa biết')}</td></tr>`).join('')) : emptyPanel('Chưa có cơ cấu tài sản', 'Không tìm thấy allocation.csv cho quỹ này.', '◫');
  const industryContent = industries.length ? tableHtml([
    { key: 'industry', label: 'Ngành (theo nguồn)', sortable: false }, { key: 'pct', label: 'Tỷ trọng', numeric: true, sortable: false }, { key: 'observed_date', label: 'Ngày quan sát', sortable: false },
  ], industries.slice().sort((a, b) => (Number(b.pct) || 0) - (Number(a.pct) || 0)).map((row) => `<tr><td>${esc(row.industry || 'Chưa phân loại')}</td><td class="numeric">${esc(percentFraction(row.pct))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td></tr>`).join('')) : emptyPanel('Chưa có phân bổ ngành', 'Không có dòng industry.csv cho quỹ này.', '▤');
  const feeContent = fees.length ? tableHtml([
    { key: 'type', label: 'Loại / chương trình', sortable: false }, { key: 'tier', label: 'Bậc / điều kiện', sortable: false }, { key: 'fee', label: 'Mức phí công bố', numeric: true, sortable: false },
  ], fees.map((row) => {
    const tier = row.begin != null || row.end != null
      ? `${row.begin != null ? integer(row.begin) : '0'} → ${row.end != null ? integer(row.end) : '∞'} ${row.unit || ''}`.trim()
      : 'Cố định / không phân bậc';
    const typeLabel = row.program ? `${row.type || 'UNKNOWN'} · ${row.program}` : (row.type || 'UNKNOWN');
    return `<tr><td><span class="mono">${esc(typeLabel)}</span></td><td>${esc(tier)}</td><td class="numeric">${esc(percentPoints(row.fee))}</td></tr>`;
  }).join('')) : emptyPanel('Chưa có biểu phí', 'Không có dòng fees.csv cho quỹ này.', '—');
  const holdingBody = holdings.map((row) => `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}" data-route>${esc(row.ticker)}</a></td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${topNCoverageBadge(row.coverage_type)}</td><td>${sourceLink(row.source_reference)}</td></tr>`).join('');
  const eventBody = events.map((row) => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(getLang() === 'en' ? (row.display_label_en || eventTypeLabel(row.classification, 'en')) : (row.display_label || row.action), 'warning')}</td><td>${esc(row.flag || 'Không có cờ')}</td></tr>`).join('');
  const performanceContent = performance.length ? `<div class="table-wrap"><table><thead><tr><th>Kỳ do nguồn báo cáo</th><th>Giá trị</th><th>Giới hạn</th></tr></thead><tbody>${performance.flatMap((row) => Object.entries(row).filter(([key, value]) => key.startsWith('ret_') && value != null).map(([key, value]) => `<tr><td>${esc(key.replaceAll('_', ' ').toUpperCase())}</td><td>${esc(number(value, 3))}</td><td>Đơn vị / phương pháp nguồn chưa độc lập xác minh</td></tr>`)).join('') || '<tr><td colspan="3" class="no-results">Không có kỳ hiệu suất.</td></tr>'}</tbody></table></div>` : emptyPanel('Hiệu suất chưa khả dụng', 'Không có bảng performance trong archive; không suy hiệu suất khi thiếu NAV phù hợp.', '∅');
  const mgmtFeeText = profile?.management_fee_pct != null ? `${percentPoints(profile.management_fee_pct)} / năm` : 'Chưa có dữ liệu';
  const firstIssueText = profile?.first_issue_date ? dateText(profile.first_issue_date) : 'Chưa có dữ liệu';
  const riskText = profile?.risk_level || 'Chưa có dữ liệu';
  return `<section class="fund-detail-panel" id="fund-detail"><div class="detail-heading"><a class="back-link" href="?view=funds" data-route>← Danh sách quỹ</a><span class="fund-avatar" aria-hidden="true">${esc(fund.fund_id.slice(0, 3))}</span><div><h2>${esc(fund.name || fund.fund_id)}</h2><p><span class="mono">${esc(fund.fund_id)}</span> · ${esc(fund.management_company || 'Công ty quản lý chưa có dữ liệu')} · ${esc(categoryText(fund.asset_code))}</p></div></div>
    ${fund.holdings_coverage ? notice('Độ phủ holdings có giới hạn', fund.holdings_coverage === 'top_n_partial_censored' ? 'Nguồn chỉ lưu top-N; mã ngoài top-N là chưa biết, không thể hiểu là đã bán hết.' : 'Nguồn khai báo danh mục đầy đủ, nhưng chưa được xác minh độc lập.', 'warning') : ''}
    <div class="detail-grid">
      ${panel('NAV / CCQ theo thời gian', 'Chuỗi NAV nguồn và snapshot quan sát được vẽ riêng; không nội suy qua khoảng trống.', renderNavChart(nav))}
      ${panel('Metadata & hồ sơ công bố', 'Ngày quan sát không đồng nghĩa ngày công bố hoặc hiệu lực; phí quản lý do nguồn báo cáo.', `<dl class="detail-list"><div><dt>Ngày NAV quan sát</dt><dd>${esc(fund.latest_nav_observation_date ? dateText(fund.latest_nav_observation_date) : 'Chưa có dữ liệu')}</dd></div><div><dt>NAV quan sát</dt><dd>${esc(fund.latest_nav_observation == null ? 'Chưa có dữ liệu' : number(fund.latest_nav_observation, 2))}</dd></div><div><dt>Ngày NAV nguồn</dt><dd>${esc(fund.latest_nav_source_date ? dateText(fund.latest_nav_source_date) : 'Chưa có dữ liệu')}</dd></div><div><dt>Snapshot holdings cuối</dt><dd>${esc(fund.latest_holdings_date ? dateText(fund.latest_holdings_date) : 'Chưa có dữ liệu')}</dd></div><div><dt>Phí quản lý (nguồn)</dt><dd>${esc(mgmtFeeText)}</dd></div><div><dt>Ngày phát hành đầu</dt><dd>${esc(firstIssueText)}</dd></div><div><dt>Mức rủi ro (nguồn)</dt><dd>${esc(riskText)}</dd></div><div><dt>Publication time</dt><dd>Chưa có dữ liệu xác minh</dd></div></dl>`)}
    </div>
    <div class="section-heading"><div><h3>Snapshot danh mục được export</h3><p>Chỉ snapshot mới nhất của từng source scope; trọng số thiếu được giữ là chưa biết.</p></div></div>
    ${holdings.length ? tableHtml([{ key: 'ticker', label: 'Mã', sortable: false }, { key: 'weight', label: 'Tỷ trọng', numeric: true, sortable: false }, { key: 'volume', label: 'Khối lượng', numeric: true, sortable: false }, { key: 'observed_date', label: 'Ngày snapshot', sortable: false }, { key: 'coverage_type', label: 'Độ phủ', sortable: false }, { key: 'source_reference', label: 'Nguồn', sortable: false }], holdingBody) : emptyPanel('Chưa có snapshot holdings', 'Không có bản ghi holdings cho quỹ trong export hiện tại.', '≡')}
    <div class="detail-grid detail-subgrid">${panel('Cơ cấu tài sản', 'Kỳ báo cáo theo dữ liệu đang lưu (tỷ trọng dạng phần trăm danh mục).', allocationContent)}${panel('Phân bổ ngành (nguồn công bố)', 'Chỉ hiển thị các ngành nguồn Fmarket báo cáo trong snapshot mới nhất.', industryContent)}</div>
    <div class="detail-grid detail-subgrid">${panel('Biểu phí giao dịch / quản lý (nguồn công bố)', 'Đơn vị điểm phần trăm theo biểu phí lưu trong fees.csv; kiểm tra bản cáo bạch trước khi giao dịch.', feeContent)}${panel('Hiệu suất nguồn báo cáo', 'Chỉ hiển thị trường có sẵn; không so sánh nếu thiếu kỳ chung hoặc đơn vị chưa rõ.', performanceContent)}</div>
    <div class="section-heading"><div><h3>Sự kiện danh mục gần đây</h3><p>Sự kiện suy luận/quan sát không phải giao dịch được xác nhận.</p></div><a class="small-link" href="?view=holdings&fund=${encodeURIComponent(fundId)}" data-route>Xem tất cả →</a></div>
    ${events.length ? tableHtml([{ key: 'observed_date', label: 'Ngày', sortable: false }, { key: 'ticker', label: 'Mã', sortable: false }, { key: 'display_label', label: 'Phân loại', sortable: false }, { key: 'flag', label: 'Cờ', sortable: false }], eventBody) : emptyPanel('Chưa có event journal', 'Không có dòng thay đổi danh mục được ghi nhận cho quỹ.', '⇄')}</section>`;
}

function renderNavChart(rows, lang = getLang()) {
  const history = rows.filter((row) => row.series_type === 'nav_history' && Number.isFinite(row.nav) && row.nav > 0).sort((a, b) => a.date.localeCompare(b.date));
  const snapshots = rows.filter((row) => row.series_type === 'nav_snapshot' && Number.isFinite(row.nav) && row.nav > 0).sort((a, b) => a.date.localeCompare(b.date));
  if (!history.length && !snapshots.length) return `<div class="chart-empty">${lang === 'en' ? 'No NAV to plot.<br>No example series is generated.' : 'Chưa có NAV để vẽ.<br>Không tạo chuỗi minh họa.'}</div>`;
  const all = [...history, ...snapshots];
  const values = all.map((row) => row.nav);
  let min = Math.min(...values); let max = Math.max(...values);
  if (min === max) { min *= .995; max *= 1.005; }
  const width = 760; const height = 200; const pad = { left: 48, right: 12, top: 12, bottom: 30 };
  const plotW = width - pad.left - pad.right; const plotH = height - pad.top - pad.bottom;
  const dates = [...new Set(all.map((row) => row.date))].sort();
  const xIndex = new Map(dates.map((date, index) => [date, index]));
  const x = (date) => pad.left + (dates.length <= 1 ? plotW / 2 : xIndex.get(date) / (dates.length - 1) * plotW);
  const y = (value) => pad.top + (max - value) / (max - min) * plotH;
  const pathFor = (series) => series.map((row, index) => `${index ? 'L' : 'M'}${x(row.date).toFixed(1)},${y(row.nav).toFixed(1)}`).join(' ');
  const grid = [0, .5, 1].map((fraction) => {
    const yy = pad.top + fraction * plotH; const value = max - fraction * (max - min);
    return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${yy}" y2="${yy}" stroke="#e5eaed" stroke-width="1"/><text x="${pad.left - 7}" y="${yy + 3}" text-anchor="end" fill="#8a989f" font-size="9">${esc(number(value, 0))}</text>`;
  }).join('');
  const histPath = history.length > 1 ? `<path d="${pathFor(history)}" fill="none" stroke="#247f7a" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>` : '';
  const histPoints = history.map((row) => `<circle cx="${x(row.date)}" cy="${y(row.nav)}" r="2.6" fill="#247f7a"><title>${esc(row.date)} · ${esc(number(row.nav, 2))} · ${lang === 'en' ? 'source date; publication time unknown' : 'ngày nguồn, publication time chưa biết'}</title></circle>`).join('');
  const snapshotPath = snapshots.length > 1 ? `<path d="${pathFor(snapshots)}" fill="none" stroke="#8698a3" stroke-width="1.6" stroke-dasharray="5 4"/>` : '';
  const snapshotPoints = snapshots.map((row) => `<circle cx="${x(row.date)}" cy="${y(row.nav)}" r="3" fill="#fff" stroke="#748994" stroke-width="1.5"><title>${esc(row.date)} · ${esc(number(row.nav, 2))} · ${lang === 'en' ? 'observation date; effective date unknown' : 'ngày quan sát; ngày hiệu lực chưa biết'}</title></circle>`).join('');
  const firstDate = dates[0]; const lastDate = dates.at(-1);
  return `<div class="chart-box"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${lang === 'en' ? `NAV chart: ${history.length} source points and ${snapshots.length} observed snapshot points; series remain separate` : `Biểu đồ NAV gồm ${history.length} điểm chuỗi nguồn và ${snapshots.length} điểm snapshot quan sát; các chuỗi giữ riêng semantics`}">${grid}${histPath}${histPoints}${snapshotPath}${snapshotPoints}<text x="${pad.left}" y="${height - 5}" fill="#7f8e97" font-size="9">${esc(firstDate)}</text><text x="${width - pad.right}" y="${height - 5}" text-anchor="end" fill="#7f8e97" font-size="9">${esc(lastDate)}</text></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>${lang === 'en' ? 'Source NAV dates · publication time unknown' : 'NAV theo ngày nguồn · publication time chưa biết'}</span><span class="legend-item"><span class="legend-line snapshot"></span>${lang === 'en' ? 'Observed snapshots · effective date unknown' : 'Snapshot ngày quan sát · effective date chưa biết'}</span></div><p class="chart-caption">${lang === 'en' ? 'Points are from the archive, not a complete daily series. Gaps are not interpolated; NAV change is not total return.' : 'Các điểm là dữ liệu thực sự có trong archive, không phải chuỗi daily đầy đủ. Khoảng trống không được nội suy; NAV change không đồng nghĩa total return.'}</p></div>`;
}

export function renderHoldingsEnglish(data, params) {
  const mode = params.get('kind') === 'holdings' ? 'holdings' : 'events';
  const funds = records(data,'funds'), allEvents = records(data,'portfolio_events'), allHoldings = records(data,'holdings');
  const fund = params.get('fund') || '', ticker = params.get('ticker') || '', classification = params.get('classification') || '', evidence = params.get('evidence') || '', from = params.get('from') || '', to = params.get('to') || '';
  const source = mode === 'events' ? allEvents : allHoldings;
  const filtered = mode === 'events' ? filterEvents(source,{fund,ticker,classification,from,to,evidence}) : source.filter(row => (!fund || row.fund === fund) && (!ticker || (row.ticker || '').toUpperCase().includes(ticker.toUpperCase())) && (!classification || row.asset_class === classification) && (!from || (row.observed_date && row.observed_date >= from)) && (!to || (row.observed_date && row.observed_date <= to)) && (!evidence || row.coverage_type === evidence));
  const sorted = applySortPage(filtered,params,mode === 'events' ? 'observed_date' : 'fund',15,mode === 'events' ? 'desc' : 'asc');
  const body = mode === 'events' ? sorted.rows.map(row => {
    const detailParams = new URLSearchParams(params); detailParams.set('view','holdings'); detailParams.set('kind','events'); detailParams.set('event_id',row.event_id || ''); detailParams.set('lang','en');
    const href = `?${detailParams}`;
    const evidenceBadge = row.censored ? badge('Top-N','warning','Entering or leaving Top-N does not prove a new position or complete exit') : badge(row.evidence_status === 'inferred_not_transaction' ? 'Inferred' : 'Not confirmed','neutral');
    return `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund || '')}&lang=en" data-route>${esc(row.fund || '—')}</a></td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}&lang=en" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(row.display_label_en || eventTypeLabel(row.classification,'en'),row.evidence_status === 'unconfirmed' ? 'warning' : 'neutral')}<span class="table-sub mono">${esc(row.action || '—')}</span></td><td>${esc(row.basis || 'Unknown')}</td><td class="numeric">${esc(signedPercentFraction(row.weight_delta,2,'en'))}</td><td class="numeric">${esc(row.volume_change_pct == null ? '—' : signedPercentPoints(row.volume_change_pct*100,2,'en'))}</td><td>${esc(row.flag || 'No flag')}</td><td>${evidenceBadge}</td><td><a class="small-link" href="${esc(href)}" data-route>Details</a></td></tr>`;
  }).join('') : sorted.rows.map(row => `<tr><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund || '')}&lang=en" data-route>${esc(row.fund || '—')}</a></td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}&lang=en" data-route>${esc(row.ticker || '—')}</a></td><td>${esc(categoryText(row.asset_class,'en'))}</td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td>${topNCoverageBadge(row.coverage_type,'en')}</td><td>${esc(row.effective_date ? dateText(row.effective_date) : 'Unknown')}</td></tr>`).join('');
  const classes = mode === 'events' ? [...new Set(allEvents.map(row => row.classification).filter(Boolean))].sort(COLLATOR.compare) : [...new Set(allHoldings.map(row => row.asset_class).filter(Boolean))];
  const classOptions = classes.map(value => `<option value="${esc(value)}" ${classification === value ? 'selected' : ''}>${esc(mode === 'events' ? eventTypeLabel(value,'en') : categoryText(value,'en'))}</option>`).join('');
  const evidenceList = mode === 'events' ? [['inferred_not_transaction','Inferred · not a confirmed trade'],['unconfirmed','Unconfirmed'],['observed_top_n_membership','Entered / exited Top-N · censored'],['heuristic','Heuristic'],['suspected','Suspected / unverified'],['unknown','Unclassified']] : [['top_n_partial_censored','Top-N · censored'],['source_claimed_full_not_independently_verified','Source-claimed full · unverified']];
  const evidenceOptions = `<option value="">${mode === 'events' ? 'All evidence statuses' : 'All coverage'}</option>${evidenceList.map(([value,label]) => `<option value="${value}" ${evidence === value ? 'selected' : ''}>${label}</option>`).join('')}`;
  const filters = `<form class="control-bar" data-filter-form><input type="hidden" name="kind" value="${mode}"><div class="field"><label for="event-fund">Fund</label><select id="event-fund" name="fund"><option value="">All funds</option>${funds.map(row => `<option value="${esc(row.fund_id)}" ${row.fund_id === fund ? 'selected' : ''}>${esc(row.fund_id)} · ${esc(row.name)}</option>`).join('')}</select></div><div class="field"><label for="event-ticker">Ticker</label><input id="event-ticker" name="ticker" type="search" value="${esc(ticker)}" placeholder="e.g. FPT"></div><div class="field"><label for="event-class">${mode === 'events' ? 'Classification' : 'Asset class'}</label><select id="event-class" name="classification"><option value="">All</option>${classOptions}</select></div><div class="field"><label for="event-evidence">Evidence / coverage</label><select id="event-evidence" name="evidence">${evidenceOptions}</select></div><div class="field"><label for="event-from">From date</label><input id="event-from" name="from" type="date" value="${esc(from)}"></div><div class="field"><label for="event-to">To date</label><input id="event-to" name="to" type="date" value="${esc(to)}"></div><button class="button primary" type="submit">Filter</button><button class="button" type="button" data-action="reset-filters">Reset</button></form>`;
  const headers = mode === 'events' ? [{key:'observed_date',label:'Observation date'},{key:'fund',label:'Fund'},{key:'ticker',label:'Ticker'},{key:'display_label',label:'Event'},{key:'basis',label:'Basis'},{key:'weight_delta',label:'Weight Δ',numeric:true},{key:'volume_change_pct',label:'Volume Δ',numeric:true},{key:'flag',label:'Flag'},{key:'evidence_status',label:'Status'},{key:'event_id',label:'Details',sortable:false}] : [{key:'fund',label:'Fund'},{key:'ticker',label:'Ticker'},{key:'asset_class',label:'Category'},{key:'weight',label:'Weight',numeric:true},{key:'volume',label:'Volume',numeric:true},{key:'observed_date',label:'Observation date'},{key:'coverage_type',label:'Coverage'},{key:'effective_date',label:'Source effective date'}];
  const table = source.length ? tableHtml(headers,body,{sort:sorted.key,direction:sorted.direction}) + paginationHtml(sorted) : emptyPanel(mode === 'events' ? 'No portfolio events' : 'No holdings snapshots',mode === 'events' ? 'Events require comparable snapshots and a persisted event journal. A missing journal does not establish that no trades occurred.' : 'The export does not have data to build this table. Missing positions are not zero.','∅');
  const selected = params.get('event_id');
  const detail = selected && mode === 'events' ? renderEventDetail(allEvents.find(event => event.event_id === selected),'en', params) : '';
  return `${pageIntro('HOLDINGS & PORTFOLIO CHANGES','Holdings & changes','Reported snapshots are distinct from inferred events. Missing data, Top-N exits and weight changes are not confirmed trades.')}${datasetStatusNotice(envelope(data,mode === 'events' ? 'portfolio_events' : 'holdings'),mode === 'events' ? 'Portfolio event journal' : 'Holdings snapshots')}
    <div class="tab-strip"><a href="?view=holdings&kind=events&lang=en" data-route class="${mode === 'events' ? 'active' : ''}" ${mode === 'events' ? 'aria-current="page"' : ''}>Portfolio events <span>${allEvents.length ? integer(allEvents.length) : '—'}</span></a><a href="?view=holdings&kind=holdings&lang=en" data-route class="${mode === 'holdings' ? 'active' : ''}" ${mode === 'holdings' ? 'aria-current="page"' : ''}>Holdings snapshots <span>${allHoldings.length ? integer(allHoldings.length) : '—'}</span></a></div>
    ${mode === 'events' ? notice('Not a trade blotter','BUY/SELL are volume-based inferences using a median fund-scale proxy, not confirmed trades. UNCONFIRMED? and WEIGHT_CHANGE? remain unconfirmed. Entering or leaving Top-N does not prove a new position or complete exit.','warning') : ''}
    ${filters}${panel(mode === 'events' ? 'Observed / inferred event journal' : 'Latest holdings snapshots',mode === 'events' ? 'Original action codes are retained alongside neutral explanations.' : 'Only the latest snapshot per fund / source is exported; coverage is labelled.',table)}${detail}`;
}

function renderHoldings(data, params) {
  if (getLang(params) === 'en') return renderHoldingsEnglish(data, params);
  const mode = params.get('kind') === 'holdings' ? 'holdings' : 'events';
  const funds = records(data, 'funds');
  const allEvents = records(data, 'portfolio_events');
  const allHoldings = records(data, 'holdings');
  const fundFilter = params.get('fund') || '';
  const tickerFilter = params.get('ticker') || '';
  const classification = params.get('classification') || '';
  const evidence = params.get('evidence') || '';
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const source = mode === 'events' ? allEvents : allHoldings;
  let filtered;
  if (mode === 'events') {
    filtered = filterEvents(source, { fund: fundFilter, ticker: tickerFilter, classification, from, to, evidence });
  } else {
    const term = tickerFilter.toUpperCase();
    filtered = source.filter((row) => (!fundFilter || row.fund === fundFilter)
      && (!term || row.ticker.includes(term))
      && (!classification || row.asset_class === classification)
      && (!from || (row.observed_date && row.observed_date >= from))
      && (!to || (row.observed_date && row.observed_date <= to))
      && (!evidence || row.coverage_type === evidence));
  }
  const sorted = applySortPage(filtered, params, mode === 'events' ? 'observed_date' : 'fund', 15, mode === 'events' ? 'desc' : 'asc');
  const body = mode === 'events' ? sorted.rows.map((row) => {
    const href = `?view=holdings&fund=${encodeURIComponent(row.fund || '')}&event_id=${encodeURIComponent(row.event_id)}`;
    return `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund || '')}" data-route>${esc(row.fund || '—')}</a></td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(getLang() === 'en' ? (row.display_label_en || eventTypeLabel(row.classification, 'en')) : (row.display_label || row.action), row.evidence_status === 'unconfirmed' ? 'warning' : 'neutral')}</td><td>${esc(row.basis || 'Chưa biết')}</td><td class="numeric">${esc(signedPercentFraction(row.weight_delta))}</td><td class="numeric">${esc(row.volume_change_pct == null ? '—' : signedPercentPoints(row.volume_change_pct * 100))}</td><td>${esc(row.flag || 'Không có cờ')}</td><td>${row.censored ? badge('Top-N', 'warning', 'Vào/rời Top-N không chứng minh mua mới hoặc bán hết') : badge(row.evidence_status === 'inferred_not_transaction' ? 'Suy luận' : 'Chưa xác nhận', 'neutral')}</td><td><a class="small-link" href="${href}" data-route>Chi tiết</a></td></tr>`;
  }).join('') : sorted.rows.map((row) => `<tr><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund)}" data-route>${esc(row.fund)}</a></td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}" data-route>${esc(row.ticker)}</a></td><td>${esc(row.asset_class)}</td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${topNCoverageBadge(row.coverage_type)}</td><td>${esc(row.effective_date ? dateText(row.effective_date) : 'Chưa biết')}</td></tr>`).join('');
  const classifications = [...new Set(allEvents.map((event) => event.classification).filter(Boolean))].sort(COLLATOR.compare);
  const classes = mode === 'events' ? classifications : [...new Set(allHoldings.map((row) => row.asset_class).filter(Boolean))];
  const options = classes.map((value) => `<option value="${esc(value)}" ${classification === value ? 'selected' : ''}>${esc(mode === 'events' ? eventTypeLabel(value) : categoryText(value))}</option>`).join('');
  const evidenceOptions = mode === 'events'
    ? `<option value="">Tất cả trạng thái</option><option value="inferred_not_transaction" ${evidence === 'inferred_not_transaction' ? 'selected' : ''}>Suy luận · chưa xác nhận giao dịch</option><option value="unconfirmed" ${evidence === 'unconfirmed' ? 'selected' : ''}>Chưa xác nhận</option><option value="observed_top_n_membership" ${evidence === 'observed_top_n_membership' ? 'selected' : ''}>Vào/rời top-N · censored</option><option value="heuristic" ${evidence === 'heuristic' ? 'selected' : ''}>Heuristic</option><option value="suspected" ${evidence === 'suspected' ? 'selected' : ''}>Nghi vấn / chưa xác minh</option><option value="unknown" ${evidence === 'unknown' ? 'selected' : ''}>Chưa phân loại</option>`
    : `<option value="">Tất cả độ phủ</option><option value="top_n_partial_censored" ${evidence === 'top_n_partial_censored' ? 'selected' : ''}>Top-N · bị kiểm duyệt</option><option value="source_claimed_full_not_independently_verified" ${evidence === 'source_claimed_full_not_independently_verified' ? 'selected' : ''}>Nguồn khai báo full · chưa xác minh</option>`;
  const filterBar = `<form class="control-bar" data-filter-form><input type="hidden" name="kind" value="${mode}"><div class="field"><label for="event-fund">Quỹ</label><select id="event-fund" name="fund"><option value="">Tất cả quỹ</option>${funds.map((fund) => `<option value="${esc(fund.fund_id)}" ${fund.fund_id === fundFilter ? 'selected' : ''}>${esc(fund.fund_id)} · ${esc(fund.name)}</option>`).join('')}</select></div><div class="field"><label for="event-ticker">Mã</label><input id="event-ticker" name="ticker" type="search" value="${esc(tickerFilter)}" placeholder="VD: FPT"></div><div class="field"><label for="event-class">${mode === 'events' ? 'Phân loại' : 'Nhóm tài sản'}</label><select id="event-class" name="classification"><option value="">Tất cả</option>${options}</select></div><div class="field"><label for="event-evidence">Bằng chứng / độ phủ</label><select id="event-evidence" name="evidence">${evidenceOptions}</select></div><div class="field"><label for="event-from">Từ ngày</label><input id="event-from" name="from" type="date" value="${esc(from)}"></div><div class="field"><label for="event-to">Đến ngày</label><input id="event-to" name="to" type="date" value="${esc(to)}"></div><button class="button primary" type="submit">Lọc</button><button class="button" type="button" data-action="reset-filters">Đặt lại</button></form>`;
  const active = mode === 'events' ? allEvents.length : allHoldings.length;
  const headers = mode === 'events'
    ? [{ key: 'observed_date', label: 'Ngày quan sát' }, { key: 'fund', label: 'Quỹ' }, { key: 'ticker', label: 'Mã' }, { key: 'display_label', label: 'Sự kiện' }, { key: 'basis', label: 'Cơ sở' }, { key: 'weight_delta', label: 'Δ tỷ trọng', numeric: true }, { key: 'volume_change_pct', label: 'Δ KL', numeric: true }, { key: 'flag', label: 'Cờ' }, { key: 'evidence_status', label: 'Trạng thái' }, { key: 'event_id', label: 'Chi tiết', sortable: false }]
    : [{ key: 'fund', label: 'Quỹ' }, { key: 'ticker', label: 'Mã' }, { key: 'asset_class', label: 'Nhóm' }, { key: 'weight', label: 'Tỷ trọng', numeric: true }, { key: 'volume', label: 'Khối lượng', numeric: true }, { key: 'observed_date', label: 'Ngày quan sát' }, { key: 'coverage_type', label: 'Độ phủ' }, { key: 'effective_date', label: 'Ngày hiệu lực nguồn' }];
  const table = active ? tableHtml(headers, body, { sort: sorted.key, direction: sorted.direction }) + paginationHtml(sorted) : emptyPanel(mode === 'events' ? 'Chưa có portfolio event' : 'Chưa có holdings snapshot', mode === 'events' ? 'Cần snapshot có thể so sánh và journal được lưu. Thiếu journal không chứng minh không có giao dịch.' : 'Dữ liệu hiện có trong export không đủ để dựng bảng này. Không coi missing position là zero.', '∅');
  const selectedEvent = params.get('event_id');
  const eventDetails = selectedEvent && mode === 'events' ? renderEventDetail(allEvents.find((event) => event.event_id === selectedEvent)) : '';
  const modeEnvelope = envelope(data, mode === 'events' ? 'portfolio_events' : 'holdings');
  return `${pageIntro('HOLDINGS & PORTFOLIO CHANGES', 'Danh mục & thay đổi', 'Tách snapshot được báo cáo khỏi sự kiện suy luận. Không quy đổi thiếu dữ liệu, top-N exit hoặc weight change thành giao dịch xác nhận.')}${datasetStatusNotice(modeEnvelope, mode === 'events' ? 'Journal sự kiện' : 'Snapshot holdings')}
    <div class="tab-strip"><a href="?view=holdings&kind=events" data-route class="${mode === 'events' ? 'active' : ''}" ${mode === 'events' ? 'aria-current="page"' : ''}>Sự kiện danh mục <span>${allEvents.length ? integer(allEvents.length) : '—'}</span></a><a href="?view=holdings&kind=holdings" data-route class="${mode === 'holdings' ? 'active' : ''}" ${mode === 'holdings' ? 'aria-current="page"' : ''}>Snapshot holdings <span>${allHoldings.length ? integer(allHoldings.length) : '—'}</span></a></div>
    ${mode === 'events' ? notice('Không phải nhật ký giao dịch', 'BUY/SELL là suy luận từ volume và proxy quy mô; UNCONFIRMED? và WEIGHT_CHANGE? vẫn chưa xác nhận. Vào/rời top-N không chứng minh mua mới hoặc bán hết.', 'warning') : ''}
    ${filterBar}${panel(mode === 'events' ? 'Journal sự kiện quan sát / suy luận' : 'Snapshot holdings mới nhất', mode === 'events' ? 'Các nhãn gốc được giữ, kèm phân loại tiếng Việt trung tính.' : 'Mỗi fund/source chỉ có snapshot mới nhất trong export; coverage được gắn nhãn riêng.', table)}${eventDetails}`;
}

function eventTypeLabel(classification, lang = getLang()) {
  if (lang === 'en') return ({position_increase_inferred:'Position increase (inferred; not a confirmed trade)', position_decrease_inferred:'Position decrease (inferred; not a confirmed trade)', pro_rata_change:'Pro-rata movement (heuristic; not a trade)', unconfirmed_volume_change:'Volume change (unconfirmed)', unconfirmed_weight_change:'Weight change (unconfirmed)', entered_top_n:'Entered Top-N (censored)', left_top_n:'Exited Top-N (not proof of full exit)', possible_corporate_action:'Possible corporate action (unverified)', unclassified:'Unclassified'})[classification] || classification || 'Unclassified';
  return ({ position_increase_inferred: 'Tăng vị thế (suy luận)', position_decrease_inferred: 'Giảm vị thế (suy luận)',
    pro_rata_change: 'Biến động đồng loạt (heuristic)', unconfirmed_volume_change: 'Thay đổi khối lượng (chưa xác nhận)',
    unconfirmed_weight_change: 'Thay đổi tỷ trọng (chưa xác nhận)', entered_top_n: 'Xuất hiện trong top-N',
    left_top_n: 'Rời top-N', possible_corporate_action: 'Nghi vấn corporate action', unclassified: 'Chưa phân loại' })[classification] || classification || 'Chưa phân loại';
}
function renderEventDetail(event, lang = getLang(), params = null) {
  if (!event) return `<section class="panel event-detail">${notice(lang === 'en' ? 'Event not found' : 'Không tìm thấy sự kiện', lang === 'en' ? 'The event key is not present in this export.' : 'Event key không tồn tại trong export hiện tại.', 'warning')}</section>`;
  const why = eventWhy(event, lang);
  const closeParams = new URLSearchParams(params || 'view=holdings&kind=events'); closeParams.delete('event_id'); closeParams.set('lang', lang);
  if (lang === 'en') return `<section class="panel event-detail"><div class="panel-header"><div><h3>Event details · ${esc(event.ticker || '—')} · ${esc(event.fund || '—')}</h3><p>${esc(event.display_label_en || eventTypeLabel(event.classification, 'en'))} · ${esc(event.observed_date ? dateText(event.observed_date) : 'Unknown date')} · ${esc(event.action || '—')}</p></div><a class="small-link" href="?${esc(closeParams)}" data-route>Close</a></div><div class="detail-grid"><div class="method-callout">${esc(why)}</div><dl class="detail-list"><div><dt>Basis</dt><dd>${esc(event.basis || 'Unknown')}</dd></div><div><dt>Flag</dt><dd>${esc(event.flag || 'None')}</dd></div><div><dt>Top-N censored</dt><dd>${event.censored ? 'Yes — censored coverage' : 'Not flagged as censored'}</dd></div><div><dt>Transaction-level</dt><dd>No confirmation evidence</dd></div><div><dt>Source</dt><dd>${sourceLink(event.source_reference)}</dd></div></dl></div></section>`;
  return `<section class="panel event-detail"><div class="panel-header"><div><h3>Chi tiết sự kiện · ${esc(event.ticker || '—')} · ${esc(event.fund || '—')}</h3><p>${esc(event.display_label || event.action)} · ${esc(event.observed_date ? dateText(event.observed_date) : 'Ngày chưa biết')}</p></div><a class="small-link" href="?view=holdings&kind=events" data-route>Đóng</a></div><div class="detail-grid"><div class="method-callout">${esc(why)}</div><dl class="detail-list"><div><dt>Basis</dt><dd>${esc(event.basis || 'Chưa rõ')}</dd></div><div><dt>Cờ</dt><dd>${esc(event.flag || 'Không có')}</dd></div><div><dt>Top-N censored</dt><dd>${event.censored ? 'Có — phạm vi bị kiểm duyệt' : 'Không được gắn cờ censored'}</dd></div><div><dt>Transaction-level</dt><dd>Không có bằng chứng xác nhận</dd></div><div><dt>Source</dt><dd>${sourceLink(event.source_reference)}</dd></div></dl></div></section>`;
}
function eventWhy(event, lang = getLang()) {
  if (lang === 'en') {
    if (event.action === 'UNCONFIRMED?') return 'Volume changed but too few common holdings had sufficient data to estimate the fund-scale proxy; an active change is not confirmed.';
    if (event.action === 'WEIGHT_CHANGE?') return 'Only weight is known; prices, fund flows or corporate actions may explain the change. Weight alone does not establish a trade.';
    if (event.action === 'NEW_IN_TOP10' || event.action === 'EXIT_TOP10') return 'Top-N is censored. Entering or leaving the list does not establish a new position or complete exit.';
    if (event.action === 'CORP_ACTION?') return 'This heuristic flags similar changes across funds and has not been checked against official corporate actions.';
    if (event.action === 'PRORATA') return 'The change is close to the fund-scale proxy and is not treated as a trading decision.';
    if (event.action === 'BUY' || event.action === 'SELL') return 'BUY/SELL are volume-based inferences using a median fund-scale proxy; they are not confirmed orders or trades.';
    return 'Classification is unknown; inspect the source and pipeline rules before interpreting.';
  }
  if (event.action === 'UNCONFIRMED?') return 'Khối lượng có thay đổi nhưng số mã chung đủ dữ liệu không đạt ngưỡng để ước lượng proxy quy mô quỹ; vì vậy không xác nhận tăng/giảm vị thế chủ động.';
  if (event.action === 'WEIGHT_CHANGE?') return 'Chỉ có tỷ trọng; thay đổi có thể do giá, dòng vốn hoặc corporate action. Pipeline không gán MUA/BÁN từ tỷ trọng đơn lẻ.';
  if (event.action === 'NEW_IN_TOP10' || event.action === 'EXIT_TOP10') return 'Nguồn chỉ lưu top-N. Mã đi vào/rời danh sách không cho biết giao dịch ngoài phạm vi top-N; không suy diễn mua mới hoặc bán hết.';
  if (event.action === 'CORP_ACTION?') return 'Cờ là heuristic từ biến động tương tự giữa nhiều quỹ. Chưa được đối chiếu với lịch corporate action chính thức.';
  if (event.action === 'PRORATA') return 'Biến động gần proxy quy mô quỹ; không xem như quyết định giao dịch hay quan điểm đầu tư.';
  if (event.action === 'BUY' || event.action === 'SELL') return 'Nhãn BUY/SELL trong journal là suy luận dựa trên khối lượng và median fund-scale proxy; không phải blotter, lệnh hay transaction đã xác nhận.';
  return 'Không có giải thích phân loại cụ thể; cần kiểm tra dữ liệu nguồn và quy tắc pipeline trước khi diễn giải.';
}

function stockRecords(data, yuantaUniverse = null) {
  const holdings = records(data, 'holdings');
  const events = records(data, 'portfolio_events');
  const map = new Map();
  for (const u of yuantaUniverse?.stocks || []) {
    if (!u?.symbol) continue;
    map.set(u.symbol, {
      ticker: u.symbol,
      company_name: u.company_name || null,
      exchange: u.exchange || null,
      macro_group: u.macro_group || null,
      macro_group_label: u.macro_group_label || null,
      sub_group: u.sub_group || null,
      sub_group_label: u.sub_group_label || u.sector_name || null,
      last_price: u.last_price ?? null,
      pe: u.pe ?? null,
      stock_rating: u.stock_rating ?? null,
      funds: new Set(),
      event_count: 0,
      latest_date: u.scanned_at ? String(u.scanned_at).slice(0, 10) : null,
      coverage: new Set(),
      has_yuanta: true,
    });
  }
  for (const item of [...holdings, ...events]) {
    if (!item.ticker) continue;
    if (!map.has(item.ticker)) {
      map.set(item.ticker, {
        ticker: item.ticker, company_name: null, exchange: null, macro_group: null,
        macro_group_label: null, sub_group_label: null, last_price: null, pe: null, stock_rating: null,
        funds: new Set(), event_count: 0, latest_date: null, coverage: new Set(), has_yuanta: false,
      });
    }
    const row = map.get(item.ticker);
    if (item.fund) row.funds.add(item.fund);
    if (item.event_id) row.event_count += 1;
    const date = item.observed_date;
    if (date && (!row.latest_date || date > row.latest_date)) row.latest_date = date;
    if (item.coverage_type) row.coverage.add(item.coverage_type);
  }
  return [...map.values()].map((row) => ({
    ...row,
    funds_count: row.funds.size,
    funds: [...row.funds].sort(COLLATOR.compare),
    coverage: [...row.coverage],
  })).sort((a, b) => COLLATOR.compare(a.ticker, b.ticker));
}
function readWatchlist() {
  try {
    const saved = JSON.parse(localStorage.getItem('vnfa-watchlist-v1') || '[]');
    return Array.isArray(saved) ? saved.filter((value) => typeof value === 'string').slice(0, 100) : [];
  } catch { return []; }
}
function saveWatchlist(items) {
  try { localStorage.setItem('vnfa-watchlist-v1', JSON.stringify([...new Set(items)].slice(0, 100))); } catch { /* storage may be disabled */ }
}
export function renderStocksEnglish(data, params) {
  const universe = data.yuantaUniverse || {status:'unavailable',count:null,stocks:[]};
  const q = (params.get('q') || '').trim(), exchange = (params.get('exchange') || '').trim().toUpperCase(), macro = (params.get('mgroup') || '').trim().toUpperCase();
  const selected = (params.get('ticker') || '').trim().toUpperCase(), stab = params.get('stab') || (universe.status === 'available' ? 'overview' : 'funds');
  const all = stockRecords(data,universe);
  const filtered = all.filter(row => (!exchange || row.exchange === exchange) && (!macro || row.macro_group === macro) && (!q || `${row.ticker} ${row.company_name || ''} ${row.sub_group_label || ''} ${translateSubGroup(row.sub_group,row.sub_group_label,'en')}`.toLocaleLowerCase('en').includes(q.toLocaleLowerCase('en'))));
  const sorted = applySortPage(filtered,params,'ticker'), defaultTab = universe.status === 'available' ? 'overview' : 'funds';
  const body = sorted.rows.map(row => `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}&stab=${defaultTab}&lang=en" data-route>${esc(row.ticker)}</a>${row.company_name ? `<span class="table-sub">${esc(row.company_name)}</span>` : ''}</td><td>${esc(row.exchange || '—')}</td><td>${esc(translateSubGroup(row.sub_group,row.sub_group_label || translateMacroGroup(row.macro_group,row.macro_group_label,'en'),'en'))}</td><td>${row.funds.slice(0,6).map(fund => `<a class="small-link mono fund-pill" href="?view=funds&fund=${encodeURIComponent(fund)}&lang=en" data-route>${esc(fund)}</a>`).join(' ') || '—'}${row.funds.length > 6 ? ` <span class="muted">+${row.funds.length-6}</span>` : ''}</td><td class="numeric">${row.pe == null ? '—' : number(row.pe,1)}</td><td class="numeric">${row.stock_rating == null ? '—' : number(row.stock_rating,0)}</td></tr>`).join('');
  const watchlist = readWatchlist(), current = all.find(row => row.ticker === selected);
  const fundRows = records(data,'holdings').filter(row => row.ticker === selected);
  const eventRows = records(data,'portfolio_events').filter(row => row.ticker === selected).sort((a,b)=>(b.observed_date || '').localeCompare(a.observed_date || ''));
  const entry = selected ? (globalThis.window?.__YUANTA_BUNDLES?.get(selected) || null) : null;
  const {nav,body:yuantaBody} = selected ? renderYuantaStockTabs(selected,stab,entry,params,'en') : {nav:'',body:null};
  const fundBody = `${current?.funds?.length ? `<p class="muted">Appears in ${integer(current.funds.length)} open-end funds in the export. Latest recorded date: ${esc(current.latest_date ? dateText(current.latest_date) : 'unknown')}.</p>` : notice('Not recorded in open-end fund Top-N', `${selected} does not appear in exported fund Top-N snapshots or events. It may still have a Yuanta stock bundle. This is not proof of zero ownership.`,'warning')}
    ${fundRows.length ? tableHtml([{key:'fund',label:'Fund',sortable:false},{key:'weight',label:'Reported weight',numeric:true,sortable:false},{key:'volume',label:'Volume',numeric:true,sortable:false},{key:'observed_date',label:'Snapshot date',sortable:false},{key:'effective_date',label:'Source effective date',sortable:false},{key:'coverage_type',label:'Coverage',sortable:false},{key:'source_reference',label:'Source',sortable:false}],fundRows.map(row => `<tr><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund || '')}&lang=en" data-route>${esc(row.fund || '—')}</a></td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td>${esc(row.effective_date ? dateText(row.effective_date) : 'Unknown')}</td><td>${topNCoverageBadge(row.coverage_type,'en')}</td><td>${sourceLink(row.source_reference)}</td></tr>`).join('')) : emptyPanel('No holdings records','No fund snapshot in the export for this ticker. Missing is not zero.','⌕')}
    <div class="section-heading"><div><h3>Portfolio events (open-end funds)</h3><p>Observed or inferred fund events only; these are not confirmed transactions.</p></div></div>${eventRows.length ? tableHtml([{key:'observed_date',label:'Date',sortable:false},{key:'fund',label:'Fund',sortable:false},{key:'display_label',label:'Classification',sortable:false},{key:'basis',label:'Basis',sortable:false},{key:'flag',label:'Flag',sortable:false}],eventRows.map(row => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Unknown')}</td><td>${esc(row.fund)}</td><td>${badge(row.display_label_en || eventTypeLabel(row.classification,'en'),'warning')}<span class="table-sub mono">${esc(row.action || '—')}</span></td><td>${esc(row.basis || '—')}</td><td>${esc(row.flag || '—')}</td></tr>`).join('')) : emptyPanel('No portfolio events','No fund events for this ticker are in the export.','⇄')}`;
  const selectedView = selected ? `<section class="panel stock-detail"><div class="panel-header"><div><div class="stock-ticker"><h2>${esc(selected)}</h2>${current?.exchange ? badge(current.exchange,'neutral') : ''}${current?.macro_group ? badge(translateMacroGroup(current.macro_group,current.macro_group_label,'en'),'ok') : ''}</div><p>${esc(current?.company_name || 'Open-end fund ownership and five static Yuanta YSradar research pages')}</p></div><button class="button watch-button" type="button" data-watch="${esc(selected)}" aria-pressed="${watchlist.includes(selected)}">${watchlist.includes(selected) ? '✓ Watching' : '+ Add to watchlist'}</button></div>${nav}${stab === 'funds' ? fundBody : yuantaBody}</section>` : '';
  const watchHtml = watchlist.length ? `<div class="watchlist">${watchlist.map(ticker => `<span class="watch-chip"><a href="?view=stocks&ticker=${encodeURIComponent(ticker)}&stab=${defaultTab}&lang=en" data-route>${esc(ticker)}</a><button type="button" data-watch-remove="${esc(ticker)}" aria-label="Remove ${esc(ticker)} from watchlist">×</button></span>`).join('')}</div>` : '<p class="muted">Watchlist is stored locally in this browser; no tickers are being watched.</p>';
  const count = value => value == null ? '—' : integer(value);
  const statusBox = universe.status === 'available' ? `<div class="unavailable-grid"><div class="unavailable-item"><strong>Yuanta exchange coverage</strong><span>${count(universe.count)} stocks (HOSE: ${count(universe.by_exchange?.HOSE)}, HNX: ${count(universe.by_exchange?.HNX)}, UPCOM: ${count(universe.by_exchange?.UPCOM)})</span></div><div class="unavailable-item"><strong>Three macro sector groups</strong><span>Banks (${count(universe.by_macro_group?.BANK)}) · Non-Bank Financials (${count(universe.by_macro_group?.NON_BANK_FINANCIAL)}) · Non-Financials incl. Real Estate (${count(universe.by_macro_group?.NON_FINANCIAL)})</span></div></div>` : `<div class="unavailable-grid"><div class="unavailable-item"><strong>Yuanta exchange data (Pages 1–5)</strong><span>${universe.status === 'not_synced' ? 'Static universe not synced' : universe.status === 'malformed' ? 'Static universe is malformed' : 'Static universe could not be loaded; absence is not established'}</span></div><div class="unavailable-item"><strong>Full-universe scanner</strong><span><code>python scripts/scan_yuanta_universe.py</code></span></div></div>`;
  const filters = `<form class="control-bar" data-filter-form><div class="field grow"><label for="stock-search">Search ticker / company</label><input id="stock-search" name="q" type="search" value="${esc(q)}" placeholder="e.g. ACB, FPT, Vinhomes"></div><div class="field"><label for="stock-exchange">Exchange</label><select id="stock-exchange" name="exchange"><option value="">All three exchanges</option>${['HOSE','HNX','UPCOM'].map(ex => `<option value="${ex}" ${exchange === ex ? 'selected' : ''}>${ex}</option>`).join('')}</select></div><div class="field"><label for="stock-mgroup">Macro sector group</label><select id="stock-mgroup" name="mgroup"><option value="">All groups</option><option value="BANK" ${macro === 'BANK' ? 'selected' : ''}>Banks</option><option value="NON_BANK_FINANCIAL" ${macro === 'NON_BANK_FINANCIAL' ? 'selected' : ''}>Non-Bank Financials</option><option value="NON_FINANCIAL" ${macro === 'NON_FINANCIAL' ? 'selected' : ''}>Non-Financials (includes Real Estate)</option></select></div><button class="button primary" type="submit">Filter</button><button class="button" type="button" data-action="reset-filters">Reset</button></form>`;
  return `${pageIntro('STOCK RESEARCH · 3 EXCHANGES','Stock research (HOSE · HNX · UPCOM)','Combines Fmarket open-end fund ownership with five Yuanta YSradar static research pages. Real Estate is separated from ICB Level 1 Financials. Yuanta ratings are third-party scores, not recommendations.')}${datasetStatusNotice(envelope(data,'holdings'),'Holdings')}${datasetStatusNotice(envelope(data,'portfolio_events'),'Portfolio events')}
    <div class="stock-layout"><section class="panel"><div class="panel-header"><div><h3>Stock universe (${integer(filtered.length)} / ${integer(all.length)})</h3><p>Select a ticker or enter one directly to open its research pages.</p></div></div>${filters}${all.length ? tableHtml([{key:'ticker',label:'Ticker / company'},{key:'exchange',label:'Exchange'},{key:'sub_group_label',label:'Industry (Real Estate separated)'},{key:'funds_count',label:'Open-end funds'},{key:'pe',label:'P/E',numeric:true},{key:'stock_rating',label:'Rating (Yuanta)',numeric:true}],body,{sort:sorted.key,direction:sorted.direction}) + paginationHtml(sorted) : emptyPanel('Stock data unavailable','Run export_dashboard.py or scan_yuanta_universe.py.','⌕')}</section>
    <div class="stacked-panels">${panel('Direct ticker lookup','Open Pages 1–5 for a ticker on any of the three exchanges.',`<form class="control-bar" data-filter-form><input type="hidden" name="stab" value="overview"><div class="field grow"><label for="direct-ticker">Ticker</label><input id="direct-ticker" name="ticker" type="search" value="${esc(selected)}" placeholder="e.g. ACB, VCB, VHM, SSI"></div><button class="button primary" type="submit">Open research pages</button></form>`)}${panel('Personal watchlist','Stored locally on this device; not sent to a server.',watchHtml)}${panel('Yuanta static data status','Same-origin static .json.gz files; the browser never calls the external API live.',statusBox)}</div></div>${selectedView}`;
}

function renderStocks(data, params) {
  if (getLang(params) === 'en') return renderStocksEnglish(data, params);
  const yuantaUniverse = data.yuantaUniverse || { status: 'not_synced', count: 0, stocks: [] };
  const q = (params.get('q') || '').trim();
  const qLower = q.toLocaleLowerCase('vi');
  const exFilter = (params.get('exchange') || '').trim().toUpperCase();
  const mgFilter = (params.get('mgroup') || '').trim().toUpperCase();
  const selected = (params.get('ticker') || '').trim().toUpperCase();
  const stab = params.get('stab') || (yuantaUniverse.status === 'available' ? 'overview' : 'funds');
  const allStocks = stockRecords(data, yuantaUniverse);
  const filtered = allStocks.filter((row) => {
    if (exFilter && row.exchange !== exFilter) return false;
    if (mgFilter && row.macro_group !== mgFilter) return false;
    if (!qLower) return true;
    const hay = `${row.ticker} ${row.company_name || ''} ${row.sub_group_label || ''}`.toLocaleLowerCase('vi');
    return hay.includes(qLower);
  });
  const sorted = applySortPage(filtered, params, 'ticker');
  const defaultTab = yuantaUniverse.status === 'available' ? 'overview' : 'funds';
  const body = sorted.rows.map((row) => `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}&stab=${defaultTab}" data-route>${esc(row.ticker)}</a>${row.company_name ? `<span class="table-sub">${esc(row.company_name)}</span>` : ''}</td><td>${esc(row.exchange || '—')}</td><td>${esc(row.sub_group_label || row.macro_group_label || '—')}</td><td>${row.funds.slice(0, 6).map((fund) => `<a class="small-link mono fund-pill" href="?view=funds&fund=${encodeURIComponent(fund)}" data-route>${esc(fund)}</a>`).join(' ') || '—'}${row.funds.length > 6 ? ` <span class="muted">+${row.funds.length - 6}</span>` : ''}</td><td class="numeric">${row.pe == null ? '—' : number(row.pe, 1)}</td><td class="numeric">${row.stock_rating == null ? '—' : number(row.stock_rating, 0)}</td></tr>`).join('');
  const watchlist = readWatchlist();
  const fundRows = records(data, 'holdings').filter((row) => row.ticker === selected);
  const eventRows = records(data, 'portfolio_events').filter((row) => row.ticker === selected).sort((a, b) => (b.observed_date || '').localeCompare(a.observed_date || ''));
  const currentSelected = allStocks.find((row) => row.ticker === selected);
  const bundleEntry = selected ? (window.__YUANTA_BUNDLES?.get(selected) || null) : null;
  const { nav: subTabNav, body: yuantaTabBody } = selected
    ? renderYuantaStockTabs(selected, stab, bundleEntry, params, getLang(params))
    : { nav: '', body: null };

  const fundOwnershipBody = `${currentSelected?.funds?.length ? `<p class="muted">Xuất hiện trong ${integer(currentSelected.funds.length)} quỹ mở có dữ liệu đã export. Ngày mới nhất: ${esc(currentSelected.latest_date ? dateText(currentSelected.latest_date) : 'chưa biết')}.</p>` : notice('Chưa ghi nhận trong Top-N quỹ mở', `Không tìm thấy ${selected} trong snapshot Top-N hoặc event export của quỹ mở (mã vẫn có thể xem đủ 5 trang dữ liệu thị trường Yuanta ở các tab bên cạnh).`, 'warning')}
    ${fundRows.length ? tableHtml([{ key: 'fund', label: 'Quỹ', sortable: false }, { key: 'weight', label: 'Tỷ trọng báo cáo', numeric: true, sortable: false }, { key: 'volume', label: 'Khối lượng', numeric: true, sortable: false }, { key: 'observed_date', label: 'Ngày snapshot', sortable: false }, { key: 'effective_date', label: 'Ngày nguồn', sortable: false }, { key: 'coverage_type', label: 'Độ phủ', sortable: false }, { key: 'source_reference', label: 'Source', sortable: false }], fundRows.map((row) => `<tr><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund)}" data-route>${esc(row.fund)}</a></td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${esc(row.effective_date ? dateText(row.effective_date) : 'Chưa biết')}</td><td>${topNCoverageBadge(row.coverage_type)}</td><td>${sourceLink(row.source_reference)}</td></tr>`).join('')) : emptyPanel('Chưa có holdings record', 'Không có snapshot quỹ mở cho mã này trong export. Không suy missing thành zero.', '⌕')}
    <div class="section-heading"><div><h3>Portfolio events (Quỹ mở)</h3><p>Chỉ sự kiện quan sát / suy luận của pipeline quỹ mở.</p></div></div>${eventRows.length ? tableHtml([{ key: 'observed_date', label: 'Ngày', sortable: false }, { key: 'fund', label: 'Quỹ', sortable: false }, { key: 'display_label', label: 'Phân loại', sortable: false }, { key: 'basis', label: 'Basis', sortable: false }, { key: 'flag', label: 'Cờ', sortable: false }], eventRows.map((row) => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${esc(row.fund)}</td><td>${badge(getLang() === 'en' ? (row.display_label_en || eventTypeLabel(row.classification, 'en')) : row.display_label, 'warning')}</td><td>${esc(row.basis || '—')}</td><td>${esc(row.flag || '—')}</td></tr>`).join('')) : emptyPanel('Chưa có event', 'Không có sự kiện quỹ mở được lưu cho mã này.', '⇄')}`;

  const selectedView = selected ? `<section class="panel stock-detail"><div class="panel-header"><div><div class="stock-ticker"><h2>${esc(selected)}</h2>${currentSelected?.exchange ? badge(currentSelected.exchange, 'neutral') : ''}${currentSelected?.macro_group_label ? badge(currentSelected.macro_group_label, 'ok') : ''}</div><p>${esc(currentSelected?.company_name || 'Tra cứu sở hữu quỹ mở & 5 phân hệ dữ liệu Yuanta YSradar (tĩnh)')}</p></div><button class="button watch-button" type="button" data-watch="${esc(selected)}" aria-pressed="${watchlist.includes(selected)}">${watchlist.includes(selected) ? '✓ Đang theo dõi' : '+ Thêm watchlist'}</button></div>
    ${subTabNav}
    ${stab === 'funds' ? fundOwnershipBody : yuantaTabBody}
  </section>` : '';

  const watchHtml = watchlist.length ? `<div class="watchlist">${watchlist.map((ticker) => `<span class="watch-chip"><a href="?view=stocks&ticker=${encodeURIComponent(ticker)}&stab=${defaultTab}" data-route>${esc(ticker)}</a><button type="button" data-watch-remove="${esc(ticker)}" aria-label="Xóa ${esc(ticker)} khỏi watchlist">×</button></span>`).join('')}</div>` : `<p class="muted">Watchlist lưu trong localStorage của trình duyệt này; chưa có mã theo dõi.</p>`;
  const stockDataNotice = `${datasetStatusNotice(envelope(data, 'holdings'), 'Holdings')}${datasetStatusNotice(envelope(data, 'portfolio_events'), 'Portfolio events')}`;
  const yuantaStatusBox = yuantaUniverse.status === 'available'
    ? `<div class="unavailable-grid"><div class="unavailable-item"><strong>Độ phủ 3 sàn Yuanta</strong><span>${integer(yuantaUniverse.count)} mã (HOSE: ${integer(yuantaUniverse.by_exchange?.HOSE || 0)}, HNX: ${integer(yuantaUniverse.by_exchange?.HNX || 0)}, UPCOM: ${integer(yuantaUniverse.by_exchange?.UPCOM || 0)})</span></div><div class="unavailable-item"><strong>Phân tách 3 khối ngành</strong><span>Ngân hàng (${integer(yuantaUniverse.by_macro_group?.BANK || 0)}) · TC phi NH (${integer(yuantaUniverse.by_macro_group?.NON_BANK_FINANCIAL || 0)}) · Phi TC &amp; BĐS (${integer(yuantaUniverse.by_macro_group?.NON_FINANCIAL || 0)})</span></div></div>`
    : `<div class="unavailable-grid"><div class="unavailable-item"><strong>Dữ liệu 3 sàn Yuanta (Trang 1–5)</strong><span>Chưa đồng bộ gói .json.gz tĩnh</span></div><div class="unavailable-item"><strong>Lệnh quét toàn sàn</strong><span><code>python scripts/scan_yuanta_universe.py</code></span></div></div>`;

  return `${pageIntro('STOCK RESEARCH · 3 EXCHANGES', 'Nghiên cứu mã cổ phiếu (HOSE · HNX · UPCOM)', 'Kết hợp bằng chứng sở hữu quỹ mở (Fmarket) và 5 trang phân tích cổ phiếu từ gói dữ liệu tĩnh Yuanta YSradar (Tổng quan, Giao dịch, Tương quan 3 khối ngành tách BĐS, BCTC Q1/2010–nay, Tin tức & sự kiện).')}${stockDataNotice}
    <div class="stock-layout"><section class="panel"><div class="panel-header"><div><h3>Danh mục mã cổ phiếu (${integer(filtered.length)} / ${integer(allStocks.length)} mã)</h3><p>Chọn mã bất kỳ hoặc nhập mã (VD: ACB) để mở chi tiết 5 trang nghiên cứu bên dưới.</p></div></div>
    <form class="control-bar" data-filter-form>
      <div class="field grow"><label for="stock-search">Tìm mã / tên công ty</label><input id="stock-search" name="q" type="search" value="${esc(q)}" placeholder="VD: ACB, FPT, Vinhomes"></div>
      <div class="field"><label for="stock-exchange">Sàn</label><select id="stock-exchange" name="exchange"><option value="">Cả 3 sàn</option>${['HOSE', 'HNX', 'UPCOM'].map((ex) => `<option value="${ex}" ${exFilter === ex ? 'selected' : ''}>${ex}</option>`).join('')}</select></div>
      <div class="field"><label for="stock-mgroup">Khối ngành (3 nhóm)</label><select id="stock-mgroup" name="mgroup"><option value="">Tất cả khối</option><option value="BANK" ${mgFilter === 'BANK' ? 'selected' : ''}>Ngân hàng</option><option value="NON_BANK_FINANCIAL" ${mgFilter === 'NON_BANK_FINANCIAL' ? 'selected' : ''}>Tài chính phi ngân hàng</option><option value="NON_FINANCIAL" ${mgFilter === 'NON_FINANCIAL' ? 'selected' : ''}>Phi tài chính (gồm BĐS)</option></select></div>
      <button class="button primary" type="submit">Lọc</button>
      <button class="button" type="button" data-action="reset-filters">Đặt lại</button>
    </form>
    ${allStocks.length ? tableHtml([{ key: 'ticker', label: 'Mã / Doanh nghiệp' }, { key: 'exchange', label: 'Sàn' }, { key: 'sub_group_label', label: 'Ngành (tách BĐS)' }, { key: 'funds_count', label: 'Quỹ mở nắm giữ' }, { key: 'pe', label: 'P/E', numeric: true }, { key: 'stock_rating', label: 'Rating (Yuanta)', numeric: true }], body, { sort: sorted.key, direction: sorted.direction }) + paginationHtml(sorted) : emptyPanel('Chưa có dữ liệu cổ phiếu', 'Hãy chạy export_dashboard.py hoặc scan_yuanta_universe.py.', '⌕')}</section>
    <div class="stacked-panels">${panel('Tra cứu trực tiếp theo mã (VD: ACB)', 'Mở nhanh Trang 1–5 cho bất kỳ mã nào trên 3 sàn.', `<form class="control-bar" data-filter-form><input type="hidden" name="stab" value="overview"><div class="field grow"><label for="direct-ticker">Mã CK</label><input id="direct-ticker" name="ticker" type="search" value="${esc(selected)}" placeholder="Nhập mã: ACB, VCB, VHM, SSI…"></div><button class="button primary" type="submit">Mở 5 trang</button></form>`)}${panel('Watchlist cá nhân', 'Lưu cục bộ trên thiết bị hiện tại; không gửi lên server.', watchHtml)}${panel('Trạng thái kho dữ liệu 3 sàn Yuanta', 'Tệp tĩnh .json.gz trên cùng origin; không gọi trực tiếp API ngoài từ trình duyệt.', yuantaStatusBox)}</div></div>${selectedView}`;
}

export function resolveCompareState(funds, navRecords, { category = '', a = '', b = '' } = {}) {
  const candidates = (funds || []).filter((row) => row.asset_code && (!category || row.asset_code === category));
  const fundMap = new Map((funds || []).map((fund) => [fund.fund_id, fund]));
  const assetA = fundMap.get(a)?.asset_code;
  const assetB = fundMap.get(b)?.asset_code;
  let result;
  if (a && b && a !== b && (!assetA || !assetB)) {
    result = { status: 'unknown_categories', dates: [], seriesA: [], seriesB: [] };
  } else if (a && b && a !== b && assetA !== assetB) {
    result = { status: 'incompatible_categories', dates: [], seriesA: [], seriesB: [] };
  } else if ((a && category && assetA && assetA !== category) || (b && category && assetB && assetB !== category)) {
    result = { status: 'selection_outside_category', dates: [], seriesA: [], seriesB: [] };
  } else {
    result = compareNavSeries(navRecords || [], a, b);
  }
  return { category, fundA: a, fundB: b, candidates, fundMap, result };
}

function renderCompare(data, params) {
  if (getLang(params) === 'en') return renderCompareEnglish(data, params);
  const funds = records(data, 'funds');
  const { category, fundA, fundB, candidates, fundMap, result } = resolveCompareState(
    funds, records(data, 'nav_history'),
    { category: params.get('category') || '', a: params.get('a') || '', b: params.get('b') || '' },
  );
  const groups = [...new Set(funds.map((row) => row.asset_code).filter(Boolean))].sort(COLLATOR.compare);
  const selectOptions = (selected) => `<option value="">Chọn quỹ</option>${candidates.map((fund) => `<option value="${esc(fund.fund_id)}" ${fund.fund_id === selected ? 'selected' : ''}>${esc(fund.fund_id)} · ${esc(fund.name)}</option>`).join('')}`;
  const controls = `<form class="control-bar" data-filter-form><div class="field"><label for="compare-category">Nhóm so sánh</label><select id="compare-category" name="category"><option value="">Tất cả nhóm (cùng loại khi so sánh)</option>${groups.map((group) => `<option value="${esc(group)}" ${group === category ? 'selected' : ''}>${esc(categoryText(group))}</option>`).join('')}</select></div><div class="field grow"><label for="compare-a">Quỹ A</label><select id="compare-a" name="a">${selectOptions(fundA)}</select></div><div class="field grow"><label for="compare-b">Quỹ B</label><select id="compare-b" name="b">${selectOptions(fundB)}</select></div><button class="button primary" type="submit">So sánh</button><button class="button" type="button" data-action="reset-filters">Đặt lại</button></form>`;
  let compareContent;
  if (result.status === 'available') {
    const fa = fundMap.get(fundA); const fb = fundMap.get(fundB);
    const navA = records(data, 'nav_history').filter((row) => row.fund === fundA && row.series_type === 'nav_history');
    const navB = records(data, 'nav_history').filter((row) => row.fund === fundB && row.series_type === 'nav_history');
    compareContent = `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>So sánh mô tả, không phải ranking</strong><p>Chỉ dùng các ngày NAV nguồn trùng chính xác; đây là biến động NAV đơn giản trên cùng ngày đầu/cuối. Không điều chỉnh phân phối, benchmark, risk, phí hoặc missing daily dates; publication time chưa biết.</p></div></div><div class="compare-summary"><div><small>${esc(fa?.fund_id || fundA)} · ${esc(categoryText(fa?.asset_code))}</small><strong>${esc(signedPercentPoints(result.changeA))}</strong><span>NAV change · ${esc(dateText(result.start))} → ${esc(dateText(result.end))}</span></div><div class="compare-separator">vs</div><div><small>${esc(fb?.fund_id || fundB)} · ${esc(categoryText(fb?.asset_code))}</small><strong>${esc(signedPercentPoints(result.changeB))}</strong><span>NAV change · ${esc(dateText(result.start))} → ${esc(dateText(result.end))}</span></div></div>${renderNormalizedCompareChart(result)}<div class="compare-footnote">Ngày chung: ${integer(result.dates.length)} · Chuỗi A có ${integer(navA.length)} điểm trong export; chuỗi B có ${integer(navB.length)} điểm. Không xếp hạng quỹ.</div>`;
  } else if (result.status === 'insufficient_common_dates') {
    compareContent = emptyPanel('Chưa đủ ngày NAV chung', 'Cần ít nhất hai ngày NAV nguồn trùng chính xác cho cả hai quỹ. Snapshot ngày quan sát không dùng để tính hiệu suất.', '▥');
  } else if (result.status === 'duplicate_source_dates') {
    compareContent = emptyPanel('Ngày NAV nguồn bị trùng', 'Có nhiều bản ghi cùng ngày cho một quỹ; không tự chọn revision để tạo kết quả so sánh.', '▥');
  } else if (result.status === 'incompatible_categories') {
    compareContent = emptyPanel('Hai quỹ không cùng nhóm tài sản', 'Chọn quỹ cùng category; không so sánh quỹ cổ phiếu và trái phiếu như cùng mục tiêu rủi ro.', '▥');
  } else if (result.status === 'unknown_categories') {
    compareContent = emptyPanel('Chưa xác định được nhóm tài sản', 'Thiếu category metadata để khẳng định hai quỹ có thể so sánh; metric không được tính.', '▥');
  } else if (result.status === 'selection_outside_category') {
    compareContent = `<div class="panel-empty"><div><div class="empty-mark" aria-hidden="true">▥</div><strong>Quỹ đang chọn trên URL không thuộc nhóm đang lọc</strong><p>Hãy chọn hai quỹ trong nhóm ${esc(categoryText(category))} hoặc xóa lựa chọn cũ để tiếp tục.</p><button class="button" type="button" data-action="clear-incompatible-compare">Xóa lựa chọn ngoài nhóm</button></div></div>`;
  } else {
    compareContent = emptyPanel('Chưa chọn đủ quỹ', 'Chọn hai quỹ cùng nhóm có dữ liệu NAV nguồn. Quỹ trái phiếu và cổ phiếu không được so sánh như cùng mục tiêu rủi ro.', '▥');
  }
  return `${pageIntro('FUND COMPARISON', 'So sánh quỹ', 'Chỉ so sánh cùng nhóm tài sản và chỉ khi có các ngày NAV nguồn trùng nhau. Kết quả không phải total return hay xếp hạng đầu tư.')}${datasetStatusNotice(envelope(data, 'nav_history'), 'NAV history')}${notice('So sánh hiện phụ thuộc dữ liệu có thật', 'Nếu lịch sử không đủ hoặc nhóm không tương thích, metric sẽ không được tính; không dùng ngày gần nhất hoặc nội suy để tạo cặp giả.', 'warning')}${controls}${panel('NAV trên kỳ chung', 'Biến động NAV mô tả, có thể thiếu phân phối / benchmark và không phải performance chuẩn hóa.', compareContent)}`;
}
export function renderCompareEnglish(data, params) {
  const funds = records(data, 'funds');
  const { category, fundA, fundB, candidates, fundMap, result } = resolveCompareState(
    funds, records(data, 'nav_history'), {category: params.get('category') || '', a: params.get('a') || '', b: params.get('b') || ''});
  const groups = [...new Set(funds.map(row => row.asset_code).filter(Boolean))].sort(COLLATOR.compare);
  const options = selected => `<option value="">Select fund</option>${candidates.map(fund => `<option value="${esc(fund.fund_id)}" ${fund.fund_id === selected ? 'selected' : ''}>${esc(fund.fund_id)} · ${esc(fund.name)}</option>`).join('')}`;
  const controls = `<form class="control-bar" data-filter-form><div class="field"><label for="compare-category">Asset category</label><select id="compare-category" name="category"><option value="">All categories (same category required)</option>${groups.map(group => `<option value="${esc(group)}" ${group === category ? 'selected' : ''}>${esc(categoryText(group, 'en'))}</option>`).join('')}</select></div><div class="field grow"><label for="compare-a">Fund A</label><select id="compare-a" name="a">${options(fundA)}</select></div><div class="field grow"><label for="compare-b">Fund B</label><select id="compare-b" name="b">${options(fundB)}</select></div><button class="button primary" type="submit">Compare</button><button class="button" type="button" data-action="reset-filters">Reset</button></form>`;
  let content;
  if (result.status === 'available') {
    const fa = fundMap.get(fundA), fb = fundMap.get(fundB);
    const countA = records(data, 'nav_history').filter(row => row.fund === fundA && row.series_type === 'nav_history').length;
    const countB = records(data, 'nav_history').filter(row => row.fund === fundB && row.series_type === 'nav_history').length;
    content = `${notice('Descriptive comparison, not a ranking', 'Uses exact common source NAV dates within the same asset category. Simple NAV change only; not total return, benchmark-adjusted alpha, or an investment recommendation. Distributions, benchmark, risk, fees and missing dates are not adjusted; publication time is unknown.', 'warning')}<div class="compare-summary"><div><small>${esc(fa?.fund_id || fundA)} · ${esc(categoryText(fa?.asset_code, 'en'))}</small><strong>${esc(signedPercentPoints(result.changeA, 2, 'en'))}</strong><span>NAV change · ${esc(dateText(result.start))} → ${esc(dateText(result.end))}</span></div><div class="compare-separator">vs</div><div><small>${esc(fb?.fund_id || fundB)} · ${esc(categoryText(fb?.asset_code, 'en'))}</small><strong>${esc(signedPercentPoints(result.changeB, 2, 'en'))}</strong><span>NAV change · ${esc(dateText(result.start))} → ${esc(dateText(result.end))}</span></div></div>${renderNormalizedCompareChart(result, 'en')}<div class="compare-footnote">Common dates: ${integer(result.dates.length)} · Exported points: Fund A ${integer(countA)}; Fund B ${integer(countB)}. Funds are not ranked.</div>`;
  } else {
    const messages = {insufficient_common_dates:['Insufficient common NAV dates','At least two exact common source NAV dates are required. Observation snapshots are not used.'], duplicate_source_dates:['Duplicate source NAV dates','Multiple records share a date; revisions are not selected automatically.'], incompatible_categories:['Different asset categories','Choose funds from the same category.'], unknown_categories:['Unknown asset categories','Category metadata is missing; no comparison is computed.'], selection_outside_category:['Selected funds are outside this category','Select compatible funds or clear the previous selection.'], select_two_funds:['Select two funds','Choose two funds in the same category with source NAV history.']};
    const [title, description] = messages[result.status] || messages.select_two_funds;
    content = emptyPanel(title, description, '▥');
    if (result.status === 'selection_outside_category') content += '<button class="button" type="button" data-action="clear-incompatible-compare">Clear incompatible selection</button>';
  }
  return `${pageIntro('FUND COMPARISON', 'Fund comparison', 'Compare the same asset category on exact common NAV source dates only. Results are not total returns or investment rankings.')}${datasetStatusNotice(envelope(data, 'nav_history'), 'NAV history')}${controls}${panel('NAV over common dates', 'Descriptive NAV change; distributions and benchmark may be missing.', content)}`;
}

function renderNormalizedCompareChart(result, lang = getLang()) {
  const series = [result.seriesA.map((row) => ({ ...row, label: 'Quỹ A', color: '#247f7a' })), result.seriesB.map((row) => ({ ...row, label: 'Quỹ B', color: '#748a98' }))];
  const values = series.flat().map((row) => row.normalized); const min = Math.min(...values); const max = Math.max(...values);
  const width = 720; const height = 210; const p = { l: 48, r: 14, t: 12, b: 29 }; const w = width-p.l-p.r; const h = height-p.t-p.b;
  const x = (i) => p.l + (result.dates.length < 2 ? w/2 : i/(result.dates.length-1)*w);
  const y = (v) => p.t + (max===min ? h/2 : (max-v)/(max-min)*h);
  const parts = series.map((rows) => `<path d="${rows.map((row,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(row.normalized).toFixed(1)}`).join(' ')}" fill="none" stroke="${rows[0].color}" stroke-width="2.2" ${rows[0].label === 'Quỹ B' ? 'stroke-dasharray="5 4"' : ''}/>`).join('');
  return `<div class="chart-box"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${lang === 'en' ? 'NAV normalized to 100 at the first common date; not total return' : 'Biểu đồ NAV đã chuẩn hóa về 100 tại ngày chung đầu tiên; không phải total return'}"><line x1="${p.l}" x2="${width-p.r}" y1="${height-p.b}" y2="${height-p.b}" stroke="#e1e7e9"/>${parts}<text x="${p.l}" y="${height-7}" fill="#84929a" font-size="9">${esc(result.start)}</text><text x="${width-p.r}" y="${height-7}" text-anchor="end" fill="#84929a" font-size="9">${esc(result.end)}</text></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>${lang === 'en' ? 'Fund A · normalized = 100' : 'Quỹ A · chuẩn hóa = 100'}</span><span class="legend-item"><span class="legend-line snapshot"></span>${lang === 'en' ? 'Fund B · normalized = 100' : 'Quỹ B · chuẩn hóa = 100'}</span></div></div>`;
}

// Older schema-1 artifacts may not have localized prose. Map their stable codes,
// never translate or expose arbitrary raw diagnostics from the provider.
export function localizeLegacyIssue(issue) {
  const id = String(issue.issue_id || ''), code = id.startsWith('quality:') ? id.split(':').at(-1) : '';
  const findings = {
    dataset_missing:'Dataset is unavailable.', invalid_container:'Expected a tabular dataset.', empty_dataset:'No observations were returned.',
    schema_missing_columns:'Required schema columns are missing.', schema_missing_optional:'An optional column is unavailable.',
    missing_fund_id:'Fund code is required.', duplicate_fund_id:'Fund codes must be unique.',
    missing_ticker:'Holding ticker is required.', duplicate_ticker:'A snapshot must have at most one row per ticker.',
    invalid_weight:'Weight must be numeric and finite.', negative_weight:'Negative portfolio weights are unsupported.',
    unusual_weight:'Weight exceeds 102%; check source units and leverage.', missing_update_date:'Source effective/reporting date is unknown.',
    invalid_update_date:'Some source update dates are invalid.', censored_top_n:'Top-N disclosure is not a complete fund portfolio.',
    invalid_nav_date:'NAV dates must be valid.', invalid_nav:'NAV must be numeric and finite.', nonpositive_nav:'NAV must be positive.',
    duplicate_nav_key:'NAV history contains duplicate keys.', effective_date_unknown:'Snapshot dates are observation dates; NAV effective dates are unknown.',
    missing_asset_code:'Allocation asset code is required.', invalid_allocation_pct:'Allocation percentage must be numeric and finite.',
    negative_allocation_pct:'Allocation percentage cannot be negative.', unusual_allocation_pct:'Allocation percentage exceeds 102%; check units.',
    missing_report_date:'Effective/reporting date is unavailable.', invalid_report_date:'Some report dates are invalid.',
  };
  const fixed = {
    missing_source_reference:'Some observations have no source reference.',
    publication_time_unknown:'Publication time is missing; the effective date does not establish publication time.',
    observation_time_unknown:'Observation time is missing; these records lack evidence for a strict PIT cutoff.',
    approximate_dates:'Some observation dates are approximate and are not PIT-safe.',
    unconfirmed_portfolio_events:'Portfolio events are observed or inferred changes, not confirmed trades.',
    top_n_censoring:'Entering or leaving Top-N does not establish a new position or complete exit.',
    'unsafe_path:research_observations':'The provenance sidecar path was unsafe and was not exported.',
  };
  let message = fixed[id];
  if (!message && id.startsWith('missing:')) message = 'Dataset is missing from the archive; this does not prove the source was empty.';
  if (!message && id.startsWith('schema:')) message = 'A dataset could not be read or did not match the expected schema.';
  if (!message && id.startsWith('duplicate:')) message = 'Duplicate keys detected; determine whether they are valid revisions or unexpected duplicates.';
  if (!message && id.startsWith('stale:')) message = 'Dataset exceeds its configured freshness SLA.';
  if (!message && code) message = findings[code] || `Validator finding: ${code}.`;
  if (!message && id.startsWith('persisted:')) message = 'Recorded validator finding; review the source and validator.';
  return {message: issue.message_en || message || `Finding: ${id || 'unknown'}.`, remediation: issue.remediation_en || 'Review the source and validator before interpreting; do not replace missing data with zero.'};
}

export function renderQualityEnglish(data, params) {
  const summary = envelope(data, 'data_quality_summary').summary || {};
  const datasets = summary.datasets || [];
  const issues = (summary.issues || []).map(issue => ({...issue, ...localizeLegacyIssue(issue)}));
  for (const report of records(data, 'data_quality')) {
    for (const finding of report.findings || []) {
      if (!finding.severity || !finding.code) continue;
      issues.push({issue_id:`persisted:${report.dataset}:${report.fund || 'all'}:${finding.code}:${report.recorded_at || ''}`,
        severity:finding.severity, dataset:report.dataset || 'unknown', fund:report.fund,
        message:finding.message_en || `Persisted validator finding (${finding.code}).`,
        remediation:'Review the quality report and source data before interpreting.',
        affected_records:finding.count, detected_at:report.recorded_at});
    }
  }
  const severity = params.get('severity') || '', dataset = params.get('dataset') || '', q = params.get('q') || '';
  const term = q.toLocaleLowerCase('en');
  const filtered = issues.filter(issue => (!severity || issue.severity === severity) && (!dataset || issue.dataset === dataset) && (!term || `${issue.message} ${issue.dataset} ${issue.fund || ''} ${issue.issue_id}`.toLocaleLowerCase('en').includes(term)));
  const sorted = applySortPage(filtered, params, 'severity');
  const source = summary.source_time_coverage || {};
  const pub = formatMissingRate(source.published_at_missing, source.observation_records, 'en');
  const ref = formatMissingRate(source.source_reference_missing, source.observation_records, 'en');
  const cards = `<div class="quality-grid">${metric('Datasets present in archive', integer(datasets.filter(d => ['available','empty'].includes(d.status)).length), `${datasets.length} dataset types checked`, '▤')}${metric('Datasets exceeding freshness SLA', integer(datasets.filter(d => d.freshness_assessment === 'stale').length), 'Only datasets with configured SLA are assessed', '◷')}${metric('Publication time missing', pub.value, pub.basis, '◷')}${metric('Source reference missing', ref.value, ref.basis, '↗')}</div>`;
  const rows = datasets.map(item => { const fresh = formatFreshness(item, 'en');
    const warnings = item.anomaly_warnings != null && item.structural_warnings != null ? `${integer(item.anomaly_warnings)}<span class="table-sub">${integer(item.structural_warnings)} structural</span>` : integer(item.validation_warnings);
    return `<tr><td><span class="mono">${esc(item.dataset)}</span></td><td>${badge(statusText(item.status, 'en'), statusTone(item.status))}</td><td class="numeric">${item.observed_rows == null ? 'Not available' : integer(item.observed_rows)}</td><td>${item.latest_date ? esc(dateText(item.latest_date)) : 'Not recorded'}</td><td>${badge(fresh.label, fresh.tone, fresh.detail)}</td><td>${esc(item.quality_status || 'Not assessed')}</td><td class="numeric">${integer(item.validation_errors)}</td><td class="numeric">${warnings}</td></tr>`; }).join('');
  const dataTable = datasets.length ? tableHtml([{key:'dataset',label:'Dataset',sortable:false},{key:'status',label:'Status',sortable:false},{key:'observed_rows',label:'Rows',numeric:true,sortable:false},{key:'latest_date',label:'Latest date',sortable:false},{key:'freshness_assessment',label:'Freshness',sortable:false},{key:'quality_status',label:'Validation',sortable:false},{key:'validation_errors',label:'Errors',numeric:true,sortable:false},{key:'validation_warnings',label:'Warnings (anomaly)',numeric:true,sortable:false}], rows) : emptyPanel('No inventory', 'Dataset metadata is not available in the export.', '◎');
  const choices = [...new Set(issues.map(issue => issue.dataset).filter(Boolean))].sort(COLLATOR.compare);
  const filters = `<form class="control-bar" data-filter-form><div class="field grow"><label for="quality-search">Search issues</label><input id="quality-search" name="q" type="search" value="${esc(q)}" placeholder="Dataset, fund, finding code"></div><div class="field"><label for="quality-severity">Severity</label><select id="quality-severity" name="severity"><option value="">All</option>${['error','warning','info'].map(v=>`<option value="${v}" ${severity===v?'selected':''}>${v}</option>`).join('')}</select></div><div class="field"><label for="quality-dataset">Dataset</label><select id="quality-dataset" name="dataset"><option value="">All datasets</option>${choices.map(v=>`<option value="${esc(v)}" ${dataset===v?'selected':''}>${esc(v)}</option>`).join('')}</select></div><button class="button primary" type="submit">Filter</button><button class="button" type="button" data-action="reset-filters">Reset</button></form>`;
  const issueRows = sorted.rows.map(issue => `<tr class="issue-row" aria-selected="${params.get('issue') === issue.issue_id}"><td><button class="issue-select" type="button" data-issue="${esc(issue.issue_id)}" aria-label="Open finding for ${esc(issue.dataset)}"><span class="issue-severity ${esc(issue.severity)}">${esc(issue.severity)}</span></button></td><td><span class="mono">${esc(issue.dataset)}</span>${issue.fund ? `<span class="table-sub">${esc(issue.fund)}</span>` : ''}</td><td>${esc(issue.message)}</td><td class="numeric">${issue.affected_records == null ? 'Unknown' : integer(issue.affected_records)}</td><td>${esc(issue.detected_at ? dateTimeText(issue.detected_at) : 'Not recorded')}</td></tr>`).join('');
  const issueTable = issues.length ? tableHtml([{key:'severity',label:'Severity'},{key:'dataset',label:'Dataset / fund'},{key:'message',label:'Explanation'},{key:'affected_records',label:'Records',numeric:true},{key:'detected_at',label:'Detection time'}], issueRows) + paginationHtml(sorted) : emptyPanel('No exported findings', 'Missing datasets are archive statuses, not proof of pipeline failure.', '◎');
  const selected = issues.find(issue => issue.issue_id === params.get('issue'));
  const detail = selected ? `<section class="panel issue-detail"><div class="panel-header"><div><h3>Finding details</h3><p>${esc(selected.issue_id)}</p></div><a class="small-link" href="?view=quality" data-route>Close</a></div><h4>${esc(selected.message)}</h4><p><strong>Severity:</strong> ${esc(selected.severity)} · <strong>Dataset:</strong> ${esc(selected.dataset)}${selected.fund ? ` · <strong>Fund:</strong> ${esc(selected.fund)}` : ''}</p><p><strong>Affected:</strong> ${selected.affected_records == null ? 'Unknown' : integer(selected.affected_records)} · <strong>Detected:</strong> ${esc(selected.detected_at ? dateTimeText(selected.detected_at) : 'Unknown')}</p><p><strong>Remediation:</strong> ${esc(selected.remediation)}</p></section>` : '';
  return `${pageIntro('DATA QUALITY', 'Data quality & coverage', 'Separates pipeline failures from source limitations. Findings describe files in the archive; freshness is assessed only with a configured SLA.')}${datasetStatusNotice(envelope(data, 'data_quality'), 'Quality ledger')}${notice('Missing is a distinct state', 'A missing dataset does not mean that the source returned no records and is never coerced to zero.', 'warning')}${cards}${panel('Coverage & validation by dataset', 'Latest date and age are observation metadata; publication time and missing values remain distinct.', dataTable)}<div class="section-heading"><div><h3>Issues and warnings</h3><p>${integer(issues.length)} issues in inventory and quality ledger.</p></div></div>${filters}${issueTable}${detail}`;
}

export function renderPipelineEnglish(data) {
  const status = envelope(data, 'pipeline_status').summary || {}, summary = status.latest_collector_summary, wf = status.workflow_status_record;
  const events = records(data, 'source_events'), source = envelope(data, 'source_events');
  const recent = events.slice().sort((a,b) => (b.completed_at || '').localeCompare(a.completed_at || '')).slice(0,20);
  const rows = recent.map(event => `<tr><td>${esc(dateTimeText(event.started_at))}</td><td>${esc(event.provider || 'Unknown')}</td><td>${esc(event.fund_code || '—')}</td><td><code>${esc(event.endpoint || 'No source reference')}</code></td><td>${badge(event.status || 'unknown', statusTone(event.status))}</td><td class="numeric">${event.http_status == null ? '—' : integer(event.http_status)}</td><td class="numeric">${event.record_count == null ? 'Unknown' : integer(event.record_count)}</td><td class="numeric">${event.retry_attempts == null ? '—' : integer(event.retry_attempts)}</td></tr>`).join('');
  const display = value => value == null ? 'Not available' : integer(value);
  const collector = summary ? `<div class="pipeline-counts"><div><span>Application date</span><strong>${esc(summary.date ? dateText(summary.date) : 'Not recorded')}</strong></div><div><span>Successful funds</span><strong>${display(summary.n_ok)}</strong></div><div><span>Failed / partial funds</span><strong>${display(summary.n_failed)} / ${display(summary.n_partial)}</strong></div><div><span>Quality warnings / errors</span><strong>${display(summary.n_quality_warnings)} / ${display(summary.n_quality_errors)}</strong></div><div><span>Observations appended</span><strong>${display(summary.n_provenance_observations)}</strong></div><div><span>Stored status / collector exit</span><strong>${wf ? esc(`${wf.conclusion}${wf.collector_exit_code != null ? ` (exit ${wf.collector_exit_code})` : ''} · budget ${wf.budget_status || 'unknown'}`) : 'Not exported / verified'}</strong></div></div>` : emptyPanel('No collector summary', 'last_run.json is absent; success cannot be inferred from other files.', '⌁');
  const outcomes = Object.entries(status.source_request_outcomes || {}).map(([key, count]) => `<div><span>${esc(key)}</span><strong>${integer(count)}</strong></div>`).join('') || '<div><span>Request outcomes</span><strong>Not available</strong></div>';
  const limited = source.truncated ? notice('Request history is limited', `The table includes the latest ${integer(events.length)} exported requests; ${display(status.source_request_count)} are recorded in the ledger.`, 'warning') : '';
  return `${pageIntro('PIPELINE HEALTH', 'Pipeline health', 'Recorded request outcomes do not prove that the enclosing GitHub Actions job completed successfully.')}${datasetStatusNotice(envelope(data, 'pipeline_status'), 'Pipeline metadata')}${datasetStatusNotice(source, 'Source request ledger')}${limited}<div class="pipeline-hero"><span class="pipeline-symbol" aria-hidden="true">${status.overall_workflow_status === 'success' ? '✓' : status.overall_workflow_status === 'failure' ? '!' : '?'}</span><div><h3>Ingestion workflow: ${esc(['success','failure','cancelled','skipped'].includes(status.overall_workflow_status) ? status.overall_workflow_status : 'unverified')}</h3><p>${esc(status.status_explanation_en || status.status_explanation || 'No sanitized workflow result is available.')}<br>Latest source request: ${esc(status.latest_source_request?.completed_at ? dateTimeText(status.latest_source_request.completed_at) : 'not recorded')}.</p><a class="actions-link" href="https://github.com/FTU-kudo/VN_Fund_Analysis/actions/workflows/daily.yml" target="_blank" rel="noreferrer">Open GitHub Actions workflow ↗</a></div></div><div class="pipeline-columns">${panel('Collector summary', 'Recorded last_run and sanitized workflow status, if available.', collector)}${panel('Source request outcomes', 'Only attempts stored in source_runs.csv are included.', `<div class="pipeline-counts">${outcomes}</div><p class="muted">Requests in ledger: ${display(status.source_request_count)}. No request bodies or credentials are exported.</p>`)}</div><div class="section-heading"><div><h3>Recent source requests</h3><p>Recorded request start/completion times; endpoints are sanitized.</p></div></div>${events.length ? tableHtml([{key:'started_at',label:'Started',sortable:false},{key:'provider',label:'Provider',sortable:false},{key:'fund_code',label:'Fund',sortable:false},{key:'endpoint',label:'Endpoint',sortable:false},{key:'status',label:'Outcome',sortable:false},{key:'http_status',label:'HTTP',numeric:true,sortable:false},{key:'record_count',label:'Rows',numeric:true,sortable:false},{key:'retry_attempts',label:'Retries',numeric:true,sortable:false}], rows) : emptyPanel('No recorded source requests', 'An absent request ledger does not prove that the source is down or empty.', '⌁')}`;
}

function renderQuality(data, params) {
  if (getLang(params) === 'en') return renderQualityEnglish(data, params);
  const summary = envelope(data, 'data_quality_summary').summary || {};
  const datasets = summary.datasets || [];
  const persisted = records(data, 'data_quality');
  const issues = [...(summary.issues || [])];
  for (const report of persisted) {
    for (const finding of report.findings || []) {
      if (!finding.severity || !finding.code) continue;
      issues.push({ issue_id: `persisted:${report.dataset}:${report.fund || 'all'}:${finding.code}:${report.recorded_at || ''}`,
        severity: finding.severity, dataset: report.dataset || 'unknown', fund: report.fund,
        message: `${finding.code} · kiểm tra validator gốc trong report đã lưu.`, message_vi: finding.message_vi, message_en: finding.message_en, affected_records: finding.count,
        detected_at: report.recorded_at, remediation: 'Mở quality report / source data và kiểm tra finding trước khi diễn giải.' });
    }
  }
  const lang = getLang(params);
  for (const issue of issues) {
    issue.message = issue[`message_${lang}`] || (lang === 'en' ? `Finding: ${issue.issue_id}` : issue.message);
    issue.remediation = issue[`remediation_${lang}`] || (lang === 'en' ? 'Review the source and validator before interpreting.' : issue.remediation);
  }
  const severity = params.get('severity') || '';
  const datasetFilter = params.get('dataset') || '';
  const q = params.get('q') || '';
  const term = q.toLocaleLowerCase('vi');
  const filtered = issues.filter((issue) => (!severity || issue.severity === severity) && (!datasetFilter || issue.dataset === datasetFilter)
    && (!term || `${issue.message} ${issue.dataset} ${issue.fund || ''} ${issue.issue_id}`.toLocaleLowerCase('vi').includes(term)));
  const sorted = applySortPage(filtered, params, 'severity');
  const body = sorted.rows.map((issue) => `<tr class="issue-row" aria-selected="${params.get('issue') === issue.issue_id}"><td><button class="issue-select" type="button" data-issue="${esc(issue.issue_id)}" aria-label="Mở chi tiết finding ${esc(issue.dataset)}"><span class="issue-severity ${esc(issue.severity)}">${issue.severity === 'error' ? '!' : issue.severity === 'warning' ? '△' : 'i'} ${esc(issue.severity)}</span></button></td><td><span class="mono">${esc(issue.dataset)}</span>${issue.fund ? `<span class="table-sub">${esc(issue.fund)}</span>` : ''}</td><td>${esc(issue.message)}</td><td class="numeric">${issue.affected_records == null ? 'Chưa biết' : integer(issue.affected_records)}</td><td>${esc(issue.detected_at ? dateTimeText(issue.detected_at) : 'Chưa ghi nhận')}</td></tr>`).join('');
  const selected = issues.find((issue) => issue.issue_id === params.get('issue'));
  const statusCounts = summary.source_time_coverage || {};
  const pubMissing = formatMissingRate(statusCounts.published_at_missing, statusCounts.observation_records, getLang());
  const srcRefMissing = formatMissingRate(statusCounts.source_reference_missing, statusCounts.observation_records, getLang());
  const staleDatasetCount = datasets.filter((row) => row.freshness_assessment === 'stale').length;
  const cards = `<div class="quality-grid">${metric('Dataset có trong archive', integer(datasets.filter((row) => ['available','empty'].includes(row.status)).length), `${datasets.length ? `${datasets.length} loại đã kiểm tra` : 'Inventory chưa có'}`, '▤')}${metric('Dataset vượt SLA độ mới', integer(staleDatasetCount), Object.keys(summary.freshness_sla_thresholds_days || {}).length ? 'Đánh giá trên các dataset có cấu hình SLA' : 'Chưa cấu hình SLA độ mới', '◷')}${metric('Publication time chưa có', pubMissing.value, pubMissing.basis, '◷')}${metric('Source reference thiếu', srcRefMissing.value, srcRefMissing.basis, '↗')}</div>`;
  const datasetBody = datasets.map((item) => {
    const fresh = formatFreshness(item, getLang());
    const anomWarn = item.anomaly_warnings;
    const structWarn = item.structural_warnings;
    const warnCell = anomWarn != null && structWarn != null
      ? `${integer(anomWarn)}<span class="table-sub">${integer(structWarn)} cấu trúc</span>`
      : integer(item.validation_warnings);
    return `<tr><td><span class="mono">${esc(item.dataset)}</span></td><td>${badge(statusText(item.status), statusTone(item.status))}</td><td class="numeric">${item.observed_rows == null ? 'Chưa có dữ liệu' : integer(item.observed_rows)}</td><td>${item.latest_date ? dateText(item.latest_date) : 'Chưa ghi nhận'}</td><td>${badge(fresh.label, fresh.tone, fresh.detail)}${item.age_days_at_reference != null ? `<span class="table-sub">${integer(item.age_days_at_reference)} ngày</span>` : ''}</td><td>${esc(item.quality_status || 'Chưa đánh giá')}</td><td class="numeric">${integer(item.validation_errors)}</td><td class="numeric">${warnCell}</td></tr>`;
  }).join('');
  const dataTable = datasets.length ? tableHtml([{key:'dataset',label:'Dataset',sortable:false},{key:'status',label:'Trạng thái',sortable:false},{key:'observed_rows',label:'Số dòng',numeric:true,sortable:false},{key:'latest_date',label:'Ngày cuối',sortable:false},{key:'age_days_at_reference',label:'Freshness / tuổi',sortable:false},{key:'quality_status',label:'Validation',sortable:false},{key:'validation_errors',label:'Errors',numeric:true,sortable:false},{key:'validation_warnings',label:'Warnings (bất thường)',numeric:true,sortable:false}], datasetBody, { className:'quality-table' }) : emptyPanel('Chưa có inventory','Export chưa có metadata dataset.','◎');
  const datasetOptions = [...new Set(issues.map((issue) => issue.dataset).filter(Boolean))].sort(COLLATOR.compare);
  const filters = `<form class="control-bar" data-filter-form><div class="field grow"><label for="quality-search">Tìm vấn đề</label><input id="quality-search" name="q" type="search" value="${esc(q)}" placeholder="Dataset, mã quỹ, finding code"></div><div class="field"><label for="quality-severity">Severity</label><select id="quality-severity" name="severity"><option value="">Tất cả</option>${['error','warning','info'].map((item)=>`<option value="${item}" ${severity===item?'selected':''}>${item}</option>`).join('')}</select></div><div class="field"><label for="quality-dataset">Dataset</label><select id="quality-dataset" name="dataset"><option value="">Tất cả dataset</option>${datasetOptions.map((item)=>`<option value="${esc(item)}" ${datasetFilter===item?'selected':''}>${esc(item)}</option>`).join('')}</select></div><button class="button primary" type="submit">Lọc</button><button class="button" type="button" data-action="reset-filters">Đặt lại</button></form>`;
  const issueTable = issues.length ? tableHtml([{key:'severity',label:'Severity'},{key:'dataset',label:'Dataset / fund'},{key:'message',label:'Giải thích'},{key:'affected_records',label:'Số bản ghi',numeric:true},{key:'detected_at',label:'Detection time'}], body, {className:'quality-table'}) + paginationHtml(sorted) : emptyPanel('Chưa có finding đã export','Nếu dataset thiếu, thiếu đó được liệt kê là trạng thái archive chứ không được xem pipeline failure.','◎');
  const issueDetail = selected ? `<section class="panel issue-detail"><div class="panel-header"><div><h3>Chi tiết finding</h3><p>${esc(selected.issue_id)}</p></div><a class="small-link" href="?view=quality" data-route>Đóng</a></div><h4>${esc(selected.message)}</h4><p><strong>Severity:</strong> ${esc(selected.severity)} · <strong>Dataset:</strong> ${esc(selected.dataset)}${selected.fund ? ` · <strong>Quỹ:</strong> ${esc(selected.fund)}` : ''}</p><p><strong>Ảnh hưởng:</strong> ${selected.affected_records == null ? 'Chưa xác định' : integer(selected.affected_records)} · <strong>Phát hiện:</strong> ${esc(selected.detected_at ? dateTimeText(selected.detected_at) : 'thời điểm này')}</p><p><strong>Hướng xử lý:</strong> ${esc(selected.remediation || 'Kiểm tra source và method trước khi dùng.')}</p></section>` : '';
  return `${pageIntro('DATA QUALITY', 'Chất lượng & độ phủ dữ liệu', 'Tách lỗi pipeline khỏi hạn chế source. Tuổi được hiển thị; stale không được gán khi chưa có SLA. Findings chỉ phản ánh file có trong archive.')}${datasetStatusNotice(envelope(data, 'data_quality'), 'Quality ledger')}${notice('Không có dữ liệu cũng là một trạng thái', 'Dataset missing không đồng nghĩa source trả về rỗng, và không được chuyển thành số 0 trong metric.', 'warning')}${cards}${panel('Coverage & validation theo dataset', 'Latest date/age là thông tin quan sát; publication time và dữ liệu thiếu được giữ nguyên.', dataTable)}<div class="section-heading"><div><h3>Vấn đề và cảnh báo</h3><p>${issues.length ? `${integer(issues.length)} issue trong manifest + quality ledger.` : 'Chưa có issue được ghi nhận.'}</p></div></div>${filters}${issueTable}${issueDetail}`;
}

function renderPipeline(data) {
  if (getLang() === 'en') return renderPipelineEnglish(data);
  const status = envelope(data, 'pipeline_status').summary || {};
  const summary = status.latest_collector_summary;
  const wf = status.workflow_status_record || null;
  const events = records(data, 'source_events');
  const outcomes = status.source_request_outcomes || {};
  const recent = events.slice().sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || '')).slice(0, 20);
  const body = recent.map((event) => `<tr><td>${esc(dateTimeText(event.started_at))}</td><td>${esc(event.provider || 'Chưa biết')}</td><td><span class="mono">${esc(event.fund_code || '—')}</span></td><td><code>${esc(event.endpoint || 'Source reference thiếu')}</code></td><td>${badge(event.status || 'unknown', statusTone(event.status))}</td><td class="numeric">${event.http_status == null ? '—' : integer(event.http_status)}</td><td class="numeric">${event.record_count == null ? 'Chưa biết' : integer(event.record_count)}</td><td class="numeric">${event.retry_attempts == null ? '—' : integer(event.retry_attempts)}</td></tr>`).join('');
  const wfExitText = wf
    ? `${wf.conclusion}${wf.collector_exit_code != null ? ` (exit ${wf.collector_exit_code})` : ''} · budget ${wf.budget_status || 'unknown'}`
    : 'Chưa được export / xác minh';
  const collectorCounts = summary ? `<div class="pipeline-counts"><div><span>Ngày ứng dụng ghi nhận</span><strong>${esc(summary.date ? dateText(summary.date) : 'Chưa ghi nhận')}</strong></div><div><span>Quỹ xử lý thành công</span><strong>${summary.n_ok == null ? 'Chưa có dữ liệu' : integer(summary.n_ok)}</strong></div><div><span>Quỹ lỗi / partial</span><strong>${summary.n_failed == null ? 'Chưa có dữ liệu' : integer(summary.n_failed)} / ${summary.n_partial == null ? 'Chưa có dữ liệu' : integer(summary.n_partial)}</strong></div><div><span>Quality warnings / errors</span><strong>${summary.n_quality_warnings == null ? 'Chưa có dữ liệu' : integer(summary.n_quality_warnings)} / ${summary.n_quality_errors == null ? 'Chưa có dữ liệu' : integer(summary.n_quality_errors)}</strong></div><div><span>Observations được append</span><strong>${summary.n_provenance_observations == null ? 'Chưa có dữ liệu' : integer(summary.n_provenance_observations)}</strong></div><div><span>Trạng thái đã lưu / exit collector</span><strong>${esc(wfExitText)}</strong></div></div>` : emptyPanel('Chưa có collector summary', 'last_run.json chưa có trong archive. Không suy thành công từ các tệp khác.', '⌁');
  const outcomeRows = Object.entries(outcomes).map(([key, count]) => `<div><span>${esc(key)}</span><strong>${integer(count)}</strong></div>`).join('') || `<div><span>Request outcomes</span><strong>Chưa có dữ liệu</strong></div>`;
  const pipelineNotice = datasetStatusNotice(envelope(data, 'pipeline_status'), 'Pipeline metadata');
  const sourceEventsEnvelope = envelope(data, 'source_events');
  const sourceEventsNotice = datasetStatusNotice(sourceEventsEnvelope, 'Source request ledger');
  const sourceLimitNotice = sourceEventsEnvelope.truncated
    ? notice('Lịch sử request được giới hạn', `Bảng chỉ hiển thị ${integer(events.length)} request gần nhất; ledger đã lưu ${status.source_request_count == null ? 'chưa xác định' : integer(status.source_request_count)} request.`, 'warning')
    : '';
  const wfTitleMap = { success: 'thành công (sanitized record)', failure: 'thất bại (sanitized record)', partial: 'một phần', cancelled: 'đã hủy' };
  const heroTitle = wfTitleMap[status.overall_workflow_status] ? `Trạng thái workflow ingest: ${wfTitleMap[status.overall_workflow_status]}` : 'Trạng thái workflow ingest: chưa xác minh';
  const heroSymbol = status.overall_workflow_status === 'success' ? '✓' : status.overall_workflow_status === 'failure' ? '!' : '?';
  return `${pageIntro('PIPELINE HEALTH', 'Sức khỏe pipeline', 'Request outcomes được lưu có thể xác minh từng lần gọi, nhưng không chứng minh toàn bộ GitHub Actions job hoàn tất thành công.')}${pipelineNotice}${sourceEventsNotice}${sourceLimitNotice}
    <div class="pipeline-hero"><span class="pipeline-symbol" aria-hidden="true">${heroSymbol}</span><div><h3>${esc(heroTitle)}</h3><p>${esc(status[`status_explanation_${getLang()}`] || status.status_explanation || 'Không có sanitized GitHub Actions run metadata trong archive hiện tại.')}<br>Latest source request: ${esc(status.latest_source_request?.completed_at ? dateTimeText(status.latest_source_request.completed_at) : 'chưa ghi nhận')}.</p><a class="actions-link" href="https://github.com/FTU-kudo/VN_Fund_Analysis/actions/workflows/daily.yml" target="_blank" rel="noreferrer">Mở GitHub Actions workflow ↗</a></div></div>
    <div class="pipeline-columns">${panel('Collector summary (chỉ số được ghi)', 'Bản ghi last_run kết hợp workflow_status.json đã sanitize (nếu có).', collectorCounts)}${panel('Source request outcomes', 'Chỉ các request đã ghi vào source_runs.csv; request lịch sử chưa ghi fund_code sẽ giữ là —.', `<div class="pipeline-counts">${outcomeRows}</div><p class="muted">Số request trong ledger: ${status.source_request_count == null ? 'Chưa có dữ liệu' : integer(status.source_request_count)} · Không có body hoặc credential trong export.</p>`)}</div>
    <div class="section-heading"><div><h3>Source requests gần nhất</h3><p>Timestamp là request start/completion đã lưu; endpoint được sanitize.</p></div></div>${events.length ? tableHtml([{key:'started_at',label:'Bắt đầu',sortable:false},{key:'provider',label:'Provider',sortable:false},{key:'fund_code',label:'Mã quỹ',sortable:false},{key:'endpoint',label:'Endpoint',sortable:false},{key:'status',label:'Kết quả',sortable:false},{key:'http_status',label:'HTTP',numeric:true,sortable:false},{key:'record_count',label:'Rows',numeric:true,sortable:false},{key:'retry_attempts',label:'Retries',numeric:true,sortable:false}], body) : emptyPanel('Chưa có source request event','source_runs.csv không có request đã lưu. Đây không phải bằng chứng nguồn đang down hoặc rỗng.','⌁')}`;
}

function renderMethodology() {
  if (getLang() === 'en') return renderMethodologyEnglish();
  return `${pageIntro('METHODOLOGY', 'Phương pháp & giới hạn', 'Các nhãn và metric phải giữ đúng semantics của pipeline. Khi dữ liệu hoặc phương pháp chưa đủ, giao diện ưu tiên nói “chưa có” thay vì điền giả định.')}
    <div class="methodology-grid">
      <article class="method-card"><h3>NAV observation ≠ publication</h3><p>Ngày trong nav_snapshots.csv là ngày collector lưu một quan sát NAV; không chứng minh ngày NAV có hiệu lực. nav.csv là ngày theo chuỗi nguồn; publication timestamp chưa được xác minh. Hai series hiển thị riêng, không gộp hoặc nội suy.</p></article>
      <article class="method-card"><h3>Holdings snapshot ≠ giao dịch</h3><p>Snapshot mô tả điều source đã báo cáo tại thời điểm/kỳ gắn kèm. Các trường volume/weight không phải transaction blotter. Missing value luôn giữ là chưa biết, không chuyển thành 0.</p></article>
      <article class="method-card"><h3>UNCONFIRMED? và WEIGHT_CHANGE?</h3><ul><li><code>UNCONFIRMED?</code>: thiếu mã chung đủ dữ liệu để tách thay đổi khối lượng khỏi quy mô quỹ.</li><li><code>WEIGHT_CHANGE?</code>: chỉ có tỷ trọng; biến động giá, dòng vốn và corporate actions chưa được tách.</li><li>BUY/SELL là suy luận volume + median scale proxy, không phải giao dịch xác nhận.</li></ul></article>
      <article class="method-card"><h3>Top-N coverage & censoring</h3><p>Danh sách top-N không phải full portfolio. Mã rời top-N có thể chỉ giảm thứ hạng hoặc vượt ra ngoài phạm vi dữ liệu; không kết luận bán hết. Full holdings do importer nhận từ factsheet được gắn là nguồn khai báo, chưa independently verified.</p></article>
      <article class="method-card"><h3>Corporate action</h3><p><code>CORP_ACTION?</code> là cờ heuristic khi nhiều quỹ có biến động gần nhau trong lookback. Cần đối chiếu với lịch quyền chính thức; không tự điều chỉnh thành transaction event.</p></article>
      <article class="method-card"><h3>NAV comparison</h3><p>Khi có hai quỹ cùng nhóm và ít nhất hai ngày NAV nguồn trùng chính xác, dashboard hiển thị biến động NAV đơn giản trên khoảng chung. Không nội suy, không dùng observed snapshots, không gọi đây là total return; không điều chỉnh phân phối, benchmark, risk hoặc phí.</p></article>
      <article class="method-card"><h3>Freshness / stale</h3><p>Giao diện hiển thị last date, age và đánh giá SLA độ mới trên các dataset đã được phê duyệt ngưỡng trong <code>config.toml</code> (45d cho holdings/allocation, 3d cho nav_snapshots, 180d cho fees); các dataset khác giữ trạng thái <code>not_assessed_no_sla</code>. Ngày date-only không được đổi thành thời điểm PIT chính xác.</p></article>
      <article class="method-card"><h3>Pipeline outcome</h3><p>Source request thành công hoặc collector counter không tự chứng minh GitHub Actions job success. Status tổng thể chỉ hiển thị khi có <code>workflow_status.json</code> đã sanitize (không chứa token/log thô); trang luôn liên kết tới Actions để kiểm chứng trực tiếp.</p></article>
      <article class="method-card"><h3>Stock research &amp; Yuanta 3-exchange lake</h3><p>Mục Nghiên cứu mã kết hợp sở hữu quỹ mở (Fmarket) với gói tĩnh <code>dashboard/data/yuanta/stocks/{SYMBOL}.json.gz</code> quét từ 3 sàn HOSE/HNX/UPCOM (Trang 1 Tổng quan, Trang 2 Giao dịch, Trang 3 Tương quan ngành 3 khối tách BĐS khỏi Tài chính L1, Trang 4 BCTC Q1/2010–nay, Trang 5 Tin tức &amp; sự kiện). Trình duyệt chỉ đọc tệp tĩnh, không gọi API live; điểm Stock Rating được ghi rõ là dữ liệu bên thứ ba của Yuanta.</p></article>
      <article class="method-card"><h3>Missingness & verification</h3><p>Missing dataset ≠ source trả về rỗng; unknown ≠ zero. Quality status chỉ mô tả validator cấu trúc/giá trị; verification status độc lập cho biết nguồn đã hay chưa được kiểm chứng ngoài pipeline.</p></article>
    </div>
    <div class="method-callout">Không có Smart Money Score, alpha claim, ranking đầu tư hay khuyến nghị mua/bán trong dashboard này. Kiểm tra terms/rate limits của provider trước khi bật collection live.</div>`;
}

export function renderMethodologyEnglish() {
  return `${pageIntro('METHODOLOGY', 'Methodology & limitations', 'Data and metrics retain the semantics of the pipeline; unknown values remain unknown.')}
  <div class="methodology-grid">
  <article class="method-card"><h3>NAV observation ≠ publication</h3><p>nav_snapshots.csv records observation dates, not verified effective dates. nav.csv uses source NAV dates; publication time is unknown. Series are not merged or interpolated.</p></article>
  <article class="method-card"><h3>Holdings snapshots ≠ transactions</h3><p>Reported volumes and weights are not a transaction blotter. Missing values remain unknown, not zero.</p></article>
  <article class="method-card"><h3>Inferred and unconfirmed changes</h3><p>BUY/SELL are volume-based inferences adjusted using a median fund-scale proxy, not confirmed trades. UNCONFIRMED? and WEIGHT_CHANGE? do not establish trades. PRORATA is a heuristic, not a trading decision.</p></article>
  <article class="method-card"><h3>Top-N censorship</h3><p>Top-N is not the full portfolio. Entering or leaving the list does not prove a new position or a complete exit. Source-claimed full holdings are not independently verified.</p></article>
  <article class="method-card"><h3>Corporate actions</h3><p>CORP_ACTION? is an unverified heuristic; check official announcements before interpreting it.</p></article>
  <article class="method-card"><h3>NAV comparison</h3><p>Only funds in the same category with at least two exact common NAV source dates are compared. Simple NAV change is not total return, benchmark-adjusted alpha, or investment advice. Distributions, benchmark, risk and fees are not adjusted.</p></article>
  <article class="method-card"><h3>Time and freshness</h3><p>observed_date, effective_date/report_date, observed_at, published_at and ingested_at are distinct. Unknown publication times are not inferred. Freshness uses configured SLAs; datasets without thresholds are not assessed.</p></article>
  <article class="method-card"><h3>Pipeline status</h3><p>Individual request results and collector counters do not establish workflow success. A sanitized workflow_status.json is required.</p></article>
  <article class="method-card"><h3>Yuanta stock research</h3><p>HOSE, HNX and UPCOM static Yuanta YSradar bundles cover overview, trading, peer correlation, financials and events. Real Estate is separated from ICB Level 1 Financials and compared as a Non-Financial subsector. Stock Rating and related scores are third-party Yuanta data, not recommendations.</p></article>
  <article class="method-card"><h3>Missingness and verification</h3><p>A missing dataset does not prove a source is empty. Structural validation and independent source verification are distinct.</p></article>
  </div><div class="method-callout">No Smart Money Score, investment ranking, alpha claim or buy/sell recommendation is provided. Review provider terms and rate limits before live collection.</div>`;
}

function renderCurrent() {
  if (!window.__VNFA_DATA) {
    if (window.__VNFA_LOAD_ERROR) renderLoadError(window.__VNFA_LOAD_ERROR);
    return;
  }
  const app = document.getElementById('app');
  const params = queryParams();
  const view = PAGE_TITLES[currentPage()] ? currentPage() : 'overview';
  setPageChrome(view);
  const data = window.__VNFA_DATA;
  let html;
  switch (view) {
    case 'funds': html = renderFunds(data, params); break;
    case 'holdings': html = renderHoldings(data, params); break;
    case 'stocks': html = renderStocks(data, params); break;
    case 'compare': html = renderCompare(data, params); break;
    case 'quality': html = renderQuality(data, params); break;
    case 'pipeline': html = renderPipeline(data); break;
    case 'methodology': html = renderMethodology(); break;
    default: html = renderOverview(data);
  }
  app.innerHTML = html;
  // Keep language in actual hrefs as well as click navigation (copy/open in new tab).
  document.querySelectorAll('a[data-route]').forEach(link => {
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    url.searchParams.set('lang', getLang(params));
    link.setAttribute('href', `${url.pathname}${url.search}${url.hash}`);
  });
  app.setAttribute('aria-busy', 'false');
  document.getElementById('footer-generated').textContent = t(getLang(params), 'footer_generated', {time: dateTimeText(data.manifest?.generated_at), schema: data.manifest?.schema_version || 'unknown'});
  if (view === 'stocks') {
    const selTicker = (params.get('ticker') || '').trim().toUpperCase();
    const stab = params.get('stab') || (data.yuantaUniverse?.status === 'available' ? 'overview' : 'funds');
    if (selTicker && stab !== 'funds') {
      window.__YUANTA_BUNDLES = window.__YUANTA_BUNDLES || new Map();
      if (!window.__YUANTA_BUNDLES.has(selTicker)) {
        window.__YUANTA_BUNDLES.set(selTicker, { status: 'loading', symbol: selTicker, bundle: null });
        loadYuantaStockBundle(selTicker).then((entry) => {
          window.__YUANTA_BUNDLES.set(selTicker, entry);
          if ((queryParams().get('ticker') || '').trim().toUpperCase() === selTicker) {
            renderCurrent();
          }
        });
      }
    }
  }
  const selected = params.get('fund') ? document.getElementById('fund-detail') : params.get('ticker') ? document.querySelector('.stock-detail') : null;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  selected?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
}

async function bootstrap() {
  const app = document.getElementById('app');
  const alert = document.getElementById('load-alert');
  app.setAttribute('aria-busy', 'true');
  try {
    clearYuantaCache();
    window.__YUANTA_BUNDLES = new Map();
    const [loaded, yuantaUniverse] = await Promise.all([
      loadDashboardData(),
      loadYuantaUniverse(),
    ]);
    loaded.yuantaUniverse = yuantaUniverse;
    window.__VNFA_DATA = loaded;
    alert.hidden = true;
    window.__VNFA_LOAD_ERROR = null;
    renderCurrent();
  } catch (error) {
    window.__VNFA_DATA = null;
    window.__VNFA_LOAD_ERROR = error?.message || 'manifest_unavailable';
    renderLoadError(window.__VNFA_LOAD_ERROR);
  }
}

function renderLoadError(code) {
  const app = document.getElementById('app');
  const alert = document.getElementById('load-alert');
  const en = getLang() === 'en';
  app.setAttribute('aria-busy', 'false');
  const detail = code === 'manifest_unsupported_schema'
    ? (en ? 'The manifest schema version is not supported. Update the UI and exporter before deployment.' : 'Phiên bản schema của manifest chưa được UI hỗ trợ. Hãy cập nhật UI/exporter tương thích trước khi deploy.')
    : (en ? 'The manifest is missing or malformed. Run scripts/export_dashboard.py to create browser-readable artifacts; no data is hardcoded.' : 'Manifest chưa có hoặc sai định dạng. Hãy chạy scripts/export_dashboard.py để tạo browser-readable artifacts; website không có dữ liệu hardcoded.');
  app.innerHTML = `<section class="panel">${notice(en ? 'Dashboard data could not be loaded' : 'Không tải được dashboard data', detail, 'error')}<button class="button" type="button" data-action="reload">${en ? 'Retry' : 'Thử lại'}</button></section>`;
  alert.hidden = false;
  alert.textContent = en ? 'The static export may be missing or the Pages path may be incorrect.' : 'Có thể đang xem bản tĩnh chưa build hoặc Pages path chưa đúng.';

}

function navigate(url, replace = false) {
  if (replace) history.replaceState({}, '', url);
  else history.pushState({}, '', url);
  renderCurrent();
}

if (typeof document !== 'undefined') {
  setPageChrome(currentPage());
  document.addEventListener('click', (event) => {
    const switcher = event.target.closest('[data-lang-switch]');
    if (switcher) {
      const lang = setLang(switcher.dataset.langSwitch);
      const params = queryParams();
      params.set('lang', lang);
      history.replaceState({}, '', `${window.location.pathname}?${params}${window.location.hash}`);
      setPageChrome(currentPage());
      renderCurrent();
      return;
    }
    const route = event.target.closest('a[data-route]');
    if (route && !event.metaKey && !event.ctrlKey && !event.shiftKey && route.target !== '_blank') {
      event.preventDefault();
      const url = new URL(route.href, window.location.href);
      if (!url.searchParams.has('lang')) url.searchParams.set('lang', getLang());
      navigate(`${url.pathname}${url.search}${url.hash}`);
      return;
    }
    const sort = event.target.closest('[data-sort]');
    if (sort) {
      const params = queryParams();
      const key = sort.dataset.sort;
      const direction = params.get('sort') === key && params.get('dir') !== 'desc' ? 'desc' : 'asc';
      updateParams({ sort: key, dir: direction, page: 1 });
      return;
    }
    const page = event.target.closest('[data-page]');
    if (page && !page.disabled) { updateParams({ page: page.dataset.page }); return; }
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (action === 'reload') { bootstrap(); return; }
    if (action === 'clear-incompatible-compare') {
      const params = queryParams();
      const cleaned = sanitizeCompareSelection(records(window.__VNFA_DATA || { datasets: {} }, 'funds'), params.get('category') || '', params.get('a') || '', params.get('b') || '');
      updateParams(cleaned);
      return;
    }
    if (action === 'reset-filters') {
      const params = queryParams();
      const keep = { view: params.get('view') || 'overview', lang: getLang(params) };
      if (params.get('kind')) keep.kind = params.get('kind');
      navigate(`${window.location.pathname}?${new URLSearchParams(keep).toString()}`);
      return;
    }
    const watch = event.target.closest('[data-watch]');
    if (watch) {
      const items = readWatchlist(); const ticker = watch.dataset.watch;
      saveWatchlist(items.includes(ticker) ? items.filter((item) => item !== ticker) : [...items, ticker]);
      renderCurrent();
      return;
    }
    const remove = event.target.closest('[data-watch-remove]');
    if (remove) {
      event.preventDefault();
      saveWatchlist(readWatchlist().filter((item) => item !== remove.dataset.watchRemove));
      renderCurrent();
      return;
    }
    const issue = event.target.closest('[data-issue]');
    if (issue) { updateParams({ issue: issue.dataset.issue }, { replace: true }); }
  });

  document.addEventListener('change', (event) => {
    const compareCat = event.target.closest('#compare-category');
    if (!compareCat || !window.__VNFA_DATA) return;
    const form = compareCat.closest('form');
    const currentA = form?.querySelector('[name="a"]')?.value ?? queryParams().get('a') ?? '';
    const currentB = form?.querySelector('[name="b"]')?.value ?? queryParams().get('b') ?? '';
    const cleaned = sanitizeCompareSelection(records(window.__VNFA_DATA, 'funds'), compareCat.value, currentA, currentB);
    updateParams(cleaned);
  });

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-filter-form]');
    if (!form) return;
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    if (currentPage() === 'compare' && window.__VNFA_DATA) {
      Object.assign(values, sanitizeCompareSelection(records(window.__VNFA_DATA, 'funds'), values.category || '', values.a || '', values.b || ''));
    }
    values.page = 1;
    if ('tfrom' in values || 'tto' in values) values.tpage = 1;
    if ('eq' in values) values.epage = 1;
    values.sort = null;
    values.dir = null;
    values.issue = null;
    updateParams(values);
  });
  window.addEventListener('popstate', () => { setPageChrome(currentPage()); renderCurrent(); });
  bootstrap();
}
