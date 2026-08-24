"""Topic atomics and the conditional topic feature block.

Reading is attributed to sections by the intermediate build, fractionally: a view
of content filed in three sections contributes a third to each, so a reader's
section views sum exactly to their views. This module rolls those sections up into
the buckets the deployment declared and turns the result into a mix.

**The topic block came with this lane, not with the lane that was deferred.** The
content-persona lane was dropped from this port, and it would have been easy to
read that as dropping topic features -- it is not. The engagement model's feature
space contains a topic block, the weekly feature assembly requires a bucket map as
an argument, and removing the persona lane removed the *owner* of the bucket map
without removing the dependency on it. So the map, this module and the
section-day intermediate all live here.

Three rules govern the arithmetic, and each one is the answer to a specific way of
getting it quietly wrong.

**Unresolved reading is excluded from the mix and counted separately.** Content
whose section metadata did not resolve is reading that happened; the reader read
something. Folding it into a bucket -- even the catch-all -- publishes a metadata
outage as a topic preference. Dropping it silently makes a reader with poor
metadata look like a reader who read nothing. So it is excluded from the
denominator and carried as ``unresolved_views_28d``, and the ratio of the two is
the coverage measure the topic gate reads.

**Shares close to exactly 1 for every reader with resolved reading.** Asserted,
not assumed (:func:`assert_share_closure`). Shares that do not close mean a bucket
went missing between the map and the matrix, and the symptom is a topic block that
quietly under-weights whatever fell out.

**The block is conditional, and it is fit on the readers it is defined for.**
Standardisation is fit on the content-active subset and everybody else is
neutral-imputed to the baseline. Fitting on everybody would put most of the mass
at "no mix at all" and compress the readers who have one into a narrow band near
the top.

Topic features use the 28-day window only. There are no 7-day topic features and
no topic momentum: a week is not enough reading for a mix to mean anything, and a
mix that swings weekly is measuring the news cycle rather than the reader.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from engagement_kernel.engagement.buckets import SectionBucketMap
from engagement_kernel.engagement.calibration import FeatureCalibration, fit_calibration
from engagement_kernel.engagement.imputation import neutral_impute
from engagement_kernel.engagement.windows import WeekGrid, window_mask
from engagement_kernel.intermediate.config import DEFAULT_UNRESOLVED_SECTION
from engagement_kernel.intermediate.tables import LOCAL_DATE_COLUMN

READER_KEY = "reader_id"
SECTION_COLUMN = "section"

#: Tolerance on the share-closure assertion. Tight: the shares are one division
#: of exact sums, so anything above floating-point noise means a bucket is missing
#: rather than that the arithmetic drifted.
SHARE_CLOSURE_TOLERANCE = 1e-9

#: Name of the breadth feature: how many distinct sections a reader touched.
TOPIC_BREADTH = "topic_breadth"


class TopicError(ValueError):
    """The topic layer was handed something it cannot turn into a mix."""


def build_topic_atomics(
    section_day: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
    bucket_map: SectionBucketMap,
    *,
    weight_column: str = "section_views",
) -> pd.DataFrame:
    """Per-reader topic atomics over the trailing window.

    ``weight_column`` defaults to fractional views. Weighting by
    ``section_time_seconds`` instead is a different, defensible model of taste --
    time says how much of the reader's attention a section held rather than how
    many pieces they opened -- and it is a model-version change, not a toggle,
    because it moves every share.
    """
    required = {READER_KEY, SECTION_COLUMN, LOCAL_DATE_COLUMN, weight_column}
    missing = sorted(required - set(section_day.columns))
    if missing:
        raise TopicError(f"reader_section_day is missing columns the topic layer needs: {missing}")

    in_window = section_day.loc[
        window_mask(section_day, grid.trailing_window(week_end), LOCAL_DATE_COLUMN)
    ].copy()
    in_window = in_window.loc[in_window[READER_KEY].notna()]

    resolved = in_window.loc[in_window[SECTION_COLUMN] != DEFAULT_UNRESOLVED_SECTION]
    unresolved = in_window.loc[in_window[SECTION_COLUMN] == DEFAULT_UNRESOLVED_SECTION]

    readers = pd.Index(in_window[READER_KEY].unique(), name=READER_KEY)
    out = pd.DataFrame(index=readers)
    out["resolved_section_views_28d"] = resolved.groupby(READER_KEY)[weight_column].sum()
    out["unresolved_views_28d"] = unresolved.groupby(READER_KEY)[weight_column].sum()
    out["distinct_sections_28d"] = resolved.groupby(READER_KEY)[SECTION_COLUMN].nunique()
    out = out.fillna(0.0)

    bucketed = resolved.assign(
        _bucket=[bucket_map.bucket_for(section) for section in resolved[SECTION_COLUMN]]
    )
    bucket_views = (
        bucketed.groupby([READER_KEY, "_bucket"])[weight_column]
        .sum()
        .unstack(fill_value=0.0)
        .reindex(index=readers, columns=list(bucket_map.bucket_names), fill_value=0.0)
        .fillna(0.0)
    )

    # A reader with no resolved reading gets shares of exactly zero, not shares of
    # 1/n. Zero says "no mix"; an even split says "equally interested in
    # everything", and those cluster in opposite directions.
    has_resolved = out["resolved_section_views_28d"] > 0
    denominator = out["resolved_section_views_28d"].where(has_resolved, 1.0)
    shares = bucket_views.div(denominator, axis=0).where(has_resolved, 0.0)

    for bucket in bucket_map.bucket_names:
        out[f"bucket_views_28d__{bucket}"] = bucket_views[bucket]
        out[f"topic_share_28d__{bucket}"] = shares[bucket]

    share_columns = [f"topic_share_28d__{bucket}" for bucket in bucket_map.bucket_names]
    # Both of these are profile-only and the model guard refuses them by name.
    # They are here because they are what a person reads to understand a cluster.
    out["top_bucket_share_28d"] = out[share_columns].max(axis=1)
    out["topic_entropy_28d"] = _normalised_entropy(out[share_columns].to_numpy(dtype=float))
    return out.reset_index()


def _normalised_entropy(shares: np.ndarray) -> np.ndarray:
    """Entropy of the bucket shares, scaled so an even mix is 1 and a single bucket 0.

    Normalising by ``log(n_buckets)`` is what makes the number comparable between
    deployments with different taxonomies. Unnormalised entropy rises with the
    bucket count, so a newsroom with twenty buckets would look uniformly broader
    than one with six.
    """
    n_buckets = shares.shape[1]
    if n_buckets < 2:  # pragma: no cover - the bucket map refuses a single bucket
        raise TopicError("entropy needs at least two buckets to be normalisable")
    with np.errstate(invalid="ignore", divide="ignore"):
        terms = np.where(shares > 0, shares * np.log(shares), 0.0)
    return -terms.sum(axis=1) / np.log(n_buckets)


def resolved_view_share(topic_atomics: pd.DataFrame) -> float:
    """Share of windowed reading whose section metadata resolved.

    The coverage measure the topic gate reads. Computed over reading, not over
    readers: a hundred readers with one unresolved view each is a different
    problem from one reader with a hundred.
    """
    resolved = float(topic_atomics["resolved_section_views_28d"].sum())
    unresolved = float(topic_atomics["unresolved_views_28d"].sum())
    total = resolved + unresolved
    if total <= 0:
        return 0.0
    return resolved / total


def section_view_shares(
    section_day: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
    *,
    weight_column: str = "section_views",
) -> dict[str, float]:
    """Share of resolved reading per section, for the bucket-map completeness check.

    The unresolved sentinel is excluded here rather than by the caller, so a
    completeness check can never be run against a denominator that includes it.
    """
    in_window = section_day.loc[
        window_mask(section_day, grid.trailing_window(week_end), LOCAL_DATE_COLUMN)
    ]
    resolved = in_window.loc[in_window[SECTION_COLUMN] != DEFAULT_UNRESOLVED_SECTION]
    totals = resolved.groupby(SECTION_COLUMN)[weight_column].sum()
    total = float(totals.sum())
    if total <= 0:
        return {}
    return {str(section): float(value) / total for section, value in totals.items()}


def assert_share_closure(
    topic_atomics: pd.DataFrame,
    content_active: pd.Series,
    bucket_map: SectionBucketMap,
) -> None:
    """Bucket shares must sum to 1 for every content-active reader."""
    share_columns = [f"topic_share_28d__{bucket}" for bucket in bucket_map.bucket_names]
    missing = [column for column in share_columns if column not in topic_atomics.columns]
    if missing:
        raise TopicError(
            f"topic atomics are missing bucket share columns {missing}; the mix cannot "
            "close over buckets that are not there"
        )
    active = topic_atomics.loc[content_active.astype(bool)]
    if active.empty:
        return
    totals = active[share_columns].sum(axis=1)
    offenders = (totals - 1.0).abs() > SHARE_CLOSURE_TOLERANCE
    if bool(offenders.any()):
        worst = float((totals - 1.0).abs().max())
        raise TopicError(
            f"{int(offenders.sum())} content-active readers have bucket shares that do not "
            f"sum to 1 (worst deviation {worst:.3e}). A bucket has gone missing between "
            "the map and the matrix, and the topic block is under-weighting whatever it was"
        )


# --- the model block --------------------------------------------------------


def topic_block_columns(bucket_map: SectionBucketMap) -> list[str]:
    """The topic block's model columns: one standardised share per bucket, plus breadth."""
    return [*bucket_map.topic_share_columns(), TOPIC_BREADTH]


def degenerate_topic_columns(
    panel_atomics: pd.DataFrame,
    content_active: pd.Series,
    bucket_map: SectionBucketMap,
) -> tuple[str, ...]:
    """Topic columns that are constant on the fitting population.

    The common case is the catch-all: a deployment that has mapped its whole taxonomy
    leaves nothing to fall into it, so ``topic_share_<catch_all>`` is zero for every
    reader. That is a *correct* map, not a broken one -- but the column carries no
    information, cannot influence an assignment, and would still occupy a share of the
    block's weight. It is also exactly the shape the unit-variance assertion refuses,
    several layers downstream, with a message about standardisation populations rather
    than about the taxonomy.

    So it is named here and dropped as a recorded decision, the same way a redundant
    consistency block is. An adopter whose taxonomy has no remainder should not have to
    debug a variance assertion to find that out.
    """
    subset = panel_atomics.loc[content_active.astype(bool)]
    degenerate: list[str] = []
    for bucket in bucket_map.bucket_names:
        source = f"topic_share_28d__{bucket}"
        if source in subset.columns and float(subset[source].std(ddof=0)) == 0.0:
            degenerate.append(f"topic_share_{bucket}")
    if float(subset["distinct_sections_28d"].std(ddof=0)) == 0.0:
        degenerate.append(TOPIC_BREADTH)
    return tuple(degenerate)


def fit_topic_block(
    panel_atomics: pd.DataFrame,
    content_active: pd.Series,
    bucket_map: SectionBucketMap,
) -> dict[str, FeatureCalibration]:
    """Fit the topic block's calibrations on the content-active panel subset."""
    subset = panel_atomics.loc[content_active.astype(bool)]
    if subset.empty:
        raise TopicError(
            "no content-active panel rows to fit the topic block on. Either the "
            "content-active floor is above what this delivery supports or section "
            "metadata is not resolving; fitting on everybody instead would learn the "
            "shape of the missing data"
        )
    calibrations: dict[str, FeatureCalibration] = {}
    for bucket in bucket_map.bucket_names:
        calibrations[f"topic_share_{bucket}"] = fit_calibration(
            subset[f"topic_share_28d__{bucket}"]
        )
    calibrations[TOPIC_BREADTH] = fit_calibration(np.log1p(subset["distinct_sections_28d"]))
    return calibrations


def apply_topic_block(
    topic_atomics: pd.DataFrame,
    content_active: pd.Series,
    calibrations: dict[str, FeatureCalibration],
    bucket_map: SectionBucketMap,
    *,
    z_clip: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The standardised topic block plus its imputation flags.

    Content-inactive readers come out at exactly z = 0 on every topic dimension,
    with the flag set. Entropy and the top-bucket share are excluded by
    construction: both are deterministic functions of the shares already here, so
    they would double-count the block.
    """
    out = pd.DataFrame(index=topic_atomics.index)
    for bucket in bucket_map.bucket_names:
        name = f"topic_share_{bucket}"
        out[name] = calibrations[name].z_clipped(
            topic_atomics[f"topic_share_28d__{bucket}"], clip=z_clip
        )
    out[TOPIC_BREADTH] = calibrations[TOPIC_BREADTH].z_clipped(
        np.log1p(topic_atomics["distinct_sections_28d"]), clip=z_clip
    )
    inactive = ~content_active.astype(bool)
    return neutral_impute(out, inactive, list(out.columns))
