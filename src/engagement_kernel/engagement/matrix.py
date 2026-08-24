"""Assembling a model matrix, and deciding how much distance each block owns.

Every column arriving here is a z-score against its own fitting population, so
before weighting they all have variance near 1 and none dominates by accident of
scale. The weighting then says, deliberately, how much of the model's distance each
block of features is allowed to account for.

The scaling is ``sqrt(w_b / n_b)``. Both parts are load-bearing. Dividing by the
block's size stops a block being louder for having more columns in it -- otherwise
"web" with six features would outvote "cross-channel" with three whatever the
weights said. The square root is because a distance is a sum of *squared*
differences, so a column scaled by ``sqrt(x)`` contributes ``x``; scaling by ``x``
directly would make the realised contributions the squares of the intended ones.

Four assertions run in a fixed order and the order is the point: refuse forbidden
columns, then drop the deterministic segment, then require finiteness, then require
unit variance *on each column's own fitting population*. Checking variance before
dropping the no-recent readers would measure the variance of a population the
column was never fit on -- and the conditional columns, which are the baseline
constant on exactly those rows, would fail for the wrong reason.

:func:`assert_column_manifest` exists because of a specific failure. A frozen
matrix silently omitted three declared columns -- a recency feature, a mix share
and one topic bucket -- and because the assembly reconciled its block membership
down to whatever columns happened to exist, nothing complained. The consequence was
that the topic shares no longer summed to 1, which is not visible in any single
number. A column may be absent, but only as a recorded decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engagement_kernel.engagement.guards import assert_no_forbidden_model_columns

#: Bounds a column's baseline variance must sit inside. A build failure outside
#: them, not a warning: a column at variance 0.02 is a column that will never
#: influence an assignment, and it is still occupying a block weight.
VARIANCE_BOUNDS = (0.5, 2.0)


class MatrixError(ValueError):
    """A model matrix violated an invariant the model depends on."""


def assert_finite(matrix: pd.DataFrame) -> None:
    values = matrix.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        bad = matrix.columns[~np.isfinite(values).all(axis=0)].tolist()
        raise MatrixError(
            f"the model matrix carries null, NaN or infinite values in {bad}. A distance "
            "against a NaN is NaN, and the row would be assigned to whichever centroid "
            "the comparison happened to favour"
        )


def assert_unit_variance(
    matrix: pd.DataFrame,
    *,
    fitting_masks: dict[str, pd.Series] | None = None,
    bounds: tuple[float, float] = VARIANCE_BOUNDS,
) -> dict[str, float]:
    """Per-column variance on the population that column was standardised against.

    ``fitting_masks`` maps a column to its fitting rows -- conditional columns to
    the channel-active rows, topic columns to the content-active rows. A column with
    no entry is checked over every row.
    """
    fitting_masks = fitting_masks or {}
    variances: dict[str, float] = {}
    violations: dict[str, float] = {}
    for column in matrix.columns:
        mask = fitting_masks.get(column)
        values = matrix[column] if mask is None else matrix.loc[mask.astype(bool), column]
        variance = float(values.var(ddof=0))
        variances[column] = variance
        if not (bounds[0] <= variance <= bounds[1]):
            violations[column] = variance
    if violations:
        raise MatrixError(
            f"baseline variance outside {list(bounds)}: {violations}. These columns were "
            "standardised on a different population from the one they are being measured "
            "on, or they are near-constant and cannot influence an assignment"
        )
    return variances


def block_membership(
    channels: tuple[str, ...],
    bucket_names: tuple[str, ...],
    *,
    include_consistency: dict[str, bool] | None = None,
    include_email: bool = True,
    include_community: bool = True,
) -> dict[str, list[str]]:
    """Which feature belongs to which block.

    Derived from the channels and buckets the delivery actually has, so a
    deployment with one reader-event channel gets a membership covering one, not a
    membership referencing columns that were never built.
    """
    include_consistency = include_consistency or {}
    consumption: list[str] = []
    for channel in channels:
        consumption.extend(
            [
                f"{channel}_intensity",
                f"{channel}_habit",
                f"{channel}_recency",
                f"{channel}_depth",
                f"{channel}_momentum",
            ]
        )
        if include_consistency.get(channel, True):
            consumption.append(f"{channel}_consistency")

    cross_channel = [f"channel_mix_{channel}" for channel in channels]
    if include_email:
        cross_channel.append("channel_mix_email")
    if include_community:
        cross_channel.append("channel_mix_community")
    cross_channel.extend(["overall_recency", "overall_active_days", "overall_momentum"])

    membership: dict[str, list[str]] = {
        "consumption": consumption,
        "cross_channel": cross_channel,
        "topic": [f"topic_share_{bucket}" for bucket in bucket_names] + ["topic_breadth"],
    }
    if include_email:
        membership["email_click"] = [
            "email_click_intensity",
            "email_click_habit",
            "email_click_recency",
        ]
    if include_community:
        membership["community"] = [
            "community_intensity",
            "community_habit",
            "community_recency",
            "community_contribution_depth",
        ]
    return membership


def declared_columns(membership: dict[str, list[str]]) -> list[str]:
    """The flat manifest a realised matrix is reconciled against."""
    return [column for columns in membership.values() for column in columns]


def assert_column_manifest(
    realized: list[str],
    declared: list[str],
    *,
    documented_drops: tuple[str, ...] | list[str] = (),
) -> None:
    """Every declared column is present, or recorded as dropped."""
    present = set(realized)
    documented = set(documented_drops)
    missing = [column for column in declared if column not in present and column not in documented]
    if missing:
        raise MatrixError(
            f"declared model columns are absent with no recorded drop: {sorted(set(missing))}. "
            "Restore them or record the drop: a matrix reconciled down to whatever columns "
            "happened to exist is how a topic bucket disappears and the shares stop closing"
        )
    spurious = sorted(column for column in documented if column in present)
    if spurious:
        raise MatrixError(f"columns recorded as dropped but still in the matrix: {spurious}")


@dataclass(frozen=True)
class WeightedMatrix:
    """A weighted matrix with the provenance needed to interpret a centroid.

    ``column_factors`` is what lets a centroid be read back in feature space: the
    centroid lives in weighted coordinates, and dividing by the factor is the only
    way to say what a cluster's actual view count looks like.
    """

    matrix: pd.DataFrame
    block_weights: dict[str, float]
    column_blocks: dict[str, str]
    column_factors: dict[str, float]
    baseline_variances: dict[str, float]


def apply_block_weights(
    standardized: pd.DataFrame,
    membership: dict[str, list[str]],
    weights: dict[str, float],
) -> WeightedMatrix:
    """Scale each column by ``sqrt(w_block / n_block)``."""
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise MatrixError(
            f"block weights sum to {total}, not 1. Weights that do not sum to 1 are not "
            "shares of the model's distance, so the realised contributions cannot be "
            "compared against them"
        )
    if set(membership) != set(weights):
        raise MatrixError(
            f"block membership covers {sorted(membership)} and weights cover "
            f"{sorted(weights)}; a block in one and not the other is either unweighted or "
            "weighted and empty"
        )

    column_blocks: dict[str, str] = {}
    for block, columns in membership.items():
        for column in columns:
            if column in column_blocks:
                raise MatrixError(
                    f"column {column!r} is in both {column_blocks[column]!r} and {block!r}, "
                    "so it would be scaled twice and count double"
                )
            column_blocks[column] = block

    matrix_columns = list(standardized.columns)
    unassigned = [column for column in matrix_columns if column not in column_blocks]
    if unassigned:
        raise MatrixError(
            f"matrix columns assigned to no block: {unassigned}. An unweighted column keeps "
            "its full unit variance and silently outweighs every weighted one"
        )
    absent = [column for column in column_blocks if column not in matrix_columns]
    if absent:
        raise MatrixError(f"block membership references columns not in the matrix: {absent}")

    out = standardized.copy()
    factors: dict[str, float] = {}
    for block, columns in membership.items():
        factor = float(np.sqrt(weights[block] / len(columns)))
        for column in columns:
            out[column] = out[column] * factor
            factors[column] = factor
    return WeightedMatrix(
        matrix=out,
        block_weights=dict(weights),
        column_blocks=column_blocks,
        column_factors=factors,
        baseline_variances={},
    )


def build_weighted_matrix(
    standardized: pd.DataFrame,
    membership: dict[str, list[str]],
    weights: dict[str, float],
    *,
    no_recent_mask: pd.Series | None = None,
    fitting_masks: dict[str, pd.Series] | None = None,
) -> WeightedMatrix:
    """The whole assembly, with every assertion, in the order that makes them mean something."""
    assert_no_forbidden_model_columns(standardized.columns)

    frame = standardized
    masks = fitting_masks or {}
    if no_recent_mask is not None:
        keep = ~no_recent_mask.astype(bool)
        frame = frame.loc[keep]
        masks = {column: mask.loc[keep] for column, mask in masks.items()}

    assert_finite(frame)
    variances = assert_unit_variance(frame, fitting_masks=masks)
    weighted = apply_block_weights(frame, membership, weights)
    return WeightedMatrix(
        matrix=weighted.matrix,
        block_weights=weighted.block_weights,
        column_blocks=weighted.column_blocks,
        column_factors=weighted.column_factors,
        baseline_variances=variances,
    )


def realized_block_contributions(
    weighted: WeightedMatrix,
    *,
    row_mask: pd.Series | None = None,
) -> pd.Series:
    """What share of the model's distance each block actually accounts for.

    Reported against the nominal weights, and they should track. Where they do not,
    the weights are a statement of intent rather than a description -- most often
    because a conditional block is the imputed baseline for most rows, so its
    realised contribution is far below its nominal weight on the full population
    and near it on the active one. Worth reporting for both.
    """
    frame = weighted.matrix
    if row_mask is not None:
        frame = frame.loc[row_mask.astype(bool)]
    squared = frame.pow(2).mean()
    contributions = squared.groupby(weighted.column_blocks).sum()
    total = contributions.sum()
    if total == 0:
        return contributions
    return contributions / total
