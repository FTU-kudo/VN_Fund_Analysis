#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an inventory of repository data without fabricating absent records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vnfa.inventory import build_data_inventory, render_inventory_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--reference-date", default=None, help="YYYY-MM-DD; controls age calculations")
    ap.add_argument("--json-out", default=str(ROOT / "reports" / "data_inventory.json"))
    ap.add_argument("--markdown-out", default=str(ROOT / "reports" / "data_inventory.md"))
    ap.add_argument("--stdout-only", action="store_true", help="print Markdown only; write no files")
    a = ap.parse_args()

    inventory = build_data_inventory(a.data_dir, reference_date=a.reference_date)
    markdown = render_inventory_markdown(inventory)
    print(markdown)
    if not a.stdout_only:
        json_path, markdown_path = Path(a.json_out), Path(a.markdown_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(markdown, encoding="utf-8")
        print(f"Inventory JSON: {json_path}")
        print(f"Inventory Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
