#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write a strictly sanitized workflow status record (`data/workflow_status.json`).

Contains ONLY whitelisted public enum/numeric/timestamp fields — zero tokens, env vars,
actor names, runner identifiers, or raw log excerpts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_CONCLUSIONS = {"success", "failure", "cancelled", "skipped", "partial", "unknown"}
ALLOWED_TRIGGERS = {"schedule", "workflow_dispatch", "manual", "push", "unknown"}
ALLOWED_BUDGET = {"ok", "warn", "fail", "unknown"}


def write_workflow_status(
    out_path: str | Path,
    *,
    workflow: str = "daily",
    conclusion: str = "unknown",
    trigger: str = "unknown",
    collector_exit_code: int | None = None,
    budget_status: str = "unknown",
    recorded_at: str | None = None,
    status_scope: str = "collector_budget",
    status_source: str = "workflow_step",
    status_updated_at: str | None = None,
) -> dict:
    clean_conclusion = conclusion.strip().lower() if conclusion else "unknown"
    if clean_conclusion not in ALLOWED_CONCLUSIONS:
        clean_conclusion = "unknown"
    clean_trigger = trigger.strip().lower() if trigger else "unknown"
    if clean_trigger not in ALLOWED_TRIGGERS:
        clean_trigger = "unknown"
    clean_budget = budget_status.strip().lower() if budget_status else "unknown"
    if clean_budget not in ALLOWED_BUDGET:
        clean_budget = "unknown"
    ts = recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "schema_version": "1.0.0",
        "workflow": "daily" if workflow not in {"daily", "pages"} else workflow,
        "conclusion": clean_conclusion,
        "recorded_at": ts,
        "status_scope": status_scope if status_scope in {"collector_budget", "final_workflow"} else "unknown",
        "status_source": status_source if status_source in {"workflow_step", "github_event", "github_actions_api"} else "unknown",
        "status_updated_at": status_updated_at,
        "trigger": clean_trigger,
        "collector_exit_code": int(collector_exit_code) if collector_exit_code is not None else None,
        "budget_status": clean_budget,
    }
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "data" / "workflow_status.json"))
    ap.add_argument("--workflow", default="daily")
    ap.add_argument("--conclusion", required=True)
    ap.add_argument("--trigger", default="unknown")
    ap.add_argument("--collector-exit-code", type=int, default=None)
    ap.add_argument("--budget-status", default="unknown")
    ap.add_argument("--recorded-at", default=None)
    ap.add_argument("--status-scope", choices=("collector_budget", "final_workflow"), default="collector_budget")
    ap.add_argument("--status-source", choices=("workflow_step", "github_event", "github_actions_api"), default="workflow_step")
    ap.add_argument("--status-updated-at", default=None)
    a = ap.parse_args()
    write_workflow_status(
        a.out,
        workflow=a.workflow,
        conclusion=a.conclusion,
        trigger=a.trigger,
        collector_exit_code=a.collector_exit_code,
        budget_status=a.budget_status,
        recorded_at=a.recorded_at,
        status_scope=a.status_scope,
        status_source=a.status_source,
        status_updated_at=a.status_updated_at,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
