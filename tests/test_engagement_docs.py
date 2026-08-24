"""The lane's documentation must carry the census, and stay in step with the code.

Two different failures are guarded here.

**A document that drifts from the code.** The not-porting census is the answer to
"why is there no scroll column?", and the question gets answered by somebody adding
one if the answer is not where they look. So the census lives in code
(:data:`engagement_kernel.intermediate.tables.NOT_BUILT` and
:data:`engagement_kernel.engagement.outputs.NOT_PORTED_COLUMNS`) and this asserts the
port document carries every entry.

**A document that names a rule the code does not have.** A generated document naming
its own staleness test is not proof the test exists, and a hand-written one naming a
constant is not proof the constant does. So the tests here read the code and look for
it in the prose, never the reverse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engagement_kernel.engagement.config import EMAIL_CLICK_UNIT
from engagement_kernel.engagement.outputs import (
    NOT_PORTED_COLUMNS,
    OUTPUTS,
    PORT_REMOVED_DEFAULTS,
    census_frame,
    census_markdown,
    render_census_into,
)
from engagement_kernel.intermediate.tables import NOT_BUILT

DOCS = Path(__file__).resolve().parents[1] / "docs"
PORT_DOC = DOCS / "engagement-lane.md"
PARITY_DOC = DOCS / "engagement-lane-parity.md"
CONTROLS_DOC = DOCS / "engagement-lane-negative-controls.md"


@pytest.fixture(scope="module")
def port_doc() -> str:
    return PORT_DOC.read_text()


def test_the_documents_exist() -> None:
    for path in (PORT_DOC, PARITY_DOC, CONTROLS_DOC):
        assert path.exists(), f"{path.name} is missing"


def test_the_document_carries_the_rendered_census_verbatim(port_doc: str) -> None:
    """The whole census, rendered from the declarations, present byte for byte.

    One assertion rather than a loop over entries, because the render is the
    authority: if this fails, the fix is to re-render rather than to retype a cell.
    """
    assert census_markdown() in port_doc, (
        "the port document's census block is stale. Re-render it with "
        "engagement_kernel.engagement.outputs.render_census_into"
    )


def test_re_rendering_the_census_is_a_no_op(port_doc: str) -> None:
    """The same assertion from the other side: it names the fix and proves it works."""
    assert render_census_into(port_doc) == port_doc


def test_every_not_built_table_and_dropped_column_reaches_the_document(port_doc: str) -> None:
    """And the render actually covers every declaration, not a subset of them.

    Without this, a renderer that silently skipped entries would keep both
    assertions above green while the document lost half the census.
    """
    for entry in NOT_BUILT:
        assert entry.name in port_doc, f"{entry.name} is not in the port document"
        assert entry.reason in port_doc, f"the reason for not building {entry.name} is missing"
    for dropped in NOT_PORTED_COLUMNS:
        assert dropped.columns in port_doc, f"dropped columns not documented: {dropped.columns}"
        assert dropped.reason in port_doc, f"the reason for dropping {dropped.columns} is missing"


def test_the_census_frame_covers_both_levels() -> None:
    frame = census_frame()
    assert set(frame["level"]) == {"table", "column"}
    assert len(frame) == len(NOT_BUILT) + len(NOT_PORTED_COLUMNS)


def test_the_removed_vendor_default_is_recorded(port_doc: str) -> None:
    """The hardcoded list id did not travel, and the port document says so.

    A reader comparing the two systems column by column will look for it, so an
    unexplained absence reads as an omission.
    """
    assert PORT_REMOVED_DEFAULTS
    assert "mailing-list identifier" in port_doc
    assert "no default" in port_doc


def test_the_click_unit_decision_is_in_the_contract_facing_document(port_doc: str) -> None:
    """The decision is named where an adopter reads about the lane, not only in code."""
    assert "click event" in port_doc.lower()
    assert "distinct campaigns clicked" in port_doc
    assert EMAIL_CLICK_UNIT == "click_event"
    # And the correction: the decision does not reach the clusters through cadence.
    assert "cadence axis is invariant" in port_doc


def test_the_port_document_names_which_week_convention_was_replaced(port_doc: str) -> None:
    """The acceptance criterion asks for this by name."""
    assert "week-anchor conventions" in port_doc
    assert "serving lane" in port_doc
    assert "the convention the port replaced" in port_doc
    assert "research lane" in port_doc


def test_the_parity_document_names_the_email_day_shift() -> None:
    """So the first parity run is not debugged as a regression."""
    parity = PARITY_DOC.read_text()
    assert "day shift" in parity
    assert "by construction" in parity
    assert "no timezone conversion" in parity
    for feature in ("email_click_days_7d", "email_click_active_weeks_4"):
        assert feature in parity, f"{feature} is not named as affected by the shift"


def test_the_parity_document_refuses_to_claim_numeric_parity() -> None:
    parity = PARITY_DOC.read_text()
    assert "not claimed numerically" in parity
    assert "structurally" in parity


def test_the_controls_document_records_the_discrimination_evidence() -> None:
    """A control with no evidence that it fails when broken is decoration."""
    controls = CONTROLS_DOC.read_text()
    assert "discriminates" in controls
    assert "6 failed, 11 passed" in controls, "the guard mutation result is not recorded"
    assert "5 failed, 8 passed" in controls, "the anchor mutation result is not recorded"


def test_every_output_table_is_documented(port_doc: str) -> None:
    for table in OUTPUTS:
        assert f"`{table.name}`" in port_doc, f"output table {table.name} is undocumented"


def test_the_readme_links_the_lane(port_doc: str) -> None:
    readme = (DOCS.parent / "README.md").read_text()
    assert "engagement-lane.md" in readme, "the port document is unreachable from the README"
