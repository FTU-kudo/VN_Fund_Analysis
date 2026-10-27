# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

from vnfa.config import load_config
from vnfa.diff import diff_holdings, flag_corporate_actions, market_flow
from vnfa.fmarket import parse_detail
from vnfa.store import Store


def H(rows):
    return pd.DataFrame(rows, columns=["ticker", "weight", "volume", "price_k", "asset_value"]).assign(
        industry="x", update_date="2026-10-01")


def test_buy_sell_new_exit():
    prev = H([("AAA", .10, 1000, 10, 10e6), ("BBB", .08, 1000, 10, 10e6), ("CCC", .05, 500, 10, 5e6), ("DDD", .04, 100, 10, 1e6)])
    new = H([("AAA", .09, 1200, 10, 12e6), ("BBB", .07, 800, 10, 8e6), ("CCC", .05, 510, 10, 5.1e6), ("EEE", .04, 100, 10, 1e6)])
    ch = diff_holdings(prev, new, 0.05, 0.005).set_index("ticker")
    assert ch.loc["AAA", "action"] == "BUY" and abs(ch.loc["AAA", "vol_pct"] - 0.2) < 1e-9
    assert ch.loc["AAA", "est_value_vnd"] == 200 * 10000  # 200 cp x giá ngầm định 10.000đ
    assert ch.loc["BBB", "action"] == "SELL" and ch.loc["BBB", "est_value_vnd"] < 0
    assert "CCC" not in ch.index                       # đổi 2% < ngưỡng 5%
    assert ch.loc["DDD", "action"] == "EXIT_TOP10" and ch.loc["DDD", "censored"]
    assert ch.loc["EEE", "action"] == "NEW_IN_TOP10"


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


def test_weight_fallback_when_no_volume():
    prev = H([("AAA", .10, np.nan, np.nan, np.nan)])
    new = H([("AAA", .12, np.nan, np.nan, np.nan)])
    ch = diff_holdings(prev, new, 0.05, 0.005)
    assert ch.iloc[0].action == "BUY" and ch.iloc[0].basis == "weight"


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


def test_store_append_only_when_changed(tmp_path):
    s = Store(tmp_path)
    h1 = H([("AAA", .1, 1000, 10, 10e6)])
    assert s.append_if_changed("F", h1, "2026-01-01")[0] == "baseline"
    assert s.append_if_changed("F", h1, "2026-01-02")[0] == "unchanged"
    st, prev = s.append_if_changed("F", H([("AAA", .1, 1100, 10, 11e6)]), "2026-01-03")
    assert st == "changed" and prev.volume.astype(float).iloc[0] == 1000
    assert s.read_holdings("F").observed_date.nunique() == 2
    assert s.last_disclosure("F").observed_date.iloc[0] == "2026-01-03"


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
    assert info["n_changed"] == 1 and ch.iloc[0].action == "BUY"
    assert len(s.read_changes()) == 1
