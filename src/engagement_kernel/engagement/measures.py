"""The single-number engagement measures, and why there are three of them.

A cluster label says which group a reader is in. It does not say whether one group
is more engaged than another, and it should not -- cluster ids are unordered
archetypes. But "how engaged is this reader" is the question people actually ask,
so the lane publishes a scalar as well, and it publishes three constructions of it
rather than one:

``m1``
    The first principal component of the standardised signals. One number, all the
    signals, weights learned from the data. Its weakness is that it is dominated by
    whichever block has the most correlated columns in it.

``m2``
    A weighted average of per-block means, where each block's weight is the share of
    that block's variance the clusters explain. It asks "which signals actually
    distinguish the groups" and weights accordingly.

``m3``
    A per-block score, each re-standardised to a common scale, then equally
    weighted across blocks. Deliberately *not* variance-weighted: it says every
    behavioural dimension counts the same, which is the right construction when a
    newsroom wants community participation to matter as much as volume even though
    far fewer readers do it.

Publishing all three is a choice about honesty rather than indecision. They agree
on the extremes and disagree in the middle, and where they disagree the reason is
interpretable -- a heavy commenter who reads little ranks far higher on ``m3`` than
on ``m1``. One number would hide that behind a false precision. The four ``m3``
sub-scores are persisted for the same reason: the composite can be read as its
parts.

Everything here is **fit once** and thereafter only applied. Percentiles are the
exception, and deliberately: they are computed within the scored week's population,
because "top 10% of engaged readers" means top 10% of the people who are here now.
A percentile against a frozen reference distribution drifts as the audience grows
and eventually describes a population that no longer exists.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

MEASURES_PARAMS_VERSION = "1"

#: Block names, in a fixed order.
BLOCK_INTENSITY = "intensity"
BLOCK_BREADTH = "breadth"
BLOCK_COMMUNITY = "community"
BLOCK_LOYALTY = "loyalty"

#: Signal -> the feature-frame atomic it is read from.
#:
#: The cadence signal reads the *source atomic*, not the surface feature name. The
#: surface name only ever exists inside the surface builder, so naming it here
#: raises on every real frame -- which is a defect that shipped once and broke both
#: the freeze fit and the weekly apply at the same time.
SIGNAL_SOURCES: dict[str, str] = {
    "resolved_section_views": "resolved_section_views_28d",
    "overall_active_days": "overall_active_days_28d",
    "distinct_sections": "distinct_sections_28d",
    "topic_entropy": "topic_entropy_28d",
    "community_actions": "community_actions_28d",
    "community_active_days": "community_active_days_28d",
    "email_cadence": "email_click_active_weeks_4",
}


class MeasuresError(ValueError):
    """The measures could not be fit or applied against what was supplied."""


@dataclass(frozen=True)
class MeasuresLayout:
    """Which signals exist for this delivery, and how they group into blocks.

    Derived from the channels and the available optional inputs rather than fixed,
    because a delivery with no community feed has no community block -- and a block
    of zeros would drag every reader's ``m3`` toward the middle by a quarter.
    """

    signals: tuple[str, ...]
    blocks: OrderedDict[str, tuple[str, ...]]
    sources: dict[str, str]

    def source_columns(self) -> list[str]:
        return [self.sources[signal] for signal in self.signals]


def build_layout(
    channels: tuple[str, ...],
    *,
    has_email: bool,
    has_community: bool,
) -> MeasuresLayout:
    """Resolve the signal set and block structure for one delivery."""
    intensity = [f"{channel}_views" for channel in channels]
    intensity.extend(["resolved_section_views", "overall_active_days"])
    sources = {f"{channel}_views": f"{channel}_views_28d" for channel in channels}
    sources.update(SIGNAL_SOURCES)

    blocks: OrderedDict[str, tuple[str, ...]] = OrderedDict()
    blocks[BLOCK_INTENSITY] = tuple(intensity)
    blocks[BLOCK_BREADTH] = ("distinct_sections", "topic_entropy")
    if has_community:
        blocks[BLOCK_COMMUNITY] = ("community_actions", "community_active_days")
    if has_email:
        blocks[BLOCK_LOYALTY] = ("email_cadence",)

    signals = tuple(signal for block in blocks.values() for signal in block)
    return MeasuresLayout(
        signals=signals,
        blocks=blocks,
        sources={signal: sources[signal] for signal in signals},
    )


def build_signal_matrix(frame: pd.DataFrame, layout: MeasuresLayout) -> pd.DataFrame:
    """The signal matrix from a feature frame's raw atomics, in canonical order.

    The single source for both the fit and the apply, so the two cannot disagree
    about which column feeds which signal.
    """
    missing = [column for column in layout.source_columns() if column not in frame.columns]
    if missing:
        raise MeasuresError(f"the feature frame is missing measure-signal atomics: {missing}")
    renamed = {layout.sources[signal]: signal for signal in layout.signals}
    return frame[layout.source_columns()].rename(columns=renamed)[list(layout.signals)]


@dataclass
class BlockParams:
    """Frozen parameters for one block's oriented first component."""

    features: list[str]
    loadings: dict[str, float]
    sign: float
    variance_explained: float
    score_center: float
    score_scale: float

    def to_dict(self) -> dict[str, object]:
        return {
            "features": list(self.features),
            "loadings": {k: float(v) for k, v in self.loadings.items()},
            "sign": float(self.sign),
            "variance_explained": float(self.variance_explained),
            "score_center": float(self.score_center),
            "score_scale": float(self.score_scale),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> BlockParams:
        return cls(
            features=list(payload["features"]),
            loadings={k: float(v) for k, v in payload["loadings"].items()},
            sign=float(payload["sign"]),
            variance_explained=float(payload["variance_explained"]),
            score_center=float(payload["score_center"]),
            score_scale=float(payload["score_scale"]),
        )


@dataclass
class MeasuresParams:
    """The complete frozen parameter set. Fit once; weekly runs only apply it."""

    model_version: str
    signals: list[str]
    blocks: OrderedDict[str, list[str]]
    sources: dict[str, str]
    signal_center: dict[str, float]
    signal_scale: dict[str, float]
    m1_loadings: dict[str, float]
    m1_sign: float
    m1_variance_explained: float
    #: Share of each signal's variance the frozen clusters explain. Informational
    #: on its own; the block weights below are built from it.
    eta_sq: dict[str, float]
    block_weights: dict[str, float]
    m3_blocks: OrderedDict[str, BlockParams]
    measures_params_version: str = MEASURES_PARAMS_VERSION

    def validate(self) -> None:
        if list(self.blocks) != list(self.m3_blocks):
            raise MeasuresError("blocks and m3_blocks must cover the same blocks")
        flattened = [signal for block in self.blocks.values() for signal in block]
        if sorted(flattened) != sorted(self.signals):
            raise MeasuresError(
                "the blocks do not partition the signals; a signal in no block is dropped "
                "from m2 and m3 while still counting in m1"
            )
        total = sum(self.block_weights.values())
        if total and abs(total - 1.0) > 1e-6:
            raise MeasuresError("m2 block weights must sum to 1")
        for block, features in self.blocks.items():
            if set(self.m3_blocks[block].loadings) != set(features):
                raise MeasuresError(f"block {block!r} loadings must cover its features {features}")

    def to_dict(self) -> dict[str, object]:
        return {
            "measures_params_version": self.measures_params_version,
            "model_version": self.model_version,
            "signals": list(self.signals),
            #: Persisted as a list, and read back from it.
            #:
            #: The blocks themselves are a JSON object, and this file is written with
            #: sorted keys so two freezes of the same model are byte-comparable -- which
            #: means the object's key order does not survive the round trip. Block order
            #: decides the column order of the sub-scores, so an alphabetised reload
            #: would hand back parameters that are not the ones that were frozen.
            "block_order": list(self.blocks),
            "blocks": {block: list(features) for block, features in self.blocks.items()},
            "sources": dict(self.sources),
            "signal_center": {k: float(v) for k, v in self.signal_center.items()},
            "signal_scale": {k: float(v) for k, v in self.signal_scale.items()},
            "m1": {
                "loadings": {k: float(v) for k, v in self.m1_loadings.items()},
                "sign": float(self.m1_sign),
                "variance_explained": float(self.m1_variance_explained),
            },
            "eta_sq": {k: float(v) for k, v in self.eta_sq.items()},
            "block_weights": {k: float(v) for k, v in self.block_weights.items()},
            "m3_blocks": {block: params.to_dict() for block, params in self.m3_blocks.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict) -> MeasuresParams:
        raw_blocks = {block: list(features) for block, features in payload["blocks"].items()}
        order = list(payload.get("block_order", raw_blocks))
        if sorted(order) != sorted(raw_blocks):
            raise MeasuresError(
                f"block_order {order} does not cover the blocks {sorted(raw_blocks)}"
            )
        params = cls(
            model_version=str(payload["model_version"]),
            signals=list(payload["signals"]),
            blocks=OrderedDict((block, raw_blocks[block]) for block in order),
            sources={k: str(v) for k, v in payload["sources"].items()},
            signal_center={k: float(v) for k, v in payload["signal_center"].items()},
            signal_scale={k: float(v) for k, v in payload["signal_scale"].items()},
            m1_loadings={k: float(v) for k, v in payload["m1"]["loadings"].items()},
            m1_sign=float(payload["m1"]["sign"]),
            m1_variance_explained=float(payload["m1"]["variance_explained"]),
            eta_sq={k: float(v) for k, v in payload["eta_sq"].items()},
            block_weights={k: float(v) for k, v in payload["block_weights"].items()},
            m3_blocks=OrderedDict(
                (block, BlockParams.from_dict(payload["m3_blocks"][block])) for block in order
            ),
            measures_params_version=str(
                payload.get("measures_params_version", MEASURES_PARAMS_VERSION)
            ),
        )
        params.validate()
        return params

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> MeasuresParams:
        return cls.from_dict(json.loads(Path(path).read_text()))

    def layout(self) -> MeasuresLayout:
        return MeasuresLayout(
            signals=tuple(self.signals),
            blocks=OrderedDict((b, tuple(f)) for b, f in self.blocks.items()),
            sources=dict(self.sources),
        )


def _zscore(matrix: np.ndarray) -> np.ndarray:
    """Column-standardise, passing a zero-variance column through as centred zeros."""
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0, ddof=0)
    scale = np.where(scale == 0.0, 1.0, scale)
    return (matrix - mean) / scale


def oriented_pc1(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Sign-oriented first principal component of a block.

    Returns ``(scores, oriented_loadings, variance_explained, applied_sign)``.
    Orientation is by the sign of the loading sum, so a higher score means more
    activity. Without it the sign is whatever the decomposition returned, and half
    of all refits would publish an engagement score that falls as engagement rises.
    """
    centered = _zscore(matrix)
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    load = vt[0]
    sign = 1.0 if load.sum() >= 0 else -1.0
    oriented = load * sign
    variance_explained = float((singular**2 / (singular**2).sum())[0])
    return centered @ oriented, oriented, variance_explained, sign


#: Decimal places the scores are snapped to before ranking.
#:
#: Not a cosmetic round, and deleting it re-opens a real failure. Average-rank ties
#: make the percentile a step function of its input: on a population of a few
#: thousand, one rank step is a couple of hundredths of a percentile, and a large
#: share of the population sits in exact tie groups because their inputs are
#: genuinely equal. Without the snap, a last-bit difference in a score -- a
#: different CPU, a different linear-algebra kernel, a different numpy build --
#: splits a tie group and moves published percentiles. A 1e-16 input difference
#: producing a 1e-2 output difference has broken continuous integration on
#: byte-identical code.
#:
#: The grid is absolute rather than significant-digit because the noise it absorbs
#: is absolute: the last bit of a dot product over a handful of O(1) terms. A
#: relative grid would stop suppressing ties exactly where the scores are near
#: zero, which is where the tie groups are densest.
PERCENTILE_QUANTUM_DECIMALS = 10


def within_week_percentile(values: np.ndarray | pd.Series) -> np.ndarray:
    """Rank percentile in ``[0, 100]`` over the population supplied."""
    quantized = np.round(np.asarray(values, dtype=float), PERCENTILE_QUANTUM_DECIMALS)
    return pd.Series(quantized).rank(pct=True).to_numpy() * 100.0


def fit_measures(
    matrix: pd.DataFrame,
    cluster_labels: np.ndarray | pd.Series,
    layout: MeasuresLayout,
    model_version: str,
) -> MeasuresParams:
    """Fit the frozen measure parameters on the freeze population."""
    frame = matrix[list(layout.signals)]
    values = frame.to_numpy(float)
    if values.shape[0] < 2:
        raise MeasuresError("cannot fit the measures on fewer than two rows")
    center = values.mean(axis=0)
    scale = values.std(axis=0, ddof=0)
    safe_scale = np.where(scale == 0.0, 1.0, scale)

    _, m1_load, m1_var, m1_sign = oriented_pc1(values)

    labels = np.asarray(cluster_labels)
    eta_sq: dict[str, float] = {}
    total_ss = ((values - values.mean(axis=0)) ** 2).sum(axis=0)
    unique = [label for label in pd.unique(labels) if not pd.isna(label)]
    for index, signal in enumerate(layout.signals):
        column = values[:, index]
        grand_mean = column.mean()
        between = 0.0
        for label in unique:
            mask = labels == label
            between += ((column[mask].mean() - grand_mean) ** 2) * mask.sum()
        eta_sq[signal] = float(between / total_ss[index]) if total_ss[index] != 0 else 0.0

    raw_weights = {
        block: sum(eta_sq[signal] for signal in signals) for block, signals in layout.blocks.items()
    }
    weight_total = sum(raw_weights.values())
    block_weights = {
        block: (weight / weight_total if weight_total != 0 else 0.0)
        for block, weight in raw_weights.items()
    }

    m3_blocks: OrderedDict[str, BlockParams] = OrderedDict()
    for block, signals in layout.blocks.items():
        indices = [layout.signals.index(signal) for signal in signals]
        block_values = values[:, indices]
        if block_values.shape[1] == 1:
            column = block_values[:, 0]
            column_scale = column.std(ddof=0) or 1.0
            raw_score = (column - column.mean()) / column_scale
            loadings = {signals[0]: 1.0}
            sign = 1.0
            variance = 1.0
        else:
            raw_score, oriented, variance, sign = oriented_pc1(block_values)
            loadings = dict(zip(signals, (float(v) for v in oriented), strict=True))
        m3_blocks[block] = BlockParams(
            features=list(signals),
            loadings={k: float(v) for k, v in loadings.items()},
            sign=float(sign),
            variance_explained=float(variance),
            score_center=float(raw_score.mean()),
            score_scale=float(raw_score.std(ddof=0)) or 1.0,
        )

    params = MeasuresParams(
        model_version=model_version,
        signals=list(layout.signals),
        blocks=OrderedDict((b, list(f)) for b, f in layout.blocks.items()),
        sources=dict(layout.sources),
        signal_center=dict(zip(layout.signals, (float(v) for v in center), strict=True)),
        signal_scale=dict(zip(layout.signals, (float(v) for v in safe_scale), strict=True)),
        m1_loadings=dict(zip(layout.signals, (float(v) for v in m1_load), strict=True)),
        m1_sign=float(m1_sign),
        m1_variance_explained=float(m1_var),
        eta_sq=eta_sq,
        block_weights=block_weights,
        m3_blocks=m3_blocks,
    )
    params.validate()
    return params


def _standardise_frozen(matrix: pd.DataFrame, params: MeasuresParams) -> np.ndarray:
    frame = matrix[list(params.signals)]
    center = np.array([params.signal_center[s] for s in params.signals], dtype=float)
    scale = np.array([params.signal_scale[s] for s in params.signals], dtype=float)
    scale = np.where(scale == 0.0, 1.0, scale)
    return (frame.to_numpy(float) - center) / scale


MEASURE_KEYS: tuple[str, ...] = ("m1", "m2", "m3")


def compute_measures(matrix: pd.DataFrame, params: MeasuresParams) -> pd.DataFrame:
    """The three raw scores and the per-block sub-scores, from frozen parameters."""
    standardised = _standardise_frozen(matrix, params)

    m1_load = np.array([params.m1_loadings[s] for s in params.signals], dtype=float)
    m1 = standardised @ m1_load

    m2 = np.zeros(standardised.shape[0], dtype=float)
    for block, signals in params.blocks.items():
        indices = [params.signals.index(signal) for signal in signals]
        m2 += params.block_weights[block] * standardised[:, indices].mean(axis=1)

    subscores: OrderedDict[str, np.ndarray] = OrderedDict()
    for block, signals in params.blocks.items():
        block_params = params.m3_blocks[block]
        indices = [params.signals.index(signal) for signal in signals]
        loadings = np.array([block_params.loadings[s] for s in signals], dtype=float)
        raw = standardised[:, indices] @ loadings
        subscores[block] = (raw - block_params.score_center) / (block_params.score_scale or 1.0)
    m3 = np.column_stack([subscores[block] for block in params.blocks]).mean(axis=1)

    data: dict[str, np.ndarray] = {"m1_score": m1, "m2_score": m2, "m3_score": m3}
    for block in params.blocks:
        data[f"m3_{block}"] = subscores[block]
    return pd.DataFrame(data, index=matrix.index)


def apply_measures(matrix: pd.DataFrame, params: MeasuresParams) -> pd.DataFrame:
    """Score one week: the frozen raw scores plus within-week percentiles."""
    raw = compute_measures(matrix, params)
    columns: dict[str, np.ndarray] = {}
    for key in MEASURE_KEYS:
        score = raw[f"{key}_score"].to_numpy()
        columns[f"{key}_score"] = score
        columns[f"{key}_percentile"] = within_week_percentile(score)
    for block in params.blocks:
        name = f"m3_{block}"
        columns[name] = raw[name].to_numpy()
    return pd.DataFrame(columns, index=matrix.index)
