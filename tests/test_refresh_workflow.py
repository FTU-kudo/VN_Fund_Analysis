"""Offline regression checks for the manual refresh orchestration."""
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_refresh_reuses_full_daily_collector_and_is_main_only():
    refresh = (WORKFLOWS / "refresh_fund_data.yml").read_text(encoding="utf-8")
    daily = (WORKFLOWS / "daily.yml").read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in refresh
    assert "  schedule:" not in refresh
    assert '"refs/heads/main"' in refresh
    assert '"refs/heads/$DEFAULT_BRANCH"' in refresh
    assert "needs: require-main" in refresh
    assert "uses: ./.github/workflows/daily.yml" in refresh
    assert 'limit: ""' in refresh
    assert "dry_run: false" in refresh
    assert "  workflow_call:" in daily
    assert "group: daily-fund-check" in daily
    assert "python scripts/run_daily.py" in daily
    assert "python scripts/check_data_budget.py --data-dir data" in daily
    assert "github.ref == 'refs/heads/main'" in daily
    assert "secrets: inherit" not in refresh


def test_pages_rebuilds_after_manual_refresh_and_only_deploys_outside_pr():
    pages = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert 'workflows: ["Daily fund holdings check (all funds)", "Refresh fund data now"]' in pages
    assert pages.count('".github/workflows/refresh_fund_data.yml"') == 2
    assert "github.event.workflow_run.head_branch == github.event.repository.default_branch" in pages
    assert "github.event_name != 'pull_request'" in pages
    assert "--status-scope final_workflow --status-source github_event" in pages
