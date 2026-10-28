# -*- coding: utf-8 -*-
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import requests

from vnfa.diagnostics import classify_exception, probe_sources
from vnfa.fmarket import (FmarketClient, FmarketEmptyResponse, FmarketError,
                          FmarketSchemaError)
from vnfa.inventory import build_data_inventory, render_inventory_markdown
from vnfa.provenance import (OBSERVATION_COLUMNS, QUALITY_COLUMNS, SOURCE_COLUMNS,
                             ProvenanceSchemaError, ProvenanceStore, make_observation)
from vnfa.quality import (DataQualityError, require_valid, validate_allocation,
                          validate_fund_universe, validate_holdings, validate_nav)
from vnfa.temporal import PointInTimeError, add_availability, select_as_of


def test_validation_distinguishes_missing_unusual_and_invalid():
    missing = validate_holdings(None)
    assert missing.status == "unavailable"
    assert missing.warnings[0].code == "dataset_missing"

    unusual = validate_holdings(pd.DataFrame([
        {"ticker": "AAA", "weight": 1.05, "volume": np.nan, "price_k": 10,
         "asset_value": 10, "update_date": "2026-01-01"}
    ]), coverage_type="full")
    assert unusual.status == "partial"
    assert not unusual.errors
    assert {finding.code for finding in unusual.warnings} >= {"unusual_weight", "missing_volume"}

    invalid = validate_holdings(pd.DataFrame([
        {"ticker": "AAA", "weight": -0.1}, {"ticker": "AAA", "weight": 0.2}
    ]))
    assert invalid.status == "invalid"
    assert {finding.code for finding in invalid.errors} >= {"negative_weight", "duplicate_ticker"}
    with pytest.raises(DataQualityError):
        require_valid(invalid)


def test_validation_handles_nullable_values_schema_and_allocation():
    frame = pd.DataFrame({"ticker": pd.Series([pd.NA], dtype="string"),
                          "weight": pd.Series([pd.NA], dtype="Float64")})
    report = validate_holdings(frame)
    assert report.status == "invalid"
    assert "invalid_weight" in {finding.code for finding in report.errors}

    schema = validate_fund_universe(pd.DataFrame([{"wrong_column": "F1"}]))
    assert schema.status == "invalid"
    assert schema.errors[0].code == "schema_missing_columns"

    allocation = validate_allocation(pd.DataFrame([
        {"asset_code": "STOCK", "pct": 1.1, "report_date": None}
    ]))
    assert allocation.status == "partial"
    assert not allocation.errors
    assert {finding.code for finding in allocation.warnings} >= {"unusual_allocation_pct", "missing_report_date"}


def test_nav_validation_flags_bad_dates_duplicates_and_nonpositive_values():
    report = validate_nav(pd.DataFrame([
        {"fund": "F1", "date": "2026-01-01", "nav": 0},
        {"fund": "F1", "date": "2026-01-01", "nav": "bad"},
        {"fund": "F2", "date": "not-a-date", "nav": 1},
    ]))
    assert report.status == "invalid"
    assert {finding.code for finding in report.errors} >= {
        "invalid_nav_date", "invalid_nav", "nonpositive_nav", "duplicate_nav_key"
    }


def test_point_in_time_late_publication_and_future_observation():
    records = pd.DataFrame([
        {"dataset": "holdings", "fund": "F1", "record_key": "AAA", "revision": "v1",
         "effective_date": "2025-12-31", "observed_at": "2026-01-02T10:00:00Z",
         "published_at": "2026-01-05T10:00:00Z"},
        {"dataset": "holdings", "fund": "F1", "record_key": "BBB", "revision": "future",
         "effective_date": "2026-01-01", "observed_at": "2026-01-06T10:00:00Z",
         "published_at": None},
    ])
    early = select_as_of(records, "2026-01-04T23:59:59Z", key_columns=["dataset", "fund", "record_key"])
    assert early.empty  # effective date is not an availability time; publication is later

    after_publish = select_as_of(records, "2026-01-05T10:00:01Z", key_columns=["dataset", "fund", "record_key"])
    assert after_publish.revision.tolist() == ["v1"]
    assert after_publish.iloc[0].pit_available_at == pd.Timestamp("2026-01-05T10:00:00Z")

    future = select_as_of(records, "2026-01-05T23:59:59Z")
    assert future.revision.tolist() == ["v1"]


def test_point_in_time_selects_only_latest_revision_available_at_cutoff():
    records = pd.DataFrame([
        {"dataset": "fund_holdings", "fund": "F1", "record_key": "AAA", "revision": "old",
         "effective_date": "2026-01-31", "observed_at": "2026-02-02T10:00:00Z",
         "published_at": "2026-02-02T12:00:00Z", "ingested_at": "2026-02-02T10:01:00Z"},
        {"dataset": "fund_holdings", "fund": "F1", "record_key": "AAA", "revision": "new",
         "effective_date": "2026-01-31", "observed_at": "2026-02-10T10:00:00Z",
         "published_at": "2026-02-10T12:00:00Z", "ingested_at": "2026-02-10T10:01:00Z"},
    ])
    key = ["dataset", "fund", "record_key"]
    assert select_as_of(records, "2026-02-05T00:00:00Z", key_columns=key).revision.tolist() == ["old"]
    assert select_as_of(records, "2026-02-11T00:00:00Z", key_columns=key).revision.tolist() == ["new"]


def test_unknown_and_approximate_observation_times_are_never_pit_safe():
    records = pd.DataFrame([
        {"record_key": "effective-only", "effective_date": "2020-01-01", "published_at": "2020-01-02T00:00:00Z"},
        {"record_key": "approximate", "observed_date": "2020-01-02", "observed_date_basis": "approximate"},
        {"record_key": "unknown", "observed_at": None, "published_at": None},
    ])
    safe = select_as_of(records, "2026-01-01")
    assert safe.empty
    descriptive = select_as_of(records, "2026-01-01", unknown_time="include")
    assert len(descriptive) == 3
    assert not descriptive.pit_eligible.any()
    assert descriptive.pit_availability_status.eq("unknown_time_included_not_pit_safe").all()


def test_date_only_availability_is_end_of_day_vietnam_and_future_effective_is_excluded():
    legacy = pd.DataFrame([{"record_key": "A", "observed_date": "2026-10-27",
                            "observed_date_basis": "verified_date_only"}])
    during_day = select_as_of(legacy, "2026-10-27T12:00:00+07:00")
    assert during_day.empty
    end_of_day = select_as_of(legacy, "2026-10-27")
    assert len(end_of_day) == 1
    assert end_of_day.iloc[0].pit_time_precision == "date_only_conservative"

    untagged = pd.DataFrame([{"observed_date": "2026-10-27"}])
    assert select_as_of(untagged, "2026-10-27").empty
    assert len(select_as_of(untagged, "2026-10-27", allow_legacy_date_only=True)) == 1

    future_effective = pd.DataFrame([{"observed_at": "2026-10-27T05:00:00Z",
                                      "effective_date": "2026-10-28"}])
    selected, counts = add_availability(future_effective, "2026-10-27T15:59:00Z")
    assert not selected.pit_eligible.iloc[0]
    assert counts["excluded_future_effective"] == 1

    with pytest.raises(PointInTimeError):
        select_as_of(legacy, "2026-10-27T12:00:00")


def test_provenance_append_is_idempotent_and_preserves_revisions(tmp_path):
    store = ProvenanceStore(tmp_path)
    first = make_observation(
        dataset="fund_holdings", fund="F1", record_key="AAA", payload={"weight": 0.1},
        source="fmarket", source_reference="/products/1", effective_date="2026-01-31",
        observed_at="2026-02-02T10:00:00Z", published_at=None, run_id="r1",
        data_quality_status="partial", quality_flags=["censored_top_n"], coverage_type="top_n")
    assert store.append_observations([first], run_id="r1") == 1
    assert store.append_observations([first], run_id="r1") == 0
    same_content_later_poll = make_observation(
        dataset="fund_holdings", fund="F1", record_key="AAA", payload={"weight": 0.1},
        source="fmarket", source_reference="/products/1", effective_date="2026-01-31",
        observed_at="2026-02-03T10:00:00Z", published_at=None, run_id="r2",
        data_quality_status="partial", quality_flags=["censored_top_n"], coverage_type="top_n")
    assert store.append_observations([same_content_later_poll], run_id="r2") == 0
    assert store.read_observations().iloc[0].observed_at == first["observed_at"]

    revised = make_observation(
        dataset="fund_holdings", fund="F1", record_key="AAA", payload={"weight": 0.12},
        source="fmarket", source_reference="/products/1", effective_date="2026-01-31",
        observed_at="2026-02-10T10:00:00Z", published_at="2026-02-10T12:00:00Z", run_id="r2",
        data_quality_status="partial", quality_flags=["censored_top_n"], coverage_type="top_n")
    assert store.append_observations([revised], run_id="r2") == 1
    rows = store.read_observations()
    assert len(rows) == 2
    assert set(rows.record_hash) == {first["record_hash"], revised["record_hash"]}
    key = ["dataset", "fund", "record_key"]
    assert select_as_of(rows, "2026-02-05T00:00:00Z", key_columns=key).record_hash.tolist() == [first["record_hash"]]
    assert select_as_of(rows, "2026-02-11T00:00:00Z", key_columns=key).record_hash.tolist() == [revised["record_hash"]]


def test_provenance_keeps_unknown_times_blank_and_source_event_is_sanitized(tmp_path):
    store = ProvenanceStore(tmp_path)
    observation = make_observation(
        dataset="full_holdings", fund="F2", record_key="XYZ", payload={"period_end": "2025-12-31"},
        source="official_manager_report", source_reference="https://manager.example/report.pdf",
        effective_date="2025-12-31", observed_at=None, published_at=None,
        data_quality_status="partial")
    store.append_observations([observation])
    row = store.read_observations().iloc[0]
    assert pd.isna(row.observed_at) and pd.isna(row.published_at)
    assert select_as_of(store.read_observations(), "2026-01-01").empty

    event = {"event_id": "event-1", "run_id": "r1", "provider": "fmarket",
             "endpoint": "GET /products/1", "source_reference": "/products/1",
             "started_at": "2026-01-01T00:00:00Z", "observed_at": "2026-01-01T00:00:01Z",
             "completed_at": "2026-01-01T00:00:01Z", "status": "rate_limit",
             "error_type": "rate_limit", "http_status": 429, "record_count": None,
             "response_hash": hashlib.sha256(b"response").hexdigest(), "retry_policy": "total=4",
             "retry_attempts": 4}
    assert store.append_source_events([event], run_id="r1") == 1
    assert store.append_source_events([event], run_id="r1") == 0
    saved = store.read_source_events().iloc[0]
    assert saved.status == "rate_limit" and saved.http_status == "429"
    assert "response_body" not in saved.index  # response body is never persisted


def test_quality_ledger_idempotency_and_schema_mismatch_is_non_destructive(tmp_path):
    store = ProvenanceStore(tmp_path)
    report = {"dataset": "fund_nav", "fund": "F1", "status": "partial", "row_count": 1,
              "findings": [{"code": "missing_nav", "severity": "warning", "message": "unknown", "count": 1}],
              "metrics": {"coverage": 0.5}}
    assert store.append_quality_reports([report], run_id="r1") == 1
    assert store.append_quality_reports([report], run_id="r1") == 0
    assert store.read_quality_reports().iloc[0].status == "partial"

    other = tmp_path / "other"
    other.mkdir()
    source_file = other / "source_runs.csv"
    source_file.write_text("event_id;legacy_column\ne1;x\n", encoding="utf-8")
    legacy_store = ProvenanceStore(other)
    before = source_file.read_bytes()
    with pytest.raises(ProvenanceSchemaError):
        legacy_store.append_source_events([{"event_id": "e2"}])
    assert source_file.read_bytes() == before
    assert len(SOURCE_COLUMNS) > 2 and len(OBSERVATION_COLUMNS) > 5 and len(QUALITY_COLUMNS) > 5


def test_fmarket_client_records_classified_shape_and_response_hash(monkeypatch):
    client = FmarketClient(delay=0, run_id="test-run")
    response = type("Response", (), {})()
    response.status_code = 200
    response.content = b'{"status":200,"data":{"rows":[]}}'
    response.raw = type("Raw", (), {"retries": type("Retries", (), {"history": []})()})()
    response.json = lambda: {"status": 200, "data": {"rows": []}}
    response.raise_for_status = lambda: None
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)

    assert client.list_products() == []
    event = client.source_events[0]
    assert event["status"] == "empty_response"
    assert event["http_status"] == 200
    assert event["response_hash"] == hashlib.sha256(response.content).hexdigest()
    assert "data" not in event and "body" not in event


def test_fmarket_client_classifies_schema_and_http_failures(monkeypatch):
    client = FmarketClient(delay=0)
    malformed = type("Response", (), {})()
    malformed.status_code, malformed.content = 200, b'{"status":200,"data":{"unexpected":[]}}'
    malformed.raw = type("Raw", (), {"retries": type("Retries", (), {"history": []})()})()
    malformed.json = lambda: {"status": 200, "data": {"unexpected": []}}
    malformed.raise_for_status = lambda: None
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: malformed)
    with pytest.raises(FmarketSchemaError):
        client.list_products()
    assert client.source_events[-1]["status"] == "schema_error"

    response = type("Response", (), {})()
    response.status_code, response.content = 403, b"{}"
    response.raw = type("Raw", (), {"retries": type("Retries", (), {"history": []})()})()
    response.json = lambda: {}
    response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError("forbidden", response=response))
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)
    with pytest.raises(requests.HTTPError):
        client.list_products()
    assert client.source_events[-1]["status"] == "authentication_error"


def test_daily_ingestion_persists_exact_observation_and_unknown_publication(tmp_path):
    import importlib.util
    from vnfa.config import load_config
    from vnfa.store import Store

    spec = importlib.util.spec_from_file_location("run_daily_provenance", "scripts/run_daily.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)

    class FakeClient:
        run_id = "fixture-run"
        source_events = []

        def __init__(self):
            self.last_source_event = None

        def list_products(self):
            self.last_source_event = {"observed_at": "2026-10-27T00:00:00Z"}
            return [{"id": 1, "code": "F1", "shortName": "F1", "owner": {"shortName": "CO"},
                     "dataFundAssetType": {"code": "STOCK"}}]

        def product_detail(self, _product_id):
            self.last_source_event = {"observed_at": "2026-10-27T00:01:00Z"}
            return {"id": 1, "code": "F1", "nav": 10000,
                    "owner": {"shortName": "CO"}, "assetType": {"code": "STOCK"},
                    "topHoldings": [{"stockCode": "AAA", "netAssetPercent": 5, "price": 10,
                                     "volume": 100, "assetValue": 1000, "type": "STOCK",
                                     "updateAt": 1791447322898}]}

    store = Store(tmp_path)
    info, _ = rd.run(load_config(None), FakeClient(), store, "2026-10-27")
    observations = ProvenanceStore(tmp_path).read_observations()
    holding = observations[observations.dataset == "fund_holdings"].iloc[0]
    assert holding.observed_at == "2026-10-27T00:01:00Z"
    assert pd.isna(holding.published_at)
    assert pd.notna(holding.ingested_at)
    assert holding.effective_date == "2026-10-08"
    assert holding.verification_status == "provider_returned_unverified"
    assert holding.coverage_type == "top_n"
    assert info["n_provenance_observations"] >= 3


def test_source_diagnostic_reports_success_empty_and_classifies_errors():
    class FakeClient:
        run_id = "fake"
        source_events = []
        last_source_event = None

        def list_products(self):
            return [{"id": 1, "code": "F1"}]

        def product_detail(self, product_id):
            assert product_id == 1
            return {"id": 1, "code": "F1"}

        def nav_history(self, product_id):
            return pd.DataFrame(columns=["date", "nav"])

    report = probe_sources(FakeClient())
    assert report["overall_status"] == "partial"
    assert report["live_access_verified"] is False
    assert report["terms_verified"] is False
    assert [probe["status"] for probe in report["probes"]] == ["success", "success", "empty_response"]

    class EmptyClient(FakeClient):
        def list_products(self):
            return []

    empty = probe_sources(EmptyClient())
    assert empty["probes"][0]["status"] == "empty_response"
    assert empty["probes"][1]["status"] == "not_run"

    assert classify_exception(FmarketError("", 403)) == ("authentication_error", 403)
    assert classify_exception(FmarketError("", 429)) == ("rate_limit", 429)
    assert classify_exception(requests.Timeout()) == ("network_error", None)
    assert classify_exception(FmarketSchemaError("schema"))[0] == "schema_error"
    assert classify_exception(FmarketEmptyResponse("empty"))[0] == "empty_response"


def test_full_holdings_import_keeps_revisions_and_unknown_publication_out_of_pit(tmp_path, monkeypatch):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("import_full_holdings_v2", "scripts/import_full_holdings.py")
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    source = tmp_path / "incoming.csv"
    output_dir = tmp_path / "data"

    def write_input(weight: str):
        pd.DataFrame([
            {"fund": "F1", "ticker": "AAA", "weight": weight, "period_end": "2026-09-30",
             "source_url": "https://user:secret@manager.example/report.pdf?token=do-not-store"},
            {"fund": "F1", "ticker": "BBB", "weight": "0.3", "period_end": "2026-09-30",
             "source_url": "https://manager.example/report.pdf"},
        ]).to_csv(source, sep=";", index=False)

    write_input("0.5")
    monkeypatch.setattr(sys, "argv", ["import_full_holdings.py", str(source), "--data-dir", str(output_dir),
                                       "--weight-unit", "fraction"])
    importer.main()
    write_input("0.55")
    monkeypatch.setattr(sys, "argv", ["import_full_holdings.py", str(source), "--data-dir", str(output_dir),
                                       "--weight-unit", "fraction"])
    importer.main()

    latest = pd.read_csv(output_dir / "holdings_full.csv", sep=";")
    assert len(latest) == 2
    assert latest.set_index("ticker").loc["AAA", "weight"] == 0.55
    assert "observed_at" not in latest.columns  # provenance sidecar does not silently change the legacy view
    assert "secret" not in " ".join(latest.source_url)
    assert "token=" not in " ".join(latest.source_url)

    observations = ProvenanceStore(output_dir).read_observations()
    full = observations[observations.dataset == "full_holdings"]
    assert len(full) == 3  # two initial rows plus a traceable revised AAA record
    assert full.observed_at.isna().all() and full.published_at.isna().all()
    assert "secret" not in " ".join(full.source_reference.fillna(""))
    assert select_as_of(full, "2026-10-27").empty
    assert set(full.effective_date) == {"2026-09-30"}


def test_historical_nav_backfill_provenance_only_does_not_touch_legacy_csv(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill_nav_provenance_only", "scripts/backfill_hat_ccq.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    def fake_details(_repo, _sha):
        return [{"code": "F1", "nav": 10000}]

    monkeypatch.setattr(backfill, "commit_details", fake_details)
    data_dir = tmp_path / "data"
    backfill.nav_snapshots(tmp_path / "mirror", [("sha1", "2026-01-01")], str(data_dir), write_legacy=False)
    assert not (data_dir / "nav_snapshots.csv").exists()
    observations = ProvenanceStore(data_dir).read_observations()
    assert len(observations) == 1
    assert observations.iloc[0].observed_date_basis == "approximate"
    assert pd.isna(observations.iloc[0].observed_at)
    assert select_as_of(observations, "2026-01-02").empty


def test_full_backfill_provenance_only_preserves_newer_legacy_holdings(tmp_path, monkeypatch):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("backfill_main_provenance_only", "scripts/backfill_hat_ccq.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    repo = tmp_path / "mirror"
    repo.mkdir()
    data_dir = tmp_path / "data"
    hold_dir = data_dir / "holdings"
    hold_dir.mkdir(parents=True)
    legacy = hold_dir / "F1.csv"
    original = pd.DataFrame([{
        "observed_date": "2026-10-01", "fund": "F1", "ticker": "AAA", "weight": .1,
        "rank": 1, "volume": 100, "price_k": 10, "asset_value": 1000, "industry": "x",
        "update_date": "2026-09-30", "content_hash": "oldhash",
    }])
    original.to_csv(legacy, sep=";", index=False)
    before = legacy.read_bytes()
    detail = {"id": 1, "code": "F1", "nav": 10000,
              "assetType": {"code": "STOCK"},
              "topHoldings": [{"stockCode": "BBB", "netAssetPercent": 5, "volume": 200,
                               "price": 10, "assetValue": 2000, "type": "STOCK"}]}

    def fake_git(_repo, *args, **kwargs):
        return b"sha1 2026-01-01\n" if args and args[0] == "log" else b""

    monkeypatch.setattr(backfill, "git", fake_git)
    monkeypatch.setattr(backfill, "commit_details", lambda _repo, _sha: [detail])
    monkeypatch.setattr(sys, "argv", ["backfill_hat_ccq.py", "--src", str(repo), "--data-dir",
                                       str(data_dir), "--provenance-only"])
    backfill.main()

    assert legacy.read_bytes() == before
    assert not (data_dir / "changes.csv").exists()
    assert not (data_dir / "nav_snapshots.csv").exists()
    observations = ProvenanceStore(data_dir).read_observations()
    assert {"fund_holdings", "nav_snapshot"}.issubset(set(observations.dataset))
    assert select_as_of(observations, "2026-10-27").empty


def test_historical_backfill_auto_skips_newer_legacy_holdings(tmp_path, monkeypatch):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("backfill_auto_backdate", "scripts/backfill_hat_ccq.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    repo = tmp_path / "mirror"
    repo.mkdir()
    data_dir = tmp_path / "data"
    hold_dir = data_dir / "holdings"
    hold_dir.mkdir(parents=True)
    legacy = hold_dir / "F1.csv"
    original = pd.DataFrame([{
        "observed_date": "2026-10-01", "fund": "F1", "ticker": "AAA", "weight": .1,
        "rank": 1, "volume": 100, "price_k": 10, "asset_value": 1000, "industry": "x",
        "update_date": "2026-09-30", "content_hash": "oldhash",
    }])
    original.to_csv(legacy, sep=";", index=False)
    before = legacy.read_bytes()
    detail = {"id": 1, "code": "F1", "nav": 10000,
              "assetType": {"code": "STOCK"},
              "topHoldings": [{"stockCode": "BBB", "netAssetPercent": 5, "volume": 200,
                               "price": 10, "assetValue": 2000, "type": "STOCK"}]}

    monkeypatch.setattr(backfill, "git", lambda _repo, *args, **kwargs:
                        (b"sha1 2026-01-01" + bytes([10])) if args and args[0] == "log" else b"")
    monkeypatch.setattr(backfill, "commit_details", lambda _repo, _sha: [detail])
    monkeypatch.setattr(sys, "argv", ["backfill_hat_ccq.py", "--src", str(repo), "--data-dir", str(data_dir)])
    backfill.main()

    assert legacy.read_bytes() == before
    assert not (data_dir / "changes.csv").exists()
    observations = ProvenanceStore(data_dir).read_observations()
    holdings = observations[observations.dataset == "fund_holdings"]
    assert len(holdings) == 1 and holdings.iloc[0].observed_date_basis == "approximate"
    assert (data_dir / "nav_snapshots.csv").exists()  # separate, schema-preserving NAV materialization


def test_manual_import_requires_explicit_timezone_for_timestamps(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("import_full_holdings_timezone", "scripts/import_full_holdings.py")
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    path = tmp_path / "timezone.csv"
    pd.DataFrame([{"fund": "F1", "ticker": "AAA", "weight": 0.5, "period_end": "2026-09-30",
                   "source_url": "https://manager.example/report.pdf",
                   "observed_at": "2026-10-01T10:00:00"}]).to_csv(path, sep=";", index=False)
    with pytest.raises(SystemExit, match="timezone"):
        importer.load_clean(path, weight_unit="fraction")


def test_actual_data_inventory_does_not_fabricate_absent_data(tmp_path):
    missing = build_data_inventory(tmp_path / "no-data", reference_date="2026-10-27")
    assert not missing["data_directory_exists"]
    assert missing["summary"]["datasets_missing"] == len(missing["datasets"])
    assert missing["summary"]["csv_rows_observed"] == 0
    assert missing["source_coverage"]["status"] == "not_recorded"
    assert "P0" == missing["prioritized_next_steps"][0]["priority"]
    assert "Missing is not zero" in render_inventory_markdown(missing)


def test_data_inventory_uses_only_present_files_and_reports_real_coverage(tmp_path):
    data = tmp_path / "data"
    (data / "holdings").mkdir(parents=True)
    pd.DataFrame([{"code": "F1", "mgmt_co": "CO", "asset_code": "STOCK"}]).to_csv(
        data / "funds.csv", sep=";", index=False)
    pd.DataFrame([{"observed_date": "2026-10-01", "fund": "F1", "ticker": "AAA",
                   "weight": 0.1, "update_date": "2026-09-30", "content_hash": "h1"}]).to_csv(
        data / "holdings" / "F1.csv", sep=";", index=False)
    pd.DataFrame([{"fund": "F1", "date": "2026-10-01", "nav": 10000}]).to_csv(
        data / "nav.csv", sep=";", index=False)
    pd.DataFrame([{"fund": "F1", "pct": 0.5}]).to_csv(data / "allocation.csv", sep=";", index=False)
    store = ProvenanceStore(data)
    observation = make_observation(
        dataset="fund_holdings", fund="F1", record_key="AAA", payload={"weight": 0.1},
        source="fmarket", source_reference="/products/1", effective_date="2026-09-30",
        observed_at="2026-10-02T00:00:00Z", published_at=None, data_quality_status="partial")
    store.append_observations([observation])
    store.append_source_events([{"event_id": "e1", "provider": "fmarket", "endpoint": "GET /products/1",
                                "status": "success", "completed_at": "2026-10-02T00:00:01Z",
                                "http_status": 200}])

    inventory = build_data_inventory(data, reference_date="2026-10-27")
    assert inventory["datasets"]["fund_holdings"]["row_count"] == 1
    assert inventory["datasets"]["fund_holdings"]["fund_count"] == 1
    assert inventory["datasets"]["fund_holdings"]["coverage"]["portfolio_coverage"] == "top_n_partial_censored"
    assert inventory["datasets"]["allocation"]["status"] == "schema_mismatch"
    assert inventory["datasets"]["research_observations"]["provenance_coverage"]["observed_at"] == 1.0
    assert inventory["source_coverage"]["outcomes"] == {"success": 1}
    assert inventory["summary"]["funds_observed_across_tables"] == 1
    text = render_inventory_markdown(inventory)
    assert "top_n_partial_censored" not in text  # machine-readable qualification stays in JSON
    assert "schema_mismatch" in text


def test_compact_provenance_reconciles_nav_series_and_enforces_90d_budget(tmp_path):
    import importlib.util
    import pytest
    from vnfa.provenance import observations_from_frame

    spec_c = importlib.util.spec_from_file_location("compact_provenance", "scripts/compact_provenance.py")
    cp = importlib.util.module_from_spec(spec_c)
    spec_c.loader.exec_module(cp)

    spec_b = importlib.util.spec_from_file_location("check_data_budget", "scripts/check_data_budget.py")
    cb = importlib.util.module_from_spec(spec_b)
    spec_b.loader.exec_module(cb)

    data = tmp_path / "data"
    data.mkdir()
    nav_df = pd.DataFrame([
        {"fund": "F1", "date": f"2026-09-{d:02d}", "nav": 10000.0 + d * 10}
        for d in range(1, 21)
    ])
    nav_df.to_csv(data / "nav.csv", sep=";", index=False)

    pstore = ProvenanceStore(data)
    hold_obs = make_observation(
        dataset="fund_holdings", fund="F1", record_key="AAA", payload={"weight": 0.1},
        source="fmarket", source_reference="/products/1", effective_date="2026-09-20",
        observed_at="2026-09-21T06:00:00Z", observed_date="2026-09-21",
        observed_date_basis="response_observed_at", data_quality_status="partial",
    )
    point_nav_obs = observations_from_frame(
        nav_df[["date", "nav"]], dataset="nav_history", fund="F1", source="fmarket",
        source_reference="/product/get-nav-history", observed_at="2026-09-21T06:00:01Z",
        key_columns=("date",), effective_date_column="date", run_id="run-1",
        data_quality_status="valid", verification_status="provider_returned_unverified",
        coverage_type="history_endpoint",
    )
    pstore.append_observations([hold_obs, *point_nav_obs], run_id="run-1")

    # Point-level nav_history triggers budget failure until compacted.
    pre_budget = cb.evaluate_data_budget(data)
    assert pre_budget["status"] == "fail" and pre_budget["point_level_nav_observations"] == 20

    # Refuses backup directory inside Git repository.
    with pytest.raises(ValueError, match="Git repository"):
        cp.compact_observations(data, backup_dir=cp.ROOT / "tmp_forbidden_backup", dry_run=False)

    # Dry-run leaves file untouched.
    before_bytes = (data / "research_observations.csv").read_bytes()
    dry = cp.compact_observations(data, dry_run=True)
    assert dry["dry_run"] is True and dry["nav_point_rows_compacted"] == 20 and dry["after_rows"] == 2
    assert (data / "research_observations.csv").read_bytes() == before_bytes

    # Live migration writes verified backup + compacts 20 rows -> 1 summary row, preserving fund_holdings verbatim.
    bdir = tmp_path / "backup_outside_repo"
    res1 = cp.compact_observations(data, backup_dir=bdir, dry_run=False)
    assert res1["after_rows"] == 2 and res1["nav_points_accounted_for"] == 20
    assert (bdir / "research_observations.csv.bak").read_bytes() == before_bytes

    # Pass 2 is 100% idempotent.
    res2 = cp.compact_observations(data, backup_dir=bdir, dry_run=False)
    assert res2["already_compact"] is True and res2["after_sha256"] == res1["after_sha256"]
    assert select_as_of(ProvenanceStore(data, write=False).read_observations(), "2026-09-22").shape[0] == 2

    # Repository's real data/ directory passes 90-day budget cleanly (< 25 MB warn, 0 point-level nav obs).
    repo_budget = cb.evaluate_data_budget("data", warn_mb=25.0, fail_mb=80.0, horizon_days=90)
    assert repo_budget["status"] == "ok"
    assert repo_budget["point_level_nav_observations"] == 0
    assert repo_budget["projected_total_mb"] < 25.0


def test_run_daily_records_observed_date_and_nav_history_summary(tmp_path):
    import importlib.util
    from vnfa.config import load_config
    from vnfa.store import Store

    spec = importlib.util.spec_from_file_location("run_daily_prov", "scripts/run_daily.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)

    class FakeLiveClient:
        run_id = "run-live-1"
        last_source_event = {"observed_at": "2026-10-27T06:35:00Z"}

        def list_products(self):
            return [{"id": 1, "code": "F1", "shortName": "F1", "owner": {"shortName": "CO"},
                     "dataFundAssetType": {"code": "STOCK"}}]

        def product_detail(self, _):
            return {"id": 1, "code": "F1", "nav": 12345.0, "owner": {"shortName": "CO"},
                    "assetType": {"code": "STOCK"},
                    "topHoldings": [{"stockCode": "AAA", "netAssetPercent": 6.0, "price": 10,
                                     "volume": 1000, "assetValue": 10_000_000, "type": "STOCK"}]}

        def nav_history(self, _):
            return pd.DataFrame([
                {"date": f"2026-10-{d:02d}", "nav": 12000.0 + d}
                for d in range(1, 16)
            ])

    cfg = load_config(None)
    store = Store(tmp_path)
    info, _ = rd.run(cfg, FakeLiveClient(), store, "2026-10-27")
    assert info["n_nav_funds"] == 1
    obs = ProvenanceStore(tmp_path, write=False).read_observations()
    nav_hist = obs[obs.dataset == "nav_history"]
    assert len(nav_hist) == 1  # 15 NAV history rows summarized into 1 provenance row!
    assert str(nav_hist.iloc[0].record_key).startswith("summary:")
    hold_obs = obs[obs.dataset == "fund_holdings"]
    assert hold_obs.iloc[0].observed_date == "2026-10-27"
    assert hold_obs.iloc[0].observed_date_basis == "response_observed_at"


def test_freshness_sla_states_bond_price_noise_and_source_runs_migration(tmp_path, monkeypatch):
    import importlib.util

    spec_m = importlib.util.spec_from_file_location("migrate_source_runs", "scripts/migrate_source_runs.py")
    ms = importlib.util.module_from_spec(spec_m)
    spec_m.loader.exec_module(ms)

    data = tmp_path / "data"
    (data / "holdings").mkdir(parents=True)
    (data / "bonds").mkdir(parents=True)
    pd.DataFrame([{"observed_date": "2026-10-27", "fund": "F1", "ticker": "AAA",
                   "weight": 0.1, "volume": 100, "price_k": 10, "asset_value": 1000000,
                   "update_date": "2026-10-15", "content_hash": "h1"}]).to_csv(
        data / "holdings" / "F1.csv", sep=";", index=False)
    # Bond holdings structurally lack price_k in Fmarket; must NOT emit missing_price_k anomaly warning!
    pd.DataFrame([{"observed_date": "2026-10-27", "fund": "F1", "ticker": "BD1",
                   "weight": 0.1, "volume": 50, "price_k": np.nan, "asset_value": 500000,
                   "update_date": "2026-10-15", "content_hash": "b1"}]).to_csv(
        data / "bonds" / "F1.csv", sep=";", index=False)
    # Stale allocation (report_date 60 days old > 45d SLA)
    pd.DataFrame([{"observed_date": "2026-10-27", "fund": "F1", "asset_code": "STOCK",
                   "pct": 0.8, "report_date": "2026-08-25", "content_hash": "a1"}]).to_csv(
        data / "allocation.csv", sep=";", index=False)
    # Empty fees dataset (header only) and schema_mismatch nav_snapshots
    pd.DataFrame(columns=["observed_date", "fund", "type", "fee", "content_hash"]).to_csv(
        data / "fees.csv", sep=";", index=False)
    pd.DataFrame([{"fund": "F1", "wrong_col": 1}]).to_csv(data / "nav_snapshots.csv", sep=";", index=False)

    # Without SLA configured -> not_assessed_no_sla
    inv_no_sla = build_data_inventory(data, reference_date="2026-10-27")
    assert inv_no_sla["datasets"]["allocation"]["freshness"]["assessment"] == "not_assessed_no_sla"
    bond_q = inv_no_sla["datasets"]["fund_bond_holdings"]["quality"]
    assert bond_q["anomaly_warnings"] == []
    assert [w["code"] for w in bond_q["structural_warnings"]] == ["censored_top_n"]

    # With SLA configured -> separates within_sla, stale, empty_dataset, schema_mismatch, unavailable_missing!
    sla = {"fund_holdings": 45, "allocation": 45, "fees": 180, "nav_snapshots": 3, "full_holdings": 45}
    inv_sla = build_data_inventory(data, reference_date="2026-10-27", freshness_sla=sla)
    assert inv_sla["datasets"]["fund_holdings"]["freshness"]["assessment"] == "within_sla"
    assert inv_sla["datasets"]["allocation"]["freshness"]["assessment"] == "stale"
    assert inv_sla["datasets"]["fees"]["freshness"]["assessment"] == "empty_dataset"
    assert inv_sla["datasets"]["nav_snapshots"]["freshness"]["assessment"] == "schema_mismatch"
    assert inv_sla["datasets"]["full_holdings"]["freshness"]["assessment"] == "unavailable_missing"
    assert inv_sla["datasets"]["industry"]["freshness"]["assessment"] == "not_assessed_no_sla"

    # Migrate v1 source_runs.csv -> v2 preserving unknown fund_code, then append new v2 event with fund_code.
    from vnfa.provenance import SOURCE_COLUMNS_V1
    v1_row = {col: "" for col in SOURCE_COLUMNS_V1}
    v1_row.update({"event_id": "legacy-evt-1", "provider": "fmarket", "endpoint": "GET /products/1",
                   "status": "success", "http_status": "200"})
    pd.DataFrame([v1_row], columns=SOURCE_COLUMNS_V1).to_csv(data / "source_runs.csv", sep=";", index=False)
    assert ProvenanceStore(data, write=False).read_source_events()["fund_code"].isna().all()
    dry = ms.migrate_source_runs(data, dry_run=True)
    assert dry["dry_run"] is True and dry["already_v2"] is False
    mig = ms.migrate_source_runs(data, backup_dir=tmp_path / "sr_bak", dry_run=False)
    assert mig["already_v2"] is False and mig["rows"] == 1
    assert ms.migrate_source_runs(data, backup_dir=tmp_path / "sr_bak", dry_run=False)["already_v2"] is True

    client = FmarketClient(delay=0, run_id="run-v2")
    resp = type("Resp", (), {
        "status_code": 200, "content": b'{"status":200,"data":{"id":7,"code":"DCDS"}}',
        "raw": None, "raise_for_status": lambda self=None: None,
        "json": lambda self=None: {"status": 200, "data": {"id": 7, "code": "DCDS"}},
    })()
    monkeypatch.setattr(client.session, "request", lambda *_a, **_kw: resp)
    client.product_detail(7, fund_code="DCDS")
    assert client.last_source_event["fund_code"] == "DCDS"
    assert ProvenanceStore(data).append_source_events(client.source_events, run_id="run-v2") == 1
    events_df = ProvenanceStore(data, write=False).read_source_events()
    assert len(events_df) == 2
    assert pd.isna(events_df.iloc[0]["fund_code"])  # historical row kept unknown!
    assert events_df.iloc[1]["fund_code"] == "DCDS"


def test_report_and_notify_render_safely_without_leaking_tokens(monkeypatch, caplog):
    import requests
    from vnfa.config import load_config
    from vnfa.notify import send_telegram
    from vnfa.report import render_report, render_text

    cfg = load_config(None)
    changes = pd.DataFrame([
        {"observed_date": "2026-10-27", "fund": "F1", "mgmt_co": "CO", "ticker": "FPT",
         "action": "BUY", "basis": "volume", "w_prev": 0.05, "w_new": 0.07, "w_delta": 0.02,
         "vol_prev": 1000, "vol_new": 1300, "vol_pct": 0.30, "vol_rel": 0.30, "vol_scale": 0.0,
         "est_value_vnd": 3e9, "censored": False, "flag": "FUND_SCALE_MEDIAN_PROXY", "asset_class": "STOCK"},
        {"observed_date": "2026-10-27", "fund": "F2", "mgmt_co": "CO2", "ticker": "HPG",
         "action": "SELL", "basis": "volume", "w_prev": 0.08, "w_new": 0.05, "w_delta": -0.03,
         "vol_prev": 2000, "vol_new": 1200, "vol_pct": -0.40, "vol_rel": -0.40, "vol_scale": 0.0,
         "est_value_vnd": -2e9, "censored": False, "flag": "FUND_SCALE_MEDIAN_PROXY", "asset_class": "STOCK"},
        {"observed_date": "2026-10-27", "fund": "F3", "mgmt_co": "CO3", "ticker": "MBB",
         "action": "WEIGHT_CHANGE?", "basis": "weight", "w_prev": 0.04, "w_new": 0.06, "w_delta": 0.02,
         "vol_prev": np.nan, "vol_new": np.nan, "vol_pct": np.nan, "vol_rel": np.nan, "vol_scale": np.nan,
         "est_value_vnd": np.nan, "censored": False, "flag": "WEIGHT_ONLY_UNCONFIRMED", "asset_class": "STOCK"},
    ])
    info = {
        "n_ok": 3, "n_total": 3, "n_failed": 0, "n_partial": 0, "n_changed": 3, "n_baseline": 0,
        "n_empty_holdings": 1, "n_nav_invalid": 1, "n_nav_history_failed": 0,
        "n_quality_reports": 3, "n_quality_errors": 0, "n_quality_warnings": 4, "n_quality_rejected": 0,
        "n_provenance_observations": 10, "n_source_events": 4, "empty_tables": {"fees": 1}, "failed": ["F9"],
    }
    md = render_report("2026-10-27", changes, info, cfg)
    assert "Tăng vị thế ròng ước tính" in md and "Giảm vị thế ròng ước tính" in md
    assert "Thay đổi chưa xác nhận" in md and "Watchlist" in md
    txt = render_text("2026-10-27", changes, info, cfg)
    assert "FPT +3.0tỷ" in txt and "HPG -2.0tỷ" in txt
    assert "Không có thay đổi" in render_text("2026-10-27", changes.iloc[0:0], info, cfg)

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert send_telegram("hi") is False

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SUPER_SECRET_BOT_TOKEN_123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99999")
    monkeypatch.setattr(requests, "post", lambda *_a, **_kw: (_ for _ in ()).throw(requests.ConnectionError("boom")))
    assert send_telegram("hi") is False
    assert "SUPER_SECRET_BOT_TOKEN_123" not in caplog.text


def test_inventory_markdown_vietnamese_and_default_english(tmp_path):
    from vnfa.inventory import build_data_inventory, render_inventory_markdown
    inv = build_data_inventory(tmp_path / 'missing', reference_date='2026-10-27')
    vi = render_inventory_markdown(inv, lang='vi')
    assert 'Báo cáo kiểm kê' in vi
    assert 'Thiếu không phải là 0' in vi
    assert 'published_at' in vi and 'observed_at' in vi
    assert '# Data inventory and coverage report' in render_inventory_markdown(inv)


def test_reaction_english_cli_offline(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'scripts/analyze_reaction.py'),
                             '--data-dir', str(tmp_path), '--lang', 'en',
                             '--out', str(tmp_path / 'report.md')],
                            capture_output=True, text=True, check=True)
    assert 'Fund reactions since' in result.stdout
    assert 'not confirmed' not in result.stdout or 'not confirmed trades' in result.stdout
    assert 'publication time is unknown' in result.stdout
    assert 'not investment advice' in result.stdout
    assert (tmp_path / 'report.md').read_text(encoding='utf-8') == result.stdout.rstrip() + '\n'
