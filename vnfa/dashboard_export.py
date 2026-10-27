# -*- coding: utf-8 -*-
"""Build a small, sanitized, browser-readable research dataset from repository CSVs.

The exporter never calls an external provider. Missing files remain missing, raw
provenance payloads are not published, and legacy records are never modified.
"""
from __future__ import annotations

import hashlib
import os
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .inventory import build_data_inventory
from .provenance import OBSERVATION_COLUMNS, QUALITY_COLUMNS, SOURCE_COLUMNS

VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = timezone.utc
SCHEMA_VERSION = "1.0.0"
MAX_SOURCE_EVENTS = 2_000
MAX_QUALITY_RECORDS = 10_000

# Whitelisted presentation fields. Do not copy arbitrary source columns into Pages.
HOLDING_FIELDS = ("ticker", "weight", "rank", "volume", "price_k", "asset_value", "industry", "update_date")
EVENT_FIELDS = ("observed_date", "fund", "mgmt_co", "ticker", "action", "basis", "w_prev", "w_new",
                "w_delta", "vol_prev", "vol_new", "vol_pct", "vol_rel", "vol_scale",
                "est_value_vnd", "censored", "flag", "asset_class")
FACT_FIELDS = {
    "allocation": ("asset_code", "asset_name", "pct", "report_date"),
    "industry": ("industry", "pct"),
    "fees": ("type", "program", "begin", "end", "unit", "fee"),
    "profile": ("name", "short_name", "mgmt_co", "asset_code", "fund_type", "risk_level",
                "management_fee_pct", "buy_min_vnd", "sell_min_units", "settle_days", "cutoff",
                "first_issue_date", "website"),
    "performance": ("nav", "ret_1d", "ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_24m",
                    "ret_36m", "ret_60m", "ret_ytd", "ann_ret_36m", "ret_since_inception"),
}


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if np.isscalar(result) else False
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    value = str(value).strip()
    return value or None


def _safe_label(value: Any, limit: int = 120) -> str | None:
    text = _text(value)
    if not text or len(text) > limit or not re.fullmatch(r"[A-Za-z0-9_.? -]+", text):
        return None
    return text


def _number(value: Any) -> int | float | None:
    if _is_missing(value) or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    if result.is_integer() and abs(result) <= 2**53:
        return int(result)
    return result


def _boolean(value: Any) -> bool | None:
    if _is_missing(value) or str(value).strip() == "":
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    token = str(value).strip().lower()
    if token in {"true", "1", "yes"}:
        return True
    if token in {"false", "0", "no"}:
        return False
    return None


def _date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = pd.to_datetime(text, errors="coerce", utc=True)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _timestamp(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = pd.Timestamp(text)
    except (TypeError, ValueError, OverflowError):
        return None
    # A timezone-free value is not silently treated as UTC.
    if parsed.tzinfo is None:
        return None
    return parsed.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def sanitize_reference(value: Any) -> str | None:
    """Retain useful public source paths while dropping credentials and URL secrets."""
    text = _text(value)
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        host = parsed.netloc.rsplit("@", 1)[-1]
        if not host:
            return None
        return urlunparse((parsed.scheme.lower(), host, parsed.path or "/", "", "", ""))
    if not parsed.scheme and text.startswith("/"):
        return urlunparse(("", "", parsed.path, "", "", ""))
    return None


def _symlink_paths(root: Path) -> list[str]:
    if not root.exists():
        return []
    if root.is_symlink():
        return ["."]
    found = []
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(directories) + files:
            path = base / name
            if path.is_symlink():
                found.append(path.relative_to(root).as_posix())
        directories[:] = [name for name in directories if not (base / name).is_symlink()]
    return sorted(found)


def _safe_csv_path(root: Path, path: Path) -> bool:
    if root.is_symlink() or path.is_symlink():
        return False
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return path.is_file()


def _read_csv(path: Path) -> tuple[pd.DataFrame | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        frame = pd.read_csv(path, sep=";", dtype=str, keep_default_na=True)
    except Exception:
        return None, "unreadable"
    return frame, "empty" if frame.empty else "available"


def _read_safe(root: Path, path: Path) -> tuple[pd.DataFrame | None, str]:
    # Check symlinks before exists(): broken symlinks are unsafe, not missing.
    if root.is_symlink() or path.is_symlink():
        return None, "unsafe_path"
    if not path.exists():
        return None, "missing"
    if not _safe_csv_path(root, path):
        return None, "unsafe_path"
    return _read_csv(path)


def _frames(root: Path, pattern: str) -> tuple[list[tuple[Path, pd.DataFrame]], list[str], str]:
    paths = sorted(root.glob(pattern)) if root.exists() else []
    safe_paths = [path for path in paths if _safe_csv_path(root, path)]
    unsafe = [path.relative_to(root).as_posix() for path in paths if path not in safe_paths]
    rows: list[tuple[Path, pd.DataFrame]] = []
    unreadable = False
    for path in safe_paths:
        frame, status = _read_csv(path)
        if frame is None:
            unreadable = True
        else:
            rows.append((path, frame))
            unreadable |= status == "unreadable"
    if not paths:
        status = "missing"
    elif unsafe and not safe_paths:
        status = "unsafe_path"
    elif unreadable:
        status = "unreadable"
    elif not rows or all(frame.empty for _, frame in rows):
        status = "empty"
    else:
        status = "available"
    return rows, unsafe, status


def _source_paths(root: Path, paths: list[Path]) -> list[str]:
    result = []
    for path in paths:
        try:
            if _safe_csv_path(root, path):
                result.append(path.relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(result)


def _inventory_status(inventory: dict[str, Any], dataset_name: str) -> dict[str, Any]:
    return inventory.get("datasets", {}).get(dataset_name, {})


def _safe_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, count in value.items():
        label = _text(key)
        numeric = _number(count)
        if label and len(label) <= 64 and re.fullmatch(r"[A-Za-z0-9_. -]+", label) and numeric is not None and numeric >= 0:
            result[label] = int(numeric)
    return dict(sorted(result.items()))


def _sanitize_source_coverage(inventory: dict[str, Any]) -> dict[str, Any]:
    source = inventory.get("source_coverage", {})
    status = inventory.get("datasets", {}).get("source_events", {}).get("status")
    if status == "available":
        projected_status = "recorded"
        event_count = _number(source.get("event_count"))
        event_count = int(event_count) if event_count is not None and event_count >= 0 else None
    elif status == "empty":
        projected_status, event_count = "empty", 0
    else:
        projected_status, event_count = "not_recorded", None
    return {
        "status": projected_status,
        "event_count": event_count,
        "providers": _safe_count_map(source.get("providers")) if projected_status == "recorded" else {},
        "outcomes": _safe_count_map(source.get("outcomes")) if projected_status == "recorded" else {},
        "http_status_counts": _safe_count_map(source.get("http_status_counts")) if projected_status == "recorded" else {},
        "last_completed_at": _timestamp(source.get("last_completed_at")) if projected_status == "recorded" else None,
        "endpoints_exported": False,
    }


def _source_envelope(*, name: str, records: list[dict[str, Any]], source_paths: list[str],
                     source_status: str, inventory_item: dict[str, Any], generated_at: str,
                     source: str, completeness: str | None = None, truncated: bool = False,
                     date_range: dict[str, str | None] | None = None,
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    status = source_status
    if status == "available" and not records:
        status = "empty"
    if truncated and status in {"available", "partial"}:
        status = "partial"
    known_empty_count = (len(records) if status in {"available", "empty"} or (status == "partial" and records) else None)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": name,
        "generated_at": generated_at,
        "status": status,
        "record_count": known_empty_count,
        "source": source,
        "source_paths": source_paths,
        "source_date_range": date_range or {},
        "completeness": completeness or status,
        "validation_status": inventory_item.get("quality", {}).get("status", "not_applicable"),
        "records": records,
    }
    if truncated:
        payload["truncated"] = True
    if inventory_item.get("missing_required_columns"):
        payload["schema_issues"] = inventory_item["missing_required_columns"]
    if extra:
        payload.update(extra)
    return payload


def _to_iso_range(frame: pd.DataFrame, column: str) -> dict[str, str | None]:
    if column not in frame.columns or frame.empty:
        return {}
    values = pd.to_datetime(frame[column], errors="coerce", utc=True).dropna()
    if values.empty:
        return {"first": None, "last": None}
    return {"first": values.min().strftime("%Y-%m-%d"), "last": values.max().strftime("%Y-%m-%d")}


def _read_provenance(path: Path) -> tuple[list[dict[str, Any]], str]:
    frame, status = _read_csv(path)
    if frame is None:
        return [], status
    if not set(OBSERVATION_COLUMNS).issubset(frame.columns):
        return [], "schema_mismatch"
    records = []
    for row in frame.to_dict(orient="records"):
        flags = []
        raw_flags = _text(row.get("quality_flags"))
        if raw_flags:
            try:
                decoded = json.loads(raw_flags)
                flags = [str(item)[:100] for item in decoded if isinstance(item, str)] if isinstance(decoded, list) else []
            except json.JSONDecodeError:
                flags = []
        records.append({
            "dataset": _safe_label(row.get("dataset")),
            "fund": _text(row.get("fund")),
            "record_key": _text(row.get("record_key")),
            "effective_date": _date(row.get("effective_date")),
            "observed_at": _timestamp(row.get("observed_at")),
            "published_at": _timestamp(row.get("published_at")),
            "ingested_at": _timestamp(row.get("ingested_at")),
            "observed_date": _date(row.get("observed_date")),
            "observed_date_basis": _safe_label(row.get("observed_date_basis")) or "unknown",
            "source": _safe_label(row.get("source")),
            "source_reference": sanitize_reference(row.get("source_reference")),
            "data_quality_status": _safe_label(row.get("data_quality_status")) or "unknown",
            "verification_status": _safe_label(row.get("verification_status")) or "unknown",
            "quality_flags": flags,
            "coverage_type": _text(row.get("coverage_type")),
        })
    records.sort(key=lambda r: (r["dataset"] or "", r["fund"] or "", r["record_key"] or "",
                                r["observed_at"] or "", r["ingested_at"] or ""))
    return records, status


def _latest_provenance(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        dataset, fund = record.get("dataset"), record.get("fund")
        if not dataset or not fund:
            continue
        key = (dataset, fund)
        old = latest.get(key)
        sort_key = (record.get("observed_at") or "", record.get("ingested_at") or "")
        if old is None or sort_key > (old.get("observed_at") or "", old.get("ingested_at") or ""):
            latest[key] = record
    return latest


def _select_latest_snapshot(frame: pd.DataFrame, *, fund_hint: str | None = None) -> pd.DataFrame:
    """Select the latest date/hash block without reordering revisions in the source CSV."""
    if frame.empty:
        return frame.copy()
    data = frame.copy().reset_index(drop=True)
    if "fund" not in data.columns:
        data["fund"] = fund_hint
    if "observed_date" in data.columns:
        data["_parsed_date"] = pd.to_datetime(data.observed_date, errors="coerce")
    else:
        data["_parsed_date"] = pd.NaT
    parts = []
    for _, group in data.groupby("fund", dropna=False, sort=True):
        valid_dates = group._parsed_date.dropna()
        if not valid_dates.empty:
            latest_date = valid_dates.max()
            candidates = group[group._parsed_date == latest_date]
        else:
            candidates = group
        if "content_hash" in candidates.columns:
            hashes = candidates.content_hash.dropna()
            if not hashes.empty:
                latest_hash = hashes.iloc[-1]
                candidates = candidates[candidates.content_hash == latest_hash]
        parts.append(candidates)
    if not parts:
        return data.iloc[0:0].drop(columns=["_parsed_date"], errors="ignore")
    result = pd.concat(parts, ignore_index=True).drop(columns=["_parsed_date"], errors="ignore")
    return result


def _safe_record_hash(record: dict[str, Any]) -> str:
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _holdings_records(root: Path, inventory: dict[str, Any], provenance: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], str, bool]:
    specs = (
        ("holdings/*.csv", "fund_holdings", "top_n_partial_censored", "STOCK"),
        ("bonds/*.csv", "fund_bond_holdings", "top_n_partial_censored", "BOND"),
    )
    output: list[dict[str, Any]] = []
    all_paths: list[str] = []
    statuses: list[str] = []
    unexported_source_rows = False
    for pattern, dataset, coverage, asset_class in specs:
        files, unsafe_paths, path_status = _frames(root, pattern)
        all_paths.extend(path.relative_to(root).as_posix() for path, _ in files)
        if unsafe_paths:
            statuses.append("unsafe_path")
        if path_status != "missing":
            statuses.append(path_status)
        item = _inventory_status(inventory, dataset)
        required = {"observed_date", "fund", "ticker", "weight"}
        for path, frame in files:
            unexported_source_rows |= not frame.empty
            if not required.issubset(frame.columns):
                statuses.append("schema_mismatch")
                continue
            latest = _select_latest_snapshot(frame, fund_hint=path.stem)
            for row in latest.to_dict(orient="records"):
                fund = _text(row.get("fund")) or path.stem
                ticker = _text(row.get("ticker"))
                if not ticker:
                    continue
                record = {
                    "fund": fund,
                    "ticker": ticker.upper(),
                    "asset_class": asset_class,
                    "coverage_type": coverage,
                    "observed_date": _date(row.get("observed_date")),
                    "effective_date": _date(row.get("update_date")),
                    "observed_at": None,
                    "published_at": None,
                    "observed_date_basis": "unknown",
                    "source": "repository_legacy_holdings",
                    "source_reference": None,
                    "data_quality_status": item.get("quality", {}).get("status", "unknown"),
                    "verification_status": "legacy_source_unknown",
                    "rank": _number(row.get("rank")),
                    "weight": _number(row.get("weight")),
                    "volume": _number(row.get("volume")),
                    "price_k": _number(row.get("price_k")),
                    "asset_value": _number(row.get("asset_value")),
                    "industry": _text(row.get("industry")),
                    "content_hash": _text(row.get("content_hash")),
                }
                # Use only an exact date/key match; otherwise do not imply a time belongs to this row.
                matches = [p for p in provenance if p.get("dataset") == dataset and p.get("fund") == fund
                           and (p.get("record_key") or "").upper() == ticker.upper() and p.get("observed_date") == record["observed_date"]
                           and record["observed_date"] is not None]
                if matches:
                    evidence = matches[-1]
                    for key in ("observed_at", "published_at", "observed_date_basis", "source",
                                "source_reference", "data_quality_status", "verification_status"):
                        if evidence.get(key) is not None:
                            record[key] = evidence[key]
                record["record_id"] = _safe_record_hash({key: value for key, value in record.items() if key != "record_id"})
                output.append(record)
    output.sort(key=lambda r: (r["fund"], r["asset_class"], r["rank"] if r["rank"] is not None else 10**9, r["ticker"]))
    errors = set(statuses) & {"unsafe_path", "unreadable", "schema_mismatch"}
    if output:
        status = "partial" if errors else "available"
    elif "unsafe_path" in errors:
        status = "unsafe_path"
    elif "unreadable" in errors:
        status = "unreadable"
    elif "schema_mismatch" in errors:
        status = "schema_mismatch"
    elif unexported_source_rows:
        status = "partial"
    elif "empty" in statuses or "available" in statuses:
        status = "empty"
    else:
        status = "missing"
    return output, sorted(set(all_paths)), status, "unsafe_path" in statuses


def _map_event(action: str | None) -> tuple[str, str, bool]:
    mapping = {
        "BUY": ("position_increase_inferred", "Tăng vị thế (suy luận; chưa xác nhận giao dịch)", False),
        "SELL": ("position_decrease_inferred", "Giảm vị thế (suy luận; chưa xác nhận giao dịch)", False),
        "PRORATA": ("pro_rata_change", "Biến động đồng loạt (heuristic; không phải giao dịch)", False),
        "UNCONFIRMED?": ("unconfirmed_volume_change", "Thay đổi khối lượng (chưa xác nhận)", False),
        "WEIGHT_CHANGE?": ("unconfirmed_weight_change", "Thay đổi tỷ trọng (chưa xác nhận)", False),
        "NEW_IN_TOP10": ("entered_top_n", "Xuất hiện trong top-N; danh mục bị kiểm duyệt", True),
        "EXIT_TOP10": ("left_top_n", "Rời top-N; không đồng nghĩa bán hết", True),
        "CORP_ACTION?": ("possible_corporate_action", "Nghi vấn sự kiện doanh nghiệp; chưa xác minh", False),
    }
    return mapping.get(action or "", ("unclassified", "Sự kiện chưa phân loại", False))


def _events(root: Path, inventory: dict[str, Any], provenance: list[dict[str, Any]], *,
            cutoff_date: date, max_records: int) -> tuple[list[dict[str, Any]], list[str], str, bool, dict[str, str | None]]:
    path = root / "changes.csv"
    frame, read_status = _read_safe(root, path)
    if frame is None:
        return [], [], read_status, False, {}
    missing = [column for column in ("observed_date", "fund", "ticker", "action") if column not in frame.columns]
    if missing:
        return [], ["changes.csv"], "schema_mismatch", False, {}
    complete_range = _to_iso_range(frame, "observed_date")
    window_truncated = False
    if not frame.empty:
        parsed = pd.to_datetime(frame.observed_date, errors="coerce")
        cutoff = pd.Timestamp(cutoff_date - timedelta(days=365 * 5))
        window_truncated = bool((parsed.notna() & (parsed < cutoff)).any())
        frame = frame[parsed.isna() | (parsed >= cutoff)].copy()
    frame["_source_order"] = range(len(frame))
    frame["_parsed_event_date"] = pd.to_datetime(frame["observed_date"], errors="coerce")
    capped = len(frame) > max_records
    if capped:
        frame = (frame.sort_values(["_parsed_event_date", "_source_order"], ascending=[False, False], na_position="last")
                      .head(max_records).sort_values("_source_order").copy())
    frame = frame.drop(columns=["_source_order", "_parsed_event_date"], errors="ignore")
    was_truncated = window_truncated or capped
    records = []
    for row in frame.to_dict(orient="records"):
        action_raw = _text(row.get("action"))
        allowed_actions = {"BUY", "SELL", "PRORATA", "UNCONFIRMED?", "WEIGHT_CHANGE?", "NEW_IN_TOP10", "EXIT_TOP10", "CORP_ACTION?"}
        action = action_raw if action_raw in allowed_actions else None
        category, label, censored_default = _map_event(action)
        raw_basis = _text(row.get("basis"))
        basis = raw_basis if raw_basis in {"volume", "weight", "rank"} else None
        date_text = _date(row.get("observed_date"))
        fund = _text(row.get("fund"))
        ticker = _text(row.get("ticker"))
        event = {
            "observed_date": date_text,
            "fund": fund,
            "mgmt_co": _text(row.get("mgmt_co")),
            "ticker": ticker.upper() if ticker else None,
            "action": action,
            "classification": category,
            "display_label": label,
            "evidence_status": "inferred_not_transaction" if action in {"BUY", "SELL"}
                else "suspected" if action == "CORP_ACTION?"
                else "observed_top_n_membership" if action in {"NEW_IN_TOP10", "EXIT_TOP10"}
                else "unconfirmed" if action in {"UNCONFIRMED?", "WEIGHT_CHANGE?"}
                else "heuristic" if action == "PRORATA" else "unknown",
            "basis": basis,
            "weight_previous": _number(row.get("w_prev")),
            "weight_current": _number(row.get("w_new")),
            "weight_delta": _number(row.get("w_delta")),
            "volume_previous": _number(row.get("vol_prev")),
            "volume_current": _number(row.get("vol_new")),
            "volume_change_pct": _number(row.get("vol_pct")),
            "volume_relative_change": _number(row.get("vol_rel")),
            "fund_scale_proxy": _number(row.get("vol_scale")),
            "estimated_value_vnd": _number(row.get("est_value_vnd")),
            "censored": _boolean(row.get("censored")) if _boolean(row.get("censored")) is not None else censored_default,
            "flag": _safe_label(row.get("flag")),
            "asset_class": _safe_label(row.get("asset_class")),
            "source_dataset": "changes",
            "source_reference": None,
            "is_confirmed_transaction": False,
        }
        # Attach a source reference only when a recorded observation matches this fund/date.
        matches = [p for p in provenance if p.get("dataset") in {"fund_holdings", "fund_bond_holdings"}
                   and p.get("fund") == fund and (p.get("record_key") or "").upper() == (ticker.upper() if ticker else "")
                   and p.get("observed_date") == date_text and date_text is not None]
        if matches:
            event["source_reference"] = matches[-1].get("source_reference")
            event["observed_at"] = matches[-1].get("observed_at")
            event["published_at"] = matches[-1].get("published_at")
        else:
            event["observed_at"] = None
            event["published_at"] = None
        event["event_id"] = _safe_record_hash(event)
        records.append(event)
    records.sort(key=lambda r: (r["observed_date"] or "", r["fund"] or "", r["ticker"] or "", r["event_id"]))
    status = "available" if records else "empty"
    return records, ["changes.csv"], status, was_truncated, complete_range


def _latest_facts(root: Path, inventory: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str], str]:
    output: dict[str, list[dict[str, Any]]] = {name: [] for name in FACT_FIELDS}
    all_paths, statuses = [], []
    for name, columns in FACT_FIELDS.items():
        path = root / f"{name}.csv"
        frame, read_status = _read_safe(root, path)
        if read_status != "missing":
            if _safe_csv_path(root, path):
                all_paths.append(path.name)
            statuses.append(read_status)
        if frame is None:
            continue
        required = {"fund"}
        if name == "allocation":
            required |= {"asset_code", "pct"}
        elif name == "industry":
            required |= {"industry", "pct"}
        if not required.issubset(frame.columns):
            statuses.append("schema_mismatch")
            continue
        if frame.empty:
            continue
        if "observed_date" in frame.columns:
            parsed = pd.to_datetime(frame.observed_date, errors="coerce")
            if parsed.notna().any():
                max_by_fund = parsed.groupby(frame.fund).transform("max")
                frame = frame[parsed == max_by_fund].copy()
        if "content_hash" in frame.columns:
            frame["_row_order"] = range(len(frame))
            last_hash = frame.groupby("fund", sort=False).content_hash.transform("last")
            frame = frame[frame.content_hash == last_hash]
            frame = frame.drop(columns="_row_order", errors="ignore")
        for row in frame.to_dict(orient="records"):
            result = {"fund": _text(row.get("fund")), "observed_date": _date(row.get("observed_date"))}
            for column in columns:
                value = row.get(column)
                if column in {"pct", "fee", "management_fee_pct", "nav", "ret_1d", "ret_1m", "ret_3m", "ret_6m",
                              "ret_12m", "ret_24m", "ret_36m", "ret_60m", "ret_ytd", "ann_ret_36m",
                              "ret_since_inception", "buy_min_vnd", "sell_min_units", "settle_days", "begin", "end"}:
                    result[column] = _number(value)
                else:
                    result[column] = _text(value)
            if name == "profile" and result.get("website"):
                result["website"] = sanitize_reference(result["website"])
            output[name].append(result)
        output[name].sort(key=lambda r: (r.get("fund") or "", r.get("observed_date") or ""))
    if not statuses:
        status = "missing"
    elif "unsafe_path" in statuses:
        status = "unsafe_path"
    elif "unreadable" in statuses:
        status = "partial" if any(rows for rows in output.values()) else "unreadable"
    elif "schema_mismatch" in statuses:
        status = "partial" if any(rows for rows in output.values()) else "schema_mismatch"
    elif any(rows for rows in output.values()):
        status = "available"
    elif "empty" in statuses:
        status = "empty"
    else:
        status = "missing"
    return output, sorted(set(all_paths)), status


def _read_source_events(path: Path, max_records: int = MAX_SOURCE_EVENTS) -> tuple[list[dict[str, Any]], str, bool, int | None]:
    frame, status = _read_csv(path)
    if frame is None:
        return [], status, False, None
    if not set(SOURCE_COLUMNS).issubset(frame.columns):
        return [], "schema_mismatch", False, None
    total_count = len(frame)
    records = []
    for row in frame.to_dict(orient="records"):
        endpoint_raw = _text(row.get("endpoint"))
        method, _, endpoint_path = (endpoint_raw or "").partition(" ")
        method = method.upper() if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else ""
        safe_path = sanitize_reference(row.get("source_reference")) or sanitize_reference(endpoint_path)
        endpoint = f"{method} {safe_path}" if method and safe_path else safe_path
        response_hash = _text(row.get("response_hash"))
        if not response_hash or not re.fullmatch(r"[A-Fa-f0-9]{64}", response_hash):
            response_hash = None
        records.append({
            "provider": _safe_label(row.get("provider")),
            "endpoint": endpoint,
            "started_at": _timestamp(row.get("started_at")),
            "observed_at": _timestamp(row.get("observed_at")),
            "completed_at": _timestamp(row.get("completed_at")),
            "status": _safe_label(row.get("status")) or "unknown",
            "error_type": _safe_label(row.get("error_type")),
            "http_status": _number(row.get("http_status")),
            "record_count": _number(row.get("record_count")),
            "retry_attempts": _number(row.get("retry_attempts")),
            "response_hash": response_hash,
        })
    records.sort(key=lambda r: (r.get("completed_at") or "", r.get("provider") or "", r.get("endpoint") or ""))
    truncated = len(records) > max_records
    if truncated:
        records = records[-max_records:]
    return records, status, truncated, total_count


def _safe_last_run(root: Path, path: Path) -> tuple[dict[str, Any] | None, str]:
    if root.is_symlink() or path.is_symlink():
        return None, "unsafe_path"
    if not path.exists():
        return None, "missing"
    try:
        path.resolve().relative_to(root.resolve())
        if path.stat().st_size > 2_000_000:
            return None, "unreadable"
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(source, dict):
        return None, "schema_mismatch"
    count_fields = ("n_total", "n_ok", "n_failed", "n_partial", "n_changed", "n_baseline", "n_empty_holdings",
                    "n_bond_changed", "n_nav_funds", "n_nav_history_failed", "n_nav_invalid", "n_quality_rejected",
                    "n_quality_errors", "n_quality_warnings", "n_quality_reports", "n_provenance_observations",
                    "n_source_events")
    if "date" not in source or not any(field in source for field in count_fields):
        return None, "schema_mismatch"
    summary = {"date": _date(source.get("date")), "overall_status": "unknown_not_recorded",
               "status_note": "A collector summary is not proof that the enclosing GitHub Actions job succeeded."}
    for field in count_fields:
        summary[field] = _number(source.get(field))
    return summary, "available"


def _make_issue(issue_id: str, severity: str, dataset: str, message: str, count: int | None,
                detected_at: str, remediation: str) -> dict[str, Any]:
    return {"issue_id": issue_id, "severity": severity, "dataset": dataset,
            "message": message, "affected_records": count, "detected_at": detected_at,
            "remediation": remediation}


def _data_quality(inventory: dict[str, Any], provenance: list[dict[str, Any]], events: list[dict[str, Any]],
                  generated_at: str, source_status: str, duplicate_counts: dict[str, int | None]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for dataset, item in inventory.get("datasets", {}).items():
        status = item.get("status")
        if status == "missing":
            issues.append(_make_issue(f"missing:{dataset}", "info", dataset,
                "Dataset chưa có trong archive; đây là tình trạng thiếu dữ liệu, không phải bằng chứng nguồn trả về rỗng.",
                None, generated_at, "Chỉ thu thập từ nguồn được phép; giữ rõ missingness và provenance."))
        elif status in {"unreadable", "schema_mismatch"}:
            issues.append(_make_issue(f"schema:{dataset}", "error", dataset,
                f"Dataset có trạng thái {status}; cần kiểm tra file và schema trước khi dùng.",
                item.get("row_count"), generated_at, "Kiểm tra header/encoding và xử lý migration tường minh; không ghi đè archive."))
        quality = item.get("quality", {})
        for finding in quality.get("errors", []) + quality.get("warnings", []):
            issues.append(_make_issue(f"quality:{dataset}:{finding.get('code', 'unknown')}",
                finding.get("severity", "warning"), dataset,
                finding.get("message", "Data quality finding"), _number(finding.get("count")), generated_at,
                "Đối chiếu source record và validator output; không tự sửa hoặc thay missing bằng zero."))
    if source_status == "unsafe_path":
        issues.append(_make_issue("unsafe_path:research_observations", "error", "research_observations",
            "Provenance sidecar is a symbolic link or resolves outside the data directory; it was not exported.",
            None, generated_at, "Use a regular repository-local file after reviewing the source path."))
    elif source_status == "schema_mismatch":
        issues.append(_make_issue("schema:research_observations", "error", "research_observations",
            "Provenance sidecar không khớp schema phiên bản kỳ vọng.", None, generated_at,
            "Giữ bản gốc và thực hiện migration có sao lưu."))
    elif provenance:
        missing_source = sum(not item.get("source_reference") for item in provenance)
        missing_pub = sum(not item.get("published_at") for item in provenance)
        missing_observed = sum(not item.get("observed_at") for item in provenance)
        if missing_source:
            issues.append(_make_issue("missing_source_reference", "warning", "research_observations",
                "Một số observation không có source reference.", missing_source, generated_at,
                "Ghi endpoint/URL đã được kiểm chứng; không đưa token/query credential vào reference."))
        if missing_pub:
            issues.append(_make_issue("publication_time_unknown", "warning", "research_observations",
                "Publication timestamp còn thiếu; effective date không thay thế thời điểm công bố.", missing_pub,
                generated_at, "Giữ published_at trống trừ khi nguồn cung cấp hoặc thời điểm được xác minh."))
        if missing_observed:
            issues.append(_make_issue("observation_time_unknown", "warning", "research_observations",
                "Observation timestamp còn thiếu; các bản ghi này không đủ căn cứ cho PIT mặc định.", missing_observed,
                generated_at, "Không suy ra observed_at từ thời gian file hoặc kỳ hiệu lực."))
        approximate = sum(item.get("observed_date_basis") == "approximate" for item in provenance)
        if approximate:
            issues.append(_make_issue("approximate_dates", "warning", "research_observations",
                "Một số ngày đến từ mirror/commit xấp xỉ và không PIT-safe.", approximate, generated_at,
                "Giữ trạng thái approximate; không đưa các bản ghi này vào cutoff PIT mặc định."))
    if events:
        unconfirmed = sum(item.get("evidence_status") in {"unconfirmed", "inferred_not_transaction", "suspected", "heuristic"}
                          for item in events)
        censored = sum(bool(item.get("censored")) for item in events)
        if unconfirmed:
            issues.append(_make_issue("unconfirmed_portfolio_events", "warning", "changes",
                "Portfolio events mô tả thay đổi quan sát hoặc suy luận, không phải giao dịch đã xác nhận.",
                unconfirmed, generated_at, "Đọc basis, corporate-action flag và coverage trước khi diễn giải."))
        if censored:
            issues.append(_make_issue("top_n_censoring", "warning", "fund_holdings",
                "Một số sự kiện là vào/rời top-N; không chứng minh mua mới hoặc bán hết.", censored,
                generated_at, "Dùng factsheet đầy đủ nếu có; không chuyển missing position thành zero."))
    for dataset, count in duplicate_counts.items():
        if count:
            issues.append(_make_issue(f"duplicate:{dataset}", "warning", dataset,
                "Phát hiện khóa trùng; kiểm tra xem đây là revision hợp lệ hay dữ liệu lặp ngoài dự kiến.",
                count, generated_at, "Đối chiếu khóa tự nhiên, revision và chính sách deduplicate trước khi dùng."))
    stale_status = "not_assessed_no_sla"
    inventory_summaries = []
    for name, item in inventory.get("datasets", {}).items():
        ranges = item.get("date_coverage", {})
        last_dates = [value.get("last") for value in ranges.values() if value.get("last")]
        latest = max(last_dates) if last_dates else None
        ages = [value.get("age_days_reference_date") for value in ranges.values()
                if value.get("age_days_reference_date") is not None]
        inventory_summaries.append({
            "dataset": name,
            "status": item.get("status"),
            "observed_rows": None if item.get("status") == "missing" else item.get("row_count"),
            "fund_count": None if item.get("status") == "missing" else item.get("fund_count"),
            "source_paths": item.get("paths", []),
            "latest_date": latest,
            "age_days_at_reference": min(ages) if ages else None,
            "freshness_assessment": stale_status,
            "quality_status": item.get("quality", {}).get("status", "not_applicable"),
            "validation_errors": sum(int(f.get("count", 0) or 0) for f in item.get("quality", {}).get("errors", [])),
            "validation_warnings": sum(int(f.get("count", 0) or 0) for f in item.get("quality", {}).get("warnings", [])),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "status": "issues_found" if any(item["severity"] in {"error", "warning"} for item in issues)
            else "data_unavailable" if not inventory.get("data_directory_exists") or not inventory.get("summary", {}).get("datasets_available") else "no_findings_recorded",
        "data_directory_exists": inventory.get("data_directory_exists", False),
        "freshness_policy": "Age is shown; stale is not assigned because no dataset SLA is configured.",
        "source_coverage": inventory.get("source_coverage", {}),
        "datasets": inventory_summaries,
        "issues": issues,
        "duplicate_checks": duplicate_counts,
        "source_time_coverage": {
            "observation_records": len(provenance) if source_status in {"available", "empty"} else None,
            "published_at_missing": sum(not item.get("published_at") for item in provenance) if source_status in {"available", "empty"} else None,
            "observed_at_missing": sum(not item.get("observed_at") for item in provenance) if source_status in {"available", "empty"} else None,
            "source_reference_missing": sum(not item.get("source_reference") for item in provenance) if source_status in {"available", "empty"} else None,
        },
        "unconfirmed_event_count": sum(item.get("evidence_status") in {"unconfirmed", "inferred_not_transaction", "suspected", "heuristic"} for item in events)
            if _inventory_status(inventory, "changes").get("status") in {"available", "empty"} else None,
    }


def _pipeline_status(root: Path, source_events: list[dict[str, Any]], source_event_status: str,
                     generated_at: str, *, source_event_total: int | None,
                     source_event_outcomes: dict[str, int] | None) -> dict[str, Any]:
    summary, summary_status = _safe_last_run(root, root / "last_run.json")
    latest_request = source_events[-1] if source_events else None
    # The source ledger does not include run_id in its public projection; report requests
    # in aggregate and explicitly keep overall workflow status unknown.
    outcomes: dict[str, int] = {}
    for event in source_events:
        status = event.get("status") or "unknown"
        outcomes[status] = outcomes.get(status, 0) + 1
    if source_event_outcomes is None and source_event_status in {"available", "empty"}:
        source_event_outcomes = outcomes
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "status": "unavailable" if not source_events and summary_status == "missing" else "recorded_activity",
        "overall_workflow_status": "unknown_not_exported",
        "status_explanation": "Individual request outcomes and collector counters do not prove that a GitHub Actions job completed successfully. No sanitized workflow-run result is stored in this checkout.",
        "latest_collector_summary": summary,
        "collector_summary_status": summary_status,
        "latest_source_request": latest_request,
        "source_request_count": source_event_total if source_event_status in {"available", "empty"} else None,
        "source_request_exported_count": len(source_events) if source_event_status in {"available", "empty"} else None,
        "source_request_outcomes": source_event_outcomes if source_event_status in {"available", "empty"} else None,
        "actions_url": "https://github.com/FTU-kudo/VN_Fund_Analysis/actions/workflows/daily.yml",
        "last_known_status": "unavailable_or_unverified",
    }


def _duplicate_count(root: Path, filename: str, keys: tuple[str, ...]) -> int | None:
    path = root / filename
    frame, status = _read_safe(root, path)
    if frame is None or status in {"unreadable", "unsafe_path"} or not set(keys).issubset(frame.columns):
        return None
    if frame.empty:
        return 0
    return int(frame.duplicated(list(keys), keep="first").sum())


def _duplicate_count_snapshot_files(root: Path, pattern: str) -> int | None:
    files = sorted(root.glob(pattern)) if root.exists() else []
    if not files:
        return None
    total = 0
    for path in files:
        frame, status = _read_safe(root, path)
        if frame is None or status in {"unreadable", "unsafe_path"}:
            return None
        keys = ("observed_date", "content_hash", "ticker")
        if not set(keys).issubset(frame.columns):
            return None
        total += int(frame.duplicated(list(keys), keep="first").sum())
    return total


def export_dashboard(data_dir: str | Path, output_dir: str | Path, *, generated_at: str | None = None,
                    reference_date: str | date | None = None, nav_years: int = 5,
                    max_events: int = 25_000) -> dict[str, Any]:
    """Export a validated, compact JSON dashboard package without changing source data.

    `generated_at` can be pinned (for example in tests or reproducible builds). Dataset
    records are byte-stable for identical inputs; manifest generation time is explicit
    operational metadata and therefore changes when a new export is made.
    """
    if not 1 <= nav_years <= 10 or not 1 <= max_events <= 100_000:
        raise ValueError("nav_years must be 1..10 and max_events must be 1..100000")
    if generated_at is None:
        generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if _timestamp(generated_at) is None:
        raise ValueError("generated_at must be a timezone-aware timestamp")
    if reference_date is None:
        reference = datetime.now(VIETNAM).date()
    elif isinstance(reference_date, datetime):
        reference = reference_date.date()
    elif isinstance(reference_date, date):
        reference = reference_date
    else:
        reference = pd.Timestamp(reference_date).date()

    root = Path(data_dir)
    unsafe_links = _symlink_paths(root)
    if unsafe_links:
        raise ValueError(f"Refusing to export data directory with symbolic links: {unsafe_links[:5]}")
    destination = Path(output_dir)
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("output_dir must be outside data_dir; dashboard artifacts must not mix with the raw archive")
    destination.mkdir(parents=True, exist_ok=True)
    inventory = build_data_inventory(root, reference_date=reference)
    inventory["generated_at"] = generated_at
    source_coverage = _sanitize_source_coverage(inventory)
    dataset_envelopes: dict[str, dict[str, Any]] = {}

    # Parse read-only provenance metadata; raw payload_json and run IDs are never exported.
    provenance_path = root / "research_observations.csv"
    if provenance_path.is_symlink() or (provenance_path.exists() and not _safe_csv_path(root, provenance_path)):
        provenance_records, provenance_status = [], "unsafe_path"
    else:
        provenance_records, provenance_status = _read_provenance(provenance_path)
    provenance_paths = ["research_observations.csv"] if provenance_status not in {"missing", "unsafe_path"} and _safe_csv_path(root, provenance_path) else []

    # Fund catalogue, enriched only by IDs actually present in source tables.
    fund_path = root / "funds.csv"
    funds_frame, funds_read = _read_safe(root, fund_path)
    funds_source_rows = None
    funds_invalid_rows = 0
    if funds_frame is not None and "code" in funds_frame.columns:
        funds_source_rows = len(funds_frame)
        fund_rows = {}
        for row in funds_frame.to_dict(orient="records"):
            code = _text(row.get("code"))
            if not code:
                funds_invalid_rows += 1
                continue
            fund_rows[code] = {
                "fund_id": code,
                "name": _text(row.get("short_name")) or _text(row.get("name")) or code,
                "management_company": _text(row.get("mgmt_co")),
                "asset_code": _text(row.get("asset_code")),
                "management_fee": _number(row.get("management_fee")),
                "last_seen": _date(row.get("last_seen")),
            }
        funds_status = funds_read
    else:
        fund_rows = {}
        funds_status = "schema_mismatch" if funds_frame is not None else funds_read
        if funds_frame is not None:
            funds_read = "schema_mismatch"
    holdings_rows, holdings_paths, holdings_status, unsafe_holdings = _holdings_records(root, inventory, provenance_records)
    fund_ids = {row["fund"] for row in holdings_rows if row.get("fund")}
    events_path = root / "changes.csv"
    events_rows, event_paths, events_status, events_truncated, event_range = _events(
        root, inventory, provenance_records, cutoff_date=reference, max_records=max_events)
    fund_ids.update(row["fund"] for row in events_rows if row.get("fund"))
    # When the catalogue CSV is absent, identify funds only from actual repository rows.
    derivation_requirements = {
        "nav.csv": {"fund", "date", "nav"},
        "nav_snapshots.csv": {"fund", "date", "nav"},
        "allocation.csv": {"fund", "asset_code", "pct"},
        "industry.csv": {"fund", "industry", "pct"},
        "fees.csv": {"fund"}, "profile.csv": {"fund"}, "performance.csv": {"fund"},
        "holdings_full.csv": {"fund", "ticker", "weight", "period_end", "source_url"},
    }
    for filename, required_columns in derivation_requirements.items():
        frame, _ = _read_safe(root, root / filename)
        if frame is not None and required_columns.issubset(frame.columns):
            fund_ids.update(code for value in frame.fund.dropna().unique() if (code := _text(value)))
    for fund_id in sorted(fund_ids):
        fund_rows.setdefault(fund_id, {"fund_id": fund_id, "name": fund_id,
                                      "management_company": None, "asset_code": None,
                                      "management_fee": None, "last_seen": None})
    for fund in fund_rows.values():
        relevant = [row for row in holdings_rows if row.get("fund") == fund["fund_id"]]
        fund["latest_holdings_date"] = max((row["observed_date"] for row in relevant if row.get("observed_date")), default=None)
        fund["latest_holdings_count"] = len(relevant) if relevant else None
        fund["latest_holdings_by_source"] = {
            source: sum(row.get("fund") == fund["fund_id"] and row.get("coverage_type") == source for row in holdings_rows)
            for source in ("top_n_partial_censored", "source_claimed_full_not_independently_verified")
        }
        fund["holdings_coverage"] = ("top_n_partial_censored" if relevant and any(
            row["coverage_type"] == "top_n_partial_censored" for row in relevant) else
            "source_claimed_full_not_independently_verified" if relevant else None)
        fund["data_quality_status"] = "partial" if fund["holdings_coverage"] else "unknown"

    funds_records = sorted(fund_rows.values(), key=lambda r: (r.get("fund_id") or ""))
    funds_inventory = _inventory_status(inventory, "fund_metadata")
    fund_source_paths = ["funds.csv"] if funds_read != "missing" and _safe_csv_path(root, fund_path) else []
    fund_source_paths.extend(path for path in holdings_paths if path not in fund_source_paths)
    if funds_read == "unsafe_path":
        funds_source_status = "unsafe_path" if not fund_source_paths else "partial"
    elif funds_read in {"unreadable", "schema_mismatch"}:
        funds_source_status = "partial" if funds_records else funds_read
    elif funds_records and funds_read in {"missing", "empty"}:
        funds_source_status = "partial"
    elif funds_invalid_rows:
        funds_source_status = "partial"
    elif not fund_source_paths and not funds_records:
        funds_source_status = "missing"
    elif fund_source_paths and not funds_records:
        funds_source_status = "empty"
    else:
        funds_source_status = "available"
    dataset_envelopes["funds"] = _source_envelope(
        name="funds", records=funds_records, source_paths=fund_source_paths,
        source_status=funds_source_status, inventory_item=funds_inventory, generated_at=generated_at,
        source="funds.csv; fund identifiers may also be derived from holdings and changes",
        completeness="fund_catalogue_partial" if funds_source_status == "partial" else "from_repository_metadata",
        extra={"catalogue_source_rows": funds_source_rows, "invalid_catalogue_rows": funds_invalid_rows,
               "source_coverage": source_coverage})

    # Latest snapshots only; the complete row archive remains in data/ and is not copied.
    holdings_envelope = _source_envelope(
        name="holdings", records=holdings_rows, source_paths=holdings_paths,
        source_status=holdings_status, inventory_item=_inventory_status(inventory, "fund_holdings"),
        generated_at=generated_at, source="holdings/*.csv and bonds/*.csv; latest stored date/hash block per fund",
        completeness="top_n_partial_censored" if holdings_rows else "missing_or_empty",
        extra={"coverage_note": "Top-N holdings are censored; source_claimed_full holdings are separately labeled and not independently verified."})
    full_path = root / "holdings_full.csv"
    full_frame, full_status = _read_safe(root, full_path)
    full_records: list[dict[str, Any]] = []
    full_required = {"fund", "ticker", "weight", "period_end", "source_url"}
    if full_frame is not None and not full_required.issubset(full_frame.columns):
        full_status = "schema_mismatch"
    if full_frame is not None and full_required.issubset(full_frame.columns):
        full_copy = full_frame.copy()
        full_copy["_period"] = pd.to_datetime(full_copy.period_end, errors="coerce")
        full_copy["_row"] = range(len(full_copy))
        for fund_id, group in full_copy.groupby("fund", sort=True):
            periods = group._period.dropna()
            latest_period = periods.max() if not periods.empty else pd.NaT
            latest = group[group._period == latest_period] if not pd.isna(latest_period) else group.tail(0)
            for row in latest.to_dict(orient="records"):
                record = {"fund": _text(fund_id), "ticker": (_text(row.get("ticker")) or "").upper(),
                    "asset_class": "STOCK", "coverage_type": "source_claimed_full_not_independently_verified",
                    "observed_date": _date(row.get("observed_date")), "effective_date": _date(row.get("period_end")),
                    "observed_at": _timestamp(row.get("observed_at")), "published_at": _timestamp(row.get("published_at")),
                    "observed_date_basis": _text(row.get("observed_date_basis")) or "unknown",
                    "source": "official_manager_report", "source_reference": sanitize_reference(row.get("source_url")),
                    "data_quality_status": "unknown", "verification_status": "source_cited_not_independently_verified",
                    "rank": _number(row.get("rank")), "weight": _number(row.get("weight")),
                    "volume": _number(row.get("volume")), "price_k": _number(row.get("price_k")),
                    "asset_value": _number(row.get("asset_value")), "industry": _text(row.get("industry")),
                    "content_hash": None}
                if record["ticker"]:
                    record["record_id"] = _safe_record_hash(record)
                    full_records.append(record)
        full_records.sort(key=lambda r: (r.get("fund") or "", r.get("rank") or 10**9, r.get("ticker") or ""))
        holdings_envelope["records"].extend(full_records)
    all_holdings_paths = sorted(set(holdings_paths + (["holdings_full.csv"] if full_status not in {"missing", "unsafe_path"} else [])))
    data_errors = {"unsafe_path", "unreadable", "schema_mismatch"}
    source_errors = {holdings_status, full_status} & data_errors
    if holdings_rows or full_records:
        partial_sources = bool(source_errors) or holdings_status == "partial" or (full_records and holdings_status == "missing")
        combined_holdings_status = "partial" if partial_sources else "available"
    elif "unsafe_path" in source_errors:
        combined_holdings_status = "unsafe_path"
    elif "unreadable" in source_errors:
        combined_holdings_status = "unreadable"
    elif "schema_mismatch" in source_errors:
        combined_holdings_status = "schema_mismatch"
    elif holdings_status == "partial":
        combined_holdings_status = "partial"
    elif all_holdings_paths:
        combined_holdings_status = "empty"
    else:
        combined_holdings_status = "missing"
    for fund in funds_records:
        relevant = [row for row in holdings_envelope["records"] if row.get("fund") == fund["fund_id"]]
        if relevant:
            fund["latest_holdings_date"] = max((row["observed_date"] for row in relevant if row.get("observed_date")), default=None)
            fund["latest_holdings_count"] = len(relevant)
            fund["latest_holdings_by_source"] = {
                source: sum(row.get("coverage_type") == source for row in relevant)
                for source in ("top_n_partial_censored", "source_claimed_full_not_independently_verified")
            }
            fund["holdings_coverage"] = (
                "top_n_partial_censored" if any(row.get("coverage_type") == "top_n_partial_censored" for row in relevant)
                else "source_claimed_full_not_independently_verified")
            fund["data_quality_status"] = "partial" if fund["holdings_coverage"] == "top_n_partial_censored" else "unknown"
    holdings_envelope["status"] = combined_holdings_status
    holdings_envelope["source_paths"] = all_holdings_paths
    exported_holdings_count = len(holdings_envelope["records"])
    holdings_envelope["record_count"] = (exported_holdings_count
                                          if combined_holdings_status in {"available", "empty"}
                                          or (combined_holdings_status == "partial" and exported_holdings_count > 0)
                                          else None)
    holdings_envelope["completeness"] = "latest_top_n_and_source_claimed_full_snapshots" if holdings_rows or full_records else "missing_or_empty"
    dataset_envelopes["holdings"] = holdings_envelope

    # Bounded NAV history: keep a recent five-year window and distinguish effective/source dates from observation dates.
    cutoff_nav = pd.Timestamp(reference - timedelta(days=365 * nav_years))
    nav_records: list[dict[str, Any]] = []
    nav_paths: list[str] = []
    nav_statuses = []
    nav_ranges: dict[str, Any] = {}
    nav_truncated = False
    nav_source_rows = 0
    nav_invalid_rows = 0
    for filename, source_type, date_type in (("nav.csv", "nav_history", "source_date_unknown_publication"),
                                               ("nav_snapshots.csv", "nav_snapshot", "observed_date_effective_date_unknown")):
        path = root / filename
        frame, read_status = _read_safe(root, path)
        if frame is None:
            nav_statuses.append(read_status)
            continue
        if not {"fund", "date", "nav"}.issubset(frame.columns):
            nav_statuses.append("schema_mismatch")
            continue
        nav_paths.append(filename)
        nav_statuses.append("empty" if frame.empty else "available")
        nav_source_rows += len(frame)
        nav_ranges[source_type] = _to_iso_range(frame, "date")
        dates = pd.to_datetime(frame.date, errors="coerce")
        nav_truncated |= bool((dates.notna() & (dates < cutoff_nav)).any())
        recent = frame[dates.isna() | (dates >= cutoff_nav)].copy()
        for row in recent.to_dict(orient="records"):
            fund_id = _text(row.get("fund"))
            date_value = _date(row.get("date"))
            nav_value = _number(row.get("nav"))
            if not fund_id or not date_value or nav_value is None or nav_value <= 0:
                nav_invalid_rows += 1
                continue
            record = {"fund": fund_id, "date": date_value, "nav": nav_value,
                      "series_type": source_type, "date_semantics": date_type,
                      "publication_time_status": "unknown", "verified": False}
            if source_type == "nav_snapshot":
                record["observed_at"] = None
                record["effective_date"] = None
                record["observation_date"] = date_value
            else:
                record["observed_at"] = None
                record["effective_date"] = date_value
                record["observation_date"] = None
            nav_records.append(record)
    nav_records.sort(key=lambda r: (r["fund"], r["series_type"], r["date"]))
    nav_errors = set(nav_statuses) & {"unsafe_path", "unreadable", "schema_mismatch"}
    valid_nav_sources = [status for status in nav_statuses if status in {"available", "empty"}]
    if not nav_statuses or all(status == "missing" for status in nav_statuses):
        nav_status = "missing"
    elif nav_errors:
        if nav_records or valid_nav_sources:
            nav_status = "partial"
        elif "unsafe_path" in nav_errors:
            nav_status = "unsafe_path"
        elif "unreadable" in nav_errors:
            nav_status = "unreadable"
        else:
            nav_status = "schema_mismatch"
    elif "missing" in nav_statuses and valid_nav_sources:
        nav_status = "partial"
    elif nav_records:
        nav_status = "partial" if nav_invalid_rows else "available"
    elif nav_invalid_rows:
        nav_status = "partial"
    elif valid_nav_sources:
        nav_status = "empty"
    else:
        nav_status = "missing"
    dataset_envelopes["nav_history"] = _source_envelope(
        name="nav_history", records=nav_records, source_paths=nav_paths, source_status=nav_status,
        inventory_item=_inventory_status(inventory, "nav_history"), generated_at=generated_at,
        source="nav.csv and nav_snapshots.csv; bounded recent history; separate date semantics",
        completeness="bounded_window_not_full_archive" if nav_truncated else "available_window_only",
        truncated=nav_truncated, date_range=nav_ranges,
        extra={"window_years": nav_years, "window_start": cutoff_nav.strftime("%Y-%m-%d"),
               "source_rows_seen": nav_source_rows, "invalid_rows_excluded": nav_invalid_rows,
               "series_types": ["nav_history", "nav_snapshot"]})

    event_inventory = _inventory_status(inventory, "changes")
    dataset_envelopes["portfolio_events"] = _source_envelope(
        name="portfolio_events", records=events_rows, source_paths=event_paths, source_status=events_status,
        inventory_item=event_inventory, generated_at=generated_at,
        source="changes.csv; derived portfolio-change journal",
        completeness="bounded_recent_5_year_window" if events_rows else "missing_or_empty",
        truncated=events_truncated, date_range=event_range,
        extra={"window_years": 5, "max_records": max_events,
               "note": "No event is a confirmed transaction; top-N exits/entries are censored."})

    # Factsheets are materialized as the latest stored block, not the entire table history.
    facts, facts_paths, facts_status = _latest_facts(root, inventory)
    dataset_envelopes["fund_facts"] = _source_envelope(
        name="fund_facts", records=[{"fund": fund, "fact_type": kind, **row}
            for kind, rows in facts.items() for row in rows for fund in [row.get("fund")]],
        source_paths=facts_paths, source_status=facts_status,
        inventory_item={"quality": {"status": "not_assessed"}}, generated_at=generated_at,
        source="latest stored blocks from allocation/industry/fees/profile/performance CSVs",
        completeness="latest_materialized_blocks_only" if facts_paths else "missing_or_empty",
        extra={"fact_types": sorted(FACT_FIELDS.keys())})

    # A compact, metadata-only projection of source attempts.
    source_path = root / "source_runs.csv"
    if source_path.is_symlink() or (source_path.exists() and not _safe_csv_path(root, source_path)):
        source_events, source_event_status, source_events_truncated, source_events_total = [], "unsafe_path", False, None
    else:
        source_events, source_event_status, source_events_truncated, source_events_total = _read_source_events(source_path)
    source_paths = ["source_runs.csv"] if source_event_status not in {"missing", "unreadable", "unsafe_path"} else []
    source_envelope = _source_envelope(
        name="source_events", records=source_events, source_paths=source_paths,
        source_status=source_event_status, inventory_item=_inventory_status(inventory, "source_events"),
        generated_at=generated_at, source="source_runs.csv; secrets, body, event IDs and run IDs excluded",
        completeness="latest_bounded_request_attempt_metadata",
        truncated=source_events_truncated,
        extra={"persisted_record_count": source_events_total, "max_records": MAX_SOURCE_EVENTS})
    dataset_envelopes["source_events"] = source_envelope

    quality_path = root / "quality_reports.csv"
    quality_frame, quality_read = _read_safe(root, quality_path)
    quality_records = []
    quality_total_count = None
    if quality_frame is not None and not set(QUALITY_COLUMNS).issubset(quality_frame.columns):
        quality_read = "schema_mismatch"
    if quality_frame is not None and set(QUALITY_COLUMNS).issubset(quality_frame.columns):
        quality_total_count = len(quality_frame)
        for row in quality_frame.to_dict(orient="records"):
            try:
                findings = json.loads(_text(row.get("findings_json")) or "[]")
            except json.JSONDecodeError:
                findings = []
            if not isinstance(findings, list):
                findings = []
            clean_findings = [{"code": _safe_label(x.get("code")), "severity": _safe_label(x.get("severity")),
                               "count": _number(x.get("count"))}
                              for x in findings if isinstance(x, dict)]
            quality_records.append({"dataset": _safe_label(row.get("dataset")), "fund": _text(row.get("fund")),
                "source": _safe_label(row.get("source")), "status": _safe_label(row.get("status")),
                "row_count": _number(row.get("row_count")), "findings": clean_findings,
                "recorded_at": _timestamp(row.get("recorded_at"))})
    latest_quality: dict[tuple[str, str], dict[str, Any]] = {}
    for item in quality_records:
        key = (item.get("dataset") or "unknown", item.get("fund") or "all")
        if key not in latest_quality or (item.get("recorded_at") or "") >= (latest_quality[key].get("recorded_at") or ""):
            latest_quality[key] = item
    quality_records = sorted(latest_quality.values(),
        key=lambda r: (r.get("recorded_at") or "", r.get("dataset") or "", r.get("fund") or ""), reverse=True)
    quality_truncated = len(quality_records) > MAX_QUALITY_RECORDS or (
        quality_total_count is not None and len(quality_records) < quality_total_count)
    quality_records = quality_records[:MAX_QUALITY_RECORDS]
    quality_records.sort(key=lambda r: (r.get("dataset") or "", r.get("fund") or "", r.get("recorded_at") or ""))
    quality_envelope_status = "partial" if quality_truncated and quality_read in {"available", "empty"} else quality_read
    dataset_envelopes["data_quality"] = _source_envelope(
        name="data_quality", records=quality_records,
        source_paths=["quality_reports.csv"] if quality_read not in {"missing", "unsafe_path"} and _safe_csv_path(root, quality_path) else [],
        source_status=quality_envelope_status, inventory_item=_inventory_status(inventory, "quality_reports"),
        generated_at=generated_at, source="latest persisted quality report per dataset/fund; raw history remains in data/",
        completeness="latest_report_per_dataset_fund; full history not copied",
        truncated=quality_truncated,
        extra={"persisted_record_count": quality_total_count, "max_records": MAX_QUALITY_RECORDS})
    duplicate_counts = {
        "fund_metadata_code": _duplicate_count(root, "funds.csv", ("code",)),
        "holdings_snapshot_key": _duplicate_count_snapshot_files(root, "holdings/*.csv"),
        "bond_snapshot_key": _duplicate_count_snapshot_files(root, "bonds/*.csv"),
        "change_event_key": _duplicate_count(root, "changes.csv", ("observed_date", "fund", "ticker")),
        "nav_history_key": _duplicate_count(root, "nav.csv", ("fund", "date")),
        "nav_snapshot_same_day_revisions": _duplicate_count(root, "nav_snapshots.csv", ("fund", "date")),
        "source_event_id": _duplicate_count(root, "source_runs.csv", ("event_id",)),
        "observation_id": _duplicate_count(root, "research_observations.csv", ("observation_id",)),
        "quality_event_id": _duplicate_count(root, "quality_reports.csv", ("quality_event_id",)),
    }
    quality_summary = _data_quality(inventory, provenance_records, events_rows, generated_at,
                                    provenance_status, duplicate_counts)
    quality_summary["source_coverage"] = source_coverage
    quality_summary["persisted_quality_event_count"] = quality_total_count if quality_read in {"available", "empty"} else None
    quality_summary["persisted_quality_status"] = quality_read
    dataset_envelopes["data_quality_summary"] = {
        "schema_version": SCHEMA_VERSION, "dataset": "data_quality_summary", "generated_at": generated_at,
        "status": quality_summary["status"], "record_count": len(quality_summary["issues"]),
        "source": "repository inventory and sanitized sidecars",
        "source_paths": sorted(provenance_paths + (["quality_reports.csv"] if quality_read not in {"missing", "unsafe_path"} and _safe_csv_path(root, quality_path) else [])),
        "source_date_range": {}, "completeness": "summary_of_present_files_only",
        "validation_status": "computed", "records": quality_summary["issues"], "summary": quality_summary,
    }

    # Overall pipeline state is deliberately unknown unless an explicit workflow result exists.
    pipeline_summary = _pipeline_status(root, source_events, source_event_status, generated_at,
                                        source_event_total=source_events_total,
                                        source_event_outcomes=source_coverage.get("outcomes") if source_coverage.get("status") in {"recorded", "empty"} else None)
    collector_status = pipeline_summary.get("collector_summary_status")
    pipeline_activity = pipeline_summary.get("latest_collector_summary") is not None or bool(source_events)
    pipeline_errors = {source_event_status, collector_status} & {"unsafe_path", "unreadable", "schema_mismatch"}
    if pipeline_activity:
        pipeline_status = "partial" if pipeline_errors else "available"
    elif "unsafe_path" in pipeline_errors:
        pipeline_status = "unsafe_path"
    elif "unreadable" in pipeline_errors:
        pipeline_status = "unreadable"
    elif "schema_mismatch" in pipeline_errors:
        pipeline_status = "schema_mismatch"
    elif source_event_status == "empty" or collector_status == "available":
        pipeline_status = "empty"
    else:
        pipeline_status = "missing"
    pipeline_paths = list(source_paths)
    if collector_status not in {"missing", "unsafe_path"}:
        pipeline_paths.append("last_run.json")
    dataset_envelopes["pipeline_status"] = _source_envelope(
        name="pipeline_status", records=[pipeline_summary] if pipeline_activity else [],
        source_paths=sorted(set(pipeline_paths)),
        source_status=pipeline_status, inventory_item={"quality": {"status": "not_applicable"}},
        generated_at=generated_at, source="last_run.json and sanitized source_runs.csv projection",
        completeness="individual requests do not establish workflow success",
        extra={"summary": pipeline_summary})

    # Enrich NAV snapshots and overview metrics from provenance only on exact record keys.
    provenance_index = {(item.get("dataset"), item.get("fund"), item.get("record_key")): item
                        for item in provenance_records if item.get("dataset") and item.get("fund") and item.get("record_key")}
    for record in dataset_envelopes["nav_history"]["records"]:
        if record["series_type"] == "nav_snapshot":
            evidence = provenance_index.get(("nav_snapshot", record["fund"], record["date"]))
            if evidence:
                record["observed_at"] = evidence.get("observed_at")
                record["published_at"] = evidence.get("published_at")
                record["source_reference"] = evidence.get("source_reference")
                record["verification_status"] = evidence.get("verification_status")
        else:
            record["source_reference"] = None
            record["verification_status"] = "source_publication_unknown"
    for fund in funds_records:
        fund_id = fund["fund_id"]
        snapshots = [row for row in nav_records if row["fund"] == fund_id and row["series_type"] == "nav_snapshot"]
        history = [row for row in nav_records if row["fund"] == fund_id and row["series_type"] == "nav_history"]
        latest_snapshot = max(snapshots, key=lambda row: row["date"], default=None)
        latest_history = max(history, key=lambda row: row["date"], default=None)
        fund["latest_nav_observation_date"] = latest_snapshot["observation_date"] if latest_snapshot else None
        fund["latest_nav_observation"] = latest_snapshot["nav"] if latest_snapshot else None
        fund["latest_nav_source_date"] = latest_history["effective_date"] if latest_history else None
        fund["latest_nav_source_value"] = latest_history["nav"] if latest_history else None

    files = {f"{name}.json": payload for name, payload in dataset_envelopes.items()}
    # Validate/sanitize before any file is written.
    for filename, payload in files.items():
        try:
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Public dashboard dataset {filename} is not strict JSON") from exc
        if _contains_sensitive_key(payload):
            raise ValueError(f"Refusing to publish a sensitive field in {filename}")
    for filename, payload in files.items():
        _write_json(destination / filename, payload)

    manifest_datasets = {}
    for filename, payload in files.items():
        manifest_datasets[payload["dataset"]] = {
            "file": filename,
            "schema_version": payload["schema_version"],
            "status": payload["status"],
            "record_count": payload["record_count"],
            "source": payload["source"],
            "source_paths": payload["source_paths"],
            "source_date_range": payload.get("source_date_range", {}),
            "generated_at": generated_at,
            "completeness": payload["completeness"],
            "validation_status": payload["validation_status"],
            "truncated": payload.get("truncated", False),
        }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "project": "VN_Fund_Analysis",
        "generated_at": generated_at,
        "reference_date": reference.isoformat(),
        "source_coverage": {
            "data_directory_exists": inventory.get("data_directory_exists", False),
            "repository_csv_files": inventory.get("repository_data_files", 0),
            "datasets_expected": inventory.get("summary", {}).get("datasets_expected"),
            "datasets_available": inventory.get("summary", {}).get("datasets_available"),
            "datasets_missing": inventory.get("summary", {}).get("datasets_missing"),
            "persisted_source_events": source_coverage.get("event_count"),
            "source_status": source_coverage.get("status"),
            "note": "Missing is not zero; repository inventory does not establish live provider coverage.",
        },
        "public_data_policy": {
            "raw_payloads_exported": False,
            "credentials_exported": False,
            "full_csv_archive_copied": False,
            "holdings_export": "latest stored snapshot per source scope only",
            "nav_window_years": nav_years,
            "events_window_years": 5,
            "events_max_records": max_events,
            "source_events_max_records": MAX_SOURCE_EVENTS,
            "quality_records_max_records": MAX_QUALITY_RECORDS,
        },
        "datasets": manifest_datasets,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest


def _contains_sensitive_key(value: Any) -> bool:
    prohibited_parts = ("token", "password", "authorization", "cookie", "secret", "apikey",
                        "credential", "privatekey", "rawbody", "payloadjson", "requestbody", "responsebody")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(part in normalized for part in prohibited_parts):
                if normalized == "credentialsexported" and item is False:
                    continue  # Explicit public policy boolean; no credential value is present.
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
