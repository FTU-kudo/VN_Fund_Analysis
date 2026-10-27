#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nạp lịch sử danh mục từ git log của repo huyhn-ba-po/hat-ccq (mirror bên thứ ba).

LƯU Ý PHÁP LÝ / BẢN QUYỀN:
- Repo `huyhn-ba-po/hat-ccq` ghi `[MIT](LICENSE)` trong README nhưng hiện KHÔNG có file
  `LICENSE` trong kho, và điều khoản tái phân phối dữ liệu của Fmarket chưa được xác minh.
- Vì vậy việc ghi trực tiếp dữ liệu mirror vào thư mục chính `data/` của repo bị CHẶN mặc định
  (cần `--allow-publish-to-data` khi đã có xác nhận pháp lý). Dùng `--dry-run` hoặc chỉ định
  thư mục tạm ngoài Git (`--data-dir /tmp/...`) để kiểm thử staging.

Repo đó commit dữ liệu Fmarket hằng ngày từ 16/05/2026; mỗi commit chứa top 10 cổ phiếu của từng quỹ.
Script phát lại các commit theo thứ tự thời gian qua cùng pipeline với run_daily.py. Ngày commit chỉ là
xấp xỉ crawl, không phải publication timestamp; provenance backfill được gắn observed_date_basis=approximate
và bị loại khỏi PIT filtering mặc định.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diff import CHANGE_COLUMNS, flag_corporate_actions  # noqa: E402
from vnfa.fmarket import (parse_allocation, parse_bonds, parse_detail, parse_fees,  # noqa: E402
                          parse_industries, parse_nav_history, parse_profile)
from vnfa.pipeline import ingest_fund  # noqa: E402
from vnfa.provenance import (ProvenanceStore, make_nav_history_summary_observation,  # noqa: E402
                             make_observation, observations_from_frame, utc_now)
from vnfa.quality import (require_valid, validate_allocation, validate_bond_holdings,  # noqa: E402
                          validate_holdings, validate_nav, validate_nav_snapshot)
from vnfa.store import SEP, Store, TableStore, atomic_write_csv, content_hash, read_csv_safe  # noqa: E402

REPO = "https://github.com/huyhn-ba-po/hat-ccq.git"


def _quality_entry(report, fund: str | None, run_id: str) -> dict:
    item = report.to_dict()
    findings = item.pop("errors") + item.pop("warnings")
    return {**item, "fund": fund, "findings": findings, "source": "hat-ccq-mirror", "run_id": run_id}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_backdated(store: Store, bond_store: Store, tables: TableStore, fund: str,
                      observed_date: str, collect: dict) -> bool:
    """Return true when appending this mirror date would backdate any legacy table."""
    dates = []
    for target in (store, bond_store):
        holdings = target.read_holdings(fund)
        if not holdings.empty and "observed_date" in holdings:
            parsed = pd.to_datetime(holdings.observed_date, errors="coerce")
            if parsed.notna().any():
                dates.append(parsed.max().date())
    for name in ("allocation", "industry", "fees", "profile"):
        if collect.get(name):
            frame = tables.read(name)
            if not frame.empty and {"fund", "observed_date"}.issubset(frame.columns):
                subset = frame[frame.fund.astype(str) == str(fund)]
                parsed = pd.to_datetime(subset.observed_date, errors="coerce")
                if parsed.notna().any():
                    dates.append(parsed.max().date())
    candidate = pd.Timestamp(observed_date).date()
    return bool(dates and candidate < max(dates))


def git(repo: Path, *args: str, inp: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=inp, capture_output=True, check=True).stdout


def commit_details(repo: Path, sha: str):
    files = git(repo, "ls-tree", "--name-only", sha, "data/details/").decode().split()
    if not files:
        return
    out = git(repo, "cat-file", "--batch", inp="".join(f"{sha}:{f}\n" for f in files).encode())
    i = 0
    for _ in files:
        nl = out.index(b"\n", i)
        hdr = out[i:nl].split()
        if hdr[-1] == b"missing":
            i = nl + 1
            continue
        size = int(hdr[2])
        body = out[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
        try:
            yield json.loads(body)
        except json.JSONDecodeError:
            continue


def nav_snapshots(repo: Path, commits, data_dir: str, *, write_legacy: bool = True,
                  dry_run: bool = False) -> dict[str, Any]:
    """Keep NAV snapshots and append mirror provenance without treating commit date as exact.

    `write_legacy=False` appends only to the research ledger, enabling safe historical
    backfills after the legacy daily store has advanced beyond the mirror dates.
    """
    rows: dict[str, list[tuple[str, str, float]]] = {}
    for sha, date in commits:
        for detail in commit_details(repo, sha):
            nav = detail.get("nav")
            if detail.get("code") and nav is not None:
                rows.setdefault(detail["code"], []).append((sha, date, float(nav)))
    out, observations = [], []
    for code, sequence in rows.items():
        prev = None
        for sha, date, nav in sequence:
            if prev is None or abs(nav - prev) > 1e-9:
                out.append((code, date, nav))
                observations.append(make_observation(
                    dataset="nav_snapshot", fund=code, record_key=date,
                    payload={"nav": nav, "mirror_commit_date": date}, source="hat-ccq-mirror",
                    source_reference=f"{REPO}/commit/{sha}", observed_at=None,
                    observed_date=date, observed_date_basis="approximate",
                    data_quality_status="partial", verification_status="third_party_mirror_unverified",
                    quality_flags=["approximate_observation_date", "effective_date_unknown"],
                    coverage_type="daily_mirror_snapshot",
                ))
                prev = nav
    df = pd.DataFrame(out, columns=["fund", "date", "nav"])
    p = Path(data_dir) / "nav_snapshots.csv"
    if write_legacy and p.exists():
        old = read_csv_safe(p, sep=SEP, dtype={"fund": str, "date": str})
        if not {"fund", "date", "nav"}.issubset(old.columns):
            raise ValueError("nav_snapshots.csv thiếu một trong các cột fund/date/nav; không ghi đè dữ liệu")
        # Keep all existing snapshots; on a key conflict existing data retains priority.
        df = pd.concat([df, old[["fund", "date", "nav"]]], ignore_index=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df.date, errors="raise").dt.strftime("%Y-%m-%d")
        df["nav"] = pd.to_numeric(df.nav, errors="raise")
        if not np.isfinite(df.nav.astype(float)).all() or (df.nav.astype(float) <= 0).any():
            raise ValueError("NAV snapshot phải là số dương, hữu hạn; không ghi đè dữ liệu")
        df = df.drop_duplicates(["fund", "date"], keep="last").sort_values(["fund", "date"], kind="stable")
    commit_list_sha256 = hashlib.sha256(
        "\n".join(f"{sha} {dt}" for sha, dt in commits).encode("utf-8")
    ).hexdigest()
    run_id = f"hat-ccq-snap-{commit_list_sha256[:16]}"
    quality = validate_nav_snapshot(df)
    require_valid(quality)
    obs_appended = 0
    if not dry_run:
        p.parent.mkdir(parents=True, exist_ok=True)
        sidecar = ProvenanceStore(data_dir, write=True)
        obs_appended = sidecar.append_observations(observations, run_id=run_id)
        sidecar.append_quality_reports([_quality_entry(quality, None, run_id)], run_id=run_id,
                                       source="hat-ccq-mirror", recorded_at=utc_now())
        if write_legacy:
            atomic_write_csv(df, p, sep=SEP)
    first, last = (df.date.min(), df.date.max()) if not df.empty else ("—", "—")
    mode = "dry-run" if dry_run else ("updated" if write_legacy else "legacy file unchanged")
    print(f"nav_snapshots.csv: {len(df)} mirror rows reviewed ({mode}), {df.fund.nunique()} funds, {first} → {last}")
    return {
        "rows_total": int(len(df)),
        "funds": int(df.fund.nunique()) if not df.empty else 0,
        "first_date": None if df.empty else str(first),
        "last_date": None if df.empty else str(last),
        "observations_prepared": len(observations),
        "observations_appended": obs_appended,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--nav-snapshots-only", action="store_true",
                    help="chỉ trích NAV chụp hằng ngày (nav_snapshots.csv), không phát lại danh mục")
    ap.add_argument("--provenance-only", action="store_true",
                    help="append lịch sử vào research_observations.csv mà không sửa CSV legacy; dùng cho backfill sau khi daily store đã tiến tới hiện tại")
    ap.add_argument("--dry-run", action="store_true",
                    help="kiểm tra nguồn, schema và số lượng bản ghi mà không ghi bất kỳ file nào")
    ap.add_argument("--allow-publish-to-data", action="store_true",
                    help="xác nhận đã kiểm tra bản quyền/điều khoản để cho phép ghi vào data/ của repo")
    ap.add_argument("--max-commits", type=int, default=0,
                    help="giới hạn số commit đầu tiên để kiểm thử nhanh (0 = tất cả)")
    ap.add_argument("--manifest-out", default=None,
                    help="ghi manifest JSON (kèm SHA-256 các file đích) ra đường dẫn chỉ định")
    ap.add_argument("--src", default=None, help="đường dẫn bản clone sẵn của hat-ccq (bỏ qua bước clone)")
    a = ap.parse_args()

    target_dir = Path(a.data_dir)
    if target_dir.resolve() == (ROOT / "data").resolve() and not a.dry_run and not a.allow_publish_to_data:
        raise SystemExit(
            "CHẶN AN TOÀN: `huyhn-ba-po/hat-ccq` hiện thiếu file LICENSE trong repo và điều khoản tái phân phối "
            "dữ liệu Fmarket chưa được xác minh. Dùng `--dry-run` hoặc `--data-dir /tmp/...` để kiểm thử staging, "
            "hoặc truyền `--allow-publish-to-data` khi chủ repo đã phê duyệt pháp lý."
        )

    cfg = load_config(a.config)
    legacy_write = (not a.provenance_only) and (not a.dry_run)
    store = Store(a.data_dir, write=legacy_write)
    bond_store = Store(a.data_dir, write=legacy_write, subdir="bonds")
    tables = TableStore(a.data_dir, write=legacy_write)
    col = cfg["collect"]
    wanted, skip = set(cfg["funds"]["asset_codes"]), set(cfg["funds"]["exclude"])
    ca = cfg["corporate_action"]
    provenance_observations, quality_reports = [], []

    tmp = None
    if a.src:
        repo = Path(a.src)
    else:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name) / "hat-ccq"
        print("Đang clone", REPO)
        subprocess.run(["git", "clone", "--quiet", REPO, str(repo)], check=True)

    try:
        log = git(repo, "log", "--reverse", "--format=%H %ad", "--date=short", "--", "data/details").decode().split("\n")
        commits = [line.split() for line in log if line.strip()]
        if a.max_commits and a.max_commits > 0:
            commits = commits[:a.max_commits]
        commit_list_sha256 = hashlib.sha256(
            "\n".join(f"{sha} {dt}" for sha, dt in commits).encode("utf-8")
        ).hexdigest()
        run_id = f"hat-ccq-{commit_list_sha256[:16]}"
        if a.nav_snapshots_only:
            snap_summary = nav_snapshots(repo, commits, a.data_dir, write_legacy=legacy_write, dry_run=a.dry_run)
            if a.manifest_out:
                Path(a.manifest_out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.manifest_out).write_text(json.dumps({
                    "mode": "nav_snapshots_only",
                    "dry_run": a.dry_run,
                    "commits_count": len(commits),
                    "commit_list_sha256": commit_list_sha256,
                    "nav_snapshots": snap_summary,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        print(f"{len(commits)} commit có dữ liệu chi tiết quỹ (dry_run={a.dry_run})")
        total_changes, history = 0, pd.DataFrame(columns=CHANGE_COLUMNS)
        bond_batches: list[pd.DataFrame] = []
        metas, source_hashes = {}, {}
        legacy_backfill_occurred, n_legacy_skipped = False, 0
        for sha, date in commits:
            day = []
            source_reference = f"{REPO}/commit/{sha}"
            for detail in commit_details(repo, sha):
                meta, holdings = parse_detail(detail)
                if meta.get("asset_code") not in wanted or meta.get("code") in skip:
                    continue
                code = meta["code"]
                backdated = a.provenance_only or _legacy_backdated(store, bond_store, tables, code, date, col)
                if backdated and not a.provenance_only:
                    legacy_backfill_occurred = True
                    n_legacy_skipped += 1
                holding_quality = validate_holdings(holdings, coverage_type="top_n")
                quality_reports.append(_quality_entry(holding_quality, code, run_id))
                require_valid(holding_quality)
                if backdated:
                    status, changes = "provenance_only", pd.DataFrame(columns=CHANGE_COLUMNS)
                else:
                    status, changes = ingest_fund(store, meta, holdings, date, cfg)
                holding_hash = content_hash(holdings) if not holdings.empty else None
                holding_key = ("fund_holdings", code)
                holding_changed = holding_hash is not None and source_hashes.get(holding_key) != holding_hash
                if holding_hash is not None:
                    source_hashes[holding_key] = holding_hash
                if holding_changed:
                    provenance_observations.extend(observations_from_frame(
                        holdings, dataset="fund_holdings", fund=code, source="hat-ccq-mirror",
                        source_reference=source_reference, observed_at=None,
                        key_columns=("ticker",), effective_date_column="update_date",
                        observed_date=date, observed_date_basis="approximate", run_id=run_id,
                        data_quality_status=holding_quality.status,
                        verification_status="third_party_mirror_unverified",
                        quality_flags=[finding.code for finding in holding_quality.findings]
                            + ["approximate_observation_date"], coverage_type="top_n",
                    ))
                if col["bonds"]:
                    bond_holdings = parse_bonds(detail)
                    bond_quality = validate_bond_holdings(bond_holdings, coverage_type="top_n")
                    quality_reports.append(_quality_entry(bond_quality, code, run_id))
                    if not bond_quality.errors:
                        if backdated:
                            bond_status, bond_changes = "provenance_only", pd.DataFrame(columns=CHANGE_COLUMNS)
                        else:
                            bond_status, bond_changes = ingest_fund(
                                bond_store, meta, bond_holdings, date, cfg, asset_class="BOND")
                        bond_hash = content_hash(bond_holdings) if not bond_holdings.empty else None
                        bond_key = ("fund_bond_holdings", code)
                        bond_changed = bond_hash is not None and source_hashes.get(bond_key) != bond_hash
                        if bond_hash is not None:
                            source_hashes[bond_key] = bond_hash
                        if bond_changed:
                            provenance_observations.extend(observations_from_frame(
                                bond_holdings, dataset="fund_bond_holdings", fund=code, source="hat-ccq-mirror",
                                source_reference=source_reference, observed_at=None,
                                key_columns=("ticker",), effective_date_column="update_date",
                                observed_date=date, observed_date_basis="approximate", run_id=run_id,
                                data_quality_status=bond_quality.status,
                                verification_status="third_party_mirror_unverified",
                                quality_flags=[finding.code for finding in bond_quality.findings]
                                    + ["approximate_observation_date"], coverage_type="top_n",
                            ))
                        if not backdated and not bond_changes.empty:
                            bond_batches.append(bond_changes)
                for name, parser in (("allocation", parse_allocation), ("industry", parse_industries),
                                     ("fees", parse_fees), ("profile", parse_profile)):
                    if not col[name]:
                        continue
                    frame = parser(detail)
                    quality_status, quality_flags = "unvalidated", []
                    if name == "allocation":
                        allocation_quality = validate_allocation(frame)
                        quality_reports.append(_quality_entry(allocation_quality, code, run_id))
                        quality_status = allocation_quality.status
                        quality_flags = [finding.code for finding in allocation_quality.findings]
                        if allocation_quality.errors:
                            continue
                    if not backdated and not frame.empty:
                        tables.append_if_changed(name, code, frame, date)
                    if not frame.empty:
                        table_hash = TableStore._hash(frame)
                        table_key = (f"fund_{name}", code)
                        if source_hashes.get(table_key) != table_hash:
                            source_hashes[table_key] = table_hash
                            key_columns = {
                                "allocation": ("asset_code",), "industry": ("industry",),
                                "fees": ("type", "program", "begin", "end", "unit"), "profile": (),
                            }[name]
                            provenance_observations.extend(observations_from_frame(
                                frame, dataset=f"fund_{name}", fund=code, source="hat-ccq-mirror",
                                source_reference=source_reference, observed_at=None,
                                key_columns=key_columns,
                                effective_date_column="report_date" if name == "allocation" else None,
                                observed_date=date, observed_date_basis="approximate", run_id=run_id,
                                data_quality_status=quality_status,
                                verification_status="third_party_mirror_unverified",
                                quality_flags=quality_flags + ["approximate_observation_date"],
                                coverage_type="source_returned",
                            ))
                metas[code] = {key: meta.get(key) for key in ("code", "short_name", "mgmt_co", "asset_code", "management_fee")}
                if not changes.empty:
                    day.append(changes)
            if day:
                current = pd.concat(day, ignore_index=True)
                recent = history[pd.to_datetime(history.observed_date) >= pd.Timestamp(date) - pd.Timedelta(days=ca["lookback_days"])] \
                    if not history.empty else history
                current, history_flagged = flag_corporate_actions(
                    current, recent, ca["min_funds"], ca["spread_pct"] / 100,
                    ca["min_change_pct"] / 100, update_history=True,
                )
                if not history_flagged.empty and not history.empty:
                    flagged_keys = set(zip(history_flagged.observed_date, history_flagged.fund, history_flagged.ticker))
                    keep_mask = [
                        (r.observed_date, r.fund, r.ticker) not in flagged_keys
                        for r in history.itertuples(index=False)
                    ]
                    history = pd.concat([history[keep_mask], history_flagged], ignore_index=True)
                history = pd.concat([history, current], ignore_index=True)
                total_changes += len(current)
                print(f"{date}: {len(current)} thay đổi")
        all_changes_frames = [f for f in [history, *bond_batches] if not f.empty]
        if all_changes_frames:
            store.append_changes(pd.concat(all_changes_frames, ignore_index=True))
        if legacy_write and not legacy_backfill_occurred and metas and commits:
            store.upsert_funds(list(metas.values()), commits[-1][1])
        elif legacy_backfill_occurred:
            print(f"Legacy CSV unchanged for {n_legacy_skipped} backdated fund snapshots; provenance sidecar will retain them.")

        nav_written = 0
        if col["nav"]:
            parts = {}
            nav_observations = []
            if legacy_write:
                existing_nav = tables.read_nav()
                if not existing_nav.empty:
                    if not {"fund", "date", "nav"}.issubset(existing_nav.columns):
                        raise ValueError("nav.csv schema không hợp lệ; không ghi đè NAV legacy")
                    existing_obs = ProvenanceStore(a.data_dir, write=False).read_observations()
                    covered_nav_series: set[tuple[str, str]] = set()
                    if not existing_obs.empty:
                        nav_obs_rows = existing_obs[existing_obs.dataset == "nav_history"]
                        for row in nav_obs_rows.itertuples(index=False):
                            try:
                                payload_obj = json.loads(str(row.payload_json))
                            except (TypeError, ValueError):
                                payload_obj = {}
                            sha_val = payload_obj.get("series_sha256")
                            if sha_val:
                                covered_nav_series.add((str(row.fund), str(sha_val)))
                    legacy_backup_obs = []
                    for code, old_part in existing_nav.groupby("fund", sort=False):
                        summary_obs = make_nav_history_summary_observation(
                            old_part, fund=str(code), source="legacy_nav_csv",
                            source_reference="data/nav.csv", observed_at=None, run_id=run_id,
                            data_quality_status="valid", verification_status="legacy_source_unknown",
                            quality_flags=["availability_time_unknown"],
                            coverage_type="legacy_materialized_history_summary",
                        )
                        if summary_obs is None:
                            continue
                        series_sha = json.loads(summary_obs["payload_json"]).get("series_sha256", "")
                        if (str(code), str(series_sha)) in covered_nav_series:
                            continue
                        old_quality = validate_nav(old_part)
                        quality_reports.append(_quality_entry(old_quality, str(code), run_id))
                        summary_obs["data_quality_status"] = old_quality.status
                        legacy_backup_obs.append(summary_obs)
                    if legacy_backup_obs and not a.dry_run:
                        # Preserve untracked pre-backfill NAV in the sidecar before an upsert can replace a key.
                        ProvenanceStore(a.data_dir, write=True).append_observations(legacy_backup_obs, run_id=run_id)
                        nav_observations.extend(legacy_backup_obs)
            nav_dir = repo / "data" / "nav-history"
            for path in sorted(nav_dir.glob("*.json")) if nav_dir.exists() else []:
                payload = json.loads(path.read_text(encoding="utf-8"))
                code = payload.get("code")
                if code in metas:
                    frame = parse_nav_history(payload.get("history"))
                    quality = validate_nav(frame.assign(fund=code))
                    quality_reports.append(_quality_entry(quality, code, run_id))
                    if quality.errors:
                        continue
                    parts[code] = frame
                    summary_obs = make_nav_history_summary_observation(
                        frame, fund=code, source="hat-ccq-mirror",
                        source_reference=f"{REPO.removesuffix('.git')}/blob/main/{path.relative_to(repo).as_posix()}",
                        observed_at=None, run_id=run_id,
                        data_quality_status="partial", verification_status="third_party_mirror_unverified",
                        quality_flags=["publication_time_unknown"], coverage_type="history_endpoint_summary",
                    )
                    if summary_obs is not None:
                        nav_observations.append(summary_obs)
            provenance_observations.extend(nav_observations)
            nav_written = tables.upsert_nav(parts) if legacy_write else 0
            print(f"NAV: {nav_written} dòng legacy mới/đổi từ {len(parts)} quỹ; provenance summaries={len(nav_observations)}")

        n_obs, n_qual = 0, 0
        if not a.dry_run:
            sidecar = ProvenanceStore(a.data_dir, write=True)
            n_obs = sidecar.append_observations(provenance_observations, run_id=run_id)
            n_qual = sidecar.append_quality_reports(quality_reports, run_id=run_id,
                                                    source="hat-ccq-mirror", recorded_at=utc_now())
        snap_summary = nav_snapshots(repo, commits, a.data_dir, write_legacy=legacy_write, dry_run=a.dry_run)
        print(f"Xong. Tổng {total_changes} stock changes; {n_obs}/{len(provenance_observations)} provenance records appended. Data directory: {a.data_dir}")

        if a.manifest_out:
            checksums = {}
            if not a.dry_run and target_dir.exists():
                for file_path in sorted(p for p in target_dir.rglob("*") if p.is_file()):
                    checksums[file_path.relative_to(target_dir).as_posix()] = _file_sha256(file_path)
            manifest = {
                "source_repo": REPO,
                "license_status": "missing_license_file_in_upstream_repo_publish_blocked_by_default",
                "dry_run": a.dry_run,
                "provenance_only": a.provenance_only,
                "commits_count": len(commits),
                "first_commit": commits[0] if commits else None,
                "last_commit": commits[-1] if commits else None,
                "commit_list_sha256": commit_list_sha256,
                "funds_count": len(metas),
                "stock_changes_count": total_changes,
                "bond_changes_count": int(sum(len(b) for b in bond_batches)),
                "nav_rows_upserted": nav_written,
                "provenance_prepared": len(provenance_observations),
                "provenance_appended": n_obs,
                "quality_reports_appended": n_qual,
                "nav_snapshots": snap_summary,
                "file_sha256": checksums,
            }
            Path(a.manifest_out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.manifest_out).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        if tmp:
            tmp.cleanup()


if __name__ == "__main__":
    main()
