"""The methodology document, held to the code.

Same discipline as the other document tests here: **read the code and look for it
in the prose, never the reverse.** A methodology document is the one most likely to
drift, because it explains reasoning rather than describing an interface, and
nothing about explaining reasoning fails when the interface moves underneath it.

Four failures are guarded, and every one of them already happened to the document
this replaces.

**A retyped threshold.** The earlier version typed every gate level as a literal in
a table. One of them -- the flat cross-algorithm bar -- was retired and replaced by
a per-k derivation, and the document went on stating the retired number for seven
weeks. So no level may be typed in the prose at all: the table is rendered from
``GateThresholds``, and the tests below assert every current level is absent
outside that block.

**A prescription presented as the method.** The earlier version prescribed a
candidate cluster range. A range is one newsroom's declaration.

**A superseded result presented as current.** The earlier version described a
six-cluster surface that had already been replaced by one whose champion is
derived rather than hand-set.

**An internal identifier.** The earlier version named the upstream product six
times and listed private-tree paths. It also carried a ticket key in an
underscored form that the leak scanner did not match, so the gate reported clean
with the identifier present. That rule is widened now, and this file checks the
document independently anyway -- a gate is not a substitute for an assertion about
the specific document it was supposed to protect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from engagement_kernel.contract import enums
from engagement_kernel.engagement import measures, selection, windows
from engagement_kernel.engagement.config import EMAIL_CLICK_UNIT, GateThresholds, LaneConfig
from engagement_kernel.engagement.parameters import GATES, gate_table_markdown, render_gates_into

DOCS = Path(__file__).resolve().parents[1] / "docs"
METHOD_DOC = DOCS / "clustering-methodology.md"
README = DOCS.parent / "README.md"

#: The documents the methodology defers to rather than restating. Named here so a
#: renamed or deleted one breaks this test rather than leaving a dead link.
DEFERS_TO = (
    "adopter-path.md",
    "engagement-lane.md",
    "canonical-input-contract.md",
    "declarations-questionnaire.md",
    "gate-configuration.md",
)

#: A decimal literal as a whole token. Substring matching is wrong here and was
#: measured to be wrong: `0.7` occurs inside the measured span `0.30 to 0.71`, and
#: `0.9` inside the measured `0.97`. A check that fires on a correct sentence is a
#: check somebody eventually deletes.
_DECIMAL = r"(?<![\d.])%s(?![\d])"


@pytest.fixture(scope="module")
def raw() -> str:
    """The document as written. For the verbatim-render assertions only."""
    return METHOD_DOC.read_text()


@pytest.fixture(scope="module")
def prose() -> str:
    """The document with runs of whitespace collapsed.

    Every phrase assertion reads this. A phrase straddling a line wrap is absent
    from the raw text and present in the document, so asserting on the raw text
    tests the wrapping and fails on a reflow that changed nothing.
    """
    return " ".join(METHOD_DOC.read_text().split())


def _outside_the_rendered_block(raw_text: str) -> str:
    """The prose with the rendered gate table removed.

    Levels are allowed to appear in the render -- that is the render's job.
    Everywhere else they are a second source of truth.
    """
    return " ".join(raw_text.replace(gate_table_markdown(), " ").split())


def _mentions(text: str, value: float) -> bool:
    return re.search(_DECIMAL % re.escape(f"{value:g}"), text) is not None


def _section(prose_text: str, number: int) -> str:
    """One numbered top-level section of the normalised prose.

    The heading marker carries a trailing space on purpose. Splitting on ``"## 9."``
    alone also matches ``"### 9.1"``, so the extracted section was everything before
    the first subsection -- which quietly made three assertions here vacuous by
    testing a couple of paragraphs instead of the section they name.
    """
    start = f"## {number}. "
    end = f"## {number + 1}. "
    assert start in prose_text, f"the document has no section {number}"
    body = prose_text.split(start, 1)[1]
    return body.split(end, 1)[0] if end in body else body


# --- it exists and is reachable ----------------------------------------------


def test_the_document_exists() -> None:
    assert METHOD_DOC.exists()


def test_the_readme_links_it() -> None:
    assert "docs/clustering-methodology.md" in README.read_text()


def test_the_adopter_path_links_it() -> None:
    """A reader asking "why is the method built this way" starts on the path."""
    assert "clustering-methodology.md" in (DOCS / "adopter-path.md").read_text()


def test_it_defers_to_each_document_rather_than_restating_it(prose: str) -> None:
    for name in DEFERS_TO:
        assert (DOCS / name).exists(), f"{name} does not exist"
        assert name in prose, f"the methodology does not defer to {name}"


# --- no threshold is typed in the prose --------------------------------------


def test_the_document_carries_the_rendered_gate_table_verbatim(raw: str) -> None:
    assert gate_table_markdown() in raw, (
        "the gate table is stale. Re-render it with "
        "engagement_kernel.engagement.parameters.render_gates_into"
    )


def test_re_rendering_the_gate_table_is_a_no_op(raw: str) -> None:
    assert render_gates_into(raw) == raw


def test_every_gate_in_the_table_is_a_real_field() -> None:
    fields = GateThresholds.__dataclass_fields__
    for gate in GATES:
        assert gate.field in fields, f"{gate.field} is not a GateThresholds field"


def test_no_scalar_gate_level_is_typed_in_the_prose(raw: str) -> None:
    """The failure this closes: a retired threshold restated for seven weeks."""
    outside = _outside_the_rendered_block(raw)
    gates = GateThresholds()
    for gate in GATES:
        if gate.field == "cross_algorithm_ari_by_k":
            continue
        value = getattr(gates, gate.field)
        assert not _mentions(outside, value), (
            f"{gate.field}'s level is typed in the prose. Levels belong in the "
            "rendered table, or the document becomes a second source of truth"
        )


def test_the_bar_table_is_not_retyped_in_the_prose(raw: str) -> None:
    """Counted rather than forbidden outright, and the reason is not a fudge.

    The bars sit just above chance-level agreement, and the document states that
    chance level as a measured range -- so one bar value coinciding with a number
    in a sentence about chance agreement is the same phenomenon showing up twice,
    not a retyped threshold. Retyping the *table* brings most of the eight along
    with it, which is what this counts.
    """
    outside = _outside_the_rendered_block(raw)
    bars = GateThresholds().cross_algorithm_ari_by_k
    typed = [k for k, bar in bars.items() if _mentions(outside, bar)]
    assert len(typed) < 3, f"the bar table looks retyped in the prose: k={typed}"


def test_the_retired_flat_bar_is_not_restated(raw: str) -> None:
    """It is not a threshold any more, so it is not a number this document carries."""
    assert not re.search(_DECIMAL % re.escape("0.55"), _outside_the_rendered_block(raw))


def test_the_document_says_whose_the_levels_are(prose: str) -> None:
    assert "rendered from" in prose
    assert "one deployment's" in prose


# --- the week anchor ---------------------------------------------------------


def test_the_week_anchor_is_a_required_declaration_with_its_reason(prose: str) -> None:
    """ "Use one weekday consistently" was the earlier advice, and it is insufficient."""
    assert "two week-anchor conventions" in prose
    assert "six days" in prose
    assert "no shared helper" in prose
    assert "no test covered the difference" in prose
    assert "which end of the week it sits on" in prose
    assert "insufficient" in prose


def test_the_code_really_has_no_week_anchor_default() -> None:
    """The document's claim about the absent default, checked against the module."""
    assert not hasattr(windows, "DEFAULT_WEEK_END_DAY")


# --- the click unit ----------------------------------------------------------


def test_the_click_unit_is_declared_with_its_invariance(prose: str) -> None:
    assert EMAIL_CLICK_UNIT == "click_event"
    assert "EMAIL_CLICK_UNIT" in prose
    assert "a click event, not a distinct campaign" in prose
    assert "folded into the model version" in prose
    # The correction that matters: the decision does not reach the clusters through
    # cadence, which is where people look for it.
    assert "cadence axis is invariant" in prose
    assert "log-transformed" in prose


# --- the perturbation screen is in the method --------------------------------


def test_the_perturbation_screen_is_in_the_method_not_an_appendix(prose: str) -> None:
    section = _section(prose, 9)
    assert "perturbed panels" in section
    assert "one-sided 95%" in section
    assert "Wilson" in section


def test_the_measured_evidence_for_it_is_cited(prose: str) -> None:
    """Without the measurement the screen reads as ceremony rather than a finding."""
    assert "0.97" in prose
    assert "0.49 and 0.66" in prose
    assert "20 of 20" in prose
    assert "two rows" in prose


def test_the_code_implements_what_the_document_describes() -> None:
    assert selection.WILSON_Z == pytest.approx(1.645)
    gates = GateThresholds()
    for field in (
        "selection_survival_floor",
        "selection_perturbation_draws",
        "selection_perturbation_row_fraction",
    ):
        assert hasattr(gates, field)
    assert hasattr(selection, "selection_stability")


def test_the_checklist_carries_the_perturbation_step(prose: str) -> None:
    """The earlier version's checklist inherited the gap in its own method."""
    checklist = _section(prose, 14)
    assert "perturbed panels" in checklist
    assert "survival bound" in checklist
    assert "Derive the cross-algorithm bar on your own panel" in checklist


# --- the cross-algorithm bar is derived, not inherited -----------------------


def test_the_bar_is_taught_as_derived_on_the_adopters_own_panel(prose: str) -> None:
    assert "cannot honestly be carried from anybody else" in prose
    assert "derive again" in prose


def test_both_reasons_a_flat_bar_is_wrong_are_given(prose: str) -> None:
    """Wrong shape and wrong level are different arguments and both are needed."""
    assert "the wrong shape" in prose
    assert "the wrong level for anybody else" in prose
    assert "0.26 to 0.31" in prose
    assert "falls" in prose
    # The level argument specifically: it depends on the panel, not on k.
    for phrase in ("row count", "dimensionality", "correlation structure"):
        assert phrase in prose, f"the level argument does not name {phrase}"


def test_the_document_points_at_the_derivation_tool_and_it_exists(prose: str) -> None:
    assert (DOCS.parent / "tools" / "derive_cross_algorithm_bars.py").exists()
    assert "tools/derive_cross_algorithm_bars.py" in prose


def test_the_two_controls_are_stated_and_the_holdout_is_named(prose: str) -> None:
    assert "positive" in prose
    assert "held-out" in prose
    assert "circular" in prose


def test_the_document_says_the_derivation_cannot_be_tuned_to_pass(prose: str) -> None:
    assert "never observes the real" in prose


def test_our_own_values_are_labelled_with_the_panel_they_came_from(prose: str) -> None:
    """Illustrative, and carrying the population they were measured on."""
    assert "six-feature" in prose
    assert "nine-feature" in prose
    assert "carried across feature spaces" in prose


# --- no prescribed candidate grid -------------------------------------------


def test_the_document_prescribes_no_candidate_grid(prose: str) -> None:
    assert "prescribes no candidate grid" in prose
    assert "declaration per deployment" in prose
    # The earlier version's prescription, in the form it took.
    assert "k = 4 through 10" not in prose


def test_two_clusters_is_taught_as_a_legitimate_answer(prose: str) -> None:
    assert "Two clusters is a legitimate result" in prose
    # And the code agrees, given a declared bar.
    assert GateThresholds().with_bars({2: 0.5}).cross_algorithm_bar(2) == 0.5


def test_both_ends_of_the_range_are_taught_as_judgements(prose: str) -> None:
    assert "The floor" in prose
    assert "The ceiling" in prose


def test_our_own_sweep_appears_only_as_an_example(prose: str) -> None:
    assert "LaneConfig.k_grid" in prose
    assert "one newsroom's range" in prose
    assert LaneConfig.__dataclass_fields__["k_grid"].default == (3, 4, 5, 6, 7, 8)
    for forbidden in ("k=3..8", "k=3..10", "3 through 8", "k = 3 through 8"):
        assert forbidden not in prose, f"the document prescribes {forbidden}"


# --- instrumentation floors --------------------------------------------------


def test_the_two_kinds_of_floor_have_different_remedies(prose: str) -> None:
    assert "under-capture ramp" in prose
    assert "structural absence" in prose
    assert "signal-specific calibration floor" in prose
    assert "consumer-side feature handling" in prose


def test_the_document_says_one_window_cannot_satisfy_both(prose: str) -> None:
    """The honest part, and the reason the section exists at all."""
    assert "six months apart" in prose
    assert "no single joint fit window" in prose


def test_the_document_uses_the_contracts_own_availability_vocabulary(prose: str) -> None:
    """So the remedy is reachable rather than merely described."""
    for value in (enums.AVAILABILITY_NOT_YET_LAUNCHED, enums.AVAILABILITY_NOT_DEPLOYED):
        assert value in prose, f"the document does not name {value}"


# --- three engagement scores -------------------------------------------------


def test_the_document_stops_recommending_one_score(prose: str) -> None:
    assert "Publish all three" in prose
    assert "agree at the extremes and disagree in the middle" in prose
    assert "sub-scores" in prose
    # It says so explicitly about its own earlier advice.
    assert "recommended the third" in prose


def test_the_code_publishes_the_blocks_the_document_describes() -> None:
    for block in (
        measures.BLOCK_INTENSITY,
        measures.BLOCK_BREADTH,
        measures.BLOCK_COMMUNITY,
        measures.BLOCK_LOYALTY,
    ):
        assert isinstance(block, str)


# --- the case appendix is scoped ---------------------------------------------


def test_the_case_appendix_states_the_published_surface(prose: str) -> None:
    case = _section(prose, 16)
    assert "five clusters, derived" in case
    assert "needs no hand-set cluster count" in case
    assert "no longer load-bearing" in case


def test_the_superseded_readout_is_labelled_as_superseded(prose: str) -> None:
    case = _section(prose, 16)
    assert "superseded" in case
    assert "six clusters" in case
    assert "flip on a two-row change" in case


def test_the_donor_conclusion_is_scoped_and_not_restated_flatly(prose: str) -> None:
    """The robust claim and the version-specific ranking, visibly separated."""
    case = _section(prose, 16)
    assert "The robust claim" in case
    assert "more engaged readers are more likely to donate" in case
    assert "computed on the segments of the earlier" in case
    assert "not on the published surface" in case
    assert "has not been recomputed" in case


# --- no internal identifiers -------------------------------------------------


def test_the_document_names_no_internal_product(raw: str) -> None:
    """The earlier version named it six times, including in a section heading."""
    lowered = raw.lower()
    for phrase in ("news detector", "news_detector"):
        assert phrase not in lowered, f"the document names {phrase!r}"


def test_the_document_carries_no_ticket_key_in_any_form(raw: str) -> None:
    """Including the underscored form the scanner did not used to match.

    Checked here as well as by the gate, because the source document passed a clean
    scan while carrying one: the rule required a hyphen and the identifier used an
    underscore. The rule is widened now, and this stays because a gate is not a
    substitute for an assertion about the specific document it protects.
    """
    prefix = "BBA" + "1"
    assert not re.search(prefix + r"[-_]?[0-9]+", raw, re.IGNORECASE)


def test_the_document_carries_no_private_tree_paths(raw: str) -> None:
    for fragment in ("src/engagement_engine", "notebooks/", "configs/", ".ipynb"):
        assert fragment not in raw, f"the document carries the private path {fragment!r}"


# --- the catch-all -----------------------------------------------------------


def test_the_document_tells_the_reader_which_numbers_are_theirs(prose: str) -> None:
    """A reader who has never seen our data must finish knowing what to set.

    Approximated by three things the document has to do: say the levels shown are
    somebody else's, point at where they are set, and do it in the section where a
    reader meets a threshold rather than only in a preamble.
    """
    assert "yours to set" in prose
    assert prose.count("gate-configuration.md") >= 3, (
        "the document points at where the numbers are set fewer than three times, so "
        "a reader arriving mid-document may never find it"
    )
    assert "gate-configuration.md" in _section(prose, 9)
