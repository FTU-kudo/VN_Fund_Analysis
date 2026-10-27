# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

from vnfa.config import load_config
from vnfa.diff import diff_holdings, flag_corporate_actions, market_flow
from vnfa.fmarket import parse_detail
from vnfa.store import Store, TableStore


def H(rows):
    return pd.DataFrame(rows, columns=["ticker", "weight", "volume", "price_k", "asset_value"]).assign(
        industry="x", update_date="2026-10-01")


def test_buy_sell_new_exit():
    # Bốn mã chung cho phép ước lượng proxy quy mô; AAA/BBB là phần dư đáng kể.
    prev = H([("AAA", .10, 1000, 10, 10e6), ("BBB", .08, 1000, 10, 10e6), ("CCC", .05, 500, 10, 5e6),
              ("DDD", .04, 100, 10, 1e6), ("EEE", .03, 200, 10, 2e6)])
    new = H([("AAA", .09, 1200, 10, 12e6), ("BBB", .07, 800, 10, 8e6), ("CCC", .05, 510, 10, 5.1e6),
             ("EEE", .03, 200, 10, 2e6), ("FFF", .04, 100, 10, 1e6)])
    ch = diff_holdings(prev, new, 0.05, 0.005).set_index("ticker")
    assert ch.loc["AAA", "action"] == "BUY" and abs(ch.loc["AAA", "vol_pct"] - 0.2) < 1e-9
    assert abs(ch.loc["AAA", "vol_scale"] - 0.01) < 1e-9
    assert abs(ch.loc["AAA", "est_value_vnd"] - 190 * 10000) < 1e-6  # trừ proxy quy mô quỹ
    assert ch.loc["BBB", "action"] == "SELL" and ch.loc["BBB", "est_value_vnd"] < 0
    assert "CCC" not in ch.index                       # đổi 2% < ngưỡng 5% sau điều chỉnh
    assert ch.loc["DDD", "action"] == "EXIT_TOP10" and ch.loc["DDD", "censored"]
    assert ch.loc["FFF", "action"] == "NEW_IN_TOP10"


def test_prorata_scaling_is_not_a_view():
    base = [("A", .1, 1000, 10, 10e6), ("B", .1, 1000, 10, 10e6), ("C", .1, 1000, 10, 10e6),
            ("D", .1, 1000, 10, 10e6), ("E", .1, 1000, 10, 10e6)]
    new = [("A", .1, 1200, 10, 12e6), ("B", .1, 1200, 10, 12e6), ("C", .1, 1200, 10, 12e6),
           ("D", .1, 1200, 10, 12e6), ("E", .1, 1800, 10, 18e6)]       # quỹ +20% mọi mã, riêng E mua thêm
    ch = diff_holdings(H(base), H(new), 0.05, 0.005).set_index("ticker")
    assert abs(ch.loc["A", "vol_scale"] - 0.2) < 1e-9 and ch.loc["A", "action"] == "PRORATA"
    assert ch.loc["E", "action"] == "BUY" and abs(ch.loc["E", "vol_rel"] - 0.5) < 1e-9
    assert abs(ch.loc["E", "est_value_vnd"] - 600 * 10000) < 1e-6        # (1800 - 1000*1.2) x 10.000đ
    assert market_flow(ch.reset_index().assign(fund="F")).set_index("ticker").loc["A", "n_buy"] == 0


def test_price_move_is_not_a_trade():
    prev = H([("AAA", .10, 1000, 10, 10e6)])
    new = H([("AAA", .13, 1000, 13, 13e6)])           # tỷ trọng +3 điểm do giá tăng, khối lượng không đổi
    assert diff_holdings(prev, new).empty


def test_weight_fallback_is_unconfirmed_not_a_trade():
    prev = H([("AAA", .10, np.nan, np.nan, np.nan)])
    new = H([("AAA", .12, np.nan, np.nan, np.nan)])
    ch = diff_holdings(prev, new, 0.05, 0.005)
    assert ch.iloc[0].action == "WEIGHT_CHANGE?" and ch.iloc[0].basis == "weight"
    assert ch.iloc[0].flag == "WEIGHT_ONLY_UNCONFIRMED" and pd.isna(ch.iloc[0].est_value_vnd)


def test_three_overlapping_holdings_are_not_counted_as_active_flow():
    prev = H([(ticker, .1, 1000, 10, 10e6) for ticker in "ABC"])
    new = H([(ticker, .1, 1100, 10, 11e6) for ticker in "ABC"])
    changes = diff_holdings(prev, new, 0.05, 0.005)
    assert set(changes.action) == {"UNCONFIRMED?"}
    flow = market_flow(changes)
    assert flow.n_buy.sum() == 0 and flow.n_unconfirmed.sum() == 3
    assert flow.net_value_bn.isna().all()


def test_missing_values_are_not_reported_as_zero_flow():
    changes = pd.DataFrame([{"ticker": "AAA", "action": "WEIGHT_CHANGE?", "basis": "weight",
                             "est_value_vnd": np.nan}])
    flow = market_flow(changes).iloc[0]
    assert flow.n_buy == 0 and flow.n_unconfirmed == 1
    assert pd.isna(flow.net_value_bn)


def test_corporate_action_flag():
    cols = ["observed_date", "fund", "mgmt_co", "ticker", "action", "basis", "w_prev", "w_new", "w_delta",
            "vol_prev", "vol_new", "vol_pct", "vol_rel", "vol_scale", "est_value_vnd", "censored", "flag"]
    rows = [("2026-07-08", f, "co", "KLB", "BUY", "volume", .05, .05, 0, 100, 129.5, p, p, 0, 1e9, False, "")
            for f, p in [("F1", .295), ("F2", .296), ("F3", .294)]]
    rows.append(("2026-07-08", "F4", "co", "KLB", "BUY", "volume", .05, .06, 0, 100, 110, .10, .10, 0, 1e9, False, ""))
    rows.append(("2026-07-08", "F5", "co", "ZZZ", "BUY", "volume", .05, .06, 0, 100, 112, .12, .12, 0, 1e9, False, ""))
    df = flag_corporate_actions(pd.DataFrame(rows, columns=cols), None, 3, 0.01, 0.03)
    assert (df[df.fund.isin(["F1", "F2", "F3"])].action == "CORP_ACTION?").all()
    assert df[df.fund == "F4"].action.iloc[0] == "BUY" and df[df.fund == "F5"].action.iloc[0] == "BUY"
    assert market_flow(df).set_index("ticker").loc["KLB", "net_value_bn"] == 1.0   # chỉ F4 được tính


def test_corporate_action_detection_reflags_prior_history():
    cols = ["observed_date", "fund", "mgmt_co", "ticker", "action", "basis", "w_prev", "w_new", "w_delta",
            "vol_prev", "vol_new", "vol_pct", "vol_rel", "vol_scale", "est_value_vnd", "censored", "flag"]
    hist_rows = [("2026-07-01", f, "co", "ABC", "BUY", "volume", .05, .05, 0, 100, 110,
                  .10, .10, 0, 1e6, False, "") for f in ("F1", "F2")]
    cur_rows = [("2026-07-02", "F3", "co", "ABC", "BUY", "volume", .05, .05, 0, 100, 110,
                 .10, .10, 0, 1e6, False, "")]
    current, history = flag_corporate_actions(
        pd.DataFrame(cur_rows, columns=cols), pd.DataFrame(hist_rows, columns=cols),
        min_funds=3, spread=.01, min_pct=.03, update_history=True)
    assert current.iloc[0].action == "CORP_ACTION?"
    assert set(history.action) == {"CORP_ACTION?"}
    assert history.est_value_vnd.isna().all()


def test_store_append_only_when_changed(tmp_path):
    s = Store(tmp_path)
    h1 = H([("AAA", .1, 1000, 10, 10e6)])
    assert s.append_if_changed("F", h1, "2026-01-01")[0] == "baseline"
    assert s.append_if_changed("F", h1, "2026-01-02")[0] == "unchanged"
    st, prev = s.append_if_changed("F", H([("AAA", .1, 1100, 10, 11e6)]), "2026-01-03")
    assert st == "changed" and prev.volume.astype(float).iloc[0] == 1000
    assert s.read_holdings("F").observed_date.nunique() == 2
    assert s.last_disclosure("F").observed_date.iloc[0] == "2026-01-03"


def test_store_rejects_backdated_holdings_snapshot(tmp_path):
    s = Store(tmp_path)
    first = H([("AAA", .10, 1000, 10, 10e6)])
    s.append_if_changed("F1", first, "2026-01-02")
    import pytest
    with pytest.raises(ValueError, match="ngược thời gian"):
        s.append_if_changed("F1", H([("AAA", .11, 1100, 10, 11e6)]), "2026-01-01")


def test_nav_snapshot_records_observation_even_when_nav_is_unchanged(tmp_path):
    s = TableStore(tmp_path)
    assert s.append_nav_snapshot("F1", 10_000, "2026-01-01") is True
    assert s.append_nav_snapshot("F1", 10_000, "2026-01-01") is False  # retry cùng ngày idempotent
    assert s.append_nav_snapshot("F1", 10_000, "2026-01-02") is True  # NAV phẳng không có nghĩa dữ liệu cũ
    out = s.read("nav_snapshots")
    assert list(out.date) == ["2026-01-01", "2026-01-02"]
    assert out.nav.astype(float).tolist() == [10_000, 10_000]


def test_upsert_nav_empty_history_is_idempotent(tmp_path):
    store = TableStore(tmp_path)
    assert store.upsert_nav({"F1": pd.DataFrame(columns=["date", "nav"])}) == 0
    assert not (tmp_path / "nav.csv").exists()


def test_nav_snapshot_rejects_backdated_observation(tmp_path):
    s = TableStore(tmp_path)
    s.append_nav_snapshot("F1", 10_000, "2026-01-02")
    import pytest
    with pytest.raises(ValueError, match="ngược thời gian"):
        s.append_nav_snapshot("F1", 10_000, "2026-01-01")


def test_parse_detail_raw_and_slim():
    raw = {"id": 1, "code": "X", "owner": {"shortName": "CO"}, "dataFundAssetType": {"code": "STOCK", "name": "Quỹ cổ phiếu"},
           "productTopHoldingList": [{"stockCode": "tcb", "netAssetPercent": 6.43, "price": 34.2, "volume": 2531800,
                                      "assetValue": 82410090000, "updateAt": 1791447322898, "type": "STOCK"},
                                     {"stockCode": "BOND1", "netAssetPercent": 1, "type": "BOND"}]}
    slim = {"id": 1, "code": "X", "owner": {"shortName": "CO"}, "assetType": {"code": "STOCK"},
            "topHoldings": raw["productTopHoldingList"]}
    for d in (raw, slim):
        meta, df = parse_detail(d)
        assert meta["asset_code"] == "STOCK" and meta["mgmt_co"] == "CO"
        assert list(df.ticker) == ["TCB"] and abs(df.weight.iloc[0] - 0.0643) < 1e-12
        assert df.update_date.iloc[0] == "2026-10-08"


def test_reaction_report_keeps_missing_allocation_as_unknown(tmp_path, monkeypatch):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("analyze_reaction", "scripts/analyze_reaction.py")
    ar = importlib.util.module_from_spec(spec); spec.loader.exec_module(ar)

    pd.DataFrame([{"code": "F1", "asset_code": "STOCK", "mgmt_co": "CO"}]).to_csv(
        tmp_path / "funds.csv", sep=";", index=False)
    pd.DataFrame([
        {"fund": "F1", "asset_code": "STOCK", "pct": .50, "report_date": "2026-04-30"},
        {"fund": "F1", "asset_code": "CASH", "pct": .20, "report_date": "2026-04-30"},
        {"fund": "F1", "asset_code": "STOCK", "pct": .60, "report_date": "2026-08-31"},
    ]).to_csv(tmp_path / "allocation.csv", sep=";", index=False)
    out = tmp_path / "reaction.md"
    monkeypatch.setattr(sys, "argv", ["analyze_reaction.py", "--data-dir", str(tmp_path), "--out", str(out)])
    ar.main()
    text = out.read_text(encoding="utf-8")
    assert "N Δ tiền" in text and "0 | 50.0%" in text
    assert "-20.0%" not in text  # thiếu CASH ở kỳ mới không bị tính thành 0%


def test_backfill_nav_merge_preserves_existing_snapshots(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill_hat_ccq", "scripts/backfill_hat_ccq.py")
    backfill = importlib.util.module_from_spec(spec); spec.loader.exec_module(backfill)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pd.DataFrame([
        {"fund": "F1", "date": "2026-10-01", "nav": 10_000},
        {"fund": "F2", "date": "2026-08-01", "nav": 20_000},
    ]).to_csv(data_dir / "nav_snapshots.csv", sep=";", index=False)

    def fake_details(_repo, sha):
        return [{"code": "F1", "nav": 11_000 if sha == "old" else 12_000}]

    monkeypatch.setattr(backfill, "commit_details", fake_details)
    backfill.nav_snapshots(tmp_path / "source", [("old", "2026-10-01"), ("new", "2026-10-02")], str(data_dir))
    out = pd.read_csv(data_dir / "nav_snapshots.csv", sep=";")
    assert len(out) == 3
    assert out.set_index(["fund", "date"]).loc[("F1", "2026-10-01"), "nav"] == 10_000
    assert out.set_index(["fund", "date"]).loc[("F1", "2026-10-02"), "nav"] == 12_000
    assert ("F2", "2026-08-01") in out.set_index(["fund", "date"]).index


def test_import_full_holdings_requires_explicit_ambiguous_weight_unit(tmp_path):
    import importlib.util
    import pytest
    spec = importlib.util.spec_from_file_location("import_full_holdings", "scripts/import_full_holdings.py")
    loader = importlib.util.module_from_spec(spec); spec.loader.exec_module(loader)
    src = tmp_path / "holdings.csv"
    pd.DataFrame([
        {"fund": "f1", "ticker": "aaa", "weight": "0.5", "period_end": "2026-09-30", "source_url": "https://manager.example/report.pdf"},
        {"fund": "f1", "ticker": "bbb", "weight": "0.3", "period_end": "2026-09-30", "source_url": "https://manager.example/report.pdf"},
    ]).to_csv(src, sep=";", index=False)
    pct = loader.load_clean(src, weight_unit="percent")
    assert pct.weight.tolist() == [.005, .003]
    fraction = loader.load_clean(src, weight_unit="fraction")
    assert fraction.weight.tolist() == [.5, .3]

    pd.DataFrame([{"fund": "f1", "ticker": "aaa", "weight": "-1", "period_end": "2026-09-30", "source_url": "https://manager.example/report.pdf"}]).to_csv(
        src, sep=";", index=False)
    with pytest.raises(SystemExit, match="dương"):
        loader.load_clean(src)


def test_run_with_fake_client(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_daily", "scripts/run_daily.py")
    rd = importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)

    def det(vol):
        return {"id": 1, "code": "F1", "owner": {"shortName": "CO"}, "assetType": {"code": "STOCK"},
                "topHoldings": [{"stockCode": "AAA", "netAssetPercent": 5, "price": 10, "volume": vol,
                                 "assetValue": vol * 10000, "updateAt": 1791447322898, "type": "STOCK"}]}

    class Fake:
        def __init__(self, vol): self.vol = vol
        def list_products(self): return [{"id": 1, "code": "F1", "shortName": "F1", "owner": {"shortName": "CO"},
                                          "dataFundAssetType": {"code": "STOCK"}}]
        def product_detail(self, i): return det(self.vol)

    cfg = load_config(None)
    s = Store(tmp_path)
    info, ch = rd.run(cfg, Fake(1000), s, "2026-10-01")
    assert info["n_baseline"] == 1 and ch.empty
    info, ch = rd.run(cfg, Fake(1500), s, "2026-10-02")
    assert info["n_changed"] == 1 and ch.iloc[0].action == "UNCONFIRMED?"
    assert ch.iloc[0].flag == "FUND_SCALE_UNIDENTIFIED"
    assert pd.isna(market_flow(ch).net_value_bn.iloc[0])
    assert len(s.read_changes()) == 1
    # Retry trong cùng ngày trả lại sự kiện đã lưu, không làm báo cáo biến thành rỗng.
    rerun_info, rerun_changes = rd.run(cfg, Fake(1500), s, "2026-10-02")
    assert rerun_info["n_changed"] == 0 and rerun_changes.iloc[0].action == "UNCONFIRMED?"


def test_later_supplementary_error_does_not_lose_holdings_change(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_daily_partial", "scripts/run_daily.py")
    rd = importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)

    class Fake:
        def __init__(self, vol): self.vol = vol
        def list_products(self): return [{"id": 1, "code": "F1", "shortName": "F1", "owner": {"shortName": "CO"},
                                          "dataFundAssetType": {"code": "STOCK"}}]
        def product_detail(self, _):
            return {"id": 1, "code": "F1", "owner": {"shortName": "CO"}, "assetType": {"code": "STOCK"},
                    "topHoldings": [{"stockCode": "AAA", "netAssetPercent": 5, "price": 10, "volume": self.vol,
                                     "assetValue": self.vol * 10000, "type": "STOCK"}]}

    cfg, store = load_config(None), Store(tmp_path)
    rd.run(cfg, Fake(1000), store, "2026-10-01")

    def fail_after_holdings(self, *_args, **_kwargs):
        raise OSError("simulated optional table write failure")

    monkeypatch.setattr(rd.TableStore, "append_if_changed", fail_after_holdings)
    info, changes = rd.run(cfg, Fake(1500), store, "2026-10-02")
    assert info["n_failed"] == 1 and info["n_partial"] == 1
    assert changes.iloc[0].action == "UNCONFIRMED?"
    assert len(store.read_changes()) == 1
