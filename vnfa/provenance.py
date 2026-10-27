# -*- coding: utf-8 -*-
"""Append-only source and observation provenance sidecars.

Legacy CSV schemas stay unchanged. New research records live in separate semicolon-
delimited ledgers so unknown publication/effective timestamps remain explicit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .store import SEP

SOURCE_COLUMNS = [
    "event_id", "run_id", "provider", "endpoint", "source_reference", "started_at",
    "observed_at", "completed_at", "status", "error_type", "http_status", "record_count",
    "response_hash", "retry_policy", "retry_attempts", "ingested_at",
]
OBSERVATION_COLUMNS = [
    "observation_id", "run_id", "dataset", "fund", "record_key", "effective_date",
    "observed_at", "published_at", "ingested_at", "observed_date", "observed_date_basis",
    "source", "source_reference", "record_hash", "payload_json", "data_quality_status",
    "verification_status", "quality_flags", "coverage_type", "schema_version",
]
QUALITY_COLUMNS = [
    "quality_event_id", "run_id", "dataset", "fund", "source", "status", "row_count",
    "findings_json", "metrics_json", "recorded_at",
]


class ProvenanceSchemaError(ValueError):
    """An existing provenance CSV has a different schema; no implicit migration is done."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_default(value: Any):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        if pd.isna(value):
            return None
        if isinstance(value, (datetime, pd.Timestamp)) and value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def record_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _field_text(value: Any) -> str | None:
    clean = _json_default(value)
    return None if clean is None else str(clean)


def make_observation(*, dataset: str, fund: str | None, record_key: str | None, payload: Any,
                     source: str, source_reference: str | None, observed_at: str | None,
                     effective_date: str | date | datetime | None = None, published_at: str | None = None,
                     ingested_at: str | None = None, observed_date: str | None = None,
                     observed_date_basis: str | None = None, run_id: str | None = None,
                     data_quality_status: str = "partial", verification_status: str = "unverified",
                     quality_flags: list[str] | None = None,
                     coverage_type: str | None = None, observation_id: str | None = None) -> dict[str, Any]:
    """Build one normalized append-only observation without inventing timestamps.

    `effective_date` may be known even while publication/observation times are unknown.
    A date-only observation must be explicitly tagged `verified_date_only` to be PIT
    eligible. Approximate mirror/commit dates are retained but marked `approximate`.
    """
    payload_text = canonical_json(payload)
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    effective_text = _field_text(effective_date)
    if observed_date and not observed_date_basis:
        observed_date_basis = "unknown"
    flags = sorted(set(quality_flags or []))
    identity = {
        "dataset": dataset,
        "fund": fund,
        "record_key": record_key,
        "effective_date": effective_text,
        "published_at": published_at,
        "observed_date": observed_date,
        "observed_date_basis": observed_date_basis,
        "source": source,
        "source_reference": source_reference,
        "record_hash": digest,
        "verification_status": verification_status,
        "quality_flags": flags,
    }
    observation_id = observation_id or hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return {
        "observation_id": observation_id,
        "run_id": run_id,
        "dataset": dataset,
        "fund": fund,
        "record_key": record_key,
        "effective_date": effective_text,
        "observed_at": observed_at,
        "published_at": published_at,
        "ingested_at": ingested_at,
        "observed_date": observed_date,
        "observed_date_basis": observed_date_basis,
        "source": source,
        "source_reference": source_reference,
        "record_hash": digest,
        "payload_json": payload_text,
        "data_quality_status": data_quality_status,
        "verification_status": verification_status,
        "quality_flags": canonical_json(flags),
        "coverage_type": coverage_type,
        "schema_version": "1",
    }


def observations_from_frame(frame: pd.DataFrame, *, dataset: str, fund: str | None,
                            source: str, source_reference: str | None, observed_at: str | None,
                            key_columns: Iterable[str] = (), effective_date_column: str | None = None,
                            published_at: str | None = None, observed_date: str | None = None,
                            observed_date_basis: str | None = None, run_id: str | None = None,
                            data_quality_status: str = "valid", verification_status: str = "unverified",
                            quality_flags: list[str] | None = None, coverage_type: str | None = None,
                            source_reference_column: str | None = None,
                            observed_at_column: str | None = None, published_at_column: str | None = None,
                            observed_date_column: str | None = None,
                            observed_date_basis_column: str | None = None) -> list[dict[str, Any]]:
    """Normalize a DataFrame into one provenance row per source record."""
    if frame is None or frame.empty:
        return []
    keys = list(key_columns)
    optional_columns = (effective_date_column, source_reference_column, observed_at_column,
                        published_at_column, observed_date_column, observed_date_basis_column)
    missing = [column for column in [*keys, *(c for c in optional_columns if c)] if column not in frame.columns]
    if missing:
        raise ValueError(f"Cannot build {dataset} provenance; columns missing: {', '.join(missing)}")
    records = []
    for row in frame.to_dict(orient="records"):
        if keys:
            key_parts = [_field_text(row.get(column)) or "" for column in keys]
            record_key = "|".join(key_parts)
        else:
            record_key = None
        row_source = _field_text(row.get(source_reference_column)) if source_reference_column else source_reference
        row_observed_at = _field_text(row.get(observed_at_column)) if observed_at_column else observed_at
        row_published_at = _field_text(row.get(published_at_column)) if published_at_column else published_at
        row_observed_date = _field_text(row.get(observed_date_column)) if observed_date_column else observed_date
        row_date_basis = _field_text(row.get(observed_date_basis_column)) if observed_date_basis_column else observed_date_basis
        metadata_columns = {column for column in (source_reference_column, observed_at_column,
                                                   published_at_column, observed_date_column,
                                                   observed_date_basis_column) if column}
        payload = {key: value for key, value in row.items() if key not in metadata_columns}
        records.append(make_observation(
            dataset=dataset, fund=fund, record_key=record_key, payload=payload, source=source,
            source_reference=row_source, observed_at=row_observed_at,
            effective_date=row.get(effective_date_column) if effective_date_column else None,
            published_at=row_published_at, observed_date=row_observed_date,
            observed_date_basis=row_date_basis, run_id=run_id,
            data_quality_status=data_quality_status, verification_status=verification_status,
            quality_flags=quality_flags, coverage_type=coverage_type,
        ))
    return records


class ProvenanceStore:
    """Append-only CSV store for source attempts, normalized observations, and quality."""

    def __init__(self, root: str | Path = "data", write: bool = True):
        self.root = Path(root)
        self.write = write
        if write:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / name

    def _read_existing(self, path: Path, expected: list[str]) -> pd.DataFrame:
        columns = list(pd.read_csv(path, sep=SEP, nrows=0).columns)
        if columns != expected:
            raise ProvenanceSchemaError(
                f"{path} has columns {columns}, expected {expected}; preserve/backup it and migrate explicitly")
        return pd.read_csv(path, sep=SEP, dtype=str)

    def read_source_events(self) -> pd.DataFrame:
        path = self._path("source_runs.csv")
        return pd.read_csv(path, sep=SEP, dtype=str) if path.exists() else pd.DataFrame(columns=SOURCE_COLUMNS)

    def read_observations(self) -> pd.DataFrame:
        path = self._path("research_observations.csv")
        return pd.read_csv(path, sep=SEP, dtype=str) if path.exists() else pd.DataFrame(columns=OBSERVATION_COLUMNS)

    def read_quality_reports(self) -> pd.DataFrame:
        path = self._path("quality_reports.csv")
        return pd.read_csv(path, sep=SEP, dtype=str) if path.exists() else pd.DataFrame(columns=QUALITY_COLUMNS)

    def append_source_events(self, events: list[dict[str, Any]], run_id: str | None = None,
                             ingested_at: str | None = None) -> int:
        if not self.write or not events:
            return 0
        ingested_at = ingested_at or utc_now()
        rows = []
        for event in events:
            row = {column: event.get(column) for column in SOURCE_COLUMNS}
            row["run_id"] = row["run_id"] or run_id
            row["ingested_at"] = row["ingested_at"] or ingested_at
            if not row["event_id"]:
                identity = {key: value for key, value in row.items() if key != "ingested_at"}
                row["event_id"] = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            rows.append(row)
        incoming = pd.DataFrame(rows, columns=SOURCE_COLUMNS)
        path = self._path("source_runs.csv")
        if path.exists():
            old = self._read_existing(path, SOURCE_COLUMNS)
            incoming = incoming[~incoming.event_id.isin(old.event_id)]
        if incoming.empty:
            return 0
        incoming.to_csv(path, sep=SEP, mode="a", header=not path.exists(), index=False)
        return len(incoming)

    def append_observations(self, observations: list[dict[str, Any]], run_id: str | None = None,
                            ingested_at: str | None = None) -> int:
        if not self.write or not observations:
            return 0
        ingested_at = ingested_at or utc_now()
        rows = []
        for item in observations:
            row = {column: item.get(column) for column in OBSERVATION_COLUMNS}
            row["run_id"] = row["run_id"] or run_id
            row["ingested_at"] = row["ingested_at"] or ingested_at
            if not row["observation_id"]:
                identity = {key: value for key, value in row.items()
                            if key not in {"observation_id", "run_id", "ingested_at"}}
                row["observation_id"] = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            rows.append(row)
        incoming = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
        path = self._path("research_observations.csv")
        if path.exists():
            old = self._read_existing(path, OBSERVATION_COLUMNS)
            incoming = incoming[~incoming.observation_id.isin(old.observation_id)]
        if incoming.empty:
            return 0
        incoming.to_csv(path, sep=SEP, mode="a", header=not path.exists(), index=False)
        return len(incoming)

    def append_quality_reports(self, reports: list[dict[str, Any]], *, run_id: str | None = None,
                               source: str | None = None, recorded_at: str | None = None) -> int:
        if not self.write or not reports:
            return 0
        recorded_at = recorded_at or utc_now()
        rows = []
        for report in reports:
            row = {
                "run_id": report.get("run_id") or run_id,
                "dataset": report.get("dataset"),
                "fund": report.get("fund"),
                "source": report.get("source") or source,
                "status": report.get("status"),
                "row_count": report.get("row_count", 0),
                "findings_json": canonical_json(report.get("findings", [])),
                "metrics_json": canonical_json(report.get("metrics", {})),
                "recorded_at": report.get("recorded_at") or recorded_at,
            }
            identity = {key: value for key, value in row.items() if key != "recorded_at"}
            row["quality_event_id"] = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            rows.append({column: row.get(column) for column in QUALITY_COLUMNS})
        incoming = pd.DataFrame(rows, columns=QUALITY_COLUMNS)
        path = self._path("quality_reports.csv")
        if path.exists():
            old = self._read_existing(path, QUALITY_COLUMNS)
            incoming = incoming[~incoming.quality_event_id.isin(old.quality_event_id)]
        if incoming.empty:
            return 0
        incoming.to_csv(path, sep=SEP, mode="a", header=not path.exists(), index=False)
        return len(incoming)
