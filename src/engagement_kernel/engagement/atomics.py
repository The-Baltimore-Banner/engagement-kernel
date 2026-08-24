"""Weekly atomic features, one row per reader, from the daily intermediate tables.

This is the layer where daily rows become a reader's week. Everything above it is
standardisation and modelling; everything below it is the contract. Four builders,
one per signal family, and they share a shape on purpose: a 7-day and a 28-day
window count, active days in each, days since the last activity, the conditional
rates, and the four weekly bins that carry habit and momentum.

Two structural decisions run through all four.

**Columns are projected before the guard runs, and the guard proves the
projection.** Each builder is handed an explicit column list
(:data:`CONSUMPTION_INPUT_COLUMNS` and friends) and then
:func:`~engagement_kernel.engagement.guards.assert_no_forbidden_inputs` checks
what survived. The projection is the mechanism; the guard is the proof it was
complete. This matters most for email: the daily email table carries an ``opens``
column on purpose, for reachability reporting, and it must not reach the atomics.
Handing the whole table to the guard would fail every run; projecting without the
guard would pass every run whether or not the projection was right.

**A reader with no activity in the window produces no row here at all.** The spine
supplies the population and :func:`join_atomics` fills the gap, because the fill
value differs by feature class: a count fills with its true zero, recency fills
with the saturated cap, and a conditional feature stays missing until neutral
imputation writes the baseline into it. Filling everything with zero here would
put every inactive reader at the bottom of every conditional distribution --
which reads as "shallow" rather than "absent".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from engagement_kernel.contract import enums
from engagement_kernel.engagement.guards import assert_no_forbidden_inputs
from engagement_kernel.engagement.transforms import (
    RECENCY_CAP_DAYS,
    SAFE_RATE_FLOORS,
    momentum,
    recency_days,
    safe_rate,
    weekly_bin_consistency,
)
from engagement_kernel.engagement.windows import WeekGrid, window_mask
from engagement_kernel.intermediate.tables import COMMUNITY_ACTION_COLUMNS, LOCAL_DATE_COLUMN

#: The reader key, everywhere. One id at one declared grain is a contract
#: property, so there is no second id column and nothing to reconcile.
READER_KEY = "reader_id"

#: Columns admitted from ``reader_channel_day``.
CONSUMPTION_INPUT_COLUMNS: tuple[str, ...] = (
    READER_KEY,
    "channel",
    LOCAL_DATE_COLUMN,
    "views",
    "sessions",
    "total_time_seconds",
    "measured_time_deliveries",
    "events",
)

#: Columns admitted from ``reader_email_day``. Clicks only; ``opens`` is left
#: behind here, which is the whole reason this list is explicit.
EMAIL_INPUT_COLUMNS: tuple[str, ...] = (READER_KEY, "list_id", LOCAL_DATE_COLUMN, "clicks")

#: Columns admitted from ``reader_community_day``.
COMMUNITY_INPUT_COLUMNS: tuple[str, ...] = (
    READER_KEY,
    LOCAL_DATE_COLUMN,
    *COMMUNITY_ACTION_COLUMNS.values(),
)

#: Daily community column -> the feature-name stem it becomes.
COMMUNITY_FEATURE_STEMS: dict[str, str] = {
    "posts_created": "community_posts",
    "replies_created": "community_replies",
    "likes_given": "community_likes",
    "dislikes_given": "community_dislikes",
    "flags_given": "community_flags",
}

#: Which community actions are *contribution* and which are *reaction*.
#:
#: Contribution is authoring -- the reader made something. Reaction is responding
#: to what somebody else made. The split is declared rather than inferred from the
#: column names, because inferring it from a suffix means an action kind added to
#: the contract with a different suffix lands in whichever half the rule happens
#: to put it, and the ratio then moves for a reason nobody chose.
COMMUNITY_CONTRIBUTION_COLUMNS: tuple[str, ...] = ("posts_created", "replies_created")
COMMUNITY_REACTION_COLUMNS: tuple[str, ...] = ("likes_given", "dislikes_given", "flags_given")

_declared = set(COMMUNITY_CONTRIBUTION_COLUMNS) | set(COMMUNITY_REACTION_COLUMNS)
_available = set(COMMUNITY_ACTION_COLUMNS.values())
if _declared != _available:  # pragma: no cover - a contract change trips this, not a run
    raise ImportError(
        "the contribution/reaction split does not cover the community columns the "
        f"intermediate build emits: missing {sorted(_available - _declared)}, "
        f"unknown {sorted(_declared - _available)}. The two ratios would silently be "
        "computed over a subset of the reader's actions"
    )

#: Feature-name suffixes that mark a *conditional* feature.
#:
#: These answer "given activity, what was it like?", so they stay missing for an
#: inactive reader instead of taking a zero. Suffix-matched rather than listed per
#: channel so a new channel gets the same treatment without another list.
CONDITIONAL_SUFFIXES: tuple[str, ...] = (
    "_time_per_view_28d",
    "_articles_per_session_28d",
    "_momentum_4",
    "_top_week_share_4",
    "_weekly_cv_4",
    "_weekly_evenness_entropy_4",
    "_contribution_ratio_28d",
    "_contribution_per_active_day_28d",
)


def channel_prefix(channel: str) -> str:
    """Feature-name prefix for one reader-event channel.

    Identity today, and a function anyway: the channel values are contract enum
    members, and the day one of them is not a valid identifier fragment this is
    the one place that has to change.
    """
    if channel not in enums.READER_EVENT_CHANNELS:
        raise ValueError(f"{channel!r} is not a contract reader-event channel")
    return channel


def _project(frame: pd.DataFrame, columns: Sequence[str], *, where: str) -> pd.DataFrame:
    """Narrow a daily frame to the admitted columns, then guard what is left."""
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise KeyError(f"{where} is missing columns the atomic layer needs: {missing}")
    narrowed = frame.loc[:, list(columns)]
    assert_no_forbidden_inputs(narrowed.columns, where=where)
    return narrowed


def _windows(
    daily: pd.DataFrame, grid: WeekGrid, week_end: date
) -> tuple[pd.DataFrame, pd.DataFrame]:
    in_28d = daily.loc[window_mask(daily, grid.trailing_window(week_end), LOCAL_DATE_COLUMN)].copy()
    in_7d = in_28d.loc[window_mask(in_28d, grid.current_week(week_end), LOCAL_DATE_COLUMN)]
    return in_28d, in_7d


def _sum_by_reader(frame: pd.DataFrame, column: str) -> pd.Series:
    return frame.groupby(READER_KEY)[column].sum()


def _active_days(frame: pd.DataFrame) -> pd.Series:
    """Distinct local days with activity.

    A count of distinct dates, so it is invariant to how many rows a day happens
    to be split across -- which is what makes it comparable between a channel
    whose grain is per-content and one whose grain is per-day.
    """
    return frame.groupby(READER_KEY)[LOCAL_DATE_COLUMN].nunique()


def _last_active(frame: pd.DataFrame) -> pd.Series:
    return frame.groupby(READER_KEY)[LOCAL_DATE_COLUMN].max()


def _bin_sums(
    daily: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
    value_column: str,
) -> pd.DataFrame:
    """Four weekly bin sums per reader, ``b1`` the most recent, zero-filled."""
    out: dict[str, pd.Series] = {}
    for index, bounds in enumerate(grid.week_bins(week_end), start=1):
        rows = daily.loc[window_mask(daily, bounds, LOCAL_DATE_COLUMN)]
        out[f"b{index}"] = rows.groupby(READER_KEY)[value_column].sum()
    return pd.DataFrame(out).fillna(0.0)


def _bin_features(bin_sums: pd.DataFrame, prefix: str, *, with_momentum: bool) -> pd.DataFrame:
    """Habit and momentum features from the four bins.

    ``with_momentum`` is False for the sparse channels. On a signal where most
    readers have one active week in four, momentum is the difference of two
    log1p'd numbers that are usually 0 and 1, so it takes three values and none of
    them means what the feature is named after.
    """
    consistency = weekly_bin_consistency(bin_sums[["b1", "b2", "b3", "b4"]])
    out = consistency.rename(columns=lambda name: f"{prefix}_{name}")
    if with_momentum:
        out[f"{prefix}_momentum_4"] = momentum(
            bin_sums["b1"], bin_sums["b2"], bin_sums["b3"], bin_sums["b4"]
        )
    return out


# --- consumption ------------------------------------------------------------


def build_consumption_atomics(
    channel_day: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
    channel: str,
) -> pd.DataFrame:
    """Weekly consumption atomics for one channel.

    ``channel_day`` is the whole ``reader_channel_day`` table; the channel filter
    happens here rather than at the call site so the filter and the prefix cannot
    disagree.
    """
    prefix = channel_prefix(channel)
    projected = _project(channel_day, CONSUMPTION_INPUT_COLUMNS, where="reader_channel_day")
    daily = projected.loc[projected["channel"] == channel]
    in_28d, in_7d = _windows(daily, grid, week_end)

    readers = pd.Index(in_28d[READER_KEY].dropna().unique(), name=READER_KEY)
    out = pd.DataFrame(index=readers)
    for label, frame in (("7d", in_7d), ("28d", in_28d)):
        out[f"{prefix}_views_{label}"] = _sum_by_reader(frame, "views")
        out[f"{prefix}_sessions_{label}"] = _sum_by_reader(frame, "sessions")
        out[f"{prefix}_events_{label}"] = _sum_by_reader(frame, "events")
        out[f"{prefix}_active_days_{label}"] = _active_days(frame)
    out = out.fillna(0.0)

    last_active = _last_active(in_28d).reindex(out.index)
    out[f"{prefix}_recency_days"] = recency_days(pd.to_datetime(last_active), week_end)

    # Attention per view uses the measured-delivery count as its denominator, not
    # the view count. The contract lets engagement time be null for a view that
    # was not instrumented, and the intermediate build carries how many of a
    # reader's views were: dividing by all views would report a reader whose app
    # views are uninstrumented as having read everything in zero seconds.
    measured_time = _sum_by_reader(in_28d, "total_time_seconds").reindex(out.index).fillna(0.0)
    measured_views = (
        _sum_by_reader(in_28d, "measured_time_deliveries").reindex(out.index).fillna(0.0)
    )
    out[f"{prefix}_time_per_view_28d"] = safe_rate(
        measured_time, measured_views, floor=SAFE_RATE_FLOORS["views"]
    )
    out[f"{prefix}_articles_per_session_28d"] = safe_rate(
        out[f"{prefix}_views_28d"],
        out[f"{prefix}_sessions_28d"],
        floor=SAFE_RATE_FLOORS["sessions"],
    )

    bins = _bin_sums(in_28d, grid, week_end, "views").reindex(out.index).fillna(0.0)
    out = out.join(_bin_features(bins, prefix, with_momentum=True))
    return out.reset_index()


# --- email ------------------------------------------------------------------


def build_email_atomics(
    email_day: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
    *,
    list_ids: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Weekly email-cadence atomics. Clicks only, on the declared lists.

    ``list_ids`` of ``None`` means every list in the delivery. There is
    deliberately no default list: a hardcoded third-party list identifier is one
    publisher's deployment configuration and means nothing to anybody else.

    The unit is the click event
    (:data:`~engagement_kernel.engagement.config.EMAIL_CLICK_UNIT`). The habit
    features here are invariant to that choice -- they count weeks with a non-zero
    bin -- and the two volume features are not. Both volume features are
    log-transformed downstream, which damps the difference further, but it is a
    real difference and it moves with the model version.
    """
    projected = _project(email_day, EMAIL_INPUT_COLUMNS, where="reader_email_day")
    if list_ids is not None:
        projected = projected.loc[projected["list_id"].isin(list(list_ids))]

    in_28d, in_7d = _windows(projected, grid, week_end)
    # A row with zero clicks is a day this reader was on the list and did not
    # click. Dropping it before counting active days is what stops "on the list"
    # being counted as "clicked".
    in_28d = in_28d.loc[in_28d["clicks"] > 0]
    in_7d = in_7d.loc[in_7d["clicks"] > 0]

    readers = pd.Index(in_28d[READER_KEY].dropna().unique(), name=READER_KEY)
    out = pd.DataFrame(index=readers)
    out["email_clicks_7d"] = _sum_by_reader(in_7d, "clicks")
    out["email_clicks_28d"] = _sum_by_reader(in_28d, "clicks")
    out["email_click_days_7d"] = _active_days(in_7d)
    out["email_click_days_28d"] = _active_days(in_28d)
    out = out.fillna(0.0)

    last_active = _last_active(in_28d).reindex(out.index)
    out["email_click_recency_days"] = recency_days(pd.to_datetime(last_active), week_end)

    bins = _bin_sums(in_28d, grid, week_end, "clicks").reindex(out.index).fillna(0.0)
    out = out.join(_bin_features(bins, "email_click", with_momentum=False))
    return out.reset_index()


# --- community --------------------------------------------------------------


def build_community_atomics(
    community_day: pd.DataFrame,
    grid: WeekGrid,
    week_end: date,
) -> pd.DataFrame:
    """Weekly community atomics, summed across community properties.

    Every count is an action the reader *performed*. The contract has no
    received-side action kind, which is what makes that structural rather than a
    convention: counting a like the reader received would invert the feature while
    leaving its magnitude entirely plausible.
    """
    projected = _project(community_day, COMMUNITY_INPUT_COLUMNS, where="reader_community_day")
    frame = projected.copy()
    action_columns = list(COMMUNITY_ACTION_COLUMNS.values())
    frame["_actions"] = frame[action_columns].sum(axis=1)
    frame = frame.loc[frame["_actions"] > 0]

    in_28d, in_7d = _windows(frame, grid, week_end)
    readers = pd.Index(in_28d[READER_KEY].dropna().unique(), name=READER_KEY)
    out = pd.DataFrame(index=readers)

    for label, window_frame in (("7d", in_7d), ("28d", in_28d)):
        for source, stem in COMMUNITY_FEATURE_STEMS.items():
            out[f"{stem}_{label}"] = _sum_by_reader(window_frame, source)
        out[f"community_actions_{label}"] = _sum_by_reader(window_frame, "_actions")
        out[f"community_active_days_{label}"] = _active_days(window_frame)
    out = out.fillna(0.0)

    contribution = sum(
        _sum_by_reader(in_28d, column).reindex(out.index).fillna(0.0)
        for column in COMMUNITY_CONTRIBUTION_COLUMNS
    )
    reaction = sum(
        _sum_by_reader(in_28d, column).reindex(out.index).fillna(0.0)
        for column in COMMUNITY_REACTION_COLUMNS
    )
    out["community_contribution_actions_28d"] = contribution
    out["community_reaction_actions_28d"] = reaction
    out["community_contribution_ratio_28d"] = contribution / np.maximum(
        out["community_actions_28d"], SAFE_RATE_FLOORS["actions"]
    )
    out["community_contribution_per_active_day_28d"] = contribution / np.maximum(
        out["community_active_days_28d"], SAFE_RATE_FLOORS["active_days"]
    )

    last_active = _last_active(in_28d).reindex(out.index)
    out["community_recency_days"] = recency_days(pd.to_datetime(last_active), week_end)

    bins = _bin_sums(in_28d, grid, week_end, "_actions").reindex(out.index).fillna(0.0)
    out = out.join(_bin_features(bins, "community", with_momentum=False))
    return out.reset_index()


# --- joining onto the spine -------------------------------------------------


def join_atomics(
    spine: pd.DataFrame,
    atomics: pd.DataFrame,
    *,
    recency_column: str | None,
) -> pd.DataFrame:
    """Left-join one atomic frame onto the spine with per-class fill rules.

    Counts fill with 0, the recency column fills with the saturated cap, and
    conditional features are left missing for neutral imputation to handle. The
    three rules exist because the three classes mean different things by absence,
    and one blanket ``fillna(0)`` is how an absent reader becomes a shallow one.
    """
    merged = spine.merge(atomics, how="left", on=READER_KEY)
    feature_columns = [column for column in atomics.columns if column != READER_KEY]
    for column in feature_columns:
        if recency_column is not None and column == recency_column:
            merged[column] = merged[column].fillna(float(RECENCY_CAP_DAYS))
        elif any(column.endswith(suffix) for suffix in CONDITIONAL_SUFFIXES):
            continue
        else:
            merged[column] = merged[column].fillna(0.0)
    return merged


def empty_atomics(columns: Sequence[str]) -> pd.DataFrame:
    """An atomic frame with the right columns and no rows.

    For an optional input the delivery does not carry. Every reader then takes the
    fill values in :func:`join_atomics`, which is correct *only* because the
    surface resolution has already dropped the features that channel feeds -- see
    :func:`~engagement_kernel.engagement.config.resolve_surface`. Without that,
    this would be the zero-filling the contract exists to prevent.
    """
    return pd.DataFrame({column: pd.Series(dtype="float64") for column in columns}).assign(
        **{READER_KEY: pd.Series(dtype="object")}
    )[[READER_KEY, *columns]]
