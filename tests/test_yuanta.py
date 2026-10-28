"""Offline tests for vnfa.yuanta and scripts/scan_yuanta_universe.py (Pages 1-5)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
import requests

from scripts import scan_yuanta_universe
from vnfa import dashboard_export, yuanta


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if not isinstance(payload, Exception) else b""
        self.content = raw

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeYuantaSession:
    """Deterministic offline Yuanta YSradar router for all Page 1-5 endpoints."""

    def __init__(self, *, year_step_fallback_only: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.year_step_fallback_only = year_step_fallback_only

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = 25.0) -> FakeResponse:
        parsed = urlparse(url)
        path = parsed.path
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if params:
            for k, v in params.items():
                if v is not None:
                    q[k] = str(v)
        self.calls.append((path, dict(q)))

        # Universe discovery across 3 exchanges (HSX/HOSE, HNX, UPCOM)
        if path == "/api/v3/market_data/price_board/stock/list_stock_info" and "exchange" in q:
            ex = q["exchange"]
            by_ex = {
                "HSX": [
                    {"StockCode": "ACB", "StockType": "ST", "Exchange": "HOSE", "CompanyNameVi": "Ngân hàng TMCP Á Châu"},
                    {"StockCode": "VHM", "StockType": "ST", "Exchange": "HOSE", "CompanyNameVi": "CTCP Vinhomes"},
                    {"StockCode": "KDH", "StockType": "ST", "Exchange": "HOSE", "CompanyNameVi": "CTCP Nhà Khang Điền"},
                    {"StockCode": "CWACB2601", "StockType": "CW", "Exchange": "HOSE", "CompanyNameVi": "CW ACB"},
                    {"StockCode": "C261", "StockType": "ST", "Exchange": "HOSE", "CompanyNameVi": "Untyped CW"},
                ],
                "HNX": [
                    {"StockCode": "SHS", "StockType": "ST", "Exchange": "HNX", "CompanyNameVi": "CTCP Chứng khoán Sài Gòn - Hà Nội"},
                ],
                "UPCOM": [
                    {"StockCode": "ACV", "StockType": "ST", "Exchange": "UPCOM", "CompanyNameVi": "Tổng công ty Cảng hàng không Việt Nam"},
                ],
            }
            return FakeResponse({"status": 200, "response": by_ex.get(ex, [])})

        if path == "/api/v3/market_data/price_board/stock/list_stock_by_sector":
            return FakeResponse({
                "status": 200,
                "response": [
                    {"SectorName": "Ngân hàng", "Stocks": [{"StockCode": "ACB"}]},
                    {"SectorName": "Bất động sản", "Stocks": "VHM,KDH"},
                    {"StockCode": "SHS", "SectorName": "Môi giới chứng khoán"},
                    {"StockCode": "ACV", "SectorName": "Hàng công nghiệp và dịch vụ"},
                ],
            })

        # Page 1: Overview (all 9 endpoints)
        if path.startswith("/api/v3/stock_rating/info/"):
            sym = path.rsplit("/", 1)[-1]
            l1_map = {"ACB": "Tài chính", "VHM": "Tài chính", "KDH": "Tài chính", "SHS": "Tài chính", "ACV": "Công nghiệp"}
            l2_map = {"ACB": "Ngân hàng", "VHM": "Bất động sản", "KDH": "Bất động sản", "SHS": "Dịch vụ tài chính", "ACV": "Vận tải"}
            return FakeResponse({
                "status": 200,
                "response": {
                    "StockCode": sym,
                    "ICBLevel1": l1_map.get(sym, "Phi tài chính"),
                    "ICBLevel2": l2_map.get(sym, "Công nghiệp"),
                    "StockRating": 86.5 if sym == "ACB" else 78.0,
                    "Fundamental": 88.0,
                    "RelativeStrength": 84.0,
                },
            })

        if path == "/ysw/api/v3/market_data/profile/info":
            sym = q.get("stock_code", "ACB")
            return FakeResponse({
                "code": 0,
                "data": {
                    "StockCode": sym,
                    "CompanyName": f"Doanh nghiệp {sym}",
                    "Overview": f"Tổng quan hoạt động {sym}",
                    "Leaders": [{"Name": "Nguyễn Văn A", "Position": "Chủ tịch HĐQT"}],
                },
            })

        if path == "/ysw/api/v3/market_data/profile/shareholder":
            return FakeResponse({
                "code": 0,
                "data": [{"Name": "Dragon Financial Holdings", "Quantity": 300000000, "OwnershipPercentage": 6.92}],
            })

        if path.startswith("/api/v3/market_data/price_board/stock/stock_info/"):
            sym = path.rsplit("/", 1)[-1]
            return FakeResponse({
                "status": 200,
                "response": {
                    "StockCode": sym,
                    "MatchPrice": 25800,
                    "RefPrice": 25500,
                    "MarketCap": 115000000000000,
                    "PE": 6.8 if sym == "ACB" else 9.4,
                    "PB": 1.42 if sym == "ACB" else 1.55,
                    "ROE": 22.4 if sym == "ACB" else 18.2,
                },
            })

        if path.startswith("/api/v3/market_data/price_board/stock/last_bid_offer/"):
            return FakeResponse({
                "status": 200,
                "response": {"BidPrice1": 25750, "BidVol1": 54200, "OfferPrice1": 25800, "OfferVol1": 31800},
            })

        if path.startswith("/api/v3/market_data/price_board/stock/list_transaction/"):
            return FakeResponse({
                "status": 200,
                "response": [{"TradingTime": "14:45:00", "MatchPrice": 25800, "MatchVolume": 120000, "Side": "B"}],
            })

        if path == "/api/v3/market_data/price_board/stock/list_stock_info":
            sym = q.get("stock_list", "ACB")
            return FakeResponse({"status": 200, "response": [{"StockCode": sym, "LastMP": 25800}]})

        if path == "/api/v3/market_data/price_board/stock/list_stock_rating_info":
            sym = q.get("stock_list", "ACB")
            return FakeResponse({"status": 200, "response": [{"StockCode": sym, "StockRating": 86.5}]})

        if path.startswith("/ysw/api/v3/market_data/profile/peers/"):
            return FakeResponse({
                "code": 0,
                "data": {"IndustryPE": 10.2, "Peers": [{"StockCode": "VCB", "CompanyName": "Vietcombank", "ClosePrice": 91000, "PE": 14.5, "PB": 2.7, "ROE": 20.1}]},
            })

        # Page 2: Trading & Foreign flow
        if path.startswith("/api/v3/market_data/market_watch/price_history/"):
            return FakeResponse({
                "status": 200,
                "response": [
                    {
                        "TradingDate": "2010-01-04T00:00:00",
                        "Open": 8.1,
                        "Highest": 8.3,
                        "Lowest": 8.0,
                        "Close": 8.2,
                        "Average": None,
                        "PerChange": 1.2,
                        "Volume": 1500000,
                        "Value": 12300000000,
                        "ForeignVolumeBuy": 100000,
                        "ForeignVolumeSell": 50000,
                        "PutThroughVolume": None,
                        "PutThroughValue": None,
                    },
                    {
                        "TradingDate": "2026-10-26T00:00:00",
                        "Open": 25.5,
                        "Highest": 25.9,
                        "Lowest": 25.4,
                        "Close": 25.8,
                        "Average": 25.7,
                        "PerChange": 1.18,
                        "Volume": 12450000,
                        "Value": 319965000000,
                        "ForeignVolumeBuy": 850000,
                        "ForeignVolumeSell": 320000,
                        "PutThroughVolume": 0,
                        "PutThroughValue": 0,
                    },
                ],
            })

        if path.startswith("/api/v3/market_data/market_watch/price_history_foreign/"):
            return FakeResponse({
                "status": 200,
                "response": [
                    {
                        "TradingDate": "2026-10-26T00:00:00",
                        "RemainRoom": 0,
                        "BuyVol": 850000,
                        "BuyVal": 21930000000,
                        "SellVol": 320000,
                        "SellVal": 8256000000,
                        "NetVol": 530000,
                        "NetVal": 13674000000,
                        "ForeignValRate": 9.43,
                        "ForeignOwnedRate": 30.0,
                    },
                ],
            })

        if path.startswith("/api/v3/foreign_screener/"):
            return FakeResponse({
                "status": 200,
                "response": [{"TradingDate": "2026-10-26", "NetVal": 13674000000, "TyLeVonHoa": 30.0}],
            })

        # Page 4: Financial statements CSTC & BCTT with multi-page backwards pagination to Q1/2010
        if path == "/api/v3/vietstock/financial_statement/report":
            sym = q.get("stock_code", "ACB")
            rtype = q.get("report_type", "CSTC")
            page = int(q.get("page_index", "1"))
            yr_param = int(q.get("year_period", "2026"))

            if self.year_step_fallback_only:
                titles = [{"term_code": "Q1", "year_period": yr_param}]
            elif page == 1:
                titles = [{"term_code": "Q3", "year_period": 2026}, {"term_code": "Q2", "year_period": 2026}]
            elif page == 2:
                titles = [{"term_code": "Q1", "year_period": 2010}, {"term_code": "Q4", "year_period": 2009}]
            else:
                titles = []

            if not titles:
                return FakeResponse({"status": 200, "response": {"title": {"data": []}, "detail": []}})

            n = len(titles)
            if rtype == "CSTC":
                roe = 22.4 if sym == "ACB" else (18.5 if sym == "VHM" else (15.2 if sym == "KDH" else 14.0))
                pe = 6.8 if sym == "ACB" else (7.9 if sym == "VHM" else (12.4 if sym == "KDH" else 13.5))
                pb = 1.42 if sym == "ACB" else (1.35 if sym == "VHM" else (1.65 if sym == "KDH" else 1.8))
                detail = [
                    {
                        "report_component_name": "Chỉ số định giá & sinh lời",
                        "data": [
                            {"field_name": "P/E", "unit": "Lần", "value": [pe + 0.2 * i for i in range(n)]},
                            {"field_name": "P/B", "unit": "Lần", "value": [pb + 0.1 * i for i in range(n)]},
                            {"field_name": "ROE", "unit": "%", "value": [roe - 0.5 * i for i in range(n)]},
                            {"field_name": "ROA", "unit": "%", "value": [1.85 - 0.05 * i for i in range(n)]},
                            {"field_name": "NIM", "unit": "%", "value": [3.95 if sym == "ACB" else None for _ in range(n)]},
                            {"field_name": "CIR", "unit": "%", "value": [32.5 if sym == "ACB" else None for _ in range(n)]},
                            {"field_name": "Tỷ suất lợi nhuận gộp biên", "unit": "%", "value": [None if sym == "ACB" else 34.2 for _ in range(n)]},
                            {"field_name": "Tỷ suất sinh lợi trên doanh thu thuần", "unit": "%", "value": [None if sym == "ACB" else 24.5 for _ in range(n)]},
                            {"field_name": "Tỷ số thanh toán hiện hành (ngắn hạn)", "unit": "Lần", "value": [None if sym == "ACB" else 1.65 for _ in range(n)]},
                            {"field_name": "Tỷ số Nợ vay trên Vốn chủ sở hữu", "unit": "Lần", "value": [None if sym == "ACB" else 0.42 for _ in range(n)]},
                        ],
                    }
                ]
            else:
                detail = [
                    {
                        "report_component_name": "Kết quả kinh doanh & Cân đối kế toán",
                        "data": [
                            {"field_name": "Thu nhập lãi thuần" if sym == "ACB" else "Doanh thu thuần về bán hàng và cung cấp dịch vụ", "unit": "Triệu đồng", "value": [7150000 for _ in range(n)]},
                            {"field_name": "Lợi nhuận sau thuế của cổ đông của Công ty mẹ", "unit": "Triệu đồng", "value": [4120000 for _ in range(n)]},
                            {"field_name": "TỔNG CỘNG TÀI SẢN", "unit": "Triệu đồng", "value": [765000000 for _ in range(n)]},
                            {"field_name": "NỢ PHẢI TRẢ", "unit": "Triệu đồng", "value": [682000000 for _ in range(n)]},
                            {"field_name": "Vốn chủ sở hữu", "unit": "Triệu đồng", "value": [83000000 for _ in range(n)]},
                            {"field_name": "Cho vay khách hàng", "unit": "Triệu đồng", "value": [550000000 if sym == "ACB" else None for _ in range(n)]},
                            {"field_name": "Tiền gửi của khách hàng", "unit": "Triệu đồng", "value": [512000000 if sym == "ACB" else None for _ in range(n)]},
                            {"field_name": "Hàng tồn kho", "unit": "Triệu đồng", "value": [None if sym == "ACB" else 52000000 for _ in range(n)]},
                        ],
                    }
                ]
            return FakeResponse({"status": 200, "response": {"title": {"data": titles}, "detail": detail}})

        # Page 5: Corporate articles & events
        if path == "/ysw/api/v3/market_data/article":
            sym = q.get("stock_code", "ACB")
            return FakeResponse({
                "code": 0,
                "data": [
                    {
                        "ArticleID": f"{sym}-101",
                        "PublishTime": "2026-10-15T08:30:00",
                        "ChannelName": "Cổ tức",
                        "Title": f"{sym}: Nghị quyết HĐQT về chi trả cổ tức năm 2026",
                        "Url": "https://ysradar.yuanta.com.vn/news/101",
                    },
                    {
                        "ArticleID": f"{sym}-102",
                        "PublishTime": "2026-07-20T09:00:00",
                        "ChannelName": "BCTC",
                        "Title": f"{sym}: Công bố Báo cáo tài chính Quý 2/2026",
                        "Url": "https://ysradar.yuanta.com.vn/news/102",
                    },
                ],
            })

        return FakeResponse({"error": "not found"}, status_code=404)


def test_classify_sector_taxonomy_splits_real_estate_out_of_financials_l1():
    # ACB -> BANK
    acb = yuanta.classify_sector_taxonomy(icb_l1="Tài chính", icb_l2="Ngân hàng", sector_name="Ngân hàng")
    assert acb["macro_group"] == "BANK"
    assert acb["sub_group"] == "BANK"
    assert acb["split_from_financial_l1"] is False

    # SSI / BVH -> NON_BANK_FINANCIAL
    ssi = yuanta.classify_sector_taxonomy(icb_l1="Tài chính", icb_l2="Dịch vụ tài chính", sector_name="Môi giới chứng khoán")
    bvh = yuanta.classify_sector_taxonomy(icb_l1="Tài chính", icb_l2="Bảo hiểm", sector_name="Bảo hiểm phi nhân thọ")
    fin_other = yuanta.classify_sector_taxonomy(icb_l1="Tài chính", sector_name="Quỹ đầu tư")
    assert ssi["macro_group"] == "NON_BANK_FINANCIAL"
    assert ssi["sub_group"] == "SECURITIES"
    assert bvh["macro_group"] == "NON_BANK_FINANCIAL"
    assert bvh["sub_group"] == "INSURANCE"
    assert fin_other["macro_group"] == "NON_BANK_FINANCIAL"
    assert fin_other["sub_group"] == "FIN_SERVICES"

    # VHM / VIC / KDH -> ICB L1 is "Tài chính" (8000 -> 8600), MUST be split out to NON_FINANCIAL / REAL_ESTATE!
    vhm = yuanta.classify_sector_taxonomy(
        icb_l1="Tài chính", icb_l2="Bất động sản", sector_name="Quản lý và phát triển bất động sản", icb_code="8633",
    )
    assert vhm["macro_group"] == "NON_FINANCIAL"
    assert vhm["macro_group_label"] == "Phi tài chính"
    assert vhm["sub_group"] == "REAL_ESTATE"
    assert vhm["peer_group_key"] == "REAL_ESTATE"
    assert vhm["split_from_financial_l1"] is True

    # Non-financial subgroups stay distinct under NON_FINANCIAL
    assert yuanta.classify_sector_taxonomy(sector_name="Phần mềm CNTT")["sub_group"] == "TECHNOLOGY"
    assert yuanta.classify_sector_taxonomy(sector_name="Thép và vật liệu")["sub_group"] == "MATERIALS_STEEL"
    assert yuanta.classify_sector_taxonomy(sector_name="Bán lẻ thực phẩm")["sub_group"] == "CONSUMER_RETAIL"
    assert yuanta.classify_sector_taxonomy(sector_name="Điện & Dầu khí")["sub_group"] == "ENERGY_UTILITIES"
    assert yuanta.classify_sector_taxonomy(sector_name="Dược phẩm y tế")["sub_group"] == "HEALTHCARE"
    assert yuanta.classify_sector_taxonomy(sector_name="Cảng & vận tải")["sub_group"] == "INDUSTRIALS"


def test_discover_universe_3_exchanges_and_financial_year_fallback():
    session = FakeYuantaSession()
    client = yuanta.YuantaClient(session=session, delay=0.0)
    universe = client.discover_universe(("HOSE", "HNX", "UPCOM"))
    symbols = {u["symbol"]: u for u in universe}

    # Equities across all 3 exchanges are discovered; CWs CWACB2601 and C261 are excluded
    assert set(symbols.keys()) == {"ACB", "VHM", "KDH", "SHS", "ACV"}
    assert symbols["ACB"]["exchange"] == "HOSE"
    assert symbols["ACB"]["sector_board"] == "Ngân hàng"
    assert symbols["VHM"]["sector_board"] == "Bất động sản"
    assert symbols["SHS"]["exchange"] == "HNX"
    assert symbols["ACV"]["exchange"] == "UPCOM"

    # Also verify year-stepping fallback when page_index=2 repeats page 1
    fallback_client = yuanta.YuantaClient(session=FakeYuantaSession(year_step_fallback_only=True), delay=0.0)
    df_fb = fallback_client.fetch_full_financial_report("ACB", report_type="CSTC", start_year=2024, end_year=2026)
    assert set(df_fb["period"]) == {"2024_Q1", "2025_Q1", "2026_Q1"}

    # Pre-flattened wide financial rows are also supported
    flat_rows, flat_periods = yuanta.parse_financial_report_page(
        {"response": [{"report_component": "Định giá", "field_name": "P/E", "unit": "x", "2010_Q1": 8.5, "Q4/2009": 7.9}]},
        symbol="ACB", report_type="CSTC", start_year=2010,
    )
    assert len(flat_rows) == 1
    assert flat_rows[0]["period"] == "2010_Q1"
    assert (2009, "Q4") in flat_periods


def test_scanner_cli_end_to_end_parquet_gzip_incremental_and_dashboard_coexistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_session = FakeYuantaSession()
    orig_init = yuanta.YuantaClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs.setdefault("session", fake_session)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(yuanta.YuantaClient, "__init__", _patched_init)

    data_dir = tmp_path / "data_yuanta"
    static_dir = tmp_path / "dashboard" / "data" / "yuanta"

    # 1. Dry-run discovers 3 exchanges without writing checkpoints or Parquet
    rc_dry = scan_yuanta_universe.main([
        "--exchanges", "HOSE,HNX,UPCOM",
        "--data-dir", str(data_dir),
        "--static-dir", str(static_dir),
        "--dry-run",
    ])
    assert rc_dry == 0
    assert not (data_dir / "stocks_master.parquet").exists()

    # 2. Full scan materializes Parquet lake + .json.gz static bundles + plain .json fallback
    rc_full = scan_yuanta_universe.main([
        "--exchanges", "HOSE,HNX,UPCOM",
        "--workers", "2",
        "--delay", "0",
        "--to-date", "2026-10-27",
        "--data-dir", str(data_dir),
        "--static-dir", str(static_dir),
        "--plain-json-fallback",
    ])
    assert rc_full == 0

    for fname in (
        "stocks_master.parquet",
        "ohlcv_daily.parquet",
        "foreign_room_daily.parquet",
        "financials_cstc.parquet",
        "financials_bctt.parquet",
        "peer_correlations.parquet",
        "articles_events.parquet",
        "scan_manifest.json",
        "source_runs_yuanta.csv",
    ):
        assert (data_dir / fname).exists(), f"Missing {fname}"

    master_df = pd.read_parquet(data_dir / "stocks_master.parquet")
    assert set(master_df["symbol"]) == {"ACB", "VHM", "KDH", "SHS", "ACV"}
    vhm_row = master_df.loc[master_df["symbol"] == "VHM"].iloc[0]
    assert vhm_row["macro_group"] == "NON_FINANCIAL"
    assert vhm_row["sub_group"] == "REAL_ESTATE"
    assert bool(vhm_row["split_from_financial_l1"]) is True

    # Missing OHLCV fields stay NaN in Parquet and null in JSON (never coerced to 0)
    ohlcv_df = pd.read_parquet(data_dir / "ohlcv_daily.parquet")
    acb_2010 = ohlcv_df[(ohlcv_df["symbol"] == "ACB") & (ohlcv_df["trading_date"] == "2010-01-04")].iloc[0]
    assert pd.isna(acb_2010["average"])
    assert pd.isna(acb_2010["put_through_vol"])

    # Verify gzipped static stock bundles have strict JSON (zero NaN/Infinity tokens) and accurate Trang 3 correlation
    for sym in ("ACB", "VHM"):
        gz_path = static_dir / "stocks" / f"{sym}.json.gz"
        assert gz_path.exists()
        raw_text = gzip.decompress(gz_path.read_bytes()).decode("utf-8")
        assert "NaN" not in raw_text
        assert "Infinity" not in raw_text

    acb_bundle = json.loads(gzip.decompress((static_dir / "stocks" / "ACB.json.gz").read_bytes()).decode("utf-8"))
    vhm_bundle = json.loads(gzip.decompress((static_dir / "stocks" / "VHM.json.gz").read_bytes()).decode("utf-8"))

    assert acb_bundle["page3_sector_correlation"]["macro_group"] == "BANK"
    assert acb_bundle["page3_sector_correlation"]["peer_universe_size"] == 1
    assert vhm_bundle["page3_sector_correlation"]["macro_group"] == "NON_FINANCIAL"
    assert vhm_bundle["page3_sector_correlation"]["peer_group_key"] == "REAL_ESTATE"
    assert vhm_bundle["page3_sector_correlation"]["split_from_financial_l1"] is True
    assert vhm_bundle["page3_sector_correlation"]["peer_universe_size"] == 2  # VHM + KDH separated from ACB & SHS!
    assert "2010_Q1" in acb_bundle["page4_financials"]["cstc"]["periods"]
    assert "2009_Q4" not in acb_bundle["page4_financials"]["cstc"]["periods"]
    assert len(acb_bundle["page4_financials"]["charts"]) >= 3
    assert all(chart.get("chart_id") and all(series.get("series_key") for series in chart["series"]) for chart in acb_bundle["page4_financials"]["charts"])
    assert acb_bundle["page5_events"]["record_count"] == 2

    # Source runs CSV has zero query params or raw bodies
    sr_df = pd.read_csv(data_dir / "source_runs_yuanta.csv", sep=";")
    assert len(sr_df) > 20
    assert not sr_df["endpoint"].str.contains(r"\?", regex=True).any()

    # 3. Incremental refresh & rebuild-static-only work cleanly
    rc_inc = scan_yuanta_universe.main([
        "--tickers", "ACB,VHM",
        "--incremental",
        "--delay", "0",
        "--to-date", "2026-10-27",
        "--data-dir", str(data_dir),
        "--static-dir", str(static_dir),
    ])
    assert rc_inc == 0

    rc_rebuild = scan_yuanta_universe.main([
        "--rebuild-static-only",
        "--data-dir", str(data_dir),
        "--static-dir", str(static_dir),
    ])
    assert rc_rebuild == 0

    # 4. Running export_dashboard on parent dashboard/data preserves dashboard/data/yuanta/
    fmarket_out = tmp_path / "dashboard" / "data"
    dashboard_export.export_dashboard("data", fmarket_out)
    assert (fmarket_out / "manifest.json").exists()
    assert (static_dir / "stocks" / "ACB.json.gz").exists()
    assert (static_dir / "universe.json").exists()


def test_clean_for_json_replaces_nan_and_inf_with_none_never_zero():
    dirty = {
        "a": float("nan"),
        "b": float("inf"),
        "c": float("-inf"),
        "zero": 0.0,
        "nested": [1.5, float("nan"), None],
    }
    cleaned = scan_yuanta_universe.clean_for_json(dirty)
    assert cleaned == {"a": None, "b": None, "c": None, "zero": 0.0, "nested": [1.5, None, None]}
    encoded = json.dumps(cleaned, allow_nan=False)
    assert json.loads(encoded)["zero"] == 0.0
    assert json.loads(encoded)["a"] is None
