#!/usr/bin/env python3
"""Export sanitized, static dashboard data from the local repository archive.

This command reads repository files only. It does not call Fmarket or any other API.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.dashboard_export import export_dashboard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data",
                        help="Existing pipeline archive directory (default: %(default)s)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dashboard" / "data",
                        help="Static JSON artifact directory (default: %(default)s)")
    parser.add_argument("--reference-date", help="Vietnam-local freshness reference date (YYYY-MM-DD)")
    parser.add_argument("--generated-at", help="Pinned timezone-aware export timestamp for reproducible builds")
    parser.add_argument("--nav-years", type=int, default=5, help="Bounded NAV history window, 1..10 years")
    parser.add_argument("--max-events", type=int, default=25_000, help="Maximum exported recent event rows")
    args = parser.parse_args()

    manifest = export_dashboard(
        args.data_dir,
        args.output_dir,
        generated_at=args.generated_at,
        reference_date=args.reference_date,
        nav_years=args.nav_years,
        max_events=args.max_events,
    )
    coverage = manifest["source_coverage"]
    print(
        "Dashboard export complete: "
        f"{len(manifest['datasets'])} datasets, "
        f"data_directory_exists={coverage['data_directory_exists']}, "
        f"available={coverage['datasets_available']}, missing={coverage['datasets_missing']}, "
        f"output={args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
