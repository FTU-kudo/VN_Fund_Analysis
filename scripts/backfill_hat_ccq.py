#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nạp lịch sử danh mục từ git log của repo huyhn-ba-po/hat-ccq (giấy phép MIT).

Repo đó commit dữ liệu Fmarket hằng ngày từ 16/05/2026; mỗi commit chứa top 10 cổ phiếu của từng quỹ.
Script phát lại các commit theo thứ tự thời gian qua cùng pipeline với run_daily.py, nên data/holdings/*.csv
và data/changes.csv có lịch sử ngay từ đầu. Ngày commit chỉ là xấp xỉ crawl, không phải publication timestamp;
provenance backfill được gắn observed_date_basis=approximate và bị loại khỏi PIT filtering mặc định.

Cần: git và kết nối tới github.com. Chạy một lần:  python scripts/backfill_hat_ccq.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diff import CHANGE_COLUMNS, flag_corporate_actions  # noqa: E402
from vnfa.fmarket import (parse_allocation, parse_bonds, parse_detail, parse_fees,  # noqa: E402
                          parse_industries, parse_nav_history, parse_profile)
from vnfa.pipeline import ingest_fund  # noqa: E402
from vnfa.provenance import ProvenanceStore, make_observation, observations_from_frame, utc_now  # noqa: E402
from vnfa.quality import require_valid, validate_allocation, validate_holdings, validate_nav  # noqa: E402
from vnfa.store import Store, TableStore  # noqa: E402

REPO = "https://github.com/huyhn-ba-po/hat-ccq.git"


def _quality_entry(report, fund: str | None, run_id: str) -> dict:
    item = report.to_dict()
    findings = item.pop("errors") + item.pop("warnings")
    return {**item, "fund": fund, "findings": findings, "source": "hat-ccq-mirror", "run_id": run_id}


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


def nav_snapshots(repo: Path, commits, data_dir: str, *, write_legacy: bool = True) -> None:
    """Keep NAV snapshots and append mirror provenance without treating commit date as exact.

    `write_legacy=False` appends only to the research ledger, enabling safe historical
    backfills after the legacy daily store has advanced beyond the mirror dates.
    """
    rows = {}
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
                    quality_flags=["approximate_observation_date"], coverage_type="daily_mirror_snapshot",
                ))
                prev = nav
    df = pd.DataFrame(out, columns=["fund", "date", "nav"])
    p = Path(data_dir) / "nav_snapshots.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    if write_legacy and p.exists():
        old = pd.read_csv(p, sep=";", dtype={"fund": str, "date": str})
        if not {"fund", "date", "nav"}.issubset(old.columns):
            raise ValueError("nav_snapshots.csv thiếu một trong các cột fund/date/nav; không ghi đè dữ liệu")
        # Keep all existing snapshots; on a key conflict existing data retains priority.
        df = pd.concat([df, old[["fund", "date", "nav"]]], ignore_index=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df.date, errors="raise").dt.strftime("%Y-%m-%d")
        df["nav"] = pd.to_numeric(df.nav, errors="raise")
        if not np.isfinite(df.nav.astype(float)).all() or (df.nav.astype(float) <= 0).any():
            raise ValueError("NAV snapshot phải là số dương, hữu hạn; không ghi đè dữ liệu")
        df = df.drop_duplicates(["fund", "date"], keep="last").sort_values(["fund", "date"])
    run_id = uuid.uuid4().hex
    quality = validate_nav(df)
    require_valid(quality)
    sidecar = ProvenanceStore(data_dir)
    sidecar.append_observations(observations, run_id=run_id)
    sidecar.append_quality_reports([_quality_entry(quality, None, run_id)], run_id=run_id,
                                   source="hat-ccq-mirror", recorded_at=utc_now())
    if write_legacy:
        temporary = p.with_name(p.name + ".tmp")
        try:
            df.to_csv(temporary, sep=";", index=False)
            temporary.replace(p)
        finally:
            temporary.unlink(missing_ok=True)
    first, last = (df.date.min(), df.date.max()) if not df.empty else ("—", "—")
    mode = "updated" if write_legacy else "legacy file unchanged"
    print(f"nav_snapshots.csv: {len(df)} mirror rows reviewed ({mode}), {df.fund.nunique()} funds, {first} → {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--nav-snapshots-only", action="store_true",
                    help="chỉ trích NAV chụp hằng ngày (nav_snapshots.csv), không phát lại danh mục")
    ap.add_argument("--provenance-only", action="store_true",
                    help="append lịch sử vào research_observations.csv mà không sửa CSV legacy; dùng cho backfill sau khi daily store đã tiến tới hiện tại")
    ap.add_argument("--src", default=None, help="đường dẫn bản clone sẵn của hat-ccq (bỏ qua bước clone)")
    a = ap.parse_args()

    cfg = load_config(a.config)
    legacy_write = not a.provenance_only
    store = Store(a.data_dir, write=legacy_write)
    bond_store = Store(a.data_dir, write=legacy_write, subdir="bonds")
    tables = TableStore(a.data_dir, write=legacy_write)
    col = cfg["collect"]
    wanted, skip = set(cfg["funds"]["asset_codes"]), set(cfg["funds"]["exclude"])
    ca = cfg["corporate_action"]
    run_id = uuid.uuid4().hex
    provenance_observations, quality_reports = [], []

    tmp = None
    if a.src:
        repo = Path(a.src)
    else:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name) / "hat-ccq"
        print("Đang clone", REPO)
        subprocess.run(["git", "clone", "--quiet", REPO, str(repo)], check=True)

    log = git(repo, "log", "--reverse", "--format=%H %ad", "--date=short", "--", "data/details").decode().split("\n")
    commits = [line.split() for line in log if line.strip()]
    if a.nav_snapshots_only:
        nav_snapshots(repo, commits, a.data_dir, write_legacy=legacy_write)
        return
    print(f"{len(commits)} commit có dữ liệu chi tiết quỹ")
    total_changes, history = 0, pd.DataFrame(columns=CHANGE_COLUMNS)
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
            holding_hash = TableStore._hash(holdings) if not holdings.empty else None
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
                bond_quality = validate_holdings(bond_holdings, coverage_type="top_n")
                quality_reports.append(_quality_entry(bond_quality, code, run_id))
                if not bond_quality.errors:
                    if backdated:
                        bond_status, bond_changes = "provenance_only", pd.DataFrame(columns=CHANGE_COLUMNS)
                    else:
                        bond_status, bond_changes = ingest_fund(
                            bond_store, meta, bond_holdings, date, cfg, asset_class="BOND")
                    bond_hash = TableStore._hash(bond_holdings) if not bond_holdings.empty else None
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
                        store.append_changes(bond_changes)
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
            current = flag_corporate_actions(current, recent, ca["min_funds"], ca["spread_pct"] / 100,
                                             ca["min_change_pct"] / 100)
            store.append_changes(current)
            history = pd.concat([history, current], ignore_index=True)
            total_changes += len(current)
            print(f"{date}: {len(current)} thay đổi")
    if legacy_write and not legacy_backfill_occurred and metas and commits:
        store.upsert_funds(list(metas.values()), commits[-1][1])
    elif legacy_backfill_occurred:
        print(f"Legacy CSV unchanged for {n_legacy_skipped} backdated fund snapshots; provenance sidecar will retain them.")
    if col["nav"]:
        parts = {}
        nav_observations = []
        if legacy_write:
            existing_nav = tables.read_nav()
            if not existing_nav.empty:
                if not {"fund", "date", "nav"}.issubset(existing_nav.columns):
                    raise ValueError("nav.csv schema không hợp lệ; không ghi đè NAV legacy")
                for code, old_part in existing_nav.groupby("fund", sort=False):
                    old_quality = validate_nav(old_part)
                    quality_reports.append(_quality_entry(old_quality, str(code), run_id))
                    nav_observations.extend(observations_from_frame(
                        old_part, dataset="nav_history", fund=str(code), source="legacy_nav_csv",
                        source_reference="data/nav.csv", observed_at=None,
                        key_columns=("date",), effective_date_column="date", run_id=run_id,
                        data_quality_status=old_quality.status, verification_status="legacy_source_unknown",
                        quality_flags=[finding.code for finding in old_quality.findings]
                            + ["availability_time_unknown"], coverage_type="legacy_materialized_history",
                    ))
                # Preserve the pre-backfill values in the sidecar before an upsert can replace a key.
                ProvenanceStore(a.data_dir).append_observations(nav_observations, run_id=run_id)
        for path in sorted((repo / "data" / "nav-history").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            code = payload.get("code")
            if code in metas:
                frame = parse_nav_history(payload.get("history"))
                quality = validate_nav(frame.assign(fund=code))
                quality_reports.append(_quality_entry(quality, code, run_id))
                if quality.errors:
                    continue
                parts[code] = frame
                nav_observations.extend(observations_from_frame(
                    frame, dataset="nav_history", fund=code, source="hat-ccq-mirror",
                    source_reference=f"{REPO.removesuffix('.git')}/blob/main/{path.relative_to(repo).as_posix()}", observed_at=None,
                    key_columns=("date",), effective_date_column="date", run_id=run_id,
                    data_quality_status="partial", verification_status="third_party_mirror_unverified",
                    quality_flags=["publication_time_unknown"], coverage_type="history_endpoint",
                ))
        provenance_observations.extend(nav_observations)
        nav_written = tables.upsert_nav(parts) if legacy_write else 0
        print(f"NAV: {nav_written} dòng legacy mới/đổi từ {len(parts)} quỹ; provenance rows={len(nav_observations)}")
    sidecar = ProvenanceStore(a.data_dir)
    n_obs = sidecar.append_observations(provenance_observations, run_id=run_id)
    sidecar.append_quality_reports(quality_reports, run_id=run_id, source="hat-ccq-mirror", recorded_at=utc_now())
    nav_snapshots(repo, commits, a.data_dir, write_legacy=legacy_write)
    print(f"Xong. Tổng {total_changes} changes; {n_obs} provenance records appended. Data directory: {a.data_dir}")
    if tmp:
        tmp.cleanup()


if __name__ == "__main__":
    main()
