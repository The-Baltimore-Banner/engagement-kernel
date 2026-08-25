"""The gate-configuration document, held to the code rather than to itself.

Same discipline as `test_engagement_docs.py`: the tests read the code and look for it
in the prose, never the reverse. A document that names a knob the engine does not have
is worse than no document, because an adopter acts on it.

The triage is the part that needs a structural guarantee. Its acceptance criterion was
that every hardcoded constant appears in exactly one of three named groups and that
"leaving the line implicit fails". A prose triage cannot hold that: adding a constant
to the code and forgetting the document is invisible. So the census lives in
`engagement.parameters`, the document carries its render verbatim, every `where` is
resolved against the module it names, and no constant may appear twice.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from engagement_kernel.engagement.config import (
    BAR_DECLARATION_HINT,
    SHIPPED_BAR_PROVENANCE,
    GateThresholds,
)
from engagement_kernel.engagement.gate_config import GATE_CONFIG_VERSION, GATE_FIELDS
from engagement_kernel.engagement.parameters import (
    ALL_GROUPS,
    DEFERRED,
    FIXED,
    PROMOTED,
    Parameter,
    render_triage_into,
    triage_markdown,
)

DOCS = Path(__file__).resolve().parents[1] / "docs"
GATES_DOC = DOCS / "gate-configuration.md"
ADOPTER_DOC = DOCS / "adopter-path.md"
README = DOCS.parent / "README.md"


@pytest.fixture(scope="module")
def gates_doc() -> str:
    """The document as written. For the verbatim-render assertions only."""
    return GATES_DOC.read_text()


@pytest.fixture(scope="module")
def gates_prose() -> str:
    """The document with runs of whitespace collapsed.

    Every phrase assertion below reads this rather than the raw text. A phrase that
    happens to straddle a line wrap is absent from the raw text and present in the
    document, so asserting on the raw text tests the wrapping and fails on a reflow
    that changed nothing.
    """
    return " ".join(GATES_DOC.read_text().split())


def _prose(path: Path) -> str:
    return " ".join(path.read_text().split())


def _all_parameters() -> tuple[Parameter, ...]:
    return PROMOTED + FIXED + DEFERRED


# --- the triage is exhaustive and disjoint -----------------------------------


def test_every_triaged_parameter_resolves_to_real_code() -> None:
    """An entry cannot outlive the constant it describes.

    Without this the census is prose in a different file: a renamed constant would
    leave a confident, wrong claim behind and nothing would fail.
    """
    for parameter in _all_parameters():
        module_path, _, attribute = parameter.where.partition(":")
        module = importlib.import_module(f"engagement_kernel.{module_path}")
        target = module
        parts = attribute.split(".")
        for index, part in enumerate(parts):
            # A dataclass field declared with a `default_factory` is not a class
            # attribute, so `hasattr` alone would report every mapping-valued and
            # dataclass-valued gate as missing. A field resolves only as the last
            # part: under postponed annotations its declared type is a *string*, so
            # continuing through one would start matching attributes of `str` and
            # pass for anything.
            fields = getattr(target, "__dataclass_fields__", {})
            if part in fields:
                assert index == len(parts) - 1, (
                    f"{parameter.where} names something inside the dataclass field "
                    f"{part!r}, which this check cannot resolve"
                )
                break
            assert hasattr(target, part), f"{parameter.where} does not exist"
            target = getattr(target, part)


def test_no_parameter_is_triaged_twice() -> None:
    seen = [parameter.where for parameter in _all_parameters()]
    assert len(seen) == len(set(seen)), "a parameter appears in more than one group"


def test_every_gate_threshold_is_in_the_promoted_group() -> None:
    """The headline commitment: every gate is settable without writing Python."""
    promoted = {parameter.where for parameter in PROMOTED}
    for name in (*GATE_FIELDS, "cross_algorithm_ari_by_k"):
        where = f"engagement.config:GateThresholds.{name}"
        assert where in promoted, f"{name} is a gate threshold that nothing promoted"


def test_the_constants_the_acceptance_criterion_names_are_each_placed() -> None:
    """Named one by one, because a group that quietly omitted one would still pass above."""
    placed = {parameter.where for parameter in _all_parameters()}
    for where in (
        "engagement.selection:WILSON_Z",
        "engagement.windows:TRAILING_WINDOW_DAYS",
        "engagement.windows:WEEK_BIN_COUNT",
        "engagement.blocks:DENSE_MIN_EXPLAINED_VARIANCE",
        "engagement.calibration:PCT_CLIP_LO",
        "engagement.matrix:VARIANCE_BOUNDS",
        "engagement.panel:PANEL_RULE",
        "engagement.config:EMAIL_CLICK_UNIT",
        "engagement.guards:FORBIDDEN_INPUT_PATTERNS",
    ):
        assert where in placed, f"{where} is in no triage group"


def test_the_block_quality_thresholds_are_all_named_in_their_entry() -> None:
    """Five constants share one row, so the row has to name the other four."""
    entry = next(p for p in DEFERRED if p.where.endswith("DENSE_MIN_EXPLAINED_VARIANCE"))
    blocks = importlib.import_module("engagement_kernel.engagement.blocks")
    for name in (
        "SPARSE_MIN_EXPLAINED_VARIANCE",
        "DENSE_MIN_ANCHOR_CORR",
        "SPARSE_MIN_ANCHOR_CORR",
        "REDUNDANCY_CORR_THRESHOLD",
    ):
        assert hasattr(blocks, name), f"blocks.{name} no longer exists"
        assert name in entry.note, f"blocks.{name} is not named in the deferred entry"


def test_the_winsorisation_pair_is_named_in_its_entry() -> None:
    entry = next(p for p in DEFERRED if p.where.endswith("PCT_CLIP_LO"))
    calibration = importlib.import_module("engagement_kernel.engagement.calibration")
    assert hasattr(calibration, "PCT_CLIP_HI")
    assert "PCT_CLIP_HI" in entry.note


def test_every_deferred_entry_gives_a_reason() -> None:
    """ "Deferred with a reason" is the criterion. An empty note is a silent cap."""
    for parameter in DEFERRED:
        assert len(parameter.note) > 80, f"{parameter.where} is deferred without a reason"


def test_every_fixed_entry_says_why_it_is_fixed() -> None:
    for parameter in FIXED:
        assert len(parameter.note) > 80, f"{parameter.where} is fixed without a reason"


# --- the document carries the census ----------------------------------------


def test_the_document_carries_the_rendered_triage_verbatim(gates_doc: str) -> None:
    assert triage_markdown() in gates_doc, (
        "the triage block is stale. Re-render it with "
        "engagement_kernel.engagement.parameters.render_triage_into"
    )


def test_re_rendering_the_triage_is_a_no_op(gates_doc: str) -> None:
    assert render_triage_into(gates_doc) == gates_doc


def test_the_document_names_all_three_groups(gates_prose: str) -> None:
    for title, _ in ALL_GROUPS:
        assert f"### {title}" in gates_prose


# --- the document is accurate about the mechanism ---------------------------


def test_the_document_shows_the_version_the_reader_accepts(gates_prose: str) -> None:
    """A worked example with the wrong version would be refused if anyone ran it."""
    assert f"version = {GATE_CONFIG_VERSION}" in gates_prose


def test_the_document_records_where_the_shipped_bars_came_from(gates_prose: str) -> None:
    """An adopter must not be able to read them as a derived universal."""
    assert "4,571 rows" in gates_prose
    assert "nine-feature" in gates_prose
    assert "SHIPPED_BAR_PROVENANCE" in gates_prose
    assert "not a recommendation" in gates_prose
    # And the code's own label agrees with the document's account of it.
    assert "One newsroom's measurement" in SHIPPED_BAR_PROVENANCE


def test_the_document_states_the_measurement_that_makes_the_bar_necessary(
    gates_prose: str,
) -> None:
    """Without the chance-agreement number the derivation reads as ceremony."""
    assert "0.26 to 0.31" in gates_prose
    assert "chance level *falls* as k rises" in gates_prose


def test_the_document_says_the_bar_does_not_transport(gates_prose: str) -> None:
    assert "carried across feature spaces" in gates_prose
    assert "derive again" in gates_prose


def test_the_document_says_two_clusters_is_allowed(gates_prose: str) -> None:
    """The hard blocker this work removed, stated where an adopter will look."""
    assert "Two clusters is a legitimate answer" in gates_prose
    # And the code agrees: a declared bar at k=2 is screenable.
    assert GateThresholds().with_bars({2: 0.5}).cross_algorithm_bar(2) == 0.5


def test_the_document_names_the_derivation_tool_and_it_exists(gates_prose: str) -> None:
    tool = DOCS.parent / "tools" / "derive_cross_algorithm_bars.py"
    assert tool.exists()
    assert "tools/derive_cross_algorithm_bars.py" in gates_prose
    assert "tools/derive_cross_algorithm_bars.py" in BAR_DECLARATION_HINT


def test_the_document_names_the_template_command(gates_prose: str) -> None:
    from engagement_kernel.engagement import cli

    assert "gates-template" in gates_prose
    parser = cli.build_parser()
    import argparse

    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert "gates-template" in action.choices, "the document names a command that does not exist"


def test_the_document_says_no_default_moved(gates_prose: str) -> None:
    assert "No default value moved" in gates_prose


# --- it is reachable --------------------------------------------------------


def test_the_adopter_path_tells_the_adopter_the_gates_are_theirs() -> None:
    """It previously never mentioned setting a threshold or choosing a cluster count."""
    path = _prose(ADOPTER_DOC)
    assert "gate-configuration.md" in path, "the document is unreachable from the path"
    assert "yours to set" in path
    assert "gates-template" in path
    assert "two clusters is a legitimate answer" in path.lower()


def test_the_adopter_path_separates_cost_from_threshold() -> None:
    path = _prose(ADOPTER_DOC)
    assert "cheaper verdict on the same screens" in path
    assert "what your deployment considers good enough" in path


def test_the_readme_links_it() -> None:
    assert "docs/gate-configuration.md" in README.read_text()
