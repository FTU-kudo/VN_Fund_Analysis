#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chạy hằng ngày: lấy danh mục top 10, ghi snapshot và báo cáo thay đổi vị thế có gắn cờ bất định."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diff import CHANGE_COLUMNS, flag_corporate_actions  # noqa: E402
from vnfa.fmarket import (ICT, FmarketClient, parse_allocation, parse_bonds, parse_detail,  # noqa: E402
                          parse_fees, parse_industries, parse_performance, parse_product_row, parse_profile)
from vnfa.notify import send_telegram  # noqa: E402
from vnfa.pipeline import ingest_fund, recent_changes  # noqa: E402
from vnfa.provenance import ProvenanceStore, observations_from_frame  # noqa: E402
from vnfa.quality import (require_valid, validate_allocation, validate_fund_universe,  # noqa: E402
                          validate_holdings, validate_nav)
from vnfa.report import render_report, render_text  # noqa: E402
from vnfa.store import Store, TableStore  # noqa: E402

log = logging.getLogger("vnfa.run")


def _response_observed_at(client) -> str | None:
    """Use the actual response receipt time; fake clients have no source timestamp."""
    event = getattr(client, "last_source_event", None)
    return event.get("observed_at") if event else None


def _record_quality(report, reports: list[dict], *, fund: str | None = None, source: str = "fmarket") -> list[str]:
    item = report.to_dict()
    findings = item.pop("errors") + item.pop("warnings")
    reports.append({**item, "findings": findings, "fund": fund, "source": source})
    return [finding["code"] for finding in findings]


def run(cfg: dict, client, store: Store, observed_date: str, limit: int | None = None):
    if limit is not None and limit <= 0:
        raise ValueError("--limit phải là số nguyên dương")
    try:
        observed_date = pd.Timestamp(observed_date).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"--date không hợp lệ: {observed_date!r}") from exc
    provenance_observations: list[dict] = []
    quality_reports: list[dict] = []
    run_id = getattr(client, "run_id", None)
    raw_products = client.list_products()
    rows = [parse_product_row(r) for r in raw_products]
    catalog_quality = validate_fund_universe(pd.DataFrame(rows))
    catalog_flags = _record_quality(catalog_quality, quality_reports)
    if catalog_quality.errors and store.write:
        try:
            ProvenanceStore(store.root).append_quality_reports(quality_reports, run_id=run_id, source="fmarket")
        except Exception as exc:
            log.error("Không ghi được báo cáo validation danh sách (%s)", type(exc).__name__)
    require_valid(catalog_quality)
    catalog_observed_at = _response_observed_at(client)
    if catalog_observed_at:
        provenance_observations.extend(observations_from_frame(
            pd.DataFrame(rows), dataset="fund_universe", fund=None, source="fmarket",
            source_reference="/products/filter", observed_at=catalog_observed_at,
            key_columns=("code",), run_id=run_id, data_quality_status=catalog_quality.status,
            verification_status="provider_returned_unverified", quality_flags=catalog_flags,
            coverage_type="catalog_page",
        ))
    wanted = set(cfg["funds"]["asset_codes"])
    skip = set(cfg["funds"]["exclude"])
    funds = [r for r in rows if r["asset_code"] in wanted and r["code"] not in skip]
    funds.sort(key=lambda r: r["code"])
    if limit is not None:
        funds = funds[:limit]
    log.info("Danh sách: %d sản phẩm, %d quỹ theo dõi", len(rows), len(funds))

    col = cfg["collect"]
    bond_store = Store(store.root, write=store.write, subdir="bonds")
    tables = TableStore(store.root, write=store.write)
    nav_known = set(tables.read_nav().fund) if col["nav"] else set()
    info = dict(n_total=len(funds), n_ok=0, n_failed=0, n_partial=0, n_changed=0, n_baseline=0,
                n_empty_holdings=0, n_bond_changed=0, n_nav_funds=0, n_nav_history_failed=0,
                n_nav_invalid=0, n_quality_rejected=0, n_quality_errors=0, n_quality_warnings=0,
                n_quality_reports=0, n_provenance_observations=0, n_provenance_write_failed=0,
                failed=[], by_class={}, empty_tables={})
    metas, all_changes, nav_parts = [], [], {}
    for f in funds:
        did_ingest = False
        try:
            detail = client.product_detail(f["id"])
            fund_observed_at = _response_observed_at(client)
            if not isinstance(detail, dict):
                raise ValueError("Chi tiết quỹ không phải JSON object")
            if f["asset_code"] in {"STOCK", "BALANCED"} and not any(
                    key in detail for key in ("productTopHoldingList", "topHoldings")):
                raise ValueError("Thiếu trường danh mục cổ phiếu đã biết; có thể API đã đổi schema")

            meta, holdings = parse_detail(detail)
            meta["mgmt_co"] = meta.get("mgmt_co") or f["mgmt_co"]
            meta["code"] = meta.get("code") or f["code"]
            meta["asset_code"] = meta.get("asset_code") or f["asset_code"]
            code = meta["code"]
            holding_quality = validate_holdings(holdings, coverage_type="top_n")
            holding_flags = _record_quality(holding_quality, quality_reports, fund=code)
            if holding_quality.errors:
                info["n_quality_rejected"] += 1
            require_valid(holding_quality)
            metas.append({k: meta.get(k) for k in ("code", "short_name", "mgmt_co", "asset_code", "management_fee")})

            # Parse các bảng trước khi ghi snapshot chính: lỗi schema không được làm mất
            # sự kiện holdings sau khi snapshot đã bị cập nhật.
            parsed_bonds = parse_bonds(detail) if col["bonds"] else None
            parsed_tables = {}
            for name, enabled, parser in (
                    ("allocation", col["allocation"], parse_allocation),
                    ("industry", col["industry"], parse_industries),
                    ("fees", col["fees"], parse_fees),
                    ("profile", col["profile"], parse_profile),
                    ("performance", col["performance"], parse_performance)):
                if enabled:
                    parsed_tables[name] = parser(detail)

            invalid_optional_tables: set[str] = set()
            allocation_quality = None
            bond_quality = None
            if parsed_bonds is not None:
                bond_quality = validate_holdings(parsed_bonds, coverage_type="top_n")
                _record_quality(bond_quality, quality_reports, fund=code)
                if bond_quality.errors:
                    parsed_bonds = None
                    info["n_quality_rejected"] = info.get("n_quality_rejected", 0) + 1
            if "allocation" in parsed_tables:
                allocation_quality = validate_allocation(parsed_tables["allocation"])
                _record_quality(allocation_quality, quality_reports, fund=code)
                if allocation_quality.errors:
                    invalid_optional_tables.add("allocation")
                    info["n_quality_rejected"] = info.get("n_quality_rejected", 0) + 1

            if fund_observed_at:
                source_reference = f"/products/{f['id']}"
                provenance_observations.extend(observations_from_frame(
                    holdings, dataset="fund_holdings", fund=code, source="fmarket",
                    source_reference=source_reference, observed_at=fund_observed_at,
                    key_columns=("ticker",), effective_date_column="update_date", run_id=run_id,
                    data_quality_status=holding_quality.status,
                    verification_status="provider_returned_unverified", quality_flags=holding_flags,
                    coverage_type="top_n",
                ))
                if parsed_bonds is not None:
                    provenance_observations.extend(observations_from_frame(
                        parsed_bonds, dataset="fund_bond_holdings", fund=code, source="fmarket",
                        source_reference=source_reference, observed_at=fund_observed_at,
                        key_columns=("ticker",), effective_date_column="update_date", run_id=run_id,
                        data_quality_status=bond_quality.status,
                        verification_status="provider_returned_unverified",
                        quality_flags=[finding.code for finding in bond_quality.findings],
                        coverage_type="top_n",
                    ))
                for dataset_name, frame in parsed_tables.items():
                    if dataset_name in invalid_optional_tables or frame.empty:
                        continue
                    key_columns = {
                        "allocation": ("asset_code",), "industry": ("industry",),
                        "fees": ("type", "program", "begin", "end", "unit"),
                        "profile": (), "performance": (),
                    }.get(dataset_name, ())
                    effective_column = "report_date" if dataset_name == "allocation" else None
                    quality_status = allocation_quality.status if dataset_name == "allocation" else "partial"
                    quality_codes = [finding.code for finding in allocation_quality.findings] \
                        if dataset_name == "allocation" and allocation_quality else []
                    provenance_observations.extend(observations_from_frame(
                        frame, dataset=f"fund_{dataset_name}", fund=code, source="fmarket",
                        source_reference=source_reference, observed_at=fund_observed_at,
                        key_columns=key_columns, effective_date_column=effective_column, run_id=run_id,
                        data_quality_status=quality_status,
                        verification_status="provider_returned_unverified", quality_flags=quality_codes,
                        coverage_type="source_returned",
                    ))

            nav_value = None
            if col["nav"] and detail.get("nav") is not None:
                try:
                    candidate = float(detail["nav"])
                except (TypeError, ValueError):
                    candidate = np.nan
                if np.isfinite(candidate) and candidate > 0:
                    nav_value = candidate
                else:
                    info["n_nav_invalid"] += 1
                    log.warning("%s: NAV hiện tại không hợp lệ; bỏ qua snapshot NAV", code)
            if nav_value is not None and fund_observed_at:
                provenance_observations.extend(observations_from_frame(
                    pd.DataFrame([{"date": observed_date, "nav": nav_value}]),
                    dataset="nav_snapshot", fund=code, source="fmarket",
                    source_reference=f"/products/{f['id']}", observed_at=fund_observed_at,
                    key_columns=("date",), run_id=run_id, data_quality_status="valid",
                    verification_status="provider_returned_unverified",
                    quality_flags=["effective_date_unknown"], coverage_type="daily_snapshot",
                ))

            status, ch = ingest_fund(store, meta, holdings, observed_date, cfg)
            did_ingest = status != "empty"
            statuses = {"stock": status}
            info["n_changed"] += status == "changed"
            info["n_baseline"] += status == "baseline"
            if f["asset_code"] in {"STOCK", "BALANCED"} and holdings.empty:
                info["n_empty_holdings"] += 1
            # Giữ thay đổi trong batch trước mọi thao tác tùy chọn có thể lỗi.
            if not ch.empty:
                all_changes.append(ch)

            if col["bonds"]:
                bstatus, bch = ingest_fund(bond_store, meta, parsed_bonds, observed_date, cfg, asset_class="BOND")
                info["n_bond_changed"] += bstatus == "changed"
                statuses["bond"] = bstatus
                if not bch.empty:
                    all_changes.append(bch)

            for name, frame in parsed_tables.items():
                if name in invalid_optional_tables:
                    continue
                if frame.empty:
                    info["empty_tables"][name] = info["empty_tables"].get(name, 0) + 1
                else:
                    tables.append_if_changed(name, code, frame, observed_date)

            if nav_value is not None:
                tables.append_nav_snapshot(code, nav_value, observed_date)
            if col["nav"] and hasattr(client, "nav_history") and code not in nav_known:
                # NAV history là dữ liệu bổ sung; lỗi endpoint này không được biến một
                # lần lấy holdings thành thất bại sau khi snapshot đã ghi.
                try:
                    nav_frame = client.nav_history(f["id"])
                    nav_quality = validate_nav(nav_frame.assign(fund=code))
                    nav_flags = _record_quality(nav_quality, quality_reports, fund=code)
                    require_valid(nav_quality)
                    nav_parts[code] = nav_frame
                    info["n_nav_funds"] += 1
                    nav_observed_at = _response_observed_at(client)
                    if nav_observed_at:
                        provenance_observations.extend(observations_from_frame(
                            nav_frame, dataset="nav_history", fund=code, source="fmarket",
                            source_reference="/product/get-nav-history", observed_at=nav_observed_at,
                            key_columns=("date",), effective_date_column="date", run_id=run_id,
                            data_quality_status=nav_quality.status,
                            verification_status="provider_returned_unverified", quality_flags=nav_flags,
                            coverage_type="history_endpoint",
                        ))
                except Exception as e:
                    info["n_nav_history_failed"] += 1
                    log.warning("%s: tải NAV history lỗi (%s: %s)", code, type(e).__name__, e)

            info["n_ok"] += 1
            info["by_class"][f["asset_code"]] = info["by_class"].get(f["asset_code"], 0) + 1
            log.info("%-10s %-8s %s | %d mã CP, %d thay đổi", code, meta.get("asset_code"), statuses, len(holdings), len(ch))
        except Exception as e:  # một quỹ lỗi không làm hỏng cả lần chạy
            info["n_failed"] += 1
            if did_ingest:
                info["n_partial"] += 1
            info["failed"].append(f["code"])
            log.warning("%s lỗi: %s: %s", f["code"], type(e).__name__, e)

    try:
        info["nav_rows_new"] = tables.upsert_nav(nav_parts)
    except Exception as e:
        # Lỗi dữ liệu NAV bổ sung không được chặn việc ghi nhật ký thay đổi holdings.
        info["nav_rows_new"] = 0
        info["n_nav_history_failed"] += len(nav_parts)
        log.error("Không cập nhật được NAV history (%s: %s)", type(e).__name__, e)
    changes = pd.concat(all_changes, ignore_index=True) if all_changes else pd.DataFrame(columns=CHANGE_COLUMNS)
    prior_changes = store.read_changes()
    if not prior_changes.empty:
        prior_today = prior_changes[prior_changes.observed_date.astype(str) == observed_date]
        if not prior_today.empty:
            changes = pd.concat([prior_today, changes], ignore_index=True)
            changes = changes.drop_duplicates(["observed_date", "fund", "ticker"], keep="last")
    ca = cfg["corporate_action"]
    stock = changes[changes.asset_class != "BOND"] if not changes.empty else changes
    bonds = changes[changes.asset_class == "BOND"] if not changes.empty else changes
    stock, flagged_history = flag_corporate_actions(
        stock, recent_changes(store, observed_date, ca["lookback_days"]),
        min_funds=ca["min_funds"], spread=ca["spread_pct"] / 100.0,
        min_pct=ca["min_change_pct"] / 100.0, update_history=True)
    changes = pd.concat([stock, bonds], ignore_index=True) if not bonds.empty else stock
    # Nếu nhiều kỳ công bố lệch ngày xác nhận cùng một corporate-action heuristic,
    # cập nhật cả event cũ để reaction report không tiếp tục cộng nhầm chúng là active BUY/SELL.
    journal_changes = pd.concat([flagged_history, changes], ignore_index=True) if not flagged_history.empty else changes
    # Ưu tiên journal sự kiện trước metadata phụ để một lỗi ghi danh bạ không làm mất thay đổi.
    store.append_changes(journal_changes)
    store.upsert_funds(metas, observed_date)
    # Chạy lại cùng ngày không được ghi đè báo cáo có sự kiện bằng "không có thay đổi".
    saved = store.read_changes()
    if not saved.empty:
        same_day = saved[saved.observed_date.astype(str) == observed_date]
        if not same_day.empty:
            changes = same_day

    info["n_quality_reports"] = len(quality_reports)
    info["n_quality_errors"] = sum(
        finding["count"] for report in quality_reports for finding in report["findings"]
        if finding["severity"] == "error")
    info["n_quality_warnings"] = sum(
        finding["count"] for report in quality_reports for finding in report["findings"]
        if finding["severity"] == "warning")
    try:
        provenance = ProvenanceStore(store.root, write=store.write)
        info["n_provenance_observations"] = provenance.append_observations(
            provenance_observations, run_id=run_id)
        provenance.append_quality_reports(quality_reports, run_id=run_id, source="fmarket")
    except Exception as exc:
        info["n_provenance_write_failed"] = 1
        info["provenance_error_type"] = type(exc).__name__
        log.error("Không ghi được provenance/quality sidecar (%s)", type(exc).__name__)
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
    info, changes = None, None
    try:
        info, changes = run(cfg, client, store, date, a.limit)
    except Exception as e:
        log.error("Không lấy được danh sách quỹ: %s: %s", type(e).__name__, e)
    source_event_write_failed = False
    if not a.dry_run and client.source_events:
        try:
            event_store = ProvenanceStore(a.data_dir)
            source_event_rows = event_store.append_source_events(client.source_events, run_id=client.run_id)
            if info is not None:
                info["n_source_events"] = source_event_rows
        except Exception as exc:
            source_event_write_failed = True
            log.error("Không ghi được source event (%s)", type(exc).__name__)
            if info is not None:
                info["n_source_event_write_failed"] = 1
    if info is None:
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

    # Ingestion cốt lõi vẫn được giữ; workflow đỏ nếu provenance/quality ledger không ghi được.
    if source_event_write_failed or info.get("n_provenance_write_failed", 0):
        log.error("Không ghi được đầy đủ provenance/quality; kiểm tra sidecar schema hoặc quyền ghi")
        return 1
    # Thất bại nếu quá 30% quỹ lỗi hoặc không quỹ nào lấy được => workflow báo đỏ
    if info["n_total"] == 0 or info["n_ok"] == 0 or info["n_failed"] > 0.3 * info["n_total"]:
        log.error("Quá nhiều quỹ lỗi: %s/%s", info["n_failed"], info["n_total"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
