const TICKER_RE = /^[A-Z0-9]{2,5}$/;
const BUNDLE_CACHE = new Map();
let UNIVERSE_CACHE = null;

const VI_NUM = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 });
const VI_INT = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 });

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmtNum(val, digits = 2) {
  if (val === null || val === undefined || val === '' || !Number.isFinite(Number(val))) return '—';
  return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: digits }).format(Number(val));
}

function fmtInt(val) {
  if (val === null || val === undefined || val === '' || !Number.isFinite(Number(val))) return '—';
  return VI_INT.format(Number(val));
}

function fmtSignedPct(val, digits = 2) {
  if (val === null || val === undefined || val === '' || !Number.isFinite(Number(val))) return '—';
  const n = Number(val);
  return `${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: digits, signDisplay: 'exceptZero' }).format(n).replace(/^-/, '−')}%`;
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
  // Check gzip magic header 0x1f 0x8b.
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
      UNIVERSE_CACHE = { status: 'not_synced', count: 0, stocks: [] };
      return UNIVERSE_CACHE;
    }
    const payload = await resp.json();
    if (!payload || !Array.isArray(payload.stocks)) {
      UNIVERSE_CACHE = { status: 'malformed', count: 0, stocks: [] };
      return UNIVERSE_CACHE;
    }
    UNIVERSE_CACHE = { status: 'available', ...payload };
    return UNIVERSE_CACHE;
  } catch {
    UNIVERSE_CACHE = { status: 'not_synced', count: 0, stocks: [] };
    return UNIVERSE_CACHE;
  }
}

export async function loadYuantaStockBundle(symbol, fetcher = globalThis.fetch, moduleUrl = import.meta.url) {
  const sym = String(symbol || '').trim().toUpperCase();
  if (!TICKER_RE.test(sym)) return { status: 'invalid_symbol', symbol: sym, bundle: null };
  if (BUNDLE_CACHE.has(sym)) return BUNDLE_CACHE.get(sym);
  if (typeof fetcher !== 'function') return { status: 'unavailable', symbol: sym, bundle: null };

  for (const candidate of [`stocks/${sym}.json.gz`, `stocks/${sym}.json`]) {
    try {
      const resp = await fetcher(resolveYuantaUrl(candidate, moduleUrl), { cache: 'no-store' });
      if (!resp || !resp.ok) continue;
      const parsed = candidate.endsWith('.gz') ? await decodeGzipOrJsonResponse(resp) : await resp.json();
      if (parsed && typeof parsed === 'object' && parsed.symbol === sym) {
        const entry = { status: 'available', symbol: sym, bundle: parsed };
        BUNDLE_CACHE.set(sym, entry);
        return entry;
      }
    } catch {
      /* try plain json fallback */
    }
  }
  const missing = { status: 'not_synced', symbol: sym, bundle: null };
  BUNDLE_CACHE.set(sym, missing);
  return missing;
}

function renderNotSyncedNotice(symbol) {
  return `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Chưa có gói dữ liệu tĩnh Yuanta cho mã ${esc(symbol)}</strong><p>Môi trường trình duyệt chỉ đọc tệp tĩnh nội bộ (không gọi trực tiếp API ngoài). Để quét toàn bộ 3 sàn HOSE, HNX, UPCOM (Trang 1–5) và sinh tệp <code>dashboard/data/yuanta/stocks/${esc(symbol)}.json.gz</code>, chạy lệnh CLI hoặc bật GitHub Actions workflow:</p><p><code>python scripts/scan_yuanta_universe.py --exchanges HOSE,HNX,UPCOM</code> (hoặc kiểm thử nhanh: <code>python scripts/scan_yuanta_universe.py --tickers ${esc(symbol)}</code>)</p></div></div>`;
}

function renderPage1Overview(bundle) {
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
  const peersRaw = Array.isArray(ov.peers) ? ov.peers : (ov.peers?.Peers || ov.peers?.data || []);
  const industryPe = ov.peers?.IndustryPE ?? ov.peers?.PE ?? rInfo.IndustryPE ?? null;

  const splitBadge = m.split_from_financial_l1
    ? `<span class="table-status warning" title="Bất động sản thuộc Tài chính (ICB L1) nhưng được tách sang khối Phi tài chính để không làm sai lệch trung vị Ngân hàng / Chứng khoán">· Tách khỏi Tài chính L1</span>`
    : '';

  const summaryCards = `<div class="metric-grid yuanta-metrics">
    <article class="metric-card"><div class="metric-label"><span>Giá khớp / Tham chiếu</span></div><div class="metric-value">${esc(fmtNum(m.last_price))}</div><div class="metric-basis">TC: ${esc(fmtNum(m.ref_price))} · Sàn ${esc(m.exchange || '—')}</div></article>
    <article class="metric-card"><div class="metric-label"><span>Định giá P/E · P/B</span></div><div class="metric-value">${esc(fmtNum(m.pe))}x · ${esc(fmtNum(m.pb))}x</div><div class="metric-basis">P/E ngành: ${esc(fmtNum(industryPe))}x</div></article>
    <article class="metric-card"><div class="metric-label"><span>ROE · ROA</span></div><div class="metric-value">${esc(fmtNum(m.roe))}% · ${esc(fmtNum(m.roa))}%</div><div class="metric-basis">Vốn hóa: ${esc(m.market_cap != null ? fmtInt(m.market_cap) : '—')}</div></article>
    <article class="metric-card"><div class="metric-label"><span>Yuanta Stock Rating</span></div><div class="metric-value">${esc(fmtNum(m.stock_rating, 0))}</div><div class="metric-basis">Cơ bản: ${esc(fmtNum(m.fundamental_score, 0))} · RS: ${esc(fmtNum(m.technical_score, 0))} (nguồn Yuanta)</div></article>
  </div>`;

  const companyBio = pInfo.Overview || pInfo.BusinessSummary || pInfo.Introduction || rInfo.Description || 'Chưa có mô tả chi tiết doanh nghiệp trong snapshot.';

  const bidRows = [1, 2, 3].map((lvl) => {
    const bp = bidOffer[`BidPrice${lvl}`] ?? bidOffer[`Best${lvl}Bid`] ?? sInfo[`BidPrice${lvl}`];
    const bv = bidOffer[`BidVol${lvl}`] ?? bidOffer[`Best${lvl}BidVolume`] ?? sInfo[`BidVol${lvl}`];
    const op = bidOffer[`OfferPrice${lvl}`] ?? bidOffer[`Best${lvl}Offer`] ?? sInfo[`OfferPrice${lvl}`];
    const ovVol = bidOffer[`OfferVol${lvl}`] ?? bidOffer[`Best${lvl}OfferVolume`] ?? sInfo[`OfferVol${lvl}`];
    return `<tr><td>Mức ${lvl}</td><td class="numeric">${esc(fmtNum(bp))}</td><td class="numeric">${esc(fmtInt(bv))}</td><td class="numeric">${esc(fmtNum(op))}</td><td class="numeric">${esc(fmtInt(ovVol))}</td></tr>`;
  }).join('');

  const txRows = txs.slice(0, 15).map((tx) => `<tr><td>${esc(tx.TradingTime || tx.Time || '—')}</td><td class="numeric">${esc(fmtNum(tx.MatchPrice ?? tx.Price))}</td><td class="numeric">${esc(fmtInt(tx.MatchVolume ?? tx.Volume))}</td><td>${esc(tx.Side || tx.MatchType || '—')}</td></tr>`).join('');

  const shRows = shareholders.slice(0, 15).map((sh) => `<tr><td>${esc(sh.Name || sh.ShareHolderName || sh.FullName || '—')}</td><td class="numeric">${esc(fmtInt(sh.Quantity || sh.Shares))}</td><td class="numeric">${esc(fmtNum(sh.OwnershipPercentage ?? sh.Percent ?? sh.Rate))}%</td></tr>`).join('');

  const leaderRows = leaders.slice(0, 12).map((ld) => `<tr><td>${esc(ld.Name || ld.FullName || '—')}</td><td>${esc(ld.Position || ld.Title || '—')}</td></tr>`).join('');

  const peerRows = peersRaw.slice(0, 25).map((pr) => {
    const pCode = pr.StockCode || pr.Symbol || pr.code || '';
    return `<tr><td><a class="ticker-link mono" href="?view=stocks&ticker=${encodeURIComponent(pCode)}&stab=overview" data-route>${esc(pCode)}</a></td><td>${esc(pr.CompanyName || pr.Name || '—')}</td><td class="numeric">${esc(fmtNum(pr.ClosePrice ?? pr.Price))}</td><td class="numeric">${esc(fmtNum(pr.PE))}</td><td class="numeric">${esc(fmtNum(pr.PB))}</td><td class="numeric">${esc(fmtNum(pr.ROE))}%</td></tr>`;
  }).join('');

  return `<div class="yuanta-section">
    <div class="notice"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Nguồn dữ liệu: Yuanta YSradar (snapshot tĩnh ${esc((bundle.scanned_at || '').slice(0, 19).replace('T', ' '))})</strong><p>Nhóm ngành 3 khối: <strong>${esc(m.macro_group_label || m.macro_group)}</strong> · Ngành chi tiết: <strong>${esc(m.sub_group_label || m.sector_name || '—')}</strong> ${splitBadge}. Điểm Stock Rating là chỉ báo định lượng của Yuanta, không phải khuyến nghị của dự án.</p></div></div>
    ${summaryCards}
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>Tổng quan doanh nghiệp &amp; Ban lãnh đạo</h3><p>ICB: ${esc([m.icb_l1, m.icb_l2, m.icb_l3].filter(Boolean).join(' → ') || m.sector_name || 'Chưa phân loại')}</p></div></div><p class="yuanta-bio">${esc(companyBio)}</p>${leaderRows ? `<div class="table-wrap"><table><thead><tr><th>Họ tên</th><th>Chức vụ</th></tr></thead><tbody>${leaderRows}</tbody></table></div>` : '<p class="muted">Danh sách lãnh đạo nằm trong hồ sơ nguồn hoặc chưa công bố riêng.</p>'}</section>
      <section class="panel"><div class="panel-header"><div><h3>Tỷ lệ sở hữu / Cổ đông lớn</h3><p>Nguồn: profile/shareholder</p></div></div><div class="table-wrap"><table><thead><tr><th>Cổ đông</th><th class="numeric">Số cổ phiếu</th><th class="numeric">Tỷ lệ</th></tr></thead><tbody>${shRows || '<tr><td colspan="3" class="no-results">Chưa có danh sách cổ đông lớn.</td></tr>'}</tbody></table></div></section>
    </div>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>Đặt mua / Đặt bán (Sổ lệnh snapshot)</h3><p>Nguồn: last_bid_offer (odd_lot=false)</p></div></div><div class="table-wrap"><table><thead><tr><th>Bước giá</th><th class="numeric">Giá mua</th><th class="numeric">KL mua</th><th class="numeric">Giá bán</th><th class="numeric">KL bán</th></tr></thead><tbody>${bidRows}</tbody></table></div></section>
      <section class="panel"><div class="panel-header"><div><h3>Khớp lệnh gần nhất</h3><p>Nguồn: list_transaction (tối đa 50 lệnh tại thời điểm quét)</p></div></div><div class="table-wrap"><table><thead><tr><th>Thời gian</th><th class="numeric">Giá khớp</th><th class="numeric">KL khớp</th><th>Phía</th></tr></thead><tbody>${txRows || '<tr><td colspan="4" class="no-results">Chưa có lệnh khớp trong phiên tại thời điểm snapshot.</td></tr>'}</tbody></table></div></section>
    </div>
    <section class="panel"><div class="panel-header"><div><h3>Cổ phiếu cùng ngành (${fmtInt(peersRaw.length)} mã · P/E ngành: ${esc(fmtNum(industryPe))})</h3><p>Nguồn: profile/peers?num_peers=1000 (hiển thị 25 mã đầu)</p></div></div><div class="table-wrap"><table><thead><tr><th>Mã</th><th>Tên doanh nghiệp</th><th class="numeric">Thị giá</th><th class="numeric">P/E</th><th class="numeric">P/B</th><th class="numeric">ROE</th></tr></thead><tbody>${peerRows || '<tr><td colspan="6" class="no-results">Chưa có danh sách cùng ngành.</td></tr>'}</tbody></table></div></section>
  </div>`;
}

function renderSimpleLineSvg(points, { title, yKey, xKey = 'trading_date', color = '#247f7a', bar = false } = {}) {
  const valid = (points || []).filter((r) => r && Number.isFinite(Number(r[yKey])));
  if (!valid.length) return `<div class="chart-empty">Chưa có chuỗi điểm cho ${esc(title)}.</div>`;
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
  return `<div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}"><line x1="${pad.l}" x2="${w - pad.r}" y1="${pad.t + ph}" y2="${pad.t + ph}" stroke="#dde5e8"/><text x="${pad.l - 6}" y="${pad.t + 8}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(max, 1))}</text><text x="${pad.l - 6}" y="${pad.t + ph}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(min, 1))}</text>${shape}<text x="${pad.l}" y="${h - 6}" font-size="9" fill="#7c8b94">${esc(valid[0][xKey] || '')}</text><text x="${w - pad.r}" y="${h - 6}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(valid.at(-1)[xKey] || '')}</text></svg></div>`;
}

function normalizeForeignScreenerSeries(raw) {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : (raw.data || raw.Data || raw.List || []);
  if (!Array.isArray(arr)) return [];
  return arr.map((item) => {
    const dt = item.TradingDate || item.Date || item.ngay || item.time || '';
    const v = item.Value ?? item.NetVal ?? item.GTGD ?? item.TyLeVonHoa ?? item.value_1 ?? item.val ?? null;
    return { label: String(dt).slice(0, 10), val: v != null ? Number(v) : null };
  }).filter((r) => r.label && Number.isFinite(r.val));
}

function renderPage2Trading(bundle, params) {
  const tr = bundle.page2_trading || {};
  const allDaily = Array.isArray(tr.daily) ? tr.daily : [];
  const tfrom = params.get('tfrom') || '';
  const tto = params.get('tto') || '';
  const filtered = allDaily.filter((r) => (!tfrom || r.trading_date >= tfrom) && (!tto || r.trading_date <= tto));
  const desc = filtered.slice().reverse();
  const page = Math.max(1, Number(params.get('tpage') || 1));
  const pageSize = 25;
  const totalPages = Math.max(1, Math.ceil(desc.length / pageSize));
  const curPage = Math.min(page, totalPages);
  const rows = desc.slice((curPage - 1) * pageSize, curPage * pageSize);

  const tableRows = rows.map((r) => {
    const chg = r.pct_change;
    const toneClass = chg > 0 ? 'price-up' : chg < 0 ? 'price-down' : 'price-ref';
    return `<tr><td>${esc(r.trading_date)}</td><td class="numeric ${toneClass}">${esc(fmtNum(r.close))}</td><td class="numeric ${toneClass}">${esc(fmtSignedPct(chg))}</td><td class="numeric">${esc(fmtNum(r.open))}</td><td class="numeric">${esc(fmtNum(r.high))}</td><td class="numeric">${esc(fmtNum(r.low))}</td><td class="numeric">${esc(fmtInt(r.volume))}</td><td class="numeric">${esc(fmtInt(r.foreign_buy_vol))}</td><td class="numeric">${esc(fmtInt(r.foreign_sell_vol))}</td><td class="numeric">${esc(fmtInt(r.foreign_net_vol))}</td><td class="numeric">${esc(r.foreign_owned_rate != null ? `${fmtNum(r.foreign_owned_rate)}%` : '—')}</td></tr>`;
  }).join('');

  const fc = tr.foreign_charts || {};
  const cNgay = normalizeForeignScreenerSeries(fc.ngay_giao_dich);
  const cGtgd = normalizeForeignScreenerSeries(fc.gtgd_binh_quan_chart);
  const cVh = normalizeForeignScreenerSeries(fc.ty_le_von_hoa_chart);

  return `<div class="yuanta-section">
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="trading">
      <div class="field"><label for="yuanta-tfrom">Từ ngày</label><input id="yuanta-tfrom" name="tfrom" type="date" value="${esc(tfrom)}"></div>
      <div class="field"><label for="yuanta-tto">Đến ngày</label><input id="yuanta-tto" name="tto" type="date" value="${esc(tto)}"></div>
      <button class="button primary" type="submit">Lọc ngày</button>
      <span class="toolbar-note">${fmtInt(filtered.length)} / ${fmtInt(allDaily.length)} phiên (2010–nay)</span>
    </form>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>Lịch sử giá đóng cửa</h3><p>Nguồn: market_watch/price_history</p></div></div>${renderSimpleLineSvg(filtered.slice(-500), { title: 'Giá đóng cửa', yKey: 'close' })}</section>
      <section class="panel"><div class="panel-header"><div><h3>Khối lượng mua/bán ròng khối ngoại</h3><p>Nguồn: price_history &amp; price_history_foreign</p></div></div>${renderSimpleLineSvg(filtered.slice(-180), { title: 'Mua bán ròng khối ngoại', yKey: 'foreign_net_vol', bar: true })}</section>
    </div>
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>Ngày giao dịch &amp; GTGD bình quân khối ngoại</h3><p>Nguồn: foreign_screener/ngay_giao_dich &amp; gtgd_binh_quan_chart</p></div></div>${cGtgd.length ? renderSimpleLineSvg(cGtgd, { title: 'GTGD bình quân khối ngoại', yKey: 'val', xKey: 'label', bar: true }) : cNgay.length ? renderSimpleLineSvg(cNgay, { title: 'Ngày giao dịch khối ngoại', yKey: 'val', xKey: 'label', bar: true }) : '<div class="chart-empty">Chưa có chuỗi foreign_screener cho kỳ lọc.</div>'}</section>
      <section class="panel"><div class="panel-header"><div><h3>Tỷ lệ vốn hóa khối ngoại</h3><p>Nguồn: foreign_screener/ty_le_von_hoa_chart</p></div></div>${cVh.length ? renderSimpleLineSvg(cVh, { title: 'Tỷ lệ vốn hóa khối ngoại', yKey: 'val', xKey: 'label', color: '#4d6b88' }) : '<div class="chart-empty">Chưa có chuỗi tỷ lệ vốn hóa khối ngoại.</div>'}</section>
    </div>
    <section class="panel"><div class="panel-header"><div><h3>Bảng OHLCV &amp; Giao dịch nước ngoài</h3><p>Trang ${curPage} / ${totalPages}</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>Ngày</th><th class="numeric">Đóng cửa</th><th class="numeric">% Đổi</th><th class="numeric">Mở cửa</th><th class="numeric">Cao nhất</th><th class="numeric">Thấp nhất</th><th class="numeric">Tổng KL</th><th class="numeric">KL Mua NN</th><th class="numeric">KL Bán NN</th><th class="numeric">KL Ròng NN</th><th class="numeric">% Sở hữu NN</th></tr></thead><tbody>${tableRows || '<tr><td colspan="11" class="no-results">Không có phiên giao dịch trong khoảng lọc.</td></tr>'}</tbody></table></div>
    </section>
  </div>`;
}

function renderRadarSvg(metrics) {
  const valid = (metrics || []).filter((m) => Number.isFinite(m.favorable_percentile));
  if (valid.length < 3) {
    return '<div class="chart-empty">Cần tối thiểu 3 chỉ số có phân vị ngành để dựng biểu đồ Radar.</div>';
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
    return `<line x1="${cx}" y1="${cy}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}" stroke="#dde6e9"/><text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="#52646d">${esc(m.label)}</text>`;
  }).join('');
  const stockPoly = valid.map((m, i) => pt(i, m.favorable_percentile).map((v) => v.toFixed(1)).join(',')).join(' ');
  const medPoly = valid.map((_, i) => pt(i, 50).map((v) => v.toFixed(1)).join(',')).join(' ');
  return `<div class="chart-box"><svg viewBox="0 0 350 270" role="img" aria-label="Biểu đồ Radar phân vị tương quan ngành">${rings}${axes}<polygon points="${medPoly}" fill="none" stroke="#8597a0" stroke-width="1.3" stroke-dasharray="4 3"/><polygon points="${stockPoly}" fill="rgba(36,127,122,0.18)" stroke="#247f7a" stroke-width="2.2"/></svg><div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>Phân vị thuận lợi của mã (0–100)</span><span class="legend-item"><span class="legend-line snapshot"></span>Trung vị ngành (P50)</span></div></div>`;
}

export function renderPage3Correlation(bundle) {
  const corr = bundle.page3_sector_correlation;
  if (!corr || !Array.isArray(corr.metrics)) {
    return '<div class="panel-empty"><strong>Chưa đủ dữ liệu BCTC để tính tương quan ngành</strong></div>';
  }
  const splitNote = corr.split_from_financial_l1
    ? `<div class="notice warning"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Đã tách nhóm ngành trọng yếu: Bất động sản</strong><p>Trong chuẩn ICB cấp 1, Bất động sản nằm chung dưới nhánh "Tài chính" (8000). Hệ thống đã tự động tách Bất động sản sang khối <strong>Phi tài chính · Bất động sản</strong> để so sánh trung vị đúng bản chất chu kỳ tồn kho/dự án, không trộn lẫn với Ngân hàng hay Chứng khoán.</p></div></div>`
    : '';

  const barsHtml = corr.metrics.map((m) => {
    const vals = [m.value, m.peer_median, m.macro_median].filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
    const maxAbs = Math.max(1e-6, ...vals.map((v) => Math.abs(v)));
    const widthPct = (v) => (v == null || !Number.isFinite(Number(v)) ? 0 : Math.min(100, (Math.abs(Number(v)) / maxAbs) * 100));
    return `<div class="multiscale-card">
      <div class="multiscale-head"><strong>${esc(m.label)}</strong><span>Phân vị ngành: ${m.favorable_percentile != null ? `${fmtNum(m.favorable_percentile, 0)}%` : '—'}</span></div>
      <div class="multiscale-row"><span>${esc(bundle.symbol)}</span><div class="multiscale-track"><div class="multiscale-fill primary" style="width:${widthPct(m.value).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.value))}</strong></div>
      <div class="multiscale-row"><span>Trung vị ${esc(corr.sub_group_label)} (${fmtInt(m.peer_count)})</span><div class="multiscale-track"><div class="multiscale-fill peer" style="width:${widthPct(m.peer_median).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.peer_median))}</strong></div>
      <div class="multiscale-row"><span>Trung vị khối ${esc(corr.macro_group_label)} (${fmtInt(m.macro_count)})</span><div class="multiscale-track"><div class="multiscale-fill macro" style="width:${widthPct(m.macro_median).toFixed(1)}%"></div></div><strong>${esc(fmtNum(m.macro_median))}</strong></div>
    </div>`;
  }).join('');

  return `<div class="yuanta-section">
    ${splitNote}
    <div class="detail-grid">
      <section class="panel"><div class="panel-header"><div><h3>Vị thế phân vị trong ngành (${esc(corr.sub_group_label)})</h3><p>Khối: ${esc(corr.macro_group_label)} · Kỳ BCTC gần nhất: ${esc(corr.latest_period || '—')} · Cỡ mẫu ngành: ${fmtInt(corr.peer_universe_size)} mã</p></div></div>${renderRadarSvg(corr.metrics)}</section>
      <section class="panel"><div class="panel-header"><div><h3>So sánh đa thang đo (Multi-scale vs Trung vị ngành)</h3><p>Tính trực tiếp từ dữ liệu Trang 4 (CSTC &amp; BCTT); mỗi chỉ số có trục tỷ lệ riêng.</p></div></div><div class="multiscale-grid">${barsHtml}</div></section>
    </div>
  </div>`;
}

function renderMultiSeriesChart(chart) {
  const series = chart?.series || [];
  if (!series.length) return '';
  const allPeriods = [...new Set(series.flatMap((s) => s.points.map((p) => p.period)))].sort();
  const allVals = series.flatMap((s) => s.points.map((p) => p.value)).filter((v) => Number.isFinite(v));
  if (!allVals.length) return '';
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
  const legend = series.map((s, idx) => `<span class="legend-item"><span class="legend-dot" style="background:${palette[idx % palette.length]}"></span>${esc(s.label)}</span>`).join('');
  return `<section class="panel"><div class="panel-header"><div><h3>${esc(chart.title)}</h3><p>Tự vẽ từ dữ liệu BCTC/CSTC</p></div></div><div class="chart-box"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(chart.title)}"><line x1="${pad.l}" x2="${w - pad.r}" y1="${pad.t + ph}" y2="${pad.t + ph}" stroke="#dde5e8"/><text x="${pad.l - 6}" y="${pad.t + 8}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(max, 1))}</text><text x="${pad.l - 6}" y="${pad.t + ph}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(fmtNum(min, 1))}</text>${paths}<text x="${pad.l}" y="${h - 6}" font-size="9" fill="#7c8b94">${esc(allPeriods[0] || '')}</text><text x="${w - pad.r}" y="${h - 6}" text-anchor="end" font-size="9" fill="#7c8b94">${esc(allPeriods.at(-1) || '')}</text></svg><div class="chart-legend">${legend}</div></div></section>`;
}

export function renderPage4Financials(bundle, params) {
  const fin = bundle.page4_financials || {};
  const rtype = params.get('rtype') === 'bctt' ? 'bctt' : 'cstc';
  const fquery = (params.get('fq') || '').trim().toLocaleLowerCase('vi');
  const pOffset = Math.max(0, Number(params.get('poff') || 0));
  const tableData = fin[rtype] || { periods: [], rows: [] };
  const allPeriods = tableData.periods || [];
  const winSize = 8;
  const clampedOff = Math.min(pOffset, Math.max(0, allPeriods.length - winSize));
  const shownPeriods = allPeriods.slice(clampedOff, clampedOff + winSize);
  const rows = (tableData.rows || []).filter((r) => !fquery || `${r.component || ''} ${r.field_name}`.toLocaleLowerCase('vi').includes(fquery));

  const chartsHtml = (fin.charts || []).map(renderMultiSeriesChart).join('');
  const headCols = shownPeriods.map((p) => `<th class="numeric">${esc(p)}</th>`).join('');
  const bodyRows = rows.map((r) => `<tr><td><span class="table-sub">${esc(r.component || '')}</span><strong>${esc(r.field_name)}</strong></td><td>${esc(r.unit || '—')}</td>${shownPeriods.map((p) => `<td class="numeric">${esc(r.values?.[p] != null ? fmtNum(r.values[p], 2) : '—')}</td>`).join('')}</tr>`).join('');

  const prevOff = Math.max(0, clampedOff - winSize);
  const nextOff = Math.min(Math.max(0, allPeriods.length - winSize), clampedOff + winSize);
  const baseHref = `?view=stocks&ticker=${encodeURIComponent(bundle.symbol)}&stab=financials`;

  return `<div class="yuanta-section">
    <div class="notice"><span class="notice-icon" aria-hidden="true">i</span><div><strong>Biểu đồ &amp; Báo cáo tài chính theo nhóm ngành: ${esc(fin.macro_group_label || fin.macro_group || '—')}</strong><p>Dữ liệu quý từ Q1/2010 đến hiện tại (${fmtInt(allPeriods.length)} kỳ báo cáo). Đơn vị giá trị tiền tệ theo chuẩn nguồn (1.000.000 VND = triệu đồng trừ khi cột đơn vị ghi khác).</p></div></div>
    ${chartsHtml ? `<div class="detail-grid">${chartsHtml}</div>` : ''}
    <div class="tab-strip">
      <a href="${baseHref}&rtype=cstc" data-route class="${rtype === 'cstc' ? 'active' : ''}">Chỉ số tài chính (CSTC · ${fmtInt((fin.cstc?.periods || []).length)} kỳ)</a>
      <a href="${baseHref}&rtype=bctt" data-route class="${rtype === 'bctt' ? 'active' : ''}">Báo cáo tài chính (BCTT · ${fmtInt((fin.bctt?.periods || []).length)} kỳ)</a>
    </div>
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="financials">
      <input type="hidden" name="rtype" value="${esc(rtype)}">
      <div class="field grow"><label for="fin-fq">Tìm khoản mục</label><input id="fin-fq" name="fq" type="search" value="${esc(params.get('fq') || '')}" placeholder="VD: ROE, NIM, Doanh thu, Hàng tồn kho"></div>
      <button class="button primary" type="submit">Lọc khoản mục</button>
      <a class="button" href="${baseHref}&rtype=${rtype}&poff=${prevOff}" data-route>← Kỳ mới hơn</a>
      <a class="button" href="${baseHref}&rtype=${rtype}&poff=${nextOff}" data-route>Kỳ cũ hơn (về 2010) →</a>
    </form>
    <section class="panel"><div class="panel-header"><div><h3>${rtype === 'cstc' ? 'Toàn bộ Chỉ số tài chính (CSTC)' : 'Toàn bộ Báo cáo tài chính (BCTT)'}</h3><p>Đang hiển thị kỳ ${esc(shownPeriods[0] || '—')} → ${esc(shownPeriods.at(-1) || '—')} (${fmtInt(rows.length)} chỉ tiêu)</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>Khoản mục</th><th>Đơn vị</th>${headCols}</tr></thead><tbody>${bodyRows || `<tr><td colspan="${2 + shownPeriods.length}" class="no-results">Không có khoản mục phù hợp.</td></tr>`}</tbody></table></div>
    </section>
  </div>`;
}

function renderPage5Events(bundle, params) {
  const ev = bundle.page5_events || {};
  const all = Array.isArray(ev.articles) ? ev.articles : [];
  const eq = (params.get('eq') || '').trim().toLocaleLowerCase('vi');
  const filtered = all.filter((a) => !eq || `${a.title || ''} ${a.category || ''} ${a.summary || ''}`.toLocaleLowerCase('vi').includes(eq));
  const page = Math.max(1, Number(params.get('epage') || 1));
  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, totalPages);
  const slice = filtered.slice((cur - 1) * pageSize, cur * pageSize);

  const rows = slice.map((item) => `<tr><td>${esc(item.published_date || (item.published_at || '').slice(0, 10) || '—')}</td><td>${esc(item.category || 'Sự kiện / Tin tức')}</td><td><strong>${esc(item.title || '—')}</strong>${item.summary ? `<span class="table-sub">${esc(item.summary)}</span>` : ''}</td><td>${item.url ? `<a class="small-link" href="${esc(item.url)}" target="_blank" rel="noreferrer">Mở bài ↗</a>` : '—'}</td></tr>`).join('');

  return `<div class="yuanta-section">
    <form class="control-bar" data-filter-form>
      <input type="hidden" name="ticker" value="${esc(bundle.symbol)}">
      <input type="hidden" name="stab" value="events">
      <div class="field grow"><label for="ev-q">Tìm tin tức &amp; sự kiện</label><input id="ev-q" name="eq" type="search" value="${esc(params.get('eq') || '')}" placeholder="VD: cổ tức, ĐHĐCĐ, phát hành"></div>
      <button class="button primary" type="submit">Tìm</button>
      <span class="toolbar-note">${fmtInt(filtered.length)} / ${fmtInt(ev.record_count ?? all.length)} sự kiện (từ 01/01/2010)</span>
    </form>
    <section class="panel"><div class="panel-header"><div><h3>Tin tức &amp; Sự kiện doanh nghiệp (${esc(bundle.symbol)})</h3><p>Nguồn: market_data/article · Trang ${cur} / ${totalPages}</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>Ngày</th><th>Nhóm</th><th>Tiêu đề / Nội dung</th><th>Liên kết</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="no-results">Không tìm thấy sự kiện phù hợp.</td></tr>'}</tbody></table></div>
    </section>
  </div>`;
}

export function renderYuantaStockTabs(symbol, stab, loadedEntry, params) {
  const tabs = [
    ['funds', 'Sở hữu quỹ mở'],
    ['overview', 'Trang 1: Tổng quan'],
    ['trading', 'Trang 2: Giao dịch'],
    ['correlation', 'Trang 3: Tương quan ngành'],
    ['financials', 'Trang 4: Báo cáo tài chính'],
    ['events', 'Trang 5: Tin tức & sự kiện'],
  ];
  const nav = `<div class="tab-strip yuanta-tabs">${tabs.map(([id, label]) => `<a href="?view=stocks&ticker=${encodeURIComponent(symbol)}&stab=${id}" data-route class="${stab === id ? 'active' : ''}" ${stab === id ? 'aria-current="page"' : ''}>${esc(label)}</a>`).join('')}</div>`;
  if (stab === 'funds') return { nav, body: null };
  if (!loadedEntry || loadedEntry.status === 'loading') {
    return { nav, body: `<div class="loading-panel"><span class="loading-mark" aria-hidden="true">${esc(symbol.slice(0, 3))}</span><p>Đang tải gói dữ liệu tĩnh ${esc(symbol)}…</p></div>` };
  }
  if (loadedEntry.status !== 'available' || !loadedEntry.bundle) {
    return { nav, body: renderNotSyncedNotice(symbol) };
  }
  const bundle = loadedEntry.bundle;
  let body;
  switch (stab) {
    case 'trading': body = renderPage2Trading(bundle, params); break;
    case 'correlation': body = renderPage3Correlation(bundle); break;
    case 'financials': body = renderPage4Financials(bundle, params); break;
    case 'events': body = renderPage5Events(bundle, params); break;
    default: body = renderPage1Overview(bundle);
  }
  return { nav, body };
}
