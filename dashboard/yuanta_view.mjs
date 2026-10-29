import {
  DEFAULT_LANG,
  formatInteger,
  formatDate,
  formatNumber,
  formatSignedPercentPoints,
  normalizeLang,
  translateChartSeriesLabel,
  translateChartTitle,
  translateCorrelationMetric,
  translateFinancialComponent,
  translateFinancialField,
  translateFinancialUnit,
  translateMacroGroup,
  translateSubGroup,
  withLangHref,
} from './i18n.mjs';

const TICKER_RE = /^[A-Z0-9]{2,5}$/;
const BUNDLE_CACHE = new Map();
let UNIVERSE_CACHE = null;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmtNum(val, digits = 2, lang = DEFAULT_LANG) {
  return formatNumber(val, digits, lang);
}

function fmtInt(val, lang = DEFAULT_LANG) {
  if (val === null || val === undefined || val === '' || !Number.isFinite(Number(val))) return '—';
  return formatInteger(val, lang, '—');
}

function fmtSignedPct(val, digits = 2, lang = DEFAULT_LANG) {
  return formatSignedPercentPoints(val, digits, lang);
}

export function clearYuantaCache() {
  BUNDLE_CACHE.clear();
  UNIVERSE_CACHE = null;
}

export function resolveYuantaUrl(relPath, moduleUrl = import.meta.url) {
  const clean = String(relPath || '').replace(/^\/+/, '');
  if (!/^[A-Za-z0-9_./-]+$/.test(clean) || clean.includes('..')) {
    throw new Error('invalid_yuanta_path');
  }
  return new URL(`./data/yuanta/${clean}`, moduleUrl).href;
}

async function decodeGzipOrJsonResponse(response) {
  const encoding = (response.headers?.get?.('content-encoding') || '').toLowerCase();
  if (encoding.includes('gzip') || typeof response.arrayBuffer !== 'function') {
    return response.json();
  }
  const buf = await response.arrayBuffer();
  const bytes = new Uint8Array(buf);
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream === 'function') {
      const ds = new DecompressionStream('gzip');
      const stream = new Blob([bytes]).stream().pipeThrough(ds);
      const text = await new Response(stream).text();
      return JSON.parse(text);
    }
    throw new Error('gzip_decompression_unavailable');
  }
  const text = new TextDecoder('utf-8').decode(bytes);
  return JSON.parse(text);
}

export async function loadYuantaUniverse(fetcher = globalThis.fetch, moduleUrl = import.meta.url) {
  if (UNIVERSE_CACHE) return UNIVERSE_CACHE;
  if (typeof fetcher !== 'function') return { status: 'unavailable', count: 0, stocks: [] };
  try {
    const resp = await fetcher(resolveYuantaUrl('universe.json', moduleUrl), { cache: 'no-store' });
    if (!resp || !resp.ok) {
      UNIVERSE_CACHE = { status: resp?.status === 404 ? 'not_synced' : 'unavailable', count: null, stocks: [] };
      return UNIVERSE_CACHE;
    }
    const payload = await resp.json();
    if (!payload || !Array.isArray(payload.stocks)) {
      UNIVERSE_CACHE = { status: 'malformed', count: 0, stocks: [] };
      return UNIVERSE_CACHE;
    }
    UNIVERSE_CACHE = { status: 'available', ...payload };
    return UNIVERSE_CACHE;
  } catch (error) {
    UNIVERSE_CACHE = { status: error instanceof SyntaxError ? 'malformed' : 'unavailable', count: null, stocks: [] };
    return UNIVERSE_CACHE;
  }
}

export async function loadYuantaStockBundle(symbol, fetcher = globalThis.fetch, moduleUrl = import.meta.url) {
  const sym = String(symbol || '').trim().toUpperCase();
  if (!TICKER_RE.test(sym)) return { status: 'invalid_symbol', symbol: sym, bundle: null };
  if (BUNDLE_CACHE.has(sym)) return BUNDLE_CACHE.get(sym);
  if (typeof fetcher !== 'function') return { status: 'unavailable', symbol: sym, bundle: null };

  let failure = null;
  for (const candidate of [`stocks/${sym}.json.gz`, `stocks/${sym}.json`]) {
    try {
      const resp = await fetcher(resolveYuantaUrl(candidate, moduleUrl), { cache: 'no-store' });
      if (!resp || !resp.ok) { if (resp?.status !== 404) failure = 'unavailable'; continue; }
      const parsed = candidate.endsWith('.gz') ? await decodeGzipOrJsonResponse(resp) : await resp.json();
      if (parsed && typeof parsed === 'object' && parsed.symbol === sym) {
        const entry = { status: 'available', symbol: sym, bundle: parsed };
        BUNDLE_CACHE.set(sym, entry);
        return entry;
      }
      failure = 'malformed';
    } catch (error) {
      failure = error instanceof SyntaxError ? 'malformed' : 'unavailable';
      /* try plain json fallback */
    }
  }
  const missing = { status: failure || 'not_synced', symbol: sym, bundle: null };
  BUNDLE_CACHE.set(sym, missing);
  return missing;
}

function renderNotSyncedNotice(symbol, lang = DEFAULT_LANG) {
  if (lang === 'en') {
    return `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>No static Yuanta bundle synced yet for ${esc(symbol)}</strong><p>This static research browser never calls external APIs live. To scan all 3 exchanges (HOSE, HNX, UPCOM — Pages 1–5) and generate <code>dashboard/data/yuanta/stocks/${esc(symbol)}.json.gz</code>, run the CLI scanner or trigger the GitHub Actions workflow:</p><p><code>python scripts/scan_yuanta_universe.py --exchanges HOSE,HNX,UPCOM</code> (or quick single-ticker test: <code>python scripts/scan_yuanta_universe.py --tickers ${esc(symbol)}</code>)</p></div></div>`;
  }
  return `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Chưa có gói dữ liệu tĩnh Yuanta cho mã ${esc(symbol)}</strong><p>Môi trường trình duyệt chỉ đọc tệp tĩnh nội bộ (không gọi trực tiếp API ngoài). Để quét toàn bộ 3 sàn HOSE, HNX, UPCOM (Trang 1–5) và sinh tệp <code>dashboard/data/yuanta/stocks/${esc(symbol)}.json.gz</code>, chạy lệnh CLI hoặc bật GitHub Actions workflow:</p><p><code>python scripts/scan_yuanta_universe.py --exchanges HOSE,HNX,UPCOM</code> (hoặc kiểm thử nhanh: <code>python scripts/scan_yuanta_universe.py --tickers ${esc(symbol)}</code>)</p></div></div>`;
}

function renderPage1Overview(bundle, lang = DEFAULT_LANG) {
  const en = lang === 'en';
  const m = bundle.master || {};
  const ov = bundle.page1_overview || {};
  const pInfo = (Array.isArray(ov.profile_info) ? ov.profile_info[0] : ov.profile_info) || {};
  const rInfo = (Array.isArray(ov.stock_rating_info) ? ov.stock_rating_info[0] : ov.stock_rating_info) || {};
  const sInfo = (Array.isArray(ov.stock_info) ? ov.stock_info[0] : ov.stock_info) || {};
  const bidOffer = (Array.isArray(ov.last_bid_offer) ? ov.last_bid_offer[0] : ov.last_bid_offer) || {};
  const shareholders = Array.isArray(ov.shareholders) ? ov.shareholders : (ov.shareholders?.data || ov.shareholders?.List || []);
  const leaders = Array.isArray(pInfo.Leaders || pInfo.Management || pInfo.BoardOfDirectors)
    ? (pInfo.Leaders || pInfo.Management || pInfo.BoardOfDirectors) : [];
  const txs = Array.isArray(ov.list_transaction) ? ov.list_transaction : [];
  const peersRaw = Array.isArray(ov.peers) ? ov.peers : (ov.peers?.Peers || ov.peers?.Peer || ov.peers?.data || []);
  const industryPe = ov.peers?.IndustryPE ?? ov.peers?.AveragePE ?? ov.peers?.PE ?? rInfo.IndustryPE ?? null;

  const macroLabel = translateMacroGroup(m.macro_group, m.macro_group_label, lang);
  const subLabel = translateSubGroup(m.sub_group, m.sub_group_label || m.sector_name, lang);

  const splitBadge = m.split_from_financial_l1
    ? (en
      ? `<span class="table-status warning" title="Real Estate sits under ICB Level 1 Financials (8000) but is explicitly separated into Non-Financials so inventory/project cycles do not distort Bank or Brokerage peer medians">· Split from ICB L1 Financials</span>`
      : `<span class="table-status warning" title="Bất động sản thuộc Tài chính (ICB L1) nhưng được tách sang khối Phi tài chính để không làm sai lệch trung vị Ngân hàng / Chứng khoán">· Tách khỏi Tài chính L1</span>`)
    : '';

  const summaryCards = `<div class="metric-grid yuanta-metrics">
    <article class="metric-card"><div class="metric-label"><span>${en ? 'Last / Reference Price' : 'Giá khớp / Tham chiếu'}</span></div><div class="metric-value">${esc(fmtNum(m.last_price ?? sInfo.LastP ?? rInfo.CurrentPrice, 2, lang))}</div><div class="metric-basis">${en ? 'Ref' : 'TC'}: ${esc(fmtNum(m.ref_price ?? sInfo.RefP, 2, lang))} · ${en ? 'Exchange' : 'Sàn'} ${esc(m.exchange || '—')}</div></article>
    <article class="metric-card"><div class="metric-label"><span>${en ? 'Valuation P/E · P/B' : 'Định giá P/E · P/B'}</span></div><div class="metric-value">${esc(fmtNum(m.pe, 2, lang))}x · ${esc(fmtNum(m.pb, 2, lang))}x</div><div class="metric-basis">${en ? 'Industry P/E' : 'P/E ngành'}: ${esc(fmtNum(industryPe, 2, lang))}x</div></article>
    <article class="metric-card"><div class="metric-label"><span>ROE · ROA</span></div><div class="metric-value">${esc(fmtNum(m.roe, 2, lang))}% · ${esc(fmtNum(m.roa, 2, lang))}%</div><div class="metric-basis">${en ? 'Market cap' : 'Vốn hóa'}: ${esc(m.market_cap != null ? fmtInt(m.market_cap, lang) : '—')}</div></article>
    <article class="metric-card"><div class="metric-label"><span>Yuanta Stock Rating</span></div><div class="metric-value">${esc(fmtNum(m.stock_rating ?? sInfo.StockRating ?? rInfo.StockRatingPoint, 0, lang))}</div><div class="metric-basis">${en ? 'Fundamental' : 'Cơ bản'}: ${esc(fmtNum(m.fundamental_score ?? sInfo.FundamentalPoint, 0, lang))} · RS: ${esc(fmtNum(m.technical_score ?? sInfo.TechnicalPoint, 0, lang))} (${en ? 'third-party Yuanta score' : 'nguồn Yuanta'})</div></article>
  </div>`;

  const companyBio = pInfo.Overview || pInfo.BusinessSummary || pInfo.Introduction || rInfo.Description
    || (en ? 'No detailed company description in this static snapshot.' : 'Chưa có mô tả chi tiết doanh nghiệp trong snapshot.');

  const bidRows = [1, 2, 3].map((lvl) => {
    const bp = bidOffer[`BidPrice${lvl}`] ?? bidOffer[`Best${lvl}Bid`] ?? bidOffer[`B${lvl}P`] ?? sInfo[`BidPrice${lvl}`];
    const bv = bidOffer[`BidVol${lvl}`] ?? bidOffer[`Best${lvl}BidVolume`] ?? bidOffer[`B${lvl}V`] ?? sInfo[`BidVol${lvl}`];
    const op = bidOffer[`OfferPrice${lvl}`] ?? bidOffer[`Best${lvl}Offer`] ?? bidOffer[`S${lvl}P`] ?? sInfo[`OfferPrice${lvl}`];
    const ovVol = bidOffer[`OfferVol${lvl}`] ?? bidOffer[`Best${lvl}OfferVolume`] ?? bidOffer[`S${lvl}V`] ?? sInfo[`OfferVol${lvl}`];
    return `<tr><td>${en ? `Level ${lvl}` : `Mức ${lvl}`}</td><td class="numeric">${esc(fmtNum(bp, 2, lang))}</td><td class="numeric">${esc(fmtInt(bv, lang))}</td><td class="numeric">${esc(fmtNum(op, 2, lang))}</td><td class="numeric">${esc(fmtInt(ovVol, lang))}</td></tr>`;
  }).join('');

  const txRows = txs.slice(0, 15).map((tx) => `<tr><td>${esc(tx.TradingTime ?? tx.Time ?? tx.MatchedTime ?? '—')}</td><td class="numeric">${esc(fmtNum(tx.MatchPrice ?? tx.MatchedPrice ?? tx.Price, 2, lang))}</td><td class="numeric">${esc(fmtInt(tx.MatchVolume ?? tx.MatchedVolume ?? tx.Volume, lang))}</td><td>${esc(tx.Side || tx.MatchType || '—')}</td></tr>`).join('');

  const shRows = shareholders.slice(0, 15).map((sh) => `<tr><td>${esc(sh.Name || sh.ShareHolderName || sh.FullName || '—')}</td><td class="numeric">${esc(fmtInt(sh.Quantity ?? sh.Shares, lang))}</td><td class="numeric">${esc(fmtNum(sh.OwnershipPercentage ?? sh.Percent ?? sh.Rate, 2, lang))}%</td></tr>`).join('');

  const leaderRows = leaders.slice(0, 12).map((ld) => `<tr><td>${esc(ld.Name || ld.FullName || '—')}</td><td>${esc(ld.Position || ld.Title || '—')}</td></tr>`).join('');

  const peerRows = peersRaw.slice(0, 25).map((pr) => {
    const pCode = pr.StockCode || pr.Symbol || pr.code || '';
    const href = withLangHref(`?view=stocks&ticker=${encodeURIComponent(pCode)}&stab=overview`, lang);
    return `<tr><td><a class="ticker-link mono" href="${esc(href)}" data-route>${esc(pCode)}</a></td><td>${esc(pr.CompanyName || pr.Name || '—')}</td><td class="numeric">${esc(fmtNum(pr.ClosePrice ?? pr.Price, 2, lang))}</td><td class="numeric">${esc(fmtNum(pr.PE, 2, lang))}</td><td class="numeric">${esc(fmtNum(pr.PB, 2, lang))}</td><td class="numeric">${esc(fmtNum(pr.ROE, 2, lang))}%</td></tr>`;
  }).join('');

  const snapTime = esc(bundle.scanned_at ? formatDate(bundle.scanned_at, lang) : (en ? 'time not recorded' : 'chưa ghi nhận thời gian'));

  return `<div class="yuanta-section">
    <div class="notice"><span class="notice-icon" aria-hidden="true">i</span><div><strong>${en ? `Data source: Yuanta YSradar (static snapshot ${snapTime})` : `Nguồn dữ liệu: Yuanta YSradar (snapshot tĩnh ${snapTime})`}</strong><p>${en ? '3-Group Macro Taxonomy' : 'Nhóm ngành 3 khối'}: <strong>${esc(macroLabel)}</strong> · ${en ? 'Peer Sub-sector' : 'Ngành chi tiết'}: <strong>${esc(subLabel)}</strong> ${splitBadge}. ${en ? 'Stock Rating is a third-party quantitative score from Yuanta YSradar, not a recommendation of this repository.' : 'Điểm Stock Rating là chỉ báo định lượng của Yuanta, không phải khuyến nghị của dự án.'}</p></div></div>
    ${summaryCards}
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Company Profile & Executive Leadership' : 'Tổng quan doanh nghiệp & Ban lãnh đạo'}</h3><p>${en ? 'Provider text may be in Vietnamese. · ' : ''}ICB: ${esc([m.icb_l1, m.icb_l2, m.icb_l3].filter(Boolean).join(' → ') || m.sector_name || (en ? 'Unclassified' : 'Chưa phân loại'))}</p></div></div><p><strong>${esc(m.company_name || pInfo.CompanyNameVi || rInfo.CompanyName || (en ? 'Company name unavailable' : 'Chưa có tên doanh nghiệp'))}</strong></p><p class="yuanta-bio">${esc(companyBio)}</p>${leaderRows ? `<div class="table-wrap"><table><thead><tr><th>${en ? 'Full Name' : 'Họ tên'}</th><th>${en ? 'Position' : 'Chức vụ'}</th></tr></thead><tbody>${leaderRows}</tbody></table></div>` : `<p class="muted">${en ? 'Leadership roster not separately listed in this snapshot.' : 'Danh sách lãnh đạo nằm trong hồ sơ nguồn hoặc chưa công bố riêng.'}</p>`}</section>
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Major Shareholders' : 'Tỷ lệ sở hữu / Cổ đông lớn'}</h3><p>${en ? 'Source: profile/shareholder' : 'Nguồn: profile/shareholder'}</p></div></div><div class="table-wrap"><table><thead><tr><th>${en ? 'Shareholder' : 'Cổ đông'}</th><th class="numeric">${en ? 'Shares' : 'Số cổ phiếu'}</th><th class="numeric">${en ? 'Ownership' : 'Tỷ lệ'}</th></tr></thead><tbody>${shRows || `<tr><td colspan="3" class="no-results">${en ? 'No major shareholders listed.' : 'Chưa có danh sách cổ đông lớn.'}</td></tr>`}</tbody></table></div></section>
    </div>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Order Book Snapshot (Bid / Ask)' : 'Đặt mua / Đặt bán (Sổ lệnh snapshot)'}</h3><p>${en ? 'Source: last_bid_offer (odd_lot=false)' : 'Nguồn: last_bid_offer (odd_lot=false)'}</p></div></div><div class="table-wrap"><table><thead><tr><th>${en ? 'Depth' : 'Bước giá'}</th><th class="numeric">${en ? 'Bid Price' : 'Giá mua'}</th><th class="numeric">${en ? 'Bid Vol' : 'KL mua'}</th><th class="numeric">${en ? 'Ask Price' : 'Giá bán'}</th><th class="numeric">${en ? 'Ask Vol' : 'KL bán'}</th></tr></thead><tbody>${bidRows}</tbody></table></div></section>
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Recent Matched Trades' : 'Khớp lệnh gần nhất'}</h3><p>${en ? 'Source: list_transaction (up to 50 trades at scan time); trade time is source-provided and may be an unverified raw timestamp' : 'Nguồn: list_transaction (tối đa 50 lệnh tại thời điểm quét); thời gian do nguồn cung cấp có thể là timestamp thô chưa xác minh'}</p></div></div><div class="table-wrap"><table><thead><tr><th>${en ? 'Time' : 'Thời gian'}</th><th class="numeric">${en ? 'Price' : 'Giá khớp'}</th><th class="numeric">${en ? 'Volume' : 'KL khớp'}</th><th>${en ? 'Side' : 'Phía'}</th></tr></thead><tbody>${txRows || `<tr><td colspan="4" class="no-results">${en ? 'No matched intraday trades recorded at snapshot time.' : 'Chưa có lệnh khớp trong phiên tại thời điểm snapshot.'}</td></tr>`}</tbody></table></div></section>
    </div>
    <section class="panel"><div class="panel-header"><div><h3>${en ? `Industry Peers (${fmtInt(peersRaw.length, lang)} stocks · Industry P/E: ${esc(fmtNum(industryPe, 2, lang))})` : `Cổ phiếu cùng ngành (${fmtInt(peersRaw.length, lang)} mã · P/E ngành: ${esc(fmtNum(industryPe, 2, lang))})`}</h3><p>${en ? 'Source: profile/peers?num_peers=1000 (showing first 25)' : 'Nguồn: profile/peers?num_peers=1000 (hiển thị 25 mã đầu)'}</p></div></div><div class="table-wrap"><table><thead><tr><th>${en ? 'Ticker' : 'Mã'}</th><th>${en ? 'Company' : 'Tên doanh nghiệp'}</th><th class="numeric">${en ? 'Price' : 'Thị giá'}</th><th class="numeric">P/E</th><th class="numeric">P/B</th><th class="numeric">ROE</th></tr></thead><tbody>${peerRows || `<tr><td colspan="6" class="no-results">${en ? 'No peer list available.' : 'Chưa có danh sách cùng ngành.'}</td></tr>`}</tbody></table></div></section>
  </div>`;
}

function renderSimpleLineSvg(points, { title, yKey, xKey = 'trading_date', color = '#247f7a', bar = false, lang = DEFAULT_LANG } = {}) {
  const en = lang === 'en';
  const valid = (points || []).filter((r) => r && r[yKey] !== null && r[yKey] !== undefined && r[yKey] !== '' && Number.isFinite(Number(r[yKey])));
  if (!valid.length) return `<div class="chart-empty">${en ? `No series points available for ${esc(title)}.` : `Chưa có chuỗi điểm cho ${esc(title)}.`}</div>`;
  const w = 680; const h = 170; const pad = { l: 54, r: 12, t: 12, b: 26 };
  const pw = w - pad.l - pad.r; const ph = h - pad.t - pad.b;
  const vals = valid.map((r) => Number(r[yKey]));
  let min = Math.min(...vals); let max = Math.max(...vals);
  if (bar) { min = Math.min(0, min); max = Math.max(0, max); }
  if (min === max) { min -= 1; max += 1; }
  const x = (i) => pad.l + (valid.length <= 1 ? pw / 2 : (i / (valid.length - 1)) * pw);
  const y = (v) => pad.t + ((max - v) / (max - min)) * ph;
  const zeroY = y(0);
  let shape;
  if (bar) {
    const bw = Math.max(1.2, (pw / Math.max(valid.length, 1)) * 0.72);
    shape = valid.map((r, i) => {
      const val = Number(r[yKey]);
      const yy = Math.min(y(val), zeroY);
      const hh = Math.max(1, Math.abs(y(val) - zeroY));
      const fill = val >= 0 ? '#1f8a65' : '#c04438';
      return `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${hh.toFixed(1)}" fill="${fill}"/>`;
    }).join('');
  } else {
    const d = valid.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(Number(r[yKey])).toFixed(1)}`).join(' ');
    shape = `<path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>`;
  }
  return `<div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><line x1="${pad.l}" x2="${w - pad.r}" y1="${pad.t + ph}" y2="${pad.t + ph}" stroke="#dde5e8"/><text x="${pad.l - 6}" y="${pad.t + 8}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(max, 1, lang))}</text><text x="${pad.l - 6}" y="${pad.t + ph}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(min, 1, lang))}</text>${shape}<text x="${pad.l}" y="${h - 6}" font-size="9" fill="#7c8b94">${esc(valid[0][xKey] || '')}</text><text x="${w - pad.r}" y="${h - 6}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(valid.at(-1)[xKey] || '')}</text></svg></div>`;
}

function normalizeForeignScreenerSeries(raw) {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : (raw.data || raw.Data || raw.List || []);
  if (!Array.isArray(arr)) return [];
  return arr.map((item) => {
    const dt = item.TradingDate || item.Date || item.ngay || item.time || '';
    const v = item.Value ?? item.NetVal ?? item.GTGD ?? item.TyLeVonHoa ?? item.value_1 ?? item.val ?? null;
    return { label: String(dt).slice(0, 10), val: v != null ? Number(v) : null };
  }).filter((r) => r.label && r.val !== null && Number.isFinite(r.val));
}

function pageLinks(params, symbol, tab, key, page, totalPages, lang) {
  const make = (next) => {
    const updated = new URLSearchParams(params);
    updated.set('view', 'stocks'); updated.set('ticker', symbol); updated.set('stab', tab);
    updated.set(key, String(next)); updated.set('lang', lang);
    return `?${updated.toString()}`;
  };
  const label = lang === 'en' ? `Page ${page} of ${totalPages}` : `Trang ${page} / ${totalPages}`;
  const prev = page > 1 ? `<a class="button" href="${esc(make(page - 1))}" data-route>${lang === 'en' ? '← Previous' : '← Trước'}</a>` : '';
  const next = page < totalPages ? `<a class="button" href="${esc(make(page + 1))}" data-route>${lang === 'en' ? 'Next →' : 'Sau →'}</a>` : '';
  return `<div class="pagination"><span>${esc(label)}</span><div class="pagination-actions">${prev}${next}</div></div>`;
}

function renderPage2Trading(bundle, params, lang = DEFAULT_LANG) {
  const en = lang === 'en';
  const tr = bundle.page2_trading || {};
  const allDaily = Array.isArray(tr.daily) ? tr.daily : [];
  const tfrom = params.get('tfrom') || '';
  const tto = params.get('tto') || '';
  const filtered = allDaily.filter((r) => (!tfrom || r.trading_date >= tfrom) && (!tto || r.trading_date <= tto));
  const desc = filtered.slice().reverse();
  const rawPage = Number(params.get('tpage') || 1);
  const page = Number.isFinite(rawPage) ? Math.max(1, Math.floor(rawPage)) : 1;
  const pageSize = 25;
  const totalPages = Math.max(1, Math.ceil(desc.length / pageSize));
  const curPage = Math.min(page, totalPages);
  const rows = desc.slice((curPage - 1) * pageSize, curPage * pageSize);

  const tableRows = rows.map((r) => {
    const chg = r.pct_change;
    const toneClass = chg > 0 ? 'price-up' : chg < 0 ? 'price-down' : 'price-ref';
    return `<tr><td>${esc(r.trading_date ? formatDate(r.trading_date, lang) : '—')}</td><td class="numeric ${toneClass}">${esc(fmtNum(r.close, 2, lang))}</td><td class="numeric ${toneClass}">${esc(fmtSignedPct(chg, 2, lang))}</td><td class="numeric">${esc(fmtNum(r.open, 2, lang))}</td><td class="numeric">${esc(fmtNum(r.high, 2, lang))}</td><td class="numeric">${esc(fmtNum(r.low, 2, lang))}</td><td class="numeric">${esc(fmtInt(r.volume, lang))}</td><td class="numeric">${esc(fmtInt(r.foreign_buy_vol, lang))}</td><td class="numeric">${esc(fmtInt(r.foreign_sell_vol, lang))}</td><td class="numeric">${esc(fmtInt(r.foreign_net_vol, lang))}</td><td class="numeric">${esc(r.foreign_owned_rate != null ? `${fmtNum(r.foreign_owned_rate, 2, lang)}%` : '—')}</td></tr>`;
  }).join('');

  const fc = tr.foreign_charts || {};
  const cNgay = normalizeForeignScreenerSeries(fc.ngay_giao_dich);
  const cGtgd = normalizeForeignScreenerSeries(fc.gtgd_binh_quan_chart);
  const cVh = normalizeForeignScreenerSeries(fc.ty_le_von_hoa_chart);

  return `<div class="yuanta-section">
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="trading">
      <div class="field"><label for="yuanta-tfrom">${en ? 'From date' : 'Từ ngày'}</label><input id="yuanta-tfrom" name="tfrom" type="date" value="${esc(tfrom)}"></div>
      <div class="field"><label for="yuanta-tto">${en ? 'To date' : 'Đến ngày'}</label><input id="yuanta-tto" name="tto" type="date" value="${esc(tto)}"></div>
      <button class="button primary" type="submit">${en ? 'Filter dates' : 'Lọc ngày'}</button>
      <span class="toolbar-note">${en ? `${fmtInt(filtered.length, lang)} / ${fmtInt(allDaily.length, lang)} sessions (2010–present)` : `${fmtInt(filtered.length, lang)} / ${fmtInt(allDaily.length, lang)} phiên (2010–nay)`}</span>
    </form>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Historical Close Price' : 'Lịch sử giá đóng cửa'}</h3><p>${en ? 'Source: market_watch/price_history' : 'Nguồn: market_watch/price_history'}</p></div></div>${renderSimpleLineSvg(filtered.slice(-500), { title: en ? 'Close Price' : 'Giá đóng cửa', yKey: 'close', lang })}</section>
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Foreign Net Volume Flow' : 'Khối lượng mua/bán ròng khối ngoại'}</h3><p>${en ? 'Source: price_history & price_history_foreign' : 'Nguồn: price_history & price_history_foreign'}</p></div></div>${renderSimpleLineSvg(filtered.slice(-180), { title: en ? 'Foreign Net Volume' : 'Mua bán ròng khối ngoại', yKey: 'foreign_net_vol', bar: true, lang })}</section>
    </div>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Foreign Trading Sessions & Average Value' : 'Ngày giao dịch & GTGD bình quân khối ngoại'}</h3><p>${en ? 'Source: foreign_screener/ngay_giao_dich & gtgd_binh_quan_chart' : 'Nguồn: foreign_screener/ngay_giao_dich & gtgd_binh_quan_chart'}</p></div></div>${cGtgd.length ? renderSimpleLineSvg(cGtgd, { title: en ? 'Foreign Average Trading Value' : 'GTGD bình quân khối ngoại', yKey: 'val', xKey: 'label', bar: true, lang }) : cNgay.length ? renderSimpleLineSvg(cNgay, { title: en ? 'Foreign Trading Sessions' : 'Ngày giao dịch khối ngoại', yKey: 'val', xKey: 'label', bar: true, lang }) : `<div class="chart-empty">${en ? 'No foreign_screener series for this window.' : 'Chưa có chuỗi foreign_screener cho kỳ lọc.'}</div>`}</section>
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Foreign Market-Cap Ownership Ratio' : 'Tỷ lệ vốn hóa khối ngoại'}</h3><p>${en ? 'Source: foreign_screener/ty_le_von_hoa_chart' : 'Nguồn: foreign_screener/ty_le_von_hoa_chart'}</p></div></div>${cVh.length ? renderSimpleLineSvg(cVh, { title: en ? 'Foreign Market-Cap Ratio' : 'Tỷ lệ vốn hóa khối ngoại', yKey: 'val', xKey: 'label', color: '#4d6b88', lang }) : `<div class="chart-empty">${en ? 'No foreign market-cap series available.' : 'Chưa có chuỗi tỷ lệ vốn hóa khối ngoại.'}</div>`}</section>
    </div>
    <section class="panel"><div class="panel-header"><div><h3>${en ? 'OHLCV & Foreign Flow Ledger' : 'Bảng OHLCV & Giao dịch nước ngoài'}</h3><p>${en ? `Page ${curPage} of ${totalPages}` : `Trang ${curPage} / ${totalPages}`}</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>${en ? 'Date' : 'Ngày'}</th><th class="numeric">${en ? 'Close' : 'Đóng cửa'}</th><th class="numeric">${en ? '% Chg' : '% Đổi'}</th><th class="numeric">${en ? 'Open' : 'Mở cửa'}</th><th class="numeric">${en ? 'High' : 'Cao nhất'}</th><th class="numeric">${en ? 'Low' : 'Thấp nhất'}</th><th class="numeric">${en ? 'Total Vol' : 'Tổng KL'}</th><th class="numeric">${en ? 'Foreign Buy' : 'KL Mua NN'}</th><th class="numeric">${en ? 'Foreign Sell' : 'KL Bán NN'}</th><th class="numeric">${en ? 'Foreign Net' : 'KL Ròng NN'}</th><th class="numeric">${en ? '% Foreign Owned' : '% Sở hữu NN'}</th></tr></thead><tbody>${tableRows || `<tr><td colspan="11" class="no-results">${en ? 'No trading sessions in the selected range.' : 'Không có phiên giao dịch trong khoảng lọc.'}</td></tr>`}</tbody></table></div>
      ${pageLinks(params, bundle.symbol, 'trading', 'tpage', curPage, totalPages, lang)}
    </section>
  </div>`;
}

function renderRadarSvg(metrics, lang = DEFAULT_LANG) {
  const en = lang === 'en';
  const valid = (metrics || []).filter((m) => Number.isFinite(m.favorable_percentile));
  if (valid.length < 3) {
    return `<div class="chart-empty">${en ? 'At least 3 metrics with peer percentiles are required to plot the Radar chart.' : 'Cần tối thiểu 3 chỉ số có phân vị ngành để dựng biểu đồ Radar.'}</div>`;
  }
  const cx = 175; const cy = 135; const rMax = 92;
  const n = valid.length;
  const angle = (i) => -Math.PI / 2 + (2 * Math.PI * i) / n;
  const pt = (i, pct) => {
    const r = (Math.max(0, Math.min(100, pct)) / 100) * rMax;
    return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
  };
  const rings = [25, 50, 75, 100].map((lvl) => {
    const poly = valid.map((_, i) => pt(i, lvl).map((v) => v.toFixed(1)).join(',')).join(' ');
    return `<polygon points="${poly}" fill="none" stroke="${lvl === 50 ? '#b9cbd0' : '#e6eef0'}" stroke-dasharray="${lvl === 50 ? '3 3' : ''}"/>`;
  }).join('');
  const axes = valid.map((m, i) => {
    const [ex, ey] = pt(i, 100);
    const [lx, ly] = pt(i, 118);
    const lbl = translateCorrelationMetric(m.key, m.label, lang);
    return `<line x1="${cx}" y1="${cy}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}" stroke="#dde6e9"/><text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="#52646d">${esc(lbl)}</text>`;
  }).join('');
  const stockPoly = valid.map((m, i) => pt(i, m.favorable_percentile).map((v) => v.toFixed(1)).join(',')).join(' ');
  const medPoly = valid.map((_, i) => pt(i, 50).map((v) => v.toFixed(1)).join(',')).join(' ');
  return `<div class="chart-box"><svg viewBox="0 0 350 270" role="img" aria-label="${en ? 'Peer correlation percentile radar chart' : 'Biểu đồ Radar phân vị tương quan ngành'}">${rings}${axes}<polygon points="${medPoly}" fill="none" stroke="#8597a0" stroke-width="1.3" stroke-dasharray="4 3"/><polygon points="${stockPoly}" fill="rgba(36,127,122,0.18)" stroke="#247f7a" stroke-width="2.2"/></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>${en ? 'Stock favorable percentile (0–100)' : 'Phân vị thuận lợi của mã (0–100)'}</span><span class="legend-item"><span class="legend-line snapshot"></span>${en ? 'Peer median (P50)' : 'Trung vị ngành (P50)'}</span></div></div>`;
}

export function renderPage3Correlation(bundle, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  const en = nl === 'en';
  const corr = bundle.page3_sector_correlation;
  if (!corr || !Array.isArray(corr.metrics)) {
    return `<div class="panel-empty"><strong>${en ? 'Insufficient financial statement data to compute sector correlation' : 'Chưa đủ dữ liệu BCTC để tính tương quan ngành'}</strong></div>`;
  }
  const macroLabel = translateMacroGroup(corr.macro_group, corr.macro_group_label, nl);
  const subLabel = translateSubGroup(corr.sub_group, corr.sub_group_label, nl);

  const splitNote = corr.split_from_financial_l1
    ? (en
      ? `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Key Sub-sector Separated: Real Estate</strong><p>Under standard ICB Level 1, Real Estate (8600) is grouped under Financials (8000). This engine explicitly separates Real Estate into <strong>Non-Financials · Real Estate (split from ICB L1 Financials)</strong> so project/inventory cycles are benchmarked against Real Estate peers rather than pooled with Banks or Securities firms.</p></div></div>`
      : `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Đã tách nhóm ngành trọng yếu: Bất động sản</strong><p>Trong chuẩn ICB cấp 1, Bất động sản nằm chung dưới nhánh "Tài chính" (8000). Hệ thống đã tự động tách Bất động sản sang khối <strong>Phi tài chính · Bất động sản</strong> để so sánh trung vị đúng bản chất chu kỳ tồn kho/dự án, không trộn lẫn với Ngân hàng hay Chứng khoán.</p></div></div>`)
    : '';

  const barsHtml = corr.metrics.map((m) => {
    const metricLabel = translateCorrelationMetric(m.key, m.label, nl);
    const vals = [m.value, m.peer_median, m.macro_median].filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
    const maxAbs = Math.max(1e-6, ...vals.map((v) => Math.abs(v)));
    const widthPct = (v) => (v == null || !Number.isFinite(Number(v)) ? 0 : Math.min(100, (Math.abs(Number(v)) / maxAbs) * 100));
    return `<div class="multiscale-card">
      <div class="multiscale-head"><strong>${esc(metricLabel)}</strong><span>${en ? 'Peer percentile' : 'Phân vị ngành'}: ${m.favorable_percentile != null ? `${fmtNum(m.favorable_percentile, 0, nl)}%` : '—'}</span></div>
      <div class="multiscale-row"><span>${esc(bundle.symbol)}</span><div class="multiscale-track"><div class="multiscale-fill primary" style="width:${widthPct(m.value).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.value, 2, nl))}</strong></div>
      <div class="multiscale-row"><span>${en ? `${esc(subLabel)} median (${fmtInt(m.peer_count, nl)})` : `Trung vị ${esc(subLabel)} (${fmtInt(m.peer_count, nl)})`}</span><div class="multiscale-track"><div class="multiscale-fill peer" style="width:${widthPct(m.peer_median).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.peer_median, 2, nl))}</strong></div>
      <div class="multiscale-row"><span>${en ? `${esc(macroLabel)} group median (${fmtInt(m.macro_count, nl)})` : `Trung vị khối ${esc(macroLabel)} (${fmtInt(m.macro_count, nl)})`}</span><div class="multiscale-track"><div class="multiscale-fill macro" style="width:${widthPct(m.macro_median).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.macro_median, 2, nl))}</strong></div>
    </div>`;
  }).join('');

  return `<div class="yuanta-section">
    ${splitNote}
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>${en ? `Peer Percentile Positioning (${esc(subLabel)})` : `Vị thế phân vị trong ngành (${esc(subLabel)})`}</h3><p>${en ? `Macro group: ${esc(macroLabel)} · Latest period: ${esc(corr.latest_period || '—')} · Peer sample: ${fmtInt(corr.peer_universe_size, nl)} stocks` : `Khối: ${esc(macroLabel)} · Kỳ BCTC gần nhất: ${esc(corr.latest_period || '—')} · Cỡ mẫu ngành: ${fmtInt(corr.peer_universe_size, nl)} mã`}</p></div></div>${renderRadarSvg(corr.metrics, nl)}</section>
      <section class="panel"><div class="panel-header"><div><h3>${en ? 'Multi-Scale Comparison vs. Peer & Macro Medians' : 'So sánh đa thang đo (Multi-scale vs Trung vị ngành)'}</h3><p>${en ? 'Derived directly from Page 4 financial statements (CSTC & BCTT); each metric uses its own independent scale.' : 'Tính trực tiếp từ dữ liệu Trang 4 (CSTC & BCTT); mỗi chỉ số có trục tỷ lệ riêng.'}</p></div></div><div class="multiscale-grid">${barsHtml}</div></section>
    </div>
  </div>`;
}

function renderMultiSeriesChart(chart, lang = DEFAULT_LANG) {
  const en = lang === 'en';
  const series = chart?.series || [];
  if (!series.length) return '';
  const allPeriods = [...new Set(series.flatMap((s) => s.points.map((p) => p.period)))].sort();
  const allVals = series.flatMap((s) => s.points.map((p) => p.value)).filter((v) => Number.isFinite(v));
  if (!allVals.length) return '';
  const chartTitle = translateChartTitle(chart, lang);
  const w = 520; const h = 165; const pad = { l: 52, r: 12, t: 12, b: 26 };
  const pw = w - pad.l - pad.r; const ph = h - pad.t - pad.b;
  let min = Math.min(...allVals); let max = Math.max(...allVals);
  if (chart.chart_type !== 'line') min = Math.min(0, min);
  if (min === max) { min -= 1; max += 1; }
  const pIdx = new Map(allPeriods.map((p, i) => [p, i]));
  const x = (p) => pad.l + (allPeriods.length <= 1 ? pw / 2 : (pIdx.get(p) / (allPeriods.length - 1)) * pw);
  const y = (v) => pad.t + ((max - v) / (max - min)) * ph;
  const palette = ['#247f7a', '#4a6fa5', '#c87d2f', '#8a5a96'];
  const paths = series.map((s, idx) => {
    const pts = s.points.filter((p) => Number.isFinite(p.value));
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.period).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${palette[idx % palette.length]}" stroke-width="2"/>`;
  }).join('');
  const legend = series.map((s, idx) => `<span class="legend-item"><span class="legend-dot" style="background:${palette[idx % palette.length]}"></span>${esc(translateChartSeriesLabel(s, lang))}</span>`).join('');
  return `<section class="panel"><div class="panel-header"><div><h3>${esc(chartTitle)}</h3><p>${en ? 'Plotted from quarterly CSTC / BCTT disclosures' : 'Tự vẽ từ dữ liệu BCTC/CSTC'}</p></div></div><div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(chartTitle)}"><line x1="${pad.l}" x2="${w - pad.r}" y1="${pad.t + ph}" y2="${pad.t + ph}" stroke="#dde5e8"/><text x="${pad.l - 6}" y="${pad.t + 8}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(max, 1, lang))}</text><text x="${pad.l - 6}" y="${pad.t + ph}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(min, 1, lang))}</text>${paths}<text x="${pad.l}" y="${h - 6}" font-size="9" fill="#7c8b94">${esc(allPeriods[0] || '')}</text><text x="${w - pad.r}" y="${h - 6}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(allPeriods.at(-1) || '')}</text></svg><div class="chart-legend">${legend}</div></div></section>`;
}

export function renderPage4Financials(bundle, params, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  const en = nl === 'en';
  const fin = bundle.page4_financials || {};
  const rtype = params.get('rtype') === 'bctt' ? 'bctt' : 'cstc';
  const fquery = (params.get('fq') || '').trim().toLocaleLowerCase('vi');
  const pOffset = Math.max(0, Number(params.get('poff') || 0));
  const tableData = fin[rtype] || { periods: [], rows: [] };
  const allPeriods = tableData.periods || [];
  const winSize = 8;
  const clampedOff = Math.min(pOffset, Math.max(0, allPeriods.length - winSize));
  const shownPeriods = allPeriods.slice(clampedOff, clampedOff + winSize);
  const macroLabel = translateMacroGroup(fin.macro_group, fin.macro_group_label, nl);

  const rows = (tableData.rows || []).filter((r) => {
    if (!fquery) return true;
    const enField = translateFinancialField(r.field_name, 'en');
    const enComp = translateFinancialComponent(r.component, 'en');
    const hay = `${r.component || ''} ${r.field_name} ${enComp} ${enField}`.toLocaleLowerCase('vi');
    return hay.includes(fquery);
  });

  const chartsHtml = (fin.charts || []).map((c) => renderMultiSeriesChart(c, nl)).join('');
  const headCols = shownPeriods.map((p) => `<th class="numeric">${esc(p)}</th>`).join('');
  const bodyRows = rows.map((r) => {
    const dispComp = translateFinancialComponent(r.component, nl);
    const dispField = translateFinancialField(r.field_name, nl);
    const dispUnit = translateFinancialUnit(r.unit, nl);
    const origTitle = en && dispField !== r.field_name ? ` title="${esc(r.field_name)}"` : '';
    return `<tr><td${origTitle}><span class="table-sub">${esc(dispComp)}</span><strong>${esc(dispField)}</strong></td><td>${esc(dispUnit)}</td>${shownPeriods.map((p) => `<td class="numeric">${esc(r.values?.[p] != null ? fmtNum(r.values[p], 2, nl) : '—')}</td>`).join('')}</tr>`;
  }).join('');

  const prevOff = Math.max(0, clampedOff - winSize);
  const nextOff = Math.min(Math.max(0, allPeriods.length - winSize), clampedOff + winSize);
  const baseHref = `?view=stocks&ticker=${encodeURIComponent(bundle.symbol)}&stab=financials`;

  return `<div class="yuanta-section">
    <div class="notice"><span class="notice-icon" aria-hidden="true">i</span><div><strong>${en ? `Sector-Tailored Charts & Financial Statements: ${esc(macroLabel)}` : `Biểu đồ & Báo cáo tài chính theo nhóm ngành: ${esc(macroLabel)}`}</strong><p>${en ? `Quarterly disclosures from Q1/2010 to present (${fmtInt(allPeriods.length, nl)} reporting periods). Monetary line items follow source scaling (1,000,000 VND = Million VND unless the Unit column states otherwise).` : `Dữ liệu quý từ Q1/2010 đến hiện tại (${fmtInt(allPeriods.length, nl)} kỳ báo cáo). Đơn vị giá trị tiền tệ theo chuẩn nguồn (1.000.000 VND = triệu đồng trừ khi cột đơn vị ghi khác).`}</p></div></div>
    ${chartsHtml ? `<div class="detail-grid">${chartsHtml}</div>` : ''}
    <div class="tab-strip">
      <a href="${esc(withLangHref(`${baseHref}&rtype=cstc`, nl))}" data-route class="${rtype === 'cstc' ? 'active' : ''}">${en ? `Financial Ratios (CSTC · ${fmtInt((fin.cstc?.periods || []).length, nl)} periods)` : `Chỉ số tài chính (CSTC · ${fmtInt((fin.cstc?.periods || []).length, nl)} kỳ)`}</a>
      <a href="${esc(withLangHref(`${baseHref}&rtype=bctt`, nl))}" data-route class="${rtype === 'bctt' ? 'active' : ''}">${en ? `Summary Statements (BCTT · ${fmtInt((fin.bctt?.periods || []).length, nl)} periods)` : `Báo cáo tài chính (BCTT · ${fmtInt((fin.bctt?.periods || []).length, nl)} kỳ)`}</a>
    </div>
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="financials">
      <input type="hidden" name="rtype" value="${esc(rtype)}">
      <div class="field grow"><label for="fin-fq">${en ? 'Filter line items' : 'Tìm khoản mục'}</label><input id="fin-fq" name="fq" type="search" value="${esc(params.get('fq') || '')}" placeholder="${en ? 'e.g. ROE, NIM, Revenue, Inventories' : 'VD: ROE, NIM, Doanh thu, Hàng tồn kho'}"></div>
      <button class="button primary" type="submit">${en ? 'Filter items' : 'Lọc khoản mục'}</button>
      <a class="button" href="${esc(withLangHref(`${baseHref}&rtype=${rtype}&poff=${prevOff}`, nl))}" data-route>${en ? '← Newer periods' : '← Kỳ mới hơn'}</a>
      <a class="button" href="${esc(withLangHref(`${baseHref}&rtype=${rtype}&poff=${nextOff}`, nl))}" data-route>${en ? 'Older periods (back to 2010) →' : 'Kỳ cũ hơn (về 2010) →'}</a>
    </form>
    <section class="panel"><div class="panel-header"><div><h3>${rtype === 'cstc' ? (en ? 'Financial Ratios Matrix (CSTC)' : 'Toàn bộ Chỉ số tài chính (CSTC)') : (en ? 'Financial Statements Matrix (BCTT)' : 'Toàn bộ Báo cáo tài chính (BCTT)')}</h3><p>${en ? `Showing periods ${esc(shownPeriods[0] || '—')} → ${esc(shownPeriods.at(-1) || '—')} (${fmtInt(rows.length, nl)} line items)` : `Đang hiển thị kỳ ${esc(shownPeriods[0] || '—')} → ${esc(shownPeriods.at(-1) || '—')} (${fmtInt(rows.length, nl)} chỉ tiêu)`}</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>${en ? 'Line Item' : 'Khoản mục'}</th><th>${en ? 'Unit' : 'Đơn vị'}</th>${headCols}</tr></thead><tbody>${bodyRows || `<tr><td colspan="${2 + shownPeriods.length}" class="no-results">${en ? 'No matching line items.' : 'Không có khoản mục phù hợp.'}</td></tr>`}</tbody></table></div>
    </section>
  </div>`;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function renderPage5Events(bundle, params, lang = DEFAULT_LANG) {
  const en = lang === 'en';
  const ev = bundle.page5_events || {};
  const all = Array.isArray(ev.articles) ? ev.articles : [];
  const eq = (params.get('eq') || '').trim().toLocaleLowerCase('vi');
  const filtered = all.filter((a) => !eq || `${a.title || ''} ${a.category || ''} ${a.summary || ''}`.toLocaleLowerCase('vi').includes(eq));
  const rawPage = Number(params.get('epage') || 1);
  const page = Number.isFinite(rawPage) ? Math.max(1, Math.floor(rawPage)) : 1;
  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, totalPages);
  const slice = filtered.slice((cur - 1) * pageSize, cur * pageSize);

  const rows = slice.map((item) => `<tr><td>${esc(item.published_date ? formatDate(item.published_date, lang) : item.published_at ? formatDate(item.published_at, lang) : '—')}</td><td>${esc(item.category || (en ? 'Corporate Event / News' : 'Sự kiện / Tin tức'))}</td><td><strong>${esc(item.title || '—')}</strong>${item.summary ? `<span class="table-sub">${esc(item.summary)}</span>` : ''}</td><td>${safeExternalUrl(item.url) ? `<a class="small-link" href="${esc(safeExternalUrl(item.url))}" target="_blank" rel="noreferrer">${en ? 'Open ↗' : 'Mở bài ↗'}</a>` : '—'}</td></tr>`).join('');

  return `<div class="yuanta-section">
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="events">
      <div class="field grow"><label for="ev-q">${en ? 'Search news & corporate events' : 'Tìm tin tức & sự kiện'}</label><input id="ev-q" name="eq" type="search" value="${esc(params.get('eq') || '')}" placeholder="${en ? 'e.g. cổ tức / dividend, AGM, issuance' : 'VD: cổ tức, ĐHĐCĐ, phát hành'}"></div>
      <button class="button primary" type="submit">${en ? 'Search' : 'Tìm'}</button>
      <span class="toolbar-note">${en ? `${fmtInt(filtered.length, lang)} / ${fmtInt(ev.record_count ?? all.length, lang)} events (since 2010-01-01)` : `${fmtInt(filtered.length, lang)} / ${fmtInt(ev.record_count ?? all.length, lang)} sự kiện (từ 01/01/2010)`}</span>
    </form>
    <section class="panel"><div class="panel-header"><div><h3>${en ? `Corporate News & Events (${esc(bundle.symbol)})` : `Tin tức & Sự kiện doanh nghiệp (${esc(bundle.symbol)})`}</h3><p>${en ? `Source: market_data/article · Page ${cur} of ${totalPages} · Provider text may be in Vietnamese` : `Nguồn: market_data/article · Trang ${cur} / ${totalPages}`}</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>${en ? 'Date' : 'Ngày'}</th><th>${en ? 'Category' : 'Nhóm'}</th><th>${en ? 'Headline / Summary' : 'Tiêu đề / Nội dung'}</th><th>${en ? 'Link' : 'Liên kết'}</th></tr></thead><tbody>${rows || `<tr><td colspan="4" class="no-results">${en ? 'No matching events found.' : 'Không tìm thấy sự kiện phù hợp.'}</td></tr>`}</tbody></table></div>
      ${pageLinks(params, bundle.symbol, 'events', 'epage', cur, totalPages, lang)}
    </section>
  </div>`;
}

export function renderYuantaStockTabs(symbol, stab, loadedEntry, params, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  const en = nl === 'en';
  const tabs = en ? [
    ['funds', 'Open-End Fund Ownership'],
    ['overview', 'Page 1: Overview'],
    ['trading', 'Page 2: Trading & Foreign Flow'],
    ['correlation', 'Page 3: Sector Correlation'],
    ['financials', 'Page 4: Financial Statements'],
    ['events', 'Page 5: News & Corporate Events'],
  ] : [
    ['funds', 'Sở hữu quỹ mở'],
    ['overview', 'Trang 1: Tổng quan'],
    ['trading', 'Trang 2: Giao dịch'],
    ['correlation', 'Trang 3: Tương quan ngành'],
    ['financials', 'Trang 4: Báo cáo tài chính'],
    ['events', 'Trang 5: Tin tức & sự kiện'],
  ];
  const nav = `<div class="tab-strip yuanta-tabs">${tabs.map(([id, label]) => {
    const href = withLangHref(`?view=stocks&ticker=${encodeURIComponent(symbol)}&stab=${id}`, nl);
    return `<a href="${esc(href)}" data-route class="${stab === id ? 'active' : ''}" ${stab === id ? 'aria-current="page"' : ''}>${esc(label)}</a>`;
  }).join('')}</div>`;
  if (stab === 'funds') return { nav, body: null };
  if (!loadedEntry || loadedEntry.status === 'loading') {
    return { nav, body: `<div class="loading-panel"><span class="loading-mark" aria-hidden="true">${esc(symbol.slice(0, 3))}</span><p>${en ? `Loading static bundle for ${esc(symbol)}…` : `Đang tải gói dữ liệu tĩnh ${esc(symbol)}…`}</p></div>` };
  }
  if (loadedEntry.status !== 'available' || !loadedEntry.bundle) {
    if (loadedEntry.status === 'not_synced') return { nav, body: renderNotSyncedNotice(symbol, nl) };
    const detail = loadedEntry.status === 'invalid_symbol'
      ? (en ? 'Ticker must contain 2–5 letters or digits.' : 'Mã phải gồm 2–5 chữ cái hoặc chữ số.')
      : loadedEntry.status === 'malformed'
        ? (en ? 'The static bundle is malformed or does not match this ticker.' : 'Gói dữ liệu tĩnh sai định dạng hoặc không khớp mã.')
        : (en ? 'The static bundle could not be loaded. Check the export and network; its absence is not established.' : 'Không tải được gói dữ liệu tĩnh. Kiểm tra export và kết nối; chưa thể kết luận là thiếu.') ;
    return { nav, body: `<div class="notice warning" role="status"><span class="notice-icon" aria-hidden="true">i</span><div><strong>${en ? 'Yuanta data unavailable' : 'Dữ liệu Yuanta không khả dụng'}</strong><p>${esc(detail)}</p></div></div>` };
  }
  const bundle = loadedEntry.bundle;
  let body;
  switch (stab) {
    case 'trading': body = renderPage2Trading(bundle, params, nl); break;
    case 'correlation': body = renderPage3Correlation(bundle, nl); break;
    case 'financials': body = renderPage4Financials(bundle, params, nl); break;
    case 'events': body = renderPage5Events(bundle, params, nl); break;
    default: body = renderPage1Overview(bundle, nl);
  }
  return { nav, body };
}
