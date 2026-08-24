"""The engagement measures, and the click-unit claim stated as arithmetic.

Two things here are worth more than the coverage.

:func:`test_the_cadence_axis_is_invariant_to_the_click_unit` encodes the corrected
mechanism behind the email click-unit decision. The decision -- click events, not
distinct campaigns clicked -- is real and it moves the model, but it does **not**
reach the model through the cadence axis, which is what an earlier framing of it
said. Cadence counts weeks with a non-zero bin, and any week containing one click has
a non-zero bin under either unit. What moves is click *volume*. Somebody debugging a
cadence difference by looking at the click unit is looking in the wrong place, and
this test is where that is written down in a form that stays true.

:func:`test_the_percentile_snap_absorbs_last_bit_noise` protects a rounding step that
looks pointless and is not. Average-rank ties make the percentile a step function of
its input, so a last-bit difference in a score -- a different CPU, a different
linear-algebra kernel -- splits a tie group and moves published percentiles. Deleting
the snap re-opens a failure that has broken continuous integration on byte-identical
code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engagement_kernel.engagement import measures
from engagement_kernel.engagement.transforms import weekly_bin_consistency


def test_the_cadence_axis_is_invariant_to_the_click_unit() -> None:
    """Same weeks, two units, identical cadence -- and different volume.

    ``clicks`` is what a reader clicked; ``campaigns`` is how many distinct sends they
    clicked in the same week. The second is always at most the first and is smaller
    wherever a reader clicked one send twice.
    """
    clicks = pd.DataFrame(
        {
            "b1": [4.0, 1.0, 0.0, 7.0],
            "b2": [2.0, 0.0, 0.0, 3.0],
            "b3": [0.0, 1.0, 0.0, 2.0],
            "b4": [1.0, 0.0, 0.0, 0.0],
        }
    )
    campaigns = pd.DataFrame(
        {
            "b1": [2.0, 1.0, 0.0, 3.0],
            "b2": [1.0, 0.0, 0.0, 1.0],
            "b3": [0.0, 1.0, 0.0, 1.0],
            "b4": [1.0, 0.0, 0.0, 0.0],
        }
    )
    assert not clicks.equals(campaigns), "the two units must actually differ here"

    left = weekly_bin_consistency(clicks)
    right = weekly_bin_consistency(campaigns)

    # The cadence axis: identical under both units.
    assert left["active_weeks_4"].tolist() == right["active_weeks_4"].tolist()

    # And the volume totals are not, which is the part the decision does move.
    assert clicks.sum(axis=1).tolist() != campaigns.sum(axis=1).tolist()


def test_active_weeks_counts_weeks_not_clicks() -> None:
    """The property the invariance rests on, asserted directly."""
    one_click_a_week = pd.DataFrame({"b1": [1.0], "b2": [1.0], "b3": [1.0], "b4": [1.0]})
    many_clicks_one_week = pd.DataFrame({"b1": [40.0], "b2": [0.0], "b3": [0.0], "b4": [0.0]})
    assert weekly_bin_consistency(one_click_a_week)["active_weeks_4"].iloc[0] == 4
    assert weekly_bin_consistency(many_clicks_one_week)["active_weeks_4"].iloc[0] == 1


def test_an_inactive_reader_has_zero_entropy_not_nan() -> None:
    """A reader with no activity has an evenness of zero, and it has to be a number."""
    empty = pd.DataFrame({"b1": [0.0], "b2": [0.0], "b3": [0.0], "b4": [0.0]})
    row = weekly_bin_consistency(empty).iloc[0]
    assert row["weekly_evenness_entropy_4"] == 0.0
    assert row["top_week_share_4"] == 0.0
    assert np.isfinite(row["weekly_cv_4"])


def test_the_layout_drops_a_block_the_delivery_cannot_support() -> None:
    """A block of zeros would drag every reader's composite toward the middle."""
    both = measures.build_layout(("web", "app"), has_email=True, has_community=True)
    assert measures.BLOCK_COMMUNITY in both.blocks
    assert measures.BLOCK_LOYALTY in both.blocks

    neither = measures.build_layout(("web", "app"), has_email=False, has_community=False)
    assert measures.BLOCK_COMMUNITY not in neither.blocks
    assert measures.BLOCK_LOYALTY not in neither.blocks
    assert "email_cadence" not in neither.signals


def test_the_cadence_signal_reads_the_source_atomic_not_a_surface_name() -> None:
    """Naming the surface column here raised on every real frame once. It is a real defect."""
    layout = measures.build_layout(("web",), has_email=True, has_community=False)
    assert layout.sources["email_cadence"] == "email_click_active_weeks_4"
    assert "email_cadence__" not in layout.sources["email_cadence"]


def test_the_layout_signals_are_partitioned_by_the_blocks() -> None:
    """A signal in no block counts in m1 and silently vanishes from m2 and m3."""
    layout = measures.build_layout(("web", "app"), has_email=True, has_community=True)
    flattened = [signal for block in layout.blocks.values() for signal in block]
    assert sorted(flattened) == sorted(layout.signals)


def test_oriented_pc1_puts_more_activity_higher() -> None:
    """PC1's sign is arbitrary; without orientation half of all refits invert the score."""
    rising = np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [10.0, 9.0]])
    scores, loadings, variance, sign = measures.oriented_pc1(rising)
    assert loadings.sum() >= 0
    assert scores[-1] > scores[0]
    assert 0.0 <= variance <= 1.0
    assert sign in (1.0, -1.0)


def test_the_percentile_snap_absorbs_last_bit_noise() -> None:
    """Two scores differing in the last bit must land in the same tie group."""
    base = 1.2345678901234
    values = np.array([base, base + 2.0e-16, base, 5.0])
    percentiles = measures.within_week_percentile(values)
    assert percentiles[0] == percentiles[1] == percentiles[2], (
        "a 1e-16 input difference split a tie group, which moves a published percentile"
    )


def test_the_snap_does_not_merge_genuinely_distinct_scores() -> None:
    """A snap coarse enough to erase real differences would be worse than none."""
    values = np.array([1.0, 1.0 + 1e-8, 2.0])
    percentiles = measures.within_week_percentile(values)
    assert percentiles[0] != percentiles[1]


def test_the_measures_apply_from_frozen_parameters_only(lane_result) -> None:
    """Applying twice to the same rows gives the same scores: nothing is re-fit."""
    from engagement_kernel.engagement.scoring import score_measures

    features = lane_result.tables["reader_week_features"]
    week = features.loc[features["as_of_week_end"] == lane_result.weeks[-1]].reset_index(drop=True)
    first = score_measures(week, lane_result.bundle)
    second = score_measures(week, lane_result.bundle)
    pd.testing.assert_frame_equal(first, second)


def test_percentiles_are_within_week_not_against_a_frozen_reference(lane_result) -> None:
    """Scoring half a week's population changes the percentiles and not the raw scores.

    "Top 10% of engaged readers" means top 10% of the people who are here now. A
    percentile against a frozen reference drifts as the audience grows and ends up
    describing a population that no longer exists.
    """
    from engagement_kernel.engagement.scoring import score_measures

    features = lane_result.tables["reader_week_features"]
    week = features.loc[features["as_of_week_end"] == lane_result.weeks[-1]].reset_index(drop=True)
    whole = score_measures(week, lane_result.bundle)
    half = score_measures(week.iloc[: len(week) // 2].reset_index(drop=True), lane_result.bundle)

    common = min(len(half), len(whole))
    assert np.allclose(
        half["m1_score"].to_numpy()[:common], whole["m1_score"].to_numpy()[:common]
    ), "the raw score is frozen and must not depend on who else was scored"
    assert not np.allclose(
        half["m1_percentile"].to_numpy()[:common], whole["m1_percentile"].to_numpy()[:common]
    ), "the percentile is within-week and must depend on the population"


def test_the_measure_parameters_round_trip(lane_result, tmp_path) -> None:
    params = measures.MeasuresParams.from_dict(lane_result.bundle.measures_params)
    path = tmp_path / "measures.json"
    params.save(path)
    reloaded = measures.MeasuresParams.load(path)
    assert reloaded.signals == params.signals
    assert list(reloaded.blocks) == list(params.blocks)
    assert reloaded.m1_loadings == pytest.approx(params.m1_loadings)


def test_the_block_weights_sum_to_one(lane_result) -> None:
    params = measures.MeasuresParams.from_dict(lane_result.bundle.measures_params)
    assert sum(params.block_weights.values()) == pytest.approx(1.0)


def test_the_three_measures_disagree_somewhere(lane_result) -> None:
    """Publishing three constructions is only honest if they are actually different.

    They agree at the extremes and disagree in the middle -- a heavy commenter who
    reads little ranks far higher on the equally-weighted composite than on the
    variance-weighted one. If they never disagreed, two of them would be noise.
    """
    measure_rows = lane_result.tables["reader_week_measures"]
    m1 = measure_rows["m1_percentile"].to_numpy()
    m3 = measure_rows["m3_percentile"].to_numpy()
    assert np.abs(m1 - m3).max() > 1.0
