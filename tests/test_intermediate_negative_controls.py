"""Every mutation trips exactly the checks it declares, and the doc is in sync.

The harness in ``tools/capture_intermediate_negative_controls.py`` renders the
committed evidence. These tests are what make the evidence trustworthy: they run
the same cases and assert the outcome, so a case that stops discriminating breaks
the build instead of quietly becoming a paragraph nobody rereads.

Three failure modes are asserted against explicitly, because each one turns a
control into decoration:

* the mutation did not apply, so the build under test was the correct one;
* the mutated SQL did not compile, so no check ever ran;
* the build completed cleanly, so the derivation was broken and nothing noticed.

And the positive control is asserted first. In a document where everything fails,
"the checks work" and "the harness is broken" look identical.
"""

from __future__ import annotations

from pathlib import Path

import capture_intermediate_negative_controls as harness
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_unmutated_build_passes_every_check() -> None:
    """The positive control. Without it nothing below means anything."""
    ok, detail = harness.run_baseline()
    assert ok, detail
    assert "[FAIL]" not in detail


def test_there_are_controls_for_every_derivation_the_docs_claim() -> None:
    """A control set that lost a case would still pass every per-case test."""
    covered = {name for mutation in harness.MUTATIONS for name in mutation.expected_failures}
    from engagement_kernel.intermediate import checks

    assert {
        checks.CHECK_SESSIONS_MAXIMISED,
        checks.CHECK_SECTION_ATTRIBUTION,
        checks.CHECK_UNRESOLVED_SENTINEL,
        checks.CHECK_DAY_BOUNDARY_EVENTS,
        checks.CHECK_DAY_BOUNDARY_EMAIL,
        checks.CHECK_DAY_BOUNDARY_COMMUNITY,
    } <= covered


@pytest.mark.parametrize("mutation", harness.MUTATIONS, ids=lambda item: item.case_id)
def test_each_mutation_fails_exactly_the_checks_it_declares(
    mutation: harness.Mutation,
) -> None:
    outcome = harness.run_mutation(mutation)
    assert outcome.valid, f"{mutation.case_id} is not usable evidence: {outcome.invalid_reason}"
    assert set(outcome.failed_checks) == set(mutation.expected_failures), (
        f"{mutation.case_id} tripped {sorted(outcome.failed_checks)}, "
        f"declared {sorted(mutation.expected_failures)}"
    )


@pytest.mark.parametrize("mutation", harness.MUTATIONS, ids=lambda item: item.case_id)
def test_each_failure_message_names_its_own_check_and_says_what_went_wrong(
    mutation: harness.Mutation,
) -> None:
    """Assert on the message, not merely on the exception.

    An exception proves something went wrong. A message proves the build can tell
    somebody *what*, which is the difference between a check and a crash.
    """
    outcome = harness.run_mutation(mutation)
    assert outcome.valid
    for name in mutation.expected_failures:
        assert name in outcome.message
    assert "[FAIL]" in outcome.message
    assert len(outcome.message) > 200, "a message this short cannot explain a wrong number"


@pytest.mark.parametrize("mutation", harness.MUTATIONS, ids=lambda item: item.case_id)
def test_each_mutation_really_changes_the_sql(mutation: harness.Mutation) -> None:
    """A substitution that finds nothing leaves the correct build in place.

    That is the quietest way for a control to stop being a control: it keeps
    passing, and what it proves is that a correct build passes its checks.
    """
    statements, _config, _context = harness._statements_for_delivery()
    assert mutation.statement in statements, mutation.statement
    original = statements[mutation.statement]
    assert mutation.find in original, (
        f"{mutation.case_id} looks for text that is no longer in the generated SQL"
    )
    assert original.replace(mutation.find, mutation.replace, 1) != original


@pytest.mark.parametrize("refusal", harness.REFUSALS, ids=lambda item: item.case_id)
def test_each_refusal_raises_what_it_declares(refusal: harness.Refusal) -> None:
    outcome = harness.run_refusal(refusal)
    assert outcome.raised == refusal.expected_exception.__name__, outcome.message
    assert outcome.message.strip(), "a refusal with no message is a crash"


def test_the_committed_evidence_matches_a_fresh_render() -> None:
    """The document is generated. A stale one is a false claim about live code."""
    committed = (REPO_ROOT / harness.DOC_RELPATH).read_text(encoding="utf-8")
    assert committed == harness.render(), (
        f"{harness.DOC_RELPATH} is stale. Regenerate it: "
        "python3 tools/capture_intermediate_negative_controls.py --write"
    )


def test_the_document_carries_no_invalid_control() -> None:
    """Belt and braces on the rendered text, in case a case is added without a test."""
    committed = (REPO_ROOT / harness.DOC_RELPATH).read_text(encoding="utf-8")
    assert "INVALID CONTROL" not in committed
    assert "**as expected** NO" not in committed
