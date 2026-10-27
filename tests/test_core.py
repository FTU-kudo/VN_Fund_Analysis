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


def test_diff_row_permutation_invariance():
    prev = H([("AAA", .12, 1000, 20, 20e6), ("BBB", .10, 2000, 15, 30e6), ("CCC", .08, 500, 10, 5e6),
              ("DDD", .07, 800, 12, 9.6e6), ("EEE", .05, 400, 8, 3.2e6), ("OLD", .04, 300, 9, 2.7e6)])
    new = H([("AAA", .14, 1300, 20, 26e6), ("BBB", .08, 1500, 15, 22.5e6), ("CCC", .08, 505, 10, 5.05e6),
             ("DDD", .07, 800, 12, 9.6e6), ("EEE", .05, 404, 8, 3.232e6), ("NEW", .04, 250, 11, 2.75e6)])
    baseline = diff_holdings(prev, new, 0.05, 0.005).reset_index(drop=True)
    for seed in (1, 7, 42, 99):
        perm_prev = prev.sample(frac=1.0, random_state=seed).reset_index(drop=True)
        perm_new = new.sample(frac=1.0, random_state=seed + 100).reset_index(drop=True)
        permuted = diff_holdings(perm_prev, perm_new, 0.05, 0.005).reset_index(drop=True)
        pd.testing.assert_frame_equal(baseline, permuted)


def test_doubling_all_volumes_emits_only_prorata_zero_active_flow():
    prev = H([("AAA", .10, 1000, 10, 10e6), ("BBB", .09, 2000, 12, 24e6),
              ("CCC", .08, 1500, 8, 12e6), ("DDD", .07, 500, 25, 12.5e6)])
    new = H([("AAA", .10, 2000, 10, 20e6), ("BBB", .09, 4000, 12, 48e6),
             ("CCC", .08, 3000, 8, 24e6), ("DDD", .07, 1000, 25, 25e6)])
    ch = diff_holdings(prev, new, 0.05, 0.005)
    assert set(ch.action) == {"PRORATA"}
    assert np.allclose(ch.vol_scale, 1.0)
    assert np.allclose(ch.vol_rel, 0.0)
    flow = market_flow(ch.assign(fund="F1"))
    assert flow.n_buy.sum() == 0 and flow.n_sell.sum() == 0
    assert flow.net_value_bn.isna().all()


def test_zero_and_negative_volumes_are_unconfirmed_not_liquidations():
    prev = H([("AAA", .10, 1000, 10, 10e6), ("BBB", .09, 1000, 10, 10e6),
              ("CCC", .08, 1000, 10, 10e6), ("DDD", .07, 1000, 10, 10e6),
              ("ZERO", .06, 1000, 10, 10e6), ("NEG", .05, -50, 10, np.nan)])
    new = H([("AAA", .10, 1000, 10, 10e6), ("BBB", .09, 1000, 10, 10e6),
             ("CCC", .08, 1000, 10, 10e6), ("DDD", .07, 1000, 10, 10e6),
             ("ZERO", .06, 0, 10, 0), ("NEG", .05, 500, 10, 5e6)])
    ch = diff_holdings(prev, new, 0.05, 0.005).set_index("ticker")
    assert ch.loc["ZERO", "action"] == "UNCONFIRMED?"
    assert ch.loc["ZERO", "flag"] == "NONPOSITIVE_VOLUME_UNCONFIRMED"
    assert pd.isna(ch.loc["ZERO", "est_value_vnd"])
    assert ch.loc["NEG", "action"] == "UNCONFIRMED?"
    assert ch.loc["NEG", "flag"] == "NONPOSITIVE_VOLUME_UNCONFIRMED"
    flow = market_flow(ch.reset_index().assign(fund="F1"))
    assert flow.n_sell.sum() == 0 and flow.n_buy.sum() == 0


def test_duplicate_tickers_exact_and_conflicting_are_permutation_invariant():
    # Exact duplicate row in prev collapses cleanly; conflicting duplicate in new becomes UNCONFIRMED?.
    prev = H([("AAA", .10, 1000, 10, 10e6), ("AAA", .10, 1000, 10, 10e6),
              ("BBB", .09, 1000, 10, 10e6), ("CCC", .08, 1000, 10, 10e6),
              ("DDD", .07, 1000, 10, 10e6), ("DUP", .06, 1000, 10, 10e6)])
    new_a = H([("AAA", .12, 1300, 10, 13e6), ("BBB", .09, 1000, 10, 10e6),
               ("CCC", .08, 1000, 10, 10e6), ("DDD", .07, 1000, 10, 10e6),
               ("DUP", .04, 700, 10, 7e6), ("DUP", .03, 500, 10, 5e6)])
    new_b = new_a.iloc[::-1].reset_index(drop=True)
    ch_a = diff_holdings(prev, new_a, 0.05, 0.005).reset_index(drop=True)
    ch_b = diff_holdings(prev.iloc[::-1].reset_index(drop=True), new_b, 0.05, 0.005).reset_index(drop=True)
    pd.testing.assert_frame_equal(ch_a, ch_b)
    by_ticker = ch_a.set_index("ticker")
    assert by_ticker.loc["AAA", "action"] == "BUY"
    assert by_ticker.loc["DUP", "action"] == "UNCONFIRMED?"
    assert by_ticker.loc["DUP", "flag"] == "DUPLICATE_TICKER_AMBIGUOUS"
    assert pd.isna(by_ticker.loc["DUP", "est_value_vnd"])


def test_renamed_ticker_empty_snapshots_missing_prices_and_signed_flow():
    # Ticker rename (OLD -> NEW) is censored exit/entry, never inferred active BUY/SELL.
    prev = H([("AAA", .10, 1000, np.nan, np.nan), ("BBB", .10, 1000, 10, 10e6),
              ("CCC", .08, 1000, 10, 10e6), ("DDD", .07, 1000, 10, 10e6), ("OLD", .05, 500, 10, 5e6)])
    new = H([("AAA", .12, 1300, np.nan, np.nan), ("BBB", .08, 700, 10, 7e6),
             ("CCC", .08, 1000, 10, 10e6), ("DDD", .07, 1000, 10, 10e6), ("NEW", .05, 500, 10, 5e6)])
    ch = diff_holdings(prev, new, 0.05, 0.005).set_index("ticker")
    assert ch.loc["OLD", "action"] == "EXIT_TOP10" and ch.loc["OLD", "censored"]
    assert ch.loc["NEW", "action"] == "NEW_IN_TOP10" and ch.loc["NEW", "censored"]
    assert ch.loc["AAA", "action"] == "BUY" and pd.isna(ch.loc["AAA", "est_value_vnd"])
    assert ch.loc["BBB", "action"] == "SELL" and np.isclose(ch.loc["BBB", "est_value_vnd"], -300 * 10_000)

    # New fund (empty prev) and closed/liquidated top-10 (empty new)
    empty = H([])
    assert set(diff_holdings(empty, new).action) == {"NEW_IN_TOP10"}
    assert set(diff_holdings(prev, empty).action) == {"EXIT_TOP10"}

    # Signed net flow across funds
    two_funds = pd.DataFrame([
        {"ticker": "XYZ", "action": "BUY", "basis": "volume", "est_value_vnd": 5e9},
        {"ticker": "XYZ", "action": "SELL", "basis": "volume", "est_value_vnd": -2e9},
        {"ticker": "XYZ", "action": "PRORATA", "basis": "volume", "est_value_vnd": 9e9},
    ])
    flow = market_flow(two_funds).set_index("ticker").loc["XYZ"]
    assert flow.n_buy == 1 and flow.n_sell == 1 and np.isclose(flow.net_value_bn, 3.0)


def test_corporate_action_ignores_bonds_and_is_permutation_invariant():
    cols = ["observed_date", "fund", "mgmt_co", "ticker", "action", "basis", "w_prev", "w_new", "w_delta",
            "vol_prev", "vol_new", "vol_pct", "vol_rel", "vol_scale", "est_value_vnd", "censored", "flag", "asset_class"]
    rows = [
        ("2026-07-08", "F1", "co", "BD1", "BUY", "volume", .05, .06, .01, 100, 120, .20, .20, 0, 1e9, False, "", "BOND"),
        ("2026-07-08", "F2", "co", "BD1", "BUY", "volume", .05, .06, .01, 100, 120, .20, .20, 0, 1e9, False, "", "BOND"),
        ("2026-07-08", "F3", "co", "BD1", "BUY", "volume", .05, .06, .01, 100, 120, .20, .20, 0, 1e9, False, "", "BOND"),
        ("2026-07-08", "F1", "co", "EQ1", "BUY", "volume", .05, .06, .01, 100, 115, .15, .15, 0, 1e9, False, "", "STOCK"),
        ("2026-07-08", "F2", "co", "EQ1", "BUY", "volume", .05, .06, .01, 100, 115.2, .152, .152, 0, 1e9, False, "", "STOCK"),
        ("2026-07-08", "F3", "co", "EQ1", "BUY", "volume", .05, .06, .01, 100, 114.8, .148, .148, 0, 1e9, False, "", "STOCK"),
    ]
    cur = pd.DataFrame(rows, columns=cols)
    flagged = flag_corporate_actions(cur, None, min_funds=3, spread=0.01, min_pct=0.03)
    flagged_rev = flag_corporate_actions(cur.iloc[::-1].reset_index(drop=True), None, min_funds=3, spread=0.01, min_pct=0.03)
    assert (flagged[flagged.asset_class == "BOND"].action == "BUY").all()
    assert (flagged[flagged.asset_class == "STOCK"].action == "CORP_ACTION?").all()
    pd.testing.assert_frame_equal(
        flagged.sort_values(["asset_class", "fund"]).reset_index(drop=True),
        flagged_rev.sort_values(["asset_class", "fund"]).reset_index(drop=True),
    )


def test_csv_preserves_na_nan_identifiers_and_tablestore_float_hash(tmp_path):
    from vnfa.fmarket import parse_allocation

    s = Store(tmp_path)
    s.upsert_funds([{"code": "NA", "mgmt_co": "CO", "asset_code": "STOCK"}], "2026-10-01")
    assert s.fund_names() == {"NA": "CO"}

    hold = H([("NA", .10, 1000, 10, 10e6), ("NAN", .08, np.nan, np.nan, np.nan), ("NULL", .06, 500, 12, 6e6)])
    status, _ = s.append_if_changed("NA", hold, "2026-10-01")
    assert status == "baseline"
    read_back = s.read_holdings("NA")
    assert read_back.fund.tolist() == ["NA", "NA", "NA"]
    assert read_back.ticker.tolist() == ["NA", "NAN", "NULL"]
    assert pd.isna(read_back.set_index("ticker").loc["NAN", "volume"])

    # Same-day STOCK and BOND changes for identical (fund, ticker) must not overwrite each other.
    s.append_changes(pd.DataFrame([
        {"observed_date": "2026-10-02", "fund": "NA", "ticker": "NA", "action": "NEW_IN_TOP10", "asset_class": "STOCK"},
        {"observed_date": "2026-10-02", "fund": "NA", "ticker": "NA", "action": "NEW_IN_TOP10", "asset_class": "BOND"},
    ]))
    ch = s.read_changes()
    assert len(ch) == 2 and set(ch.asset_class) == {"STOCK", "BOND"}
    assert ch.fund.tolist() == ["NA", "NA"] and ch.ticker.tolist() == ["NA", "NA"]

    # Historical IEEE-754 float noise in allocation.csv must match clean rounded parse_allocation output.
    legacy_alloc = pd.DataFrame([{
        "observed_date": "2026-10-01", "fund": "F1", "asset_code": "STOCK",
        "asset_name": "Cổ phiếu", "pct": "0.20980000000000001", "report_date": "2026-09-01",
        "content_hash": "legacyhash01",
    }])
    legacy_alloc.to_csv(tmp_path / "allocation.csv", sep=";", index=False)
    ts = TableStore(tmp_path)
    parsed = parse_allocation({"productAssetHoldingList": [
        {"assetType": {"code": "STOCK", "name": "Cổ phiếu"}, "assetPercent": 20.98, "reportTime": "2026-09-01"}
    ]})
    assert parsed.pct.iloc[0] == 0.2098
    assert ts.append_if_changed("allocation", "F1", parsed, "2026-10-02") == "unchanged"
