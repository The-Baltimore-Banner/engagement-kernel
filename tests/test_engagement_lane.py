"""The lane end to end, and the structural assertions that stand in for parity.

Parity here is stated **structurally, not numerically**, and that is a decision
rather than a shortcut. Cluster membership is not reproducible across a change of
data source: the calibrations are z-scores against a fitted mean and standard
deviation, the centroids live in that space, and moving to a different derivation of
the same feature moves the space. That is precisely why a frozen bundle records the
lineage it was fit under and refuses to score anything else -- the machinery exists
because numeric equality across constructions is not available.

What *is* checkable, and is checked here:

* the feature-column manifest -- the surface has exactly the dimensions it declares,
  in the declared order, because a frozen centroid is a vector in that order;
* row counts equal to the spine, per week, so no join has fanned out or dropped;
* share closure on the topic buckets;
* unit variance on each column's own fitting population;
* every reader gets exactly one label;
* the gates produce a verdict, and the interpretability gate fails by default.

There is one thing this file deliberately does not assert: that two runs against
different derivations produce the same centroids. See
``docs/engagement-lane-parity.md`` for why, including the email day shift that makes
numeric email parity unavailable by construction rather than merely unmeasured.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from engagement_kernel.engagement import lane, surfaces
from engagement_kernel.engagement.config import (
    EMAIL_CLICK_UNIT,
    SURFACE_INTENSITY,
    SURFACE_JOINT,
    LaneConfigError,
    resolve_surface,
)
from engagement_kernel.engagement.freeze import FrozenBundle, FrozenBundleError
from engagement_kernel.engagement.segments import NO_RECENT_SEGMENT

# --- it runs, and it froze something ----------------------------------------


def test_the_lane_froze_a_model(lane_result) -> None:
    assert lane_result.froze_a_model
    assert lane_result.champion_k in lane_result.config.k_grid
    assert lane_result.bundle is not None
    lane_result.bundle.validate()


def test_every_declared_output_table_was_written(lane_result) -> None:
    from engagement_kernel.engagement.outputs import OUTPUTS_BY_NAME

    for name in OUTPUTS_BY_NAME:
        assert name in lane_result.tables, f"declared output {name} was not produced"
        assert not lane_result.tables[name].empty, f"{name} is empty"


# --- structural parity ------------------------------------------------------


def test_the_surface_has_exactly_its_declared_columns_in_order(lane_result) -> None:
    """A frozen centroid is a vector in this order, so the order is part of the model."""
    config = lane_result.config
    declared = surfaces.surface_feature_columns(config)
    assert lane_result.bundle.main.feature_columns == declared
    assert lane_result.bundle.surface_space.feature_columns == declared
    assert lane_result.bundle.main.centroids.shape == (lane_result.champion_k, len(declared))


def test_row_counts_equal_the_spine_every_week(lane_result) -> None:
    features = lane_result.tables["reader_week_features"]
    clusters = lane_result.tables["reader_week_cluster"]
    measures = lane_result.tables["reader_week_measures"]
    for week, group in features.groupby("as_of_week_end"):
        expected = len(group)
        assert group["reader_id"].nunique() == expected, f"{week}: duplicate reader in the spine"
        assert (clusters["as_of_week_end"] == week).sum() == expected
        assert (measures["as_of_week_end"] == week).sum() == expected


def test_topic_shares_close_to_one_for_content_active_readers(lane_result) -> None:
    features = lane_result.tables["reader_week_features"]
    columns = [
        f"topic_share_28d__{bucket}" for bucket in lane_result.config.bucket_map.bucket_names
    ]
    active = features.loc[features["content_active_flag"].astype(bool)]
    assert not active.empty
    totals = active[columns].sum(axis=1)
    assert np.allclose(totals, 1.0, atol=1e-9)


def test_topic_shares_are_zero_not_evenly_split_for_readers_with_no_resolved_reading(
    lane_result,
) -> None:
    """Zero says "no mix"; an even split says "equally interested in everything"."""
    features = lane_result.tables["reader_week_features"]
    columns = [
        f"topic_share_28d__{bucket}" for bucket in lane_result.config.bucket_map.bucket_names
    ]
    none_resolved = features.loc[features["resolved_section_views_28d"] == 0]
    if none_resolved.empty:
        pytest.skip("this cohort has no reader with zero resolved reading")
    assert (none_resolved[columns].sum(axis=1) == 0).all()


def test_surface_variance_is_near_one_on_the_fitting_population(lane_result) -> None:
    """Each column is a z-score against its own fitting rows, so it should be near 1 there.

    A column far from 1 on the population it was fit on means the fit and the apply
    saw different rows -- which produces a full set of finite, wrong features. The run
    records the measured variances in the bundle, so this reads them rather than
    re-deriving the panel and getting to check a different number.
    """
    variances = lane_result.bundle.k_selection["surface_variances"]
    assert set(variances) == set(lane_result.bundle.main.feature_columns)
    for column, variance in variances.items():
        assert 0.5 <= variance <= 2.0, f"{column} has baseline variance {variance:.3f}"


def test_every_reader_week_gets_exactly_one_label(lane_result) -> None:
    clusters = lane_result.tables["reader_week_cluster"]
    assert clusters["cluster_label"].notna().all()
    assert not clusters.duplicated(subset=["reader_id", "as_of_week_end"]).any()
    labels = set(clusters["cluster_label"].unique())
    model_labels = set(lane_result.bundle.main.label_map.values())
    assert labels <= model_labels | {NO_RECENT_SEGMENT}


def test_the_no_recent_segment_carries_no_distance(lane_result) -> None:
    """It never entered the fit, so there is nothing to be confident about."""
    clusters = lane_result.tables["reader_week_cluster"]
    quiet = clusters.loc[clusters["cluster_label"] == NO_RECENT_SEGMENT]
    if quiet.empty:
        pytest.skip("this cohort has no fully inactive reader-week")
    assert quiet["cluster_distance"].isna().all()
    assert quiet["cluster_index"].isna().all()


def test_the_gates_produce_a_verdict_and_the_manual_one_fails_by_default(lane_result) -> None:
    """The interpretability gate has no automatable evidence, so its default is a failure.

    Defaulting it to a pass would publish cluster names nobody had looked at.
    """
    report = lane_result.tables["gate_report"]
    assert not report.empty
    interpretability = report.loc[report["check"] == "interpretability_reviewed"].iloc[0]
    assert not interpretability["passed"]
    assert lane_result.decision is not None
    assert not lane_result.decision.publish_labels
    assert "interpretability" in lane_result.decision.describe()


def test_every_gate_reports_its_realised_value(lane_result) -> None:
    """A gate that reports only pass/fail cannot be used to set its own threshold."""
    report = lane_result.tables["gate_report"]
    thresholded = report.loc[
        report["check"].isin(
            [
                "topic_metadata_coverage",
                "bucket_map_completeness",
                "champion_survives_perturbed_panels",
                "no_unintended_micro_clusters",
                "four_week_retention",
            ]
        )
    ]
    assert not thresholded.empty
    assert (thresholded["detail"].str.len() > 0).all()


# --- the frozen bundle ------------------------------------------------------


def test_the_bundle_round_trips(lane_result, tmp_path) -> None:
    path = tmp_path / "bundle.json"
    lane_result.bundle.save(path)
    reloaded = FrozenBundle.load(path)
    assert reloaded.model_version == lane_result.bundle.model_version
    assert reloaded.lineage == lane_result.bundle.lineage
    assert np.allclose(reloaded.main.centroids, lane_result.bundle.main.centroids)
    assert reloaded.main.feature_columns == lane_result.bundle.main.feature_columns


def test_the_bundle_refuses_a_different_lineage(lane_result) -> None:
    """The one mismatch that would otherwise produce a full set of plausible numbers."""
    with pytest.raises(FrozenBundleError) as exc:
        lane_result.bundle.assert_lineage("contract=1.0.0|article_view=something-else")
    assert "different feature version" in str(exc.value)
    lane_result.bundle.assert_lineage(lane_result.config.feature_version())


def test_the_lineage_moves_with_the_declarations(lane_config) -> None:
    """Every declaration that changes the numbers is in the lineage string."""
    base = lane_config.feature_version()
    manifest = lane_config.manifest

    other_population = dataclasses.replace(
        manifest.scored_population, definition_id="a-different-population"
    )
    changed = dataclasses.replace(
        lane_config, manifest=dataclasses.replace(manifest, scored_population=other_population)
    )
    assert changed.feature_version() != base

    other_zone = dataclasses.replace(
        lane_config, manifest=dataclasses.replace(manifest, day_boundary_timezone="UTC")
    )
    assert other_zone.feature_version() != base


def test_the_click_unit_is_recorded_and_moves_with_the_model(lane_config) -> None:
    """The decision is click events, and it is part of the version, not a comment."""
    assert EMAIL_CLICK_UNIT == "click_event"
    assert f"click_unit={EMAIL_CLICK_UNIT}" in lane_config.feature_version()


def test_the_bucket_map_snapshot_is_in_the_bundle(lane_result) -> None:
    snapshot = lane_result.bundle.bucket_map_snapshot
    assert snapshot, "a version string alone cannot say which sections a bucket held"
    assert set(snapshot) == set(lane_result.config.bucket_map.buckets)


# --- surface resolution -----------------------------------------------------


def test_the_joint_surface_is_chosen_when_both_optional_inputs_are_available(manifest) -> None:
    assert resolve_surface(manifest) == SURFACE_JOINT


def test_an_absent_optional_input_selects_the_alternate_surface(manifest) -> None:
    """Absence names a different feature set. It does not zero-fill columns."""
    without_community = dataclasses.replace(
        manifest,
        optional_inputs={
            name: value
            for name, value in manifest.optional_inputs.items()
            if name != "community_action"
        },
    )
    assert resolve_surface(without_community) == SURFACE_INTENSITY


def test_naming_an_unsupported_surface_is_refused_not_downgraded(manifest, bucket_map) -> None:
    """A silent downgrade would publish labels from a surface nobody asked for."""
    without_email = dataclasses.replace(
        manifest,
        optional_inputs={
            name: value for name, value in manifest.optional_inputs.items() if name != "email_click"
        },
    )
    with pytest.raises(LaneConfigError) as exc:
        lane.resolve_config(without_email, bucket_map, surface=SURFACE_JOINT)
    assert "declared alternate" in str(exc.value)
    assert "email_click" in str(exc.value)


def test_the_intensity_surface_runs_too(weekly_inputs, lane_config) -> None:
    """The alternate feature set is a real path, not a name in a docstring."""
    intensity = dataclasses.replace(lane_config, surface=SURFACE_INTENSITY)
    result = lane.run_lane(weekly_inputs, intensity, max_weeks=6)
    assert result.tables["k_selection"] is not None
    expected = surfaces.intensity_feature_columns(intensity)
    if result.bundle is not None:
        assert result.bundle.main.feature_columns == expected
        assert len(expected) < len(surfaces.joint_feature_columns(lane_config))


# --- refusals ---------------------------------------------------------------


def test_a_run_too_short_for_a_panel_is_refused(weekly_inputs, lane_config) -> None:
    with pytest.raises(lane.LaneError) as exc:
        lane.run_lane(weekly_inputs, lane_config, max_weeks=1)
    assert "cannot support a panel" in str(exc.value)


def test_a_population_that_matches_nobody_is_refused(weekly_inputs, lane_config) -> None:
    """An empty population is refused rather than producing an empty run."""
    scored = dataclasses.replace(
        lane_config.manifest.scored_population, entitled_states=("expired",)
    )
    empty = dataclasses.replace(
        lane_config,
        manifest=dataclasses.replace(lane_config.manifest, scored_population=scored),
    )
    with pytest.raises(Exception, match="scored population"):
        lane.run_lane(weekly_inputs, empty, max_weeks=4)


def test_the_measures_are_scored_for_everyone_including_the_quiet(lane_result) -> None:
    """A measure is a position on a continuum, so "at the bottom" is a real answer."""
    measures = lane_result.tables["reader_week_measures"]
    features = lane_result.tables["reader_week_features"]
    assert len(measures) == len(features)
    for key in ("m1", "m2", "m3"):
        assert measures[f"{key}_score"].notna().all()
        assert measures[f"{key}_percentile"].between(0, 100).all()


def test_the_cluster_profile_is_in_raw_units(lane_result) -> None:
    """The table the interpretability review reads has to be readable."""
    profile = lane_result.tables["cluster_profile"]
    assert "share" in profile.columns
    assert profile["share"].sum() == pytest.approx(1.0)
    assert (profile["n_readers"] > 0).all()
    assert any(column.endswith("_views_28d") for column in profile.columns)


def test_scoring_refuses_a_misaligned_segment_mask(lane_result, weekly_inputs, lane_config) -> None:
    from engagement_kernel.engagement.scoring import ScoringError, score_week

    week = lane.build_weekly_features(weekly_inputs, lane_result.weeks[-1], lane_config)
    frame = week.frame.reset_index(drop=True)
    misaligned = pd.Series(week.no_recent.to_numpy(), index=range(1, len(frame) + 1))
    with pytest.raises(ScoringError) as exc:
        score_week(
            frame,
            lane_result.bundle,
            no_recent=misaligned,
            feature_version=lane_config.feature_version(),
        )
    assert "aligned" in str(exc.value)
