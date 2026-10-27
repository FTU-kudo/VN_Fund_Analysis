#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a rate-limited, classified Fmarket connectivity/schema diagnostic."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.config import load_config  # noqa: E402
from vnfa.diagnostics import probe_sources  # noqa: E402
from vnfa.fmarket import FmarketClient  # noqa: E402
from vnfa.provenance import ProvenanceStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--output", default=None, help="JSON report path (default: reports/source_diagnostic.json)")
    ap.add_argument("--delay", type=float, default=None, help="increase configured delay per request (never lower it)")
    ap.add_argument("--timeout", type=int, default=None, help="override configured timeout in seconds")
    ap.add_argument("--skip-nav", action="store_true", help="skip the NAV-history endpoint probe")
    a = ap.parse_args()

    cfg = load_config(a.config)
    configured_delay = cfg["http"]["delay_seconds"]
    delay = configured_delay if a.delay is None else a.delay
    timeout = cfg["http"]["timeout_seconds"] if a.timeout is None else a.timeout
    if delay < configured_delay or timeout <= 0:
        ap.error("--delay may only increase the configured rate limit and --timeout must be > 0")

    client = FmarketClient(delay=delay, timeout=timeout)
    report = probe_sources(client, include_nav=not a.skip_nav)
    store = ProvenanceStore(a.data_dir)
    report["source_event_rows_appended"] = store.append_source_events(
        client.source_events, run_id=client.run_id)

    output = Path(a.output) if a.output else ROOT / "reports" / "source_diagnostic.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Diagnostic JSON: {output}")
    return 1 if report["overall_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
