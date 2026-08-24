"""The transformation library every feature is built out of.

Pure numeric work over pandas and numpy: winsorisation, the count transform,
floored rates, recency, momentum and the weekly-bin consistency metrics. No
vocabulary, no contract, no I/O -- which is why this module is where the
arithmetic decisions are recorded rather than scattered through the builders.

Four of those decisions are load-bearing and each exists because the obvious
alternative is wrong in a way that still produces a number:

**Winsorisation is fit, not applied on the fly.** Thresholds come from the
training panel and are then applied everywhere, so a week with one extreme reader
does not move the scale for everybody else in that week.

**Zero-heavy features winsorise on their positive rows only.** A feature that is
95% zero has a 99.5th percentile of zero, so the naive branch clips the whole
feature to a constant zero -- a feature that exists, passes every finiteness
check, and carries no information at all.

**A rate's denominator is floored, never guarded with an ``if``.** Time per view
on one view is noise; reporting it as a small number is worse than not reporting
it. The floor makes the low-denominator rows converge on the numerator's scale
instead of exploding.

**Inactivity in a window is a real value, not a null.** Recency saturates at
:data:`RECENCY_CAP_DAYS` rather than going missing, so "no activity in the window"
and "activity on the last possible day" stay distinguishable after
standardisation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from engagement_kernel.engagement.windows import TRAILING_WINDOW_DAYS, WEEK_BIN_COUNT

#: Days a fully inactive reader is credited with since their last activity.
#:
#: One past the window, not the window length. A reader last active on the first
#: day of the 28-day window and a reader with no activity at all are different
#: facts, and at 28 they would collapse to the same number.
RECENCY_CAP_DAYS = TRAILING_WINDOW_DAYS + 1

#: Upper quantile the winsorisation threshold is taken at.
DEFAULT_WINSOR_QUANTILE = 0.995

#: Zero share at or above which winsorisation switches to its positive-only
#: branch. See the module docstring: below this the naive quantile is fine, above
#: it the naive quantile is zero.
DEFAULT_ZERO_SHARE_THRESHOLD = 0.90

#: Denominator floors for the rate features, by the quantity in the denominator.
#:
#: Each is the point below which the rate is noise rather than a small number.
#: They are floors and not minimum-row filters, so every reader keeps a value.
SAFE_RATE_FLOORS: dict[str, float] = {
    "views": 3.0,
    "sessions": 2.0,
    "active_days": 1.0,
    "actions": 1.0,
}

#: Guard against a zero mean in the coefficient of variation. Small enough not to
#: move a real value, large enough that an all-zero row yields 0 rather than nan.
WEEKLY_CV_EPS = 1e-9


class TransformError(ValueError):
    """A transform was asked for something it cannot compute honestly."""


@dataclass(frozen=True)
class WinsorizationParams:
    """A fitted winsorisation threshold, with the branch that produced it.

    ``positive_conditional`` is carried rather than recomputed because it is the
    difference between "this feature's tail was clipped" and "this feature is
    sparse and its tail was clipped among the readers who have any" -- and a
    frozen model has to be able to say which happened.
    """

    lower: float
    upper: float
    quantile: float
    zero_share: float
    positive_conditional: bool

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "quantile": self.quantile,
            "zero_share": self.zero_share,
            "positive_conditional": self.positive_conditional,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> WinsorizationParams:
        return cls(
            lower=float(payload["lower"]),
            upper=float(payload["upper"]),
            quantile=float(payload["quantile"]),
            zero_share=float(payload["zero_share"]),
            positive_conditional=bool(payload["positive_conditional"]),
        )


def fit_winsorization(
    values: pd.Series | np.ndarray,
    *,
    quantile: float = DEFAULT_WINSOR_QUANTILE,
    zero_share_threshold: float = DEFAULT_ZERO_SHARE_THRESHOLD,
) -> WinsorizationParams:
    """Fit the upper clip on the training panel."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise TransformError("cannot fit winsorization on an empty feature")
    if (arr < 0).any():
        raise TransformError(
            "winsorization expects non-negative count features; a negative value means "
            "the wrong column arrived, not an outlier"
        )
    zero_share = float((arr == 0).mean())
    positive_conditional = zero_share >= zero_share_threshold
    if positive_conditional:
        positive = arr[arr > 0]
        upper = float(np.quantile(positive, quantile)) if positive.size else 0.0
    else:
        upper = float(np.quantile(arr, quantile))
    if (arr > 0).any() and upper <= 0:
        raise TransformError(
            "winsorization produced a zero upper threshold for a feature with positive "
            "values, which would clip it to a constant"
        )
    return WinsorizationParams(
        lower=0.0,
        upper=upper,
        quantile=quantile,
        zero_share=zero_share,
        positive_conditional=positive_conditional,
    )


def apply_winsorization(
    values: pd.Series | np.ndarray,
    params: WinsorizationParams,
) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), params.lower, params.upper)


def log_count(values: pd.Series | np.ndarray, params: WinsorizationParams) -> np.ndarray:
    """The count transform: ``log1p`` **after** winsorisation.

    Order matters and this is the only order that works. Logging first and
    clipping the logs puts the threshold on a scale nobody declared, and the
    clip point then moves with the base of the logarithm.
    """
    return np.log1p(apply_winsorization(values, params))


def safe_rate(
    numerator: pd.Series | np.ndarray,
    denominator: pd.Series | np.ndarray,
    floor: float,
) -> np.ndarray:
    """``numerator / max(denominator, floor)``."""
    if floor <= 0:
        raise TransformError("a rate floor must be positive; zero re-admits the division by zero")
    num = np.asarray(numerator, dtype=float)
    den = np.asarray(denominator, dtype=float)
    return num / np.maximum(den, floor)


def recency_days(last_active: pd.Series, as_of_week_end: date) -> pd.Series:
    """Days since the last activity in the window, saturating when there was none."""
    out = pd.Series(float(RECENCY_CAP_DAYS), index=last_active.index, dtype=float)
    active = last_active.notna()
    if active.any():
        deltas = (
            pd.Timestamp(as_of_week_end) - pd.to_datetime(last_active[active])
        ).dt.days.astype(float)
        out[active] = deltas
    return out


def recency_score(days: pd.Series | np.ndarray) -> np.ndarray:
    """Recency as a ``[0, 1]`` score: 1 active on the snapshot day, 0 inactive."""
    arr = np.asarray(days, dtype=float)
    return 1.0 - np.minimum(arr, RECENCY_CAP_DAYS) / RECENCY_CAP_DAYS


def momentum(
    bin_1: pd.Series | np.ndarray,
    bin_2: pd.Series | np.ndarray,
    bin_3: pd.Series | np.ndarray,
    bin_4: pd.Series | np.ndarray,
) -> np.ndarray:
    """``log1p(b1 + b2) - log1p(b3 + b4)``; positive means ramping up.

    A conditional feature: for a reader with no activity in the window it is
    exactly zero, which is indistinguishable from a steady reader. Callers mask
    inactive rows and neutral-impute them rather than letting the two share a
    value -- see :mod:`engagement_kernel.engagement.imputation`.
    """
    recent = np.asarray(bin_1, dtype=float) + np.asarray(bin_2, dtype=float)
    earlier = np.asarray(bin_3, dtype=float) + np.asarray(bin_4, dtype=float)
    return np.log1p(recent) - np.log1p(earlier)


def weekly_bin_consistency(bins: pd.DataFrame) -> pd.DataFrame:
    """Habit and evenness metrics from the four weekly bins, ``b1`` most recent.

    Returns active weeks, the share in the busiest week, the coefficient of
    variation of the logged bins, and the normalised entropy of the bin shares.

    Orientation is deliberately **not** applied here. Two of these read
    "higher = less consistent", and flipping them at the point where they are
    assembled into a model input keeps the raw metric readable in the profile
    tables. Flipping here would mean a published ``top_week_share`` that is not a
    share of anything.

    ``active_weeks`` is a count of bins with any activity, which makes it
    invariant to the unit its input is measured in -- a week with one click and a
    week with one campaign-click are both a week with a non-zero bin. That
    property is what lets the email cadence axis be stated independently of the
    click-unit decision.
    """
    arr = bins.to_numpy(dtype=float)
    if arr.shape[1] != WEEK_BIN_COUNT:
        raise TransformError(
            f"weekly_bin_consistency expects exactly {WEEK_BIN_COUNT} bin columns, "
            f"got {arr.shape[1]}"
        )
    total = arr.sum(axis=1)
    active_weeks = (arr > 0).sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        top_share = np.where(total > 0, arr.max(axis=1) / np.where(total > 0, total, 1.0), 0.0)

    logged = np.log1p(arr)
    cv = logged.std(axis=1, ddof=0) / (logged.mean(axis=1) + WEEKLY_CV_EPS)

    safe_total = np.where(total[:, None] > 0, total[:, None], 1.0)
    shares = np.where(total[:, None] > 0, arr / safe_total, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        terms = np.where(shares > 0, shares * np.log(shares), 0.0)
    entropy = -terms.sum(axis=1) / np.log(WEEK_BIN_COUNT)
    entropy = np.where(total > 0, entropy, 0.0)

    return pd.DataFrame(
        {
            "active_weeks_4": active_weeks,
            "top_week_share_4": top_share,
            "weekly_cv_4": cv,
            "weekly_evenness_entropy_4": entropy,
        },
        index=bins.index,
    )
