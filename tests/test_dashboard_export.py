from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from vnfa.diff import CHANGE_COLUMNS
from vnfa.dashboard_export import export_dashboard, sanitize_reference
from vnfa.inventory import build_data_inventory
from vnfa.provenance import OBSERVATION_COLUMNS, QUALITY_COLUMNS, SOURCE_COLUMNS
from vnfa.quality import validate_bond_holdings, validate_nav_snapshot

GENERATED_AT = "2026-10-27T00:00:00Z"
REFERENCE_DATE = "2026-10-27"


def write_frame(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, sep=";", index=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_data_exports_versioned_empty_envelopes_without_zeroes(tmp_path):
    data = tmp_path / "data-does-not-exist"
    output = tmp_path / "public"
    manifest = export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)

    assert manifest["schema_version"] == "1.0.0"
    assert manifest["source_coverage"]["data_directory_exists"] is False
    assert manifest["source_coverage"]["datasets_available"] == 0
    for name in ("funds", "holdings", "nav_history", "portfolio_events", "fund_facts", "source_events"):
        envelope = read_json(output / f"{name}.json")
        assert envelope["status"] == "missing"
        assert envelope["record_count"] is None
        assert envelope["records"] == []
    pipeline = read_json(output / "pipeline_status.json")
    assert pipeline["summary"]["overall_workflow_status"] == "unknown_not_exported"
    quality = read_json(output / "data_quality_summary.json")
    assert quality["status"] == "data_unavailable"
    assert quality["summary"]["source_time_coverage"]["observation_records"] is None
    assert (output / "manifest.json").is_file()


def test_header_only_sources_are_empty_not_missing(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    data.mkdir()
    write_frame(data / "funds.csv", [], ["code", "name", "short_name", "mgmt_co", "asset_code"])
    write_frame(data / "changes.csv", [], CHANGE_COLUMNS)
    write_frame(data / "source_runs.csv", [], SOURCE_COLUMNS)
    write_frame(data / "research_observations.csv", [], OBSERVATION_COLUMNS)
    write_frame(data / "nav.csv", [], ["fund", "date", "nav"])

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    assert read_json(output / "funds.json")["status"] == "empty"
    assert read_json(output / "funds.json")["record_count"] == 0
    assert read_json(output / "portfolio_events.json")["status"] == "empty"
    assert read_json(output / "portfolio_events.json")["record_count"] == 0
    sources = read_json(output / "source_events.json")
    assert sources["status"] == "empty" and sources["record_count"] == 0
    assert read_json(output / "nav_history.json")["status"] == "partial"
    assert read_json(output / "nav_history.json")["record_count"] is None
    quality = read_json(output / "data_quality_summary.json")["summary"]
    assert quality["source_time_coverage"]["observation_records"] == 0


def test_malformed_and_schema_changed_inputs_remain_visible(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    data.mkdir()
    write_frame(data / "funds.csv", [{"not_code": "F1"}])
    write_frame(data / "nav.csv", [{"fund": "F1", "date": "2026-01-01", "wrong_nav": "100"}])
    write_frame(data / "changes.csv", [{"date": "2026-01-01", "ticker": "AAA"}])
    write_frame(data / "holdings" / "F1.csv", [{"ticker": "AAA", "weight": "0.1"}])
    write_frame(data / "quality_reports.csv", [{"bad_header": "not the ledger"}])
    (data / "last_run.json").write_text("{not json", encoding="utf-8")

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    assert read_json(output / "funds.json")["status"] == "schema_mismatch"
    assert read_json(output / "holdings.json")["status"] == "schema_mismatch"
    assert read_json(output / "nav_history.json")["status"] == "schema_mismatch"
    assert read_json(output / "portfolio_events.json")["status"] == "schema_mismatch"
    assert read_json(output / "data_quality.json")["status"] == "schema_mismatch"
    assert read_json(output / "pipeline_status.json")["status"] == "unreadable"
    assert read_json(output / "pipeline_status.json")["summary"]["overall_workflow_status"] == "unknown_not_exported"


def test_symlink_inputs_are_refused_including_broken_links(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    data.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("code;name\nF1;Secret\n", encoding="utf-8")
    try:
        (data / "funds.csv").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    with pytest.raises(ValueError, match="symbolic links"):
        export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    (data / "funds.csv").unlink()
    (data / "broken.csv").symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(ValueError, match="symbolic links"):
        export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)


def test_output_cannot_be_written_inside_raw_archive(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with pytest.raises(ValueError, match="outside data_dir"):
        export_dashboard(data, data / "dashboard", generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)


def test_export_bounds_holdings_nav_events_and_sanitizes_secrets(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    data.mkdir()
    write_frame(data / "funds.csv", [{
        "code": "F1", "name": "Fund One", "short_name": "F1", "mgmt_co": "Manager",
        "asset_code": "STOCK", "management_fee": "0.01", "last_seen": "2026-10-20",
    }])
    write_frame(data / "holdings" / "F1.csv", [
        {"observed_date": "2025-01-01", "fund": "F1", "ticker": "OLD", "weight": "0.01", "rank": "1", "volume": "10", "price_k": "10", "asset_value": "100", "industry": "Old", "update_date": "2024-12-31", "content_hash": "old"},
        {"observed_date": "2026-01-01", "fund": "F1", "ticker": "AAA", "weight": "0.10", "rank": "1", "volume": "100", "price_k": "10", "asset_value": "1000", "industry": "Tech", "update_date": "2025-12-31", "content_hash": "new"},
        {"observed_date": "2026-01-01", "fund": "F1", "ticker": "BBB", "weight": "0.05", "rank": "2", "volume": "50", "price_k": "10", "asset_value": "500", "industry": "Bank", "update_date": "2025-12-31", "content_hash": "new"},
    ])
    write_frame(data / "holdings_full.csv", [{
        "fund": "F1", "ticker": "FULL", "weight": "0.02", "period_end": "2026-06-30",
        "observed_date": "2026-07-15", "source_url": "https://user:pass@example.test/facts?token=PRIVATE",
        "rank": "3", "volume": "20",
    }])
    write_frame(data / "nav.csv", [
        {"fund": "F1", "date": "2020-01-01", "nav": "900"},
        {"fund": "F1", "date": "2026-01-01", "nav": "1000"},
        {"fund": "F1", "date": "2026-02-01", "nav": "1020"},
    ])
    write_frame(data / "nav_snapshots.csv", [
        {"fund": "F1", "date": "2026-02-01", "nav": "1020"},
    ])
    changes = [
        {"observed_date": "2026-08-01", "fund": "F1", "mgmt_co": "Manager", "ticker": "AAA", "action": "UNCONFIRMED?", "basis": "volume", "w_prev": "0.10", "w_new": "0.11", "w_delta": "0.01", "vol_prev": "10", "vol_new": "12", "vol_pct": "0.2", "vol_rel": None, "vol_scale": None, "est_value_vnd": None, "censored": "False", "flag": "FUND_SCALE_UNIDENTIFIED", "asset_class": "STOCK"},
        {"observed_date": "2022-01-01", "fund": "F1", "mgmt_co": "Manager", "ticker": "OLD", "action": "EXIT_TOP10", "basis": "rank", "w_prev": "0.02", "w_new": None, "w_delta": "-0.02", "vol_prev": "5", "vol_new": None, "vol_pct": None, "vol_rel": None, "vol_scale": None, "est_value_vnd": None, "censored": "True", "flag": "", "asset_class": "STOCK"},
        {"observed_date": "2026-03-01", "fund": "F1", "mgmt_co": "Manager", "ticker": "BBB", "action": "WEIGHT_CHANGE?", "basis": "weight", "w_prev": "0.05", "w_new": "0.06", "w_delta": "0.01", "vol_prev": None, "vol_new": None, "vol_pct": None, "vol_rel": None, "vol_scale": None, "est_value_vnd": None, "censored": "False", "flag": "WEIGHT_ONLY_UNCONFIRMED", "asset_class": "STOCK"},
        {"observed_date": "2026-04-01", "fund": "F1", "mgmt_co": "Manager", "ticker": "CCC", "action": "CORP_ACTION?", "basis": "volume", "w_prev": "0.01", "w_new": "0.01", "w_delta": "0", "vol_prev": "10", "vol_new": "20", "vol_pct": "1", "vol_rel": None, "vol_scale": None, "est_value_vnd": None, "censored": "False", "flag": "CORP_ACTION?", "asset_class": "STOCK"},
        {"observed_date": "2020-01-01", "fund": "F1", "mgmt_co": "Manager", "ticker": "VERY-OLD", "action": "NEW_IN_TOP10", "basis": "rank", "w_prev": None, "w_new": "0.01", "w_delta": "0.01", "vol_prev": None, "vol_new": None, "vol_pct": None, "vol_rel": None, "vol_scale": None, "est_value_vnd": None, "censored": "True", "flag": "", "asset_class": "STOCK"},
    ]
    write_frame(data / "changes.csv", changes, CHANGE_COLUMNS)
    source = {column: None for column in SOURCE_COLUMNS}
    source.update({
        "event_id": "evt-1", "run_id": "private-run-id", "provider": "fmarket",
        "endpoint": "GET https://api.example.test/v1/funds?access_token=PRIVATE",
        "source_reference": "https://user:pass@api.example.test/v1/funds?api_key=PRIVATE#fragment",
        "started_at": "2026-10-26T10:00:00Z", "observed_at": "2026-10-26T10:00:01Z",
        "completed_at": "2026-10-26T10:00:02Z", "status": "ok", "error_type": "",
        "http_status": "200", "record_count": "1", "response_hash": "hash-only",
        "retry_policy": "none", "retry_attempts": "0", "ingested_at": "2026-10-26T10:00:03Z",
    })
    write_frame(data / "source_runs.csv", [source], SOURCE_COLUMNS)
    observation = {column: None for column in OBSERVATION_COLUMNS}
    observation.update({
        "observation_id": "private-observation-id", "run_id": "private-run-id", "dataset": "fund_holdings",
        "fund": "F1", "record_key": "AAA", "effective_date": "2025-12-31",
        "observed_at": "2026-01-02T10:00:00Z", "published_at": None,
        "ingested_at": "2026-01-02T10:00:01Z", "observed_date": "2026-01-01",
        "observed_date_basis": "response_observed_at", "source": "fmarket",
        "source_reference": "https://user:pass@api.example.test/products/1?token=PRIVATE",
        "record_hash": "hash", "payload_json": '{"api_token":"PRIVATE"}',
        "data_quality_status": "partial", "verification_status": "unverified",
        "quality_flags": "[]", "coverage_type": "top_n", "schema_version": "1",
    })
    write_frame(data / "research_observations.csv", [observation], OBSERVATION_COLUMNS)

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE, max_events=4)
    holdings = read_json(output / "holdings.json")
    assert holdings["status"] == "available"
    assert holdings["record_count"] == 3
    assert {row["ticker"] for row in holdings["records"]} == {"AAA", "BBB", "FULL"}
    assert next(row for row in holdings["records"] if row["ticker"] == "FULL")["coverage_type"] == "source_claimed_full_not_independently_verified"
    assert next(row for row in holdings["records"] if row["ticker"] == "AAA")["observed_at"] == "2026-01-02T10:00:00Z"

    nav = read_json(output / "nav_history.json")
    assert nav["truncated"] is True
    assert all(row["date"] != "2020-01-01" for row in nav["records"])
    snapshot = next(row for row in nav["records"] if row["series_type"] == "nav_snapshot")
    assert snapshot["effective_date"] is None
    assert snapshot["date_semantics"] == "observed_date_effective_date_unknown"

    events = read_json(output / "portfolio_events.json")
    assert events["truncated"] is True
    assert len(events["records"]) == 4
    event_map = {row["action"]: row for row in events["records"]}
    assert event_map["UNCONFIRMED?"]["evidence_status"] == "unconfirmed"
    assert event_map["UNCONFIRMED?"]["is_confirmed_transaction"] is False
    assert event_map["WEIGHT_CHANGE?"]["evidence_status"] == "unconfirmed"
    assert event_map["EXIT_TOP10"]["censored"] is True
    assert event_map["CORP_ACTION?"]["evidence_status"] == "suspected"

    source_events = read_json(output / "source_events.json")
    endpoint = source_events["records"][0]["endpoint"]
    assert endpoint == "GET https://api.example.test/v1/funds"
    bundle_text = "\n".join(path.read_text(encoding="utf-8") for path in output.glob("*.json"))
    for forbidden in ("PRIVATE", "user:pass", "private-run-id", "private-observation-id", "api_token", "access_token"):
        assert forbidden not in bundle_text
    assert "unknown_not_exported" in bundle_text

    inventory = build_data_inventory(data, reference_date=REFERENCE_DATE)
    nav_quality = inventory["datasets"]["nav_snapshots"]["quality"]
    assert nav_quality["status"] == "valid"
    assert "effective_date_unknown" in {finding["code"] for finding in nav_quality["warnings"]}


def test_invalid_fund_catalogue_rows_are_partial_not_empty(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    write_frame(data / "funds.csv", [{"code": "", "name": "No fund id"}], ["code", "name"])

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    funds = read_json(output / "funds.json")
    assert funds["status"] == "partial"
    assert funds["records"] == []
    assert funds["catalogue_source_rows"] == 1
    assert funds["invalid_catalogue_rows"] == 1


def test_nav_rows_with_invalid_values_are_partial_not_empty(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    write_frame(data / "nav.csv", [{"fund": "F1", "date": "2026-01-01", "nav": "-100"}], ["fund", "date", "nav"])
    write_frame(data / "nav_snapshots.csv", [], ["fund", "date", "nav"])

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    nav = read_json(output / "nav_history.json")
    assert nav["status"] == "partial"
    assert nav["records"] == []
    assert nav["record_count"] is None
    assert nav["source_rows_seen"] == 1
    assert nav["invalid_rows_excluded"] == 1


def test_event_window_chooses_latest_dates_not_last_source_rows(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    data.mkdir()
    rows = []
    for day, ticker in (("2026-08-01", "NEW"), ("2022-01-01", "OLD"), ("2026-03-01", "MID")):
        rows.append({"observed_date": day, "fund": "F1", "mgmt_co": "M", "ticker": ticker,
                     "action": "UNCONFIRMED?", "basis": "volume", "w_prev": None, "w_new": None,
                     "w_delta": None, "vol_prev": None, "vol_new": None, "vol_pct": None, "vol_rel": None,
                     "vol_scale": None, "est_value_vnd": None, "censored": "False", "flag": "", "asset_class": "STOCK"})
    write_frame(data / "changes.csv", rows, CHANGE_COLUMNS)
    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE, max_events=2)
    exported = read_json(output / "portfolio_events.json")
    assert [row["ticker"] for row in exported["records"]] == ["MID", "NEW"]
    assert exported["truncated"] is True


def test_source_and_quality_ledgers_export_bounded_latest_projections(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "public"
    source_rows = []
    for index in range(2_005):
        row = {column: None for column in SOURCE_COLUMNS}
        row.update({
            "event_id": f"event-{index}", "run_id": f"run-{index}", "provider": "fmarket",
            "endpoint": "GET https://api.example.test/funds?token=DO-NOT-EXPORT",
            "started_at": f"2026-10-26T00:{index % 60:02d}:00Z",
            "observed_at": f"2026-10-26T00:{index % 60:02d}:01Z",
            "completed_at": f"2026-10-26T00:{index % 60:02d}:02Z", "status": "ok",
            "http_status": "200", "record_count": "1", "retry_attempts": "0",
        })
        source_rows.append(row)
    write_frame(data / "source_runs.csv", source_rows, SOURCE_COLUMNS)
    quality_rows = []
    for event_id, count, timestamp in (("old", "1", "2026-10-26T00:00:00Z"),
                                       ("new", "2", "2026-10-27T00:00:00Z")):
        row = {column: None for column in QUALITY_COLUMNS}
        row.update({"quality_event_id": event_id, "run_id": f"run-{event_id}",
                    "dataset": "fund_holdings", "fund": "F1", "source": "validator",
                    "status": "warning", "row_count": "10",
                    "findings_json": json.dumps([{"code": "missing_source", "severity": "warning", "count": count}]),
                    "metrics_json": "{}", "recorded_at": timestamp})
        quality_rows.append(row)
    write_frame(data / "quality_reports.csv", quality_rows, QUALITY_COLUMNS)

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    source = read_json(output / "source_events.json")
    assert source["status"] == "partial"
    assert source["truncated"] is True
    assert source["record_count"] == 2_000
    assert source["persisted_record_count"] == 2_005
    assert len(source["records"]) == 2_000
    pipeline = read_json(output / "pipeline_status.json")["summary"]
    assert pipeline["source_request_count"] == 2_005
    assert pipeline["source_request_exported_count"] == 2_000
    assert pipeline["source_request_outcomes"] == {"ok": 2_005}
    quality = read_json(output / "data_quality.json")
    assert quality["status"] == "partial"
    assert quality["record_count"] == 1
    assert quality["persisted_record_count"] == 2
    assert quality["records"][0]["recorded_at"] == "2026-10-27T00:00:00Z"
    assert quality["records"][0]["findings"][0]["count"] == 2
    assert "DO-NOT-EXPORT" not in json.dumps({"source": source, "quality": quality})


def test_bond_and_nav_snapshot_validators_keep_source_specific_statuses(tmp_path):
    bond = validate_bond_holdings(pd.DataFrame([{"ticker": "BOND-A", "weight": -0.1}]))
    assert bond.dataset == "fund_bond_holdings"
    assert bond.status == "invalid"
    assert "negative_weight" in {finding.code for finding in bond.errors}

    data = tmp_path / "data"
    write_frame(data / "bonds" / "F1.csv", [{
        "observed_date": "2026-01-01", "fund": "F1", "ticker": "BOND-A", "weight": "-0.1",
        "rank": "1", "volume": "10", "price_k": "10", "asset_value": "100", "update_date": "2025-12-31",
    }])
    inventory = build_data_inventory(data, reference_date=REFERENCE_DATE)
    bond_quality = inventory["datasets"]["fund_bond_holdings"]["quality"]
    assert bond_quality["status"] == "invalid"
    assert "negative_weight" in {finding["code"] for finding in bond_quality["errors"]}

    snapshot = validate_nav_snapshot(pd.DataFrame([{"fund": "F1", "date": "2026-01-01", "nav": "1000"}]))
    assert snapshot.dataset == "nav_snapshots"
    assert snapshot.status == "valid"
    assert "effective_date_unknown" in {finding.code for finding in snapshot.warnings}


def test_sanitize_reference_removes_userinfo_query_and_fragment():
    assert sanitize_reference("https://user:password@example.test/a/b?token=SECRET#section") == "https://example.test/a/b"
    assert sanitize_reference("/products/123?api_key=SECRET#fragment") == "/products/123"
    assert sanitize_reference("javascript:alert(1)") is None
    assert sanitize_reference("relative/path") is None


def test_invalid_limits_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="nav_years"):
        export_dashboard(tmp_path / "data", tmp_path / "public", nav_years=11)
    with pytest.raises(ValueError, match="max_events"):
        export_dashboard(tmp_path / "data", tmp_path / "public", max_events=100_001)


def test_holdings_and_events_join_timestamp_provenance_without_explicit_observed_date(tmp_path):
    data, output = tmp_path / "data", tmp_path / "public"
    write_frame(data / "holdings" / "NA.csv", [{
        "observed_date": "2026-10-27", "fund": "NA", "ticker": "NAN", "weight": "0.10",
        "rank": "1", "volume": "1000", "price_k": "10", "asset_value": "10000000",
        "industry": "Bank", "update_date": "2026-09-30", "content_hash": "h1",
    }])
    write_frame(data / "changes.csv", [{
        "observed_date": "2026-10-27", "fund": "NA", "mgmt_co": "CO", "ticker": "NAN",
        "action": "NEW_IN_TOP10", "basis": "rank", "w_prev": "", "w_new": "0.10", "w_delta": "0.10",
        "vol_prev": "", "vol_new": "1000", "vol_pct": "", "vol_rel": "", "vol_scale": "",
        "est_value_vnd": "", "censored": "True", "flag": "", "asset_class": "STOCK",
    }], CHANGE_COLUMNS)
    observation = {column: None for column in OBSERVATION_COLUMNS}
    observation.update({
        "observation_id": "obs-ts-1", "run_id": "run-1", "dataset": "fund_holdings",
        "fund": "NA", "record_key": "NAN", "effective_date": "2026-09-30",
        "observed_at": "2026-10-27T06:35:00Z", "published_at": None,
        "ingested_at": "2026-10-27T06:37:56Z", "observed_date": None,
        "observed_date_basis": None, "source": "fmarket", "source_reference": "/products/99",
        "record_hash": "rh", "payload_json": "{}", "data_quality_status": "partial",
        "verification_status": "provider_returned_unverified", "quality_flags": "[]",
        "coverage_type": "top_n", "schema_version": "1",
    })
    write_frame(data / "research_observations.csv", [observation], OBSERVATION_COLUMNS)

    export_dashboard(data, output, generated_at=GENERATED_AT, reference_date=REFERENCE_DATE)
    hold = read_json(output / "holdings.json")["records"][0]
    assert hold["fund"] == "NA" and hold["ticker"] == "NAN"
    assert hold["observed_at"] == "2026-10-27T06:35:00Z"
    assert hold["observed_date_basis"] == "response_observed_at"
    assert hold["source"] == "fmarket" and hold["source_reference"] == "/products/99"

    evt = read_json(output / "portfolio_events.json")["records"][0]
    assert evt["fund"] == "NA" and evt["ticker"] == "NAN"
    assert evt["observed_at"] == "2026-10-27T06:35:00Z"
    assert evt["source_reference"] == "/products/99"
