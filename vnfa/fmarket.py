# -*- coding: utf-8 -*-
"""Client và bộ phân tích cho API công khai của Fmarket (fmarket.vn).

Lưu ý: đây là API không có tài liệu chính thức. Giữ tần suất gọi thấp (mặc định 1 request/giây),
kiểm tra điều khoản sử dụng của Fmarket và đối chiếu số liệu quan trọng với factsheet của công ty quản lý quỹ.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://api.fmarket.vn/res"
ICT = timezone(timedelta(hours=7))
DEFAULT_UA = "VN_Fund_Analysis/0.1 (personal research; public data)"
log = logging.getLogger("vnfa.fmarket")


class FmarketError(RuntimeError):
    pass


class FmarketClient:
    def __init__(self, delay: float = 1.0, timeout: int = 30, user_agent: str | None = None):
        self.delay, self.timeout = delay, timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or DEFAULT_UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://fmarket.vn",
            "Referer": "https://fmarket.vn/",
        })
        retry = Retry(total=4, backoff_factor=2.0, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _call(self, method: str, path: str, **kw):
        time.sleep(self.delay)
        resp = self.session.request(method, f"{BASE}{path}", timeout=self.timeout, **kw)
        resp.raise_for_status()
        js = resp.json()
        if isinstance(js, dict):
            if js.get("status") not in (None, 200):
                raise FmarketError(f"{path}: status={js.get('status')} message={js.get('message')}")
            return js.get("data", js)
        return js

    def list_products(self) -> list[dict]:
        body = {
            "types": ["NEW_FUND", "TRADING_FUND"], "issuerIds": [], "sortOrder": "DESC",
            "sortField": "navTo6Months", "page": 1, "pageSize": 500, "isIpo": False,
            "fundAssetTypes": [], "bondRemainPeriods": [], "searchField": "",
            "isBuyByReward": False, "thirdAppIds": [],
        }
        data = self._call("POST", "/products/filter", json=body)
        return data.get("rows", []) if isinstance(data, dict) else list(data)

    def product_detail(self, product_id: int) -> dict:
        return self._call("GET", f"/products/{product_id}")

    def nav_history(self, product_id: int, from_date: str = "2014-01-01", to_date: str | None = None) -> pd.DataFrame:
        """Lịch sử NAV/CCQ. Trả về DataFrame [date, nav]."""
        to_date = to_date or datetime.now(ICT).date().isoformat()
        body = {"isAllData": 1, "productId": product_id, "fromDate": from_date, "toDate": to_date}
        data = self._call("POST", "/product/get-nav-history", json=body)
        return parse_nav_history(data)


# ───────────────────────── Bộ phân tích ─────────────────────────
def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def parse_product_row(r: dict) -> dict:
    asset = r.get("dataFundAssetType") or r.get("assetType") or {}
    return {
        "id": r.get("id"),
        "code": r.get("code"),
        "short_name": r.get("shortName"),
        "name": r.get("name"),
        "mgmt_co": (r.get("owner") or {}).get("shortName"),
        "asset_code": asset.get("code") if isinstance(asset, dict) else asset,
        "asset_name": asset.get("name") if isinstance(asset, dict) else None,
        "status": r.get("type"),
    }


HOLDING_COLUMNS = ["ticker", "weight", "rank", "volume", "price_k", "asset_value", "industry", "update_date"]


def parse_detail(d: dict) -> tuple[dict, pd.DataFrame]:
    """Trả về (meta, holdings). Chấp nhận cả JSON gốc của API lẫn bản rút gọn của hat-ccq.

    weight là phân số (0.0643 = 6,43% NAV); price_k tính bằng nghìn đồng;
    asset_value là giá trị tài sản (đồng) tại kỳ báo cáo.
    """
    asset = d.get("dataFundAssetType") or d.get("assetType") or {}
    meta = {
        "id": d.get("id"), "code": d.get("code"), "short_name": d.get("shortName"), "name": d.get("name"),
        "mgmt_co": (d.get("owner") or {}).get("shortName"),
        "asset_code": asset.get("code") if isinstance(asset, dict) else asset,
        "asset_name": asset.get("name") if isinstance(asset, dict) else None,
        "management_fee": d.get("managementFee"),
    }
    raw = d.get("productTopHoldingList")
    if raw is None:
        raw = d.get("topHoldings") or []
    rows = []
    for t in raw:
        if t.get("type") not in (None, "STOCK"):
            continue
        ticker = (t.get("stockCode") or "").strip().upper()
        w = _num(t.get("netAssetPercent"))
        if not ticker or np.isnan(w):
            continue
        ms = t.get("updateAt")
        upd = datetime.fromtimestamp(ms / 1000, tz=ICT).date().isoformat() if ms else None
        rows.append({
            "ticker": ticker, "weight": round(w / 100.0, 6), "volume": _num(t.get("volume")),
            "price_k": _num(t.get("price")), "asset_value": _num(t.get("assetValue")),
            "industry": t.get("industry"), "update_date": upd,
        })
    df = pd.DataFrame(rows, columns=[c for c in HOLDING_COLUMNS if c != "rank"])
    if not df.empty:
        df = df.sort_values("weight", ascending=False).drop_duplicates("ticker").reset_index(drop=True)
        df.insert(2, "rank", np.arange(1, len(df) + 1))
    else:
        df.insert(2, "rank", pd.Series(dtype=int))
    return meta, df


# ───────────────────────── Các bảng bổ sung (mọi loại quỹ) ─────────────────────────
def _ms_to_date(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=ICT).date().isoformat() if ms else None


def parse_nav_history(data) -> pd.DataFrame:
    """Chấp nhận JSON gốc (navDate, nav) hoặc bản rút gọn của hat-ccq (d, v)."""
    rows = []
    for r in data or []:
        d, v = r.get("navDate", r.get("d")), _num(r.get("nav", r.get("v")))
        if d and not np.isnan(v):
            rows.append((str(d)[:10], v))
    df = pd.DataFrame(rows, columns=["date", "nav"])
    return df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def parse_allocation(d: dict) -> pd.DataFrame:
    """Cơ cấu tài sản (cổ phiếu/trái phiếu/tiền...) theo kỳ báo cáo."""
    rows = []
    for a in (d.get("productAssetHoldingList") if d.get("productAssetHoldingList") is not None
              else d.get("assetAllocation")) or []:
        t = a.get("assetType") or {}
        rows.append({"asset_code": t.get("code"), "asset_name": t.get("name"),
                     "pct": _num(a.get("assetPercent")) / 100.0, "report_date": _ms_to_date(a.get("reportTime"))})
    return pd.DataFrame(rows, columns=["asset_code", "asset_name", "pct", "report_date"])


def parse_industries(d: dict) -> pd.DataFrame:
    rows = []
    for a in (d.get("productIndustriesHoldingList") if d.get("productIndustriesHoldingList") is not None
              else d.get("industries")) or []:
        rows.append({"industry": a.get("industry"), "pct": _num(a.get("assetPercent")) / 100.0})
    return pd.DataFrame(rows, columns=["industry", "pct"])


def parse_bonds(d: dict) -> pd.DataFrame:
    """Top trái phiếu nắm giữ: cùng schema với holdings để dùng chung diff."""
    raw = d.get("productTopHoldingBondList")
    if raw is None:
        raw = d.get("topHoldingsBond") or []
    rows = []
    for t in raw:
        code = (t.get("stockCode") or "").strip().upper()
        w = _num(t.get("netAssetPercent"))
        if code and not np.isnan(w):
            rows.append({"ticker": code, "weight": round(w / 100.0, 6), "volume": _num(t.get("volume")),
                         "price_k": np.nan, "asset_value": _num(t.get("assetValue")),
                         "industry": t.get("industry"), "update_date": _ms_to_date(t.get("updateAt"))})
    df = pd.DataFrame(rows, columns=[c for c in HOLDING_COLUMNS if c != "rank"])
    if not df.empty:
        df = df.sort_values("weight", ascending=False).drop_duplicates("ticker").reset_index(drop=True)
        df.insert(2, "rank", np.arange(1, len(df) + 1))
    else:
        df.insert(2, "rank", pd.Series(dtype=int))
    return df


def parse_fees(d: dict) -> pd.DataFrame:
    rows = []
    for f in (d.get("productFeeList") if d.get("productFeeList") is not None else d.get("fees")) or []:
        prog = f.get("program")
        if prog is None and isinstance(f.get("productProgram"), dict):
            prog = f["productProgram"].get("name")
        rows.append({"type": f.get("type"), "program": prog, "begin": f.get("beginVolume"),
                     "end": f.get("endVolume"), "unit": f.get("feeUnitType"), "fee": _num(f.get("fee"))})
    return pd.DataFrame(rows, columns=["type", "program", "begin", "end", "unit", "fee"])


def parse_profile(d: dict) -> pd.DataFrame:
    """Một dòng hồ sơ quỹ (chỉ trường ít đổi): phí quản lý, giao dịch tối thiểu, loại quỹ, website..."""
    asset = d.get("dataFundAssetType") or d.get("assetType") or {}
    risk, ftype = d.get("riskLevel"), d.get("fundType")
    return pd.DataFrame([{
        "name": d.get("name"), "short_name": d.get("shortName"), "mgmt_co": (d.get("owner") or {}).get("shortName"),
        "asset_code": asset.get("code") if isinstance(asset, dict) else asset,
        "fund_type": ftype.get("name") if isinstance(ftype, dict) else ftype,
        "risk_level": risk.get("name") if isinstance(risk, dict) else risk,
        "management_fee_pct": _num(d.get("managementFee")),
        "buy_min_vnd": _num(d.get("buyMinValue")), "sell_min_units": _num(d.get("sellMin")),
        "settle_days": _num(d.get("completeTransactionDuration")), "cutoff": d.get("closedOrderBookAt"),
        "first_issue_date": _ms_to_date(d.get("firstIssueAt")), "website": d.get("websiteURL"),
    }])


def parse_performance(d: dict) -> pd.DataFrame:
    """Hiệu suất theo kỳ do Fmarket công bố (đổi hằng ngày, mặc định không thu: tự tính được từ NAV)."""
    nc = d.get("productNavChange") or {}
    row = {"nav": _num(d.get("nav"))}
    for key, col in [("navToPrevious", "ret_1d"), ("navTo1Months", "ret_1m"), ("navTo3Months", "ret_3m"),
                     ("navTo6Months", "ret_6m"), ("navTo12Months", "ret_12m"), ("navTo24Months", "ret_24m"),
                     ("navTo36Months", "ret_36m"), ("navTo60Months", "ret_60m"), ("navToLastYear", "ret_ytd"),
                     ("annualizedReturn36Months", "ann_ret_36m"), ("navToBeginning", "ret_since_inception")]:
        row[col] = _num(nc.get(key))
    return pd.DataFrame([row])
