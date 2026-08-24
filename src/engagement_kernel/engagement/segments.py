"""The deterministic segments, which exist outside every fitted model.

Two labels are decided by a rule rather than by a model, and both matter because
the alternative is a model asked to describe readers it has no information about.

``NO_RECENT_OBSERVED_ENGAGEMENT``
    Every activity anchor is zero across the whole window. These readers are
    excluded from the fit and labelled deterministically. Leaving them in would
    hand k-means a large, perfectly tight cluster at the origin, and it would
    spend one of its k on it -- so a run with k=5 would really be describing the
    active population with four clusters, and nothing in the output would say so.

``NO_TOPIC_PROFILE``
    Not enough resolved reading for a topic mix to mean anything. Carried with a
    reason code, because the three ways to get here are different facts: the
    reader read nothing, the reader read things whose metadata did not resolve, or
    the reader read a little. The middle one is a data-quality signal about the
    publisher and the other two are about the reader, and a single label would
    make a metadata outage look like an audience that stopped reading.

The anchors are passed in rather than hardcoded. Which channels exist is a
property of the delivery -- a publisher with no community feed has no community
anchor -- and hardcoding four would test a column of zeros for a channel nobody
delivers, which makes every reader look inactive on it.
"""

from __future__ import annotations

import pandas as pd

NO_RECENT_SEGMENT = "NO_RECENT_OBSERVED_ENGAGEMENT"
NO_TOPIC_PROFILE = "NO_TOPIC_PROFILE"

#: Why a reader has no topic profile. Distinct values because the remedies differ.
REASON_NO_READING = "no_reading"
REASON_UNRESOLVED_METADATA = "unresolved_metadata"
REASON_BELOW_THRESHOLD = "below_threshold"


def no_recent_mask(frame: pd.DataFrame, anchors: tuple[str, ...]) -> pd.Series:
    """True where every anchor is zero over the window."""
    missing = [anchor for anchor in anchors if anchor not in frame.columns]
    if missing:
        raise KeyError(
            f"activity anchors absent from the feature frame: {missing}. Testing a column "
            "that is not there would silently treat that channel as inactive for everyone"
        )
    mask = pd.Series(True, index=frame.index)
    for anchor in anchors:
        mask &= frame[anchor].fillna(0) == 0
    return mask


def clusterable_mask(frame: pd.DataFrame, anchors: tuple[str, ...]) -> pd.Series:
    """True where the reader has any observed activity in the window."""
    return ~no_recent_mask(frame, anchors)


def content_active_mask(
    topic_counts: pd.DataFrame,
    *,
    min_views: int,
    min_sections: int,
) -> pd.Series:
    """True where the reader has enough resolved reading for a topic mix.

    Both conditions are needed. Views alone admits a reader who read six pieces
    from one section, whose "mix" is 100% one bucket -- a fact about the section,
    not a preference. Sections alone admits a reader with two views total.
    """
    return (topic_counts["resolved_section_views_28d"].fillna(0) >= min_views) & (
        topic_counts["distinct_sections_28d"].fillna(0) >= min_sections
    )


def no_topic_profile_reason(
    topic_counts: pd.DataFrame,
    *,
    min_views: int,
    min_sections: int,
) -> pd.Series:
    """Reason code per reader; ``None`` where the reader is content-active."""
    resolved = topic_counts["resolved_section_views_28d"].fillna(0)
    unresolved = topic_counts["unresolved_views_28d"].fillna(0)
    active = content_active_mask(topic_counts, min_views=min_views, min_sections=min_sections)
    reasons = pd.Series(REASON_BELOW_THRESHOLD, index=topic_counts.index, dtype=object)
    reasons[(resolved == 0) & (unresolved > 0)] = REASON_UNRESOLVED_METADATA
    reasons[(resolved == 0) & (unresolved == 0)] = REASON_NO_READING
    reasons[active] = None
    return reasons
