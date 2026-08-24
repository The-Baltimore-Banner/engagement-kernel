"""The week grid and the trailing windows every feature is measured over.

**The week anchor is read from the delivery's manifest, never from this module.**
That is the whole point of the file and the reason it carries no default. Two
conventions are in live use in the system this replaces, they differ by up to six
days, and nothing in that system states that both exist or tests the difference:

* one lane runs weeks that **end** on Sunday -- Monday through Sunday;
* another runs weeks that **start** on Sunday -- Sunday through Saturday.

The same calendar date is week-ending in one and mid-week in the other, so a port
that silently inherited the wrong convention would produce four weekly bins
shifted by six days against the day boundary. Every count would still be
plausible and no existing test would notice. So the anchor arrives as data
(:class:`WeekGrid.from_manifest`), a hand-built grid is checked just as hard, and
:mod:`engagement_kernel.engagement` has no module-level ``DEFAULT_WEEK_END_DAY``
for anything to fall back on.

The manifest declares the anchor as a weekday plus which end of the week it sits
on. This module resolves that pair into the one thing the feature code needs -- a
week-ending weekday -- once, here, so no downstream module has to know that the
``week_starts_on`` form exists.

Windows are inclusive on both ends and expressed in the local calendar days the
intermediate build emits, which are already in the manifest's declared timezone.
Nothing here re-derives a day boundary; doing so is how one channel ends up hours
away from the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from engagement_kernel.contract import enums
from engagement_kernel.contract.manifest import Manifest, WeekAnchor

#: Weekday name -> ``date.weekday()`` index. Built from the contract's own
#: vocabulary rather than written out, so a weekday the contract accepts can
#: never be one this module cannot resolve.
WEEKDAY_INDEX: dict[str, int] = {name: index for index, name in enumerate(enums.WEEKDAYS)}

#: Days in the long feature window. Four whole weeks, so the weekly bins tile it
#: exactly; a 30-day window would leave two days in no bin.
TRAILING_WINDOW_DAYS = 28

#: Number of weekly bins the long window is cut into.
WEEK_BIN_COUNT = 4


class WindowError(ValueError):
    """A window or week grid was asked for something it cannot mean.

    Raised rather than warned about, because every member of this class produces
    a run that completes: a snapshot taken mid-week still yields four bins and a
    28-day window, they are simply not the weeks anyone declared.
    """


@dataclass(frozen=True)
class WindowBounds:
    """An inclusive ``[start, end]`` range of local calendar days."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise WindowError(f"window start {self.start} is after its end {self.end}")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class WeekGrid:
    """Which weekday ends a week, resolved from a declaration.

    Constructed from a manifest in normal use. The direct constructor exists for
    tests and is validated in ``__post_init__`` rather than in the classmethod,
    so there is no path into the feature code that skips the check.
    """

    #: The weekday a week ends on, as a contract weekday name.
    week_end_day: str
    #: The declaration this grid came from, carried so a run can report which
    #: convention it used instead of leaving a reader to infer it from a date.
    declared_anchor: WeekAnchor

    def __post_init__(self) -> None:
        if self.week_end_day not in WEEKDAY_INDEX:
            raise WindowError(
                f"week_end_day {self.week_end_day!r} is not one of {list(enums.WEEKDAYS)}"
            )

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> WeekGrid:
        """Resolve the week grid from the delivery's own declaration.

        The only supported source. A deployment that wanted to override it would
        be running the model on a week that is not the week its data declares,
        which is exactly the divergence this repository exists to make
        impossible.
        """
        return cls.from_anchor(manifest.week_anchor)

    @classmethod
    def from_anchor(cls, anchor: WeekAnchor) -> WeekGrid:
        """Turn the declared ``(weekday, position)`` pair into a week-ending day.

        ``week_ends_on`` is the identity. ``week_starts_on`` resolves to the day
        before, so a week declared to start on Sunday ends on Saturday. Both
        forms are supported here, once, so nothing downstream branches on the
        position again.
        """
        if anchor.position == "week_ends_on":
            end_day = anchor.weekday
        elif anchor.position == "week_starts_on":
            start_index = WEEKDAY_INDEX[anchor.weekday]
            end_day = enums.WEEKDAYS[(start_index - 1) % len(enums.WEEKDAYS)]
        else:  # pragma: no cover - WeekAnchor validates the vocabulary
            raise WindowError(f"unsupported week anchor position {anchor.position!r}")
        return cls(week_end_day=end_day, declared_anchor=anchor)

    @property
    def week_end_index(self) -> int:
        """``date.weekday()`` value of the week-ending day."""
        return WEEKDAY_INDEX[self.week_end_day]

    def describe(self) -> str:
        return (
            f"{self.declared_anchor.position}={self.declared_anchor.weekday} "
            f"-> weeks end on {self.week_end_day}"
        )

    # --- the windows --------------------------------------------------------

    def validate_week_end(self, as_of_week_end: date) -> None:
        """Refuse a snapshot date that is not a week end under this grid.

        A mid-week snapshot still produces a full set of windows, so this is the
        only thing standing between a declared weekly cadence and a run whose
        "weeks" are arbitrary seven-day slices.
        """
        if as_of_week_end.weekday() != self.week_end_index:
            actual = enums.WEEKDAYS[as_of_week_end.weekday()]
            raise WindowError(
                f"as_of_week_end {as_of_week_end.isoformat()} is a {actual}, but this "
                f"delivery declares {self.describe()}. Weekly snapshots are taken on "
                "complete weeks; a mid-week snapshot yields windows nobody declared"
            )

    def current_week(self, as_of_week_end: date) -> WindowBounds:
        """The seven days ending on the snapshot, inclusive."""
        self.validate_week_end(as_of_week_end)
        return WindowBounds(as_of_week_end - timedelta(days=6), as_of_week_end)

    def trailing_window(self, as_of_week_end: date) -> WindowBounds:
        """The 28 days ending on the snapshot, inclusive."""
        self.validate_week_end(as_of_week_end)
        return WindowBounds(
            as_of_week_end - timedelta(days=TRAILING_WINDOW_DAYS - 1), as_of_week_end
        )

    def week_bins(self, as_of_week_end: date) -> tuple[WindowBounds, ...]:
        """Four non-overlapping weekly bins, bin 1 the most recent.

        They tile :meth:`trailing_window` exactly, which is what lets a bin sum
        be compared against a window total without a reconciliation step.
        """
        self.validate_week_end(as_of_week_end)
        return tuple(
            WindowBounds(
                as_of_week_end - timedelta(days=7 * index + 6),
                as_of_week_end - timedelta(days=7 * index),
            )
            for index in range(WEEK_BIN_COUNT)
        )

    def complete_week_ends(self, start: date, end: date) -> list[date]:
        """Every week end in ``[start, end]`` whose whole week lies inside it.

        A partial current week never produces a snapshot: its counts are lower
        than a full week's for a reason that has nothing to do with the readers.
        """
        if start > end:
            raise WindowError(f"start {start} is after end {end}")
        first_candidate = start + timedelta(days=6)
        offset = (self.week_end_index - first_candidate.weekday()) % len(enums.WEEKDAYS)
        week_end = first_candidate + timedelta(days=offset)
        out: list[date] = []
        while week_end <= end:
            out.append(week_end)
            week_end += timedelta(days=7)
        return out

    def week_ends_with_full_window(self, start: date, end: date) -> list[date]:
        """Week ends whose whole 28-day feature window lies inside ``[start, end]``.

        Narrower than :meth:`complete_week_ends`, and the right bound for a fit:
        a week whose window reaches back before the data begins has three real
        bins and one empty one, which reads as a reader who went quiet.
        """
        return [
            week_end
            for week_end in self.complete_week_ends(start, end)
            if self.trailing_window(week_end).start >= start
        ]


def window_mask(
    frame: pd.DataFrame,
    bounds: WindowBounds,
    date_column: str,
) -> pd.Series:
    """Boolean mask for rows whose ``date_column`` lies in the inclusive window.

    ``date_column`` is required rather than defaulted. The intermediate tables
    name their day column one thing and a caller passing a frame with two date
    columns should have to say which one it means.
    """
    if date_column not in frame.columns:
        raise WindowError(f"frame has no {date_column!r} column to window on")
    dates = pd.to_datetime(frame[date_column]).dt.date
    return (dates >= bounds.start) & (dates <= bounds.end)


def assert_no_future_dates(
    frame: pd.DataFrame,
    as_of_week_end: date,
    date_column: str,
) -> None:
    """No row may postdate the snapshot it is being used to build.

    A future row does not raise anywhere else: it lands outside every window and
    is silently ignored, so a delivery accidentally including tomorrow's partial
    day produces a snapshot that is right by luck.
    """
    dates = pd.to_datetime(frame[date_column]).dt.date
    future = dates > as_of_week_end
    if bool(future.any()):
        raise WindowError(
            f"{int(future.sum())} rows carry {date_column} after as_of_week_end "
            f"{as_of_week_end.isoformat()}"
        )
