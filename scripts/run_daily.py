#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chạy hằng ngày: lấy danh mục top 10 của các quỹ, ghi snapshot khi đổi, phát hiện MUA/BÁN, tạo báo cáo."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diff import CHANGE_COLUMNS, flag_corporate_actions  # noqa: E402
from vnfa.fmarket import (ICT, FmarketClient, parse_allocation, parse_bonds, parse_detail,  # noqa: E402
                          parse_fees, parse_industries, parse_performance, parse_product_row, parse_profile)
from vnfa.notify import send_telegram  # noqa: E402
from vnfa.pipeline import ingest_fund, recent_changes  # noqa: E402
from vnfa.report import render_report, render_text  # noqa: E402
from vnfa.store import Store, TableStore  # noqa: E402

log = logging.getLogger("vnfa.run")


def run(cfg: dict, client, store: Store, observed_date: str, limit: int | None = None):
    rows = [parse_product_row(r) for r in client.list_products()]
    wanted = set(cfg["funds"]["asset_codes"])
    skip = set(cfg["funds"]["exclude"])
    funds = [r for r in rows if r["asset_code"] in wanted and r["code"] not in skip]
    funds.sort(key=lambda r: r["code"])
    if limit:
        funds = funds[:limit]
    log.info("Danh sách: %d sản phẩm, %d quỹ theo dõi", len(rows), len(funds))

    col = cfg["collect"]
    bond_store = Store(store.root, write=store.write, subdir="bonds")
    tables = TableStore(store.root, write=store.write)
    nav_known = set(tables.read_nav().fund) if col["nav"] else set()
    info = dict(n_total=len(funds), n_ok=0, n_failed=0, n_changed=0, n_baseline=0, n_bond_changed=0,
                n_nav_funds=0, failed=[], by_class={})
    metas, all_changes, nav_parts = [], [], {}
    for f in funds:
        try:
            detail = client.product_detail(f["id"])
            meta, holdings = parse_detail(detail)
            meta["mgmt_co"] = meta.get("mgmt_co") or f["mgmt_co"]
            meta["code"] = meta.get("code") or f["code"]
            code = meta["code"]
            status, ch = ingest_fund(store, meta, holdings, observed_date, cfg)
            statuses = {"stock": status}
            if col["bonds"]:
                bstatus, bch = ingest_fund(bond_store, meta, parse_bonds(detail), observed_date, cfg, asset_class="BOND")
                info["n_bond_changed"] += bstatus == "changed"
                statuses["bond"] = bstatus
                if not bch.empty:
                    all_changes.append(bch)
            if col["allocation"]:
                tables.append_if_changed("allocation", code, parse_allocation(detail), observed_date)
            if col["industry"]:
                tables.append_if_changed("industry", code, parse_industries(detail), observed_date)
            if col["fees"]:
                tables.append_if_changed("fees", code, parse_fees(detail), observed_date)
            if col["profile"]:
                tables.append_if_changed("profile", code, parse_profile(detail), observed_date)
            if col["performance"]:
                tables.append_if_changed("performance", code, parse_performance(detail), observed_date)
            if col["nav"]:
                if detail.get("nav") is not None:                       # NAV hiện tại: chụp hằng ngày
                    tables.append_nav_snapshot(code, float(detail["nav"]), observed_date)
                if hasattr(client, "nav_history") and code not in nav_known:   # lịch sử dài: chỉ tải lần đầu
                    nav_parts[code] = client.nav_history(f["id"])
                    info["n_nav_funds"] += 1
            metas.append({k: meta.get(k) for k in ("code", "short_name", "mgmt_co", "asset_code", "management_fee")})
            info["n_ok"] += 1
            info["n_changed"] += status == "changed"
            info["n_baseline"] += status == "baseline"
            if not ch.empty:
                all_changes.append(ch)
            log.info("%-10s %-8s %s | %d mã CP, %d thay đổi", code, meta.get("asset_code"), statuses, len(holdings), len(ch))
        except Exception as e:  # một quỹ lỗi không làm hỏng cả lần chạy
            info["n_failed"] += 1
            info["failed"].append(f["code"])
            log.warning("%s lỗi: %s: %s", f["code"], type(e).__name__, e)

    info["nav_rows_new"] = tables.upsert_nav(nav_parts)
    changes = pd.concat(all_changes, ignore_index=True) if all_changes else pd.DataFrame(columns=CHANGE_COLUMNS)
    ca = cfg["corporate_action"]
    stock = changes[changes.asset_class != "BOND"] if not changes.empty else changes
    bonds = changes[changes.asset_class == "BOND"] if not changes.empty else changes
    stock = flag_corporate_actions(
        stock, recent_changes(store, observed_date, ca["lookback_days"]),
        min_funds=ca["min_funds"], spread=ca["spread_pct"] / 100.0, min_pct=ca["min_change_pct"] / 100.0)
    changes = pd.concat([stock, bonds], ignore_index=True) if not bonds.empty else stock
    store.upsert_funds(metas, observed_date)
    store.append_changes(changes)
    return info, changes[changes.asset_class != "BOND"] if not changes.empty else changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--reports-dir", default=str(ROOT / "reports"))
    ap.add_argument("--limit", type=int, default=None, help="chỉ chạy N quỹ đầu (gỡ lỗi)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (mặc định: hôm nay theo giờ Việt Nam)")
    ap.add_argument("--dry-run", action="store_true", help="không ghi file, không gửi thông báo")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(a.config)
    date = a.date or datetime.now(ICT).date().isoformat()
    store = Store(a.data_dir, write=not a.dry_run)
    client = FmarketClient(delay=cfg["http"]["delay_seconds"], timeout=cfg["http"]["timeout_seconds"])
    try:
        info, changes = run(cfg, client, store, date, a.limit)
    except Exception as e:
        log.error("Không lấy được danh sách quỹ: %s: %s", type(e).__name__, e)
        return 1

    md = render_report(date, changes, info, cfg)
    if a.dry_run:
        print(md)
    else:
        rd = Path(a.reports_dir)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "latest.md").write_text(md, encoding="utf-8")
        if not changes.empty or info["n_baseline"]:
            (rd / f"{date}.md").write_text(md, encoding="utf-8")
        Path(a.data_dir, "last_run.json").write_text(json.dumps({"date": date, **info}, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
        if not changes.empty:
            send_telegram(render_text(date, changes, info, cfg))

    # Thất bại nếu quá 30% quỹ lỗi hoặc không quỹ nào lấy được => workflow báo đỏ
    if info["n_total"] == 0 or info["n_ok"] == 0 or info["n_failed"] > 0.3 * info["n_total"]:
        log.error("Quá nhiều quỹ lỗi: %s/%s", info["n_failed"], info["n_total"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
