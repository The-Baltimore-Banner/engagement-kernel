"""Negative controls for the two feature guards -- and proof they discriminate.

A guard that passes on a clean tree proves nothing. So this module seeds four
forbidden columns and requires each to be refused, which is the ordinary form of the
control.

That is not enough here, and the reason is specific. The guards were ported from a
system with a different vocabulary. The *pattern* list survives translation
untouched -- ``*scroll*`` means the same thing in any vocabulary -- but the *name*
list does not: a literal copy of the original list names columns this contract cannot
produce, so it would load, run on every build, and match nothing forever. A control
that seeds only scroll, sends and opens exercises the patterns and **would pass
against exactly that dead name list.** It proves half the guard.

So the controls here are in two tiers:

1. all four columns are refused (:func:`test_every_seeded_forbidden_column_is_refused`);
2. the subscription-state column is refused **by the name rule**, and the pattern
   list alone provably does not catch it -- so the counterfeit guard with an empty
   name list is shown passing three of the four
   (:func:`test_the_pattern_list_alone_would_pass_the_state_column`).

The second tier is the one that means the name list was translated rather than
copied.
"""

from __future__ import annotations

import fnmatch

import pandas as pd
import pytest

from engagement_kernel.engagement import guards
from engagement_kernel.engagement.matrix import build_weighted_matrix

#: The four seeded columns, and which rule must refuse each.
#:
#: The last one is the discriminating control. The first three are caught by
#: vocabulary-independent patterns and would be caught by an untranslated guard too.
SEEDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("web_scroll_depth_28d", "pattern", "a scroll measure, declared out of scope"),
    ("email_sends_28d", "pattern", "an email send: the publisher's behaviour, not the reader's"),
    ("email_opens_28d", "pattern", "an email open: reachability only"),
    ("state", "name", "subscription state: the discriminating control"),
)


@pytest.mark.parametrize(("column", "rule", "why"), SEEDED_COLUMNS)
def test_every_seeded_forbidden_column_is_refused(column: str, rule: str, why: str) -> None:
    """Tier one: each seeded column is refused, by the rule it should be."""
    findings = guards.inspect_model_columns([column])
    assert findings, f"{column} ({why}) was permitted as a model feature"
    assert findings[0].rule == rule, (
        f"{column} was refused by the {findings[0].rule} rule, not the {rule} rule. "
        "Which rule fires is the whole point of these controls"
    )


@pytest.mark.parametrize(("column", "rule", "why"), SEEDED_COLUMNS)
def test_the_error_message_names_the_offending_column(column: str, rule: str, why: str) -> None:
    """A guard that refuses without naming the column sends whoever hit it grepping."""
    with pytest.raises(guards.ForbiddenModelColumn) as exc:
        guards.assert_no_forbidden_model_columns(["web_intensity", column, "topic_breadth"])
    assert column in str(exc.value)
    assert "web_intensity" not in str(exc.value), "the message named a permitted column too"


def test_the_pattern_list_alone_would_pass_the_state_column() -> None:
    """Tier two: the discrimination proof.

    This is the counterfeit guard -- the patterns with a dead name list, which is
    what a literal copy of the original list amounts to against this contract. It
    catches three of the four seeded columns, and misses the one that matters.

    If this test ever fails because ``state`` started matching a pattern, the
    discrimination has been lost: the controls above would then pass against an
    untranslated name list, and this file would no longer be evidence of anything.
    """

    def counterfeit(column: str) -> bool:
        return any(fnmatch.fnmatch(column, pattern) for pattern in guards.FORBIDDEN_MODEL_PATTERNS)

    caught = [column for column, _, _ in SEEDED_COLUMNS if counterfeit(column)]
    missed = [column for column, _, _ in SEEDED_COLUMNS if not counterfeit(column)]

    assert missed == ["state"], (
        "the patterns alone now catch every seeded column, so seeding them no longer "
        f"discriminates a translated name list from a copied one. Missed: {missed}"
    )
    assert len(caught) == 3

    # And the real guard does catch it.
    findings = guards.inspect_model_columns(["state"])
    assert findings and findings[0].rule == "name"


def test_the_name_list_is_derived_from_the_contract_not_written_out() -> None:
    """The name list tracks the contract, so a new field is refused when it lands.

    Asserted rather than trusted: the alternative is a hand-written list that is
    correct on the day it is written and silently incomplete afterwards.
    """
    names = guards.forbidden_model_names()
    for field in ("state", "payer_type", "reader_id", "session_id", "list_id", "campaign_id"):
        assert field in names, f"contract field {field!r} is not refused at the matrix"
    findings = guards.inspect_model_columns(["payer_type"])
    assert findings[0].detail == guards.NAME_SOURCE_CONTRACT


def test_the_guard_permits_the_real_model_columns() -> None:
    """A guard that refused everything would pass every test above.

    So: the columns the lane actually builds must be permitted. Without this the
    guard could be tightened into uselessness and nothing would notice until a run
    refused its own matrix.
    """
    permitted = [
        "web_intensity",
        "app_habit",
        "web_consistency",
        "community_contribution_depth",
        "email_click_recency",
        "channel_mix_web",
        "overall_active_days",
        "overall_momentum",
        "topic_share_news",
        "topic_share_other",
        "topic_breadth",
        "z_log__web_views_28d",
        "z__topic_entropy_28d",
        "email_cadence__active_weeks_4",
    ]
    findings = guards.inspect_model_columns(permitted)
    assert findings == [], f"the guard refused columns the lane builds: {findings}"


def test_the_raw_entropy_atomic_is_refused_but_its_surface_dimension_is_not() -> None:
    """The one deliberate asymmetry, asserted so it stays deliberate.

    ``topic_entropy_28d`` is refused as a block feature -- it is a function of bucket
    shares that are already in that block, so it double-counts. The standardised
    surface dimension is permitted, because the surfaces carry no bucket shares and
    it is the only breadth-of-taste dimension there.
    """
    assert guards.inspect_model_columns(["topic_entropy_28d"])
    assert guards.inspect_model_columns(["z__topic_entropy_28d"]) == []


def test_the_input_guard_refuses_a_widened_daily_frame() -> None:
    """The first layer: a forbidden signal must not reach the atomics at all."""
    with pytest.raises(guards.ForbiddenInput) as exc:
        guards.assert_no_forbidden_inputs(
            ["reader_id", "local_date", "views", "scroll_pct_75"], where="reader_channel_day"
        )
    assert "scroll_pct_75" in str(exc.value)
    assert "reader_channel_day" in str(exc.value), "the message must name the frame"


def test_the_input_guard_permits_the_projected_daily_columns() -> None:
    """The projections the atomic layer actually uses must survive their own guard."""
    from engagement_kernel.engagement.atomics import (
        COMMUNITY_INPUT_COLUMNS,
        CONSUMPTION_INPUT_COLUMNS,
        EMAIL_INPUT_COLUMNS,
    )

    for columns, where in (
        (CONSUMPTION_INPUT_COLUMNS, "reader_channel_day"),
        (EMAIL_INPUT_COLUMNS, "reader_email_day"),
        (COMMUNITY_INPUT_COLUMNS, "reader_community_day"),
    ):
        guards.assert_no_forbidden_inputs(columns, where=where)


def test_the_email_projection_leaves_opens_behind() -> None:
    """The projection is the mechanism; the guard is the proof it was complete.

    The daily email table carries ``opens`` on purpose, for reachability reporting.
    If the projection ever admitted it, the guard would refuse it -- so this asserts
    the projection, and the guard test above asserts the backstop.
    """
    from engagement_kernel.engagement.atomics import EMAIL_INPUT_COLUMNS

    assert "opens" not in EMAIL_INPUT_COLUMNS
    assert guards.inspect_model_columns(["opens"]), "opens must also be refused at the matrix"


def test_the_matrix_builder_refuses_a_seeded_state_column() -> None:
    """The guard at the layer it actually runs at, not just as a function call."""
    frame = pd.DataFrame(
        {
            "web_intensity": [0.4, -0.3, 1.1, -1.2],
            "topic_breadth": [0.2, 0.1, -0.9, 0.6],
            "state": ["active", "active", "trial", "grace"],
        }
    )
    membership = {"consumption": ["web_intensity"], "topic": ["topic_breadth", "state"]}
    with pytest.raises(guards.ForbiddenModelColumn) as exc:
        build_weighted_matrix(frame, membership, {"consumption": 0.5, "topic": 0.5})
    assert "state" in str(exc.value)
    assert any(finding.rule == "name" for finding in exc.value.findings)


def test_the_lane_surface_columns_pass_the_guard(lane_result) -> None:
    """And the guard runs on the surface that is actually frozen.

    The system this ports from ran its model guard inside the block-weighted builder,
    while the surface it actually froze and published was assembled by a different
    function that never called it. This asserts the surface that got frozen here went
    through the guard.
    """
    assert lane_result.bundle is not None
    findings = guards.inspect_model_columns(lane_result.bundle.main.feature_columns)
    assert findings == [], f"the frozen surface carries refused columns: {findings}"
