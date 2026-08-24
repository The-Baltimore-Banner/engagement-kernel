"""Scoring one week with a frozen bundle.

Every reader in the scored population comes out with exactly one label. That is the
invariant this module enforces, and it is enforced rather than described because the
alternative fails quietly: a reader who fell out of the label join is absent from
the published table, and an absent row reads as a reader who is not a subscriber.

Three populations, three treatments:

* readers with no observed activity across the whole window get the deterministic
  no-recent label, and no distance -- they never entered the fit, so there is
  nothing to be confident about;
* readers who are content-active are scored on the surface;
* readers who are active but not content-active are also labelled, because the
  surface's breadth dimensions are defined for them (as zero reading, which is a
  real value) -- but they carry the projection flag, since the surface was fit on
  the content-active population.

Nothing here fits. If the bundle's lineage does not match the delivery's feature
version, this refuses rather than scoring: that is the one mismatch that produces a
complete, plausible set of numbers from a calibration fit on something else.
"""

from __future__ import annotations

import pandas as pd

from engagement_kernel.engagement import measures as measures_layer
from engagement_kernel.engagement.assignment import confidence_and_ood, nearest_centroid
from engagement_kernel.engagement.freeze import FrozenBundle
from engagement_kernel.engagement.segments import NO_RECENT_SEGMENT
from engagement_kernel.engagement.surfaces import build_surface_matrix

READER_KEY = "reader_id"

#: Columns published for every scored reader-week.
SCORE_COLUMNS: tuple[str, ...] = (
    READER_KEY,
    "as_of_week_end",
    "cluster_label",
    "cluster_index",
    "cluster_distance",
    "cluster_second_distance",
    "cluster_confidence_margin",
    "within_cluster_distance_pct",
    "out_of_distribution",
    "out_of_distribution_severe",
    "partial_window_flag",
    "projected_flag",
    "model_version",
)


class ScoringError(ValueError):
    """A week could not be scored with the bundle supplied."""


def score_week(
    frame: pd.DataFrame,
    bundle: FrozenBundle,
    *,
    no_recent: pd.Series,
    feature_version: str,
) -> pd.DataFrame:
    """Label every reader in one week's feature frame.

    ``frame`` is the assembled weekly feature frame -- the spine plus every atomic
    layer. ``no_recent`` is the deterministic segment mask, aligned to it.
    """
    bundle.assert_lineage(feature_version)
    if not no_recent.index.equals(frame.index):
        raise ScoringError(
            "the no-recent mask is not aligned to the feature frame; a silent realignment "
            "would label the wrong readers deterministically"
        )

    out = frame[[READER_KEY, "as_of_week_end", "partial_window_flag", "projected_flag"]].copy()
    out["cluster_label"] = NO_RECENT_SEGMENT
    out["cluster_index"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    for column in (
        "cluster_distance",
        "cluster_second_distance",
        "cluster_confidence_margin",
        "within_cluster_distance_pct",
    ):
        out[column] = float("nan")
    out["out_of_distribution"] = 0
    out["out_of_distribution_severe"] = 0
    out["model_version"] = bundle.model_version

    clusterable = ~no_recent.astype(bool)
    if clusterable.any():
        surface = build_surface_matrix(frame.loc[clusterable], bundle.surface_space)
        assignments = nearest_centroid(
            surface[bundle.main.feature_columns].to_numpy(dtype=float),
            bundle.main.centroids,
        )
        confidence = confidence_and_ood(assignments, bundle.main.ood)
        index = out.index[clusterable]
        raw = [int(value) for value in confidence["cluster_index"]]
        out.loc[index, "cluster_index"] = pd.array(raw, dtype="Int64")
        out.loc[index, "cluster_label"] = [bundle.main.label_map[value] for value in raw]
        for column in (
            "cluster_distance",
            "cluster_second_distance",
            "cluster_confidence_margin",
            "within_cluster_distance_pct",
            "out_of_distribution",
            "out_of_distribution_severe",
        ):
            out.loc[index, column] = confidence[column].to_numpy()

    if out["cluster_label"].isna().any():
        raise ScoringError(
            "some readers came out of scoring with no label. Every reader in the scored "
            "population must get exactly one: an absent row is indistinguishable "
            "downstream from a reader who is not a subscriber"
        )
    return out[list(SCORE_COLUMNS)]


def score_measures(
    frame: pd.DataFrame,
    bundle: FrozenBundle,
) -> pd.DataFrame:
    """The engagement measures for one week, from the frozen parameters.

    Scored for every reader in the population, including the no-recent segment: a
    measure is a position on a continuum and "at the bottom of it" is a real answer,
    unlike a cluster label, which would be a claim about which archetype an inactive
    reader resembles.
    """
    if not bundle.measures_params:
        raise ScoringError(
            "this bundle carries no frozen measure parameters, so there is nothing to "
            "apply. Re-freezing would fit them on this week's population, which is the "
            "per-week re-fit the freeze exists to prevent"
        )
    params = measures_layer.MeasuresParams.from_dict(bundle.measures_params)
    matrix = measures_layer.build_signal_matrix(frame, params.layout())
    scored = measures_layer.apply_measures(matrix, params)
    scored.insert(0, "as_of_week_end", frame["as_of_week_end"].to_numpy())
    scored.insert(0, READER_KEY, frame[READER_KEY].to_numpy())
    scored["model_version"] = bundle.model_version
    return scored


def cluster_profile(
    scores: pd.DataFrame,
    frame: pd.DataFrame,
    profile_columns: tuple[str, ...],
) -> pd.DataFrame:
    """Mean of each named atomic per cluster, plus the cluster's share.

    The table a person reads to decide what a cluster is and what to call it, which
    is what the interpretability gate is waiting on. Built from the *raw* atomics
    rather than the standardised surface, because "reads eleven articles a week"
    means something to a newsroom and "sits at +1.4 standard deviations" does not.
    """
    missing = [column for column in profile_columns if column not in frame.columns]
    if missing:
        raise ScoringError(f"profile columns absent from the feature frame: {missing}")
    joined = scores[[READER_KEY, "cluster_label", "cluster_index"]].merge(
        frame[[READER_KEY, *profile_columns]], on=READER_KEY, how="left"
    )
    grouped = joined.groupby(["cluster_index", "cluster_label"], dropna=False)
    profile = grouped[list(profile_columns)].mean().reset_index()
    sizes = grouped.size().reset_index(name="n_readers")
    profile = profile.merge(sizes, on=["cluster_index", "cluster_label"])
    profile["share"] = profile["n_readers"] / profile["n_readers"].sum()
    return profile.sort_values("cluster_index").reset_index(drop=True)
