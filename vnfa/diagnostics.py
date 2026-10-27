# -*- coding: utf-8 -*-
"""Repeatable, classified diagnostics for the Fmarket endpoints used by the collector."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .fmarket import FmarketClient, FmarketEmptyResponse, FmarketError, FmarketSchemaError
from .provenance import utc_now


FAILURE_STATUSES = {"authentication_error", "rate_limit", "network_error", "schema_error", "error"}


def classify_exception(exc: BaseException) -> tuple[str, int | None]:
    """Classify a source exception without exposing response bodies or request secrets."""
    if isinstance(exc, FmarketEmptyResponse):
        return "empty_response", None
    if isinstance(exc, FmarketSchemaError):
        return "schema_error", None
    if isinstance(exc, FmarketError):
        status = int(exc.api_status) if str(exc.api_status).isdigit() else None
        if status in (401, 403):
            return "authentication_error", status
        if status == 429:
            return "rate_limit", status
        if status == 408 or status is not None and status >= 500:
            return "network_error", status
        return "schema_error", status
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return "authentication_error", status
        if status == 429:
            return "rate_limit", status
        if status == 408 or status is not None and status >= 500:
            return "network_error", status
        return "schema_error", status
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return "network_error", None
    if isinstance(exc, (requests.exceptions.JSONDecodeError, ValueError, TypeError)):
        return "schema_error", None
    if isinstance(exc, requests.exceptions.RequestException):
        return "network_error", None
    return "error", None


def _call_with_fund_code(fn, *args, fund_code: str | None = None):
    try:
        return fn(*args, fund_code=fund_code)
    except TypeError as exc:
        if "fund_code" in str(exc):
            return fn(*args)
        raise


def _last_event_status(client: FmarketClient, fallback: str) -> str:
    event = getattr(client, "last_source_event", None)
    return str(event.get("status")) if event and event.get("status") else fallback


def _probe_result(endpoint: str, status: str, *, error_type: str | None = None,
                  http_status: int | None = None, record_count: int | None = None,
                  fund: str | None = None, note: str | None = None) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "status": status,
        "error_type": error_type,
        "http_status": http_status,
        "record_count": record_count,
        "fund": fund,
        "note": note,
    }


def probe_sources(client: FmarketClient | None = None, *, include_nav: bool = True) -> dict[str, Any]:
    """Probe catalogue, one product detail, and optionally its NAV history.

    The function deliberately makes no assertion about API terms, completeness, or
    investment usefulness. It sends no secrets and stores only status/shape metadata.
    """
    client = client or FmarketClient()
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    products = None
    product_id = None
    fund_code = None
    product_asset_code = None

    try:
        products = client.list_products()
        status = "empty_response" if not products else _last_event_status(client, "success")
        results.append(_probe_result("POST /products/filter", status,
                                     record_count=len(products),
                                     note="No products returned" if not products else None))
        if products:
            candidate = next((row for row in products if row.get("id") is not None and row.get("code")), None)
            if candidate is None:
                results[-1]["status"] = "schema_error"
                results[-1]["error_type"] = "schema"
                results[-1]["note"] = "No row contains the required product id and fund code"
            else:
                product_id, fund_code = candidate["id"], str(candidate["code"])
                asset = candidate.get("dataFundAssetType") or candidate.get("assetType") or {}
                product_asset_code = asset.get("code") if isinstance(asset, dict) else asset
    except Exception as exc:  # diagnostic must report classification, not provider error text
        status, http_status = classify_exception(exc)
        results.append(_probe_result("POST /products/filter", status,
                                     error_type=status.removesuffix("_error"), http_status=http_status,
                                     note=type(exc).__name__))

    if product_id is None:
        reason = "not run because catalogue yielded no usable product id"
        results.append(_probe_result("GET /products/{id}", "not_run", note=reason))
        if include_nav:
            results.append(_probe_result("POST /product/get-nav-history", "not_run", note=reason))
    else:
        try:
            detail = _call_with_fund_code(client.product_detail, product_id, fund_code=fund_code)
            status = _last_event_status(client, "success")
            has_stock_holdings = any(key in detail for key in ("productTopHoldingList", "topHoldings"))
            if product_asset_code in {"STOCK", "BALANCED"} and not has_stock_holdings:
                status = "schema_error"
                marker = getattr(client, "_mark_last_event", None)
                if callable(marker):
                    marker("schema_error", "schema", record_count=1)
            results.append(_probe_result(
                "GET /products/{id}", status, error_type="schema" if status == "schema_error" else None,
                record_count=1, fund=fund_code,
                note="Known stock-holdings fields are missing" if status == "schema_error" else None,
            ))
        except Exception as exc:
            status, http_status = classify_exception(exc)
            detail = None
            results.append(_probe_result("GET /products/{id}", status,
                                         error_type=status.removesuffix("_error"), http_status=http_status,
                                         fund=fund_code, note=type(exc).__name__))
        if include_nav:
            if detail is None:
                results.append(_probe_result("POST /product/get-nav-history", "not_run",
                                             fund=fund_code, note="not run because product detail failed"))
            else:
                try:
                    history = _call_with_fund_code(client.nav_history, product_id, fund_code=fund_code)
                    status = _last_event_status(client, "success")
                    if history.empty:
                        status = "empty_response"
                    results.append(_probe_result("POST /product/get-nav-history", status,
                                                 record_count=len(history), fund=fund_code))
                except Exception as exc:
                    status, http_status = classify_exception(exc)
                    results.append(_probe_result("POST /product/get-nav-history", status,
                                                 error_type=status.removesuffix("_error"),
                                                 http_status=http_status, fund=fund_code,
                                                 note=type(exc).__name__))

    statuses = [item["status"] for item in results]
    if any(status in FAILURE_STATUSES for status in statuses):
        overall = "failed"
    elif "empty_response" in statuses or "not_run" in statuses:
        overall = "partial"
    else:
        overall = "success"
    events = getattr(client, "source_events", [])
    live_verified = any(
        isinstance(event.get("http_status"), int) and 200 <= event["http_status"] < 300
        for event in events
    )
    return {
        "provider": "fmarket",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "run_id": getattr(client, "run_id", None),
        "overall_status": overall,
        "probes": results,
        "terms_verified": False,
        "live_access_verified": live_verified,
        "live_attempted": bool(events),
    }
