"""The wide, block-weighted construction, exercised end to end.

This is the alternate matrix: every semantic feature, grouped into blocks, each block
allocated a share of the model's distance. It is a supported construction and it is
not the default -- see :mod:`engagement_kernel.engagement.surfaces` for why -- and it
is tested here for a reason that matters more than coverage.

**Shipped code that nothing runs is a liability.** A construction that is present,
documented and never executed will be wrong by the time somebody reaches for it, and
they will reach for it while trying to solve a different problem. So this fits the
whole pipeline on the real panel, assembles the weighted matrix with every assertion
live, and checks the arithmetic that makes the weights mean what they say.

The weighting arithmetic is the part worth reading. Each column is scaled by
``sqrt(w_block / n_block)``, and both halves are load-bearing: dividing by the block's
size stops a block being louder for having more columns in it, and the square root is
because a distance sums *squared* differences, so a column scaled by ``sqrt(x)``
contributes ``x``. Scaling by ``x`` directly would make the realised contributions the
squares of the intended ones -- which still produces a matrix, still clusters, and is
wrong in a way no single number reveals.
"""

from __future__ import annotations

import numpy as np
import pytest

from engagement_kernel.engagement import matrix as matrix_layer
from engagement_kernel.engagement import panel as panel_layer
from engagement_kernel.engagement import pipeline as pipeline_layer
from engagement_kernel.engagement.features import (
    activity_anchors,
    feature_channels,
    stack_weeks,
)
from engagement_kernel.engagement.gates import feature_quality_gates
from engagement_kernel.engagement.guards import ForbiddenModelColumn
from engagement_kernel.engagement.imputation import imputation_share
from engagement_kernel.engagement.lane import _week_ends, build_weekly_features
from engagement_kernel.engagement.spine import fit_cohort_mask


@pytest.fixture(scope="module")
def block_fit(weekly_inputs, lane_config):
    """Fit the wide pipeline on the real panel, and apply it to the stacked weeks."""
    weeks = _week_ends(weekly_inputs, lane_config)[-8:]
    weekly = [build_weekly_features(weekly_inputs, week, lane_config) for week in weeks]
    stacked = stack_weeks(weekly)

    eligible = stacked.loc[fit_cohort_mask(stacked) & ~stacked["no_recent_flag"].astype(bool)]
    panel = panel_layer.sample_panel(eligible, seed=lane_config.panel_seed)
    panel_content_active = panel["content_active_flag"].astype(bool)

    anchors = activity_anchors(lane_config, weekly_inputs)
    channels = lane_config.channels
    fitted = pipeline_layer.fit_pipeline(
        panel,
        lane_config.bucket_map,
        anchors,
        channels,
        panel_content_active=panel_content_active,
    )
    features, flags = pipeline_layer.apply_pipeline(
        panel, fitted, content_active=panel_content_active
    )
    masks = pipeline_layer.conditional_fitting_masks(
        panel, fitted, content_active=panel_content_active
    )
    return {
        "config": lane_config,
        "inputs": weekly_inputs,
        "panel": panel,
        "content_active": panel_content_active,
        "fitted": fitted,
        "features": features,
        "flags": flags,
        "masks": masks,
        "anchors": anchors,
        "channels": channels,
    }


@pytest.fixture(scope="module")
def weighted(block_fit):
    config = block_fit["config"]
    membership = matrix_layer.block_membership(
        block_fit["channels"],
        config.bucket_map.bucket_names,
        include_consistency=block_fit["fitted"].include_consistency(),
        include_email="email" in block_fit["anchors"],
        include_community="community" in block_fit["anchors"],
    )
    realized = list(block_fit["features"].columns)
    declared = matrix_layer.declared_columns(membership)
    # The fit's own record of what it dropped and why -- not "whatever is missing".
    # Reconciling against the absences would make the assertion vacuous: any column
    # that vanished for any reason would be documented by definition.
    documented = block_fit["fitted"].dropped_columns()
    matrix_layer.assert_column_manifest(realized, declared, documented_drops=documented)
    live = {
        block: [column for column in columns if column in realized]
        for block, columns in membership.items()
    }
    live = {block: columns for block, columns in live.items() if columns}
    weights = {block: 1.0 / len(live) for block in live}
    return (
        matrix_layer.build_weighted_matrix(
            block_fit["features"][[column for columns in live.values() for column in columns]],
            live,
            weights,
            fitting_masks=block_fit["masks"],
        ),
        live,
        weights,
        documented,
    )


def test_the_pipeline_fits_every_block(block_fit) -> None:
    fitted = block_fit["fitted"]
    assert fitted.blocks, "no semantic block was fit"
    for name, model in fitted.blocks.items():
        assert model.method in ("pca", "anchor", "equal_weight", "dropped"), name
        if model.method != "dropped":
            assert model.score_calibration is not None, name


def test_a_block_that_failed_its_gates_fell_back_rather_than_being_forced(block_fit) -> None:
    """A forced first component is a direction through a cloud wearing a name."""
    for name, model in block_fit["fitted"].blocks.items():
        if model.method == "pca":
            assert not model.gate_failures, f"{name} kept a component that failed its gates"
        else:
            assert model.gate_failures or model.method == "dropped", (
                f"{name} fell back to {model.method} with no recorded reason"
            )


def test_pca_blocks_are_oriented_to_their_anchor(block_fit) -> None:
    """Without orientation, half of all refits publish a score that falls as activity rises."""
    for name, model in block_fit["fitted"].blocks.items():
        if model.method == "pca":
            assert model.anchor_corr is not None
            assert model.anchor_corr > 0, f"{name} is oriented against its anchor"


def test_the_consistency_redundancy_decision_is_recorded_as_a_number(block_fit) -> None:
    """A dropped column with no recorded correlation is an absence nobody can audit."""
    fitted = block_fit["fitted"]
    for channel in block_fit["channels"]:
        assert channel in fitted.consistency_dropped
        if fitted.consistency_dropped[channel]:
            assert channel in fitted.consistency_corr
            assert abs(fitted.consistency_corr[channel]) > 0.8


def test_the_feature_frame_carries_no_forbidden_column(block_fit) -> None:
    from engagement_kernel.engagement.guards import inspect_model_columns

    findings = inspect_model_columns(block_fit["features"].columns)
    assert findings == [], f"the wide feature frame carries refused columns: {findings}"


def test_the_imputation_flags_are_not_in_the_feature_frame(block_fit) -> None:
    """They tell a distance function which rows were imputed. Separate frame, by design."""
    assert not any(column.endswith("_imputed_flag") for column in block_fit["features"].columns)
    assert any(column.endswith("_imputed_flag") for column in block_fit["flags"].columns)


def test_conditional_features_are_exactly_zero_where_imputed(block_fit) -> None:
    features, flags = block_fit["features"], block_fit["flags"]
    for flag_column in flags.columns:
        feature = flag_column.removesuffix("_imputed_flag")
        if feature not in features.columns:
            continue
        imputed = flags[flag_column].astype(bool)
        if imputed.any():
            assert (features.loc[imputed, feature] == 0.0).all(), feature


def test_the_topic_imputation_share_reconciles_with_the_content_active_share(block_fit) -> None:
    """The gate that catches a fit mask and an impute mask coming apart."""
    report = feature_quality_gates(
        blocks=block_fit["fitted"].describe_blocks(),
        imputation_share=imputation_share(block_fit["flags"]),
        content_active_share=float(block_fit["content_active"].mean()),
    )
    reconciles = next(
        check for check in report.checks if check.name == "topic_imputation_share_reconciles"
    )
    assert reconciles.passed, reconciles.detail


def test_the_weighted_matrix_assembles_with_every_assertion_live(weighted) -> None:
    result, live, weights, _documented = weighted
    assert not result.matrix.empty
    assert set(result.column_blocks.values()) == set(live)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert np.isfinite(result.matrix.to_numpy(dtype=float)).all()


def test_the_scaling_is_sqrt_of_weight_over_block_size(weighted) -> None:
    """The arithmetic that makes a block weight mean a share of the distance."""
    result, live, weights, _documented = weighted
    for block, columns in live.items():
        expected = float(np.sqrt(weights[block] / len(columns)))
        for column in columns:
            assert result.column_factors[column] == pytest.approx(expected)


def test_the_realised_block_contributions_track_the_nominal_weights(weighted) -> None:
    """If they do not, the weights are intent rather than description.

    Checked on the rows every block is defined for. On the full population a
    conditional block sits at the imputed baseline for most readers, so its realised
    contribution is legitimately far below its nominal weight -- which is worth
    reporting and is not what this asserts.
    """
    result, live, weights, _documented = weighted
    contributions = matrix_layer.realized_block_contributions(result)
    for block in live:
        assert contributions[block] == pytest.approx(weights[block], abs=0.25), (
            f"{block}: realised {contributions[block]:.3f} against nominal {weights[block]:.3f}"
        )


def test_an_unassigned_column_is_refused(block_fit) -> None:
    """An unweighted column keeps its full unit variance and outweighs every weighted one."""
    features = block_fit["features"]
    columns = list(features.columns)[:3]
    membership = {"a": columns[:1], "b": columns[1:2]}
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.apply_block_weights(features[columns], membership, {"a": 0.5, "b": 0.5})
    assert "assigned to no block" in str(exc.value)


def test_a_column_in_two_blocks_is_refused(block_fit) -> None:
    features = block_fit["features"]
    columns = list(features.columns)[:2]
    membership = {"a": columns, "b": columns[:1]}
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.apply_block_weights(features[columns], membership, {"a": 0.5, "b": 0.5})
    assert "scaled twice" in str(exc.value)


def test_weights_that_do_not_sum_to_one_are_refused(block_fit) -> None:
    features = block_fit["features"]
    column = list(features.columns)[0]
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.apply_block_weights(features[[column]], {"a": [column]}, {"a": 0.75})
    assert "not 1" in str(exc.value)


def test_a_silently_absent_declared_column_is_refused() -> None:
    """The defect this assertion exists for: a frozen matrix short of a declared column."""
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.assert_column_manifest(
            ["web_intensity"], ["web_intensity", "topic_share_news"]
        )
    assert "topic_share_news" in str(exc.value)
    # Recorded as a drop, it is permitted.
    matrix_layer.assert_column_manifest(
        ["web_intensity"],
        ["web_intensity", "topic_share_news"],
        documented_drops=("topic_share_news",),
    )


def test_a_column_recorded_as_dropped_but_still_present_is_refused() -> None:
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.assert_column_manifest(
            ["web_intensity"], ["web_intensity"], documented_drops=("web_intensity",)
        )
    assert "still in the matrix" in str(exc.value)


def test_the_matrix_builder_runs_the_model_guard_first(block_fit) -> None:
    """Before any arithmetic, so a forbidden column cannot be standardised on the way."""
    features = block_fit["features"].copy()
    features["state"] = "active"
    column = [c for c in block_fit["features"].columns][0]
    with pytest.raises(ForbiddenModelColumn):
        matrix_layer.build_weighted_matrix(
            features[[column, "state"]],
            {"a": [column, "state"]},
            {"a": 1.0},
        )


def test_a_non_finite_value_is_refused(block_fit) -> None:
    """A distance against NaN is NaN, and the row lands wherever the comparison fell."""
    features = block_fit["features"].copy()
    column = list(features.columns)[0]
    features.loc[features.index[0], column] = np.nan
    with pytest.raises(matrix_layer.MatrixError) as exc:
        matrix_layer.assert_finite(features[[column]])
    assert column in str(exc.value)


def test_the_variance_assertion_reads_each_columns_own_fitting_population(block_fit) -> None:
    """A conditional column measured on the full population fails for the wrong reason."""
    features, masks = block_fit["features"], block_fit["masks"]
    conditional = [column for column in features.columns if column in masks]
    assert conditional, "expected at least one conditional column"
    # On its own fitting rows every column is near unit variance.
    matrix_layer.assert_unit_variance(features, fitting_masks=masks)


def test_a_catch_all_bucket_with_no_remainder_is_dropped_as_a_recorded_decision(
    block_fit,
) -> None:
    """The portability case a mapped-in-full taxonomy produces.

    The cohort's bucket map covers every section it publishes, so nothing falls into
    the catch-all and its share is zero for every reader. The column is correct and it
    carries no information: it cannot influence an assignment and would still occupy a
    share of the topic block's weight. Left in, it fails the unit-variance assertion
    several layers downstream with a message about standardisation populations rather
    than about the taxonomy -- so it is named at fit time and dropped on the record.
    """
    fitted = block_fit["fitted"]
    catch_all = f"topic_share_{block_fit['config'].bucket_map.catch_all_bucket}"
    assert catch_all in fitted.topic_dropped, (
        "the cohort maps every section, so the catch-all share is constant zero and "
        "should have been recorded as dropped"
    )
    assert catch_all not in block_fit["features"].columns
    assert catch_all in fitted.dropped_columns()


def test_the_documented_drops_are_the_fits_own_record(block_fit) -> None:
    """Not "whatever is missing" -- that would make the manifest assertion vacuous."""
    fitted = block_fit["fitted"]
    realized = set(block_fit["features"].columns)
    for column in fitted.dropped_columns():
        assert column not in realized, f"{column} is recorded as dropped and still present"


def test_the_channel_helpers_agree_with_the_delivery(block_fit) -> None:
    """The helpers the wide path needs are the ones the lane resolved."""
    config, inputs = block_fit["config"], block_fit["inputs"]
    channels = feature_channels(config, inputs)
    anchors = activity_anchors(config, inputs)
    assert set(anchors) == set(channels)
    for channel in config.channels:
        assert anchors[channel] == f"{channel}_views_28d"
