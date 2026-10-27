# -*- coding: utf-8 -*-
"""Reproducible inventory and coverage summary for data actually present on disk."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .config import load_config
from .provenance import OBSERVATION_COLUMNS, QUALITY_COLUMNS, SOURCE_COLUMNS_V1
from .quality import (STRUCTURAL_WARNING_CODES, QualityReport, validate_allocation,
                      validate_bond_holdings, validate_fund_universe, validate_holdings,
                      validate_nav, validate_nav_snapshot)
from .store import read_csv_safe

VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")
REPO_ROOT = Path(__file__).resolve().parents[1]

# Only known repository data products are enumerated. A missing path is reported as
# missing; no expected rows, fund counts, dates, or source responses are synthesized.
DATASETS = {
    "fund_metadata": {"patterns": ["funds.csv"], "required": ["code"]},
    "fund_holdings": {"patterns": ["holdings/*.csv"], "required": ["observed_date", "fund", "ticker", "weight"]},
    "fund_bond_holdings": {"patterns": ["bonds/*.csv"], "required": ["observed_date", "fund", "ticker", "weight"]},
    "full_holdings": {"patterns": ["holdings_full.csv"], "required": ["fund", "ticker", "weight", "period_end", "source_url"]},
    "changes": {"patterns": ["changes.csv"], "required": ["observed_date", "fund", "ticker", "action"]},
    "nav_history": {"patterns": ["nav.csv"], "required": ["fund", "date", "nav"]},
    "nav_snapshots": {"patterns": ["nav_snapshots.csv"], "required": ["fund", "date", "nav"]},
    "allocation": {"patterns": ["allocation.csv"], "required": ["fund", "asset_code", "pct"]},
    "industry": {"patterns": ["industry.csv"], "required": ["fund", "industry", "pct"]},
    "fees": {"patterns": ["fees.csv"], "required": ["fund"]},
    "profile": {"patterns": ["profile.csv"], "required": ["fund"]},
    "performance": {"patterns": ["performance.csv"], "required": ["fund"]},
    "source_events": {"patterns": ["source_runs.csv"], "required": SOURCE_COLUMNS_V1},
    "research_observations": {"patterns": ["research_observations.csv"], "required": OBSERVATION_COLUMNS},
    "quality_reports": {"patterns": ["quality_reports.csv"], "required": QUALITY_COLUMNS},
}

DATE_COLUMNS = {
    "fund_metadata": ("last_seen",),
    "fund_holdings": ("observed_date", "update_date"),
    "fund_bond_holdings": ("observed_date", "update_date"),
    "full_holdings": ("observed_date", "period_end"),
    "changes": ("observed_date",),
    "nav_history": ("date",),
    "nav_snapshots": ("date",),
    "allocation": ("observed_date", "report_date"),
    "industry": ("observed_date",),
    "fees": ("observed_date",),
    "profile": ("observed_date",),
    "research_observations": ("observed_at", "published_at", "ingested_at", "effective_date", "observed_date"),
}

FRESHNESS_PRIMARY_COLUMN = {
    "fund_holdings": "update_date",
    "fund_bond_holdings": "update_date",
    "full_holdings": "period_end",
    "allocation": "report_date",
    "nav_snapshots": "date",
    "nav_history": "date",
    "fees": "observed_date",
    "industry": "observed_date",
    "profile": "observed_date",
}


def _resolve_freshness_sla(
    root: Path,
    *,
    config_path: str | Path | None = None,
    freshness_sla: dict[str, int] | None = None,
) -> tuple[bool, dict[str, int]]:
    if freshness_sla is not None:
        return True, {str(k): int(v) for k, v in freshness_sla.items() if isinstance(v, (int, float)) and int(v) >= 0}
    resolved_cfg_path: Path | None = None
    if config_path is not None:
        resolved_cfg_path = Path(config_path)
    else:
        try:
            if root.resolve() == (REPO_ROOT / "data").resolve() and (REPO_ROOT / "config.toml").exists():
                resolved_cfg_path = REPO_ROOT / "config.toml"
            elif root.parent != REPO_ROOT and (root.parent / "config.toml").exists():
                resolved_cfg_path = root.parent / "config.toml"
        except OSError:
            resolved_cfg_path = None
    cfg = load_config(resolved_cfg_path)
    section = cfg.get("freshness") or {}
    if not bool(section.get("enabled")):
        return False, {}
    thresholds = {
        str(k): int(v)
        for k, v in section.items()
        if k != "enabled" and isinstance(v, (int, float)) and not isinstance(v, bool) and int(v) >= 0
    }
    return bool(thresholds), thresholds


def _assess_freshness(
    dataset_name: str,
    status: str,
    date_coverage: dict[str, Any],
    *,
    sla_enabled: bool,
    sla_thresholds: dict[str, int],
) -> dict[str, Any]:
    primary_col = FRESHNESS_PRIMARY_COLUMN.get(dataset_name, "observed_date")
    age_days = None
    basis_col = None
    for candidate in (primary_col, "observed_date", "date"):
        info = date_coverage.get(candidate)
        if info and info.get("age_days_reference_date") is not None:
            age_days = int(info["age_days_reference_date"])
            basis_col = candidate
            break
    threshold = sla_thresholds.get(dataset_name) if sla_enabled else None
    if not sla_enabled or threshold is None:
        return {
            "assessment": "not_assessed_no_sla",
            "sla_days": None,
            "age_days": age_days,
            "date_basis": basis_col,
        }
    if status == "missing":
        assessment = "unavailable_missing"
    elif status == "empty":
        assessment = "empty_dataset"
    elif status in {"unreadable", "schema_mismatch"}:
        assessment = "schema_mismatch"
    elif age_days is None:
        assessment = "unknown_no_dates"
    elif age_days > threshold:
        assessment = "stale"
    else:
        assessment = "within_sla"
    return {
        "assessment": assessment,
        "sla_days": threshold,
        "age_days": age_days,
        "date_basis": basis_col,
    }


def _combine_quality(dataset: str, reports: list[QualityReport], row_count: int) -> dict[str, Any]:
    if not reports:
        return {"dataset": dataset, "status": "not_applicable", "row_count": row_count,
                "errors": [], "warnings": [], "structural_warnings": [], "anomaly_warnings": [],
                "metrics": {}}
    findings: dict[tuple[str, str, str], int] = Counter()
    for report in reports:
        for finding in report.findings:
            findings[(finding.code, finding.severity, finding.message)] += finding.count
    errors = [{"code": code, "severity": severity, "message": message, "count": count}
              for (code, severity, message), count in findings.items() if severity == "error"]
    warnings = [{"code": code, "severity": severity, "message": message, "count": count}
                for (code, severity, message), count in findings.items() if severity == "warning"]
    structural_limitations = [f for f in warnings if f["code"] in STRUCTURAL_WARNING_CODES]
    anomaly_warnings = [f for f in warnings if f["code"] not in STRUCTURAL_WARNING_CODES]
    non_snapshot_warnings = [finding for finding in warnings if finding["code"] != "effective_date_unknown"]
    if errors:
        status = "invalid"
    elif any(report.status in {"partial", "unavailable"} for report in reports) or non_snapshot_warnings:
        status = "partial"
    else:
        status = "valid"
    return {"dataset": dataset, "status": status, "row_count": row_count,
            "errors": errors, "warnings": warnings,
            "structural_warnings": structural_limitations,
            "anomaly_warnings": anomaly_warnings,
            "metrics": {"validation_groups": len(reports)}}


def _validation_reports(name: str, frames: list[tuple[Path, pd.DataFrame]]) -> list[QualityReport]:
    reports = []
    for _, frame in frames:
        if name == "fund_metadata":
            reports.append(validate_fund_universe(frame))
        elif name in {"fund_holdings", "fund_bond_holdings"}:
            group_cols = [column for column in ("observed_date", "content_hash") if column in frame.columns]
            blocks = [part for _, part in frame.groupby(group_cols, dropna=False, sort=False)] if group_cols else [frame]
            validator = validate_bond_holdings if name == "fund_bond_holdings" else validate_holdings
            reports.extend(validator(block, coverage_type="top_n") for block in blocks)
        elif name == "full_holdings":
            group_cols = [column for column in ("fund", "period_end") if column in frame.columns]
            blocks = [part for _, part in frame.groupby(group_cols, dropna=False, sort=False)] if group_cols else [frame]
            reports.extend(validate_holdings(block, coverage_type="full") for block in blocks)
        elif name in {"nav_history", "nav_snapshots"}:
            validator = validate_nav_snapshot if name == "nav_snapshots" else validate_nav
            reports.append(validator(frame))
        elif name == "allocation":
            reports.append(validate_allocation(frame))
    return reports


def _read_csv(path: Path) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    try:
        frame = read_csv_safe(path, sep=";", dtype=str)
        return frame, {"status": "read", "row_count": len(frame), "columns": list(frame.columns),
                       "bytes": path.stat().st_size}
    except Exception as exc:
        return None, {"status": "unreadable", "row_count": None, "columns": [],
                      "bytes": path.stat().st_size if path.exists() else None,
                      "error_type": type(exc).__name__}


def _date_summary(name: str, frames: list[pd.DataFrame], reference: date) -> dict[str, Any]:
    result = {}
    for column in DATE_COLUMNS.get(name, ()):
        observed, total, future = [], 0, 0
        for frame in frames:
            if column not in frame.columns:
                continue
            total += len(frame)
            values = pd.to_datetime(frame[column], errors="coerce", utc=True).dropna()
            observed.extend(values.tolist())
            local_dates = values.dt.tz_convert(VIETNAM).dt.date
            future += sum(item > reference for item in local_dates)
        first, last = (min(observed), max(observed)) if observed else (None, None)
        last_local_date = last.astimezone(VIETNAM).date() if last is not None else None
        result[column] = {
            "coverage_count": len(observed),
            "row_count_with_column": total,
            "coverage": round(len(observed) / total, 6) if total else 0.0,
            "first": first.isoformat() if first is not None else None,
            "last": last.isoformat() if last is not None else None,
            "age_days_reference_date": (reference - last_local_date).days if last_local_date else None,
            "future_date_count": future,
        }
    return result


def _dataset_paths(root: Path, patterns: list[str]) -> list[Path]:
    paths = []
    for pattern in patterns:
        paths.extend(path for path in root.glob(pattern) if path.is_file())
    return sorted(set(paths))


def build_data_inventory(
    data_dir: str | Path,
    *,
    reference_date: str | date | None = None,
    config_path: str | Path | None = None,
    freshness_sla: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build an inventory from present files only; `reference_date` controls age reporting."""
    root = Path(data_dir)
    if reference_date is None:
        reference = datetime.now(VIETNAM).date()
    elif isinstance(reference_date, datetime):
        reference = reference_date.date()
    elif isinstance(reference_date, date):
        reference = reference_date
    else:
        reference = pd.Timestamp(reference_date).date()
    sla_enabled, sla_thresholds = _resolve_freshness_sla(
        root, config_path=config_path, freshness_sla=freshness_sla
    )

    datasets: dict[str, Any] = {}
    raw_frames: dict[str, list[tuple[Path, pd.DataFrame]]] = {}
    all_fund_values: set[str] = set()
    for name, spec in DATASETS.items():
        paths = _dataset_paths(root, spec["patterns"]) if root.exists() else []
        entries, usable_frames = [], []
        for path in paths:
            frame, details = _read_csv(path)
            details["path"] = str(path.relative_to(root))
            if frame is not None:
                usable_frames.append((path, frame))
                missing = [column for column in spec["required"] if column not in frame.columns]
                details["missing_required_columns"] = missing
                details["schema_status"] = "mismatch" if missing else "ok"
            entries.append(details)
        raw_frames[name] = usable_frames
        row_count = sum(len(frame) for _, frame in usable_frames)
        files = [path for path, _ in usable_frames]
        frames = [frame for _, frame in usable_frames]
        columns = sorted({column for frame in frames for column in frame.columns})
        fund_values = set()
        if name == "fund_metadata":
            for frame in frames:
                if "code" in frame.columns:
                    fund_values.update(str(value) for value in frame.code.dropna().unique())
        else:
            for path, frame in usable_frames:
                if "fund" in frame.columns:
                    fund_values.update(str(value) for value in frame.fund.dropna().unique())
                elif name in {"fund_holdings", "fund_bond_holdings"}:
                    fund_values.add(path.stem)
        all_fund_values.update(fund_values)
        missing_schema = sorted({column for item in entries for column in item.get("missing_required_columns", [])})
        signatures = sorted({tuple(item.get("columns", [])) for item in entries if item["status"] == "read"})
        validation = _combine_quality(name, _validation_reports(name, usable_frames), row_count)
        if not paths:
            status = "missing"
        elif any(item["status"] == "unreadable" for item in entries):
            status = "unreadable"
        elif missing_schema or len(signatures) > 1:
            status = "schema_mismatch"
        elif row_count == 0:
            status = "empty"
        else:
            status = "available"
        date_cov = _date_summary(name, frames, reference)
        freshness_info = _assess_freshness(
            name, status, date_cov, sla_enabled=sla_enabled, sla_thresholds=sla_thresholds
        )
        datasets[name] = {
            "status": status,
            "paths": [str(path.relative_to(root)) for path in paths],
            "file_count": len(paths),
            "row_count": row_count,
            "file_bytes": sum(item.get("bytes") or 0 for item in entries),
            "columns": columns,
            "schema_consistent": len(signatures) <= 1,
            "missing_required_columns": missing_schema,
            "fund_count": len(fund_values),
            "date_coverage": date_cov,
            "freshness": freshness_info,
            "quality": validation,
            "files": entries,
        }
        if name == "fund_holdings" and row_count:
            datasets[name]["coverage"] = {
                "portfolio_coverage": "top_n_partial_censored",
                "note": "Top holdings are not the complete portfolio; excluded/lower-ranked securities are unknown.",
            }
        elif name == "full_holdings" and row_count:
            datasets[name]["coverage"] = {"portfolio_coverage": "source_claimed_full", "verified_complete": False}

    observation_frames = [frame for _, frame in raw_frames["research_observations"]]
    if observation_frames:
        observations = pd.concat(observation_frames, ignore_index=True)
        coverage = {}
        for column in ("effective_date", "observed_at", "published_at", "ingested_at", "observed_date"):
            coverage[column] = round(float(observations[column].notna().mean()), 6) if column in observations else 0.0
        datasets["research_observations"]["provenance_coverage"] = coverage
        datasets["research_observations"]["by_dataset"] = (
            observations.dataset.fillna("unknown").value_counts().to_dict() if "dataset" in observations else {})
        datasets["research_observations"]["availability_basis"] = (
            observations.observed_date_basis.fillna("unknown").value_counts().to_dict()
            if "observed_date_basis" in observations else {"legacy_unclassified": len(observations)})
        datasets["research_observations"]["verification_status_counts"] = (
            observations.verification_status.fillna("unknown").value_counts().to_dict()
            if "verification_status" in observations else {"legacy_unclassified": len(observations)})
        datasets["research_observations"]["data_quality_status_counts"] = (
            observations.data_quality_status.fillna("unknown").value_counts().to_dict()
            if "data_quality_status" in observations else {"legacy_unclassified": len(observations)})

    quality_frames = [frame for _, frame in raw_frames["quality_reports"]]
    if quality_frames:
        quality_rows = pd.concat(quality_frames, ignore_index=True)
        finding_counts: Counter[str] = Counter()
        severity_counts: Counter[str] = Counter()
        for raw in quality_rows.get("findings_json", pd.Series(dtype=str)).dropna():
            try:
                findings = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            for finding in findings if isinstance(findings, list) else []:
                finding_counts[str(finding.get("code", "unknown"))] += int(finding.get("count", 0) or 0)
                severity_counts[str(finding.get("severity", "unknown"))] += int(finding.get("count", 0) or 0)
        datasets["quality_reports"]["status_counts"] = (
            quality_rows.status.fillna("unknown").value_counts().to_dict() if "status" in quality_rows else {})
        datasets["quality_reports"]["finding_counts"] = dict(finding_counts)
        datasets["quality_reports"]["severity_counts"] = dict(severity_counts)

    source_frames = [frame for _, frame in raw_frames["source_events"]]
    if source_frames:
        source_events = pd.concat(source_frames, ignore_index=True)
        source_summary = {
            "status": "recorded",
            "event_count": len(source_events),
            "providers": source_events.provider.fillna("unknown").value_counts().to_dict()
                if "provider" in source_events else {},
            "endpoints": source_events.endpoint.fillna("unknown").value_counts().to_dict()
                if "endpoint" in source_events else {},
            "outcomes": source_events.status.fillna("unknown").value_counts().to_dict()
                if "status" in source_events else {},
            "last_completed_at": max(source_events.completed_at.dropna()).__str__()
                if "completed_at" in source_events and source_events.completed_at.notna().any() else None,
            "http_status_counts": source_events.http_status.fillna("unknown").value_counts().to_dict()
                if "http_status" in source_events else {},
            "fund_code_recorded_count": int(
                (source_events.fund_code.notna() & source_events.fund_code.astype(str).str.strip().ne("")).sum()
            ) if "fund_code" in source_events else 0,
        }
    else:
        source_summary = {"status": "not_recorded", "event_count": 0,
                          "note": "No persisted source diagnostics/requests were found; live connectivity is unverified."}

    all_csv = sorted(root.rglob("*.csv")) if root.exists() else []
    known = {str(path.relative_to(root)) for spec in DATASETS.values()
             for pattern in spec["patterns"] for path in _dataset_paths(root, [pattern])}
    unclassified = [str(path.relative_to(root)) for path in all_csv if str(path.relative_to(root)) not in known]
    missing_count = sum(item["status"] == "missing" for item in datasets.values())
    issue_count = sum(item["status"] in {"unreadable", "schema_mismatch"} for item in datasets.values())
    available_count = sum(item["status"] == "available" for item in datasets.values())

    priorities = []
    if not root.exists() or not all_csv:
        priorities.append({"priority": "P0", "action": "Create/restore the data archive and run a small approved collection; inventory found no existing CSV observations."})
    if datasets["source_events"]["status"] == "missing":
        priorities.append({"priority": "P1", "action": "Run the classified source diagnostic from an environment allowed to reach Fmarket; check terms/rate limits separately."})
    if datasets["research_observations"]["status"] == "missing":
        priorities.append({"priority": "P1", "action": "Start collecting exact observed_at/ingested_at and leave unknown published_at blank; exclude timestamp-unknown records from PIT analyses."})
    if issue_count:
        priorities.append({"priority": "P1", "action": "Resolve CSV parsing/schema mismatches explicitly before using affected datasets."})
    if missing_count and all_csv:
        priorities.append({"priority": "P2", "action": "Populate only datasets needed for research; preserve missingness and report per-source coverage rather than filling gaps."})
    priorities.extend([
        {"priority": "P2", "action": "Accumulate verified full holdings, fund AUM/flows, official publication times, NAV history, and benchmark/fee data before any ranking or backtest."},
        {"priority": "P3", "action": "Do not interpret inferred holdings changes as trades or assert investment alpha; validate only after a point-in-time dataset is sufficiently covered."},
    ])
    try:
        data_path_label = root.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        data_path_label = str(root)

    return {
        "inventory_version": "1",
        "generated_at": datetime.now(VIETNAM).isoformat(timespec="seconds"),
        "reference_date": reference.isoformat(),
        "data_directory": data_path_label,
        "data_directory_exists": root.exists(),
        "repository_data_files": len(all_csv),
        "summary": {
            "datasets_expected": len(datasets),
            "datasets_available": available_count,
            "datasets_missing": missing_count,
            "datasets_stale": sum(item.get("freshness", {}).get("assessment") == "stale" for item in datasets.values()),
            "datasets_with_schema_or_read_issues": issue_count,
            "csv_rows_observed": sum(item["row_count"] for item in datasets.values()),
            "funds_observed_across_tables": len(all_fund_values),
            "unclassified_csv_files": unclassified,
        },
        "freshness_policy": {
            "enabled": sla_enabled,
            "thresholds_days": sla_thresholds,
        },
        "source_coverage": source_summary,
        "limitations": [
            "Inventory describes files present at generation time; it does not represent current live Fmarket coverage.",
            "Missing datasets and unknown values are not interpreted as zero or as proof that a source has no records.",
            "Top-N holdings are censored. Date-only and approximate backfill observations have lower or no PIT eligibility.",
            "Source terms, published-time semantics, and live schema require independent verification.",
        ],
        "prioritized_next_steps": priorities,
        "datasets": datasets,
    }


def render_inventory_markdown(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# Data inventory and coverage report",
        "",
        f"- Generated: `{inventory['generated_at']}` (Asia/Ho_Chi_Minh)",
        f"- Reference date: `{inventory['reference_date']}`",
        f"- Data directory exists: **{inventory['data_directory_exists']}** (`{inventory['data_directory']}`)",
        f"- CSV files found: **{inventory['repository_data_files']}**",
        f"- Expected datasets: **{summary['datasets_expected']}**; available **{summary['datasets_available']}**; missing **{summary['datasets_missing']}**; schema/read issues **{summary['datasets_with_schema_or_read_issues']}**",
        f"- CSV rows observed across known datasets: **{summary['csv_rows_observed']}**",
        f"- Funds observed across tables: **{summary['funds_observed_across_tables']}**",
        "",
        "> Counts below come only from files on disk. Missing is not zero, and this report does not assert live source connectivity.",
        "",
        "## Dataset inventory",
        "",
        "| Dataset | Status | Freshness | Files | Rows | Funds | Quality | Date coverage / range / age |",
        "|---|---:|---|---:|---:|---:|---|---|",
    ]
    for name, item in inventory["datasets"].items():
        dates = item.get("date_coverage", {})
        freshness = item.get("freshness", {})
        fresh_label = freshness.get("assessment", "not_assessed_no_sla")
        if freshness.get("sla_days") is not None:
            fresh_label = f"{fresh_label} (≤{freshness['sla_days']}d)"
        date_text = ", ".join(
            f"{key}: {data['coverage']:.0%} ({data['first'] or '—'} → {data['last'] or '—'}; age {data['age_days_reference_date'] if data['age_days_reference_date'] is not None else '—'}d)"
            for key, data in dates.items()) or "n/a"
        lines.append(f"| `{name}` | {item['status']} | {fresh_label} | {item['file_count']} | {item['row_count']} | {item['fund_count']} | {item['quality']['status']} | {date_text} |")
    lines.extend(["", "## Quality findings", ""])
    findings_printed = False
    for name, item in inventory["datasets"].items():
        quality = item["quality"]
        if quality["errors"] or quality["warnings"]:
            findings_printed = True
            parts = [f"{finding['code']} ({finding['count']})" for finding in quality["errors"] + quality["warnings"]]
            lines.append(f"- `{name}` — {quality['status']}: {', '.join(parts)}")
    if not findings_printed:
        lines.append("- No quality findings were computed (datasets missing or validators not applicable).")
    lines.extend(["", "## Source connectivity and provenance", "",
                  f"- Persisted source attempts: **{inventory['source_coverage']['event_count']}** ({inventory['source_coverage']['status']}).",
                  f"- Outcome counts: `{inventory['source_coverage'].get('outcomes', {})}`",
                  "- Source diagnostic does not verify provider terms or permission to reuse data."])
    observation_inventory = inventory["datasets"]["research_observations"]
    if "provenance_coverage" in observation_inventory:
        lines.append(f"- Observation time coverage: `{observation_inventory['provenance_coverage']}`")
        lines.append(f"- Verification status counts: `{observation_inventory.get('verification_status_counts', {})}`")
        lines.append(f"- Availability-date basis counts: `{observation_inventory.get('availability_basis', {})}`")
    quality_inventory = inventory["datasets"]["quality_reports"]
    if "severity_counts" in quality_inventory:
        lines.append(f"- Persisted quality finding severities: `{quality_inventory['severity_counts']}`")
    lines.extend(["", "## Prioritized next steps", ""])
    for item in inventory["prioritized_next_steps"]:
        lines.append(f"- **{item['priority']}** — {item['action']}")
    lines.extend(["", "## Qualifications", ""])
    for limitation in inventory["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)
