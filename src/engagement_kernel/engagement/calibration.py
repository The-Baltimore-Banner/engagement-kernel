"""Fitted standardisation and percentile calibration for one feature.

Every calibration is fit on the training panel and applied everywhere else. That
is the discipline that makes a cluster id mean the same thing in week 40 as in
week 4: a per-week standardisation would re-centre the space every week, so a
reader whose behaviour never changed would drift across cluster boundaries as the
population around them moved.

A calibration carries four fitted statistics and the sorted panel values behind
them, because they answer different questions and a frozen model needs all of
them: mean and standard deviation for the z-score the models consume, median and
inter-quartile range for a robust variant, and the sorted values for the
empirical percentile that the cross-channel mix is built on.

The percentile is an empirical CDF with mid-rank ties, not an interpolated
quantile. Interpolation invents values between panel observations, which for a
sparse count feature -- where most of the mass sits on a handful of small
integers -- puts readers at percentiles no panel member occupies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

#: Default clip applied to z-scores that feed a model.
#:
#: A distance-based model has no notion of a diminishing return, so an unclipped
#: outlier at 40 standard deviations does not just sit far away -- it drags a
#: centroid to itself and empties the cluster it belongs to.
Z_CLIP_DEFAULT = 5.0

#: Percentile clip before the inverse normal, so the transform stays finite.
PCT_CLIP_LO = 0.001
PCT_CLIP_HI = 0.999

#: Floor on a divisor. Guards a zero-variance feature: dividing by its real zero
#: gives inf or nan, and both propagate silently through a distance.
STD_EPS = 1e-12

#: Inverse normal CDF, from the standard library rather than from SciPy.
#:
#: This is the only place the rank-normal variant needed a special function, and
#: taking it from ``statistics`` keeps one more dependency out of an adopter's
#: install for a single call.
_STANDARD_NORMAL = NormalDist()


class CalibrationError(ValueError):
    """A calibration was asked to fit or apply something it cannot."""


@dataclass(frozen=True)
class FeatureCalibration:
    """One feature's fitted calibration."""

    mean: float
    std: float
    median: float
    iqr: float
    #: The panel values, sorted, backing :meth:`pct`. Excluded from ``repr``
    #: because a fitted panel is thousands of numbers and a traceback carrying
    #: them is unreadable.
    sorted_panel_values: np.ndarray = field(repr=False)

    @property
    def n_panel(self) -> int:
        return int(self.sorted_panel_values.size)

    def z(self, values: pd.Series | np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        return (arr - self.mean) / max(self.std, STD_EPS)

    def z_clipped(self, values: pd.Series | np.ndarray, clip: float = Z_CLIP_DEFAULT) -> np.ndarray:
        return np.clip(self.z(values), -clip, clip)

    def robust_z(self, values: pd.Series | np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        return (arr - self.median) / max(self.iqr, STD_EPS)

    def pct(self, values: pd.Series | np.ndarray) -> np.ndarray:
        """Empirical CDF against the panel, with mid-rank ties."""
        arr = np.asarray(values, dtype=float)
        below = np.searchsorted(self.sorted_panel_values, arr, side="left")
        upto = np.searchsorted(self.sorted_panel_values, arr, side="right")
        return (below + 0.5 * (upto - below)) / self.n_panel

    def rank_normal(self, values: pd.Series | np.ndarray) -> np.ndarray:
        """Inverse normal CDF of the clipped panel percentile."""
        pct = np.clip(self.pct(values), PCT_CLIP_LO, PCT_CLIP_HI)
        return np.array([_STANDARD_NORMAL.inv_cdf(float(p)) for p in np.ravel(pct)]).reshape(
            np.shape(pct)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mean": self.mean,
            "std": self.std,
            "median": self.median,
            "iqr": self.iqr,
            "sorted_panel_values": self.sorted_panel_values.tolist(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> FeatureCalibration:
        return cls(
            mean=float(payload["mean"]),  # type: ignore[arg-type]
            std=float(payload["std"]),  # type: ignore[arg-type]
            median=float(payload["median"]),  # type: ignore[arg-type]
            iqr=float(payload["iqr"]),  # type: ignore[arg-type]
            sorted_panel_values=np.asarray(payload["sorted_panel_values"], dtype=float),
        )


def fit_calibration(panel_values: pd.Series | np.ndarray) -> FeatureCalibration:
    """Fit a calibration on training-panel values only."""
    arr = np.asarray(panel_values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise CalibrationError(
            "cannot fit a calibration on an empty feature: with no panel values there is "
            "no scale to apply, and defaulting to the identity would publish raw counts "
            "as if they were standardised"
        )
    q75, q25 = np.quantile(arr, [0.75, 0.25])
    return FeatureCalibration(
        mean=float(arr.mean()),
        std=float(arr.std(ddof=0)),
        median=float(np.median(arr)),
        iqr=float(q75 - q25),
        sorted_panel_values=np.sort(arr),
    )


def calibrated_variants(
    values: pd.Series,
    calibration: FeatureCalibration,
    *,
    name: str,
    z_clip: float = Z_CLIP_DEFAULT,
) -> pd.DataFrame:
    """The three stored variants of a published score: raw, z, panel percentile.

    All three are persisted because they answer different questions and only one
    of them is comparable across model versions. The raw value is what happened,
    the z is what the model saw, and the percentile is what a person means when
    they ask where a reader sits.
    """
    return pd.DataFrame(
        {
            f"{name}_raw": values.astype(float),
            f"{name}_z": calibration.z_clipped(values, clip=z_clip),
            f"{name}_pct": calibration.pct(values),
        },
        index=values.index,
    )
