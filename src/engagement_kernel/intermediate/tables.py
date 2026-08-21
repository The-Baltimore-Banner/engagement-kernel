"""The output tables, declared as data.

Seven tables, and only seven. The set is short because a census of the system
this replaces found that of eleven declared daily tables plus two undeclared
ones, six were load-bearing for the two publication lanes and the rest were
built, stored, mirrored and read by nothing. :data:`NOT_BUILT` carries that
decision in full, table by table, with the reason each one was left out -- so
"we did not port it" is a recorded judgement rather than an omission somebody
has to reverse-engineer later.

Each table declares its grain and the deduplication key that grain implies, and
:mod:`engagement_kernel.intermediate.checks` asserts the key on every build. A
documented grain that nothing enforces is how two rows per key arrive: every
count downstream doubles, and no single number looks wrong.

Two tables are marked ``published=False``. They exist because something else
needs them, they are not part of the surface the modelling lanes read, and
saying so here stops them being treated as outputs by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engagement_kernel.contract import enums

#: Column emitted by this build and rejected by the contract on the way in.
#:
#: The asymmetry is the design. A producer that supplies a pre-bucketed calendar
#: date has already applied *some* timezone, and no validator can recover which
#: one; so the contract refuses the column by name on input. The engine then
#: produces exactly that column on output, once, in the one declared zone. If
#: one of these tables is ever fed back in as a delivery it will be refused,
#: which is the correct outcome: it would be bucketed twice.
LOCAL_DATE_COLUMN = "local_date"


@dataclass(frozen=True)
class OutputColumn:
    """One column of one output table, and what it means."""

    name: str
    definition: str
    #: True when the column can legitimately be null, with the meaning stated in
    #: the definition. Nothing here is nullable by accident.
    nullable: bool = False


@dataclass(frozen=True)
class OutputTable:
    """One table this build produces."""

    name: str
    grain: str
    dedup_key: tuple[str, ...]
    purpose: str
    columns: tuple[OutputColumn, ...]
    #: Contract inputs it derives from, for the build report.
    inputs: tuple[str, ...]
    #: False for an internal intermediate: built because something else needs
    #: it, not part of the surface the modelling lanes read.
    published: bool = True
    #: Contract inputs that are optional. When one is absent the table is not
    #: built at all, and its absence is reported. It is never built with a
    #: column of zeros: a reader with no community feed is not a reader who
    #: never commented.
    optional_inputs: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def column(self, name: str) -> OutputColumn | None:
        for item in self.columns:
            if item.name == name:
                return item
        return None


#: Mapping from a community action kind to the column that counts it.
#:
#: Derived from the contract's enum rather than written out, so adding an action
#: kind to the contract cannot silently leave its count out of this table. Every
#: value names an action the reader *performed*: a like given, never a like
#: received. Getting that backwards inverts the feature and the number still
#: looks reasonable.
COMMUNITY_ACTION_COLUMNS: dict[str, str] = {
    "post_created": "posts_created",
    "reply_created": "replies_created",
    "like_given": "likes_given",
    "dislike_given": "dislikes_given",
    "flag_given": "flags_given",
}

# Written out rather than derived, because mechanical pluralisation produces
# "replys_created" and a column name that reads as a typo gets renamed by hand
# later, quietly, in one place. Checked here instead: a kind added to the
# contract with no column, or a column naming a kind the contract dropped, fails
# at import rather than producing a table that silently counts four of five
# actions.
_missing = [k for k in enums.COMMUNITY_ACTION_KINDS if k not in COMMUNITY_ACTION_COLUMNS]
_unknown = [k for k in COMMUNITY_ACTION_COLUMNS if k not in enums.COMMUNITY_ACTION_KINDS]
if _missing or _unknown:  # pragma: no cover - a contract change trips this, not a run
    raise ImportError(
        "COMMUNITY_ACTION_COLUMNS is out of step with the contract's action kinds: "
        f"missing a column for {_missing}, and naming unknown kinds {_unknown}. "
        "reader_community_day would silently count only the kinds that still line up"
    )


CONTENT_DIMENSION = OutputTable(
    name="content_dimension",
    published=False,
    grain="One row per piece of content.",
    dedup_key=("content_id",),
    purpose=(
        "The content dimension, reduced to what the daily tables actually read. "
        "Nine of the upstream table's fourteen columns had no reader at all and "
        "two of those nine were forbidden from ever reaching a model, so the "
        "reduction is the point rather than a side effect."
    ),
    inputs=("content",),
    columns=(
        OutputColumn("content_id", "Opaque identifier for the content."),
        OutputColumn(
            "content_type",
            "The content's type, carried because the article-view predicate is "
            "the one thing that reads it. Upstream this arrived on the event "
            "itself; in this contract it lives on the content row, so the "
            "dimension has to carry it.",
        ),
        OutputColumn(
            "section_resolution",
            "The producer's declared statement about whether this content's "
            "section metadata resolved. Carried, not inferred from an empty "
            "list, so the sentinel attribution in reader_section_day is "
            "traceable to a declared outcome.",
        ),
        OutputColumn(
            "sections",
            "The sections this content belongs to. Null or empty exactly when "
            "section_resolution is unresolved.",
            nullable=True,
        ),
    ),
    notes=(
        "Author and content-type lists are not carried. The two tables that "
        "read them are on the not-built list, so carrying the lists would "
        "materialise data for a reader that does not exist.",
    ),
)


READER_CONTENT_DAY = OutputTable(
    name="reader_content_day",
    published=False,
    grain="One row per reader per channel per piece of content per local day.",
    dedup_key=("reader_id", "channel", "content_id", LOCAL_DATE_COLUMN),
    purpose=(
        "Per-content consumption, as the internal intermediate the channel and "
        "section tables are built from. No modelling lane reads it, so it is "
        "not published; it exists because the fractional section attribution "
        "needs a per-content row to divide."
    ),
    inputs=("reader_event", "content"),
    columns=(
        OutputColumn("reader_id", "The reader."),
        OutputColumn("channel", "The surface the reading happened on."),
        OutputColumn("content_id", "The content read."),
        OutputColumn(
            LOCAL_DATE_COLUMN,
            "Calendar day in the configured timezone, computed here from the "
            "event instant and never taken from the producer.",
        ),
        OutputColumn(
            "views",
            "Qualifying article views of this content by this reader on this "
            "day and channel. At least 1 in every row: a row exists only where "
            "there was a view.",
        ),
        OutputColumn(
            "sessions",
            "Distinct sessions in which this reader viewed THIS content today. "
            "Present because the upstream table had it, and it is the column "
            "whose obvious summation is wrong -- see distinct_sessions_day.",
        ),
        OutputColumn(
            "distinct_sessions_day",
            "Distinct sessions in which this reader viewed ANY content on this "
            "day and channel. Identical on every row of the reader-day, "
            "computed once at the event layer, and carried here so the channel "
            "table can recover it with a maximum. This is not the sum of "
            "`sessions`, and the sum is larger and plausible.",
        ),
        OutputColumn(
            "total_time_seconds",
            "Measured attention on this content, summed over views, skipping "
            "unmeasured events. Null when nothing was measured -- which is not "
            "zero, and must never be read as zero.",
            nullable=True,
        ),
        OutputColumn(
            "measured_time_deliveries",
            "How many of those views carried a measurement. The honest "
            "denominator for any time-per-view rate, and the reason a null "
            "total does not have to be guessed at downstream.",
        ),
        OutputColumn(
            "events",
            "All reader events attributable to this content on this day and "
            "channel, interactions included. Always at least `views`.",
        ),
    ),
    notes=(
        "A reader-channel-day with no qualifying view produces no row at all. "
        "That is deliberate: a channel active day is a day with at least one "
        "qualifying view, so a row of zeros would turn a day of no reading into "
        "a day of reading nothing.",
        "Events on content whose type is outside the article-view definition "
        "are therefore not counted anywhere in this build. The upstream system "
        "dropped them the same way; it is recorded here because the "
        "consequence -- a reader who only watches video reads as inactive -- is "
        "a property of the article-view definition, not of this code.",
    ),
)


READER_CHANNEL_DAY = OutputTable(
    name="reader_channel_day",
    grain="One row per reader per channel per local day.",
    dedup_key=("reader_id", "channel", LOCAL_DATE_COLUMN),
    purpose=(
        "The primary daily consumption table. Every web and app feature in the "
        "modelling lanes is built from these five measures over a window."
    ),
    inputs=("reader_event", "content"),
    columns=(
        OutputColumn("reader_id", "The reader."),
        OutputColumn("channel", "The surface."),
        OutputColumn(LOCAL_DATE_COLUMN, "Calendar day in the configured timezone."),
        OutputColumn("views", "Qualifying article views on this day and channel."),
        OutputColumn(
            "sessions",
            "Distinct sessions on this day and channel. The MAXIMUM of the "
            "carried per-reader-day count, not the sum of the per-content "
            "counts. Summing yields a larger, entirely plausible, wrong number, "
            "and a views-per-session feature absorbs the error so nothing looks "
            "broken.",
        ),
        OutputColumn(
            "total_time_seconds",
            "Measured attention summed over views. Null when nothing on this "
            "reader-day was measured.",
            nullable=True,
        ),
        OutputColumn(
            "measured_time_deliveries",
            "Views that carried a measurement, so a rate has a real denominator.",
        ),
        OutputColumn("events", "All events attributable to viewed content on this day."),
    ),
)


READER_SECTION_DAY = OutputTable(
    name="reader_section_day",
    grain="One row per reader per section per local day.",
    dedup_key=("reader_id", "section", LOCAL_DATE_COLUMN),
    purpose=(
        "The topic surface. Both the reader-cluster topic block and the whole "
        "content-persona lane are built from this table, which makes it the "
        "most load-bearing output after the channel table."
    ),
    inputs=("reader_event", "content"),
    columns=(
        OutputColumn("reader_id", "The reader."),
        OutputColumn(
            "section",
            "The section, or the unresolved sentinel. Never null: a null here "
            "would be dropped by every group-by downstream, which is exactly "
            "how unresolved reading becomes no reading.",
        ),
        OutputColumn(LOCAL_DATE_COLUMN, "Calendar day in the configured timezone."),
        OutputColumn(
            "section_views",
            "Fractional views. A view of content in n sections contributes 1/n "
            "to each, so the day's section views sum exactly to the day's total "
            "views. Fractional on purpose: whole-number attribution per section "
            "multiplies a multi-section reader's activity by the number of "
            "sections the content happened to be filed under.",
        ),
        OutputColumn(
            "section_time_seconds",
            "Measured attention, attributed by the same fraction. Null when "
            "nothing behind these views was measured.",
            nullable=True,
        ),
        OutputColumn(
            "distinct_content_ids",
            "Distinct pieces of content behind these views. Counted per row, "
            "so it is not additive across sections.",
        ),
    ),
    notes=(
        "No channel in the grain. Section reading is deliberately cross-channel: "
        "the same article read on web and in the app is the same topic interest, "
        "and splitting it would halve every section signal for a "
        "multi-surface reader.",
    ),
)


READER_EMAIL_DAY = OutputTable(
    name="reader_email_day",
    grain="One row per list per reader per local day.",
    dedup_key=("list_id", "reader_id", LOCAL_DATE_COLUMN),
    purpose=(
        "Daily email activity per list. Clicks are the only email signal a "
        "model may use; opens are carried for reachability reporting and "
        "nothing else."
    ),
    inputs=("email_click", "email_open"),
    optional_inputs=("email_click", "email_open"),
    columns=(
        OutputColumn("list_id", "The list or newsletter."),
        OutputColumn("reader_id", "The reader."),
        OutputColumn(LOCAL_DATE_COLUMN, "Calendar day in the configured timezone."),
        OutputColumn(
            "clicks",
            "Click events on this list by this reader today. The unit is the "
            "click event, not the campaign clicked.",
        ),
        OutputColumn(
            "opens",
            "Open events. Reachability only. Machine opens inflate this and "
            "cannot be cleaned out of it, so an open says the message reached a "
            "live inbox and nothing about interest.",
        ),
    ),
    notes=(
        "Sends are not carried. The upstream table had them and the live "
        "modelling lane forbids them by name in two independent guards, so the "
        "column exists only to be rejected.",
        "The click unit is an open editorial question: clicks, or distinct "
        "campaigns clicked. This table answers it as click events, which is "
        "what the upstream table counted. Answering it as campaigns needs a "
        "`distinct_campaigns_clicked` column here -- the contract already "
        "carries `email_click.campaign_id`, so the input is not the blocker. "
        "It is deliberately not added in advance of the decision, because a "
        "column carrying an unmade choice gets read as if the choice were made.",
        "Each of the two inputs is independently optional. If only one is "
        "delivered, only its column is emitted -- the other is absent from the "
        "schema rather than present and zero, because zero opens and no open "
        "feed are different facts and only one of them is about the reader.",
    ),
)


READER_COMMUNITY_DAY = OutputTable(
    name="reader_community_day",
    grain="One row per reader per local day.",
    dedup_key=("reader_id", LOCAL_DATE_COLUMN),
    purpose=(
        "Daily community participation, as five counts of actions the reader "
        "performed. Splits into contribution and reaction downstream."
    ),
    inputs=("community_action",),
    optional_inputs=("community_action",),
    columns=(
        OutputColumn("reader_id", "The reader who performed the actions."),
        OutputColumn(LOCAL_DATE_COLUMN, "Calendar day in the configured timezone."),
        *(
            OutputColumn(
                column,
                f"Count of `{kind}` actions performed by this reader today, "
                "summed across community properties.",
            )
            for kind, column in COMMUNITY_ACTION_COLUMNS.items()
        ),
    ),
    notes=(
        "The site identifier is not in the grain. The downstream lane sums "
        "across it and never groups on it, and a single-property deployment "
        "carries one constant value, so keeping it in the key would split every "
        "reader-day for no reader.",
        "Every count is an action given, never received. A received reaction "
        "measures somebody else, and counting it here inverts the feature while "
        "leaving the magnitude plausible.",
    ),
)


SUBSCRIPTION_STATE_INTERVAL = OutputTable(
    name="subscription_state_interval",
    grain="One row per reader per state interval.",
    dedup_key=("reader_id", "start_ts"),
    purpose=(
        "Subscription state as half-open intervals, so a reader's state can be "
        "resolved as of any historical date. State decides which readers are "
        "fit and scored; it is never a model feature."
    ),
    inputs=("subscription_span",),
    columns=(
        OutputColumn("reader_id", "The reader."),
        OutputColumn("state", "The commercial state for the whole interval."),
        OutputColumn(
            "payer_type",
            "Who pays. Null means the billing system cannot say, and never 'individual'.",
            nullable=True,
        ),
        OutputColumn("start_ts", "Instant the interval begins, inclusive."),
        OutputColumn(
            "end_ts",
            "Instant the interval ends, EXCLUSIVE. Null means the span is still open.",
            nullable=True,
        ),
        OutputColumn(
            "start_date",
            "Local calendar day of start_ts in the configured timezone. Emitted "
            "so a window expressed in local days can be resolved against a span "
            "without re-deriving the boundary somewhere else in a different "
            "zone.",
        ),
        OutputColumn(
            "end_date",
            "Local calendar day of end_ts, still exclusive. Null for an open span.",
            nullable=True,
        ),
        OutputColumn("is_open", "True when the span has no end."),
    ),
    notes=(
        "Two upstream columns are dropped: a registration state emitted as the "
        "same literal on every row, and a provenance string. Both are constants "
        "that read like data.",
        "Half-open on the instants, so consecutive spans meet without "
        "overlapping and without a one-unit gap. The local dates are the "
        "calendar days of those instants and inherit the same convention: "
        "end_date is the first day NOT covered.",
    ),
)


#: Build order. Later tables read earlier ones, so this is a dependency order
#: and not a preference.
OUTPUTS: tuple[OutputTable, ...] = (
    CONTENT_DIMENSION,
    READER_CONTENT_DAY,
    READER_CHANNEL_DAY,
    READER_SECTION_DAY,
    READER_EMAIL_DAY,
    READER_COMMUNITY_DAY,
    SUBSCRIPTION_STATE_INTERVAL,
)

OUTPUTS_BY_NAME: dict[str, OutputTable] = {table.name: table for table in OUTPUTS}

PUBLISHED_OUTPUTS: tuple[OutputTable, ...] = tuple(t for t in OUTPUTS if t.published)


@dataclass(frozen=True)
class NotBuilt:
    """A table the upstream system builds and this one deliberately does not."""

    name: str
    upstream_columns: str
    reason: str


#: The not-porting list, carried verbatim from the census that produced it.
#:
#: Here rather than only in prose because "we decided not to build this" is a
#: fact a reader of the code needs, and because the list is the answer to the
#: question somebody will otherwise answer by building one of them again.
NOT_BUILT: tuple[NotBuilt, ...] = (
    NotBuilt(
        name="user_author_day",
        upstream_columns="11",
        reason=("Zero lane references. Author-level preference is not in any published model."),
    ),
    NotBuilt(
        name="user_content_type_day",
        upstream_columns="11",
        reason=(
            "Zero lane references. Content-type preference is in no published model, and the "
            "type itself is already on the content dimension for the one reader that needs "
            "it -- the article-view predicate."
        ),
    ),
    NotBuilt(
        name="user_device_day",
        upstream_columns="12",
        reason=(
            "Zero lane references, and the most expensive table in the build: it "
            "re-reads the raw web and app event feeds from scratch rather than "
            "deriving from the consumption table. Dropping it alone removes a "
            "second full scan of the raw event feeds."
        ),
    ),
    NotBuilt(
        name="person_day_activity_v1",
        upstream_columns="60+",
        reason=("Built by the local pipeline, declared in no contract, consumed by no lane."),
    ),
    NotBuilt(
        name="email_user_day (v1)",
        upstream_columns="8",
        reason=(
            "Superseded by the v2 email table. Its engagement fields have been "
            "zero since its source feed stopped loading, and before that it "
            "under-captured clicks non-deterministically."
        ),
    ),
    NotBuilt(
        name="web_user_content_day, app_user_content_day",
        upstream_columns="12 each",
        reason=(
            "The only consumer was a business-segment overlay reading a views "
            "anchor, which the channel table supplies equivalently. This build "
            "has no per-content published table at all: views are counted from "
            "the events, so there is nothing to reconcile against."
        ),
    ),
)

#: Why there is no deduplication layer, stated rather than left as an absence.
#:
#: The draft of this work budgeted for one, on the assumption the contract might
#: carry raw-ish events needing a dedupe on (reader, event name, timestamp,
#: session discriminator) with a preference for the row carrying more engagement
#: time. The contract as landed makes that layer unnecessary: `reader_event`
#: declares a non-nullable `event_id` as its deduplication key and requires a
#: re-delivered event to reuse its id, and the contract validator refuses a
#: delivery with a repeated key. Events therefore arrive deduplicated, and a
#: dedupe here would be a pass that can only ever be a no-op -- while looking,
#: to anyone reading the code later, like the thing that guarantees uniqueness.
DEDUPLICATION_LAYER_NOTE = (
    "No event-deduplication layer is built. The contract's reader_event table "
    "declares a non-nullable event_id as its deduplication key, requires a "
    "re-delivered event to reuse that id, and its validator refuses a delivery "
    "with a repeated key -- so events arrive pre-deduplicated and a dedupe pass "
    "here could only ever be a no-op. It is recorded rather than omitted "
    "silently, because an absent layer nobody wrote down reads later as an "
    "oversight, and the obvious fix is to add a pass that hides the guarantee "
    "it duplicates."
)
