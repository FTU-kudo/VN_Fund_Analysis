# -*- coding: utf-8 -*-
"""Reusable, source-aware data validation. Missing values are never coerced to zero."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class QualityFinding:
    code: str
    severity: str  # "error" or "warning"
    message: str
    count: int = 0


@dataclass
class QualityReport:
    dataset: str
    status: str
    row_count: int
    findings: list[QualityFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[QualityFinding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[QualityFinding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "status": self.status,
            "row_count": self.row_count,
            "errors": [asdict(f) for f in self.errors],
            "warnings": [asdict(f) for f in self.warnings],
            "metrics": self.metrics,
        }


class DataQualityError(ValueError):
    """A hard validation failure; the source record must not be persisted as valid."""

    def __init__(self, report: QualityReport):
        self.report = report
        summary = "; ".join(f"{f.code}: {f.message}" for f in report.errors)
        super().__init__(f"{report.dataset} validation failed: {summary}")


def _report(dataset: str, frame: pd.DataFrame | None) -> tuple[QualityReport, list[QualityFinding]]:
    if frame is None:
        finding = QualityFinding("dataset_missing", "warning", "Dataset is not available")
        return QualityReport(dataset, "unavailable", 0, [finding]), [finding]
    if not isinstance(frame, pd.DataFrame):
        finding = QualityFinding("invalid_container", "error", "Expected a pandas DataFrame")
        return QualityReport(dataset, "invalid", 0, [finding]), [finding]
    if frame.empty:
        finding = QualityFinding("empty_dataset", "warning", "No observations were returned")
        return QualityReport(dataset, "unavailable", 0, [finding]), [finding]
    return QualityReport(dataset, "valid", len(frame)), []


def _finish(report: QualityReport, findings: list[QualityFinding]) -> QualityReport:
    report.findings = findings
    if any(f.severity == "error" for f in findings):
        report.status = "invalid"
    elif findings:
        report.status = "partial"
    return report


def _missing_columns(frame: pd.DataFrame, required: tuple[str, ...], findings: list[QualityFinding], dataset: str) -> bool:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        findings.append(QualityFinding(
            "schema_missing_columns", "error", f"Missing required columns: {', '.join(missing)}", len(missing)))
        return True
    return False


def _bad_dates(series: pd.Series) -> int:
    present = series.notna() & series.astype(str).str.strip().ne("")
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return int((present & parsed.isna()).sum())


def validate_fund_universe(frame: pd.DataFrame | None) -> QualityReport:
    report, findings = _report("fund_universe", frame)
    if frame is None or frame.empty or report.status == "invalid":
        return report
    if _missing_columns(frame, ("code",), findings, report.dataset):
        return _finish(report, findings)
    code = frame.code.astype("string").str.strip()
    missing = int(code.isna().sum() + code.eq("").fillna(False).sum())
    if missing:
        findings.append(QualityFinding("missing_fund_id", "error", "Fund code is required", missing))
    present_codes = code[code.notna() & code.ne("").fillna(False)]
    duplicated = int(present_codes.duplicated(keep=False).sum())
    if duplicated:
        findings.append(QualityFinding("duplicate_fund_id", "error", "Fund codes must be unique in the universe", duplicated))
    for column in ("mgmt_co", "asset_code"):
        if column not in frame.columns:
            findings.append(QualityFinding("schema_missing_optional", "warning", f"{column} is not available", len(frame)))
        else:
            missing_values = int(frame[column].isna().sum() + frame[column].astype(str).str.strip().eq("").sum())
            if missing_values:
                findings.append(QualityFinding(f"missing_{column}", "warning", f"{column} is missing", missing_values))
    report.metrics["fund_count"] = int(code.nunique(dropna=True))
    return _finish(report, findings)


def validate_holdings(frame: pd.DataFrame | None, coverage_type: str = "top_n") -> QualityReport:
    report, findings = _report("fund_holdings", frame)
    if frame is None or frame.empty or report.status == "invalid":
        return report
    if _missing_columns(frame, ("ticker", "weight"), findings, report.dataset):
        return _finish(report, findings)

    ticker = frame.ticker.astype("string").str.strip().str.upper()
    missing_ticker = int((ticker.isna() | ticker.eq("")).sum())
    if missing_ticker:
        findings.append(QualityFinding("missing_ticker", "error", "Holding ticker is required", missing_ticker))
    duplicated = int(ticker.duplicated(keep=False).sum())
    if duplicated:
        findings.append(QualityFinding("duplicate_ticker", "error", "A snapshot must contain at most one row per ticker", duplicated))

    weight = pd.to_numeric(frame.weight, errors="coerce")
    invalid_weight = int((~np.isfinite(weight.to_numpy(dtype=float, na_value=np.nan))).sum())
    if invalid_weight:
        findings.append(QualityFinding("invalid_weight", "error", "Weight must be numeric and finite", invalid_weight))
    negative_weight = int((weight < 0).sum())
    if negative_weight:
        findings.append(QualityFinding("negative_weight", "error", "Negative portfolio weights are not supported by this schema", negative_weight))
    unusually_large = int((weight > 1.02).sum())
    if unusually_large:
        findings.append(QualityFinding("unusual_weight", "warning", "Weight exceeds 102%; verify unit, leverage, and source", unusually_large))

    metrics: dict[str, Any] = {
        "ticker_count": int(ticker.nunique(dropna=True)),
        "weight_coverage": float(weight.notna().mean()),
        "coverage_type": coverage_type,
    }
    for column in ("volume", "price_k", "asset_value"):
        if column not in frame.columns:
            findings.append(QualityFinding(f"missing_column_{column}", "warning", f"{column} is not available", len(frame)))
            metrics[f"{column}_coverage"] = 0.0
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        present = frame[column].notna() & frame[column].astype(str).str.strip().ne("")
        invalid = int((present & values.isna()).sum())
        if invalid:
            findings.append(QualityFinding(f"invalid_{column}", "error", f"{column} contains non-numeric values", invalid))
        negative = int((values < 0).sum())
        if negative:
            findings.append(QualityFinding(f"negative_{column}", "error", f"{column} cannot be negative", negative))
        missing = int(values.isna().sum())
        if missing:
            findings.append(QualityFinding(f"missing_{column}", "warning", f"{column} is missing; it is not treated as zero", missing))
        metrics[f"{column}_coverage"] = float(values.notna().mean())

    if "update_date" in frame.columns:
        bad = _bad_dates(frame.update_date)
        if bad:
            findings.append(QualityFinding("invalid_update_date", "warning", "Some source update dates are invalid", bad))
        missing_dates = int(frame.update_date.isna().sum() + frame.update_date.astype(str).str.strip().eq("").sum())
        if missing_dates:
            findings.append(QualityFinding("missing_update_date", "warning", "Some source effective/reporting dates are unknown", missing_dates))
        metrics["effective_date_coverage"] = float(frame.update_date.notna().mean())
    else:
        findings.append(QualityFinding("missing_update_date", "warning", "Source effective/reporting date is unavailable", len(frame)))
        metrics["effective_date_coverage"] = 0.0
    if coverage_type in {"top_n", "top10", "top_10"}:
        findings.append(QualityFinding("censored_top_n", "warning", "Top-N disclosure is not a complete fund portfolio", len(frame)))
    report.metrics.update(metrics)
    return _finish(report, findings)


def validate_bond_holdings(frame: pd.DataFrame | None, coverage_type: str = "top_n") -> QualityReport:
    """Validate bond holdings with the holdings rules but retain bond-specific identity."""
    report = validate_holdings(frame, coverage_type=coverage_type)
    report.dataset = "fund_bond_holdings"
    return report


def validate_nav(frame: pd.DataFrame | None, key_columns: tuple[str, ...] = ("fund", "date")) -> QualityReport:
    report, findings = _report("fund_nav", frame)
    if frame is None or frame.empty or report.status == "invalid":
        return report
    required = ("date", "nav") + (("fund",) if "fund" in key_columns else ())
    if _missing_columns(frame, required, findings, report.dataset):
        return _finish(report, findings)
    bad_dates = _bad_dates(frame.date)
    if bad_dates:
        findings.append(QualityFinding("invalid_nav_date", "error", "NAV dates must be valid", bad_dates))
    nav = pd.to_numeric(frame.nav, errors="coerce")
    invalid = int((~np.isfinite(nav.to_numpy(dtype=float))).sum())
    if invalid:
        findings.append(QualityFinding("invalid_nav", "error", "NAV must be numeric and finite", invalid))
    nonpositive = int((nav <= 0).sum())
    if nonpositive:
        findings.append(QualityFinding("nonpositive_nav", "error", "NAV must be positive", nonpositive))
    if all(c in frame.columns for c in key_columns):
        duplicates = int(frame.duplicated(list(key_columns), keep=False).sum())
        if duplicates:
            findings.append(QualityFinding("duplicate_nav_key", "error", "Materialized NAV history has duplicate keys", duplicates))
    report.metrics.update({"fund_count": int(frame.fund.nunique()) if "fund" in frame else None,
                           "first_date": str(pd.to_datetime(frame.date, errors="coerce").min().date()) if frame.date.notna().any() else None,
                           "last_date": str(pd.to_datetime(frame.date, errors="coerce").max().date()) if frame.date.notna().any() else None})
    return _finish(report, findings)


def validate_nav_snapshot(frame: pd.DataFrame | None) -> QualityReport:
    """Validate observed NAV snapshots and explicitly retain unknown effective dates."""
    report = validate_nav(frame)
    report.dataset = "nav_snapshots"
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        report.metrics["effective_date_status"] = "unknown"
        report.metrics["effective_date_unknown_rows"] = len(frame)
        report.findings.append(QualityFinding(
            "effective_date_unknown", "warning",
            "Snapshot dates are observation dates; NAV effective dates are not established by this file.",
            len(frame),
        ))
    return report


def validate_allocation(frame: pd.DataFrame | None) -> QualityReport:
    report, findings = _report("fund_allocation", frame)
    if frame is None or frame.empty or report.status == "invalid":
        return report
    if _missing_columns(frame, ("asset_code", "pct"), findings, report.dataset):
        return _finish(report, findings)
    asset_code = frame.asset_code.astype("string").str.strip()
    missing_asset = int((asset_code.isna() | asset_code.eq("").fillna(False)).sum())
    if missing_asset:
        findings.append(QualityFinding("missing_asset_code", "error", "Allocation asset_code is required", missing_asset))
    pct = pd.to_numeric(frame.pct, errors="coerce")
    invalid = int((~np.isfinite(pct.to_numpy(dtype=float, na_value=np.nan))).sum())
    if invalid:
        findings.append(QualityFinding("invalid_allocation_pct", "error", "Allocation percentage must be numeric and finite", invalid))
    negative = int((pct < 0).sum())
    if negative:
        findings.append(QualityFinding("negative_allocation_pct", "error", "Allocation percentage cannot be negative", negative))
    large = int((pct > 1.02).sum())
    if large:
        findings.append(QualityFinding("unusual_allocation_pct", "warning", "Allocation percentage exceeds 102%; verify source units", large))
    if "report_date" not in frame.columns:
        findings.append(QualityFinding("missing_report_date", "warning", "Effective/reporting date is unavailable", len(frame)))
        report.metrics["effective_date_coverage"] = 0.0
    else:
        bad_dates = _bad_dates(frame.report_date)
        if bad_dates:
            findings.append(QualityFinding("invalid_report_date", "warning", "Some report dates are invalid", bad_dates))
        missing_dates = int(frame.report_date.isna().sum() + frame.report_date.astype(str).str.strip().eq("").sum())
        if missing_dates:
            findings.append(QualityFinding("missing_report_date", "warning", "Some report dates are unknown", missing_dates))
        report.metrics["effective_date_coverage"] = float(frame.report_date.notna().mean())
    report.metrics.update({"asset_types": int(frame.asset_code.nunique(dropna=True)),
                           "allocation_coverage": float(pct.notna().mean())})
    return _finish(report, findings)


def require_valid(report: QualityReport) -> QualityReport:
    if report.errors:
        raise DataQualityError(report)
    return report
