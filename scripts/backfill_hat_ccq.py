#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nạp lịch sử danh mục từ git log của repo huyhn-ba-po/hat-ccq (giấy phép MIT).

Repo đó commit dữ liệu Fmarket hằng ngày từ 16/05/2026; mỗi commit chứa top 10 cổ phiếu của từng quỹ.
Script phát lại các commit theo thứ tự thời gian qua cùng pipeline với run_daily.py, nên data/holdings/*.csv
và data/changes.csv có lịch sử ngay từ đầu. observed_date = ngày commit (xấp xỉ ngày crawl của repo đó).

Cần: git và kết nối tới github.com. Chạy một lần:  python scripts/backfill_hat_ccq.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diff import CHANGE_COLUMNS, flag_corporate_actions  # noqa: E402
from vnfa.fmarket import (parse_allocation, parse_bonds, parse_detail, parse_fees,  # noqa: E402
                          parse_industries, parse_nav_history, parse_profile)
from vnfa.pipeline import ingest_fund  # noqa: E402
from vnfa.store import Store, TableStore  # noqa: E402

REPO = "https://github.com/huyhn-ba-po/hat-ccq.git"


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


def nav_snapshots(repo: Path, commits, data_dir: str) -> None:
    """NAV/CCQ ghi lại ở từng commit (trường `nav` của chi tiết quỹ). API lịch sử NAV của hat-ccq có thể dừng ở 2025-10-31,
    nên đây là cách có NAV 2026. Ngày = ngày commit (xấp xỉ ngày công bố NAV). Chỉ giữ điểm khi NAV đổi."""
    rows = {}
    for sha, date in commits:
        for d in commit_details(repo, sha):
            nav = d.get("nav")
            if d.get("code") and nav is not None:
                rows.setdefault(d["code"], []).append((date, float(nav)))
    out = []
    for code, seq in rows.items():
        prev = None
        for date, nav in seq:
            if prev is None or abs(nav - prev) > 1e-9:
                out.append((code, date, nav))
                prev = nav
    df = pd.DataFrame(out, columns=["fund", "date", "nav"]).sort_values(["fund", "date"])
    p = Path(data_dir) / "nav_snapshots.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, sep=";", index=False)
    print(f"nav_snapshots.csv: {len(df)} điểm, {df.fund.nunique()} quỹ, {df.date.min()} → {df.date.max()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--nav-snapshots-only", action="store_true",
                    help="chỉ trích NAV chụp hằng ngày (nav_snapshots.csv), không phát lại danh mục")
    ap.add_argument("--src", default=None, help="đường dẫn bản clone sẵn của hat-ccq (bỏ qua bước clone)")
    a = ap.parse_args()

    cfg = load_config(a.config)
    store = Store(a.data_dir)
    bond_store = Store(a.data_dir, subdir="bonds")
    tables = TableStore(a.data_dir)
    col = cfg["collect"]
    wanted, skip = set(cfg["funds"]["asset_codes"]), set(cfg["funds"]["exclude"])
    ca = cfg["corporate_action"]

    tmp = None
    if a.src:
        repo = Path(a.src)
    else:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name) / "hat-ccq"
        print("Đang clone", REPO)
        subprocess.run(["git", "clone", "--quiet", REPO, str(repo)], check=True)

    log = git(repo, "log", "--reverse", "--format=%H %ad", "--date=short", "--", "data/details").decode().split("\n")
    commits = [l.split() for l in log if l.strip()]
    if a.nav_snapshots_only:
        nav_snapshots(repo, commits, a.data_dir)
        return
    print(f"{len(commits)} commit có dữ liệu chi tiết quỹ")
    total_changes, history = 0, pd.DataFrame(columns=CHANGE_COLUMNS)
    metas = {}
    for sha, date in commits:
        day = []
        for d in commit_details(repo, sha):
            meta, holdings = parse_detail(d)
            if meta.get("asset_code") not in wanted or meta.get("code") in skip:
                continue
            code = meta["code"]
            _, ch = ingest_fund(store, meta, holdings, date, cfg)
            if col["bonds"]:
                _, bch = ingest_fund(bond_store, meta, parse_bonds(d), date, cfg, asset_class="BOND")
                if not bch.empty:
                    store.append_changes(bch)
            for name, fn in (("allocation", parse_allocation), ("industry", parse_industries),
                             ("fees", parse_fees), ("profile", parse_profile)):
                if col[name]:
                    tables.append_if_changed(name, code, fn(d), date)
            metas[meta["code"]] = {k: meta.get(k) for k in ("code", "short_name", "mgmt_co", "asset_code", "management_fee")}
            if not ch.empty:
                day.append(ch)
        if day:
            cur = pd.concat(day, ignore_index=True)
            recent = history[pd.to_datetime(history.observed_date) >= pd.Timestamp(date) - pd.Timedelta(days=ca["lookback_days"])] \
                if not history.empty else history
            cur = flag_corporate_actions(cur, recent, ca["min_funds"], ca["spread_pct"] / 100, ca["min_change_pct"] / 100)
            store.append_changes(cur)
            history = pd.concat([history, cur], ignore_index=True)
            total_changes += len(cur)
            print(f"{date}: {len(cur)} thay đổi")
    store.upsert_funds(list(metas.values()), commits[-1][1])
    if col["nav"]:
        parts = {}
        for f in sorted((repo / "data" / "nav-history").glob("*.json")):
            js = json.loads(f.read_text(encoding="utf-8"))
            if js.get("code") in metas:
                parts[js["code"]] = parse_nav_history(js.get("history"))
        print(f"NAV: {tables.upsert_nav(parts)} dòng mới từ {len(parts)} quỹ")
    nav_snapshots(repo, commits, a.data_dir)
    print(f"Xong. Tổng {total_changes} thay đổi. Dữ liệu tại {a.data_dir}")
    if tmp:
        tmp.cleanup()


if __name__ == "__main__":
    main()
