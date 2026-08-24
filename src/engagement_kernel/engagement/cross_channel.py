"""Cross-channel features: how a reader's attention is split, and how much of it there is.

One app view is not one email click. Raw counts from different channels are not
commensurate, so a mix computed from raw activity would say a reader who clicks
twenty newsletters and reads three articles is overwhelmingly an email reader --
when what it actually measured is that clicks are more frequent events than
article views for everybody.

The fix is to put every channel on one scale first. Each channel's activity is
turned into its **panel percentile among readers who use that channel at all**, so
a signal of 0.8 means "busier on this channel than 80% of the people who use it",
which is a comparable statement across channels. Non-use stays exactly zero rather
than becoming the bottom percentile: a reader who has never opened the app is not
the least engaged app reader.

The mix shares are then compositional -- they sum to 1 for any reader with
activity, so one of them is linearly redundant. That is fine for a distance-based
model and it is not fine for a full-covariance mixture model, which will find the
degenerate direction and fail to invert. Stated here because the constraint
belongs with the feature, not with whoever picks an algorithm later.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from engagement_kernel.engagement.calibration import FeatureCalibration, fit_calibration
from engagement_kernel.engagement.transforms import momentum
from engagement_kernel.engagement.windows import WeekGrid, window_mask
from engagement_kernel.intermediate.tables import LOCAL_DATE_COLUMN

READER_KEY = "reader_id"


class CrossChannelError(ValueError):
    """A cross-channel feature could not be built from the anchors supplied."""


def channel_signal_column(channel: str) -> str:
    return f"channel_signal_{channel}"


def channel_mix_column(channel: str) -> str:
    return f"channel_mix_{channel}"


def fit_channel_signals(
    panel: pd.DataFrame,
    anchors: dict[str, str],
) -> dict[str, FeatureCalibration]:
    """Fit one percentile table per channel, on the panel's *active* rows.

    ``anchors`` maps a channel name to the activity column that anchors it. Fitting
    on active rows only is the load-bearing part: including the zeros would put the
    median of most channels at zero, and every active reader would then land in the
    top decile of a distribution that is mostly absence.
    """
    tables: dict[str, FeatureCalibration] = {}
    for channel, anchor in anchors.items():
        if anchor not in panel.columns:
            raise CrossChannelError(f"channel {channel!r} anchors on absent column {anchor!r}")
        values = panel[anchor].fillna(0.0)
        active = values[values > 0]
        if active.empty:
            raise CrossChannelError(
                f"no panel row is active on channel {channel!r} (anchor {anchor!r}), so its "
                "percentile table would be fit on nothing. A delivery with no activity on a "
                "channel should not be declaring that channel available"
            )
        tables[channel] = fit_calibration(np.log1p(active))
    return tables


def channel_signals(
    frame: pd.DataFrame,
    tables: dict[str, FeatureCalibration],
    anchors: dict[str, str],
) -> pd.DataFrame:
    """Per-channel signal in ``[0, 1]``: 0 for non-use, else the active percentile."""
    out = pd.DataFrame(index=frame.index)
    for channel, anchor in anchors.items():
        values = frame[anchor].fillna(0.0)
        percentile = tables[channel].pct(np.log1p(values))
        out[channel_signal_column(channel)] = np.where(values > 0, percentile, 0.0)
    return out


def channel_mix(signals: pd.DataFrame, channels: tuple[str, ...]) -> pd.DataFrame:
    """Shares of total channel signal, plus the total itself.

    A reader with no signal at all keeps zeros rather than an even split. They are
    the deterministic no-recent segment and never reach a fitted model, but the
    columns still have to hold a defensible value: an even split would say their
    attention was evenly divided across channels they did not use.
    """
    signal_columns = [channel_signal_column(channel) for channel in channels]
    total = signals[signal_columns].sum(axis=1)
    out = pd.DataFrame(index=signals.index)
    out["total_channel_signal"] = total
    safe_total = total.where(total > 0, 1.0)
    for channel in channels:
        share = signals[channel_signal_column(channel)] / safe_total
        out[channel_mix_column(channel)] = share.where(total > 0, 0.0)
    return out


def channel_entropy(mix: pd.DataFrame, channels: tuple[str, ...]) -> pd.Series:
    """Normalised entropy of the channel mix. Profile-only, never a model feature.

    A deterministic function of shares that are already in the matrix, so it adds
    no information to a model and would spend a second weight on the same signal.
    The model guard refuses it by name; it is computed because it is the number a
    person reads to say "this cluster is single-channel".
    """
    shares = mix[[channel_mix_column(channel) for channel in channels]].to_numpy(dtype=float)
    if shares.shape[1] < 2:
        return pd.Series(0.0, index=mix.index, name="channel_entropy")
    with np.errstate(invalid="ignore", divide="ignore"):
        terms = np.where(shares > 0, shares * np.log(shares), 0.0)
    return pd.Series(
        -terms.sum(axis=1) / np.log(shares.shape[1]), index=mix.index, name="channel_entropy"
    )


def overall_recency_days(frame: pd.DataFrame, recency_columns: tuple[str, ...]) -> pd.Series:
    """Days since the reader's last activity on *any* channel.

    The minimum of the per-channel recencies, which is the right reduction: a
    reader active on one channel yesterday is a reader active yesterday, whatever
    the other channels say. Averaging would make a single-channel reader look
    stale in proportion to how many channels the publisher runs.
    """
    missing = [column for column in recency_columns if column not in frame.columns]
    if missing:
        raise CrossChannelError(f"recency columns absent from the feature frame: {missing}")
    return frame[list(recency_columns)].min(axis=1).rename("overall_recency_days")


def overall_activity(
    daily_union: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
) -> pd.DataFrame:
    """Overall active days and overall momentum, from the union of every channel's days.

    Counted on the union of ``(reader, local_date)`` pairs, so a reader who read on
    the web and clicked an email on the same day has one active day, not two.
    Summing per-channel active days instead would credit a multi-channel reader
    with more days than the window contains.
    """
    in_window = daily_union.loc[
        window_mask(daily_union, grid.trailing_window(week_end), LOCAL_DATE_COLUMN)
    ]
    active_days = in_window.groupby(READER_KEY)[LOCAL_DATE_COLUMN].nunique()
    out = pd.DataFrame({"overall_active_days_28d": active_days})

    bins: dict[str, pd.Series] = {}
    for index, bounds in enumerate(grid.week_bins(week_end), start=1):
        rows = daily_union.loc[window_mask(daily_union, bounds, LOCAL_DATE_COLUMN)]
        bins[f"b{index}"] = rows.groupby(READER_KEY)[LOCAL_DATE_COLUMN].nunique()
    bin_frame = pd.DataFrame(bins).reindex(out.index).fillna(0.0)
    out["overall_momentum_4"] = momentum(
        bin_frame["b1"], bin_frame["b2"], bin_frame["b3"], bin_frame["b4"]
    )
    out.index.name = READER_KEY
    return out.reset_index()
