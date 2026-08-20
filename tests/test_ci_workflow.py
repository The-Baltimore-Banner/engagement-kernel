"""The CI workflow must keep running the gate, and keep failing on it.

These are text assertions on the committed workflow, which makes them coarse:
they prove the step is still there and has not been given an escape hatch. They
do not prove the workflow runs -- only a real pull request does that. Their value
is catching the specific, quiet regression this repository cannot afford: a leak
gate softened to a warning.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_workflow_runs_on_pull_requests() -> None:
    assert "pull_request:" in _text()


def test_workflow_runs_every_required_check() -> None:
    text = _text()
    for fragment in ("ruff check", "pytest", "gitleaks", "tools/leak_scan.py"):
        assert fragment in text, f"CI no longer runs: {fragment}"


def test_no_step_is_allowed_to_fail_softly() -> None:
    text = _text()
    for escape in ("continue-on-error", "|| true", "exit 0"):
        assert escape not in text, f"CI contains a failure escape hatch: {escape}"
