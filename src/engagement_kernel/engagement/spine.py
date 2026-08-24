"""The spine: which readers are scored in a week, resolved from state history.

The spine is built first and activity is left-joined onto it, so the feature
table has exactly one row per scored reader per week whether or not that reader
did anything. Building it the other way round -- starting from activity and
adding the readers -- silently drops every inactive reader, and the population
then moves with engagement rather than with entitlement.

This module replaces **two** mechanisms with one. The system it ports from
resolved the population differently on its two paths: an entitlement-span table
on the fitting path, and a publisher-specific subscriber taxonomy on the serving
path. Two resolutions of one question is one resolution too many -- they can
disagree, and when they do the model is fit on one population and scored on
another. Here there is a single resolution, from a single input: the contract's
``subscription_span`` history, evaluated as of a date against the states the
manifest declares in-population.

Three consequences of doing it that way are worth stating, because each replaces
something the old system did differently.

**Subscriber type is gone.** The old fit ran on individually-paying subscribers
only and *projected* labels onto guests and institutions -- a taxonomy the
contract deliberately cannot express, because ``payer_type`` is optional and a
publisher whose billing system cannot distinguish payer types must supply null.
What replaces it is window completeness: the fit runs on readers entitled for the
whole feature window, and readers entitled for only part of it are scored with a
``projected_flag``. That is a distinction the contract can always express, it is
the distinction that actually matters to the arithmetic -- a partial window has
fewer days to accumulate activity in -- and it does not require the publisher to
have a payer taxonomy at all.

**State is spine metadata and never a feature.** It decides who is in the
population; it is refused at the model matrix by name. A cluster of subscription
state is trivially findable, perfectly stable, and worthless.

**Exclusions are a list of opaque ids, not predicates.** The old population
definition carried four hardcoded exclusion predicates, two of them matching
fragments of personal email addresses. The contract carries no personal field, so
such a predicate is not expressible against it; the manifest takes a list of
reader ids instead, and refuses an entry that looks like a personal identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from engagement_kernel.engagement.config import LaneConfig
from engagement_kernel.engagement.windows import TRAILING_WINDOW_DAYS, WindowBounds

#: Columns of the spine, in order. Every one of them is metadata: not one is a
#: model feature, and the model guard refuses each by name or by pattern.
SPINE_COLUMNS: tuple[str, ...] = (
    "reader_id",
    "as_of_week_end",
    "state",
    "payer_type",
    "entitled_days_in_window",
    "partial_window_flag",
    "projected_flag",
)


class SpineError(ValueError):
    """The spine could not be built from what was supplied."""


def _normalise_spans(intervals: pd.DataFrame) -> pd.DataFrame:
    """Take the local-date columns the intermediate build emits and check them.

    ``start_date`` is inclusive and ``end_date`` is exclusive -- the first day the
    span does *not* cover. Carried through rather than converted, because the
    off-by-one that comes from treating an exclusive bound as inclusive is exactly
    one day of entitlement per span and never looks wrong.
    """
    required = {"reader_id", "state", "start_date", "end_date"}
    missing = sorted(required - set(intervals.columns))
    if missing:
        raise SpineError(
            f"subscription_state_interval is missing columns the spine needs: {missing}"
        )
    frame = intervals.copy()
    frame["start_date"] = _as_dates(frame["start_date"])
    frame["end_date"] = _as_dates(frame["end_date"])
    if "payer_type" not in frame.columns:
        frame["payer_type"] = None
    return frame


def _as_dates(values: pd.Series) -> pd.Series:
    """Coerce a day column to plain ``date`` objects, with ``None`` for a null.

    Explicitly object-dtype rather than left as whatever pandas inferred. An
    all-null day column -- every span still open, which is the common case for a
    delivery whose subscribers have not churned -- stays ``datetime64`` through
    ``.dt.date``, and comparing that against a ``date`` raises rather than
    returning False. The failure is loud here and would be, so this is about the
    message rather than the correctness: coercing once means the comparison below
    reads as the interval arithmetic it is.
    """
    converted = pd.to_datetime(values, errors="coerce")
    return pd.Series(
        [None if pd.isna(value) else value.date() for value in converted],
        index=values.index,
        dtype="object",
    )


def _covered_days(
    spans: list[tuple[date, date | None]],
    window: WindowBounds,
) -> int:
    """Days of the inclusive window covered by any span, counting overlaps once.

    Spans are merged before counting. A reader who cancelled and resubscribed
    inside one window has two overlapping-at-the-edges spans, and summing their
    lengths would credit them with more days than the window has -- which then
    makes ``partial_window_flag`` false for a reader who was not entitled
    throughout.
    """
    clipped: list[tuple[date, date]] = []
    for start, end_exclusive in spans:
        # end_exclusive is the first uncovered day; the last covered day is the
        # day before it. An open span runs to the end of the window.
        last_covered = window.end if end_exclusive is None else end_exclusive - timedelta(days=1)
        low = max(start, window.start)
        high = min(last_covered, window.end)
        if low <= high:
            clipped.append((low, high))
    if not clipped:
        return 0
    clipped.sort()
    merged: list[list[date]] = [list(clipped[0])]
    for low, high in clipped[1:]:
        if low <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return sum((high - low).days + 1 for low, high in merged)


@dataclass(frozen=True)
class SpineResult:
    """One week's spine, with the counts a run should report about it."""

    frame: pd.DataFrame
    as_of_week_end: date
    #: Readers with a state history who were not in an entitled state on the day.
    out_of_population: int
    #: Readers removed by the manifest's exclusion list.
    excluded: int

    @property
    def n_rows(self) -> int:
        return len(self.frame)


def build_spine(
    intervals: pd.DataFrame,
    as_of_week_end: date,
    config: LaneConfig,
) -> SpineResult:
    """Resolve the scored population as of one week end.

    A reader is in the population when a span covering ``as_of_week_end`` carries
    one of the manifest's declared entitled states. A reader with no span at all
    is *not* in the population and is not in it with a zero either: state unknown
    and state known-and-unentitled are different facts, and the contract keeps
    them apart on purpose.
    """
    config.week_grid.validate_week_end(as_of_week_end)
    window = config.week_grid.trailing_window(as_of_week_end)
    frame = _normalise_spans(intervals)
    entitled_states = set(config.entitled_states)
    excluded_ids = set(config.population_exclusions)

    rows: list[dict[str, object]] = []
    out_of_population = 0
    excluded = 0
    for reader_id, group in frame.groupby("reader_id", dropna=False, sort=True):
        if reader_id in excluded_ids:
            excluded += 1
            continue

        # The span covering the evaluation date decides the state. There is at
        # most one: the contract's spans are half-open and the validator refuses
        # an overlap, so a reader has one state on any given day.
        covering = group.loc[
            (group["start_date"] <= as_of_week_end)
            & (group["end_date"].isna() | (group["end_date"] > as_of_week_end))
        ]
        if covering.empty:
            out_of_population += 1
            continue
        current = covering.sort_values("start_date").iloc[-1]
        state = str(current["state"])
        if state not in entitled_states:
            out_of_population += 1
            continue

        entitled_spans = [
            (start, None if pd.isna(end) else end)
            for start, end, span_state in zip(
                group["start_date"], group["end_date"], group["state"], strict=True
            )
            if str(span_state) in entitled_states and not pd.isna(start)
        ]
        days = _covered_days(entitled_spans, window)
        rows.append(
            {
                "reader_id": reader_id,
                "as_of_week_end": as_of_week_end,
                "state": state,
                "payer_type": current["payer_type"],
                "entitled_days_in_window": days,
                "partial_window_flag": days < TRAILING_WINDOW_DAYS,
                # A partial-window reader is scored but not fit on, so their label
                # is a projection from a model that never saw a row like theirs.
                # Published so a consumer can tell the two apart.
                "projected_flag": int(days < TRAILING_WINDOW_DAYS),
            }
        )

    spine = pd.DataFrame(rows, columns=list(SPINE_COLUMNS))
    if spine.duplicated(subset=["reader_id", "as_of_week_end"]).any():
        raise SpineError(
            "the spine has more than one row for a reader-week, which would double every "
            "count joined onto it while leaving each individual number plausible"
        )
    return SpineResult(
        frame=spine,
        as_of_week_end=as_of_week_end,
        out_of_population=out_of_population,
        excluded=excluded,
    )


def fit_cohort_mask(spine: pd.DataFrame) -> pd.Series:
    """Readers whose whole feature window was entitled -- the fitting population.

    Full-window only, because a partial window is a shorter accumulation period
    and fitting on a mixture of window lengths teaches the model that recent
    subscribers are light readers.
    """
    return ~spine["partial_window_flag"].astype(bool)


def scored_population_mask(spine: pd.DataFrame) -> pd.Series:
    """Every row of the spine is scored.

    Trivially true, and here as a named function anyway: the fitting population
    and the scored population are different, this is the one place that says so,
    and a reader of the pipeline should not have to infer that partial-window rows
    are scored from the absence of a filter.
    """
    return pd.Series(True, index=spine.index)


def assert_sources_fresh(
    latest_local_date: dict[str, date],
    as_of_week_end: date,
) -> list[str]:
    """Name the inputs that do not reach the week being scored.

    Returns the stale ones rather than raising, so a run can put them in its gate
    report instead of stopping -- but they are *named*, because an input that
    stops two days before the week end produces a week whose last two days are
    empty for everybody, and that reads as an audience that went quiet.
    """
    return sorted(
        f"{name} (through {value.isoformat()})"
        for name, value in latest_local_date.items()
        if value < as_of_week_end
    )
