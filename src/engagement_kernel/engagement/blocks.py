"""Human-defined semantic blocks, with PCA allowed to weight *within* a block.

The division of labour here is the whole design. People decide what the concepts
are -- intensity, habit, consistency, depth -- and which atomics belong to each.
PCA is then allowed to choose the weights inside one narrow block, and only if it
earns the right to:

1. it must explain enough of the block's variance to be a summary rather than one
   of several directions;
2. it must correlate with the block's declared anchor, which is what fixes the
   *meaning* of the axis -- PC1 is sign-arbitrary, and without an anchor a run can
   silently publish a "more engaged" score that increases as engagement falls;
3. if either fails, the block falls back -- to the anchor alone, then to an
   equal-weight average, then to being dropped.

That fallback chain matters more than the PCA. A forced PC1 that failed its gates
is a direction through a cloud, and it will be given a block weight and a name
that says it means something. The chain lets a block degrade to something honest
instead.

Every threshold below is looser for a *sparse* block. On a signal where most
readers are at zero, the first component of the active minority genuinely explains
less of the total variance, and holding it to the dense bar would drop email and
community every time -- not because the summary is bad but because the population
is mostly absent from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engagement_kernel.engagement.calibration import FeatureCalibration, fit_calibration
from engagement_kernel.engagement.transforms import (
    WinsorizationParams,
    fit_winsorization,
    log_count,
)

#: Variance a block's PC1 must explain to be taken as the block's summary.
DENSE_MIN_EXPLAINED_VARIANCE = 0.50
SPARSE_MIN_EXPLAINED_VARIANCE = 0.35

#: Correlation PC1 must reach with the block's anchor, so the axis has a direction
#: somebody chose rather than one the arithmetic happened to produce.
DENSE_MIN_ANCHOR_CORR = 0.60
SPARSE_MIN_ANCHOR_CORR = 0.40

#: Correlation above which two features in the same block measure one thing.
REDUNDANCY_CORR_THRESHOLD = 0.8

TRANSFORM_RAW = "raw"
TRANSFORM_LOG_COUNT = "log_count"
TRANSFORM_ONE_MINUS = "one_minus"
TRANSFORM_NEGATE = "negate"

METHOD_PCA = "pca"
METHOD_ANCHOR = "anchor"
METHOD_EQUAL_WEIGHT = "equal_weight"
METHOD_DROPPED = "dropped"

FEATURE_CLASS_LEVEL = "level"
FEATURE_CLASS_CONDITIONAL = "conditional"


class BlockError(ValueError):
    """A semantic block could not be fit or applied as declared."""


@dataclass(frozen=True)
class InputSpec:
    """One atomic feeding a block, and how it is transformed on the way in.

    ``one_minus`` and ``negate`` exist so a metric whose natural direction is
    "higher = worse" can be oriented at the point it becomes a model input, while
    the published atomic keeps its readable meaning. Flipping the atomic instead
    would publish a ``top_week_share`` that is not a share of anything.
    """

    column: str
    transform: str = TRANSFORM_RAW


@dataclass(frozen=True)
class BlockSpec:
    """One human-defined semantic block."""

    name: str
    inputs: tuple[InputSpec, ...]
    #: The input whose direction fixes the block's meaning.
    anchor: str
    feature_class: str
    sparse: bool = False
    #: For a conditional block, the boolean column naming the rows it is defined on.
    active_mask_column: str | None = None

    def __post_init__(self) -> None:
        if not self.inputs:
            raise BlockError(f"block {self.name!r} has no inputs")
        if self.anchor not in {spec.column for spec in self.inputs}:
            raise BlockError(
                f"block {self.name!r} anchors on {self.anchor!r}, which is not one of its "
                "inputs. An anchor outside the block cannot orient it"
            )
        if self.feature_class == FEATURE_CLASS_CONDITIONAL and self.active_mask_column is None:
            raise BlockError(
                f"conditional block {self.name!r} declares no active-mask column, so it "
                "would be fit on rows it is undefined for"
            )

    @property
    def min_explained_variance(self) -> float:
        return SPARSE_MIN_EXPLAINED_VARIANCE if self.sparse else DENSE_MIN_EXPLAINED_VARIANCE

    @property
    def min_anchor_corr(self) -> float:
        return SPARSE_MIN_ANCHOR_CORR if self.sparse else DENSE_MIN_ANCHOR_CORR


@dataclass
class FittedBlock:
    """A fitted block: the PCA champion, or the fallback that replaced it."""

    spec: BlockSpec
    method: str
    winsorization: dict[str, WinsorizationParams]
    input_calibrations: dict[str, FeatureCalibration]
    loadings: np.ndarray | None
    explained_variance_ratio: float | None
    anchor_corr: float | None
    sign_flipped: bool
    score_calibration: FeatureCalibration | None
    gate_failures: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.spec.name,
            "method": self.method,
            "inputs": [spec.column for spec in self.spec.inputs],
            "anchor": self.spec.anchor,
            "loadings": None if self.loadings is None else self.loadings.tolist(),
            "explained_variance_ratio": self.explained_variance_ratio,
            "anchor_corr": self.anchor_corr,
            "sign_flipped": self.sign_flipped,
            "gate_failures": list(self.gate_failures),
        }


def _transform(
    values: pd.Series,
    spec: InputSpec,
    winsorization: dict[str, WinsorizationParams],
) -> np.ndarray:
    if spec.transform == TRANSFORM_LOG_COUNT:
        return log_count(values, winsorization[spec.column])
    if spec.transform == TRANSFORM_ONE_MINUS:
        return 1.0 - np.asarray(values, dtype=float)
    if spec.transform == TRANSFORM_NEGATE:
        return -np.asarray(values, dtype=float)
    if spec.transform == TRANSFORM_RAW:
        return np.asarray(values, dtype=float)
    raise BlockError(f"unknown input transform {spec.transform!r}")


def _standardised_inputs(
    frame: pd.DataFrame,
    spec: BlockSpec,
    winsorization: dict[str, WinsorizationParams],
    calibrations: dict[str, FeatureCalibration],
) -> np.ndarray:
    columns = []
    for input_spec in spec.inputs:
        transformed = _transform(frame[input_spec.column], input_spec, winsorization)
        columns.append(calibrations[input_spec.column].z(transformed))
    return np.column_stack(columns)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def fit_block(
    panel: pd.DataFrame,
    spec: BlockSpec,
    *,
    zero_share_threshold: float = 0.90,
) -> FittedBlock:
    """Fit one block on the panel -- on its active rows if it is conditional."""
    if spec.active_mask_column is not None:
        if spec.active_mask_column not in panel.columns:
            raise BlockError(
                f"block {spec.name!r} needs the mask column {spec.active_mask_column!r}"
            )
        fit_frame = panel.loc[panel[spec.active_mask_column].astype(bool)]
    else:
        fit_frame = panel
    if fit_frame.empty:
        raise BlockError(
            f"no rows to fit block {spec.name!r} on. A conditional block whose active "
            "population is empty cannot be fit, and imputing every row to the baseline "
            "would publish a column that is constant zero"
        )

    winsorization: dict[str, WinsorizationParams] = {}
    calibrations: dict[str, FeatureCalibration] = {}
    for input_spec in spec.inputs:
        if input_spec.column not in fit_frame.columns:
            raise BlockError(f"block {spec.name!r} needs absent column {input_spec.column!r}")
        if input_spec.transform == TRANSFORM_LOG_COUNT:
            winsorization[input_spec.column] = fit_winsorization(
                fit_frame[input_spec.column], zero_share_threshold=zero_share_threshold
            )
        transformed = _transform(fit_frame[input_spec.column], input_spec, winsorization)
        calibrations[input_spec.column] = fit_calibration(transformed)

    matrix = _standardised_inputs(fit_frame, spec, winsorization, calibrations)
    anchor_values = np.asarray(fit_frame[spec.anchor], dtype=float)

    gate_failures: list[str] = []
    loadings: np.ndarray | None = None
    explained: float | None = None
    anchor_corr: float | None = None
    sign_flipped = False
    scores: np.ndarray | None = None

    if np.allclose(matrix.std(axis=0), 0.0):
        gate_failures.append("degenerate_inputs")
    else:
        from sklearn.decomposition import PCA

        pca = PCA(n_components=1)
        scores = pca.fit_transform(matrix)[:, 0]
        loadings = pca.components_[0]
        explained = float(pca.explained_variance_ratio_[0])
        anchor_corr = _safe_corr(scores, anchor_values)

        # PC1's sign is arbitrary. Orienting it to the anchor is what makes "higher
        # is more engaged" true rather than true half the time.
        if anchor_corr is not None and anchor_corr < 0:
            loadings = -loadings
            scores = -scores
            anchor_corr = -anchor_corr
            sign_flipped = True

        if explained < spec.min_explained_variance:
            gate_failures.append(
                f"explained_variance {explained:.3f} < {spec.min_explained_variance}"
            )
        if anchor_corr is None or anchor_corr < spec.min_anchor_corr:
            gate_failures.append(f"anchor_corr {anchor_corr} < {spec.min_anchor_corr}")

    if not gate_failures:
        method = METHOD_PCA
        score_values = scores
    else:
        anchor_input = next(
            (spec_in for spec_in in spec.inputs if spec_in.column == spec.anchor), None
        )
        anchor_z = None
        if anchor_input is not None:
            anchor_z = calibrations[spec.anchor].z(
                _transform(fit_frame[spec.anchor], anchor_input, winsorization)
            )
        if anchor_z is not None and float(np.std(anchor_z)) > 0:
            method = METHOD_ANCHOR
            loadings = None
            score_values = anchor_z
        elif not np.allclose(matrix.std(axis=0), 0.0):
            method = METHOD_EQUAL_WEIGHT
            loadings = np.full(len(spec.inputs), 1.0 / len(spec.inputs))
            score_values = matrix.mean(axis=1)
        else:
            return FittedBlock(
                spec=spec,
                method=METHOD_DROPPED,
                winsorization=winsorization,
                input_calibrations=calibrations,
                loadings=None,
                explained_variance_ratio=explained,
                anchor_corr=anchor_corr,
                sign_flipped=False,
                score_calibration=None,
                gate_failures=gate_failures,
            )

    return FittedBlock(
        spec=spec,
        method=method,
        winsorization=winsorization,
        input_calibrations=calibrations,
        loadings=loadings,
        explained_variance_ratio=explained,
        anchor_corr=anchor_corr,
        sign_flipped=sign_flipped,
        score_calibration=fit_calibration(score_values),
        gate_failures=gate_failures,
    )


def apply_block(model: FittedBlock, frame: pd.DataFrame) -> pd.Series:
    """Score any population with a panel-fit block, returning a panel z-score."""
    if model.method == METHOD_DROPPED:
        raise BlockError(
            f"block {model.spec.name!r} was dropped at fit time and has no score. It is "
            "absent from the matrix by decision; applying it would invent one"
        )
    matrix = _standardised_inputs(frame, model.spec, model.winsorization, model.input_calibrations)
    if model.method == METHOD_PCA:
        raw = matrix @ model.loadings
    elif model.method == METHOD_ANCHOR:
        anchor_index = [spec.column for spec in model.spec.inputs].index(model.spec.anchor)
        raw = matrix[:, anchor_index]
    elif model.method == METHOD_EQUAL_WEIGHT:
        raw = matrix.mean(axis=1)
    else:  # pragma: no cover - the methods above are exhaustive
        raise BlockError(f"unknown block method {model.method!r}")
    assert model.score_calibration is not None
    return pd.Series(model.score_calibration.z(raw), index=frame.index, name=model.spec.name)


def redundancy_check(
    left: pd.Series,
    right: pd.Series,
    active_mask: pd.Series,
) -> tuple[float, bool]:
    """Correlation between two block scores among the rows both are defined on.

    Returns ``(corr, drop_left)``. Two blocks correlated above the threshold are
    measuring one thing, and keeping both hands that thing two block weights.
    Measured on active rows only: including the inactive rows, where both scores
    are the imputed baseline, correlates the imputation rather than the features.
    """
    mask = active_mask.astype(bool)
    corr = _safe_corr(
        left.loc[mask].to_numpy(dtype=float),
        right.loc[mask].to_numpy(dtype=float),
    )
    if corr is None:
        return 0.0, False
    return corr, abs(corr) > REDUNDANCY_CORR_THRESHOLD


# --- the default block definitions ------------------------------------------


def consumption_blocks(prefix: str) -> list[BlockSpec]:
    """The four blocks every reader-event channel gets.

    Identical in shape per channel on purpose: a channel-specific block structure
    would make the per-channel scores mean different things, and the cross-channel
    mix compares them.
    """
    active = f"{prefix}_active"
    return [
        BlockSpec(
            name=f"{prefix}_intensity",
            inputs=(
                InputSpec(f"{prefix}_views_7d", TRANSFORM_LOG_COUNT),
                InputSpec(f"{prefix}_views_28d", TRANSFORM_LOG_COUNT),
                InputSpec(f"{prefix}_sessions_7d", TRANSFORM_LOG_COUNT),
                InputSpec(f"{prefix}_sessions_28d", TRANSFORM_LOG_COUNT),
            ),
            anchor=f"{prefix}_views_28d",
            feature_class=FEATURE_CLASS_LEVEL,
        ),
        BlockSpec(
            name=f"{prefix}_habit",
            inputs=(
                InputSpec(f"{prefix}_active_days_7d"),
                InputSpec(f"{prefix}_active_days_28d"),
                InputSpec(f"{prefix}_active_weeks_4"),
            ),
            anchor=f"{prefix}_active_days_28d",
            feature_class=FEATURE_CLASS_LEVEL,
        ),
        BlockSpec(
            name=f"{prefix}_consistency",
            inputs=(
                InputSpec(f"{prefix}_active_weeks_4"),
                # Both oriented so higher means steadier, which is the direction the
                # block is named for.
                InputSpec(f"{prefix}_top_week_share_4", TRANSFORM_ONE_MINUS),
                InputSpec(f"{prefix}_weekly_cv_4", TRANSFORM_NEGATE),
            ),
            anchor=f"{prefix}_active_weeks_4",
            feature_class=FEATURE_CLASS_CONDITIONAL,
            active_mask_column=active,
        ),
        BlockSpec(
            name=f"{prefix}_depth",
            inputs=(
                InputSpec(f"{prefix}_time_per_view_28d"),
                InputSpec(f"{prefix}_articles_per_session_28d"),
            ),
            anchor=f"{prefix}_time_per_view_28d",
            feature_class=FEATURE_CLASS_CONDITIONAL,
            active_mask_column=active,
        ),
    ]


def email_blocks() -> list[BlockSpec]:
    """Email intensity and habit. Sparse: most readers click nothing in a month."""
    return [
        BlockSpec(
            name="email_click_intensity",
            inputs=(
                InputSpec("email_clicks_7d", TRANSFORM_LOG_COUNT),
                InputSpec("email_clicks_28d", TRANSFORM_LOG_COUNT),
            ),
            anchor="email_clicks_28d",
            feature_class=FEATURE_CLASS_LEVEL,
            sparse=True,
        ),
        BlockSpec(
            name="email_click_habit",
            inputs=(
                InputSpec("email_click_days_7d"),
                InputSpec("email_click_days_28d"),
                InputSpec("email_click_active_weeks_4"),
            ),
            anchor="email_click_days_28d",
            feature_class=FEATURE_CLASS_LEVEL,
            sparse=True,
        ),
    ]


def community_blocks() -> list[BlockSpec]:
    """Community intensity, habit and the contribution/reaction split."""
    return [
        BlockSpec(
            name="community_intensity",
            inputs=(
                InputSpec("community_actions_7d", TRANSFORM_LOG_COUNT),
                InputSpec("community_actions_28d", TRANSFORM_LOG_COUNT),
            ),
            anchor="community_actions_28d",
            feature_class=FEATURE_CLASS_LEVEL,
            sparse=True,
        ),
        BlockSpec(
            name="community_habit",
            inputs=(
                InputSpec("community_active_days_7d"),
                InputSpec("community_active_days_28d"),
                InputSpec("community_active_weeks_4"),
            ),
            anchor="community_active_days_28d",
            feature_class=FEATURE_CLASS_LEVEL,
            sparse=True,
        ),
        BlockSpec(
            name="community_contribution_depth",
            inputs=(
                InputSpec("community_contribution_actions_28d", TRANSFORM_LOG_COUNT),
                InputSpec("community_contribution_ratio_28d"),
                InputSpec("community_contribution_per_active_day_28d"),
            ),
            anchor="community_contribution_actions_28d",
            feature_class=FEATURE_CLASS_CONDITIONAL,
            sparse=True,
            active_mask_column="community_active",
        ),
    ]
