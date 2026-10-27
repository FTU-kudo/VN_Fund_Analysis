# -*- coding: utf-8 -*-
"""Client và bộ phân tích cho API công khai của Fmarket (fmarket.vn).

Lưu ý: đây là API không có tài liệu chính thức. Giữ tần suất gọi thấp (mặc định 1 request/giây),
kiểm tra điều khoản sử dụng của Fmarket và đối chiếu số liệu quan trọng với factsheet của công ty quản lý quỹ.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .provenance import utc_now

BASE = "https://api.fmarket.vn/res"
ICT = timezone(timedelta(hours=7))
DEFAULT_UA = "VN_Fund_Analysis/0.1 (personal research; public data)"
log = logging.getLogger("vnfa.fmarket")


class FmarketError(RuntimeError):
    def __init__(self, message: str, api_status: int | str | None = None):
        super().__init__(message)
        self.api_status = api_status


class FmarketSchemaError(FmarketError):
    """The endpoint returned a shape that does not meet the adapter contract."""


class FmarketEmptyResponse(FmarketError):
    """The endpoint responded successfully but returned no records."""


class FmarketClient:
    def __init__(self, delay: float = 1.0, timeout: int = 30, user_agent: str | None = None,
                 run_id: str | None = None):
        self.delay, self.timeout = delay, timeout
        self.run_id = run_id or uuid.uuid4().hex
        self.source_events: list[dict] = []
        self.last_source_event: dict | None = None
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
        self.retry_policy = "urllib3 total=4; backoff_factor=2; statuses=429,500,502,503,504; respect_retry_after=true"

    @staticmethod
    def _http_category(status: int | None) -> tuple[str, str]:
        if status in (401, 403):
            return "authentication_error", "authentication"
        if status == 429:
            return "rate_limit", "rate_limit"
        if status in (408, 425) or status is not None and status >= 500:
            return "network_error", "network"
        if status is not None and status >= 400:
            return "schema_error", "schema"
        return "error", "http"

    def _mark_last_event(self, status: str, error_type: str | None = None,
                         record_count: int | None = None) -> None:
        if self.last_source_event is None:
            return
        self.last_source_event["status"] = status
        if error_type is not None:
            self.last_source_event["error_type"] = error_type
        if record_count is not None:
            self.last_source_event["record_count"] = record_count

    @staticmethod
    def _retry_attempt_count(response) -> int | None:
        retries = getattr(getattr(response, "raw", None), "retries", None)
        history = getattr(retries, "history", None)
        return len(history) if history is not None else None

    def _call(self, method: str, path: str, **kw):
        event = {
            "event_id": None,
            "run_id": self.run_id,
            "provider": "fmarket",
            "endpoint": f"{method.upper()} {path}",
            "source_reference": path,
            "started_at": utc_now(),
            "observed_at": None,
            "completed_at": None,
            "status": "started",
            "error_type": None,
            "http_status": None,
            "record_count": None,
            "response_hash": None,
            "retry_policy": self.retry_policy,
            "retry_attempts": None,
            "ingested_at": None,
        }
        response = None
        try:
            time.sleep(self.delay)
            response = self.session.request(method, f"{BASE}{path}", timeout=self.timeout, **kw)
            event["observed_at"] = utc_now()
            event["http_status"] = response.status_code
            event["retry_attempts"] = self._retry_attempt_count(response)
            body = getattr(response, "content", b"")
            if body:
                event["response_hash"] = hashlib.sha256(body).hexdigest()
            response.raise_for_status()
            try:
                js = response.json()
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError) as exc:
                event["status"], event["error_type"] = "schema_error", "schema"
                raise FmarketSchemaError(f"{path}: response is not valid JSON") from exc
            if isinstance(js, dict):
                if js.get("status") not in (None, 200):
                    api_status = js.get("status")
                    category, error_type = self._http_category(
                        int(api_status) if str(api_status).isdigit() else None)
                    event["status"], event["error_type"] = category, error_type
                    raise FmarketError(f"{path}: source returned a non-success status", api_status=api_status)
                data = js.get("data", js)
            else:
                data = js
            if isinstance(data, list):
                event["record_count"] = len(data)
            elif isinstance(data, dict):
                rows = data.get("rows")
                event["record_count"] = len(rows) if isinstance(rows, list) else 1
            event["status"] = "success"
            return data
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code is None and response is not None:
                status_code = response.status_code
            category, error_type = self._http_category(status_code)
            event["status"], event["error_type"] = category, error_type
            event["http_status"] = status_code
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            event["status"], event["error_type"] = "network_error", "network"
            raise
        except requests.exceptions.RequestException:
            event["status"], event["error_type"] = "network_error", "network"
            raise
        except FmarketSchemaError:
            event["status"], event["error_type"] = "schema_error", "schema"
            raise
        except FmarketError as exc:
            status = int(exc.api_status) if str(exc.api_status).isdigit() else None
            category, error_type = self._http_category(status)
            event["status"], event["error_type"] = category, error_type
            event["http_status"] = status
            raise
        except (ValueError, TypeError):
            event["status"], event["error_type"] = "schema_error", "schema"
            raise
        finally:
            event["completed_at"] = utc_now()
            if event["observed_at"] is None and response is not None:
                event["observed_at"] = utc_now()
            self.source_events.append(event)
            self.last_source_event = event

    def list_products(self) -> list[dict]:
        body = {
            "types": ["NEW_FUND", "TRADING_FUND"], "issuerIds": [], "sortOrder": "DESC",
            "sortField": "navTo6Months", "page": 1, "pageSize": 500, "isIpo": False,
            "fundAssetTypes": [], "bondRemainPeriods": [], "searchField": "",
            "isBuyByReward": False, "thirdAppIds": [],
        }
        data = self._call("POST", "/products/filter", json=body)
        if isinstance(data, dict):
            if "rows" not in data:
                self._mark_last_event("schema_error", "schema")
                raise FmarketSchemaError("/products/filter: required rows field is missing")
            rows = data["rows"]
        elif isinstance(data, list):
            rows = data
        else:
            self._mark_last_event("schema_error", "schema")
            raise FmarketSchemaError("/products/filter: expected a list or an object containing rows")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            self._mark_last_event("schema_error", "schema")
            raise FmarketSchemaError("/products/filter: rows must be a list of objects")
        if any(row.get("id") is None or not row.get("code") for row in rows):
            self._mark_last_event("schema_error", "schema", record_count=len(rows))
            raise FmarketSchemaError("/products/filter: each product requires id and code")
        self._mark_last_event("empty_response" if not rows else "success", record_count=len(rows))
        return rows

    def product_detail(self, product_id: int) -> dict:
        path = f"/products/{product_id}"
        data = self._call("GET", path)
        if not isinstance(data, dict):
            self._mark_last_event("schema_error", "schema")
            raise FmarketSchemaError(f"{path}: expected a JSON object")
        if not data:
            self._mark_last_event("empty_response", "empty_response", record_count=0)
            raise FmarketEmptyResponse(f"{path}: no product detail returned")
        if not any(data.get(key) for key in ("id", "code")):
            self._mark_last_event("schema_error", "schema", record_count=1)
            raise FmarketSchemaError(f"{path}: required product identity fields are missing")
        self._mark_last_event("success", record_count=1)
        return data

    def nav_history(self, product_id: int, from_date: str = "2014-01-01", to_date: str | None = None) -> pd.DataFrame:
        """Lịch sử NAV/CCQ. Trả về DataFrame [date, nav]."""
        to_date = to_date or datetime.now(ICT).date().isoformat()
        body = {"isAllData": 1, "productId": product_id, "fromDate": from_date, "toDate": to_date}
        data = self._call("POST", "/product/get-nav-history", json=body)
        if not isinstance(data, list):
            self._mark_last_event("schema_error", "schema")
            raise FmarketSchemaError("/product/get-nav-history: expected a list")
        parsed = parse_nav_history(data)
        if data and parsed.empty:
            self._mark_last_event("schema_error", "schema", record_count=0)
            raise FmarketSchemaError("/product/get-nav-history: rows were present but none had valid date/NAV")
        self._mark_last_event("empty_response" if parsed.empty else "success", record_count=len(parsed))
        return parsed


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
    if ms is None or ms == "":
        return None
    if isinstance(ms, (int, float)) and not isinstance(ms, bool):
        if not np.isfinite(ms) or ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=ICT).date().isoformat()
    parsed = pd.to_datetime(str(ms).strip()[:10], errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def parse_nav_history(data) -> pd.DataFrame:
    """Chấp nhận JSON gốc (navDate, nav) hoặc bản rút gọn của hat-ccq (d, v).

    Bỏ điểm không có ngày hợp lệ hoặc NAV không dương/hữu hạn để không đưa giá trị
    lỗi vào chuỗi lợi nhuận. Giá trị bị loại sẽ được ghi log để có thể truy tìm nguồn.
    """
    rows = []
    for r in data or []:
        d, v = r.get("navDate", r.get("d")), _num(r.get("nav", r.get("v")))
        parsed_date = pd.to_datetime(str(d)[:10], errors="coerce") if d else pd.NaT
        if pd.isna(parsed_date) or not np.isfinite(v) or v <= 0:
            log.warning("Bỏ điểm NAV không hợp lệ: date=%r nav=%r", d, v)
            continue
        rows.append((parsed_date.date().isoformat(), v))
    df = pd.DataFrame(rows, columns=["date", "nav"])
    return df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _pct_fraction(x) -> float:
    value = _num(x)
    return round(value / 100.0, 6) if np.isfinite(value) else np.nan


def parse_allocation(d: dict) -> pd.DataFrame:
    """Cơ cấu tài sản (cổ phiếu/trái phiếu/tiền...) theo kỳ báo cáo."""
    rows = []
    for a in (d.get("productAssetHoldingList") if d.get("productAssetHoldingList") is not None
              else d.get("assetAllocation")) or []:
        t = a.get("assetType") or {}
        rows.append({"asset_code": t.get("code"), "asset_name": t.get("name"),
                     "pct": _pct_fraction(a.get("assetPercent")), "report_date": _ms_to_date(a.get("reportTime"))})
    return pd.DataFrame(rows, columns=["asset_code", "asset_name", "pct", "report_date"])


def parse_industries(d: dict) -> pd.DataFrame:
    rows = []
    for a in (d.get("productIndustriesHoldingList") if d.get("productIndustriesHoldingList") is not None
              else d.get("industries")) or []:
        rows.append({"industry": a.get("industry"), "pct": _pct_fraction(a.get("assetPercent"))})
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
