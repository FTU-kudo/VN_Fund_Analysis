#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate `data/source_runs.csv` from schema v1 (16 cols) to v2 (17 cols with `fund_code`).

Historical rows retain empty `fund_code` (explicitly unknown — NEVER guessed from row order).
Requires an out-of-Git backup with verified SHA-256 unless `--dry-run` is set.
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

from vnfa.provenance import SOURCE_COLUMNS, SOURCE_COLUMNS_V1, ProvenanceSchemaError  # noqa: E402
from vnfa.store import SEP, atomic_write_csv, read_csv_safe  # noqa: E402


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_source_runs(
    data_dir: str | Path = ROOT / "data",
    *,
    backup_dir: str | Path = "/tmp/vnfa_source_runs_backup",
    dry_run: bool = False,
) -> dict[str, Any]:
    path = Path(data_dir) / "source_runs.csv"
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy {path}")
    cols = list(pd.read_csv(path, sep=SEP, nrows=0).columns)
    before_bytes = path.stat().st_size
    before_sha256 = _sha256_file(path)

    if cols == SOURCE_COLUMNS:
        df = read_csv_safe(path, sep=SEP, dtype=str)
        return {
            "dry_run": dry_run,
            "already_v2": True,
            "rows": int(len(df)),
            "before_bytes": before_bytes,
            "after_bytes": before_bytes,
            "before_sha256": before_sha256,
            "after_sha256": before_sha256,
            "backup_file": None,
        }
    if cols != SOURCE_COLUMNS_V1:
        raise ProvenanceSchemaError(
            f"{path} có cột {cols}, không khớp v1 ({SOURCE_COLUMNS_V1}) hay v2 ({SOURCE_COLUMNS})"
        )

    v1 = read_csv_safe(path, sep=SEP, dtype=str)
    v2 = v1.copy()
    v2["fund_code"] = ""
    v2 = v2.reindex(columns=SOURCE_COLUMNS)

    buf = io.BytesIO()
    v2.to_csv(buf, sep=SEP, index=False)
    projected_bytes_data = buf.getvalue()
    projected_sha256 = hashlib.sha256(projected_bytes_data).hexdigest()

    roundtrip = read_csv_safe(io.BytesIO(projected_bytes_data), sep=SEP, dtype=str)
    pd.testing.assert_frame_equal(v1.reset_index(drop=True), roundtrip[SOURCE_COLUMNS_V1].reset_index(drop=True))
    assert roundtrip["fund_code"].isna().all(), "Historical fund_code must remain unknown (NA), never guessed"

    backup_file_str = None
    if not dry_run:
        bdir = Path(backup_dir).resolve()
        try:
            bdir.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(f"--backup-dir ({bdir}) không được nằm trong Git repository ({ROOT.resolve()})")
        bdir.mkdir(parents=True, exist_ok=True)
        bak_path = bdir / "source_runs.csv.bak"
        shutil.copy2(path, bak_path)
        if _sha256_file(bak_path) != before_sha256:
            raise AssertionError("Backup SHA-256 mismatch")
        (bdir / "source_runs.csv.sha256").write_text(f"{before_sha256}  source_runs.csv.bak\n", encoding="utf-8")
        backup_file_str = str(bak_path)
        atomic_write_csv(v2, path, sep=SEP)
        if _sha256_file(path) != projected_sha256:
            raise AssertionError("Written SHA-256 mismatch")

    return {
        "dry_run": dry_run,
        "already_v2": False,
        "rows": int(len(v2)),
        "before_bytes": before_bytes,
        "after_bytes": len(projected_bytes_data),
        "before_sha256": before_sha256,
        "after_sha256": projected_sha256,
        "backup_file": backup_file_str,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--backup-dir", default="/tmp/vnfa_source_runs_backup")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(migrate_source_runs(a.data_dir, backup_dir=a.backup_dir, dry_run=a.dry_run),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
