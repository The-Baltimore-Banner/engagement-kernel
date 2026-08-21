"""One timezone, applied once, to every channel -- proved against Python.

The defect this guards is the largest silent-wrongness risk in the system being
replaced: web and app converted to the publisher's zone, email and community left
in whatever zone the vendor sent. For an ISO-8601 ``Z`` timestamp that puts email
days four to five hours ahead of reading days, so an evening click lands on the
next day and a Saturday-evening click lands in the following week's bin. Nothing
raises. Every window is mis-bucketed for one channel and every number stays
plausible.

Two independent things are asserted here, and neither is a restatement of the
build's own checks.

**Against a different implementation.** The expected local day is computed with
Python's ``zoneinfo``, not with DuckDB. The build's in-run checks recompute the
day with DuckDB's own ``AT TIME ZONE``, which catches a statement that stopped
converting -- but could not catch DuckDB and the standard library disagreeing
about a zone. These tests can.

**Under three session timezones.** DuckDB evaluates a bare
``CAST(timestamptz AS DATE)`` in the session zone, which defaults to the host's.
A build containing such a cast is right on a laptop set to the publisher's zone
and wrong everywhere else, which is the worst case because the defect ships. So
the whole build runs under three session zones and the output must be byte-for-
byte identical.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from engagement_kernel.contract import demo, spec
from engagement_kernel.contract.manifest import parse_manifest
from engagement_kernel.intermediate import build, session, tables
from engagement_kernel.intermediate.config import BuildConfig

DELIVERY = "examples/demo-delivery"
PUBLISHER_ZONE = ZoneInfo(demo.DEMO_TIMEZONE)


def _expected_local_date(instant: datetime) -> date:
    """The day the publisher's zone puts an instant on, per the standard library."""
    return instant.astimezone(PUBLISHER_ZONE).date()


# --- the fixture has something to discriminate against ----------------------


def test_the_delivery_carries_a_near_midnight_row_on_every_channel() -> None:
    """Without this, every assertion below would pass on an unconverted build.

    A day-boundary test over rows that sit in the middle of the local day proves
    nothing: the converted and unconverted answers are the same. So the first
    thing to check is that the fixture actually distinguishes them, per channel.
    """
    covered = {example.table for example in demo.DAY_BOUNDARY_EVENTS}
    assert covered == {"reader_event", "email_open", "community_action"}

    discriminating = {
        example.table
        for example in demo.DAY_BOUNDARY_EVENTS
        if example.local_dates[demo.DEMO_TIMEZONE] != example.local_dates["UTC"]
    }
    assert discriminating == {"reader_event", "email_open", "community_action"}, (
        "every channel needs a row whose local day differs from its UTC day, or that "
        "channel's boundary check has nothing to catch"
    )


def test_the_email_click_feed_also_crosses_a_boundary() -> None:
    """Clicks are the modelling signal, so they need their own crossing row.

    ``DAY_BOUNDARY_EVENTS`` carries the open-side case because that is the one a
    consumer is most likely to forget; this asserts the click side is covered
    too, which it is by ``ecl-0007``.
    """
    clicks = demo.build_tables()["email_click"]
    crossing = [
        row
        for row in clicks.to_pylist()
        if _expected_local_date(row["event_ts"]) != row["event_ts"].date()
    ]
    assert crossing, "no email click crosses the local day boundary"


# --- every channel lands on the expected local day --------------------------


@pytest.mark.parametrize(
    "example", demo.DAY_BOUNDARY_EVENTS, ids=lambda item: f"{item.table}:{item.event_id}"
)
def test_the_declared_boundary_claims_hold(example: demo.DayBoundaryExample) -> None:
    """Each worked example in the delivery says which day it lands on, per zone.

    Recomputed here from the instant. A claim in a fixture that nothing checks is
    a comment.
    """
    instant = datetime.fromisoformat(example.instant).replace(tzinfo=ZoneInfo("UTC"))
    for zone, claimed in example.local_dates.items():
        actual = instant.astimezone(ZoneInfo(zone)).date().isoformat()
        assert actual == claimed, f"{example.event_id} in {zone}: claimed {claimed}, is {actual}"


def test_reader_event_days_match_the_publisher_zone() -> None:
    """Every consumption row's day, checked against zoneinfo event by event."""
    result = build.build_delivery(DELIVERY)
    events = demo.build_tables()["reader_event"].to_pylist()
    by_reader_channel: dict[tuple[str, str], set[date]] = {}
    view_kinds = set(result.config.article_view.event_kinds)
    view_types = set(result.config.article_view.content_types)
    content_types = {
        row["content_id"]: row["content_type"] for row in demo.build_tables()["content"].to_pylist()
    }
    for event in events:
        if event["event_kind"] not in view_kinds or event["content_id"] is None:
            continue
        content_type = content_types.get(event["content_id"])
        if content_type is not None and content_type not in view_types:
            continue
        key = (event["reader_id"], event["channel"])
        by_reader_channel.setdefault(key, set()).add(_expected_local_date(event["event_ts"]))

    built: dict[tuple[str, str], set[date]] = {}
    for row in result.table(tables.READER_CHANNEL_DAY.name).to_pylist():
        built.setdefault((row["reader_id"], row["channel"]), set()).add(row["local_date"])
    assert built == by_reader_channel


def test_the_evening_read_stays_on_the_evening_day() -> None:
    """evt-boundary-1 is 03:15 UTC, which is 22:15 the previous day in the zone.

    A build that never converted would put it on the 17th. It belongs on the
    16th, and it belongs on the same reader-day as that reader's other reading --
    which is what makes the session count 2 rather than 3.
    """
    result = build.build_delivery(DELIVERY)
    days = {
        row["local_date"]
        for row in result.table(tables.READER_CHANNEL_DAY.name).to_pylist()
        if row["reader_id"] == demo.READER_FULL_HISTORY and row["channel"] == "web"
    }
    assert date(2026, 2, 16) in days
    assert date(2026, 2, 17) not in days


def test_the_sunday_evening_read_stays_in_sunday() -> None:
    """evt-boundary-2 changes week as well as day under a Sunday-ending anchor.

    2026-03-09 02:30 UTC is Sunday 2026-03-08 21:30 in the publisher's zone.
    Unconverted it becomes Monday, which moves it into the next weekly bin -- the
    failure that survives every plausibility check downstream.
    """
    result = build.build_delivery(DELIVERY)
    days = {
        row["local_date"]
        for row in result.table(tables.READER_CHANNEL_DAY.name).to_pylist()
        if row["reader_id"] == demo.READER_LAPSING and row["channel"] == "web"
    }
    assert date(2026, 3, 8) in days
    assert date(2026, 3, 9) not in days
    assert date(2026, 3, 8).isoweekday() == 7


def test_email_days_match_the_publisher_zone_for_clicks_and_opens() -> None:
    """The channel the upstream system leaves unconverted, both halves of it."""
    result = build.build_delivery(DELIVERY)
    expected: dict[tuple[str, str], set[date]] = {}
    for table_name in ("email_click", "email_open"):
        for row in demo.build_tables()[table_name].to_pylist():
            key = (row["reader_id"], row["list_id"])
            expected.setdefault(key, set()).add(_expected_local_date(row["event_ts"]))
    built: dict[tuple[str, str], set[date]] = {}
    for row in result.table(tables.READER_EMAIL_DAY.name).to_pylist():
        built.setdefault((row["reader_id"], row["list_id"]), set()).add(row["local_date"])
    assert built == expected


def test_the_late_evening_click_stays_on_the_evening_day() -> None:
    """ecl-0007 at 02:31 UTC is 21:31 on the Sunday in the publisher's zone."""
    result = build.build_delivery(DELIVERY)
    days = {
        row["local_date"]
        for row in result.table(tables.READER_EMAIL_DAY.name).to_pylist()
        if row["reader_id"] == demo.READER_LAPSING
    }
    assert date(2026, 3, 8) in days
    assert date(2026, 3, 9) not in days


def test_the_late_evening_open_stays_on_the_evening_day() -> None:
    """eop-0007, on a day this reader did not click at all.

    So the open side of the email feed carries the whole weight of the assertion:
    a build that converts clicks and forgets opens is caught here and nowhere
    else.
    """
    result = build.build_delivery(DELIVERY)
    row = next(
        item
        for item in result.table(tables.READER_EMAIL_DAY.name).to_pylist()
        if item["reader_id"] == demo.READER_FULL_HISTORY and item["local_date"] == date(2026, 3, 8)
    )
    assert row["opens"] == 1
    assert row["clicks"] == 0, "the assertion rests on the open side, so there must be no click"


def test_community_days_match_the_publisher_zone() -> None:
    """The other unconverted channel."""
    result = build.build_delivery(DELIVERY)
    expected: dict[str, set[date]] = {}
    for row in demo.build_tables()["community_action"].to_pylist():
        expected.setdefault(row["reader_id"], set()).add(_expected_local_date(row["event_ts"]))
    built: dict[str, set[date]] = {}
    for row in result.table(tables.READER_COMMUNITY_DAY.name).to_pylist():
        built.setdefault(row["reader_id"], set()).add(row["local_date"])
    assert built == expected


def test_the_late_evening_community_action_stays_on_the_evening_day() -> None:
    """cmt-0007 at 02:35 UTC is 21:35 on the Sunday in the publisher's zone."""
    result = build.build_delivery(DELIVERY)
    row = next(
        item
        for item in result.table(tables.READER_COMMUNITY_DAY.name).to_pylist()
        if item["reader_id"] == demo.READER_FULL_HISTORY and item["local_date"] == date(2026, 3, 8)
    )
    assert row["replies_created"] == 1


def test_subscription_span_local_dates_match_the_publisher_zone() -> None:
    """Spans get local dates too, and from the same zone as everything else.

    A window expressed in local days is resolved against these, so a span
    bucketed in a different zone is a one-day entitlement error on one table
    only.
    """
    result = build.build_delivery(DELIVERY)
    for row in result.table(tables.SUBSCRIPTION_STATE_INTERVAL.name).to_pylist():
        assert row["start_date"] == _expected_local_date(row["start_ts"])
        if row["end_ts"] is None:
            assert row["end_date"] is None
        else:
            assert row["end_date"] == _expected_local_date(row["end_ts"])


# --- the session zone must not matter ---------------------------------------


@pytest.mark.parametrize("session_zone", ["UTC", "America/New_York", "Asia/Tokyo"])
def test_the_build_is_identical_under_any_session_timezone(session_zone: str) -> None:
    """A bare cast would make this fail on two of the three.

    ``America/New_York`` is in the list on purpose: it is the publisher's own
    zone, so a build relying on the session default is *correct* there. Testing
    only that zone is how the defect ships.
    """
    import duckdb

    connection = duckdb.connect()
    connection.execute(f"SET TimeZone='{session_zone}'")
    manifest = parse_manifest(demo.build_manifest())
    config = BuildConfig.from_manifest(manifest)
    result = build.build_from_arrow(
        demo.build_tables(), config, manifest=manifest, connection=connection
    )
    reference = build.build_delivery(DELIVERY)
    for name in reference.tables:
        assert result.table(name).equals(reference.table(name)), name


def test_the_session_zone_is_pinned_rather_than_inherited() -> None:
    """Pinning does not make a stray cast correct; it makes it visibly wrong."""
    import duckdb

    connection = duckdb.connect()
    connection.execute("SET TimeZone='Asia/Tokyo'")
    con = session.connect(connection=connection)
    assert session.scalar(con, "SELECT current_setting('TimeZone')") == session.SESSION_TIMEZONE


# --- the zone itself is required, never guessed -----------------------------


def test_there_is_no_default_timezone_anywhere() -> None:
    """Checked by absence *and* by refusal, because absence alone is weak.

    A default added later would not fail a test that only greps the source.
    """
    from engagement_kernel.contract.manifest import ManifestError

    raw = demo.build_manifest()
    del raw["day_boundary_timezone"]
    with pytest.raises(ManifestError) as exc:
        parse_manifest(raw)
    assert "day_boundary_timezone" in str(exc.value)


def test_a_config_built_by_hand_still_refuses_a_missing_zone() -> None:
    from engagement_kernel.intermediate.config import BuildConfigError

    manifest = parse_manifest(demo.build_manifest())
    with pytest.raises(BuildConfigError) as exc:
        BuildConfig(day_boundary_timezone="", article_view=manifest.article_view)
    assert "no default" in str(exc.value)


def test_an_unknown_zone_is_refused_rather_than_falling_back() -> None:
    from engagement_kernel.intermediate.config import BuildConfigError

    manifest = parse_manifest(demo.build_manifest())
    with pytest.raises(BuildConfigError) as exc:
        BuildConfig(day_boundary_timezone="Publisher/Newsroom", article_view=manifest.article_view)
    assert "not a known IANA timezone" in str(exc.value)


def test_the_contract_rejects_a_pre_bucketed_date_on_the_way_in() -> None:
    """The whole design rests on this, so it is asserted rather than assumed.

    The engine emits ``local_date``. The contract refuses a column by that name
    on input, because a producer supplying one has already applied some zone and
    no validator can recover which.
    """
    from engagement_kernel.contract.validate import _forbidden_reason

    reason = _forbidden_reason(tables.LOCAL_DATE_COLUMN)
    assert reason is not None
    assert "day-boundary" in reason


def test_every_contract_timestamp_is_timezone_aware() -> None:
    """A naive timestamp silently inherits whichever zone produced it."""
    import pyarrow as pa

    for table in spec.TABLES:
        for field in table.fields:
            if pa.types.is_timestamp(field.arrow_type):
                assert field.arrow_type.tz is not None, f"{table.name}.{field.name}"
