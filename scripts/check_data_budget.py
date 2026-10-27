#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm tra ngân sách dung lượng `data/` và dự phóng tăng trưởng 90 ngày cho Git/CI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.store import SEP, read_csv_safe  # noqa: E402

BYTES_PER_MB = 1_000_000


def evaluate_data_budget(
    data_dir: str | Path = ROOT / "data",
    *,
    warn_mb: float = 25.0,
    fail_mb: float = 80.0,
    max_single_file_mb: float = 20.0,
    horizon_days: int = 90,
    funds_count: int = 69,
) -> dict[str, Any]:
    root = Path(data_dir)
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
    sizes = {p.relative_to(root).as_posix(): p.stat().st_size for p in files}
    total_bytes = sum(sizes.values())
    largest_file, largest_bytes = max(sizes.items(), key=lambda kv: kv[1]) if sizes else (None, 0)

    point_level_nav_obs = 0
    obs_path = root / "research_observations.csv"
    if obs_path.exists():
        obs = read_csv_safe(obs_path, sep=SEP, dtype=str)
        if {"dataset", "record_key", "coverage_type"}.issubset(obs.columns):
            nav_obs = obs[obs["dataset"].eq("nav_history")]
            if not nav_obs.empty:
                is_summary = (
                    nav_obs["record_key"].fillna("").str.startswith("summary:")
                    | nav_obs["coverage_type"].fillna("").str.endswith("_summary")
                )
                point_level_nav_obs = int((~is_summary).sum())

    # Conservative 90-day growth model for 69 funds:
    # - Steady day (unchanged holdings/tables; daily source_runs + quality_reports + nav_snapshots + nav_snapshot obs):
    #   ~140 source_runs (~35 KB) + ~210 quality_reports (~45 KB) + ~69 nav_snapshots (~1.6 KB) + ~69 nav_snapshot obs (~30 KB)
    #   ~= 115 KB/day; plus ~2 monthly portfolio/allocation disclosure waves (~1.1 MB/month).
    steady_daily_bytes = int(funds_count * 1_700)
    monthly_disclosure_wave_bytes = int(funds_count * 16_500)
    projected_growth_bytes = steady_daily_bytes * horizon_days + monthly_disclosure_wave_bytes * max(1, horizon_days // 30)
    projected_total_bytes = total_bytes + projected_growth_bytes

    warnings: list[str] = []
    errors: list[str] = []
    if total_bytes > fail_mb * BYTES_PER_MB:
        errors.append(f"Tổng dung lượng data/ ({total_bytes / BYTES_PER_MB:.2f} MB) vượt ngưỡng lỗi {fail_mb:.1f} MB")
    elif total_bytes > warn_mb * BYTES_PER_MB:
        warnings.append(f"Tổng dung lượng data/ ({total_bytes / BYTES_PER_MB:.2f} MB) vượt ngưỡng cảnh báo {warn_mb:.1f} MB")

    if largest_bytes > max_single_file_mb * BYTES_PER_MB:
        errors.append(
            f"File lớn nhất {largest_file} ({largest_bytes / BYTES_PER_MB:.2f} MB) vượt ngưỡng {max_single_file_mb:.1f} MB"
        )
    if point_level_nav_obs > 0:
        errors.append(
            f"research_observations.csv còn {point_level_nav_obs} dòng nav_history chi tiết theo ngày (chưa compact sang summary)"
        )
    if projected_total_bytes > fail_mb * BYTES_PER_MB:
        warnings.append(
            f"Dự phóng {horizon_days} ngày ({projected_total_bytes / BYTES_PER_MB:.2f} MB) tiệm cận/vượt ngưỡng {fail_mb:.1f} MB"
        )

    status = "fail" if errors else ("warn" if warnings else "ok")
    return {
        "status": status,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / BYTES_PER_MB, 3),
        "largest_file": largest_file,
        "largest_bytes": largest_bytes,
        "largest_mb": round(largest_bytes / BYTES_PER_MB, 3),
        "point_level_nav_observations": point_level_nav_obs,
        "horizon_days": horizon_days,
        "projected_growth_mb": round(projected_growth_bytes / BYTES_PER_MB, 3),
        "projected_total_mb": round(projected_total_bytes / BYTES_PER_MB, 3),
        "thresholds_mb": {"warn": warn_mb, "fail": fail_mb, "max_single_file": max_single_file_mb},
        "warnings": warnings,
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--warn-mb", type=float, default=25.0)
    ap.add_argument("--fail-mb", type=float, default=80.0)
    ap.add_argument("--max-single-file-mb", type=float, default=20.0)
    ap.add_argument("--horizon-days", type=int, default=90)
    a = ap.parse_args()

    result = evaluate_data_budget(
        a.data_dir,
        warn_mb=a.warn_mb,
        fail_mb=a.fail_mb,
        max_single_file_mb=a.max_single_file_mb,
        horizon_days=a.horizon_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
