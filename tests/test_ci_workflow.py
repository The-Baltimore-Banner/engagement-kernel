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


def test_the_leak_scan_job_supplies_the_out_of_tree_deny_list() -> None:
    """The wiring that makes `deny-name` more than decoration.

    Text assertions, and coarse for it -- but the regression they catch is the exact
    state this repository shipped in for months: the job running with no deny file,
    so the rule compiled zero patterns and matched nothing behind a green check.
    """
    text = _text()
    assert "LEAK_SCAN_DENY_TOML" in text, "the leak-scan job no longer reads the deny secret"
    assert "LEAK_SCAN_DENY_FILE" in text, "the scanner is not told where the deny list is"
    assert "RUNNER_TEMP" in text, (
        "the deny list must be written outside the workspace: the scanner enumerates "
        "the tree with git ls-files, so one inside the checkout is scannable and "
        "committable"
    )


def test_the_leak_scan_job_asserts_the_deny_list_is_not_empty() -> None:
    """Without this the fix reverts silently the moment the secret is unset.

    A missing deny file raises; an empty one does not. An unset secret writes an
    empty file, so exit code alone cannot tell a working gate from an inert one.
    """
    assert "--require-deny-names 1" in _text(), (
        "the leak-scan job no longer asserts a positive count of loaded name terms"
    )


def test_the_fork_limitation_is_stated_next_to_the_job() -> None:
    """A rule that silently does not apply on one trigger is how the gap was born."""
    text = _text()
    assert "fork" in text.lower()
    assert "::warning" in text, "a fork run must say deny-name is not in force, not stay quiet"


def test_provisioning_is_written_down_and_names_nothing() -> None:
    """A secret only one person can rebuild is a gate that expires with them."""
    doc = WORKFLOW.parents[2] / "docs" / "leak-scan-provisioning.md"
    assert doc.is_file(), "the deny-list provisioning note is missing"
    text = doc.read_text(encoding="utf-8")
    assert "LEAK_SCAN_DENY_TOML" in text
    assert "[deny]" in text
    assert "gh secret set" in text


def test_workflow_runs_every_required_check() -> None:
    text = _text()
    for fragment in (
        "ruff check",
        "pytest",
        "gitleaks",
        "tools/leak_scan.py",
        "tools/import_closure_check.py",
    ):
        assert fragment in text, f"CI no longer runs: {fragment}"


def test_the_import_closure_job_installs_the_core_dependencies_only() -> None:
    """Installing the dev extra there would defeat the check.

    The claim is that the reference engine runs on the four core dependencies.
    A test extra can pull a transitive vendor library, which would then satisfy
    exactly the import the check exists to refuse -- and the job would still be
    green.
    """
    text = _text()
    job = text.split("import-closure:", 1)[1].split("\n  secret-scan:", 1)[0]
    assert "pip install -e ." in job
    assert "[dev]" not in job, "the import-closure job must not install the dev extra"


def test_no_step_is_allowed_to_fail_softly() -> None:
    text = _text()
    for escape in ("continue-on-error", "|| true", "exit 0"):
        assert escape not in text, f"CI contains a failure escape hatch: {escape}"
