"""The week grid, and the negative control that proves it reads the declaration.

The window layer takes its week anchor from the delivery's manifest and not from
ported code. That claim needs a control, because a module that ignored the manifest
and used a hardcoded Sunday would pass every test written against a Sunday-anchored
delivery -- and the delivery this repository ships is Sunday-anchored.

So :func:`test_changing_the_declared_anchor_moves_the_weekly_bins` changes the
declaration and requires the bins to move. It is the whole point of the file.

The two conventions differ by up to six days and both are in live use: one lane runs
weeks ending Sunday, another runs weeks starting Sunday. The same calendar date is a
week end in the first and mid-week in the second.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pandas as pd
import pytest

from engagement_kernel.contract.manifest import WeekAnchor
from engagement_kernel.engagement.windows import (
    TRAILING_WINDOW_DAYS,
    WEEK_BIN_COUNT,
    WeekGrid,
    WindowBounds,
    WindowError,
    assert_no_future_dates,
    window_mask,
)


def grid(weekday: str, position: str) -> WeekGrid:
    return WeekGrid.from_anchor(WeekAnchor(weekday=weekday, position=position))


def test_week_ends_on_resolves_to_that_weekday() -> None:
    assert grid("Sunday", "week_ends_on").week_end_day == "Sunday"
    assert grid("Wednesday", "week_ends_on").week_end_day == "Wednesday"


def test_week_starts_on_resolves_to_the_day_before() -> None:
    """A week declared to start on Sunday ends on Saturday.

    Resolved once, here, so nothing downstream branches on the position again -- and
    so a delivery using the other convention is not silently read as the first.
    """
    assert grid("Sunday", "week_starts_on").week_end_day == "Saturday"
    assert grid("Monday", "week_starts_on").week_end_day == "Sunday"


def test_the_two_conventions_disagree_about_the_same_date() -> None:
    """The failure this whole design exists to prevent, stated as a test."""
    a_sunday = date(2026, 6, 28)
    ends_sunday = grid("Sunday", "week_ends_on")
    starts_sunday = grid("Sunday", "week_starts_on")

    ends_sunday.validate_week_end(a_sunday)  # a week end under the first convention
    with pytest.raises(WindowError) as exc:
        starts_sunday.validate_week_end(a_sunday)  # mid-week under the second
    assert "week_starts_on" in str(exc.value)
    assert "Sunday" in str(exc.value)


def test_a_midweek_snapshot_is_refused() -> None:
    """A mid-week snapshot yields full windows that are not the weeks anyone declared."""
    with pytest.raises(WindowError):
        grid("Sunday", "week_ends_on").validate_week_end(date(2026, 6, 24))


def test_the_windows_tile_exactly() -> None:
    """The four bins cover the long window with no gap and no overlap."""
    week_grid = grid("Sunday", "week_ends_on")
    week_end = date(2026, 6, 28)
    window = week_grid.trailing_window(week_end)
    bins = week_grid.week_bins(week_end)

    assert window.n_days == TRAILING_WINDOW_DAYS
    assert len(bins) == WEEK_BIN_COUNT
    assert sum(bound.n_days for bound in bins) == TRAILING_WINDOW_DAYS
    assert bins[0].end == week_end
    assert bins[-1].start == window.start
    covered = {bound.start.toordinal() + offset for bound in bins for offset in range(bound.n_days)}
    assert len(covered) == TRAILING_WINDOW_DAYS


def test_changing_the_declared_anchor_moves_the_weekly_bins() -> None:
    """The negative control. Same data, different declaration, different bins.

    A window layer with a hardcoded Sunday would produce identical bins under both
    declarations and this would fail -- which is exactly what it is for.
    """
    # Wide enough to cover both conventions' 28-day windows in full. A frame that
    # only covered one of them would make the two totals differ for want of a day,
    # which says nothing about the anchor.
    days = pd.DataFrame(
        {"local_date": [date(2026, 5, 20) + pd.Timedelta(days=offset) for offset in range(60)]}
    )
    days["value"] = 1.0

    # The same snapshot date is a week end under one declaration and not the other, so
    # the control compares each convention on its own nearest week end -- the thing a
    # real run would do -- rather than forcing one convention onto the other's date.
    ends_sunday = grid("Sunday", "week_ends_on")
    starts_sunday = grid("Sunday", "week_starts_on")
    sunday_end = date(2026, 6, 28)
    saturday_end = date(2026, 6, 27)

    sunday_bins = ends_sunday.week_bins(sunday_end)
    saturday_bins = starts_sunday.week_bins(saturday_end)

    assert sunday_bins != saturday_bins
    assert sunday_bins[0].start != saturday_bins[0].start

    sunday_counts = [int(window_mask(days, bound, "local_date").sum()) for bound in sunday_bins]
    saturday_counts = [int(window_mask(days, bound, "local_date").sum()) for bound in saturday_bins]
    # Both tile 28 days, so the totals match; what moves is which day lands in which
    # bin -- and that is what a weekly feature is built from.
    assert sum(sunday_counts) == sum(saturday_counts) == TRAILING_WINDOW_DAYS
    assert ends_sunday.trailing_window(sunday_end).start != (
        starts_sunday.trailing_window(saturday_end).start
    )


def test_changing_the_declared_anchor_moves_a_readers_bin_values(
    weekly_inputs, lane_config
) -> None:
    """The same control, at the level the acceptance criterion asks about.

    Real intermediate tables, one declaration changed, and the weekly bin features
    for real readers come out different. This is the end-to-end form: it exercises
    the manifest, the config resolution, the atomic builders and the bin arithmetic
    together, so a hardcoded anchor anywhere in that path fails it.
    """
    from engagement_kernel.engagement.atomics import build_consumption_atomics

    declared = lane_config.week_grid
    other = WeekGrid.from_anchor(
        WeekAnchor(weekday=declared.declared_anchor.weekday, position="week_starts_on")
    )
    assert other.week_end_day != declared.week_end_day

    # Each grid is asked about its own nearest week end, six days apart at most.
    channel = lane_config.channels[0]
    declared_week = declared.complete_week_ends(date(2026, 3, 1), date(2026, 6, 28))[-1]
    other_week = other.complete_week_ends(date(2026, 3, 1), date(2026, 6, 28))[-1]
    assert declared_week != other_week

    left = build_consumption_atomics(
        weekly_inputs.reader_channel_day, declared, declared_week, channel
    ).set_index("reader_id")
    right = build_consumption_atomics(
        weekly_inputs.reader_channel_day, other, other_week, channel
    ).set_index("reader_id")

    shared = left.index.intersection(right.index)
    assert len(shared) > 10, "not enough readers in both windows to compare"
    bin_column = f"{channel}_top_week_share_4"
    differences = (left.loc[shared, bin_column] - right.loc[shared, bin_column]).abs()
    assert (differences > 1e-9).any(), (
        "the weekly bin features are identical under two different declared anchors, "
        "which means the window layer is not reading the declaration"
    )


def test_a_full_window_is_required_not_just_a_complete_week() -> None:
    """A week whose window reaches back before the data is not scored.

    It would have three real bins and one empty one, which reads as a reader who went
    quiet rather than as a period nobody has data for.
    """
    week_grid = grid("Sunday", "week_ends_on")
    start, end = date(2026, 6, 1), date(2026, 7, 5)
    complete = week_grid.complete_week_ends(start, end)
    full = week_grid.week_ends_with_full_window(start, end)
    assert complete, "expected at least one complete week in the period"
    assert len(full) < len(complete)
    for week_end in full:
        assert week_grid.trailing_window(week_end).start >= start


def test_a_future_row_is_refused_rather_than_ignored() -> None:
    """A future row lands outside every window and is otherwise silently dropped."""
    frame = pd.DataFrame({"local_date": [date(2026, 6, 27), date(2026, 6, 30)]})
    with pytest.raises(WindowError) as exc:
        assert_no_future_dates(frame, date(2026, 6, 28), "local_date")
    assert "after as_of_week_end" in str(exc.value)


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(WindowError):
        WindowBounds(date(2026, 6, 28), date(2026, 6, 1))


def test_the_grid_reports_which_convention_it_resolved(lane_config) -> None:
    """The port notes have to say which convention was replaced; so does a run."""
    described = lane_config.week_grid.describe()
    assert lane_config.week_grid.declared_anchor.position in described
    assert lane_config.week_grid.week_end_day in described
    assert described in lane_config.describe()


def test_the_grid_has_no_module_level_default() -> None:
    """There must be nothing for a caller to fall back on.

    A ``DEFAULT_WEEK_END_DAY`` constant is how the wrong convention gets inherited:
    one caller omits the argument and the whole lane silently uses the other lane's
    week.
    """
    from engagement_kernel.engagement import windows

    assert not [name for name in dir(windows) if "DEFAULT_WEEK" in name]
    with pytest.raises(TypeError):
        WeekGrid.from_anchor()  # type: ignore[call-arg]


def test_a_hand_built_grid_is_validated_too() -> None:
    """The check is in ``__post_init__``, so there is no path around it."""
    with pytest.raises(WindowError):
        dataclasses.replace(grid("Sunday", "week_ends_on"), week_end_day="Someday")
