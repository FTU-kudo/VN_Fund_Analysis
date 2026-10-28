export const SUPPORTED_LANGS = ['vi', 'en'];
export const DEFAULT_LANG = 'vi';
export const LANG_STORAGE_KEY = 'vnfa-lang-v1';

const COLLATORS = {
  vi: new Intl.Collator('vi', { numeric: true, sensitivity: 'base' }),
  en: new Intl.Collator('en', { numeric: true, sensitivity: 'base' }),
};

const DATE_FORMATS = {
  vi: new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }),
  en: new Intl.DateTimeFormat('en-US', { month: 'short', day: '2-digit', year: 'numeric', timeZone: 'UTC' }),
};

const DATETIME_FORMATS = {
  vi: new Intl.DateTimeFormat('vi-VN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Ho_Chi_Minh' }),
  en: new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Ho_Chi_Minh' }),
};

const NUMBER_FORMAT_CACHE = new Map();

function getNumberFormatter(locale, optionsKey, options) {
  const key = `${locale}|${optionsKey}`;
  if (!NUMBER_FORMAT_CACHE.has(key)) {
    NUMBER_FORMAT_CACHE.set(key, new Intl.NumberFormat(locale, options));
  }
  return NUMBER_FORMAT_CACHE.get(key);
}

export function normalizeLang(raw) {
  if (typeof raw !== 'string') return null;
  const clean = raw.trim().toLowerCase();
  if (clean === 'en') return 'en';
  if (clean === 'vi') return 'vi';
  return null;
}

export function resolveLanguage(searchOrParams = null, storage = undefined) {
  if (searchOrParams !== null && searchOrParams !== undefined) {
    let rawParam = null;
    if (typeof searchOrParams === 'string') {
      const s = searchOrParams.startsWith('?') || searchOrParams.includes('=')
        ? new URLSearchParams(searchOrParams.replace(/^\?/, '')).get('lang')
        : searchOrParams;
      rawParam = s;
    } else if (typeof URLSearchParams !== 'undefined' && searchOrParams instanceof URLSearchParams) {
      rawParam = searchOrParams.get('lang');
    } else if (typeof URL !== 'undefined' && searchOrParams instanceof URL) {
      rawParam = searchOrParams.searchParams.get('lang');
    } else if (typeof searchOrParams?.get === 'function') {
      rawParam = searchOrParams.get('lang');
    }
    const fromParam = normalizeLang(rawParam);
    if (fromParam) return fromParam;
  } else if (typeof window !== 'undefined' && window.location?.search) {
    const fromWin = normalizeLang(new URLSearchParams(window.location.search).get('lang'));
    if (fromWin) return fromWin;
  }

  try {
    const saved = (storage === undefined ? globalThis.localStorage : storage)?.getItem?.(LANG_STORAGE_KEY);
    const fromStorage = normalizeLang(saved);
    if (fromStorage) return fromStorage;
  } catch {
    /* localStorage may be blocked or absent in Node */
  }
  return DEFAULT_LANG;
}

export function getLang(searchOrParams = null) {
  return resolveLanguage(searchOrParams);
}

export function setLang(lang, storage = undefined) {
  const normalized = normalizeLang(lang) || DEFAULT_LANG;
  try {
    (storage === undefined ? globalThis.localStorage : storage)?.setItem?.(LANG_STORAGE_KEY, normalized);
  } catch {
    /* storage may be disabled */
  }
  return normalized;
}

export function withLangHref(href, lang) {
  const normalized = normalizeLang(lang) || DEFAULT_LANG;
  const str = String(href || '');
  if (!str || (!str.startsWith('?') && !str.startsWith('./?') && !str.startsWith('/'))) return str;
  const prefix = str.startsWith('./?') ? './?' : str.startsWith('?') ? '?' : '';
  if (!prefix) return str;
  const [queryAndHash, hash = ''] = str.slice(prefix.length).split('#');
  const params = new URLSearchParams(queryAndHash);
  if (normalized === 'en') {
    params.set('lang', 'en');
  } else if (params.has('lang')) {
    params.set('lang', 'vi');
  }
  const suffix = hash ? `#${hash}` : '';
  return `${prefix}${params.toString()}${suffix}`;
}

export function localeTag(lang) {
  return normalizeLang(lang) === 'en' ? 'en-US' : 'vi-VN';
}

export function getCollator(lang = DEFAULT_LANG) {
  return COLLATORS[normalizeLang(lang) || DEFAULT_LANG];
}

export function formatNumber(value, digits = 2, lang = DEFAULT_LANG) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const loc = localeTag(lang);
  return getNumberFormatter(loc, `dec:${digits}`, { maximumFractionDigits: digits }).format(Number(value));
}

export function formatInteger(value, lang = DEFAULT_LANG, missingText = null) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) {
    if (missingText !== null) return missingText;
    return normalizeLang(lang) === 'en' ? 'Not available' : 'Chưa có dữ liệu';
  }
  const loc = localeTag(lang);
  return getNumberFormatter(loc, 'int', { maximumFractionDigits: 0 }).format(Number(value));
}

export function formatPercentFraction(value, digits = 2, lang = DEFAULT_LANG) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const loc = localeTag(lang);
  return getNumberFormatter(loc, `pct:${digits}`, { style: 'percent', maximumFractionDigits: digits }).format(Number(value));
}

export function formatPercentPoints(value, digits = 2, lang = DEFAULT_LANG) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  return `${formatNumber(value, digits, lang)}%`;
}

export function formatSignedPercentFraction(value, digits = 2, lang = DEFAULT_LANG) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const loc = localeTag(lang);
  return getNumberFormatter(loc, `spct:${digits}`, {
    style: 'percent',
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  }).format(Number(value)).replace(/^-/, '−');
}

export function formatSignedPercentPoints(value, digits = 2, lang = DEFAULT_LANG) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
  const loc = localeTag(lang);
  const formatted = getNumberFormatter(loc, `sdec:${digits}`, {
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  }).format(Number(value)).replace(/^-/, '−');
  return `${formatted}%`;
}

export function formatDate(value, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  if (!value) return nl === 'en' ? 'Not available' : 'Chưa có dữ liệu';
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(String(value));
  if (!dateOnly && typeof value === 'string' && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    return `${value} (${nl === 'en' ? 'timezone unknown' : 'chưa rõ múi giờ'})`;
  }
  const parsed = new Date(dateOnly ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(parsed.getTime())) return nl === 'en' ? 'Unknown' : 'Không xác định';
  return dateOnly ? DATE_FORMATS[nl].format(parsed) : `${DATETIME_FORMATS[nl].format(parsed)} (ICT / GMT+7)`;
}

export function formatDateTime(value, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  return value ? formatDate(value, nl) : (nl === 'en' ? 'Not recorded' : 'Chưa ghi nhận');
}

export const UI_STRINGS = {
  vi: {
    meta_description: 'Bảng điều khiển nghiên cứu quỹ và cổ phiếu Việt Nam — minh bạch nguồn, thời điểm và giới hạn dữ liệu.',
    skip_link: 'Bỏ qua điều hướng',
    sidebar_aria: 'Điều hướng chính',
    brand_aria: 'VN Fund Research, trang tổng quan',
    sidebar_explore: 'KHÁM PHÁ',
    sidebar_system: 'HỆ THỐNG',
    nav_overview: 'Tổng quan',
    nav_funds: 'Quỹ đầu tư',
    nav_holdings: 'Danh mục & thay đổi',
    nav_stocks: 'Nghiên cứu mã',
    nav_compare: 'So sánh quỹ',
    nav_quality: 'Chất lượng dữ liệu',
    nav_pipeline: 'Sức khỏe pipeline',
    nav_methodology: 'Phương pháp',
    privacy_note: 'Dữ liệu là quan sát và suy luận nghiên cứu — không phải khuyến nghị đầu tư.',
    github_link: 'Mã nguồn dự án',
    static_badge: 'Dữ liệu tĩnh · không gọi nguồn live',
    lang_group_aria: 'Chọn ngôn ngữ hiển thị',
    reload_aria: 'Tải lại dữ liệu dashboard',
    reload_title: 'Tải lại dữ liệu',
    loading_data: 'Đang tải dữ liệu nghiên cứu…',
    footer_brand: 'VN_Fund_Analysis · Bảng điều khiển nghiên cứu',
    footer_generated_unknown: 'Thời điểm tạo dữ liệu: chưa xác định',
    footer_generated: 'Bản xuất: {time} · schema {schema}',
    no_data: 'Chưa có dữ liệu',
    unknown: 'Chưa biết',
    not_recorded: 'Chưa ghi nhận',
    unclassified: 'Chưa phân loại',
    no_flag: 'Không có cờ',
    open_source: 'Mở nguồn ↗',
    no_source_ref: 'Không có source reference',
    sort_by: 'Sắp xếp theo {label}',
    no_matching_records: 'Không có bản ghi phù hợp với bộ lọc.',
    pagination_showing: 'Hiển thị {start}–{end} / {total} bản ghi',
    pagination_prev: '← Trước',
    pagination_page: 'Trang {page} / {totalPages}',
    pagination_next: 'Sau →',
    btn_filter: 'Lọc',
    btn_search: 'Tìm',
    btn_reset: 'Đặt lại',
    btn_compare: 'So sánh',
    btn_close: 'Đóng',
    btn_retry: 'Thử lại',
  },
  en: {
    meta_description: 'Vietnam open-end fund & 3-exchange equity research workspace — transparent provenance, point-in-time semantics, and explicit data limitations.',
    skip_link: 'Skip to main content',
    sidebar_aria: 'Primary navigation',
    brand_aria: 'VN Fund Research, overview page',
    sidebar_explore: 'EXPLORE',
    sidebar_system: 'SYSTEM',
    nav_overview: 'Overview',
    nav_funds: 'Funds',
    nav_holdings: 'Holdings & Changes',
    nav_stocks: 'Stock Research',
    nav_compare: 'Fund Comparison',
    nav_quality: 'Data Quality',
    nav_pipeline: 'Pipeline Health',
    nav_methodology: 'Methodology',
    privacy_note: 'Data represents research observations and inferences — not investment recommendations.',
    github_link: 'Source repository',
    static_badge: 'Static artifacts · zero live network calls',
    lang_group_aria: 'Select display language',
    reload_aria: 'Reload dashboard data',
    reload_title: 'Reload data',
    loading_data: 'Loading research datasets…',
    footer_brand: 'VN_Fund_Analysis · Research dashboard',
    footer_generated_unknown: 'Generated time: unknown',
    footer_generated: 'Export: {time} · schema {schema}',
    no_data: 'Not available',
    unknown: 'Unknown',
    not_recorded: 'Not recorded',
    unclassified: 'Unclassified',
    no_flag: 'No flag',
    open_source: 'Open source ↗',
    no_source_ref: 'No source reference',
    sort_by: 'Sort by {label}',
    no_matching_records: 'No records match the current filters.',
    pagination_showing: 'Showing {start}–{end} of {total} records',
    pagination_prev: '← Prev',
    pagination_page: 'Page {page} of {totalPages}',
    pagination_next: 'Next →',
    btn_filter: 'Filter',
    btn_search: 'Search',
    btn_reset: 'Reset',
    btn_compare: 'Compare',
    btn_close: 'Close',
    btn_retry: 'Retry',
  },
};

export function t(lang, key, vars = {}) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  const template = UI_STRINGS[nl]?.[key] ?? UI_STRINGS.vi?.[key] ?? key;
  return String(template).replace(/\{(\w+)\}/g, (_, k) => (Object.hasOwn(vars, k) ? String(vars[k]) : `{${k}}`));
}

// ── Yuanta 3-Group Taxonomy & Financial Statement Bilingual Dictionaries ──────

export const YUANTA_MACRO_GROUP_LABELS = {
  vi: {
    BANK: 'Ngân hàng',
    NON_BANK_FINANCIAL: 'Tài chính phi ngân hàng',
    NON_FINANCIAL: 'Phi tài chính',
  },
  en: {
    BANK: 'Banks',
    NON_BANK_FINANCIAL: 'Non-Bank Financials',
    NON_FINANCIAL: 'Non-Financials',
  },
};

export const YUANTA_SUB_GROUP_LABELS = {
  vi: {
    BANK: 'Ngân hàng',
    SECURITIES: 'Chứng khoán',
    INSURANCE: 'Bảo hiểm',
    FIN_SERVICES: 'Dịch vụ tài chính khác',
    REAL_ESTATE: 'Bất động sản (tách khỏi Tài chính L1)',
    TECHNOLOGY: 'Công nghệ & Viễn thông',
    CONSUMER_RETAIL: 'Bán lẻ & Hàng tiêu dùng',
    MATERIALS_STEEL: 'Tài nguyên cơ bản & Vật liệu',
    INDUSTRIALS: 'Công nghiệp & Xây dựng',
    ENERGY_UTILITIES: 'Dầu khí & Tiện ích',
    HEALTHCARE: 'Y tế & Dược phẩm',
    GENERAL_NON_FIN: 'Phi tài chính khác',
  },
  en: {
    BANK: 'Banks',
    SECURITIES: 'Securities & Brokerage',
    INSURANCE: 'Insurance',
    FIN_SERVICES: 'Other Financial Services',
    REAL_ESTATE: 'Real Estate (split from ICB L1 Financials)',
    TECHNOLOGY: 'Technology & Telecom',
    CONSUMER_RETAIL: 'Consumer & Retail',
    MATERIALS_STEEL: 'Basic Materials & Steel',
    INDUSTRIALS: 'Industrials & Construction',
    ENERGY_UTILITIES: 'Energy & Utilities',
    HEALTHCARE: 'Healthcare & Pharmaceuticals',
    GENERAL_NON_FIN: 'Other Non-Financials',
  },
};

export const YUANTA_METRIC_LABELS = {
  vi: {
    roe: 'ROE (%)',
    roa: 'ROA (%)',
    nim: 'NIM (%)',
    cir: 'CIR (%)',
    ldr: 'LDR (%)',
    equity_to_assets: 'VCSH / Tổng TS (%)',
    net_margin: 'Biên LN ròng (%)',
    gross_margin: 'Biên lãi gộp (%)',
    debt_to_equity: 'Nợ vay / VCSH',
    debt_to_assets: 'Nợ / Tổng TS (%)',
    current_ratio: 'Thanh toán hiện hành (x)',
    pe: 'P/E (x)',
    pb: 'P/B (x)',
  },
  en: {
    roe: 'ROE (%)',
    roa: 'ROA (%)',
    nim: 'NIM (%)',
    cir: 'Cost-to-Income CIR (%)',
    ldr: 'Loan-to-Deposit LDR (%)',
    equity_to_assets: 'Equity / Total Assets (%)',
    net_margin: 'Net Profit Margin (%)',
    gross_margin: 'Gross Margin (%)',
    debt_to_equity: 'Debt / Equity',
    debt_to_assets: 'Liabilities / Assets (%)',
    current_ratio: 'Current Ratio (x)',
    pe: 'P/E (x)',
    pb: 'P/B (x)',
  },
};

export const YUANTA_CHART_TITLES = {
  vi: {
    bank_income: 'Thu nhập lãi thuần & LNST',
    bank_profitability: 'Hiệu quả sinh lời: ROE, ROA & NIM (%)',
    bank_scale: 'Quy mô Tín dụng & Huy động',
    valuation_pe_pb: 'Định giá P/E & P/B',
    nbf_income: 'Doanh thu hoạt động & LNST',
    nbf_profitability: 'Hiệu quả sinh lời: ROE & ROA (%)',
    nbf_capital: 'Cơ cấu Tổng tài sản & Vốn chủ sở hữu',
    nf_income: 'Doanh thu thuần & Lợi nhuận sau thuế',
    nf_margins: 'Biên lợi nhuận & ROE (%)',
    nf_balance: 'Cơ cấu Nguồn vốn & Hàng tồn kho',
  },
  en: {
    bank_income: 'Net Interest Income & Net Profit',
    bank_profitability: 'Profitability: ROE, ROA & NIM (%)',
    bank_scale: 'Customer Loans & Deposits Scale',
    valuation_pe_pb: 'Valuation Multiples: P/E & P/B',
    nbf_income: 'Operating Revenue & Net Profit',
    nbf_profitability: 'Profitability: ROE & ROA (%)',
    nbf_capital: 'Total Assets & Shareholders’ Equity',
    nf_income: 'Net Revenue & Net Profit',
    nf_margins: 'Profit Margins & ROE (%)',
    nf_balance: 'Capital Structure & Inventories',
  },
};

const CHART_TITLE_BY_VI = {
  'Thu nhập lãi thuần & LNST': 'Net Interest Income & Net Profit',
  'Doanh thu thuần & LNST': 'Net Revenue & Net Profit',
  'Hiệu quả sinh lời: ROE, ROA & NIM (%)': 'Profitability: ROE, ROA & NIM (%)',
  'Quy mô Tín dụng & Huy động': 'Customer Loans & Deposits Scale',
  'Định giá P/E & P/B': 'Valuation Multiples: P/E & P/B',
  'Doanh thu hoạt động & LNST': 'Operating Revenue & Net Profit',
  'Hiệu quả sinh lời: ROE & ROA (%)': 'Profitability: ROE & ROA (%)',
  'Cơ cấu Tổng tài sản & Vốn chủ sở hữu': 'Total Assets & Shareholders’ Equity',
  'Doanh thu thuần & Lợi nhuận sau thuế': 'Net Revenue & Net Profit',
  'Biên lợi nhuận & ROE (%)': 'Profit Margins & ROE (%)',
  'Cơ cấu Nguồn vốn & Hàng tồn kho': 'Capital Structure & Inventories',
};

const SERIES_LABEL_EN = {
  nii: 'Net Interest Income',
  npat_bank: 'Attributable Net Profit',
  roe: 'ROE (%)',
  nim: 'NIM (%)',
  roa: 'ROA (%)',
  customer_loans: 'Customer Loans',
  customer_deposits: 'Customer Deposits',
  pe: 'P/E',
  pb: 'P/B',
  operating_revenue: 'Operating Revenue',
  npat: 'Net Profit After Tax',
  total_assets: 'Total Assets',
  equity: 'Shareholders’ Equity',
  net_revenue: 'Net Revenue',
  npat_parent: 'Parent NPAT',
  gross_margin: 'Gross Margin (%)',
  net_margin: 'Net Margin (%)',
  total_liabilities: 'Total Liabilities',
  inventories: 'Inventories',
};

const SERIES_LABEL_BY_VI = {
  'Thu nhập lãi thuần': 'Net Interest Income',
  'LNST cổ đông Ngân hàng': 'Attributable Net Profit',
  'LNST': 'Net Profit After Tax',
  'Lợi nhuận sau thuế': 'Net Profit After Tax',
  'LNST Công ty mẹ': 'Parent NPAT',
  'Doanh thu hoạt động': 'Operating Revenue',
  'Doanh thu thuần': 'Net Revenue',
  'Cho vay khách hàng': 'Customer Loans',
  'Tiền gửi khách hàng': 'Customer Deposits',
  'Tổng tài sản': 'Total Assets',
  'Nợ phải trả': 'Total Liabilities',
  'Vốn chủ sở hữu': 'Shareholders’ Equity',
  'Hàng tồn kho': 'Inventories',
  'Biên lãi gộp (%)': 'Gross Margin (%)',
  'Biên lãi ròng (%)': 'Net Margin (%)',
  'ROE (%)': 'ROE (%)',
  'ROA (%)': 'ROA (%)',
  'NIM (%)': 'NIM (%)',
  'P/E': 'P/E',
  'P/B': 'P/B',
};

function stripAccents(str) {
  if (!str) return '';
  return String(str)
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

const FINANCIAL_FIELD_EN_EXACT = {
  'doanh thu thuan ve ban hang va cung cap dich vu': 'Net Revenue from Sales & Services',
  'doanh thu ban hang va cung cap dich vu': 'Gross Revenue from Sales & Services',
  'gia von hang ban': 'Cost of Goods Sold (COGS)',
  'loi nhuan gop ve ban hang va cung cap dich vu': 'Gross Profit',
  'doanh thu hoat dong tai chinh': 'Financial Income',
  'chi phi tai chinh': 'Financial Expenses',
  'trong do: chi phi lai vay': 'Of which: Interest Expense',
  'chi phi lai vay': 'Interest Expense',
  'chi phi ban hang': 'Selling Expenses',
  'chi phi quan ly doanh nghiep': 'General & Administrative Expenses',
  'loi nhuan thuan tu hoat dong kinh doanh': 'Operating Profit',
  'tong loi nhuan ke toan truoc thue': 'Profit Before Tax (PBT)',
  'loi nhuan truoc thue': 'Profit Before Tax (PBT)',
  'loi nhuan sau thue thu nhap doanh nghiep': 'Net Profit After Tax (NPAT)',
  'loi nhuan sau thue cua co dong cua cong ty me': 'NPAT Attributable to Parent Shareholders',
  'loi nhuan sau thue cua co dong ngan hang': 'NPAT Attributable to Bank Shareholders',
  'thu nhap lai va cac khoan thu nhap tuong tu': 'Interest & Similar Income',
  'chi phi lai va cac chi phi tuong tu': 'Interest & Similar Expenses',
  'thu nhap lai thuan': 'Net Interest Income (NII)',
  'lai/lo thuan tu hoat dong dich vu': 'Net Fee & Commission Income',
  'lai/lo thuan tu hoat dong kinh doanh ngoai hoi': 'Net FX Trading Gain/Loss',
  'tong thu nhap hoat dong': 'Total Operating Income (TOI)',
  'chi phi hoat dong': 'Operating Expenses (OPEX)',
  'chi phi du phong rui ro tin dung': 'Credit Provision Expenses',
  'doanh thu hoat dong': 'Operating Revenue',
  'doanh thu nghiep vu moi gioi chung khoan': 'Brokerage Revenue',
  'lai tu cac tai san tai chinh ghi nhan thong qua lai/lo (fvtpl)': 'Gain from FVTPL Financial Assets',
  'lai tu cac khoan cho vay va phai thu': 'Income from Margin Loans & Receivables',
  'tai san ngan han': 'Current Assets',
  'tien va cac khoan tuong duong tien': 'Cash & Cash Equivalents',
  'dau tu tai chinh ngan han': 'Short-Term Financial Investments',
  'cac khoan phai thu ngan han': 'Short-Term Receivables',
  'hang ton kho': 'Inventories',
  'tai san dai han': 'Non-Current Assets',
  'tai san co dinh': 'Fixed Assets',
  'tai san do dang dai han': 'Long-Term Work in Progress',
  'bat dong san dau tu': 'Investment Properties',
  'tong cong tai san': 'Total Assets',
  'tong tai san': 'Total Assets',
  'cho vay khach hang': 'Customer Loans',
  'du phong rui ro cho vay khach hang': 'Loan Loss Reserves',
  'tien gui tai ngan hang nha nuoc': 'Balances with the State Bank (SBV)',
  'tien gui va cho vay cac to chuc tin dung khac': 'Placements & Loans to Other Credit Institutions',
  'tien gui cua khach hang': 'Customer Deposits',
  'phat hanh giay to co gia': 'Valuable Papers Issued',
  'no phai tra': 'Total Liabilities',
  'no ngan han': 'Current Liabilities',
  'no dai han': 'Non-Current Liabilities',
  'vay va no thue tai chinh ngan han': 'Short-Term Borrowings & Finance Leases',
  'vay va no thue tai chinh dai han': 'Long-Term Borrowings & Finance Leases',
  'nguoi mua tra tien truoc ngan han': 'Short-Term Customer Advances / Pre-sales',
  'von chu so huu': 'Shareholders’ Equity',
  'von gop cua chu so huu': 'Paid-in Charter Capital',
  'von dieu le': 'Charter Capital',
  'thang du von co phan': 'Share Premium',
  'loi nhuan sau thue chua phan phoi': 'Retained Earnings',
  'loi ich co dong khong kiem soat': 'Non-Controlling Interests',
  'eps 4 quy gan nhat': 'Trailing 4Q EPS',
  'eps co ban': 'Basic EPS',
  'bvps': 'Book Value Per Share (BVPS)',
  'p/e': 'P/E',
  'p/b': 'P/B',
  'p/s': 'P/S',
  'ev/ebitda': 'EV/EBITDA',
  'roe': 'ROE',
  'roa': 'ROA',
  'roic': 'ROIC',
  'nim': 'Net Interest Margin (NIM)',
  'cir': 'Cost-to-Income Ratio (CIR)',
  'ldr': 'Loan-to-Deposit Ratio (LDR)',
  'ty le no xau': 'Non-Performing Loan Ratio (NPL)',
  'ty suat loi nhuan gop bien': 'Gross Profit Margin',
  'ty suat sinh loi tren doanh thu thuan': 'Net Profit Margin (ROS)',
  'ty suat sinh loi tren von chu so huu': 'Return on Equity (ROE)',
  'ty suat sinh loi tren tong tai san': 'Return on Assets (ROA)',
  'ty so thanh toan hien hanh (ngan han)': 'Current Ratio',
  'ty so thanh toan nhanh': 'Quick Ratio',
  'ty so no tren tong tai san': 'Total Liabilities / Total Assets',
  'ty so no vay tren von chu so huu': 'Debt / Equity Ratio',
  'vong quay hang ton kho': 'Inventory Turnover',
  'vong quay khoan phai thu': 'Receivables Turnover',
  'vong quay tong tai san': 'Total Asset Turnover',
};

const COMPONENT_EN_MAP = {
  'ket qua kinh doanh': 'Income Statement',
  'can doi ke toan': 'Balance Sheet',
  'ket qua kinh doanh & can doi ke toan': 'Income Statement & Balance Sheet',
  'luu chuyen tien te': 'Cash Flow Statement',
  'chi so dinh gia': 'Valuation Multiples',
  'chi so dinh gia & sinh loi': 'Valuation & Profitability Ratios',
  'chi so sinh loi': 'Profitability Ratios',
  'kha nang sinh loi': 'Profitability Ratios',
  'chi so thanh toan': 'Liquidity Ratios',
  'kha nang thanh toan': 'Liquidity Ratios',
  'co cau nguon von': 'Capital Structure & Solvency',
  'chi so hoat dong': 'Efficiency & Turnover Ratios',
  'hieu qua hoat dong': 'Efficiency & Turnover Ratios',
  'tang truong': 'Growth Metrics',
  'kqkd': 'Income Statement',
  'cdkt': 'Balance Sheet',
};

const UNIT_EN_MAP = {
  'trieu dong': 'Million VND',
  'ty dong': 'Billion VND',
  'nghin dong': 'Thousand VND',
  'dong': 'VND',
  'vnd': 'VND',
  'lan': 'x',
  '%': '%',
  'co phieu': 'Shares',
  'ngay': 'Days',
};

export function translateMacroGroup(macroGroup, fallbackLabel, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  return YUANTA_MACRO_GROUP_LABELS[nl]?.[macroGroup] || fallbackLabel || macroGroup || '—';
}

export function translateSubGroup(subGroup, fallbackLabel, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  return YUANTA_SUB_GROUP_LABELS[nl]?.[subGroup] || fallbackLabel || subGroup || '—';
}

export function translateCorrelationMetric(metricKey, fallbackLabel, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  return YUANTA_METRIC_LABELS[nl]?.[metricKey] || fallbackLabel || metricKey || '—';
}

export function translateFinancialField(fieldName, lang = DEFAULT_LANG) {
  const raw = String(fieldName || '').trim();
  if (!raw || normalizeLang(lang) !== 'en') return raw;
  const norm = stripAccents(raw);
  return FINANCIAL_FIELD_EN_EXACT[norm] || raw;
}

export function translateFinancialComponent(componentName, lang = DEFAULT_LANG) {
  const raw = String(componentName || '').trim();
  if (!raw || normalizeLang(lang) !== 'en') return raw;
  const norm = stripAccents(raw);
  return COMPONENT_EN_MAP[norm] || raw;
}

export function translateFinancialUnit(unit, lang = DEFAULT_LANG) {
  const raw = String(unit || '').trim();
  if (!raw) return '—';
  if (normalizeLang(lang) !== 'en') return raw;
  const norm = stripAccents(raw);
  return UNIT_EN_MAP[norm] || raw;
}

export function translateChartTitle(chart, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  if (chart?.chart_id && YUANTA_CHART_TITLES[nl]?.[chart.chart_id]) {
    return YUANTA_CHART_TITLES[nl][chart.chart_id];
  }
  if (nl === 'en' && chart?.title && CHART_TITLE_BY_VI[chart.title]) {
    return CHART_TITLE_BY_VI[chart.title];
  }
  return chart?.title || '';
}

export function translateChartSeriesLabel(series, lang = DEFAULT_LANG) {
  const nl = normalizeLang(lang) || DEFAULT_LANG;
  if (nl === 'en') {
    if (series?.series_key && SERIES_LABEL_EN[series.series_key]) return SERIES_LABEL_EN[series.series_key];
    if (series?.label && SERIES_LABEL_BY_VI[series.label]) return SERIES_LABEL_BY_VI[series.label];
  }
  return series?.label || '';
}
