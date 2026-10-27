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
const DATE_FORMAT = new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
const DATETIME_FORMAT = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Ho_Chi_Minh' });

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
function statusText(status) {
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
function topNCoverageBadge(coverageType) {
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
  return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: digits }).format(Number(value));
}
function integer(value) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return 'Chưa có dữ liệu';
  return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(Number(value));
}
function percentFraction(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return new Intl.NumberFormat('vi-VN', { style: 'percent', maximumFractionDigits: digits }).format(Number(value));
}
function percentPoints(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return `${number(value, digits)}%`;
}
export function signedPercentFraction(value, digits = 2) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return new Intl.NumberFormat('vi-VN', {
    style: 'percent',
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  }).format(n).replace(/^-/, '−');
}
export function signedPercentPoints(value, digits = 2) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  const formatted = new Intl.NumberFormat('vi-VN', {
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  }).format(n).replace(/^-/, '−');
  return `${formatted}%`;
}
export function formatMissingRate(missingCount, totalCount) {
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
export function formatFreshness(item = {}) {
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
  if (!value) return 'Chưa có dữ liệu';
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const parsed = new Date(dateOnly ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(parsed.getTime())) return 'Không xác định';
  return dateOnly ? DATE_FORMAT.format(parsed) : DATETIME_FORMAT.format(parsed);
}
function dateTimeText(value) { return value ? dateText(value) : 'Chưa ghi nhận'; }
function categoryText(code) {
  return ({ STOCK: 'Cổ phiếu', BALANCED: 'Cân bằng', BOND: 'Trái phiếu / MMF' })[code] || (code ? `Khác · ${code}` : 'Chưa phân loại');
}
function sourceLink(value) {
  if (!value) return '<span class="muted">Không có source reference</span>';
  try {
    const url = new URL(value, window.location.href);
    if (['https:', 'http:'].includes(url.protocol) && url.origin !== window.location.origin) {
      return `<a class="small-link" href="${esc(url.href)}" target="_blank" rel="noreferrer">Mở nguồn ↗</a>`;
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
    const label = header.sortable === false ? esc(header.label) : `<button type="button" data-sort="${esc(header.key)}" aria-label="Sắp xếp theo ${esc(header.label)}">${esc(header.label)}${arrow}</button>`;
    return `<th class="${header.numeric ? 'numeric' : ''}" aria-sort="${aria}">${label}</th>`;
  }).join('');
  return `<div class="table-wrap ${className}"><table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${headers.length}" class="no-results">Không có bản ghi phù hợp với bộ lọc.</td></tr>`}</tbody></table></div>`;
}
function paginationHtml(result) {
  const start = result.total === 0 ? 0 : (result.page - 1) * result.pageSize + 1;
  const end = Math.min(result.page * result.pageSize, result.total);
  return `<div class="pagination"><span>Hiển thị ${start}–${end} / ${integer(result.total)} bản ghi</span><div class="pagination-actions"><button class="button" type="button" data-page="${result.page - 1}" ${result.page <= 1 ? 'disabled' : ''}>← Trước</button><span class="pagination-current">Trang ${result.page} / ${result.totalPages}</span><button class="button" type="button" data-page="${result.page + 1}" ${result.page >= result.totalPages ? 'disabled' : ''}>Sau →</button></div></div>`;
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
  const title = PAGE_TITLES[view] || PAGE_TITLES.overview;
  document.title = `${title} — VN Fund Research`;
  document.getElementById('page-title').textContent = title;
  document.querySelectorAll('[data-view]').forEach((link) => {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

function renderOverview(data) {
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
    const fresh = formatFreshness(item);
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
      ${metric('Portfolio events trong export', events.record_count == null ? 'Chưa có dữ liệu' : integer(events.record_count), 'Quan sát/suy luận; không phải nhật ký lệnh xác nhận', '⇄')}
      ${metric('Cảnh báo chất lượng', warningValue, 'Finding ghi nhận trong inventory; không đồng nghĩa pipeline thất bại', '◎')}
      ${metric('Pipeline job status', pipelineStatus, `Request gần nhất: ${activity}`, '⌁')}
      ${metric('Giao dịch đã xác nhận', 'Chưa có dữ liệu', 'Không có transaction-level blotter trong project hiện tại', '—')}
    </div>
    <div class="overview-grid">
      ${datasetsPanel}
      <div class="stacked-panels">
        ${panel('Lối tắt nghiên cứu', 'Mở một view dữ liệu, không phải tín hiệu giao dịch.', `<div class="shortcut-list"><a href="?view=funds" data-route>Khám phá quỹ <span>→</span></a><a href="?view=holdings" data-route>Xem sự kiện danh mục <span>→</span></a><a href="?view=stocks" data-route>Nghiên cứu theo mã <span>→</span></a><a href="?view=quality" data-route>Kiểm tra chất lượng dữ liệu <span>→</span></a></div>`)}
        ${panel('Giới hạn hiện tại', 'Những gì dashboard không thể kết luận.', `<ul class="compact-list"><li>Không có dữ liệu công ty, định giá hoặc giá thị trường.</li><li>Thay đổi holdings không chứng minh giao dịch.</li><li>Fmarket/mirror chưa được xác minh độc lập.</li><li>Trạng thái GitHub Actions không có trong archive hiện tại.</li></ul>`)}
      </div>
    </div>`;
}

function renderFunds(data, params) {
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
  const eventBody = events.map((row) => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(row.display_label || row.action, 'warning')}</td><td>${esc(row.flag || 'Không có cờ')}</td></tr>`).join('');
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

function renderNavChart(rows) {
  const history = rows.filter((row) => row.series_type === 'nav_history' && Number.isFinite(row.nav) && row.nav > 0).sort((a, b) => a.date.localeCompare(b.date));
  const snapshots = rows.filter((row) => row.series_type === 'nav_snapshot' && Number.isFinite(row.nav) && row.nav > 0).sort((a, b) => a.date.localeCompare(b.date));
  if (!history.length && !snapshots.length) return `<div class="chart-empty">Chưa có NAV để vẽ.<br>Không tạo chuỗi minh họa.</div>`;
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
  const histPoints = history.map((row) => `<circle cx="${x(row.date)}" cy="${y(row.nav)}" r="2.6" fill="#247f7a"><title>${esc(row.date)} · ${esc(number(row.nav, 2))} · ngày nguồn, publication time chưa biết</title></circle>`).join('');
  const snapshotPath = snapshots.length > 1 ? `<path d="${pathFor(snapshots)}" fill="none" stroke="#8698a3" stroke-width="1.6" stroke-dasharray="5 4"/>` : '';
  const snapshotPoints = snapshots.map((row) => `<circle cx="${x(row.date)}" cy="${y(row.nav)}" r="3" fill="#fff" stroke="#748994" stroke-width="1.5"><title>${esc(row.date)} · ${esc(number(row.nav, 2))} · ngày quan sát; ngày hiệu lực chưa biết</title></circle>`).join('');
  const firstDate = dates[0]; const lastDate = dates.at(-1);
  return `<div class="chart-box"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Biểu đồ NAV gồm ${history.length} điểm chuỗi nguồn và ${snapshots.length} điểm snapshot quan sát; các chuỗi giữ riêng semantics">${grid}${histPath}${histPoints}${snapshotPath}${snapshotPoints}<text x="${pad.left}" y="${height - 5}" fill="#7f8e97" font-size="9">${esc(firstDate)}</text><text x="${width - pad.right}" y="${height - 5}" text-anchor="end" fill="#7f8e97" font-size="9">${esc(lastDate)}</text></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>NAV theo ngày nguồn · publication time chưa biết</span><span class="legend-item"><span class="legend-line snapshot"></span>Snapshot ngày quan sát · effective date chưa biết</span></div><p class="chart-caption">Các điểm là dữ liệu thực sự có trong archive, không phải chuỗi daily đầy đủ. Khoảng trống không được nội suy; NAV change không đồng nghĩa total return.</p></div>`;
}

function renderHoldings(data, params) {
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
    return `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund || '')}" data-route>${esc(row.fund || '—')}</a></td><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker || '')}" data-route>${esc(row.ticker || '—')}</a></td><td>${badge(row.display_label || row.action, row.evidence_status === 'unconfirmed' ? 'warning' : 'neutral')}</td><td>${esc(row.basis || 'Chưa biết')}</td><td class="numeric">${esc(signedPercentFraction(row.weight_delta))}</td><td class="numeric">${esc(row.volume_change_pct == null ? '—' : signedPercentPoints(row.volume_change_pct * 100))}</td><td>${esc(row.flag || 'Không có cờ')}</td><td>${row.censored ? badge('Top-N', 'warning', 'Vào/rời Top-N không chứng minh mua mới hoặc bán hết') : badge(row.evidence_status === 'inferred_not_transaction' ? 'Suy luận' : 'Chưa xác nhận', 'neutral')}</td><td><a class="small-link" href="${href}" data-route>Chi tiết</a></td></tr>`;
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
  const table = active ? tableHtml(headers, body, { sort: sorted.key, direction: sorted.direction }) + paginationHtml(sorted) : emptyPanel(mode === 'events' ? 'Chưa có portfolio event' : 'Chưa có holdings snapshot', 'Dữ liệu hiện có trong export không đủ để dựng bảng này. Không coi missing position là zero.', '∅');
  const selectedEvent = params.get('event_id');
  const eventDetails = selectedEvent && mode === 'events' ? renderEventDetail(allEvents.find((event) => event.event_id === selectedEvent)) : '';
  const modeEnvelope = envelope(data, mode === 'events' ? 'portfolio_events' : 'holdings');
  return `${pageIntro('HOLDINGS & PORTFOLIO CHANGES', 'Danh mục & thay đổi', 'Tách snapshot được báo cáo khỏi sự kiện suy luận. Không quy đổi thiếu dữ liệu, top-N exit hoặc weight change thành giao dịch xác nhận.')}${datasetStatusNotice(modeEnvelope, mode === 'events' ? 'Journal sự kiện' : 'Snapshot holdings')}
    <div class="tab-strip"><a href="?view=holdings&kind=events" data-route class="${mode === 'events' ? 'active' : ''}" ${mode === 'events' ? 'aria-current="page"' : ''}>Sự kiện danh mục <span>${allEvents.length ? integer(allEvents.length) : '—'}</span></a><a href="?view=holdings&kind=holdings" data-route class="${mode === 'holdings' ? 'active' : ''}" ${mode === 'holdings' ? 'aria-current="page"' : ''}>Snapshot holdings <span>${allHoldings.length ? integer(allHoldings.length) : '—'}</span></a></div>
    ${mode === 'events' ? notice('Không phải nhật ký giao dịch', 'BUY/SELL là suy luận từ volume và proxy quy mô; UNCONFIRMED? và WEIGHT_CHANGE? vẫn chưa xác nhận. Vào/rời top-N không chứng minh mua mới hoặc bán hết.', 'warning') : ''}
    ${filterBar}${panel(mode === 'events' ? 'Journal sự kiện quan sát / suy luận' : 'Snapshot holdings mới nhất', mode === 'events' ? 'Các nhãn gốc được giữ, kèm phân loại tiếng Việt trung tính.' : 'Mỗi fund/source chỉ có snapshot mới nhất trong export; coverage được gắn nhãn riêng.', table)}${eventDetails}`;
}

function eventTypeLabel(classification) {
  return ({ position_increase_inferred: 'Tăng vị thế (suy luận)', position_decrease_inferred: 'Giảm vị thế (suy luận)',
    pro_rata_change: 'Biến động đồng loạt (heuristic)', unconfirmed_volume_change: 'Thay đổi khối lượng (chưa xác nhận)',
    unconfirmed_weight_change: 'Thay đổi tỷ trọng (chưa xác nhận)', entered_top_n: 'Xuất hiện trong top-N',
    left_top_n: 'Rời top-N', possible_corporate_action: 'Nghi vấn corporate action', unclassified: 'Chưa phân loại' })[classification] || classification || 'Chưa phân loại';
}
function renderEventDetail(event) {
  if (!event) return `<section class="panel event-detail">${notice('Không tìm thấy sự kiện', 'Event key không tồn tại trong export hiện tại.', 'warning')}</section>`;
  const why = eventWhy(event);
  return `<section class="panel event-detail"><div class="panel-header"><div><h3>Chi tiết sự kiện · ${esc(event.ticker || '—')} · ${esc(event.fund || '—')}</h3><p>${esc(event.display_label || event.action)} · ${esc(event.observed_date ? dateText(event.observed_date) : 'Ngày chưa biết')}</p></div><a class="small-link" href="?view=holdings&kind=events" data-route>Đóng</a></div><div class="detail-grid"><div class="method-callout">${esc(why)}</div><dl class="detail-list"><div><dt>Basis</dt><dd>${esc(event.basis || 'Chưa rõ')}</dd></div><div><dt>Cờ</dt><dd>${esc(event.flag || 'Không có')}</dd></div><div><dt>Top-N censored</dt><dd>${event.censored ? 'Có — phạm vi bị kiểm duyệt' : 'Không được gắn cờ censored'}</dd></div><div><dt>Transaction-level</dt><dd>Không có bằng chứng xác nhận</dd></div><div><dt>Source</dt><dd>${sourceLink(event.source_reference)}</dd></div></dl></div></section>`;
}
function eventWhy(event) {
  if (event.action === 'UNCONFIRMED?') return 'Khối lượng có thay đổi nhưng số mã chung đủ dữ liệu không đạt ngưỡng để ước lượng proxy quy mô quỹ; vì vậy không xác nhận tăng/giảm vị thế chủ động.';
  if (event.action === 'WEIGHT_CHANGE?') return 'Chỉ có tỷ trọng; thay đổi có thể do giá, dòng vốn hoặc corporate action. Pipeline không gán MUA/BÁN từ tỷ trọng đơn lẻ.';
  if (event.action === 'NEW_IN_TOP10' || event.action === 'EXIT_TOP10') return 'Nguồn chỉ lưu top-N. Mã đi vào/rời danh sách không cho biết giao dịch ngoài phạm vi top-N; không suy diễn mua mới hoặc bán hết.';
  if (event.action === 'CORP_ACTION?') return 'Cờ là heuristic từ biến động tương tự giữa nhiều quỹ. Chưa được đối chiếu với lịch corporate action chính thức.';
  if (event.action === 'PRORATA') return 'Biến động gần proxy quy mô quỹ; không xem như quyết định giao dịch hay quan điểm đầu tư.';
  if (event.action === 'BUY' || event.action === 'SELL') return 'Nhãn BUY/SELL trong journal là suy luận dựa trên khối lượng và median fund-scale proxy; không phải blotter, lệnh hay transaction đã xác nhận.';
  return 'Không có giải thích phân loại cụ thể; cần kiểm tra dữ liệu nguồn và quy tắc pipeline trước khi diễn giải.';
}

function stockRecords(data) {
  const holdings = records(data, 'holdings');
  const events = records(data, 'portfolio_events');
  const map = new Map();
  for (const item of [...holdings, ...events]) {
    if (!item.ticker) continue;
    if (!map.has(item.ticker)) map.set(item.ticker, { ticker: item.ticker, funds: new Set(), event_count: 0, latest_date: null, coverage: new Set() });
    const row = map.get(item.ticker);
    if (item.fund) row.funds.add(item.fund);
    if (item.event_id) row.event_count += 1;
    const date = item.observed_date;
    if (date && (!row.latest_date || date > row.latest_date)) row.latest_date = date;
    if (item.coverage_type) row.coverage.add(item.coverage_type);
  }
  return [...map.values()].map((row) => ({ ...row, funds: [...row.funds].sort(COLLATOR.compare), coverage: [...row.coverage] })).sort((a, b) => COLLATOR.compare(a.ticker, b.ticker));
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
function renderStocks(data, params) {
  const q = (params.get('q') || '').trim().toUpperCase();
  const selected = (params.get('ticker') || '').trim().toUpperCase();
  const allStocks = stockRecords(data);
  const filtered = allStocks.filter((row) => !q || row.ticker.includes(q));
  const sorted = applySortPage(filtered, params, 'ticker');
  const body = sorted.rows.map((row) => `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(row.ticker)}" data-route>${esc(row.ticker)}</a></td><td>${row.funds.map((fund) => `<a class="small-link mono fund-pill" href="?view=funds&fund=${encodeURIComponent(fund)}" data-route>${esc(fund)}</a>`).join(' ') || '—'}</td><td class="numeric">${row.event_count ? integer(row.event_count) : 'Chưa có event'}</td><td>${row.latest_date ? dateText(row.latest_date) : 'Ngày chưa biết'}</td><td>${row.coverage.map((kind) => kind === 'top_n_partial_censored' ? 'Top-N · censored' : 'Source claimed full').map(esc).join(', ') || 'Chưa xác định'}</td></tr>`).join('');
  const watchlist = readWatchlist();
  const fundRows = records(data, 'holdings').filter((row) => row.ticker === selected);
  const eventRows = records(data, 'portfolio_events').filter((row) => row.ticker === selected).sort((a, b) => (b.observed_date || '').localeCompare(a.observed_date || ''));
  const currentSelected = allStocks.find((row) => row.ticker === selected);
  const selectedView = selected ? `<section class="panel stock-detail"><div class="panel-header"><div><div class="stock-ticker">${esc(selected)}</div><p>Reported fund ownership · không phải tín hiệu mua/bán</p></div><button class="button watch-button" type="button" data-watch="${esc(selected)}" aria-pressed="${watchlist.includes(selected)}">${watchlist.includes(selected) ? '✓ Đang theo dõi' : '+ Thêm watchlist'}</button></div>
    ${currentSelected ? `<p class="muted">Xuất hiện trong ${integer(currentSelected.funds.length)} quỹ có dữ liệu đã export. Ngày mới nhất: ${esc(currentSelected.latest_date ? dateText(currentSelected.latest_date) : 'chưa biết')}.</p>` : notice('Chưa có bằng chứng ownership', `Không tìm thấy ${selected} trong holdings hoặc event export.`, 'warning')}
    ${fundRows.length ? tableHtml([{ key: 'fund', label: 'Quỹ', sortable: false }, { key: 'weight', label: 'Tỷ trọng báo cáo', numeric: true, sortable: false }, { key: 'volume', label: 'Khối lượng', numeric: true, sortable: false }, { key: 'observed_date', label: 'Ngày snapshot', sortable: false }, { key: 'effective_date', label: 'Ngày nguồn', sortable: false }, { key: 'coverage_type', label: 'Độ phủ', sortable: false }, { key: 'source_reference', label: 'Source', sortable: false }], fundRows.map((row) => `<tr><td><a class="fund-link mono" href="?view=funds&fund=${encodeURIComponent(row.fund)}" data-route>${esc(row.fund)}</a></td><td class="numeric">${esc(percentFraction(row.weight))}</td><td class="numeric">${esc(integer(row.volume))}</td><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${esc(row.effective_date ? dateText(row.effective_date) : 'Chưa biết')}</td><td>${esc(row.coverage_type)}</td><td>${sourceLink(row.source_reference)}</td></tr>`).join('')) : emptyPanel('Chưa có holdings record', 'Không có snapshot mã này trong export. Không suy missing thành zero.', '⌕')}
    <div class="section-heading"><div><h3>Portfolio events</h3><p>Chỉ sự kiện quan sát / suy luận của pipeline.</p></div></div>${eventRows.length ? tableHtml([{ key: 'observed_date', label: 'Ngày', sortable: false }, { key: 'fund', label: 'Quỹ', sortable: false }, { key: 'display_label', label: 'Phân loại', sortable: false }, { key: 'basis', label: 'Basis', sortable: false }, { key: 'flag', label: 'Cờ', sortable: false }], eventRows.map((row) => `<tr><td>${esc(row.observed_date ? dateText(row.observed_date) : 'Chưa biết')}</td><td>${esc(row.fund)}</td><td>${badge(row.display_label, 'warning')}</td><td>${esc(row.basis || '—')}</td><td>${esc(row.flag || '—')}</td></tr>`).join('')) : emptyPanel('Chưa có event', 'Không có sự kiện được lưu cho mã này.', '⇄')}</section>` : '';
  const watchHtml = watchlist.length ? `<div class="watchlist">${watchlist.map((ticker) => `<span class="watch-chip"><a href="?view=stocks&ticker=${encodeURIComponent(ticker)}" data-route>${esc(ticker)}</a><button type="button" data-watch-remove="${esc(ticker)}" aria-label="Xóa ${esc(ticker)} khỏi watchlist">×</button></span>`).join('')}</div>` : `<p class="muted">Watchlist lưu trong localStorage của trình duyệt này; chưa có mã theo dõi.</p>`;
  const stockDataNotice = `${datasetStatusNotice(envelope(data, 'holdings'), 'Holdings')}${datasetStatusNotice(envelope(data, 'portfolio_events'), 'Portfolio events')}`;
  return `${pageIntro('STOCK RESEARCH', 'Nghiên cứu mã', 'Bằng chứng hiện có chỉ là mã được báo cáo trong holdings và các thay đổi do pipeline phát hiện. Không có fundamentals, định giá, giá thị trường hoặc transaction-level data.')}${stockDataNotice}
    <div class="stock-layout"><section class="panel"><div class="panel-header"><div><h3>Mã xuất hiện trong dữ liệu quỹ</h3><p>${allStocks.length ? `${integer(allStocks.length)} mã có record trong export.` : 'Chưa có mã được export.'}</p></div></div><form class="control-bar" data-filter-form><div class="field grow"><label for="stock-search">Tìm ticker</label><input id="stock-search" name="q" type="search" value="${esc(q)}" placeholder="VD: FPT"></div><button class="button primary" type="submit">Tìm</button><button class="button" type="button" data-action="reset-filters">Đặt lại</button></form>${allStocks.length ? tableHtml([{ key: 'ticker', label: 'Ticker' }, { key: 'funds', label: 'Quỹ báo cáo' }, { key: 'event_count', label: 'Event count', numeric: true }, { key: 'latest_date', label: 'Ngày cuối' }, { key: 'coverage', label: 'Độ phủ' }], body) + paginationHtml(sorted) : emptyPanel('Chưa có stock evidence', 'Danh mục chưa có trong archive. Không tạo mã ví dụ.', '⌕')}</section>
    <div class="stacked-panels">${panel('Watchlist cá nhân', 'Lưu cục bộ trên thiết bị hiện tại; không gửi lên server.', watchHtml)}${panel('Dữ liệu chưa có', 'Không tự động lấp các phần thiếu bằng giả định.', `<div class="unavailable-grid"><div class="unavailable-item"><strong>Company fundamentals</strong><span>Chưa có nguồn / dataset.</span></div><div class="unavailable-item"><strong>Valuation / target price</strong><span>Không được tính.</span></div><div class="unavailable-item"><strong>Confirmed transactions</strong><span>Không có blotter.</span></div></div>`)}</div></div>${selectedView}`;
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
function renderNormalizedCompareChart(result) {
  const series = [result.seriesA.map((row) => ({ ...row, label: 'Quỹ A', color: '#247f7a' })), result.seriesB.map((row) => ({ ...row, label: 'Quỹ B', color: '#748a98' }))];
  const values = series.flat().map((row) => row.normalized); const min = Math.min(...values); const max = Math.max(...values);
  const width = 720; const height = 210; const p = { l: 48, r: 14, t: 12, b: 29 }; const w = width-p.l-p.r; const h = height-p.t-p.b;
  const x = (i) => p.l + (result.dates.length < 2 ? w/2 : i/(result.dates.length-1)*w);
  const y = (v) => p.t + (max===min ? h/2 : (max-v)/(max-min)*h);
  const parts = series.map((rows) => `<path d="${rows.map((row,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(row.normalized).toFixed(1)}`).join(' ')}" fill="none" stroke="${rows[0].color}" stroke-width="2.2" ${rows[0].label === 'Quỹ B' ? 'stroke-dasharray="5 4"' : ''}/>`).join('');
  return `<div class="chart-box"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Biểu đồ NAV đã chuẩn hóa về 100 tại ngày chung đầu tiên; không phải total return"><line x1="${p.l}" x2="${width-p.r}" y1="${height-p.b}" y2="${height-p.b}" stroke="#e1e7e9"/>${parts}<text x="${p.l}" y="${height-7}" fill="#84929a" font-size="9">${esc(result.start)}</text><text x="${width-p.r}" y="${height-7}" text-anchor="end" fill="#84929a" font-size="9">${esc(result.end)}</text></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>Quỹ A · chuẩn hóa = 100</span><span class="legend-item"><span class="legend-line snapshot"></span>Quỹ B · chuẩn hóa = 100</span></div></div>`;
}

function renderQuality(data, params) {
  const summary = envelope(data, 'data_quality_summary').summary || {};
  const datasets = summary.datasets || [];
  const persisted = records(data, 'data_quality');
  const issues = [...(summary.issues || [])];
  for (const report of persisted) {
    for (const finding of report.findings || []) {
      if (!finding.severity || !finding.code) continue;
      issues.push({ issue_id: `persisted:${report.dataset}:${report.fund || 'all'}:${finding.code}:${report.recorded_at || ''}`,
        severity: finding.severity, dataset: report.dataset || 'unknown', fund: report.fund,
        message: `${finding.code} · kiểm tra validator gốc trong report đã lưu.`, affected_records: finding.count,
        detected_at: report.recorded_at, remediation: 'Mở quality report / source data và kiểm tra finding trước khi diễn giải.' });
    }
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
  const pubMissing = formatMissingRate(statusCounts.published_at_missing, statusCounts.observation_records);
  const srcRefMissing = formatMissingRate(statusCounts.source_reference_missing, statusCounts.observation_records);
  const staleDatasetCount = datasets.filter((row) => row.freshness_assessment === 'stale').length;
  const cards = `<div class="quality-grid">${metric('Dataset có trong archive', integer(datasets.filter((row) => ['available','empty'].includes(row.status)).length), `${datasets.length ? `${datasets.length} loại đã kiểm tra` : 'Inventory chưa có'}`, '▤')}${metric('Dataset vượt SLA độ mới', integer(staleDatasetCount), Object.keys(summary.freshness_sla_thresholds_days || {}).length ? 'Đánh giá trên các dataset có cấu hình SLA' : 'Chưa cấu hình SLA độ mới', '◷')}${metric('Publication time chưa có', pubMissing.value, pubMissing.basis, '◷')}${metric('Source reference thiếu', srcRefMissing.value, srcRefMissing.basis, '↗')}</div>`;
  const datasetBody = datasets.map((item) => {
    const fresh = formatFreshness(item);
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
  const collectorCounts = summary ? `<div class="pipeline-counts"><div><span>Ngày ứng dụng ghi nhận</span><strong>${esc(summary.date ? dateText(summary.date) : 'Chưa ghi nhận')}</strong></div><div><span>Quỹ xử lý thành công</span><strong>${summary.n_ok == null ? 'Chưa có dữ liệu' : integer(summary.n_ok)}</strong></div><div><span>Quỹ lỗi / partial</span><strong>${summary.n_failed == null ? 'Chưa có dữ liệu' : integer(summary.n_failed)} / ${summary.n_partial == null ? 'Chưa có dữ liệu' : integer(summary.n_partial)}</strong></div><div><span>Quality warnings / errors</span><strong>${summary.n_quality_warnings == null ? 'Chưa có dữ liệu' : integer(summary.n_quality_warnings)} / ${summary.n_quality_errors == null ? 'Chưa có dữ liệu' : integer(summary.n_quality_errors)}</strong></div><div><span>Observations được append</span><strong>${summary.n_provenance_observations == null ? 'Chưa có dữ liệu' : integer(summary.n_provenance_observations)}</strong></div><div><span>Exit status của workflow</span><strong>${esc(wfExitText)}</strong></div></div>` : emptyPanel('Chưa có collector summary', 'last_run.json chưa có trong archive. Không suy thành công từ các tệp khác.', '⌁');
  const outcomeRows = Object.entries(outcomes).map(([key, count]) => `<div><span>${esc(key)}</span><strong>${integer(count)}</strong></div>`).join('') || `<div><span>Request outcomes</span><strong>Chưa có dữ liệu</strong></div>`;
  const pipelineNotice = datasetStatusNotice(envelope(data, 'pipeline_status'), 'Pipeline metadata');
  const sourceEventsEnvelope = envelope(data, 'source_events');
  const sourceEventsNotice = datasetStatusNotice(sourceEventsEnvelope, 'Source request ledger');
  const sourceLimitNotice = sourceEventsEnvelope.truncated
    ? notice('Lịch sử request được giới hạn', `Bảng chỉ hiển thị ${integer(events.length)} request gần nhất; ledger đã lưu ${status.source_request_count == null ? 'chưa xác định' : integer(status.source_request_count)} request.`, 'warning')
    : '';
  const wfTitleMap = { success: 'thành công (sanitized record)', failure: 'thất bại (sanitized record)', partial: 'một phần', cancelled: 'đã hủy' };
  const heroTitle = wf ? `Trạng thái workflow ingest: ${wfTitleMap[wf.conclusion] || wf.conclusion}` : 'Trạng thái workflow ingest: chưa xác minh';
  const heroSymbol = wf?.conclusion === 'success' ? '✓' : wf?.conclusion === 'failure' ? '!' : '?';
  return `${pageIntro('PIPELINE HEALTH', 'Sức khỏe pipeline', 'Request outcomes được lưu có thể xác minh từng lần gọi, nhưng không chứng minh toàn bộ GitHub Actions job hoàn tất thành công.')}${pipelineNotice}${sourceEventsNotice}${sourceLimitNotice}
    <div class="pipeline-hero"><span class="pipeline-symbol" aria-hidden="true">${heroSymbol}</span><div><h3>${esc(heroTitle)}</h3><p>${esc(status.status_explanation || 'Không có sanitized GitHub Actions run metadata trong archive hiện tại.')}<br>Latest source request: ${esc(status.latest_source_request?.completed_at ? dateTimeText(status.latest_source_request.completed_at) : 'chưa ghi nhận')}.</p><a class="actions-link" href="https://github.com/FTU-kudo/VN_Fund_Analysis/actions/workflows/daily.yml" target="_blank" rel="noreferrer">Mở GitHub Actions workflow ↗</a></div></div>
    <div class="pipeline-columns">${panel('Collector summary (chỉ số được ghi)', 'Bản ghi last_run kết hợp workflow_status.json đã sanitize (nếu có).', collectorCounts)}${panel('Source request outcomes', 'Chỉ các request đã ghi vào source_runs.csv; request lịch sử chưa ghi fund_code sẽ giữ là —.', `<div class="pipeline-counts">${outcomeRows}</div><p class="muted">Số request trong ledger: ${status.source_request_count == null ? 'Chưa có dữ liệu' : integer(status.source_request_count)} · Không có body hoặc credential trong export.</p>`)}</div>
    <div class="section-heading"><div><h3>Source requests gần nhất</h3><p>Timestamp là request start/completion đã lưu; endpoint được sanitize.</p></div></div>${events.length ? tableHtml([{key:'started_at',label:'Bắt đầu',sortable:false},{key:'provider',label:'Provider',sortable:false},{key:'fund_code',label:'Mã quỹ',sortable:false},{key:'endpoint',label:'Endpoint',sortable:false},{key:'status',label:'Kết quả',sortable:false},{key:'http_status',label:'HTTP',numeric:true,sortable:false},{key:'record_count',label:'Rows',numeric:true,sortable:false},{key:'retry_attempts',label:'Retries',numeric:true,sortable:false}], body) : emptyPanel('Chưa có source request event','source_runs.csv không có request đã lưu. Đây không phải bằng chứng nguồn đang down hoặc rỗng.','⌁')}`;
}

function renderMethodology() {
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
      <article class="method-card"><h3>Stock research</h3><p>Fund ownership và thay đổi báo cáo là bằng chứng holdings, không phải buy signal. Repository hiện không cung cấp fundamentals, valuation, target price, market price hoặc transaction-level records; các module đó được đánh dấu chưa khả dụng.</p></article>
      <article class="method-card"><h3>Missingness & verification</h3><p>Missing dataset ≠ source trả về rỗng; unknown ≠ zero. Quality status chỉ mô tả validator cấu trúc/giá trị; verification status độc lập cho biết nguồn đã hay chưa được kiểm chứng ngoài pipeline.</p></article>
    </div>
    <div class="method-callout">Không có Smart Money Score, alpha claim, ranking đầu tư hay khuyến nghị mua/bán trong dashboard này. Kiểm tra terms/rate limits của provider trước khi bật collection live.</div>`;
}

function renderCurrent() {
  if (!window.__VNFA_DATA) return;
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
  app.setAttribute('aria-busy', 'false');
  document.getElementById('footer-generated').textContent = `Export: ${dateTimeText(data.manifest?.generated_at)} · schema ${data.manifest?.schema_version || 'unknown'}`;
  const selected = params.get('fund') ? document.getElementById('fund-detail') : params.get('ticker') ? document.querySelector('.stock-detail') : null;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  selected?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
}

async function bootstrap() {
  const app = document.getElementById('app');
  const alert = document.getElementById('load-alert');
  app.setAttribute('aria-busy', 'true');
  try {
    const loaded = await loadDashboardData();
    window.__VNFA_DATA = loaded;
    alert.hidden = true;
    renderCurrent();
  } catch (error) {
    app.setAttribute('aria-busy', 'false');
    const detail = error?.message === 'manifest_unsupported_schema'
      ? 'Phiên bản schema của manifest chưa được UI hỗ trợ. Hãy cập nhật UI/exporter tương thích trước khi deploy.'
      : 'Manifest chưa có hoặc sai định dạng. Hãy chạy scripts/export_dashboard.py để tạo browser-readable artifacts; website không có dữ liệu hardcoded.';
    app.innerHTML = `<section class="panel">${notice('Không tải được dashboard data', detail, 'error')}<button class="button" type="button" data-action="reload">Thử lại</button></section>`;
    alert.hidden = false;
    alert.textContent = 'Có thể đang xem bản tĩnh chưa build hoặc Pages path chưa đúng.';
  }
}

function navigate(url, replace = false) {
  if (replace) history.replaceState({}, '', url);
  else history.pushState({}, '', url);
  renderCurrent();
}

if (typeof document !== 'undefined') {
  document.addEventListener('click', (event) => {
    const route = event.target.closest('a[data-route]');
    if (route && !event.metaKey && !event.ctrlKey && !event.shiftKey && route.target !== '_blank') {
      event.preventDefault();
      const url = new URL(route.href, window.location.href);
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
      const keep = { view: params.get('view') };
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
    values.sort = null;
    values.dir = null;
    values.issue = null;
    updateParams(values);
  });
  window.addEventListener('popstate', renderCurrent);
  bootstrap();
}
