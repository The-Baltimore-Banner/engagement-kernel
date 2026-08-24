"""The clustering surfaces: the small, frozen feature spaces the models are fit in.

A surface is a named, fixed-order list of standardised dimensions, with its
calibrations fit once on the baseline and applied verbatim every week afterwards.
That discipline is what makes a cluster id stable: re-standardising each week would
move the space under the centroids, so a reader whose behaviour never changed
would cross a boundary as the population around them shifted.

Two surfaces, and the relationship between them is a fallback, not a preference.

:data:`~engagement_kernel.engagement.config.SURFACE_INTENSITY`
    Six dimensions: engagement *magnitude* across the reader-event channels, plus
    resolved section views and overall active days, plus two breadth ride-alongs.
    No topic mix and no community features, so it is robust to a publisher who has
    neither.

:data:`~engagement_kernel.engagement.config.SURFACE_JOINT`
    The intensity surface, unchanged, plus community magnitude and email cadence.
    It *composes* the intensity space rather than re-deriving it, so the magnitude
    tiers are identical between the two and a deployment that gains a community
    feed does not have its existing tiers renumbered.

The joint surface is the richer one and it is selected only when the delivery
declares both optional inputs available -- see
:func:`~engagement_kernel.engagement.config.resolve_surface`. That is the contract's
promise about absent optional inputs honoured in code: absence names a different
feature set, it does not fill columns with zeros.

Why a small z-space surface rather than the block-weighted matrix
----------------------------------------------------------------

:mod:`engagement_kernel.engagement.matrix` builds a wider, block-weighted matrix
over every semantic feature, and it is a supported construction. It is not the
default, because in the investigation this port carries over, the wide
topic-inclusive matrix did not produce a surface that passed the stability screens
and the magnitude-only construction did. That is an empirical result on one
publisher's data and it may not transfer, so both constructions ship and the
default is the one with evidence behind it. An adopter who wants the wide matrix
runs the screens and sees.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engagement_kernel.engagement.calibration import FeatureCalibration, fit_calibration
from engagement_kernel.engagement.config import (
    SURFACE_INTENSITY,
    SURFACE_JOINT,
    LaneConfig,
)
from engagement_kernel.engagement.guards import assert_no_forbidden_model_columns

#: Breadth ride-alongs, shared by both surfaces.
#:
#: ``distinct_sections_28d`` is log-scaled -- it is a count with a long tail.
#: ``topic_entropy_28d`` is not: it is already bounded in ``[0, 1]`` by
#: construction, and logging a bounded share compresses the top of it for no
#: reason.
BREADTH_LOG_COLUMN = "distinct_sections_28d"
ENTROPY_COLUMN = "topic_entropy_28d"

#: Community magnitude, on the joint surface only.
COMMUNITY_LOG_COLUMNS: tuple[str, ...] = (
    "community_actions_28d",
    "community_active_days_28d",
)

#: The email cadence axis: source atomic, and the feature name it takes.
#:
#: The source is the *atomic* -- the count of weeks in four with any click -- and
#: not a surface feature name. Naming the surface column as the source is a real
#: defect that happened: it raised on every real frame, because the surface name
#: only ever exists inside the surface builder and never appears in a feature
#: frame, which broke both the freeze fit and the weekly apply.
#:
#: This axis is where the "click events or distinct campaigns clicked" decision
#: does *not* reach. It counts weeks with a non-zero bin, and any week containing
#: one click has a non-zero bin under either unit.
CADENCE_SOURCE_COLUMN = "email_click_active_weeks_4"
CADENCE_FEATURE_COLUMN = "email_cadence__active_weeks_4"


class SurfaceError(ValueError):
    """A surface could not be fit or applied against the frame supplied."""


def intensity_log_columns(config: LaneConfig) -> tuple[str, ...]:
    """The magnitude columns, derived from the channels the contract declares.

    Derived rather than written out: a channel added to the contract joins the
    magnitude space instead of being silently absent from it.
    """
    return (
        *(f"{channel}_views_28d" for channel in config.channels),
        "resolved_section_views_28d",
        "overall_active_days_28d",
    )


def intensity_feature_columns(config: LaneConfig) -> list[str]:
    columns = [f"z_log__{column}" for column in intensity_log_columns(config)]
    columns.append(f"z_log__{BREADTH_LOG_COLUMN}")
    columns.append(f"z__{ENTROPY_COLUMN}")
    return columns


def joint_feature_columns(config: LaneConfig) -> list[str]:
    columns = intensity_feature_columns(config)
    columns.extend(f"z_log__{column}" for column in COMMUNITY_LOG_COLUMNS)
    columns.append(CADENCE_FEATURE_COLUMN)
    return columns


def surface_feature_columns(config: LaneConfig) -> list[str]:
    if config.surface == SURFACE_JOINT:
        return joint_feature_columns(config)
    return intensity_feature_columns(config)


@dataclass(frozen=True)
class SurfaceSpace:
    """The frozen calibrations of one surface, and its column order."""

    name: str
    #: Column -> calibration of ``log1p(column)``.
    log_calibrations: dict[str, FeatureCalibration]
    #: Column -> calibration of the raw column, for the already-bounded ones.
    raw_calibrations: dict[str, FeatureCalibration]
    feature_columns: list[str]
    source_columns: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "log_calibrations": {k: v.to_dict() for k, v in self.log_calibrations.items()},
            "raw_calibrations": {k: v.to_dict() for k, v in self.raw_calibrations.items()},
            "feature_columns": list(self.feature_columns),
            "source_columns": list(self.source_columns),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> SurfaceSpace:
        return cls(
            name=str(payload["name"]),
            log_calibrations={
                k: FeatureCalibration.from_dict(v) for k, v in payload["log_calibrations"].items()
            },
            raw_calibrations={
                k: FeatureCalibration.from_dict(v) for k, v in payload["raw_calibrations"].items()
            },
            feature_columns=list(payload["feature_columns"]),
            source_columns=list(payload["source_columns"]),
        )


def _require(frame: pd.DataFrame, columns: tuple[str, ...] | list[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise SurfaceError(f"the {name} surface needs columns absent from the frame: {missing}")


def _surface_sources(config: LaneConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(log-scaled sources, raw sources)`` for the configured surface."""
    log_sources = (*intensity_log_columns(config), BREADTH_LOG_COLUMN)
    raw_sources = (ENTROPY_COLUMN,)
    if config.surface == SURFACE_JOINT:
        log_sources = (*log_sources, *COMMUNITY_LOG_COLUMNS)
        raw_sources = (*raw_sources, CADENCE_SOURCE_COLUMN)
    return log_sources, raw_sources


def fit_surface(
    content_active_panel: pd.DataFrame,
    config: LaneConfig,
    *,
    cadence_fit_mask: pd.Series | None = None,
) -> SurfaceSpace:
    """Fit the surface calibrations on the content-active panel.

    ``content_active_panel`` must already be restricted to the fitting population.

    ``cadence_fit_mask`` optionally narrows *only* the cadence calibration, for the
    case where email coverage begins later than the rest of the data. The cadence
    axis looks back four weeks, so the first trustworthy week end is the first one
    whose whole lookback sits inside the email feed's coverage -- flooring at the
    feed's first date instead re-admits four week-ends whose cadence is
    mechanically under-counted, which is the exact defect the floor exists to
    exclude. An all-False mask is refused rather than silently fitting on nothing.
    """
    log_sources, raw_sources = _surface_sources(config)
    _require(content_active_panel, (*log_sources, *raw_sources), config.surface)

    log_calibrations: dict[str, FeatureCalibration] = {}
    for column in log_sources:
        values = np.log1p(content_active_panel[column].fillna(0).to_numpy(dtype=float))
        log_calibrations[column] = fit_calibration(values)

    raw_calibrations: dict[str, FeatureCalibration] = {}
    for column in raw_sources:
        panel = content_active_panel
        if column == CADENCE_SOURCE_COLUMN and cadence_fit_mask is not None:
            if not bool(cadence_fit_mask.to_numpy().any()):
                raise SurfaceError(
                    "cadence_fit_mask selects no rows, so the cadence axis would be "
                    "calibrated on nothing and every reader would land at the same z"
                )
            panel = content_active_panel.loc[cadence_fit_mask]
        raw_calibrations[column] = fit_calibration(panel[column].fillna(0).to_numpy(dtype=float))

    return SurfaceSpace(
        name=config.surface,
        log_calibrations=log_calibrations,
        raw_calibrations=raw_calibrations,
        feature_columns=surface_feature_columns(config),
        source_columns=[*log_sources, *raw_sources],
    )


def build_surface_matrix(frame: pd.DataFrame, space: SurfaceSpace) -> pd.DataFrame:
    """Apply the frozen calibrations, producing the surface matrix.

    Works on any frame carrying the source columns -- the panel at fit time, a
    week's content-active rows at scoring time -- which is what keeps cluster ids
    meaning the same thing across weeks.

    The result is passed through the model guard. The block-weighted builder guards
    its own output too; guarding here as well is deliberate, because in the system
    this ports from the guard lived inside the block-weighted builder and the
    surface that was actually frozen and published never went through it.
    """
    _require(frame, space.source_columns, space.name)
    out = pd.DataFrame(index=frame.index)
    for column, calibration in space.log_calibrations.items():
        out[f"z_log__{column}"] = calibration.z(
            np.log1p(frame[column].fillna(0).to_numpy(dtype=float))
        )
    for column, calibration in space.raw_calibrations.items():
        target = CADENCE_FEATURE_COLUMN if column == CADENCE_SOURCE_COLUMN else f"z__{column}"
        out[target] = calibration.z(frame[column].fillna(0).to_numpy(dtype=float))

    missing = [column for column in space.feature_columns if column not in out.columns]
    if missing:
        raise SurfaceError(
            f"the {space.name} surface declares columns its calibrations did not produce: "
            f"{missing}. A surface silently short of a dimension is the defect that made "
            "bucket shares stop summing to 1 without any single number looking wrong"
        )
    out = out[space.feature_columns]
    assert_no_forbidden_model_columns(out.columns)
    values = out.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        bad = out.columns[~np.isfinite(values).all(axis=0)].tolist()
        raise SurfaceError(f"the {space.name} surface has non-finite values in {bad}")
    return out


def surface_variances(matrix: pd.DataFrame) -> dict[str, float]:
    """Per-column variance of a surface matrix, for the build report.

    Every column is a z-score against its own fitting population, so on that
    population each should be near 1. A column far from 1 on the population it was
    fit on means the fit and the apply saw different rows.
    """
    return {column: float(matrix[column].var(ddof=0)) for column in matrix.columns}


SURFACE_NAMES: tuple[str, ...] = (SURFACE_INTENSITY, SURFACE_JOINT)
