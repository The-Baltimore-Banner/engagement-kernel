"""The spine: one population resolution, from subscription-state history.

The claim being tested is that there is exactly one mechanism deciding who is
scored, and that it reads the manifest's declared entitled states. The controls that
matter are the ones distinguishing "not entitled" from "unknown", and the interval
arithmetic around an exclusive end date -- because the off-by-one there is one day of
entitlement per span and never looks wrong.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from engagement_kernel.contract.manifest import ScoredPopulation
from engagement_kernel.engagement.spine import (
    SPINE_COLUMNS,
    SpineError,
    assert_sources_fresh,
    build_spine,
    fit_cohort_mask,
)
from engagement_kernel.engagement.windows import TRAILING_WINDOW_DAYS

WEEK_END = date(2026, 6, 28)


def intervals(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=[
            "reader_id",
            "state",
            "payer_type",
            "start_ts",
            "end_ts",
            "start_date",
            "end_date",
        ],
    )


def span(
    reader_id: str,
    state: str,
    start: date,
    end: date | None = None,
    payer_type: str | None = "individual",
) -> dict:
    return {
        "reader_id": reader_id,
        "state": state,
        "payer_type": payer_type,
        "start_ts": datetime(start.year, start.month, start.day, tzinfo=UTC),
        "end_ts": None if end is None else datetime(end.year, end.month, end.day, tzinfo=UTC),
        "start_date": start,
        "end_date": end,
    }


def config_with(lane_config, **population):
    scored = dataclasses.replace(lane_config.manifest.scored_population, **population)
    manifest = dataclasses.replace(lane_config.manifest, scored_population=scored)
    return dataclasses.replace(lane_config, manifest=manifest)


def test_an_entitled_reader_with_a_full_window_is_fit_on(lane_config) -> None:
    frame = intervals([span("rdr-1", "active", date(2025, 1, 1))])
    result = build_spine(frame, WEEK_END, lane_config)
    assert list(result.frame.columns) == list(SPINE_COLUMNS)
    row = result.frame.iloc[0]
    assert row["entitled_days_in_window"] == TRAILING_WINDOW_DAYS
    assert not row["partial_window_flag"]
    assert row["projected_flag"] == 0
    assert bool(fit_cohort_mask(result.frame).iloc[0])


def test_a_reader_entitled_for_part_of_the_window_is_scored_but_projected(lane_config) -> None:
    """Window completeness is what replaced the publisher-specific subscriber taxonomy.

    A partial window has fewer days to accumulate activity in, so fitting on a
    mixture of window lengths would teach the model that recent subscribers are light
    readers. They are scored, and flagged.
    """
    frame = intervals([span("rdr-1", "active", date(2026, 6, 20))])
    result = build_spine(frame, WEEK_END, lane_config)
    row = result.frame.iloc[0]
    assert 0 < row["entitled_days_in_window"] < TRAILING_WINDOW_DAYS
    assert row["partial_window_flag"]
    assert row["projected_flag"] == 1
    assert not bool(fit_cohort_mask(result.frame).iloc[0])


def test_a_reader_in_an_undeclared_state_is_out_of_population(lane_config) -> None:
    """``cancelled`` is a state the cohort's manifest does not declare entitled."""
    frame = intervals([span("rdr-1", "cancelled", date(2025, 1, 1))])
    result = build_spine(frame, WEEK_END, lane_config)
    assert result.frame.empty
    assert result.out_of_population == 1


def test_a_reader_with_no_covering_span_is_absent_not_zero(lane_config) -> None:
    """State unknown and state known-and-unentitled are different facts.

    A reader whose only span ended before the week is not in the population with a
    zero -- they are not in it. Carrying them with zeros would put a row in every
    published table for somebody who is not a subscriber.
    """
    frame = intervals([span("rdr-1", "active", date(2025, 1, 1), date(2026, 1, 1))])
    result = build_spine(frame, WEEK_END, lane_config)
    assert result.frame.empty
    assert result.out_of_population == 1


def test_the_end_date_is_exclusive(lane_config) -> None:
    """The last covered day is the day before ``end_date``.

    Treating an exclusive bound as inclusive credits one extra day of entitlement per
    span, which is exactly enough to flip ``partial_window_flag`` for a reader whose
    span ends on the last day of the window.
    """
    # A span ending the day after the week end covers the week end itself.
    covering = intervals(
        [span("rdr-1", "active", date(2025, 1, 1), WEEK_END + pd.Timedelta(days=1))]
    )
    assert not build_spine(covering, WEEK_END, lane_config).frame.empty

    # A span ending *on* the week end does not cover it.
    ending = intervals([span("rdr-1", "active", date(2025, 1, 1), WEEK_END)])
    assert build_spine(ending, WEEK_END, lane_config).frame.empty


def test_overlapping_entitled_spans_are_counted_once(lane_config) -> None:
    """A reader who cancelled and resubscribed inside one window.

    Summing the two spans' lengths would credit them with more days than the window
    has, which then reports a partially-entitled reader as fully entitled.
    """
    frame = intervals(
        [
            span("rdr-1", "active", date(2026, 6, 1), date(2026, 6, 20)),
            span("rdr-1", "active", date(2026, 6, 15)),
        ]
    )
    result = build_spine(frame, WEEK_END, lane_config)
    assert result.frame.iloc[0]["entitled_days_in_window"] <= TRAILING_WINDOW_DAYS


def test_days_outside_an_entitled_state_do_not_count(lane_config) -> None:
    """Only spans whose state is declared entitled contribute window days."""
    frame = intervals(
        [
            span("rdr-1", "cancelled", date(2026, 5, 1), date(2026, 6, 20)),
            span("rdr-1", "active", date(2026, 6, 20)),
        ]
    )
    result = build_spine(frame, WEEK_END, lane_config)
    row = result.frame.iloc[0]
    assert row["state"] == "active"
    assert row["entitled_days_in_window"] < TRAILING_WINDOW_DAYS


def test_the_declared_population_decides_who_is_in(lane_config) -> None:
    """Change the declaration, change the population. Nothing else does."""
    frame = intervals([span("rdr-1", "cancelled", date(2025, 1, 1))])
    assert build_spine(frame, WEEK_END, lane_config).frame.empty

    widened = config_with(lane_config, entitled_states=("active", "trial", "grace", "cancelled"))
    assert len(build_spine(frame, WEEK_END, widened).frame) == 1


def test_the_exclusion_list_removes_a_reader(lane_config) -> None:
    """Exclusions are opaque ids, never predicates over a personal attribute.

    The contract carries no personal field, so a predicate like the four hardcoded
    ones in the system this replaces -- two of them matching fragments of personal
    email addresses -- is not expressible against it at all.
    """
    frame = intervals(
        [span("rdr-1", "active", date(2025, 1, 1)), span("rdr-2", "active", date(2025, 1, 1))]
    )
    manifest = dataclasses.replace(lane_config.manifest, population_exclusions=("rdr-2",))
    excluded = dataclasses.replace(lane_config, manifest=manifest)
    result = build_spine(frame, WEEK_END, excluded)
    assert result.frame["reader_id"].tolist() == ["rdr-1"]
    assert result.excluded == 1


def test_a_null_payer_type_is_carried_as_unknown(lane_config) -> None:
    """Never defaulted to a value, and never a model feature either way."""
    frame = intervals([span("rdr-1", "active", date(2025, 1, 1), payer_type=None)])
    result = build_spine(frame, WEEK_END, lane_config)
    assert result.frame.iloc[0]["payer_type"] is None


def test_a_midweek_snapshot_is_refused(lane_config) -> None:
    frame = intervals([span("rdr-1", "active", date(2025, 1, 1))])
    with pytest.raises(Exception, match="week"):
        build_spine(frame, date(2026, 6, 24), lane_config)


def test_missing_columns_are_named(lane_config) -> None:
    with pytest.raises(SpineError) as exc:
        build_spine(pd.DataFrame({"reader_id": ["rdr-1"]}), WEEK_END, lane_config)
    assert "start_date" in str(exc.value)


def test_stale_sources_are_named_not_swallowed() -> None:
    """An input that stops short of the week end empties everybody's last days."""
    stale = assert_sources_fresh(
        {"reader_channel_day": date(2026, 6, 26), "reader_email_day": WEEK_END}, WEEK_END
    )
    assert len(stale) == 1
    assert "reader_channel_day" in stale[0]
    assert "2026-06-26" in stale[0]
    assert assert_sources_fresh({"reader_channel_day": WEEK_END}, WEEK_END) == []


def test_the_spine_carries_no_model_feature(lane_config) -> None:
    """Every spine column is metadata, and the model guard says so."""
    from engagement_kernel.engagement.guards import inspect_model_columns

    findings = inspect_model_columns(SPINE_COLUMNS)
    refused = {finding.column for finding in findings}
    assert refused == set(SPINE_COLUMNS), (
        f"a spine column is permitted as a model feature: {sorted(set(SPINE_COLUMNS) - refused)}"
    )


def test_the_lane_population_matches_the_declaration(lane_result, lane_config) -> None:
    """End to end: every scored reader is in a declared entitled state."""
    features = lane_result.tables["reader_week_features"]
    assert set(features["state"].unique()) <= set(lane_config.entitled_states)
    assert isinstance(lane_config.scored_population, ScoredPopulation)
