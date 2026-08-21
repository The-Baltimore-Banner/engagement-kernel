"""The validator's negative controls, asserted rather than only rendered.

``docs/validator-negative-controls.md`` states that it is checked against a fresh
render by this file. It was not, until this file existed -- so the document made a
claim about a test that did not run. That is the specific failure this guards: a
generated artefact whose staleness nothing detects reads as current evidence
forever.

Two things are asserted. The document is in sync, and each case still produces
**exactly** the finding codes it declares. The second matters more: a control
that starts tripping an unrelated code has stopped proving what it says it
proves, and the rendered document would look no different.
"""

from __future__ import annotations

from pathlib import Path

import capture_negative_controls as harness
import pytest

from engagement_kernel.contract import demo, validate

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_there_are_cases_at_all() -> None:
    """An empty case list would make every parametrised test below vacuous."""
    assert len(harness.CASES) > 20


def test_the_conformant_delivery_passes() -> None:
    """The positive control.

    Every case starts from this delivery and applies one mutation. If the base
    did not validate, each case would be failing for a reason that has nothing to
    do with its mutation, and the document would look exactly the same.
    """
    delivery = harness.Delivery.conformant()
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        delivery.write(directory)
        report = validate.validate_directory(directory)
    assert report.exit_code == 0, report.render()


@pytest.mark.parametrize("case", harness.CASES, ids=lambda item: item.case_id)
def test_each_case_produces_exactly_the_codes_it_declares(case: harness.Case) -> None:
    outcome = harness.run_case(case)
    if case.expected_manifest_error:
        assert case.expected_manifest_error in outcome.rendered, outcome.rendered
    codes = {finding.code for table in outcome.report.results for finding in table.findings}
    if case.expected_codes:
        assert codes == set(case.expected_codes), (
            f"{case.case_id} produced {sorted(codes)}, declares {sorted(case.expected_codes)}"
        )
    assert outcome.report.exit_code == case.expected_exit, case.case_id


@pytest.mark.parametrize("case", harness.CASES, ids=lambda item: item.case_id)
def test_no_case_passes_validation(case: harness.Case) -> None:
    """A negative control that validates cleanly is not a control."""
    outcome = harness.run_case(case)
    assert outcome.report.exit_code != 0, (
        f"{case.case_id} was accepted by the validator, so it proves nothing"
    )


def test_the_committed_document_matches_a_fresh_render() -> None:
    committed = (REPO_ROOT / harness.DOC_RELPATH).read_text(encoding="utf-8")
    assert committed == harness.build_document(), (
        f"{harness.DOC_RELPATH} is stale. Regenerate it: "
        "python3 tools/capture_negative_controls.py --write"
    )


def test_the_document_reports_the_row_counts_of_the_current_delivery() -> None:
    """Cheap staleness tripwire, independent of the full-text comparison.

    The full comparison above already covers this. It is here because a row-count
    drift is the most likely way the document goes stale, and a test naming it
    says what happened instead of printing a thousand-line diff.
    """
    committed = (REPO_ROOT / harness.DOC_RELPATH).read_text(encoding="utf-8")
    for name, table in demo.build_tables().items():
        assert f"{name:<20} {table.num_rows} rows" in committed, (
            f"the document does not report {name} at {table.num_rows} rows"
        )
