# -*- coding: utf-8 -*-
"""Conservative point-in-time filtering for normalized observation records.

`effective_date` describes a reporting/economic period. It never establishes when the
record became available. Exact availability uses `observed_at`, delayed as needed by
`published_at`. Date-only legacy observations are eligible only as end-of-day lower-
precision records; explicitly approximate backfill dates are excluded.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import pandas as pd

LOCAL_TIMEZONE = "Asia/Ho_Chi_Minh"


class PointInTimeError(ValueError):
    """Invalid cutoff or natural-key request."""


def _parse_cutoff(cutoff: str | date | datetime | pd.Timestamp) -> tuple[pd.Timestamp, bool]:
    """Return a UTC cutoff and whether the caller supplied a date-only cutoff.

    A bare YYYY-MM-DD is interpreted as end-of-day in Vietnam local time. Timestamp
    cutoffs must include an explicit timezone, avoiding host-local assumptions.
    """
    is_date_only = isinstance(cutoff, date) and not isinstance(cutoff, datetime)
    if isinstance(cutoff, str):
        text = cutoff.strip()
        is_date_only = len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"
        cutoff = text
    try:
        ts = pd.Timestamp(cutoff)
    except (TypeError, ValueError) as exc:
        raise PointInTimeError(f"Invalid cutoff: {cutoff!r}") from exc
    if ts.tzinfo is None:
        if not is_date_only:
            raise PointInTimeError("Timestamp cutoff must include a timezone; use YYYY-MM-DD for Vietnam end-of-day")
        ts = (ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1))
        ts = ts.tz_localize(LOCAL_TIMEZONE).tz_convert("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts, is_date_only


def _timestamps(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True)


def add_availability(records: pd.DataFrame, cutoff: str | date | datetime | pd.Timestamp,
                     observed_at_col: str = "observed_at", published_at_col: str = "published_at",
                     legacy_observed_date_col: str = "observed_date",
                     observed_date_basis_col: str = "observed_date_basis",
                     allow_legacy_date_only: bool = False) -> tuple[pd.DataFrame, dict[str, int]]:
    """Annotate record availability and report inclusion/exclusion counts.

    If exact observation and publication timestamps exist, the later is used. A
    publication timestamp alone is insufficient: system observation is still unknown.
    Date-only `observed_date` is eligible only when explicitly tagged
    `verified_date_only`/`source_observed_date`/`date_only`. Untagged legacy dates are
    excluded unless `allow_legacy_date_only=True` is explicitly requested. Values
    marked `approximate` or `unknown` are always excluded.
    """
    limit, _ = _parse_cutoff(cutoff)
    out = records.copy()
    if observed_at_col in out.columns:
        observed = _timestamps(out[observed_at_col])
    else:
        observed = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    if published_at_col in out.columns:
        published = _timestamps(out[published_at_col])
    else:
        published = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")

    exact = observed.notna()
    # Published time may delay availability, but cannot replace a missing observation time.
    available = observed.copy()
    both = observed.notna() & published.notna()
    available.loc[both] = pd.concat([observed.loc[both], published.loc[both]], axis=1).max(axis=1)
    precision = pd.Series("unknown", index=out.index, dtype="object")
    precision.loc[exact] = "timestamp"

    if legacy_observed_date_col in out.columns:
        parsed_day = pd.to_datetime(out[legacy_observed_date_col], errors="coerce")
        if parsed_day.dt.tz is None:
            parsed_day = parsed_day.dt.tz_localize(LOCAL_TIMEZONE)
        else:
            parsed_day = parsed_day.dt.tz_convert(LOCAL_TIMEZONE)
        legacy = parsed_day.dt.normalize().dt.tz_convert("UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        if observed_date_basis_col in out.columns:
            basis = out[observed_date_basis_col].fillna("unknown").astype(str).str.strip().str.lower()
            allowed = basis.isin({"verified_date_only", "source_observed_date", "date_only"})
        else:
            allowed = pd.Series(bool(allow_legacy_date_only), index=out.index)
        use_legacy = ~exact & legacy.notna() & allowed
        available.loc[use_legacy] = legacy.loc[use_legacy]
        precision.loc[use_legacy] = "date_only_conservative"

    out["pit_available_at"] = available
    out["pit_time_precision"] = precision
    out["pit_eligible"] = available.notna() & (available <= limit)
    # A future effective date is not accepted; same-day effective observations remain valid.
    if "effective_date" in out.columns:
        effective = _timestamps(out.effective_date)
        cutoff_local_day = limit.tz_convert(LOCAL_TIMEZONE).strftime("%Y-%m-%d")
        effective_local_day = effective.dt.tz_convert(LOCAL_TIMEZONE).dt.strftime("%Y-%m-%d")
        future_effective = effective.notna() & (effective_local_day > cutoff_local_day)
        out.loc[future_effective, "pit_eligible"] = False
    invalid_observed = 0
    if observed_at_col in out.columns:
        present = out[observed_at_col].notna() & out[observed_at_col].astype(str).str.strip().ne("")
        invalid_observed = int((present & observed.isna()).sum())
    invalid_published = 0
    if published_at_col in out.columns:
        present = out[published_at_col].notna() & out[published_at_col].astype(str).str.strip().ne("")
        invalid_published = int((present & published.isna()).sum())
    counts = {
        "records": int(len(out)),
        "eligible": int(out.pit_eligible.sum()),
        "excluded_after_cutoff": int((available.notna() & (available > limit)).sum()),
        "excluded_unknown": int(available.isna().sum()),
        "excluded_future_effective": int((~out.pit_eligible & available.notna() & (available <= limit)).sum()),
        "invalid_observed_at": invalid_observed,
        "invalid_published_at": invalid_published,
    }
    return out, counts


def select_as_of(records: pd.DataFrame, cutoff: str | date | datetime | pd.Timestamp,
                 key_columns: Iterable[str] | None = None, *,
                 observed_at_col: str = "observed_at", published_at_col: str = "published_at",
                 legacy_observed_date_col: str = "observed_date",
                 observed_date_basis_col: str = "observed_date_basis",
                 unknown_time: str = "exclude", allow_legacy_date_only: bool = False) -> pd.DataFrame:
    """Select observations safely available by `cutoff`.

    `unknown_time='include'` is descriptive only: rows remain marked
    `pit_eligible=False`. Untagged `observed_date` requires the explicit
    `allow_legacy_date_only=True` opt-in. If natural-key columns are supplied, select the latest known
    revision at the cutoff, breaking ties with ingestion time and observation ID.
    """
    if unknown_time not in {"exclude", "include"}:
        raise ValueError("unknown_time must be 'exclude' or 'include'")
    annotated, _ = add_availability(records, cutoff, observed_at_col, published_at_col,
                                    legacy_observed_date_col, observed_date_basis_col,
                                    allow_legacy_date_only)
    if unknown_time == "exclude":
        selected = annotated[annotated.pit_eligible].copy()
    else:
        selected = annotated[annotated.pit_eligible | annotated.pit_available_at.isna()].copy()
        selected.loc[selected.pit_available_at.isna(), "pit_availability_status"] = "unknown_time_included_not_pit_safe"
    if key_columns:
        keys = list(key_columns)
        missing = [key for key in keys if key not in selected.columns]
        if missing:
            raise PointInTimeError(f"Natural key columns missing: {', '.join(missing)}")
        tie_breakers = [c for c in ("pit_available_at", "ingested_at", "observation_id") if c in selected.columns]
        if tie_breakers:
            selected = selected.sort_values(tie_breakers, kind="stable")
        selected = selected.drop_duplicates(keys, keep="last")
    return selected.reset_index(drop=True)
