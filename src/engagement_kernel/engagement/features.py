"""Assembling one week's feature frame: spine first, activity joined onto it.

This module is where the daily intermediate tables become one row per scored
reader. It does the joining and the bookkeeping and computes nothing new, which is
deliberate: every number here was produced by a module that documents it, and the
value of a single assembly point is that the row count, the fill rules and the
deterministic segments are decided once.

The order is not arbitrary. The spine is built from the subscription history, then
each atomic frame is left-joined onto it, then the row count is asserted equal to
the spine's. Assembling the other way round -- concatenating the activity frames
and adding the population afterwards -- silently drops every reader who did
nothing, and the population then tracks engagement instead of entitlement. That
failure is invisible in aggregate: the averages all rise.

Which channels have features is resolved from the delivery. A publisher with no
community feed gets no community features and no community anchor, rather than a
community block full of zeros -- see
:func:`~engagement_kernel.engagement.config.resolve_surface` for why absence
selects an alternate feature set instead of a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from engagement_kernel.engagement import atomics as atomic_layer
from engagement_kernel.engagement import cross_channel, segments, topics
from engagement_kernel.engagement.buckets import CompletenessReport, check_completeness
from engagement_kernel.engagement.config import LaneConfig
from engagement_kernel.engagement.spine import build_spine
from engagement_kernel.intermediate.tables import LOCAL_DATE_COLUMN

READER_KEY = "reader_id"

#: The channel name the email cadence signal travels under in the cross-channel
#: mix. Not a reader-event channel: it is a separate contract input, and the mix
#: is over "ways this reader engages", not over web surfaces.
EMAIL_CHANNEL = "email"
COMMUNITY_CHANNEL = "community"


class FeatureAssemblyError(ValueError):
    """A week's feature frame could not be assembled as declared."""


@dataclass(frozen=True)
class WeeklyInputs:
    """The intermediate tables one week's features are built from.

    The three optional frames are ``None`` when the delivery does not carry the
    contract input behind them. ``None`` and "an empty frame" are different: an
    empty frame is a publisher who delivers the feed and had no activity, and it
    still gets features.
    """

    subscription_state_interval: pd.DataFrame
    reader_channel_day: pd.DataFrame
    reader_section_day: pd.DataFrame
    reader_email_day: pd.DataFrame | None = None
    reader_community_day: pd.DataFrame | None = None

    @property
    def has_email(self) -> bool:
        return self.reader_email_day is not None

    @property
    def has_community(self) -> bool:
        return self.reader_community_day is not None


def feature_channels(config: LaneConfig, inputs: WeeklyInputs) -> tuple[str, ...]:
    """The engagement channels this delivery supports, in a fixed order.

    Fixed rather than sorted, because it becomes the column order of the mix block
    and a frozen centroid is a vector in that order.
    """
    channels = list(config.channels)
    if inputs.has_email:
        channels.append(EMAIL_CHANNEL)
    if inputs.has_community:
        channels.append(COMMUNITY_CHANNEL)
    return tuple(channels)


def activity_anchors(config: LaneConfig, inputs: WeeklyInputs) -> dict[str, str]:
    """Channel -> the 28-day column that says whether the reader used it at all."""
    anchors = {channel: f"{channel}_views_28d" for channel in config.channels}
    if inputs.has_email:
        anchors[EMAIL_CHANNEL] = "email_clicks_28d"
    if inputs.has_community:
        anchors[COMMUNITY_CHANNEL] = "community_actions_28d"
    return anchors


def recency_columns(config: LaneConfig, inputs: WeeklyInputs) -> tuple[str, ...]:
    """The per-channel recency columns, for the overall-recency reduction."""
    columns = [f"{channel}_recency_days" for channel in config.channels]
    if inputs.has_email:
        columns.append("email_click_recency_days")
    if inputs.has_community:
        columns.append("community_recency_days")
    return tuple(columns)


@dataclass
class WeeklyFeatures:
    """One week's assembled features and the deterministic labels that go with them."""

    frame: pd.DataFrame
    as_of_week_end: date
    no_recent: pd.Series
    content_active: pd.Series
    no_topic_reasons: pd.Series
    #: Share of windowed reading whose section metadata resolved. Read by the
    #: topic gate, and reported whether or not it passes.
    resolved_view_share: float
    completeness: CompletenessReport
    #: Readers with state history who were not entitled on the day, and readers
    #: removed by the manifest's exclusion list. Reported because a population
    #: that halved between two weeks is the first thing worth knowing.
    out_of_population: int
    excluded: int

    @property
    def n_rows(self) -> int:
        return len(self.frame)


def _daily_union(inputs: WeeklyInputs) -> pd.DataFrame:
    """Every ``(reader, local_date)`` pair with activity on any channel.

    Deduplicated, so one reader-day is one row however many channels it came from.
    """
    frames = [inputs.reader_channel_day[[READER_KEY, LOCAL_DATE_COLUMN]]]
    if inputs.reader_email_day is not None:
        frames.append(inputs.reader_email_day[[READER_KEY, LOCAL_DATE_COLUMN]])
    if inputs.reader_community_day is not None:
        frames.append(inputs.reader_community_day[[READER_KEY, LOCAL_DATE_COLUMN]])
    union = pd.concat(frames, ignore_index=True).dropna(subset=[READER_KEY])
    return union.drop_duplicates()


def build_weekly_features(
    inputs: WeeklyInputs,
    week_end: date,
    config: LaneConfig,
) -> WeeklyFeatures:
    """Assemble the feature frame for one week end."""
    config.week_grid.validate_week_end(week_end)
    grid = config.week_grid

    spine_result = build_spine(inputs.subscription_state_interval, week_end, config)
    spine = spine_result.frame
    if spine.empty:
        raise FeatureAssemblyError(
            f"no reader is in the scored population on {week_end.isoformat()}. The "
            f"declared population is {config.scored_population.definition_id} "
            f"({', '.join(config.entitled_states)}); either the states do not match the "
            "delivery's history or the week is outside its coverage"
        )

    frame = spine
    for channel in config.channels:
        channel_atomics = atomic_layer.build_consumption_atomics(
            inputs.reader_channel_day, grid, week_end, channel
        )
        frame = atomic_layer.join_atomics(
            frame, channel_atomics, recency_column=f"{channel}_recency_days"
        )

    if inputs.reader_email_day is not None:
        email_atomics = atomic_layer.build_email_atomics(
            inputs.reader_email_day, grid, week_end, list_ids=config.email_list_ids
        )
        frame = atomic_layer.join_atomics(
            frame, email_atomics, recency_column="email_click_recency_days"
        )

    if inputs.reader_community_day is not None:
        community_atomics = atomic_layer.build_community_atomics(
            inputs.reader_community_day, grid, week_end
        )
        frame = atomic_layer.join_atomics(
            frame, community_atomics, recency_column="community_recency_days"
        )

    topic_atomics = topics.build_topic_atomics(
        inputs.reader_section_day, grid, week_end, config.bucket_map
    )
    frame = frame.merge(topic_atomics, how="left", on=READER_KEY)
    # Topic counts and shares are level features: a reader with no section rows
    # read nothing resolvable, and zero is the honest value. The conditional part
    # of the topic block is the standardised block, imputed later.
    topic_columns = [column for column in topic_atomics.columns if column != READER_KEY]
    frame[topic_columns] = frame[topic_columns].fillna(0.0)

    overall = cross_channel.overall_activity(_daily_union(inputs), grid, week_end)
    frame = frame.merge(overall, how="left", on=READER_KEY).copy()
    frame["overall_active_days_28d"] = frame["overall_active_days_28d"].fillna(0.0)
    # A reader with no activity has no momentum to speak of; zero is the neutral
    # value on a log-ratio and it is what an inactive reader's ramp actually is.
    frame["overall_momentum_4"] = frame["overall_momentum_4"].fillna(0.0)
    frame["overall_recency_days"] = cross_channel.overall_recency_days(
        frame, recency_columns(config, inputs)
    )

    if len(frame) != len(spine):
        raise FeatureAssemblyError(
            f"the feature frame has {len(frame)} rows against a spine of {len(spine)}. A "
            "join has fanned out, so every count on the duplicated readers is now "
            "double-counted while each individual number still looks reasonable"
        )

    anchors = activity_anchors(config, inputs)
    no_recent = segments.no_recent_mask(frame, tuple(anchors.values()))
    content_active = segments.content_active_mask(
        frame,
        min_views=config.content_active_min_views,
        min_sections=config.content_active_min_sections,
    )
    reasons = segments.no_topic_profile_reason(
        frame,
        min_views=config.content_active_min_views,
        min_sections=config.content_active_min_sections,
    )
    # Metadata, never a feature: the model guard refuses it by the *_flag pattern.
    frame["content_active_flag"] = content_active
    topics.assert_share_closure(frame, content_active, config.bucket_map)

    return WeeklyFeatures(
        frame=frame,
        as_of_week_end=week_end,
        no_recent=no_recent,
        content_active=content_active,
        no_topic_reasons=reasons,
        resolved_view_share=topics.resolved_view_share(topic_atomics),
        completeness=check_completeness(
            config.bucket_map,
            topics.section_view_shares(inputs.reader_section_day, grid, week_end),
        ),
        out_of_population=spine_result.out_of_population,
        excluded=spine_result.excluded,
    )


def stack_weeks(weekly: list[WeeklyFeatures]) -> pd.DataFrame:
    """One frame over several weeks, with the deterministic labels carried along.

    The labels travel as columns because the panel sample and the fitting masks
    are taken over the stacked frame, and recomputing them from it would mean
    re-deriving a mask from features that have already been filled.
    """
    if not weekly:
        raise FeatureAssemblyError("no weeks to stack")
    frames = []
    for week in weekly:
        frame = week.frame.copy()
        frame["no_recent_flag"] = week.no_recent.to_numpy()
        frame["no_topic_profile_reason"] = week.no_topic_reasons.to_numpy()
        frames.append(frame)
    stacked = pd.concat(frames, ignore_index=True)
    duplicated = stacked.duplicated(subset=[READER_KEY, "as_of_week_end"])
    if bool(duplicated.any()):
        raise FeatureAssemblyError(
            f"{int(duplicated.sum())} duplicate reader-weeks after stacking; the same week "
            "has been built twice and every fit would double-weight those readers"
        )
    return stacked
