"""A small synthetic delivery that conforms to the contract.

Every value here is invented. Nothing is sampled from, derived from, or
anonymised out of any real publisher's data: the readers, the content, the
sections and the timestamps were written by hand to exercise specific parts of
the contract, and the file is useful precisely because you can read it and see
what each row is for.

What the dataset deliberately contains
--------------------------------------

*Readers with and without a paid subscription.* Note what "anonymous" can and
cannot mean here. The contract has exactly one reader-id grain -- a resolved
person -- so an unresolved browser or app install is not representable at all,
by design (two grains in one id column make every distinct-reader count
meaningless). "Anonymous" in this dataset therefore means a pseudonymous reader
who has never paid: ``registered_unpaid``. One reader has no subscription row at
all, which is a third, distinct case: state unknown, and therefore outside the
scored population rather than inside it with a zero.

*Subscriptions that start, cancel and renew.* One reader runs
trial → active → cancelled → expired → active, as five half-open intervals that
meet exactly. Another runs active → payment_failed → grace → cancelled. The
renewal matters: a reader whose second paid span is treated as their first has
the wrong tenure and, if the spans are merged, the wrong churn.

*Several sections, including one piece of content in two of them.* A view of
content in n sections contributes 1/n to each, so a two-section piece is the
smallest case where that rule is visible.

*Missing content metadata, in all three of its shapes.* Content whose sections
did not resolve and which carries a null list; content whose sections did not
resolve and which carries an empty list; and a reader event pointing at a
content id that has no row in ``content`` at all. All three are conformant, and
all three mean "we do not know what this was about" -- which is not "the reader
read nothing".

*Null engagement time.* Several events have no measured attention. Null is not
zero: a run that reads it as zero reports measured indifference where there was
no measurement.

*Events that fall on different calendar days depending on the timezone, on
every channel.* See :data:`DAY_BOUNDARY_EVENTS`. This is the point of the whole
contract shape, so the dataset carries worked examples rather than a warning in
prose -- and it carries one on reader events, on email and on community actions,
because the system this contract replaces converted the first and left the other
two in whatever zone the vendor sent. A dataset with a near-midnight row on only
one channel lets a consumer convert that one, forget the others, and pass.

The manifest this module writes declares ``America/New_York`` and a
Sunday-ending week. Those are **the synthetic publisher's own declarations**,
not contract defaults and not a recommendation: the contract has no default for
either, and a deployment must state its own.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from engagement_kernel.contract import enums, spec
from engagement_kernel.contract.manifest import MANIFEST_FILENAME

#: The synthetic publisher's declared day boundary. Its own choice, not a default.
DEMO_TIMEZONE = "America/New_York"

#: A second zone, used only to demonstrate that the same instants land on
#: different calendar days -- and, for one of them, in different weeks.
DEMO_COMPARISON_TIMEZONE = "Europe/Berlin"

DEMO_ARTICLE_VIEW_DEFINITION_ID = "demo-article-view-v1"
DEMO_SCORED_POPULATION_DEFINITION_ID = "demo-scored-population-v1"


def _ts(text: str) -> datetime:
    """An instant, written as UTC so the literal is unambiguous on the page."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# --- readers ----------------------------------------------------------------

READER_FULL_HISTORY = "rdr-a1c7"
READER_INSTITUTIONAL = "rdr-b2d8"
READER_REGISTERED_THEN_TRIAL = "rdr-c3e9"
READER_LAPSING = "rdr-d4f0"
READER_NEVER_PAID = "rdr-e5a1"
READER_NEVER_PAID_QUIET = "rdr-f6b2"
READER_NO_SUBSCRIPTION_ROW = "rdr-g7c3"
READER_GUEST = "rdr-h8d4"
READER_EXCLUDED = "rdr-x9e5"

_READERS: tuple[str, ...] = (
    READER_FULL_HISTORY,
    READER_INSTITUTIONAL,
    READER_REGISTERED_THEN_TRIAL,
    READER_LAPSING,
    READER_NEVER_PAID,
    READER_NEVER_PAID_QUIET,
    READER_NO_SUBSCRIPTION_ROW,
    READER_GUEST,
    READER_EXCLUDED,
)


# --- content ----------------------------------------------------------------

#: Referenced by a reader event and deliberately absent from ``content``: the
#: metadata never resolved. Permitted by the contract, and not an error.
CONTENT_WITH_NO_METADATA_ROW = "cnt-99"

_CONTENT: tuple[tuple[str, str, str, list[str] | None, str | None], ...] = (
    ("cnt-01", "article", enums.SECTION_RESOLUTION_RESOLVED, ["news"], "2026-02-14T14:00:00"),
    # Two sections: the case where 1/n attribution is visible.
    (
        "cnt-02",
        "article",
        enums.SECTION_RESOLUTION_RESOLVED,
        ["news", "education"],
        "2026-02-15T09:30:00",
    ),
    ("cnt-03", "liveblog", enums.SECTION_RESOLUTION_RESOLVED, ["sports"], "2026-02-16T23:00:00"),
    ("cnt-04", "article", enums.SECTION_RESOLUTION_RESOLVED, ["food"], "2026-02-18T11:15:00"),
    ("cnt-05", "video", enums.SECTION_RESOLUTION_RESOLVED, ["arts"], "2026-02-19T17:45:00"),
    ("cnt-06", "newsletter", enums.SECTION_RESOLUTION_RESOLVED, ["news"], "2026-02-20T06:00:00"),
    # Unresolved, null list: metadata missing entirely.
    ("cnt-07", "article", enums.SECTION_RESOLUTION_UNRESOLVED, None, None),
    # Unresolved, empty list: the same fact stated the other permitted way.
    ("cnt-08", "gallery", enums.SECTION_RESOLUTION_UNRESOLVED, [], "2026-02-21T13:20:00"),
    ("cnt-09", "article", enums.SECTION_RESOLUTION_RESOLVED, ["sports", "news"], None),
    ("cnt-10", "podcast", enums.SECTION_RESOLUTION_RESOLVED, ["arts"], "2026-02-24T08:00:00"),
)


# --- the day-boundary examples ----------------------------------------------


class DayBoundaryExample:
    """One instant, and the calendar day it falls on in each of three zones.

    Written out rather than computed so the file states what it is claiming.
    ``tests/test_demo_dataset.py`` recomputes every one of these from the
    instant and fails if the claim is wrong, so the table cannot drift.

    ``table`` names the contract table the row lives on, because there is one
    of these on **every** channel and not only on reader events. That is
    deliberate: the system this contract replaces converted web and app to the
    publisher's zone and left email and community in whatever zone the vendor
    sent, so a dataset that only exercised the boundary on reader events would
    let exactly that defect through. A consumer's day-boundary test needs a
    near-midnight row per channel to have anything to discriminate against.
    """

    __slots__ = ("event_id", "instant", "local_dates", "table", "why")

    def __init__(
        self,
        event_id: str,
        instant: str,
        local_dates: dict[str, str],
        why: str,
        table: str = "reader_event",
    ) -> None:
        self.event_id = event_id
        self.instant = instant
        self.local_dates = local_dates
        self.why = why
        self.table = table


DAY_BOUNDARY_EVENTS: tuple[DayBoundaryExample, ...] = (
    DayBoundaryExample(
        event_id="evt-boundary-1",
        instant="2026-02-17T03:15:00",
        local_dates={
            DEMO_TIMEZONE: "2026-02-16",
            "UTC": "2026-02-17",
            DEMO_COMPARISON_TIMEZONE: "2026-02-17",
        },
        why=(
            "A late-evening read in the publisher's own zone. Bucketed in UTC it moves to "
            "the next day, which is how a source that never converts its vendor timestamps "
            "shifts one channel's whole history by a few hours."
        ),
    ),
    DayBoundaryExample(
        event_id="evt-boundary-2",
        instant="2026-03-09T02:30:00",
        local_dates={
            DEMO_TIMEZONE: "2026-03-08",
            "UTC": "2026-03-09",
            DEMO_COMPARISON_TIMEZONE: "2026-03-09",
        },
        why=(
            "Sunday evening in the publisher's zone, Monday elsewhere. Under a "
            "Sunday-ending week anchor this event changes WEEK as well as day, which is "
            "the failure that survives every plausibility check downstream."
        ),
    ),
    DayBoundaryExample(
        event_id="evt-boundary-3",
        instant="2026-03-08T06:45:00",
        local_dates={
            DEMO_TIMEZONE: "2026-03-08",
            "UTC": "2026-03-08",
            DEMO_COMPARISON_TIMEZONE: "2026-03-08",
        },
        why=(
            "The morning the publisher's zone springs forward. Same calendar day in all "
            "three zones, and included as the control: the boundary examples above are not "
            "an artefact of the harness."
        ),
    ),
    DayBoundaryExample(
        event_id="eop-0007",
        instant="2026-03-09T02:33:00",
        table="email_open",
        local_dates={
            DEMO_TIMEZONE: "2026-03-08",
            "UTC": "2026-03-09",
            DEMO_COMPARISON_TIMEZONE: "2026-03-09",
        },
        why=(
            "The same Sunday evening, on the email feed. Email is the channel where an "
            "unconverted vendor timestamp historically does the most damage, and this reader "
            "has no click on the day at all -- so a consumer that converts clicks and forgets "
            "opens has a row that only the open side can catch."
        ),
    ),
    DayBoundaryExample(
        event_id="cmt-0007",
        instant="2026-03-09T02:35:00",
        table="community_action",
        local_dates={
            DEMO_TIMEZONE: "2026-03-08",
            "UTC": "2026-03-09",
            DEMO_COMPARISON_TIMEZONE: "2026-03-09",
        },
        why=(
            "The same Sunday evening again, on the community feed -- the other channel the "
            "upstream system left unconverted. Without this row a community day-boundary "
            "check has nothing to discriminate against and passes whether or not the "
            "conversion is applied."
        ),
    ),
)


# --- reader events ----------------------------------------------------------

_D = enums.EVENT_KIND_CONTENT_DELIVERY
_I = enums.EVENT_KIND_CONTENT_INTERACTION
_WEB = enums.CHANNEL_WEB
_APP = enums.CHANNEL_APP

# (event_id, reader, instant, channel, kind, content_id, session, seconds)
_EVENTS: tuple[tuple[str, str, str, str, str, str | None, str, float | None], ...] = (
    ("evt-0001", READER_FULL_HISTORY, "2026-02-16T13:02:00", _WEB, _D, "cnt-01", "ses-0001", 74.5),
    ("evt-0002", READER_FULL_HISTORY, "2026-02-16T13:05:00", _WEB, _D, "cnt-02", "ses-0001", 130.0),
    ("evt-0003", READER_FULL_HISTORY, "2026-02-16T13:07:00", _WEB, _I, None, "ses-0001", None),
    ("evt-0004", READER_FULL_HISTORY, "2026-02-18T08:40:00", _APP, _D, "cnt-04", "ses-0002", 61.0),
    ("evt-0005", READER_FULL_HISTORY, "2026-02-18T20:11:00", _APP, _D, "cnt-03", "ses-0003", None),
    ("evt-0006", READER_FULL_HISTORY, "2026-03-02T15:30:00", _WEB, _D, "cnt-09", "ses-0004", 42.0),
    ("evt-0007", READER_INSTITUTIONAL, "2026-02-17T12:00:00", _WEB, _D, "cnt-01", "ses-0005", 15.0),
    (
        "evt-0008",
        READER_INSTITUTIONAL,
        "2026-02-17T12:04:00",
        _WEB,
        _D,
        # No row in `content`: the metadata never resolved. Conformant.
        CONTENT_WITH_NO_METADATA_ROW,
        "ses-0005",
        22.0,
    ),
    (
        "evt-0009",
        READER_INSTITUTIONAL,
        "2026-02-19T09:00:00",
        _WEB,
        _D,
        "cnt-07",
        "ses-0006",
        None,
    ),
    (
        "evt-0010",
        READER_INSTITUTIONAL,
        "2026-02-19T09:03:00",
        _WEB,
        _D,
        "cnt-08",
        "ses-0006",
        18.0,
    ),
    (
        "evt-0011",
        READER_REGISTERED_THEN_TRIAL,
        "2026-02-20T18:22:00",
        _APP,
        _D,
        "cnt-06",
        "ses-0007",
        95.0,
    ),
    (
        "evt-0012",
        READER_REGISTERED_THEN_TRIAL,
        "2026-02-25T07:15:00",
        _APP,
        _D,
        "cnt-10",
        "ses-0008",
        410.0,
    ),
    (
        "evt-0013",
        READER_REGISTERED_THEN_TRIAL,
        "2026-02-25T07:31:00",
        _APP,
        _I,
        "cnt-10",
        "ses-0008",
        0.0,
    ),
    ("evt-0014", READER_LAPSING, "2026-02-21T21:45:00", _WEB, _D, "cnt-05", "ses-0009", 205.0),
    ("evt-0015", READER_LAPSING, "2026-02-22T10:05:00", _WEB, _D, "cnt-04", "ses-0010", None),
    ("evt-0016", READER_LAPSING, "2026-03-01T11:11:00", _WEB, _D, "cnt-01", "ses-0011", 33.0),
    ("evt-0017", READER_NEVER_PAID, "2026-02-16T19:50:00", _WEB, _D, "cnt-03", "ses-0012", 12.0),
    ("evt-0018", READER_NEVER_PAID, "2026-02-23T19:55:00", _WEB, _D, "cnt-09", "ses-0013", None),
    (
        "evt-0019",
        READER_NEVER_PAID_QUIET,
        "2026-02-24T06:30:00",
        _APP,
        _D,
        "cnt-02",
        "ses-0014",
        58.0,
    ),
    (
        "evt-0020",
        READER_NO_SUBSCRIPTION_ROW,
        "2026-02-26T16:40:00",
        _WEB,
        _D,
        "cnt-01",
        "ses-0015",
        27.5,
    ),
    ("evt-0021", READER_GUEST, "2026-02-27T13:13:00", _APP, _D, "cnt-06", "ses-0016", 88.0),
    ("evt-0022", READER_GUEST, "2026-02-27T13:20:00", _APP, _I, None, "ses-0016", None),
    ("evt-0023", READER_EXCLUDED, "2026-02-28T23:05:00", _WEB, _D, "cnt-01", "ses-0017", 9.0),
    # The three worked day-boundary cases, in the order of DAY_BOUNDARY_EVENTS.
    (
        "evt-boundary-1",
        READER_FULL_HISTORY,
        "2026-02-17T03:15:00",
        _WEB,
        _D,
        "cnt-02",
        "ses-0018",
        140.0,
    ),
    (
        "evt-boundary-2",
        READER_LAPSING,
        "2026-03-09T02:30:00",
        _WEB,
        _D,
        "cnt-09",
        "ses-0019",
        66.0,
    ),
    (
        "evt-boundary-3",
        READER_INSTITUTIONAL,
        "2026-03-08T06:45:00",
        _APP,
        _D,
        "cnt-04",
        "ses-0020",
        None,
    ),
)


# --- subscription spans -----------------------------------------------------

# (reader, state, payer, start, end or None)
_SPANS: tuple[tuple[str, str, str | None, str, str | None], ...] = (
    # Starts, runs, cancels, expires -- and then renews. Five spans that meet
    # exactly, so status as of any date resolves to exactly one of them.
    (READER_FULL_HISTORY, "trial", "individual", "2025-09-01T00:00:00", "2025-09-15T00:00:00"),
    (READER_FULL_HISTORY, "active", "individual", "2025-09-15T00:00:00", "2026-01-10T00:00:00"),
    (READER_FULL_HISTORY, "cancelled", "individual", "2026-01-10T00:00:00", "2026-02-01T00:00:00"),
    (READER_FULL_HISTORY, "expired", "individual", "2026-02-01T00:00:00", "2026-03-01T00:00:00"),
    (READER_FULL_HISTORY, "active", "individual", "2026-03-01T00:00:00", None),
    (READER_INSTITUTIONAL, "active", "institutional", "2025-06-01T00:00:00", None),
    # payer_type null: this billing system cannot say who paid for a free
    # registration. Null means unknown, and never "individual".
    (
        READER_REGISTERED_THEN_TRIAL,
        "registered_unpaid",
        None,
        "2025-05-01T00:00:00",
        "2026-02-14T00:00:00",
    ),
    (READER_REGISTERED_THEN_TRIAL, "trial", "individual", "2026-02-14T00:00:00", None),
    (READER_LAPSING, "active", "individual", "2025-08-01T00:00:00", "2026-02-20T00:00:00"),
    (
        READER_LAPSING,
        "payment_failed",
        "individual",
        "2026-02-20T00:00:00",
        "2026-02-27T00:00:00",
    ),
    (READER_LAPSING, "grace", "individual", "2026-02-27T00:00:00", "2026-03-10T00:00:00"),
    (READER_LAPSING, "cancelled", "individual", "2026-03-10T00:00:00", None),
    (READER_NEVER_PAID, "registered_unpaid", None, "2025-01-15T00:00:00", None),
    (READER_NEVER_PAID_QUIET, "registered_unpaid", None, "2024-11-01T00:00:00", None),
    (READER_GUEST, "active", "guest", "2026-02-01T00:00:00", None),
    (READER_EXCLUDED, "active", "individual", "2025-12-01T00:00:00", None),
    # READER_NO_SUBSCRIPTION_ROW is deliberately absent from this table: state
    # unknown, so outside the scored population rather than inside it as a zero.
)


# --- optional inputs --------------------------------------------------------

EMAIL_LIST_DAILY = "lst-daily"
EMAIL_LIST_WEEKEND = "lst-weekend"

#: Declared floors for the optional inputs. Chosen to sit before the event
#: window so the demo delivery passes, and stated at all because an input that
#: is available "from some point" and read as zero before it is a pre-launch gap
#: reported as disengagement.
EMAIL_AVAILABLE_FROM = "2025-11-01"
COMMUNITY_AVAILABLE_FROM = "2025-10-21"

# (event_id, reader, instant, list, campaign or None)
_EMAIL_CLICKS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("ecl-0001", READER_FULL_HISTORY, "2026-02-16T11:40:00", EMAIL_LIST_DAILY, "cmp-0201"),
    ("ecl-0002", READER_FULL_HISTORY, "2026-02-16T11:41:30", EMAIL_LIST_DAILY, "cmp-0201"),
    ("ecl-0003", READER_FULL_HISTORY, "2026-02-23T11:38:00", EMAIL_LIST_DAILY, "cmp-0208"),
    ("ecl-0004", READER_INSTITUTIONAL, "2026-02-21T09:12:00", EMAIL_LIST_WEEKEND, "cmp-0301"),
    # Unattributed click: the provider could not name the send.
    ("ecl-0005", READER_LAPSING, "2026-02-19T20:02:00", EMAIL_LIST_DAILY, None),
    ("ecl-0006", READER_GUEST, "2026-02-27T07:55:00", EMAIL_LIST_DAILY, "cmp-0214"),
    # Same instant as a day-boundary read, on the channel where an unconverted
    # vendor timestamp historically does the most damage.
    ("ecl-0007", READER_LAPSING, "2026-03-09T02:31:00", EMAIL_LIST_DAILY, "cmp-0224"),
)

# (event_id, reader, instant, list, campaign or None)
_EMAIL_OPENS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("eop-0001", READER_FULL_HISTORY, "2026-02-16T11:39:00", EMAIL_LIST_DAILY, "cmp-0201"),
    ("eop-0002", READER_INSTITUTIONAL, "2026-02-21T09:10:00", EMAIL_LIST_WEEKEND, "cmp-0301"),
    # Reachable, never engaged: opens with no click anywhere in the delivery.
    # This is the whole permitted use of the table, and the reason it is not a
    # model feature.
    ("eop-0003", READER_NEVER_PAID_QUIET, "2026-02-17T05:20:00", EMAIL_LIST_DAILY, "cmp-0202"),
    ("eop-0004", READER_NEVER_PAID_QUIET, "2026-02-18T05:21:00", EMAIL_LIST_DAILY, "cmp-0203"),
    ("eop-0005", READER_NEVER_PAID_QUIET, "2026-02-19T05:19:00", EMAIL_LIST_DAILY, None),
    ("eop-0006", READER_LAPSING, "2026-02-19T20:00:00", EMAIL_LIST_DAILY, "cmp-0205"),
    # Sunday evening in the publisher's zone, Monday in UTC, and this reader has
    # no click that day -- so the open side of the email feed carries a
    # boundary case of its own. See DAY_BOUNDARY_EVENTS.
    ("eop-0007", READER_FULL_HISTORY, "2026-03-09T02:33:00", EMAIL_LIST_DAILY, "cmp-0224"),
)

DEMO_COMMUNITY_SITE = "site-main"

# (event_id, reader, instant, action, site, target content or None)
_COMMUNITY: tuple[tuple[str, str, str, str, str, str | None], ...] = (
    (
        "cmt-0001",
        READER_FULL_HISTORY,
        "2026-02-16T13:12:00",
        "post_created",
        DEMO_COMMUNITY_SITE,
        "cnt-01",
    ),
    (
        "cmt-0002",
        READER_FULL_HISTORY,
        "2026-02-16T13:20:00",
        "reply_created",
        DEMO_COMMUNITY_SITE,
        "cnt-01",
    ),
    # Given, not received. There is no received-side action kind.
    (
        "cmt-0003",
        READER_FULL_HISTORY,
        "2026-02-16T13:22:00",
        "like_given",
        DEMO_COMMUNITY_SITE,
        "cnt-01",
    ),
    (
        "cmt-0004",
        READER_LAPSING,
        "2026-02-21T22:03:00",
        "dislike_given",
        DEMO_COMMUNITY_SITE,
        "cnt-05",
    ),
    ("cmt-0005", READER_LAPSING, "2026-02-21T22:05:00", "flag_given", DEMO_COMMUNITY_SITE, None),
    (
        "cmt-0006",
        READER_GUEST,
        "2026-02-27T13:25:00",
        "post_created",
        DEMO_COMMUNITY_SITE,
        "cnt-06",
    ),
    # The community channel's own day-boundary case: Sunday evening in the
    # publisher's zone, Monday in UTC, so it moves week as well as day. See
    # DAY_BOUNDARY_EVENTS.
    (
        "cmt-0007",
        READER_FULL_HISTORY,
        "2026-03-09T02:35:00",
        "reply_created",
        DEMO_COMMUNITY_SITE,
        "cnt-09",
    ),
)


# --- table construction -----------------------------------------------------


def _table(table_spec: spec.TableSpec, columns: dict[str, list]) -> pa.Table:
    return pa.table(columns, schema=table_spec.arrow_schema())


def build_tables() -> dict[str, pa.Table]:
    """Every contract table, as Arrow tables. Deterministic."""
    reader = _table(
        spec.READER,
        {
            "reader_id": list(_READERS),
            "id_grain": [enums.GRAIN_RESOLVED_PERSON] * len(_READERS),
        },
    )

    reader_event = _table(
        spec.READER_EVENT,
        {
            "event_id": [row[0] for row in _EVENTS],
            "reader_id": [row[1] for row in _EVENTS],
            "event_ts": [_ts(row[2]) for row in _EVENTS],
            "channel": [row[3] for row in _EVENTS],
            "event_kind": [row[4] for row in _EVENTS],
            "content_id": [row[5] for row in _EVENTS],
            "session_id": [row[6] for row in _EVENTS],
            "engagement_time_seconds": [row[7] for row in _EVENTS],
        },
    )

    content = _table(
        spec.CONTENT,
        {
            "content_id": [row[0] for row in _CONTENT],
            "content_type": [row[1] for row in _CONTENT],
            "section_resolution": [row[2] for row in _CONTENT],
            "sections": [row[3] for row in _CONTENT],
            "published_ts": [_ts(row[4]) if row[4] else None for row in _CONTENT],
        },
    )

    subscription_span = _table(
        spec.SUBSCRIPTION_SPAN,
        {
            "reader_id": [row[0] for row in _SPANS],
            "state": [row[1] for row in _SPANS],
            "payer_type": [row[2] for row in _SPANS],
            "start_ts": [_ts(row[3]) for row in _SPANS],
            "end_ts": [_ts(row[4]) if row[4] else None for row in _SPANS],
        },
    )

    email_click = _table(
        spec.EMAIL_CLICK,
        {
            "event_id": [row[0] for row in _EMAIL_CLICKS],
            "reader_id": [row[1] for row in _EMAIL_CLICKS],
            "event_ts": [_ts(row[2]) for row in _EMAIL_CLICKS],
            "list_id": [row[3] for row in _EMAIL_CLICKS],
            "campaign_id": [row[4] for row in _EMAIL_CLICKS],
        },
    )

    email_open = _table(
        spec.EMAIL_OPEN,
        {
            "event_id": [row[0] for row in _EMAIL_OPENS],
            "reader_id": [row[1] for row in _EMAIL_OPENS],
            "event_ts": [_ts(row[2]) for row in _EMAIL_OPENS],
            "list_id": [row[3] for row in _EMAIL_OPENS],
            "campaign_id": [row[4] for row in _EMAIL_OPENS],
        },
    )

    community_action = _table(
        spec.COMMUNITY_ACTION,
        {
            "event_id": [row[0] for row in _COMMUNITY],
            "reader_id": [row[1] for row in _COMMUNITY],
            "event_ts": [_ts(row[2]) for row in _COMMUNITY],
            "action_kind": [row[3] for row in _COMMUNITY],
            "site_id": [row[4] for row in _COMMUNITY],
            "target_content_id": [row[5] for row in _COMMUNITY],
        },
    )

    return {
        spec.READER.name: reader,
        spec.READER_EVENT.name: reader_event,
        spec.CONTENT.name: content,
        spec.SUBSCRIPTION_SPAN.name: subscription_span,
        spec.EMAIL_CLICK.name: email_click,
        spec.EMAIL_OPEN.name: email_open,
        spec.COMMUNITY_ACTION.name: community_action,
    }


def build_manifest() -> dict:
    """The delivery manifest, as the JSON shape the contract reads.

    The timezone, the week anchor, the article-view selection and the scored
    population are all stated here because the contract has no default for any
    of them. These are one synthetic publisher's answers.
    """
    return {
        "contract_name": spec.CONTRACT_NAME,
        "contract_version": spec.CONTRACT_VERSION,
        "day_boundary_timezone": DEMO_TIMEZONE,
        "week_anchor": {"weekday": "Sunday", "position": "week_ends_on"},
        "article_view": {
            "definition_id": DEMO_ARTICLE_VIEW_DEFINITION_ID,
            # This synthetic publisher counts articles and liveblogs and does
            # not count video, podcasts, newsletters or galleries. Another
            # publisher's answer is a different definition id.
            "content_types": ["article", "liveblog"],
            "event_kinds": [enums.EVENT_KIND_CONTENT_DELIVERY],
        },
        "scored_population": {
            "definition_id": DEMO_SCORED_POPULATION_DEFINITION_ID,
            "entitled_states": ["trial", "active", "grace"],
        },
        "optional_inputs": {
            spec.EMAIL_CLICK.name: {
                "status": enums.AVAILABILITY_AVAILABLE,
                "available_from": EMAIL_AVAILABLE_FROM,
            },
            spec.EMAIL_OPEN.name: {
                "status": enums.AVAILABILITY_AVAILABLE,
                "available_from": EMAIL_AVAILABLE_FROM,
            },
            spec.COMMUNITY_ACTION.name: {
                "status": enums.AVAILABILITY_AVAILABLE,
                "available_from": COMMUNITY_AVAILABLE_FROM,
            },
        },
        # Opaque ids only. The contract carries no personal field, so an
        # exclusion over a personal attribute is not expressible against it --
        # a deployment resolves its policy to ids before it reaches here.
        "population_exclusions": [READER_EXCLUDED],
    }


def write_delivery(directory: str | Path) -> list[Path]:
    """Write the demo delivery -- every table plus the manifest -- and list it."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in build_tables().items():
        path = target / spec.TABLES_BY_NAME[name].filename
        # Uncompressed on purpose: the file is a readable, byte-stable artefact
        # in a repository, not a storage optimisation.
        pq.write_table(table, path, compression="none")
        written.append(path)
    manifest_path = target / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(build_manifest(), indent=2) + "\n", encoding="utf-8")
    written.append(manifest_path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-demo-dataset",
        description=(
            "Write the synthetic demo delivery -- seven Parquet files and a manifest -- to a "
            "directory. Every value is invented; nothing derives from any real data."
        ),
    )
    parser.add_argument("directory", help="directory to write the delivery into")
    args = parser.parse_args(argv)
    for path in write_delivery(args.directory):
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
