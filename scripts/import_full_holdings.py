#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nhập DANH MỤC ĐẦY ĐỦ từ báo cáo/factsheet chính thức của công ty quản lý quỹ.

Input CSV (sep=';') bắt buộc có fund;ticker;weight;period_end;source_url. `weight` là
phân số 0.0643 hoặc phần trăm 6.43. Optional provenance columns: observed_at,
published_at, observed_date, observed_date_basis. Timestamp phải kèm timezone; không
suy ra thời điểm quan sát/công bố từ period_end, URL hoặc thời điểm chạy script.

Bản materialized hiện hành nằm ở data/holdings_full.csv. Mỗi phiên bản nhập được giữ
append-only trong data/research_observations.csv; nhập lại cùng nội dung là idempotent.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.provenance import ProvenanceStore, observations_from_frame, utc_now  # noqa: E402
from vnfa.quality import require_valid, validate_holdings  # noqa: E402

SEP = ";"
REQUIRED = ["fund", "ticker", "weight", "period_end", "source_url"]
PROVENANCE_FIELDS = {"observed_at", "published_at", "observed_date", "observed_date_basis"}
VALID_OBSERVED_DATE_BASES = {"unknown", "verified_date_only", "source_observed_date", "date_only", "approximate"}


def _normalize_temporal_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate explicit timestamps; never infer them from period_end or file time."""
    frame = frame.copy()
    for column in ("observed_at", "published_at"):
        if column not in frame:
            continue
        normalized = []
        for value in frame[column]:
            if pd.isna(value) or not str(value).strip():
                normalized.append(None)
                continue
            try:
                ts = pd.Timestamp(value)
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"{column} có timestamp không hợp lệ") from exc
            if ts.tzinfo is None:
                raise SystemExit(f"{column} phải có timezone; không tự giả định UTC")
            normalized.append(ts.tz_convert("UTC").isoformat().replace("+00:00", "Z"))
        frame[column] = normalized
    if "observed_date" in frame:
        parsed = pd.to_datetime(frame.observed_date, errors="coerce")
        present = frame.observed_date.notna() & frame.observed_date.astype(str).str.strip().ne("")
        if (present & parsed.isna()).any():
            raise SystemExit("observed_date phải là ngày hợp lệ YYYY-MM-DD")
        frame["observed_date"] = parsed.dt.date.astype("string").where(present, None)
    if "observed_date_basis" in frame:
        basis = frame.observed_date_basis.fillna("unknown").astype(str).str.strip().str.lower()
        if not basis.isin(VALID_OBSERVED_DATE_BASES).all():
            raise SystemExit(f"observed_date_basis phải thuộc: {sorted(VALID_OBSERVED_DATE_BASES)}")
        frame["observed_date_basis"] = basis
    return frame


def _safe_source_url(value: str) -> str:
    parsed = urlparse(str(value))
    # Persist a useful citation but never credentials, query tokens, or fragments.
    host = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))


def load_clean(path: str | Path, weight_unit: str = "auto") -> pd.DataFrame:
    """Read official holdings and normalize weight to fraction, preserving explicit metadata.

    `auto` retains compatibility with historical inputs (a group with max > 1 is %).
    If percent values are all <= 1, the unit is ambiguous and must be specified.
    """
    if weight_unit not in {"auto", "fraction", "percent"}:
        raise SystemExit("weight_unit phải là auto, fraction hoặc percent")
    df = pd.read_csv(path, sep=SEP, dtype=str)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"Thiếu cột: {missing}")
    if df.empty:
        raise SystemExit("File holdings rỗng")

    df["ticker"] = df.ticker.str.strip().str.upper()
    df["fund"] = df.fund.str.strip().str.upper()
    if df[["ticker", "fund"]].isna().any().any() or (df[["ticker", "fund"]] == "").any().any():
        raise SystemExit("fund và ticker bắt buộc, không được để trống")

    dates = pd.to_datetime(df.period_end, dayfirst=False, errors="coerce")
    if dates.isna().any():
        raise SystemExit("period_end phải là ngày hợp lệ YYYY-MM-DD")
    df["period_end"] = dates.dt.date.astype(str)

    df["source_url"] = df.source_url.str.strip()
    if df.source_url.isna().any() or (df.source_url == "").any():
        raise SystemExit("source_url bắt buộc cho mọi dòng")

    def valid_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    if not df.source_url.map(valid_url).all():
        raise SystemExit("source_url phải là URL http(s) hợp lệ")
    # Avoid persisting userinfo, signed query strings, or fragments to a repository CSV.
    df["source_url"] = df.source_url.map(_safe_source_url)

    raw_weight = pd.to_numeric(
        df.weight.str.replace("%", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
    if not np.isfinite(raw_weight.to_numpy(dtype=float)).all() or (raw_weight <= 0).any():
        raise SystemExit("weight phải là số dương, hữu hạn; không chấp nhận missing/0/âm")
    if (raw_weight > 100).any():
        raise SystemExit("weight vượt 100%; kiểm tra lại đơn vị đầu vào")
    df["weight"] = raw_weight.astype(float)

    if df.groupby(["fund", "period_end", "ticker"]).size().max() > 1:
        raise SystemExit("Trùng (fund, period_end, ticker)")
    for _, idx in df.groupby(["fund", "period_end"]).groups.items():
        group = df.loc[idx, "weight"]
        use_percent = weight_unit == "percent" or (weight_unit == "auto" and group.max() > 1.0)
        if weight_unit == "fraction" and group.max() > 1.0:
            raise SystemExit("weight_unit=fraction nhưng có tỷ trọng > 1")
        if use_percent:
            df.loc[idx, "weight"] = group / 100.0

    if (df.weight > 1.02).any():
        raise SystemExit("Tỷ trọng đơn lẻ sau chuẩn hóa vượt 102%")
    totals = df.groupby(["fund", "period_end"]).weight.sum()
    if (totals > 1.02).any():
        raise SystemExit(f"Tổng tỷ trọng > 102% ở: {totals[totals > 1.02].to_dict()}")
    return _normalize_temporal_columns(df)


def _quality_entry(report, fund: str, run_id: str) -> dict:
    item = report.to_dict()
    findings = item.pop("errors") + item.pop("warnings")
    return {**item, "fund": fund, "findings": findings, "source": "official_import", "run_id": run_id}


def _observations_for_import(frame: pd.DataFrame, *, run_id: str, date_basis: str,
                             quality_reports: list[dict]) -> list[dict]:
    observations = []
    for (fund, period_end, source_url), group in frame.groupby(
            ["fund", "period_end", "source_url"], dropna=False, sort=True):
        quality = validate_holdings(group, coverage_type="full")
        require_valid(quality)
        quality_reports.append(_quality_entry(quality, str(fund), run_id))

        payload = group.drop(columns=["source_url"], errors="ignore").copy()
        if "observed_date_basis" not in payload.columns:
            payload["observed_date_basis"] = date_basis
        elif payload.observed_date_basis.isna().any():
            payload["observed_date_basis"] = payload.observed_date_basis.fillna(date_basis)
        if "observed_date" in payload.columns and "observed_date_basis" not in group.columns:
            payload["observed_date_basis"] = date_basis
        if "observed_date" in payload.columns and "observed_date_basis" in payload.columns:
            unknown = payload.observed_date.notna() & payload.observed_date_basis.eq("")
            payload.loc[unknown, "observed_date_basis"] = date_basis

        observations.extend(observations_from_frame(
            payload, dataset="full_holdings", fund=str(fund), source="official_manager_report",
            source_reference=_safe_source_url(source_url), observed_at=None,
            key_columns=("ticker",), effective_date_column="period_end", run_id=run_id,
            data_quality_status=quality.status,
            verification_status="source_cited_not_independently_verified",
            quality_flags=[finding.code for finding in quality.findings], coverage_type="source_claimed_full",
            observed_at_column="observed_at" if "observed_at" in payload.columns else None,
            published_at_column="published_at" if "published_at" in payload.columns else None,
            observed_date_column="observed_date" if "observed_date" in payload.columns else None,
            observed_date_basis_column="observed_date_basis" if "observed_date_basis" in payload.columns else None,
        ))
    return observations


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--weight-unit", choices=["auto", "fraction", "percent"], default="auto",
                    help="weight unit; use percent if all percentage values are <= 1")
    ap.add_argument("--observed-date-basis", choices=sorted(VALID_OBSERVED_DATE_BASES), default="unknown",
                    help="basis for date-only observed_date; approximate dates are excluded from PIT by default")
    a = ap.parse_args()

    new = load_clean(a.csv, weight_unit=a.weight_unit)
    out = Path(a.data_dir) / "holdings_full.csv"
    quality_reports: list[dict] = []
    run_id = uuid.uuid4().hex

    old = pd.DataFrame()
    if out.exists():
        old = pd.read_csv(out, sep=SEP, dtype=str)
        missing_old = [column for column in REQUIRED if column not in old.columns]
        if missing_old:
            raise SystemExit(f"holdings_full.csv thiếu cột bắt buộc {missing_old}; không ghi đè")

    keep_provenance_in_legacy = PROVENANCE_FIELDS.intersection(old.columns) if not old.empty else set()
    legacy_new = new.drop(columns=sorted(PROVENANCE_FIELDS - keep_provenance_in_legacy), errors="ignore")
    if not old.empty:
        added = sorted(set(legacy_new.columns) - set(old.columns))
        if added:
            raise SystemExit(f"Schema holdings_full.csv khác (cột mới {added}); không ghi đè âm thầm")
        legacy_new = legacy_new.reindex(columns=old.columns)
        materialized = pd.concat([old, legacy_new], ignore_index=True)
        materialized = materialized.drop_duplicates(["fund", "period_end", "ticker"], keep="last")
    else:
        materialized = legacy_new.copy()

    # Sidecar is appended before replacing the materialized latest view: old revisions
    # remain recoverable even if the final CSV write fails. Missing timestamps stay blank.
    sidecar = ProvenanceStore(a.data_dir)
    observations = []
    if not old.empty:
        old_for_archive = old.copy()
        if "weight" in old_for_archive:
            old_for_archive["weight"] = pd.to_numeric(old_for_archive.weight, errors="coerce")
        if "observed_date_basis" not in old_for_archive:
            old_for_archive["observed_date_basis"] = "unknown"
        old_for_archive = _normalize_temporal_columns(old_for_archive)
        observations.extend(_observations_for_import(
            old_for_archive, run_id=run_id, date_basis="unknown", quality_reports=quality_reports))
    observations.extend(_observations_for_import(
        new, run_id=run_id, date_basis=a.observed_date_basis, quality_reports=quality_reports))
    sidecar.append_observations(observations, run_id=run_id)
    sidecar.append_quality_reports(quality_reports, run_id=run_id, source="official_import", recorded_at=utc_now())

    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    try:
        materialized.sort_values(["fund", "period_end", "weight"],
                                 ascending=[True, True, False]).to_csv(temporary, sep=SEP, index=False)
        temporary.replace(out)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Đã ghi {len(materialized)} dòng, {materialized.fund.nunique()} quỹ -> {out}")
    print(f"Provenance observations: {len(observations)} rows considered (append is idempotent)")


if __name__ == "__main__":
    main()
