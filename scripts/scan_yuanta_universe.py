#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan all stocks across Vietnam's 3 exchanges (HOSE, HNX, UPCOM) from Yuanta YSradar.

Collects all 5 research pages per stock (modeled on the ACB reference endpoints):
  - Trang 1 (Tổng quan): rating_info, profile_info, shareholders, stock_info, last_bid_offer,
    list_transaction, list_stock_info, list_stock_rating_info, peers (top 1000 + industry P/E)
  - Trang 2 (Dữ liệu giao dịch): price_history (2010–present), price_history_foreign (2025–present),
    and 3 foreign screener chart series (ngay_giao_dich, gtgd_binh_quan_chart, ty_le_von_hoa_chart)
  - Trang 3 (Tương quan trong ngành): computed across all scanned stocks from Trang 4 data, split into
    3 macro groups (Ngân hàng, Tài chính phi ngân hàng, Phi tài chính — separating Bất động sản out of L1 Tài chính)
  - Trang 4 (Báo cáo tài chính): full CSTC & BCTT quarterly history (Q1/2010–present) + 3-group charts
  - Trang 5 (Tin tức & sự kiện): corporate articles/events (2010-01-01–present)

Outputs:
  1. Out-of-Git Parquet data lake (`data/yuanta/`, gitignored)
  2. Compact static browser bundles (`dashboard/data/yuanta/`, gitignored, deployable to GitHub Pages)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.provenance import SOURCE_COLUMNS  # noqa: E402
from vnfa.store import SEP, atomic_write_csv  # noqa: E402
from vnfa.yuanta import (  # noqa: E402
    EXCHANGE_CANONICAL_MAP,
    YuantaClient,
    attach_stock_peer_correlation,
    build_financial_charts,
    classify_sector_taxonomy,
    compute_peer_group_medians,
    extract_latest_metrics_for_correlation,
    normalize_symbol,
    write_gzip_json,
)

log = logging.getLogger("scan_yuanta")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_for_json(obj: Any) -> Any:
    """Recursively convert NaN/Inf/numpy scalars/Timestamps into strict JSON-safe primitives."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (date, datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_for_json(v) for v in obj]
    if hasattr(obj, "item"):
        try:
            return clean_for_json(obj.item())
        except Exception:  # noqa: BLE001
            return None
    if pd.isna(obj):
        return None
    return str(obj)


def _wide_financial_table(df: pd.DataFrame, max_periods: int = 68) -> dict[str, Any]:
    """Convert tidy long CSTC/BCTT DataFrame into compact wide matrix for Trang 4 UI."""
    if df is None or df.empty:
        return {"periods": [], "rows": []}
    periods = sorted(df["period"].unique().tolist(), reverse=True)[:max_periods]
    period_set = set(periods)
    sub = df[df["period"].isin(period_set)].copy()
    grouped_rows: list[dict[str, Any]] = []
    for (comp_ord, field_ord, comp_name, fname, unit), grp in sub.groupby(
        ["component_order", "field_order", "report_component", "field_name", "unit"],
        dropna=False,
        sort=True,
    ):
        val_map = {}
        for rec in grp.itertuples(index=False):
            if rec.value is not None and not pd.isna(rec.value):
                val_map[str(rec.period)] = round(float(rec.value), 4)
        grouped_rows.append({
            "component": None if pd.isna(comp_name) else str(comp_name),
            "field_name": str(fname),
            "unit": None if pd.isna(unit) else str(unit),
            "values": val_map,
        })
    return {"periods": periods, "rows": grouped_rows}


def _extract_overview_summary(
    symbol: str,
    seed: dict[str, Any],
    overview_raw: dict[str, Any],
    fin_fields: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract normalized master record + sector taxonomy from Trang 1 payloads."""
    r_info = overview_raw.get("stock_rating_info")
    if isinstance(r_info, list):
        r_info = r_info[0] if r_info else {}
    r_info = r_info if isinstance(r_info, dict) else {}

    p_info = overview_raw.get("profile_info")
    if isinstance(p_info, list):
        p_info = p_info[0] if p_info else {}
    p_info = p_info if isinstance(p_info, dict) else {}

    s_info = overview_raw.get("stock_info")
    if isinstance(s_info, list):
        s_info = s_info[0] if s_info else {}
    s_info = s_info if isinstance(s_info, dict) else {}

    board_list = overview_raw.get("list_stock_info")
    board = board_list[0] if isinstance(board_list, list) and board_list else (board_list if isinstance(board_list, dict) else {})

    rating_list = overview_raw.get("list_stock_rating_info")
    rating_row = rating_list[0] if isinstance(rating_list, list) and rating_list else (rating_list if isinstance(rating_list, dict) else {})

    icb_l1 = str(r_info.get("ICBLevel1") or p_info.get("ICBName1") or p_info.get("IndustryLevel1") or "").strip() or None
    icb_l2 = str(r_info.get("ICBLevel2") or p_info.get("ICBName2") or p_info.get("IndustryLevel2") or "").strip() or None
    icb_l3 = str(r_info.get("ICBLevel3") or p_info.get("ICBName3") or p_info.get("IndustryLevel3") or "").strip() or None
    icb_code = str(r_info.get("ICBCode") or p_info.get("ICBCode") or "").strip() or None
    sector_name = (
        icb_l3 or icb_l2
        or str(r_info.get("SectorName") or p_info.get("SectorName") or seed.get("sector_board") or "").strip()
        or None
    )

    taxonomy = classify_sector_taxonomy(
        sector_name=sector_name,
        icb_l1=icb_l1,
        icb_l2=icb_l2,
        icb_l3=icb_l3,
        icb_code=icb_code,
        financial_fields=fin_fields,
    )

    ex_raw = str(
        seed.get("exchange")
        or board.get("Exchange")
        or s_info.get("Exchange")
        or r_info.get("Exchange")
        or "HOSE"
    ).strip().upper()
    exchange = EXCHANGE_CANONICAL_MAP.get(ex_raw, ex_raw)

    def _num(*candidates: Any) -> float | None:
        for c in candidates:
            if c is None or isinstance(c, bool) or c == "":
                continue
            try:
                f = float(c)
                if math.isfinite(f):
                    return f
            except (TypeError, ValueError):
                continue
        return None

    master = {
        "symbol": symbol,
        "exchange": exchange,
        "company_name": (
            str(p_info.get("CompanyName") or p_info.get("FullName") or r_info.get("CompanyNameVi")
                or board.get("CompanyNameVi") or seed.get("company_name") or "").strip() or None
        ),
        "sector_name": sector_name,
        "icb_l1": icb_l1,
        "icb_l2": icb_l2,
        "icb_l3": icb_l3,
        "icb_code": icb_code,
        "macro_group": taxonomy["macro_group"],
        "macro_group_label": taxonomy["macro_group_label"],
        "sub_group": taxonomy["sub_group"],
        "sub_group_label": taxonomy["sub_group_label"],
        "peer_group_key": taxonomy["peer_group_key"],
        "split_from_financial_l1": taxonomy["split_from_financial_l1"],
        "last_price": _num(s_info.get("MatchPrice"), s_info.get("LastMP"), board.get("LastMP"), seed.get("last_price")),
        "ref_price": _num(s_info.get("RefPrice"), board.get("RefPrice"), seed.get("ref_price")),
        "market_cap": _num(s_info.get("MarketCap"), r_info.get("MarketCap"), board.get("MarketCap")),
        "pe": _num(s_info.get("PE"), r_info.get("PE"), board.get("PE")),
        "pb": _num(s_info.get("PB"), r_info.get("PB"), board.get("PB")),
        "roe": _num(r_info.get("ROE"), s_info.get("ROE")),
        "roa": _num(r_info.get("ROA"), s_info.get("ROA")),
        "stock_rating": _num(rating_row.get("StockRating"), r_info.get("StockRating")),
        "fundamental_score": _num(rating_row.get("Fundamental"), r_info.get("Fundamental")),
        "technical_score": _num(rating_row.get("RelativeStrength"), r_info.get("RelativeStrength")),
    }
    return master, taxonomy


def scan_single_ticker(
    client: YuantaClient,
    seed: dict[str, Any],
    *,
    modules: set[str],
    include_intraday: bool,
    from_date: str,
    foreign_from_date: str,
    to_date: str,
    from_year: int,
    cached_last_trading_date: str | None = None,
) -> dict[str, Any]:
    symbol = seed["symbol"]
    scanned_at = _utc_now()
    overview_data: dict[str, Any] = {}
    if "overview" in modules:
        overview_data = client.fetch_page1_overview(symbol, include_intraday=include_intraday, num_peers=1000)

    eff_from = from_date
    eff_foreign_from = foreign_from_date
    if cached_last_trading_date:
        eff_from = cached_last_trading_date
        if cached_last_trading_date > foreign_from_date:
            eff_foreign_from = cached_last_trading_date

    trading_res: dict[str, Any] = {"daily_df": pd.DataFrame(), "foreign_charts": {}, "_errors": {}}
    if "trading" in modules:
        trading_res = client.fetch_page2_trading(
            symbol,
            from_date=eff_from,
            foreign_from_date=eff_foreign_from,
            to_date=to_date,
            exchange=seed.get("exchange") or "",
        )

    cstc_df = pd.DataFrame()
    bctt_df = pd.DataFrame()
    fin_errors: dict[str, str] = {}
    if "financials" in modules:
        for rtype in ("CSTC", "BCTT"):
            try:
                df_rep = client.fetch_full_financial_report(
                    symbol, report_type=rtype, start_year=from_year, page_size=5,
                )
                if rtype == "CSTC":
                    cstc_df = df_rep
                else:
                    bctt_df = df_rep
            except Exception as exc:  # noqa: BLE001
                fin_errors[rtype] = type(exc).__name__

    articles: list[dict[str, Any]] = []
    events_error: str | None = None
    if "events" in modules:
        try:
            articles = client.fetch_page5_articles(symbol, from_date=from_date, to_date=to_date)
        except Exception as exc:  # noqa: BLE001
            events_error = type(exc).__name__

    fin_field_names: list[str] = []
    if not bctt_df.empty:
        fin_field_names.extend(bctt_df.loc[bctt_df["value"].notna(), "field_name"].dropna().unique().tolist())
    if not cstc_df.empty:
        fin_field_names.extend(cstc_df.loc[cstc_df["value"].notna(), "field_name"].dropna().unique().tolist())

    master, taxonomy = _extract_overview_summary(symbol, seed, overview_data, fin_field_names)
    master["scanned_at"] = scanned_at

    corr_inputs = extract_latest_metrics_for_correlation(cstc_df, bctt_df, macro_group=taxonomy["macro_group"])
    # Fill valuation fallbacks from Trang 1 if CSTC latest period lacked market P/E or P/B.
    for v_key in ("pe", "pb", "roe", "roa"):
        if corr_inputs["values"].get(v_key) is None and master.get(v_key) is not None:
            corr_inputs["values"][v_key] = master[v_key]

    return {
        "symbol": symbol,
        "scanned_at": scanned_at,
        "master": master,
        "taxonomy": taxonomy,
        "overview": overview_data,
        "daily_df": trading_res["daily_df"],
        "foreign_charts": trading_res["foreign_charts"],
        "trading_errors": trading_res.get("_errors") or {},
        "cstc_df": cstc_df,
        "bctt_df": bctt_df,
        "financial_errors": fin_errors,
        "articles": articles,
        "events_error": events_error,
        "correlation_input": {
            "symbol": symbol,
            "macro_group": taxonomy["macro_group"],
            "sub_group": taxonomy["sub_group"],
            "peer_group_key": taxonomy["peer_group_key"],
            "split_from_financial_l1": taxonomy["split_from_financial_l1"],
            "latest_period": corr_inputs["latest_period"],
            "values": corr_inputs["values"],
        },
    }


def save_ticker_checkpoint(raw_item: dict[str, Any], ckpt_dir: Path) -> Path:
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    symbol = raw_item["symbol"]
    payload = clean_for_json({
        "symbol": symbol,
        "scanned_at": raw_item["scanned_at"],
        "master": raw_item["master"],
        "taxonomy": raw_item["taxonomy"],
        "overview": raw_item["overview"],
        "daily_records": raw_item["daily_df"].to_dict(orient="records") if not raw_item["daily_df"].empty else [],
        "foreign_charts": raw_item["foreign_charts"],
        "cstc_records": raw_item["cstc_df"].to_dict(orient="records") if not raw_item["cstc_df"].empty else [],
        "bctt_records": raw_item["bctt_df"].to_dict(orient="records") if not raw_item["bctt_df"].empty else [],
        "articles": raw_item["articles"],
        "correlation_input": raw_item["correlation_input"],
        "errors": {
            "overview": (raw_item["overview"] or {}).get("_errors") or {},
            "trading": raw_item.get("trading_errors") or {},
            "financials": raw_item.get("financial_errors") or {},
            "events": raw_item.get("events_error"),
        },
    })
    out_path = ckpt_dir / f"{symbol}.json.gz"
    write_gzip_json(payload, out_path)
    return out_path


def load_ticker_checkpoint(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def materialize_lake_and_static_bundles(
    ckpt_dir: Path,
    *,
    data_dir: Path,
    static_dir: Path | None,
    write_plain_json_fallback: bool = False,
) -> dict[str, Any]:
    """Consolidate per-ticker checkpoints into Parquet tables + Trang 3 correlations + `.json.gz` bundles."""
    ckpt_files = sorted(ckpt_dir.glob("*.json.gz")) if ckpt_dir.exists() else []
    loaded_items = [load_ticker_checkpoint(p) for p in ckpt_files]

    masters: list[dict[str, Any]] = []
    ohlcv_frames: list[pd.DataFrame] = []
    cstc_frames: list[pd.DataFrame] = []
    bctt_frames: list[pd.DataFrame] = []
    article_rows: list[dict[str, Any]] = []
    corr_inputs: list[dict[str, Any]] = []

    for item in loaded_items:
        masters.append(item["master"])
        corr_inputs.append(item["correlation_input"])
        if item.get("daily_records"):
            ohlcv_frames.append(pd.DataFrame(item["daily_records"]))
        if item.get("cstc_records"):
            cstc_frames.append(pd.DataFrame(item["cstc_records"]))
        if item.get("bctt_records"):
            bctt_frames.append(pd.DataFrame(item["bctt_records"]))
        if item.get("articles"):
            article_rows.extend(item["articles"])

    medians_summary = compute_peer_group_medians(corr_inputs)
    corr_by_symbol: dict[str, dict[str, Any]] = {}
    corr_flat_rows: list[dict[str, Any]] = []
    for c_in in corr_inputs:
        sym = c_in["symbol"]
        attached = attach_stock_peer_correlation(c_in, corr_inputs, medians_summary)
        corr_by_symbol[sym] = attached
        for m in attached["metrics"]:
            corr_flat_rows.append({
                "symbol": sym,
                "macro_group": attached["macro_group"],
                "sub_group": attached["sub_group"],
                "peer_group_key": attached["peer_group_key"],
                "split_from_financial_l1": attached["split_from_financial_l1"],
                "latest_period": attached["latest_period"],
                **m,
            })

    data_dir.mkdir(parents=True, exist_ok=True)
    parquet_hashes: dict[str, dict[str, Any]] = {}

    def _save_parquet(df: pd.DataFrame, filename: str) -> None:
        target = data_dir / filename
        df.to_parquet(target, index=False)
        parquet_hashes[filename] = {
            "rows": int(len(df)),
            "bytes": target.stat().st_size,
            "sha256": _sha256_file(target),
        }

    master_df = pd.DataFrame(masters) if masters else pd.DataFrame(columns=["symbol", "exchange", "macro_group"])
    ohlcv_df = pd.concat(ohlcv_frames, ignore_index=True) if ohlcv_frames else pd.DataFrame(columns=["symbol", "trading_date"])
    if not ohlcv_df.empty:
        ohlcv_df = ohlcv_df.sort_values(["symbol", "trading_date"]).drop_duplicates(["symbol", "trading_date"], keep="last")
    foreign_df = (
        ohlcv_df[ohlcv_df["trading_date"] >= "2025-01-01"].copy()
        if not ohlcv_df.empty and "trading_date" in ohlcv_df.columns
        else pd.DataFrame(columns=["symbol", "trading_date"])
    )
    cstc_all = pd.concat(cstc_frames, ignore_index=True) if cstc_frames else pd.DataFrame(columns=["symbol", "period", "field_name", "value"])
    bctt_all = pd.concat(bctt_frames, ignore_index=True) if bctt_frames else pd.DataFrame(columns=["symbol", "period", "field_name", "value"])
    corr_df = pd.DataFrame(corr_flat_rows) if corr_flat_rows else pd.DataFrame(columns=["symbol", "macro_group", "key", "value"])
    art_df = pd.DataFrame(article_rows) if article_rows else pd.DataFrame(columns=["symbol", "published_at", "title"])

    _save_parquet(master_df, "stocks_master.parquet")
    _save_parquet(ohlcv_df, "ohlcv_daily.parquet")
    _save_parquet(foreign_df, "foreign_room_daily.parquet")
    _save_parquet(cstc_all, "financials_cstc.parquet")
    _save_parquet(bctt_all, "financials_bctt.parquet")
    _save_parquet(corr_df, "peer_correlations.parquet")
    _save_parquet(art_df, "articles_events.parquet")

    generated_at = _utc_now()
    static_files_written = 0
    if static_dir is not None:
        stocks_out = static_dir / "stocks"
        stocks_out.mkdir(parents=True, exist_ok=True)
        universe_entries: list[dict[str, Any]] = []

        for item in loaded_items:
            sym = item["symbol"]
            m = item["master"]
            cstc_i = pd.DataFrame(item["cstc_records"]) if item.get("cstc_records") else pd.DataFrame()
            bctt_i = pd.DataFrame(item["bctt_records"]) if item.get("bctt_records") else pd.DataFrame()
            charts_4 = build_financial_charts(cstc_i, bctt_i, macro_group=m["macro_group"])
            bundle = clean_for_json({
                "schema_version": "1.0.0",
                "source_provider": "Yuanta YSradar (static snapshot)",
                "symbol": sym,
                "scanned_at": item["scanned_at"],
                "generated_at": generated_at,
                "master": m,
                "page1_overview": {
                    k: v for k, v in (item.get("overview") or {}).items() if k != "_errors"
                },
                "page2_trading": {
                    "daily": item.get("daily_records") or [],
                    "foreign_charts": item.get("foreign_charts") or {},
                },
                "page3_sector_correlation": corr_by_symbol.get(sym),
                "page4_financials": {
                    "macro_group": m["macro_group"],
                    "macro_group_label": m["macro_group_label"],
                    "cstc": _wide_financial_table(cstc_i),
                    "bctt": _wide_financial_table(bctt_i),
                    "charts": charts_4,
                },
                "page5_events": {
                    "record_count": len(item.get("articles") or []),
                    "articles": (item.get("articles") or [])[:2000],
                },
                "diagnostics": item.get("errors") or {},
            })
            gz_path = stocks_out / f"{sym}.json.gz"
            write_gzip_json(bundle, gz_path)
            static_files_written += 1
            if write_plain_json_fallback:
                (stocks_out / f"{sym}.json").write_text(
                    json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    encoding="utf-8",
                )
            universe_entries.append(clean_for_json({
                "symbol": sym,
                "exchange": m.get("exchange"),
                "company_name": m.get("company_name"),
                "sector_name": m.get("sector_name"),
                "macro_group": m.get("macro_group"),
                "macro_group_label": m.get("macro_group_label"),
                "sub_group": m.get("sub_group"),
                "sub_group_label": m.get("sub_group_label"),
                "split_from_financial_l1": m.get("split_from_financial_l1"),
                "last_price": m.get("last_price"),
                "pe": m.get("pe"),
                "pb": m.get("pb"),
                "roe": m.get("roe"),
                "stock_rating": m.get("stock_rating"),
                "scanned_at": item["scanned_at"],
                "bundle_gzip": f"stocks/{sym}.json.gz",
                "bundle_json": f"stocks/{sym}.json" if write_plain_json_fallback else None,
            }))

        universe_entries.sort(key=lambda r: r["symbol"])
        (static_dir / "universe.json").write_text(
            json.dumps({
                "schema_version": "1.0.0",
                "generated_at": generated_at,
                "count": len(universe_entries),
                "by_exchange": {
                    ex: sum(1 for u in universe_entries if u.get("exchange") == ex)
                    for ex in ("HOSE", "HNX", "UPCOM")
                },
                "by_macro_group": {
                    mg: sum(1 for u in universe_entries if u.get("macro_group") == mg)
                    for mg in ("BANK", "NON_BANK_FINANCIAL", "NON_FINANCIAL")
                },
                "stocks": universe_entries,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (static_dir / "sector_medians.json").write_text(
            json.dumps({
                "schema_version": "1.0.0",
                "generated_at": generated_at,
                **clean_for_json(medians_summary),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "stocks_materialized": len(masters),
        "by_exchange": {
            ex: sum(1 for m in masters if m.get("exchange") == ex)
            for ex in ("HOSE", "HNX", "UPCOM")
        },
        "by_macro_group": {
            mg: sum(1 for m in masters if m.get("macro_group") == mg)
            for mg in ("BANK", "NON_BANK_FINANCIAL", "NON_FINANCIAL")
        },
        "parquet_tables": parquet_hashes,
        "static_bundles_written": static_files_written,
    }
    (data_dir / "scan_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_scan(
    *,
    exchanges: tuple[str, ...] = ("HOSE", "HNX", "UPCOM"),
    tickers: list[str] | None = None,
    limit: int | None = None,
    workers: int = 6,
    delay: float = 0.15,
    from_date: str = "2010-01-01",
    foreign_from_date: str = "2025-01-01",
    to_date: str | None = None,
    from_year: int = 2010,
    modules: set[str] | None = None,
    include_intraday: bool = True,
    resume: bool = True,
    incremental: bool = False,
    rebuild_static_only: bool = False,
    dry_run: bool = False,
    data_dir: str | Path = ROOT / "data" / "yuanta",
    static_dir: str | Path | None = ROOT / "dashboard" / "data" / "yuanta",
    write_plain_json_fallback: bool = False,
    client: YuantaClient | None = None,
) -> dict[str, Any]:
    out_data = Path(data_dir)
    out_static = Path(static_dir) if static_dir is not None else None
    ckpt_dir = out_data / "checkpoints"
    active_modules = modules or {"overview", "trading", "financials", "events"}
    target_to_date = to_date or date.today().isoformat()

    if rebuild_static_only:
        if dry_run:
            return {"dry_run": True, "mode": "rebuild_static_only", "checkpoints_found": len(list(ckpt_dir.glob("*.json.gz"))) if ckpt_dir.exists() else 0}
        return materialize_lake_and_static_bundles(
            ckpt_dir,
            data_dir=out_data,
            static_dir=out_static,
            write_plain_json_fallback=write_plain_json_fallback,
        )

    api = client or YuantaClient(delay=delay)
    if tickers:
        universe = []
        for raw_t in tickers:
            sym = normalize_symbol(raw_t)
            if sym:
                universe.append({"symbol": sym, "exchange": None, "company_name": None, "sector_board": None})
    else:
        universe = api.discover_universe(exchanges=exchanges)

    if limit is not None and limit > 0:
        universe = universe[:limit]

    if dry_run:
        return {
            "dry_run": True,
            "exchanges": list(exchanges),
            "planned_symbol_count": len(universe),
            "sample_symbols": [u["symbol"] for u in universe[:15]],
            "modules": sorted(active_modules),
            "include_intraday": include_intraday,
            "from_date": from_date,
            "foreign_from_date": foreign_from_date,
            "from_year": from_year,
        }

    cached_last_dates: dict[str, str] = {}
    ohlcv_pq = out_data / "ohlcv_daily.parquet"
    if incremental and ohlcv_pq.exists():
        existing_ohlcv = pd.read_parquet(ohlcv_pq, columns=["symbol", "trading_date"])
        if not existing_ohlcv.empty:
            cached_last_dates = existing_ohlcv.groupby("symbol")["trading_date"].max().to_dict()

    pending: list[dict[str, Any]] = []
    skipped_resume = 0
    for item in universe:
        sym = item["symbol"]
        if resume and not incremental and (ckpt_dir / f"{sym}.json.gz").exists():
            skipped_resume += 1
            continue
        pending.append(item)

    scanned_ok = 0
    scanned_failed: list[str] = []

    def _job(seed_row: dict[str, Any]) -> tuple[str, bool]:
        sym = seed_row["symbol"]
        last_dt = cached_last_dates.get(sym) if incremental else None
        res = scan_single_ticker(
            api,
            seed_row,
            modules=active_modules,
            include_intraday=include_intraday,
            from_date=from_date,
            foreign_from_date=foreign_from_date,
            to_date=target_to_date,
            from_year=from_year,
            cached_last_trading_date=last_dt,
        )
        # If incremental, merge newly fetched daily_df with existing checkpoint's daily_records.
        prev_ckpt = ckpt_dir / f"{sym}.json.gz"
        if incremental and prev_ckpt.exists():
            old = load_ticker_checkpoint(prev_ckpt)
            if old.get("daily_records") and not res["daily_df"].empty:
                merged_daily = pd.concat([pd.DataFrame(old["daily_records"]), res["daily_df"]], ignore_index=True)
                merged_daily = merged_daily.sort_values("trading_date").drop_duplicates(["trading_date"], keep="last")
                res["daily_df"] = merged_daily
        save_ticker_checkpoint(res, ckpt_dir)
        return sym, True

    if pending:
        max_w = max(1, min(int(workers), len(pending)))
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            futs = {pool.submit(_job, row): row["symbol"] for row in pending}
            for idx, fut in enumerate(as_completed(futs), start=1):
                sym = futs[fut]
                try:
                    fut.result()
                    scanned_ok += 1
                except Exception as exc:  # noqa: BLE001
                    log.error("Quét %s thất bại: %s", sym, type(exc).__name__)
                    scanned_failed.append(sym)
                if idx % 25 == 0 or idx == len(pending):
                    log.info("Tiến độ Yuanta: %d/%d mã (OK=%d, lỗi=%d)", idx, len(pending), scanned_ok, len(scanned_failed))

    manifest = materialize_lake_and_static_bundles(
        ckpt_dir,
        data_dir=out_data,
        static_dir=out_static,
        write_plain_json_fallback=write_plain_json_fallback,
    )
    if api.source_events:
        events_df = pd.DataFrame(api.source_events).reindex(columns=SOURCE_COLUMNS)
        atomic_write_csv(events_df, out_data / "source_runs_yuanta.csv", sep=SEP)

    manifest.update({
        "universe_discovered": len(universe),
        "skipped_resume": skipped_resume,
        "scanned_ok": scanned_ok,
        "scanned_failed": sorted(scanned_failed),
        "source_events_recorded": len(api.source_events),
    })
    (out_data / "scan_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exchanges", default="HOSE,HNX,UPCOM", help="Danh sách sàn: HOSE,HNX,UPCOM")
    ap.add_argument("--tickers", default="", help="Danh sách mã cụ thể (VD: ACB,VCB,VHM,SSI,HPG); để trống = toàn bộ 3 sàn")
    ap.add_argument("--limit", type=int, default=None, help="Giới hạn số mã quét (dùng khi kiểm thử)")
    ap.add_argument("--workers", type=int, default=6, help="Số luồng tải song song có điều tiết tốc độ")
    ap.add_argument("--delay", type=float, default=0.15, help="Khoảng giãn cách tối thiểu giữa 2 request (giây)")
    ap.add_argument("--from-date", default="2010-01-01", help="Ngày bắt đầu lịch sử giá & sự kiện (mặc định 2010-01-01)")
    ap.add_argument("--foreign-from-date", default="2025-01-01", help="Ngày bắt đầu chi tiết khối ngoại (mặc định 2025-01-01)")
    ap.add_argument("--to-date", default=None, help="Ngày kết thúc YYYY-MM-DD (mặc định hôm nay)")
    ap.add_argument("--from-year", type=int, default=2010, help="Năm bắt đầu BCTC/CSTC (mặc định 2010 -> Q1/2010)")
    ap.add_argument("--modules", default="overview,trading,financials,events",
                    help="Các module cần quét: overview,trading,financials,events")
    ap.add_argument("--no-intraday", action="store_true",
                    help="Bỏ qua 2 API sổ lệnh/khớp lệnh trong phiên (last_bid_offer, list_transaction)")
    ap.add_argument("--no-resume", action="store_true", help="Quét lại từ đầu kể cả mã đã có checkpoint")
    ap.add_argument("--incremental", action="store_true", help="Chỉ tải tiếp lịch sử giao dịch từ ngày mới nhất đã lưu")
    ap.add_argument("--rebuild-static-only", action="store_true",
                    help="Không gọi mạng; chỉ dựng lại bảng Parquet, tương quan ngành & .json.gz từ checkpoint")
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "yuanta"))
    ap.add_argument("--static-dir", default=str(ROOT / "dashboard" / "data" / "yuanta"))
    ap.add_argument("--plain-json-fallback", action="store_true",
                    help="Ghi kèm stocks/{SYMBOL}.json không nén ngoài .json.gz")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    ex_list = tuple(s.strip().upper() for s in args.exchanges.split(",") if s.strip())
    t_list = [s.strip().upper() for s in args.tickers.split(",") if s.strip()] or None
    mods = {s.strip().lower() for s in args.modules.split(",") if s.strip()}

    result = run_scan(
        exchanges=ex_list,
        tickers=t_list,
        limit=args.limit,
        workers=args.workers,
        delay=args.delay,
        from_date=args.from_date,
        foreign_from_date=args.foreign_from_date,
        to_date=args.to_date,
        from_year=args.from_year,
        modules=mods,
        include_intraday=not args.no_intraday,
        resume=not args.no_resume,
        incremental=args.incremental,
        rebuild_static_only=args.rebuild_static_only,
        dry_run=args.dry_run,
        data_dir=args.data_dir,
        static_dir=args.static_dir,
        write_plain_json_fallback=args.plain_json_fallback,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("scanned_failed") else 0


if __name__ == "__main__":
    sys.exit(main())
