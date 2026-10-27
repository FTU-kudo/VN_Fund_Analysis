#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compact point-level `nav_history` rows in `data/research_observations.csv` into per-fund/fetch summaries.

Guarantees:
1. Preserves exact `OBSERVATION_COLUMNS` schema (20 columns, semicolon-delimited).
2. Preserves 100% of rows from all other datasets byte/value-identically.
3. Conserves 100% of `nav_history` point counts and cross-verifies against `data/nav.csv`.
4. Requires an out-of-Git backup with verified SHA-256 before modifying `research_observations.csv`.
5. Supports `--dry-run` and is 100% idempotent on repeated execution.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.provenance import (OBSERVATION_COLUMNS, ProvenanceSchemaError,  # noqa: E402
                             make_nav_history_summary_observation)
from vnfa.store import SEP, atomic_write_csv, read_csv_safe  # noqa: E402


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _opt_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _is_summary_row(row: pd.Series) -> bool:
    key = _opt_str(row.get("record_key")) or ""
    cov = _opt_str(row.get("coverage_type")) or ""
    return key.startswith("summary:") or cov.endswith("_summary")


def compact_observations(
    data_dir: str | Path,
    *,
    backup_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(data_dir)
    obs_path = root / "research_observations.csv"
    if not obs_path.exists():
        raise FileNotFoundError(f"Không tìm thấy {obs_path}")

    header_cols = list(pd.read_csv(obs_path, sep=SEP, nrows=0).columns)
    if header_cols != OBSERVATION_COLUMNS:
        raise ProvenanceSchemaError(
            f"{obs_path} có cột {header_cols}, kỳ vọng {OBSERVATION_COLUMNS}"
        )

    before_bytes = obs_path.stat().st_size
    before_sha256 = _sha256_file(obs_path)
    df = read_csv_safe(obs_path, sep=SEP, dtype=str)
    before_counts = {str(k): int(v) for k, v in df["dataset"].value_counts(dropna=False).items()}

    is_nav = df["dataset"].eq("nav_history")
    non_nav_before = df[~is_nav].copy().reset_index(drop=True)
    nav_rows = df[is_nav].copy().reset_index(drop=True)

    summary_mask = pd.Series([_is_summary_row(r) for _, r in nav_rows.iterrows()], index=nav_rows.index, dtype=bool) \
        if not nav_rows.empty else pd.Series(dtype=bool)
    existing_summaries = nav_rows[summary_mask].copy().reset_index(drop=True)
    point_rows = nav_rows[~summary_mask].copy().reset_index(drop=True)

    new_summary_records: list[dict[str, Any]] = []
    compacted_points_total = 0
    if not point_rows.empty:
        group_cols = [
            "run_id", "fund", "source", "source_reference", "observed_at",
            "published_at", "observed_date", "observed_date_basis",
            "data_quality_status", "verification_status", "quality_flags",
        ]
        # Replace NaN with sentinel for deterministic grouping without altering values
        sentinel = "__VNFA_MISSING__"
        keyed = point_rows.copy()
        for col in group_cols:
            keyed[f"_g_{col}"] = keyed[col].where(keyed[col].notna(), sentinel)
        g_keys = [f"_g_{col}" for col in group_cols]

        for _, grp in keyed.groupby(g_keys, sort=False):
            points = []
            for row in grp.itertuples(index=False):
                dt = _opt_str(row.effective_date) or _opt_str(row.record_key)
                payload = json.loads(str(row.payload_json))
                nav_val = payload.get("nav")
                if dt is None or nav_val is None:
                    raise ValueError(f"Dòng nav_history thiếu date/nav trong payload: observation_id={row.observation_id}")
                points.append({"date": dt, "nav": float(nav_val)})
            points_df = pd.DataFrame(points)
            # Enforce 100% within-group uniqueness so point_count equals input row count
            if points_df["date"].duplicated().any():
                raise ValueError(f"Phát hiện trùng ngày trong cùng lô nav_history của quỹ {grp['fund'].iloc[0]}")
            raw_flags = _opt_str(grp["quality_flags"].iloc[0])
            flags_list = json.loads(raw_flags) if raw_flags else []
            cov_orig = _opt_str(grp["coverage_type"].iloc[0]) or "history_endpoint"
            cov_summary = cov_orig if cov_orig.endswith("_summary") else f"{cov_orig}_summary"
            summary = make_nav_history_summary_observation(
                points_df,
                fund=_opt_str(grp["fund"].iloc[0]),
                source=_opt_str(grp["source"].iloc[0]) or "unknown",
                source_reference=_opt_str(grp["source_reference"].iloc[0]),
                observed_at=_opt_str(grp["observed_at"].iloc[0]),
                published_at=_opt_str(grp["published_at"].iloc[0]),
                observed_date=_opt_str(grp["observed_date"].iloc[0]),
                observed_date_basis=_opt_str(grp["observed_date_basis"].iloc[0]),
                run_id=_opt_str(grp["run_id"].iloc[0]),
                data_quality_status=_opt_str(grp["data_quality_status"].iloc[0]) or "partial",
                verification_status=_opt_str(grp["verification_status"].iloc[0]) or "unverified",
                quality_flags=flags_list,
                coverage_type=cov_summary,
                extra_payload={"compacted_from_observation_rows": int(len(grp))},
            )
            assert summary is not None
            ingested_vals = [str(v) for v in grp["ingested_at"].dropna() if str(v).strip()]
            if ingested_vals:
                summary["ingested_at"] = max(ingested_vals)
            compacted_points_total += int(json.loads(summary["payload_json"])["point_count"])
            new_summary_records.append(summary)

        if compacted_points_total != len(point_rows):
            raise AssertionError(
                f"Đối chiếu thất bại: tổng point_count={compacted_points_total} != {len(point_rows)}"
            )

        # Verify against materialized nav.csv if present so zero NAV data points are lost.
        nav_csv_path = root / "nav.csv"
        if nav_csv_path.exists():
            nav_csv = read_csv_safe(nav_csv_path, sep=SEP, dtype={"fund": str, "date": str})
            nav_csv["date"] = pd.to_datetime(nav_csv["date"], errors="raise").dt.strftime("%Y-%m-%d")
            nav_csv["nav"] = pd.to_numeric(nav_csv["nav"], errors="raise")
            materialized_shas: dict[str, str] = {}
            for fund_code, fgrp in nav_csv.groupby("fund", sort=False):
                fgrp_clean = fgrp.drop_duplicates("date", keep="last").sort_values("date", kind="stable")
                canon = "\n".join(f"{d};{round(float(v), 8):.8f}" for d, v in zip(fgrp_clean["date"], fgrp_clean["nav"]))
                materialized_shas[str(fund_code)] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
            for summary in new_summary_records:
                fcode = str(summary["fund"])
                payload_obj = json.loads(summary["payload_json"])
                if fcode not in materialized_shas:
                    raise AssertionError(f"Quỹ {fcode} có trong nav_history provenance nhưng thiếu trong nav.csv!")
                if materialized_shas[fcode] != payload_obj["series_sha256"]:
                    raise AssertionError(f"Chuỗi NAV của quỹ {fcode} không khớp SHA-256 với data/nav.csv!")

    new_summaries_df = pd.DataFrame(new_summary_records, columns=OBSERVATION_COLUMNS) \
        if new_summary_records else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    # Deduplicate summary observation_id if re-compacting mixed data
    combined_nav = pd.concat([existing_summaries, new_summaries_df], ignore_index=True)
    if not combined_nav.empty:
        combined_nav = combined_nav.drop_duplicates("observation_id", keep="first")

    out_df = pd.concat([non_nav_before, combined_nav], ignore_index=True).reindex(columns=OBSERVATION_COLUMNS)

    # Verify 100% identity of all non-nav_history rows after round-trip serialization!
    buf = io.BytesIO()
    out_df.to_csv(buf, sep=SEP, index=False)
    projected_bytes_data = buf.getvalue()
    projected_bytes = len(projected_bytes_data)
    projected_sha256 = _sha256_bytes(projected_bytes_data)

    roundtrip_df = read_csv_safe(io.BytesIO(projected_bytes_data), sep=SEP, dtype=str)
    non_nav_after = roundtrip_df[roundtrip_df["dataset"].ne("nav_history")].reset_index(drop=True)
    pd.testing.assert_frame_equal(non_nav_before, non_nav_after)

    after_counts = {str(k): int(v) for k, v in roundtrip_df["dataset"].value_counts(dropna=False).items()}
    backup_file_str = None
    if not dry_run and not point_rows.empty:
        if backup_dir is None:
            raise ValueError("Phải chỉ định --backup-dir (ngoài Git repo) khi chạy thật không có --dry-run")
        bdir = Path(backup_dir).resolve()
        try:
            bdir.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(f"--backup-dir ({bdir}) không được nằm trong Git repository ({ROOT.resolve()})")
        bdir.mkdir(parents=True, exist_ok=True)
        bak_path = bdir / "research_observations.csv.bak"
        shutil.copy2(obs_path, bak_path)
        bak_sha = _sha256_file(bak_path)
        if bak_sha != before_sha256:
            raise AssertionError(f"Backup SHA-256 mismatch: {bak_sha} != {before_sha256}")
        (bdir / "research_observations.csv.sha256").write_text(
            f"{before_sha256}  research_observations.csv.bak\n", encoding="utf-8"
        )
        backup_file_str = str(bak_path)
        atomic_write_csv(out_df, obs_path, sep=SEP)
        written_sha = _sha256_file(obs_path)
        if written_sha != projected_sha256:
            raise AssertionError(f"Written SHA-256 mismatch: {written_sha} != {projected_sha256}")

    report = {
        "dry_run": dry_run,
        "already_compact": bool(point_rows.empty),
        "before_bytes": before_bytes,
        "after_bytes": before_bytes if point_rows.empty else projected_bytes,
        "bytes_saved": 0 if point_rows.empty else (before_bytes - projected_bytes),
        "reduction_pct": 0.0 if point_rows.empty or before_bytes == 0 else round((1 - projected_bytes / before_bytes) * 100, 2),
        "before_sha256": before_sha256,
        "after_sha256": before_sha256 if point_rows.empty else projected_sha256,
        "before_rows": int(len(df)),
        "after_rows": int(len(df)) if point_rows.empty else int(len(out_df)),
        "non_nav_rows_preserved": int(len(non_nav_before)),
        "nav_point_rows_compacted": int(len(point_rows)),
        "nav_points_accounted_for": compacted_points_total,
        "nav_summary_rows_after": int(len(combined_nav)),
        "before_dataset_counts": before_counts,
        "after_dataset_counts": before_counts if point_rows.empty else after_counts,
        "backup_file": backup_file_str,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--backup-dir", default="/tmp/vnfa_provenance_backup",
                    help="thư mục ngoài Git để lưu bản sao lưu nguyên gốc + SHA-256")
    ap.add_argument("--dry-run", action="store_true",
                    help="tính toán dung lượng, số dòng và đối chiếu mà không sửa file")
    ap.add_argument("--report-out", default=None, help="ghi báo cáo đối chiếu JSON ra file")
    a = ap.parse_args()

    report = compact_observations(a.data_dir, backup_dir=a.backup_dir, dry_run=a.dry_run)
    if a.report_out:
        out_p = Path(a.report_out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
