# -*- coding: utf-8 -*-
"""Yuanta YSradar 3-exchange (HOSE, HNX, UPCOM) scanner, parser, and 3-group sector engine.

Covers all 5 analytical modules specified for stock-level research:
  1. Trang 1 (Tổng quan): rating info, profile & leadership, shareholders, quick quote,
     bid/offer orderbook, matched transactions, price-board info, stock rating, and top peers.
  2. Trang 2 (Dữ liệu giao dịch): OHLCV + foreign flow (2010–present), detailed foreign history
     (2025–present), and the 3 foreign screener chart series.
  3. Trang 3 (Tương quan trong ngành): computed directly from Trang 4 financial statements across
     3 macro groups (`BANK`, `NON_BANK_FINANCIAL`, `NON_FINANCIAL`) while explicitly splitting key
     sub-sectors such as Real Estate (`Bất động sản`, ICB 8600) out of L1 Financials (`8000`).
  4. Trang 4 (Báo cáo tài chính): full quarterly/annual pagination (`Q1/2010`–present) for `CSTC`
     (financial ratios) and `BCTT` (financial statements) + 3-group chart series builder.
  5. Trang 5 (Tin tức & sự kiện): corporate events and articles (`2010-01-01`–present).

Design invariants:
  - Missing values remain `None`/`NaN` (never silently coerced to `0`).
  - Browser code never calls `ysradarapi.yuanta.com.vn` directly; static `.json.gz` bundles are
    exported by `scripts/scan_yuanta_universe.py`.
  - No credentials, cookies, or raw HTTP bodies are persisted in `source_events`.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import re
import threading
import time
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("vnfa.yuanta")

YUANTA_BASE_URL = "https://ysradarapi.yuanta.com.vn"

EXCHANGE_QUERY_MAP = {
    "HOSE": "HSX",
    "HSX": "HSX",
    "HNX": "HNX",
    "UPCOM": "UPCOM",
}
EXCHANGE_CANONICAL_MAP = {
    "HSX": "HOSE",
    "HOSE": "HOSE",
    "HNX": "HNX",
    "UPCOM": "UPCOM",
}

MACRO_GROUP_LABELS = {
    "BANK": "Ngân hàng",
    "NON_BANK_FINANCIAL": "Tài chính phi ngân hàng",
    "NON_FINANCIAL": "Phi tài chính",
}

SUB_GROUP_LABELS = {
    "BANK": "Ngân hàng",
    "SECURITIES": "Chứng khoán",
    "INSURANCE": "Bảo hiểm",
    "FIN_SERVICES": "Dịch vụ tài chính khác",
    "REAL_ESTATE": "Bất động sản (tách khỏi Tài chính L1)",
    "TECHNOLOGY": "Công nghệ & Viễn thông",
    "CONSUMER_RETAIL": "Bán lẻ & Hàng tiêu dùng",
    "MATERIALS_STEEL": "Tài nguyên cơ bản & Vật liệu",
    "INDUSTRIALS": "Công nghiệp & Xây dựng",
    "ENERGY_UTILITIES": "Dầu khí & Tiện ích",
    "HEALTHCARE": "Y tế & Dược phẩm",
    "GENERAL_NON_FIN": "Phi tài chính khác",
}

OHLCV_RENAME = {
    "TradingDate": "trading_date",
    "PerChange": "pct_change",
    "Close": "close",
    "Open": "open",
    "Highest": "high",
    "Lowest": "low",
    "Average": "average",
    "Volume": "volume",
    "Value": "value",
    "ForeignVolumeBuy": "foreign_buy_vol",
    "ForeignVolumeSell": "foreign_sell_vol",
    "PutThroughVolume": "put_through_vol",
    "PutThroughValue": "put_through_val",
}

FOREIGN_RENAME = {
    "TradingDate": "trading_date",
    "RemainRoom": "remain_room",
    "BuyVol": "foreign_buy_vol",
    "BuyVal": "foreign_buy_val",
    "SellVol": "foreign_sell_vol",
    "SellVal": "foreign_sell_val",
    "NetVol": "foreign_net_vol",
    "NetVal": "foreign_net_val",
    "ForeignValRate": "foreign_val_rate",
    "ForeignOwnedRate": "foreign_owned_rate",
}

# Canonical metric definitions per macro group for Trang 3 (Peer Correlation) & Trang 4 (Charts).
GROUP_CORRELATION_METRICS: dict[str, list[dict[str, Any]]] = {
    "BANK": [
        {"key": "roe", "label": "ROE (%)", "unit": "%", "higher_better": True,
         "patterns": ["roe", "ty suat sinh loi tren von chu so huu"]},
        {"key": "roa", "label": "ROA (%)", "unit": "%", "higher_better": True,
         "patterns": ["roa", "ty suat sinh loi tren tong tai san"]},
        {"key": "nim", "label": "NIM (%)", "unit": "%", "higher_better": True,
         "patterns": ["nim", "ty le thu nhap lai thuan"]},
        {"key": "cir", "label": "CIR (%)", "unit": "%", "higher_better": False,
         "patterns": ["cir", "chi phi hoat dong tren tong thu nhap", "ty le chi phi tren thu nhap"]},
        {"key": "ldr", "label": "LDR (%)", "unit": "%", "higher_better": False,
         "patterns": ["ldr", "cho vay tren huy dong", "ty le du no tren huy dong"]},
        {"key": "equity_to_assets", "label": "VCSH / Tổng TS (%)", "unit": "%", "higher_better": True,
         "patterns": ["von chu so huu tren tong tai san", "vcsh/tong ts"]},
        {"key": "pe", "label": "P/E (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/e$", "^pe$"]},
        {"key": "pb", "label": "P/B (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/b$", "^pb$"]},
    ],
    "NON_BANK_FINANCIAL": [
        {"key": "roe", "label": "ROE (%)", "unit": "%", "higher_better": True,
         "patterns": ["roe", "ty suat sinh loi tren von chu so huu"]},
        {"key": "roa", "label": "ROA (%)", "unit": "%", "higher_better": True,
         "patterns": ["roa", "ty suat sinh loi tren tong tai san"]},
        {"key": "net_margin", "label": "Biên LN ròng (%)", "unit": "%", "higher_better": True,
         "patterns": ["ty suat sinh loi tren doanh thu thuan", "bien loi nhuan rong", "ros"]},
        {"key": "debt_to_equity", "label": "Nợ vay / VCSH (x/%)", "unit": "ratio", "higher_better": False,
         "patterns": ["ty so no vay tren von chu so huu", "no tren von chu so huu", "d/e"]},
        {"key": "debt_to_assets", "label": "Nợ / Tổng TS (%)", "unit": "%", "higher_better": False,
         "patterns": ["ty so no tren tong tai san", "no phai tra tren tong tai san"]},
        {"key": "current_ratio", "label": "Thanh toán hiện hành (x)", "unit": "x", "higher_better": True,
         "patterns": ["ty so thanh toan hien hanh"]},
        {"key": "pe", "label": "P/E (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/e$", "^pe$"]},
        {"key": "pb", "label": "P/B (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/b$", "^pb$"]},
    ],
    "NON_FINANCIAL": [
        {"key": "roe", "label": "ROE (%)", "unit": "%", "higher_better": True,
         "patterns": ["roe", "ty suat sinh loi tren von chu so huu"]},
        {"key": "roa", "label": "ROA (%)", "unit": "%", "higher_better": True,
         "patterns": ["roa", "ty suat sinh loi tren tong tai san"]},
        {"key": "gross_margin", "label": "Biên lãi gộp (%)", "unit": "%", "higher_better": True,
         "patterns": ["ty suat loi nhuan gop bien", "bien lai gop", "bien loi nhuan gop"]},
        {"key": "net_margin", "label": "Biên lãi ròng (%)", "unit": "%", "higher_better": True,
         "patterns": ["ty suat sinh loi tren doanh thu thuan", "bien loi nhuan rong", "ros"]},
        {"key": "debt_to_equity", "label": "Nợ vay / VCSH", "unit": "ratio", "higher_better": False,
         "patterns": ["ty so no vay tren von chu so huu", "no vay/vcsh"]},
        {"key": "current_ratio", "label": "Thanh toán hiện hành (x)", "unit": "x", "higher_better": True,
         "patterns": ["ty so thanh toan hien hanh"]},
        {"key": "pe", "label": "P/E (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/e$", "^pe$"]},
        {"key": "pb", "label": "P/B (x)", "unit": "x", "higher_better": False,
         "patterns": ["^p/b$", "^pb$"]},
    ],
}


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    cleaned = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _finite_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool) or val == "":
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def normalize_symbol(raw: Any) -> str | None:
    """Validate and uppercase a Vietnam stock ticker (preserving literal 'NA'/'NAN'!)."""
    if raw is None or isinstance(raw, bool):
        return None
    sym = str(raw).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,5}", sym):
        return None
    return sym


def is_equity_stock_row(row: dict[str, Any]) -> bool:
    """Filter exchange board rows to listed/registered equity stocks (exclude CWs, bonds, futures)."""
    sym = normalize_symbol(row.get("StockCode") or row.get("symbol") or row.get("code"))
    if not sym or len(sym) > 4:
        return False
    stock_type = str(row.get("StockType") or row.get("stock_type") or row.get("SecType") or "ST").strip().upper()
    if stock_type and stock_type not in {"ST", "STOCK", "SH", "EQ", ""}:
        return False
    # Exclude covered warrants starting with C + 2 digits on HOSE if untyped.
    if len(sym) == 4 and sym.startswith("C") and sym[2:].isdigit():
        return False
    return True


def classify_sector_taxonomy(
    *,
    sector_name: str | None = None,
    icb_l1: str | None = None,
    icb_l2: str | None = None,
    icb_l3: str | None = None,
    icb_code: str | None = None,
    financial_fields: list[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Classify a stock into the 3 macro groups while splitting Real Estate out of L1 Financials.

    Returns:
      {
        "macro_group": "BANK" | "NON_BANK_FINANCIAL" | "NON_FINANCIAL",
        "macro_group_label": "Ngân hàng" | "Tài chính phi ngân hàng" | "Phi tài chính",
        "sub_group": "BANK" | "SECURITIES" | "INSURANCE" | "REAL_ESTATE" | ...,
        "sub_group_label": ...,
        "peer_group_key": ...,
        "split_from_financial_l1": bool,
      }
    """
    joined = " | ".join(filter(None, [icb_l3, icb_l2, sector_name, icb_l1]))
    norm = _strip_accents(joined)
    l1_norm = _strip_accents(icb_l1 or "")
    code_str = str(icb_code or "").strip()
    fset = {_strip_accents(f) for f in (financial_fields or []) if f}

    # 1. Explicit Real Estate check FIRST so ICB L1 "Tài chính" (8000 -> 8600 Bất động sản)
    #    is ALWAYS separated into NON_FINANCIAL / REAL_ESTATE per Trang 3 requirement!
    if (
        code_str.startswith("86")
        or "bat dong san" in norm
        or "real estate" in norm
    ):
        split_flag = ("tai chinh" in l1_norm or "financial" in l1_norm or code_str.startswith("86"))
        return {
            "macro_group": "NON_FINANCIAL",
            "macro_group_label": MACRO_GROUP_LABELS["NON_FINANCIAL"],
            "sub_group": "REAL_ESTATE",
            "sub_group_label": SUB_GROUP_LABELS["REAL_ESTATE"],
            "peer_group_key": "REAL_ESTATE",
            "split_from_financial_l1": bool(split_flag),
        }

    # 2. Explicit ICB / sector-name classification before statement-field heuristics
    #    (so margin-lending brokerages or conglomerates are never misclassified as BANK!).
    if code_str.startswith("83") or "ngan hang" in norm or re.search(r"\bbanks?\b", norm):
        return {
            "macro_group": "BANK",
            "macro_group_label": MACRO_GROUP_LABELS["BANK"],
            "sub_group": "BANK",
            "sub_group_label": SUB_GROUP_LABELS["BANK"],
            "peer_group_key": "BANK",
            "split_from_financial_l1": False,
        }

    # 3. Non-Bank Financials: Securities / Brokerage, Insurance, Fund/Asset Management.
    if code_str.startswith("85") or "bao hiem" in norm or "insurance" in norm:
        return {
            "macro_group": "NON_BANK_FINANCIAL",
            "macro_group_label": MACRO_GROUP_LABELS["NON_BANK_FINANCIAL"],
            "sub_group": "INSURANCE",
            "sub_group_label": SUB_GROUP_LABELS["INSURANCE"],
            "peer_group_key": "INSURANCE",
            "split_from_financial_l1": False,
        }
    if (
        code_str.startswith("87")
        or "chung khoan" in norm
        or "dich vu tai chinh" in norm
        or "financial services" in norm
        or "securities" in norm
    ):
        return {
            "macro_group": "NON_BANK_FINANCIAL",
            "macro_group_label": MACRO_GROUP_LABELS["NON_BANK_FINANCIAL"],
            "sub_group": "SECURITIES",
            "sub_group_label": SUB_GROUP_LABELS["SECURITIES"],
            "peer_group_key": "SECURITIES",
            "split_from_financial_l1": False,
        }

    # 3b. Statement-field fallback ONLY when sector metadata is absent or generic "Tài chính".
    has_bank_fields = any("thu nhap lai thuan" in f or "tien gui cua khach hang" in f for f in fset)
    has_sec_fields = any("moi gioi chung khoan" in f or "tai san tai chinh ghi nhan thong qua lai/lo" in f for f in fset)
    if not norm or norm in {"tai chinh", "financial", "financials"}:
        if has_bank_fields:
            return {
                "macro_group": "BANK",
                "macro_group_label": MACRO_GROUP_LABELS["BANK"],
                "sub_group": "BANK",
                "sub_group_label": SUB_GROUP_LABELS["BANK"],
                "peer_group_key": "BANK",
                "split_from_financial_l1": False,
            }
        if has_sec_fields:
            return {
                "macro_group": "NON_BANK_FINANCIAL",
                "macro_group_label": MACRO_GROUP_LABELS["NON_BANK_FINANCIAL"],
                "sub_group": "SECURITIES",
                "sub_group_label": SUB_GROUP_LABELS["SECURITIES"],
                "peer_group_key": "SECURITIES",
                "split_from_financial_l1": False,
            }

    if "tai chinh" in norm or "financial" in norm or code_str.startswith("8"):
        return {
            "macro_group": "NON_BANK_FINANCIAL",
            "macro_group_label": MACRO_GROUP_LABELS["NON_BANK_FINANCIAL"],
            "sub_group": "FIN_SERVICES",
            "sub_group_label": SUB_GROUP_LABELS["FIN_SERVICES"],
            "peer_group_key": "FIN_SERVICES",
            "split_from_financial_l1": False,
        }

    # 4. Non-Financial key sub-sectors.
    if any(k in norm for k in ("cong nghe", "vien thong", "phan mem", "technology", "telecom")):
        sub = "TECHNOLOGY"
    elif any(k in norm for k in ("ban le", "tieu dung", "thuc pham", "do uong", "retail", "consumer", "food")):
        sub = "CONSUMER_RETAIL"
    elif any(k in norm for k in ("thep", "tai nguyen", "vat lieu", "hoa chat", "khai khoang", "steel", "materials", "chemical")):
        sub = "MATERIALS_STEEL"
    elif any(k in norm for k in ("dau khi", "dien", "nuoc", "khi dot", "nang luong", "oil", "gas", "utilities", "power")):
        sub = "ENERGY_UTILITIES"
    elif any(k in norm for k in ("y te", "duoc", "health", "pharma")):
        sub = "HEALTHCARE"
    elif any(k in norm for k in ("xay dung", "cong nghiep", "van tai", "cang", "industrial", "construction", "logistics")):
        sub = "INDUSTRIALS"
    else:
        sub = "GENERAL_NON_FIN"

    return {
        "macro_group": "NON_FINANCIAL",
        "macro_group_label": MACRO_GROUP_LABELS["NON_FINANCIAL"],
        "sub_group": sub,
        "sub_group_label": SUB_GROUP_LABELS[sub],
        "peer_group_key": sub,
        "split_from_financial_l1": False,
    }


def _unwrap_payload(payload: Any) -> Any:
    """Extract primary data payload across Yuanta `/api/v3/` (`response`) and `/ysw/api/v3/` (`data`)."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    if "response" in payload and payload["response"] is not None:
        return payload["response"]
    if "data" in payload and payload["data"] is not None:
        return payload["data"]
    return payload


def parse_ohlcv_and_foreign(
    price_history_payload: Any,
    foreign_history_payload: Any = None,
    *,
    symbol: str,
) -> pd.DataFrame:
    """Normalize and merge Trang 2 `price_history` (2010–nay) and `price_history_foreign` (2025–nay)."""
    p_rows = _unwrap_payload(price_history_payload)
    f_rows = _unwrap_payload(foreign_history_payload)
    p_list = p_rows if isinstance(p_rows, list) else []
    f_list = f_rows if isinstance(f_rows, list) else []

    base_cols = [
        "symbol", "trading_date", "open", "high", "low", "close", "average", "pct_change",
        "volume", "value", "put_through_vol", "put_through_val",
        "foreign_buy_vol", "foreign_sell_vol", "foreign_net_vol",
        "foreign_buy_val", "foreign_sell_val", "foreign_net_val",
        "remain_room", "foreign_val_rate", "foreign_owned_rate",
    ]
    if not p_list and not f_list:
        return pd.DataFrame(columns=base_cols)

    df_p = pd.DataFrame(p_list).rename(columns=OHLCV_RENAME) if p_list else pd.DataFrame(columns=["trading_date"])
    if "trading_date" in df_p.columns and not df_p.empty:
        df_p["trading_date"] = df_p["trading_date"].astype(str).str.slice(0, 10)
        df_p = df_p[df_p["trading_date"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
        df_p = df_p.drop_duplicates(subset=["trading_date"], keep="last")

    df_f = pd.DataFrame(f_list).rename(columns=FOREIGN_RENAME) if f_list else pd.DataFrame(columns=["trading_date"])
    if "trading_date" in df_f.columns and not df_f.empty:
        df_f["trading_date"] = df_f["trading_date"].astype(str).str.slice(0, 10)
        df_f = df_f[df_f["trading_date"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
        df_f = df_f.drop_duplicates(subset=["trading_date"], keep="last")

    if df_p.empty:
        merged = df_f.copy()
    elif df_f.empty:
        merged = df_p.copy()
    else:
        overlap = [c for c in df_f.columns if c in df_p.columns and c != "trading_date"]
        merged = df_p.merge(df_f, on="trading_date", how="outer", suffixes=("_price", ""))
        for col in overlap:
            price_col = f"{col}_price"
            if price_col in merged.columns:
                merged[col] = merged[col].combine_first(merged[price_col])
                merged = merged.drop(columns=[price_col])

    merged["symbol"] = symbol
    for col in base_cols:
        if col not in merged.columns:
            merged[col] = np.nan
        elif col not in {"symbol", "trading_date"}:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    # Derive foreign_net_vol only when both buy & sell are non-null and net is missing.
    missing_net_vol = merged["foreign_net_vol"].isna() & merged["foreign_buy_vol"].notna() & merged["foreign_sell_vol"].notna()
    merged.loc[missing_net_vol, "foreign_net_vol"] = (
        merged.loc[missing_net_vol, "foreign_buy_vol"] - merged.loc[missing_net_vol, "foreign_sell_vol"]
    )
    missing_net_val = merged["foreign_net_val"].isna() & merged["foreign_buy_val"].notna() & merged["foreign_sell_val"].notna()
    merged.loc[missing_net_val, "foreign_net_val"] = (
        merged.loc[missing_net_val, "foreign_buy_val"] - merged.loc[missing_net_val, "foreign_sell_val"]
    )

    return merged[base_cols].sort_values("trading_date").reset_index(drop=True)


def parse_financial_report_page(
    payload: Any,
    *,
    symbol: str,
    report_type: str,
    start_year: int = 2010,
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Parse one Yuanta `/vietstock/financial_statement/report` page into tidy long records.

    Supports both:
      1) Standard nested `response.title.data` + `response.detail[].data[]` structure, and
      2) Pre-flattened wide rows (`report_component`, `field_name`, `2026_Q2` / `Q2/2026`).
    Returns `(records, observed_periods_sorted)` where each period is `(year, term_code)`.
    """
    body = _unwrap_payload(payload)
    records: list[dict[str, Any]] = []
    periods_seen: list[tuple[int, str]] = []

    if isinstance(body, dict) and "detail" in body and "title" in body:
        raw_periods = (body.get("title") or {}).get("data") or []
        period_meta: list[tuple[int, str, str] | None] = []
        for p in raw_periods:
            try:
                yr = int(p.get("year_period"))
            except (TypeError, ValueError):
                period_meta.append(None)
                continue
            term = str(p.get("term_code") or "FY").strip().upper()
            label = f"{yr}_{term}" if term != "FY" else f"{yr}_FY"
            period_meta.append((yr, term, label))
            periods_seen.append((yr, term))

        for comp_idx, comp in enumerate(body.get("detail") or []):
            comp_name = str(comp.get("report_component_name") or comp.get("report_component") or "").strip()
            for field_idx, item in enumerate(comp.get("data") or []):
                fname = str(item.get("field_name") or "").strip()
                if not fname:
                    continue
                unit = str(item.get("unit") or "").strip() or None
                values = item.get("value") or []
                for idx, raw_val in enumerate(values):
                    if idx >= len(period_meta) or period_meta[idx] is None:
                        continue
                    yr, term, period_key = period_meta[idx]
                    if yr < start_year:
                        continue
                    q_num = int(term[1]) if re.fullmatch(r"Q[1-4]", term) else None
                    records.append({
                        "symbol": symbol,
                        "report_type": report_type,
                        "period": period_key,
                        "year": yr,
                        "term_code": term,
                        "quarter": q_num,
                        "component_order": comp_idx,
                        "field_order": field_idx,
                        "report_component": comp_name,
                        "field_name": fname,
                        "unit": unit,
                        "value": _finite_float(raw_val),
                    })
    elif isinstance(body, list):
        for field_idx, row in enumerate(body):
            if not isinstance(row, dict):
                continue
            comp_name = str(row.get("report_component") or row.get("report_component_name") or "").strip()
            fname = str(row.get("field_name") or "").strip()
            if not fname:
                continue
            unit = str(row.get("unit") or "").strip() or None
            for k, raw_val in row.items():
                m1 = re.fullmatch(r"(\d{4})_(Q[1-4]|FY)", str(k))
                m2 = re.fullmatch(r"(Q[1-4]|FY)/(\d{4})", str(k))
                if m1:
                    yr, term = int(m1.group(1)), m1.group(2)
                elif m2:
                    term, yr = m2.group(1), int(m2.group(2))
                else:
                    continue
                periods_seen.append((yr, term))
                if yr < start_year:
                    continue
                q_num = int(term[1]) if term.startswith("Q") else None
                records.append({
                    "symbol": symbol,
                    "report_type": report_type,
                    "period": f"{yr}_{term}",
                    "year": yr,
                    "term_code": term,
                    "quarter": q_num,
                    "component_order": 0,
                    "field_order": field_idx,
                    "report_component": comp_name,
                    "field_name": fname,
                    "unit": unit,
                    "value": _finite_float(raw_val),
                })

    return records, periods_seen


def parse_articles(payload: Any, *, symbol: str) -> list[dict[str, Any]]:
    """Normalize Trang 5 `/ysw/api/v3/market_data/article` corporate news & events."""
    items = _unwrap_payload(payload)
    if isinstance(items, dict) and isinstance(items.get("data"), list):
        items = items["data"]
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        art_id = str(row.get("ArticleID") or row.get("ID") or row.get("id") or "")
        title = str(row.get("Title") or row.get("Content") or row.get("title") or "").strip()
        pub_raw = str(row.get("PublishTime") or row.get("PublishedDate") or row.get("ExRightDate") or row.get("CreateDate") or "").strip()
        pub_date = pub_raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", pub_raw) else None
        url = str(row.get("Url") or row.get("Link") or row.get("FileUrl") or "").strip() or None
        channel = str(row.get("ChannelName") or row.get("Type") or row.get("Category") or "").strip() or None
        summary = str(row.get("Head") or row.get("Summary") or "").strip() or None
        dedup_key = art_id or f"{pub_raw}|{title}"
        if not title and not art_id:
            continue
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)
        out.append({
            "symbol": symbol,
            "article_id": art_id or None,
            "published_at": pub_raw or None,
            "published_date": pub_date,
            "category": channel,
            "title": title,
            "summary": summary,
            "url": url if url and url.startswith(("https://", "http://")) else None,
        })
    out.sort(key=lambda r: (r.get("published_at") or r.get("published_date") or ""), reverse=True)
    return out


def extract_latest_metrics_for_correlation(
    cstc_df: pd.DataFrame,
    bctt_df: pd.DataFrame,
    *,
    macro_group: str,
) -> dict[str, Any]:
    """Extract latest non-null financial metrics for Trang 3 peer correlation from Trang 4 tables."""
    metrics_spec = GROUP_CORRELATION_METRICS.get(macro_group, GROUP_CORRELATION_METRICS["NON_FINANCIAL"])
    combined = pd.concat([df for df in (cstc_df, bctt_df) if df is not None and not df.empty], ignore_index=True) \
        if any(df is not None and not df.empty for df in (cstc_df, bctt_df)) else pd.DataFrame()
    if combined.empty:
        return {"latest_period": None, "values": {m["key"]: None for m in metrics_spec}}

    valid = combined[combined["value"].notna()].copy()
    if valid.empty:
        return {"latest_period": None, "values": {m["key"]: None for m in metrics_spec}}

    valid["_norm_field"] = valid["field_name"].map(_strip_accents)
    valid = valid.sort_values(["year", "term_code"], ascending=[True, True])
    latest_period = str(valid.iloc[-1]["period"])

    extracted: dict[str, float | None] = {}
    periods_used: dict[str, str | None] = {}
    for spec in metrics_spec:
        val_found: float | None = None
        per_found: str | None = None
        for pat in spec["patterns"]:
            if pat.startswith("^"):
                mask = valid["_norm_field"].str.match(pat, na=False)
            else:
                mask = valid["_norm_field"].str.contains(pat, regex=False, na=False)
            sub = valid[mask]
            if not sub.empty:
                last_row = sub.iloc[-1]
                val_found = _finite_float(last_row["value"])
                per_found = str(last_row["period"])
                break
        extracted[spec["key"]] = val_found
        periods_used[spec["key"]] = per_found

    # Fallback derivation from BCTT balance sheet if VCSH / Tổng TS or Nợ / Tổng TS is absent in CSTC.
    def _latest_bctt(patterns: list[str]) -> float | None:
        if bctt_df is None or bctt_df.empty:
            return None
        bdf = bctt_df[bctt_df["value"].notna()].copy()
        if bdf.empty:
            return None
        bdf["_nf"] = bdf["field_name"].map(_strip_accents)
        bdf = bdf.sort_values(["year", "term_code"])
        for pat in patterns:
            sub = bdf[bdf["_nf"].str.contains(pat, regex=False, na=False)]
            if not sub.empty:
                return _finite_float(sub.iloc[-1]["value"])
        return None

    total_assets = _latest_bctt(["tong cong tai san", "tong tai san"])
    equity = _latest_bctt(["von chu so huu"])
    liabilities = _latest_bctt(["no phai tra"])
    if macro_group == "BANK" and extracted.get("equity_to_assets") is None:
        if total_assets and total_assets > 0 and equity is not None:
            extracted["equity_to_assets"] = round(equity / total_assets * 100.0, 2)
    if extracted.get("debt_to_assets") is None and "debt_to_assets" in extracted:
        if total_assets and total_assets > 0 and liabilities is not None:
            extracted["debt_to_assets"] = round(liabilities / total_assets * 100.0, 2)

    return {"latest_period": latest_period, "values": extracted, "periods": periods_used}


def compute_peer_group_medians(stock_metric_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute industry sub-group medians and 3-macro-group medians across all scanned stocks."""
    by_macro: dict[str, dict[str, list[float]]] = {}
    by_peer_key: dict[str, dict[str, list[float]]] = {}

    for row in stock_metric_rows:
        mgroup = row.get("macro_group") or "NON_FINANCIAL"
        pkey = row.get("peer_group_key") or mgroup
        vals = row.get("values") or {}
        by_macro.setdefault(mgroup, {})
        by_peer_key.setdefault(pkey, {})
        for k, v in vals.items():
            fv = _finite_float(v)
            if fv is None:
                continue
            by_macro[mgroup].setdefault(k, []).append(fv)
            by_peer_key[pkey].setdefault(k, []).append(fv)

    def _summarize(bucket: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for metric_key, arr in bucket.items():
            if not arr:
                continue
            s = sorted(arr)
            out[metric_key] = {
                "median": round(float(np.median(s)), 3),
                "p25": round(float(np.percentile(s, 25)), 3),
                "p75": round(float(np.percentile(s, 75)), 3),
                "count": len(s),
            }
        return out

    return {
        "macro_groups": {g: _summarize(b) for g, b in by_macro.items()},
        "peer_groups": {g: _summarize(b) for g, b in by_peer_key.items()},
    }


def attach_stock_peer_correlation(
    symbol_row: dict[str, Any],
    all_rows: list[dict[str, Any]],
    medians_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build Trang 3 correlation payload for a single stock vs its key peer sector & macro group."""
    mgroup = symbol_row.get("macro_group") or "NON_FINANCIAL"
    pkey = symbol_row.get("peer_group_key") or mgroup
    specs = GROUP_CORRELATION_METRICS.get(mgroup, GROUP_CORRELATION_METRICS["NON_FINANCIAL"])
    peer_rows = [r for r in all_rows if (r.get("peer_group_key") or r.get("macro_group")) == pkey]
    peer_stats = (medians_summary.get("peer_groups") or {}).get(pkey) or {}
    macro_stats = (medians_summary.get("macro_groups") or {}).get(mgroup) or {}

    comparisons: list[dict[str, Any]] = []
    for spec in specs:
        key = spec["key"]
        val = _finite_float((symbol_row.get("values") or {}).get(key))
        peer_vals = sorted(
            fv for fv in (_finite_float((r.get("values") or {}).get(key)) for r in peer_rows)
            if fv is not None
        )
        percentile: float | None = None
        if val is not None and peer_vals:
            if spec["higher_better"]:
                rank = sum(1 for pv in peer_vals if pv <= val)
            else:
                rank = sum(1 for pv in peer_vals if pv >= val)
            percentile = round(rank / len(peer_vals) * 100.0, 1)
        comparisons.append({
            "key": key,
            "label": spec["label"],
            "unit": spec["unit"],
            "higher_better": spec["higher_better"],
            "value": val,
            "peer_median": (peer_stats.get(key) or {}).get("median"),
            "peer_count": (peer_stats.get(key) or {}).get("count", 0),
            "macro_median": (macro_stats.get(key) or {}).get("median"),
            "macro_count": (macro_stats.get(key) or {}).get("count", 0),
            "favorable_percentile": percentile,
        })

    return {
        "macro_group": mgroup,
        "macro_group_label": MACRO_GROUP_LABELS.get(mgroup, mgroup),
        "sub_group": symbol_row.get("sub_group") or pkey,
        "sub_group_label": SUB_GROUP_LABELS.get(symbol_row.get("sub_group") or pkey, pkey),
        "peer_group_key": pkey,
        "split_from_financial_l1": bool(symbol_row.get("split_from_financial_l1")),
        "latest_period": symbol_row.get("latest_period"),
        "peer_universe_size": len(peer_rows),
        "metrics": comparisons,
    }


def build_financial_charts(
    cstc_df: pd.DataFrame,
    bctt_df: pd.DataFrame,
    *,
    macro_group: str,
    max_quarters: int = 24,
) -> list[dict[str, Any]]:
    """Build Trang 4 tailored financial chart series per macro group (Bank / Non-Bank Fin / Non-Fin)."""
    combined = pd.concat([df for df in (cstc_df, bctt_df) if df is not None and not df.empty], ignore_index=True) \
        if any(df is not None and not df.empty for df in (cstc_df, bctt_df)) else pd.DataFrame()
    if combined.empty:
        return []
    combined = combined[combined["value"].notna()].copy()
    if combined.empty:
        return []
    combined["_nf"] = combined["field_name"].map(_strip_accents)
    periods = sorted(combined["period"].unique().tolist())[-max_quarters:]

    def _series_for(patterns: list[str], label: str) -> dict[str, Any] | None:
        for pat in patterns:
            mask = combined["_nf"].str.match(pat, na=False) if pat.startswith("^") else combined["_nf"].str.contains(pat, regex=False, na=False)
            sub = combined[mask]
            if not sub.empty:
                first_name = sub.iloc[0]["field_name"]
                exact = sub[sub["field_name"] == first_name].drop_duplicates(subset=["period"], keep="last")
                by_p = dict(zip(exact["period"], exact["value"]))
                pts = [{"period": p, "value": _finite_float(by_p.get(p))} for p in periods if p in by_p]
                if pts:
                    return {"label": label, "source_field": first_name, "points": pts}
        return None

    chart_templates: dict[str, list[tuple[str, str, list[tuple[str, list[str]]]]]] = {
        "BANK": [
            ("Thu nhập lãi thuần & LNST", "bar_line", [
                ("Thu nhập lãi thuần", ["thu nhap lai thuan"]),
                ("LNST cổ đông Ngân hàng", ["loi nhuan sau thue cua co dong", "loi nhuan sau thue"]),
            ]),
            ("Hiệu quả sinh lời: ROE, ROA & NIM (%)", "line", [
                ("ROE (%)", ["^roe$", "ty suat sinh loi tren von chu so huu"]),
                ("NIM (%)", ["^nim$", "ty le thu nhap lai thuan"]),
                ("ROA (%)", ["^roa$", "ty suat sinh loi tren tong tai san"]),
            ]),
            ("Quy mô Tín dụng & Huy động", "bar", [
                ("Cho vay khách hàng", ["cho vay khach hang"]),
                ("Tiền gửi khách hàng", ["tien gui cua khach hang", "tien gui khach hang"]),
            ]),
            ("Định giá P/E & P/B", "line", [
                ("P/E", ["^p/e$", "^pe$"]),
                ("P/B", ["^p/b$", "^pb$"]),
            ]),
        ],
        "NON_BANK_FINANCIAL": [
            ("Doanh thu hoạt động & LNST", "bar_line", [
                ("Doanh thu hoạt động", ["doanh thu hoat dong", "doanh thu thuan"]),
                ("Lợi nhuận sau thuế", ["loi nhuan sau thue"]),
            ]),
            ("Hiệu quả sinh lời: ROE & ROA (%)", "line", [
                ("ROE (%)", ["^roe$", "ty suat sinh loi tren von chu so huu"]),
                ("ROA (%)", ["^roa$", "ty suat sinh loi tren tong tai san"]),
            ]),
            ("Cơ cấu Tổng tài sản & Vốn chủ sở hữu", "bar", [
                ("Tổng tài sản", ["tong cong tai san", "tong tai san"]),
                ("Vốn chủ sở hữu", ["von chu so huu"]),
            ]),
            ("Định giá P/E & P/B", "line", [
                ("P/E", ["^p/e$", "^pe$"]),
                ("P/B", ["^p/b$", "^pb$"]),
            ]),
        ],
        "NON_FINANCIAL": [
            ("Doanh thu thuần & Lợi nhuận sau thuế", "bar_line", [
                ("Doanh thu thuần", ["doanh thu thuan ve ban hang", "doanh thu thuan"]),
                ("LNST Công ty mẹ", ["loi nhuan sau thue cua co dong", "loi nhuan sau thue"]),
            ]),
            ("Biên lợi nhuận & ROE (%)", "line", [
                ("Biên lãi gộp (%)", ["ty suat loi nhuan gop bien", "bien lai gop"]),
                ("Biên lãi ròng (%)", ["ty suat sinh loi tren doanh thu thuan", "bien loi nhuan rong"]),
                ("ROE (%)", ["^roe$", "ty suat sinh loi tren von chu so huu"]),
            ]),
            ("Cơ cấu Nguồn vốn & Hàng tồn kho", "bar", [
                ("Tổng tài sản", ["tong cong tai san", "tong tai san"]),
                ("Nợ phải trả", ["^no phai tra$"]),
                ("Vốn chủ sở hữu", ["^von chu so huu$"]),
                ("Hàng tồn kho", ["^hang ton kho$"]),
            ]),
            ("Định giá P/E & P/B", "line", [
                ("P/E", ["^p/e$", "^pe$"]),
                ("P/B", ["^p/b$", "^pb$"]),
            ]),
        ],
    }

    charts: list[dict[str, Any]] = []
    for title, kind, series_specs in chart_templates.get(macro_group, chart_templates["NON_FINANCIAL"]):
        series_list = [s for s in (_series_for(pats, lbl) for lbl, pats in series_specs) if s is not None]
        if series_list:
            charts.append({"title": title, "chart_type": kind, "series": series_list})
    return charts


class YuantaClient:
    """Thread-safe, rate-limited HTTP client for Yuanta YSradar public endpoints."""

    def __init__(
        self,
        *,
        base_url: str = YUANTA_BASE_URL,
        delay: float = 0.15,
        timeout: int = 25,
        retries: int = 3,
        run_id: str | None = None,
        session: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.run_id = run_id or f"yuanta-{int(time.time())}"
        self._injected_session = session
        self._rate_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._local = threading.local()
        self._last_req_ts = 0.0
        self.source_events: list[dict[str, Any]] = []

    def _session(self) -> requests.Session:
        if self._injected_session is not None:
            return self._injected_session
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update({
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) VN-Fund-Research/1.0",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
                "Referer": "https://ysradar.yuanta.com.vn/",
                "Origin": "https://ysradar.yuanta.com.vn",
            })
            self._local.session = sess
        return sess

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait = self.delay - (now - self._last_req_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_req_ts = time.monotonic()

    def get_json(self, path: str, *, params: dict[str, Any] | None = None, symbol: str = "") -> Any:
        clean_path = "/" + path.lstrip("/")
        url = f"{self.base_url}{clean_path}"
        started_at = _utc_iso()
        http_status: int | None = None
        err_msg = ""
        err_type = ""

        for attempt in range(self.retries):
            self._throttle()
            try:
                resp = self._session().get(url, params=params, timeout=self.timeout)
                http_status = resp.status_code
                if http_status == 429 or http_status >= 500:
                    err_type = "rate_limit" if http_status == 429 else "http_5xx"
                    err_msg = f"HTTP {http_status}"
                    if attempt < self.retries - 1:
                        time.sleep(min(15.0, (2 ** attempt) * max(0.5, self.delay * 4)))
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                raw_bytes = resp.content or b""
                payload = resp.json()
                completed_at = _utc_iso()
                body_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else ""
                unwrapped = _unwrap_payload(payload)
                rec_cnt = len(unwrapped) if isinstance(unwrapped, list) else (1 if unwrapped else 0)
                status = "empty_response" if rec_cnt == 0 else "success"
                self._record_event(
                    symbol=symbol, path=clean_path, started_at=started_at, completed_at=completed_at,
                    status=status, http_status=http_status, retry_attempts=attempt,
                    response_bytes=len(raw_bytes), response_hash=body_hash, record_count=rec_cnt,
                    error_type="", error_message="",
                )
                return payload
            except (requests.RequestException, ValueError) as exc:
                err_type = type(exc).__name__
                err_msg = f"{type(exc).__name__} on {urlsplit(url).path}"
                if attempt < self.retries - 1:
                    time.sleep(min(10.0, (2 ** attempt) * 0.8))
                else:
                    self._record_event(
                        symbol=symbol, path=clean_path, started_at=started_at, completed_at=_utc_iso(),
                        status="network_error" if http_status is None else "http_error",
                        http_status=http_status, retry_attempts=attempt,
                        response_bytes=None, response_hash="", record_count=None,
                        error_type=err_type, error_message=err_msg,
                    )
                    raise

    def _record_event(self, *, symbol: str, path: str, started_at: str, completed_at: str,
                      status: str, http_status: int | None, retry_attempts: int,
                      response_bytes: int | None, response_hash: str, record_count: int | None,
                      error_type: str, error_message: str) -> None:
        raw_id = f"{self.run_id}|yuanta|{symbol}|{path}|{started_at}|{completed_at}"
        evt = {
            "event_id": hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24],
            "run_id": self.run_id,
            "provider": "yuanta_ysradar",
            "fund_code": symbol,
            "endpoint": f"GET {path}",
            "source_reference": path,
            "started_at": started_at,
            "observed_at": completed_at if status in {"success", "empty_response"} else "",
            "completed_at": completed_at,
            "status": status,
            "error_type": error_type,
            "http_status": http_status,
            "retry_attempts": retry_attempts,
            "response_bytes": response_bytes,
            "response_hash": response_hash,
            "record_count": record_count,
            "error_message": error_message,
        }
        with self._events_lock:
            self.source_events.append(evt)

    # ── Universe Discovery (HOSE / HNX / UPCOM) ───────────────────────────────
    def list_exchange_stocks(self, exchange: str) -> list[dict[str, Any]]:
        ex_query = EXCHANGE_QUERY_MAP.get(exchange.strip().upper(), exchange.strip().upper())
        payload = self.get_json("/api/v3/market_data/price_board/stock/list_stock_info", params={"exchange": ex_query})
        rows = _unwrap_payload(payload)
        return rows if isinstance(rows, list) else []

    def list_stocks_by_sector(self) -> list[dict[str, Any]]:
        payload = self.get_json("/api/v3/market_data/price_board/stock/list_stock_by_sector")
        rows = _unwrap_payload(payload)
        return rows if isinstance(rows, list) else []

    def discover_universe(self, exchanges: tuple[str, ...] = ("HOSE", "HNX", "UPCOM")) -> list[dict[str, Any]]:
        sector_map: dict[str, str] = {}
        try:
            for item in self.list_stocks_by_sector():
                if not isinstance(item, dict):
                    continue
                sec_name = str(item.get("Sector") or item.get("SectorName") or "").strip()
                single_code = normalize_symbol(item.get("StockCode") or item.get("symbol"))
                if single_code and sec_name:
                    sector_map[single_code] = sec_name
                stocks_field = item.get("Stocks") or item.get("StockList") or []
                if isinstance(stocks_field, str):
                    codes = [s.strip().upper() for s in stocks_field.split(",") if s.strip()]
                elif isinstance(stocks_field, list):
                    codes = [
                        str(s.get("StockCode") if isinstance(s, dict) else s).strip().upper()
                        for s in stocks_field if s
                    ]
                else:
                    codes = []
                for c in codes:
                    if c:
                        sector_map[c] = sec_name
        except (requests.RequestException, ValueError) as exc:
            log.warning("Không lấy được bảng phân ngành tổng hợp (%s); sẽ phân ngành từ hồ sơ từng mã.", type(exc).__name__)

        seen: dict[str, dict[str, Any]] = {}
        for ex in exchanges:
            canon_ex = EXCHANGE_CANONICAL_MAP.get(ex.strip().upper(), ex.strip().upper())
            for row in self.list_exchange_stocks(ex):
                if not isinstance(row, dict) or not is_equity_stock_row(row):
                    continue
                sym = normalize_symbol(row.get("StockCode") or row.get("symbol"))
                if not sym or sym in seen:
                    continue
                seen[sym] = {
                    "symbol": sym,
                    "exchange": canon_ex,
                    "company_name": str(row.get("CompanyNameVi") or row.get("StockName") or row.get("FullName") or "").strip() or None,
                    "sector_board": sector_map.get(sym) or str(row.get("SectorName") or "").strip() or None,
                    "ref_price": _finite_float(row.get("RefPrice") or row.get("BasicPrice")),
                    "last_price": _finite_float(row.get("LastMP") or row.get("ClosePrice") or row.get("MatchPrice")),
                    "total_volume": _finite_float(row.get("TotalVol") or row.get("TotalVolume")),
                }
        return [seen[k] for k in sorted(seen)]

    # ── Trang 1: Tổng quan (9 endpoints) ──────────────────────────────────────
    def fetch_page1_overview(self, symbol: str, *, include_intraday: bool = True, num_peers: int = 1000) -> dict[str, Any]:
        specs: list[tuple[str, str, dict[str, Any] | None]] = [
            ("stock_rating_info", f"/api/v3/stock_rating/info/{symbol}", {"lang": "vi"}),
            ("profile_info", "/ysw/api/v3/market_data/profile/info", {"stock_code": symbol}),
            ("shareholders", "/ysw/api/v3/market_data/profile/shareholder", {"stock_code": symbol}),
            ("stock_info", f"/api/v3/market_data/price_board/stock/stock_info/{symbol}", None),
            ("list_stock_info", "/api/v3/market_data/price_board/stock/list_stock_info", {"stock_list": symbol, "stock_type": "ST"}),
            ("list_stock_rating_info", "/api/v3/market_data/price_board/stock/list_stock_rating_info", {"stock_list": symbol, "stock_type": "ST"}),
            ("peers", f"/ysw/api/v3/market_data/profile/peers/{symbol}", {"num_peers": num_peers}),
        ]
        if include_intraday:
            specs.extend([
                ("last_bid_offer", f"/api/v3/market_data/price_board/stock/last_bid_offer/{symbol}", {"odd_lot": "false"}),
                ("list_transaction", f"/api/v3/market_data/price_board/stock/list_transaction/{symbol}", {"limit": 50, "last_auto_id": 0}),
            ])
        result: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, path, params in specs:
            try:
                result[key] = _unwrap_payload(self.get_json(path, params=params, symbol=symbol))
            except (requests.RequestException, ValueError) as exc:
                result[key] = None
                errors[key] = type(exc).__name__
        result["_errors"] = errors
        return result

    # ── Trang 2: Dữ liệu giao dịch (5 endpoints) ──────────────────────────────
    def fetch_page2_trading(
        self,
        symbol: str,
        *,
        from_date: str = "2010-01-01",
        foreign_from_date: str = "2025-01-01",
        to_date: str | None = None,
        icb: str = "",
        exchange: str = "",
    ) -> dict[str, Any]:
        end_dt = to_date or date.today().isoformat()
        price_raw = None
        foreign_raw = None
        charts: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for key, path, params in [
            ("price_history", f"/api/v3/market_data/market_watch/price_history/{symbol}",
             {"from_date": from_date, "to_date": end_dt}),
            ("price_history_foreign", f"/api/v3/market_data/market_watch/price_history_foreign/{symbol}",
             {"from_date": foreign_from_date, "to_date": end_dt}),
            ("ngay_giao_dich", "/api/v3/foreign_screener/ngay_giao_dich",
             {"from_date": foreign_from_date, "ma_ck": symbol, "to_date": end_dt}),
            ("gtgd_binh_quan_chart", "/api/v3/foreign_screener/gtgd_binh_quan_chart",
             {"from_date": foreign_from_date, "ma_ck": symbol, "to_date": end_dt}),
            ("ty_le_von_hoa_chart", "/api/v3/foreign_screener/ty_le_von_hoa_chart",
             {"from_date": foreign_from_date, "icb": icb, "ma_ck": symbol, "san": exchange, "to_date": end_dt}),
        ]:
            try:
                payload = self.get_json(path, params=params, symbol=symbol)
                if key == "price_history":
                    price_raw = payload
                elif key == "price_history_foreign":
                    foreign_raw = payload
                else:
                    charts[key] = _unwrap_payload(payload)
            except (requests.RequestException, ValueError) as exc:
                errors[key] = type(exc).__name__
                if key not in {"price_history", "price_history_foreign"}:
                    charts[key] = None

        merged_df = parse_ohlcv_and_foreign(price_raw, foreign_raw, symbol=symbol)
        return {"daily_df": merged_df, "foreign_charts": charts, "_errors": errors}

    # ── Trang 4: Báo cáo tài chính (CSTC & BCTT, Q1/2010–nay) ─────────────────
    def fetch_full_financial_report(
        self,
        symbol: str,
        *,
        report_type: str = "CSTC",
        start_year: int = 2010,
        end_year: int | None = None,
        report_term_type: int = 2,
        page_size: int = 5,
        unit: int = 1_000_000,
        max_pages: int = 24,
    ) -> pd.DataFrame:
        """Paginate `/api/v3/vietstock/financial_statement/report` across all quarters since `start_year`.

        Starts from `(end_year, Q4)` and advances `page_index = 1..max_pages` until `start_year`
        is reached or no unseen periods appear. If an API deployment only returns 1 static window
        per `(year_period, term_code)` regardless of `page_index`, automatically falls back to
        stepping `year_period` down to `start_year` so `Q1/2010`–present is always complete.
        """
        target_end_year = end_year or datetime.now(UTC).year
        anchor_term = "Q4" if report_term_type == 2 else "FY"
        seen_periods: set[tuple[int, str]] = set()
        all_rows: dict[tuple[str, str, str], dict[str, Any]] = {}

        def _ingest(payload: Any) -> list[tuple[int, str]]:
            rows, periods = parse_financial_report_page(
                payload, symbol=symbol, report_type=report_type, start_year=start_year,
            )
            for rec in rows:
                dedup_k = (rec["period"], rec["report_component"], rec["field_name"])
                if dedup_k not in all_rows or all_rows[dedup_k]["value"] is None:
                    all_rows[dedup_k] = rec
            return periods

        page_index = 1
        stepped_by_page = False
        while page_index <= max_pages:
            params = {
                "lang": "vi",
                "page_index": page_index,
                "page_size": page_size,
                "report_term_type": report_term_type,
                "report_type": report_type,
                "stock_code": symbol,
                "term_code": anchor_term,
                "unit": unit,
                "year_period": target_end_year,
            }
            payload = self.get_json("/api/v3/vietstock/financial_statement/report", params=params, symbol=symbol)
            periods = _ingest(payload)
            if not periods:
                break
            new_periods = [p for p in periods if p not in seen_periods]
            if page_index == 2 and new_periods:
                stepped_by_page = True
            seen_periods.update(periods)
            min_year_seen = min(yr for yr, _ in periods)
            if min_year_seen <= start_year and (start_year, "Q1" if report_term_type == 2 else "FY") in seen_periods:
                break
            if not new_periods:
                break
            page_index += 1

        # Fallback if server ignored `page_index` on page 2 while older years remain unfetched.
        if seen_periods and not stepped_by_page:
            earliest_yr = min(yr for yr, _ in seen_periods)
            cursor_yr = earliest_yr - 1
            while cursor_yr >= start_year:
                params = {
                    "lang": "vi",
                    "page_index": 1,
                    "page_size": page_size,
                    "report_term_type": report_term_type,
                    "report_type": report_type,
                    "stock_code": symbol,
                    "term_code": anchor_term,
                    "unit": unit,
                    "year_period": cursor_yr,
                }
                payload = self.get_json("/api/v3/vietstock/financial_statement/report", params=params, symbol=symbol)
                periods = _ingest(payload)
                if not periods:
                    cursor_yr -= 1
                    continue
                new_p = [p for p in periods if p not in seen_periods]
                seen_periods.update(periods)
                if not new_p:
                    break
                cursor_yr = min(yr for yr, _ in periods) - 1

        cols = [
            "symbol", "report_type", "period", "year", "term_code", "quarter",
            "component_order", "field_order", "report_component", "field_name", "unit", "value",
        ]
        if not all_rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(list(all_rows.values()))
        return df[cols].sort_values(["component_order", "field_order", "year", "term_code"]).reset_index(drop=True)

    # ── Trang 5: Tin tức & sự kiện (2010–nay) ─────────────────────────────────
    def fetch_page5_articles(
        self,
        symbol: str,
        *,
        from_date: str = "2010-01-01",
        to_date: str | None = None,
        page_size: int = 100_000,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        end_dt = to_date or date.today().isoformat()
        collected: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for page in range(1, max_pages + 1):
            payload = self.get_json(
                "/ysw/api/v3/market_data/article",
                params={
                    "from": from_date,
                    "lang": "vi",
                    "page": page,
                    "page_size": page_size,
                    "stock_code": symbol,
                    "to": end_dt,
                },
                symbol=symbol,
            )
            batch = parse_articles(payload, symbol=symbol)
            new_count = 0
            for item in batch:
                k = item.get("article_id") or f"{item.get('published_at')}|{item.get('title')}"
                if k not in seen_keys:
                    seen_keys.add(k)
                    collected.append(item)
                    new_count += 1
            has_next = isinstance(payload, dict) and bool(payload.get("hasNextPage"))
            if not batch or new_count == 0 or not has_next:
                break
        collected.sort(key=lambda r: (r.get("published_at") or r.get("published_date") or ""), reverse=True)
        return collected


def write_gzip_json(data: Any, path: Path) -> int:
    """Atomically write compact UTF-8 JSON compressed as `.json.gz` and return byte size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with gzip.open(tmp, "wb", compresslevel=6) as fh:
            fh.write(raw)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path.stat().st_size
