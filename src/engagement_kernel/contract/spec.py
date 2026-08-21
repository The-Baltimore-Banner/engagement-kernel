"""The canonical input contract, declared as data.

Everything a producer has to satisfy is in the ``TABLES`` tuple below: seven
tables, each with a grain, a deduplication key, a stated null behaviour, and a
field list where every field carries a type, a nullability rule, its enum
vocabulary where it has one, and a one-line semantic definition. There is no
branching to read. A reviewer who wants to know what is required reads this
module top to bottom and stops.

Three properties of the shape are deliberate, and each one exists to make a
specific class of quiet wrongness impossible rather than merely discouraged.

**Reader activity enters as events with an instant, not as pre-bucketed days.**
Every timestamp column is a timezone-aware instant. No table carries a
calendar-date column, and an unexpected one is rejected by name. A day-grain
input cannot show that its bucketing used the timezone the analysis is supposed
to use, so a contract that accepts one silently inherits whatever boundary each
source happened to apply -- which is how evening clicks land on the next day
and Saturday-evening clicks land in the next week's bin. The single configured
timezone in the manifest is applied once, by the engine, to every channel.

**One reader id, at one declared grain, in one id space.** ``reader`` is the
registry: it names each reader once and declares its grain. Every other table
references it, and a reference to a reader that is not in the registry is an
error. So "the same id space across reader events, email and community" is a
referential-integrity property, not a request in prose.

**Optional inputs are absent, not zero.** An optional table's absence is
declared in the manifest with a reason, and the reference engine answers by
selecting a named alternate feature set. Nothing fills a missing input with 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa

from engagement_kernel.contract import enums

#: Semantic version of the contract. The major version changes when a producer
#: that satisfied the previous version can no longer satisfy this one.
CONTRACT_VERSION = "1.0.0"

#: The name a manifest must declare, so a directory of files cannot be mistaken
#: for a different contract that happens to share table names.
CONTRACT_NAME = "engagement-kernel-input"


# --- declaration types ------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One column: its type, whether it may be null, and what it means."""

    name: str
    arrow_type: pa.DataType
    nullable: bool
    definition: str
    enum: tuple[str, ...] | None = None
    non_negative: bool = False

    def arrow_field(self) -> pa.Field:
        return pa.field(self.name, self.arrow_type, nullable=self.nullable)


#: Requirement vocabulary for :class:`ConditionalRule`.
REQUIRE_NON_NULL = "non_null"
REQUIRE_NON_EMPTY_LIST = "non_empty_list"
REQUIRE_NULL_OR_EMPTY_LIST = "null_or_empty_list"


@dataclass(frozen=True)
class ConditionalRule:
    """A field requirement that depends on another field's value.

    Declared rather than coded so that "``content_id`` is required on a
    delivery event" is readable in the contract instead of buried in the
    validator.
    """

    rule_id: str
    when_column: str
    when_values: tuple[str, ...]
    then_column: str
    requirement: str
    definition: str


@dataclass(frozen=True)
class TableSpec:
    """One table in the contract."""

    name: str
    required: bool
    purpose: str
    grain: str
    dedup_key: tuple[str, ...]
    null_behaviour: str
    fields: tuple[FieldSpec, ...]
    #: Which feature block of the reference engine this table feeds. Used by
    #: :mod:`engagement_kernel.contract.degradation` to name what is lost when
    #: an optional table is absent.
    feature_block: str
    conditional_rules: tuple[ConditionalRule, ...] = ()
    #: The column holding a reader registry reference, if any. ``reader``
    #: itself is the registry, so it declares ``None``.
    reader_reference_column: str | None = "reader_id"
    #: Timestamp column used for the availability-floor check.
    event_time_column: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    def field_by_name(self, name: str) -> FieldSpec | None:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def arrow_schema(self) -> pa.Schema:
        return pa.schema([item.arrow_field() for item in self.fields])

    @property
    def filename(self) -> str:
        return f"{self.name}.parquet"


# --- shared types -----------------------------------------------------------

ID = pa.string()
#: A timezone-aware instant. The unit is declarative; the validator accepts any
#: timestamp unit but rejects a timezone-naive column outright, because a naive
#: timestamp is exactly the defect this contract exists to stop: it silently
#: inherits whichever zone the producing system happened to use.
INSTANT = pa.timestamp("us", tz="UTC")
SECONDS = pa.float64()
SECTION_LIST = pa.list_(pa.string())


# --- the tables -------------------------------------------------------------

READER = TableSpec(
    name="reader",
    required=True,
    feature_block="spine",
    purpose=(
        "The reader registry. Declares every reader the delivery covers, once, "
        "with its identity grain. Every other table references it."
    ),
    grain="One row per reader.",
    dedup_key=("reader_id",),
    null_behaviour=(
        "No nullable fields. A reader that cannot be named at the declared "
        "grain does not belong in the delivery at all."
    ),
    reader_reference_column=None,
    fields=(
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "Opaque pseudonymous identifier for one reader. Must not be, or "
                "encode, an email address, a login name, or any other personal "
                "datum, and must not carry a namespace prefix -- a prefixed id "
                "is the signature of two grains sharing one column."
            ),
        ),
        FieldSpec(
            name="id_grain",
            arrow_type=ID,
            nullable=False,
            enum=enums.READER_ID_GRAINS,
            definition=(
                "What the id identifies. Exactly one value is permitted at this "
                "contract version: a resolved person. A device, browser, app "
                "install or session is not a reader."
            ),
        ),
    ),
    notes=(
        "More than one distinct id_grain in this table is rejected as a "
        "mixed-grain id column, separately from the enum check, so the failure "
        "message names the actual problem.",
    ),
)


READER_EVENT = TableSpec(
    name="reader_event",
    required=True,
    feature_block="consumption",
    purpose=(
        "Web and app reading activity, one row per event, with the instant it "
        "happened. Views, distinct sessions, engagement time and raw event "
        "counts are all derived from this table by the reference engine, in the "
        "single timezone the manifest declares."
    ),
    grain="One row per reader event.",
    dedup_key=("event_id",),
    null_behaviour=(
        "content_id is null only on an interaction event. session_id is always "
        "present. engagement_time_seconds is null when attention was not "
        "measured, which is not the same as measured-and-zero: a null must "
        "never be read as 0.0."
    ),
    event_time_column="event_ts",
    fields=(
        FieldSpec(
            name="event_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "Stable opaque identifier for this event, unique across the "
                "delivery. Re-delivering the same event must reuse its id so "
                "the deduplication key can do its job."
            ),
        ),
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition="The reader who produced the event. Must exist in `reader`.",
        ),
        FieldSpec(
            name="event_ts",
            arrow_type=INSTANT,
            nullable=False,
            definition=(
                "The instant the event happened, timezone-aware. The calendar "
                "day it belongs to is computed by the engine in the manifest's "
                "timezone; the producer must not pre-bucket it."
            ),
        ),
        FieldSpec(
            name="channel",
            arrow_type=ID,
            nullable=False,
            enum=enums.READER_EVENT_CHANNELS,
            definition="Surface the event happened on.",
        ),
        FieldSpec(
            name="event_kind",
            arrow_type=ID,
            nullable=False,
            enum=enums.READER_EVENT_KINDS,
            definition=(
                "Whether content was delivered to the reader (a page or screen "
                "shown) or the reader interacted with content already shown. "
                "Only deliveries are candidates for a view; which deliveries "
                "count as an article view is set in the manifest, not here."
            ),
        ),
        FieldSpec(
            name="content_id",
            arrow_type=ID,
            nullable=True,
            definition=(
                "The content the event concerns. Required on a delivery. A "
                "content_id with no matching row in `content` is permitted and "
                "means the metadata did not resolve -- it is not an error."
            ),
        ),
        FieldSpec(
            name="session_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "Opaque identifier for the visit this event belongs to. Present "
                "so the engine can count distinct sessions per reader-day "
                "directly; a session count supplied as a pre-aggregated number "
                "cannot be checked and is not accepted."
            ),
        ),
        FieldSpec(
            name="engagement_time_seconds",
            arrow_type=SECONDS,
            nullable=True,
            non_negative=True,
            definition=(
                "Attention attributable to this event, in seconds. Null means "
                "not measured. Additive across events. Any rate derived from it "
                "is undefined below the minimum-deliveries threshold the "
                "contract declares (see ENGAGEMENT_TIME_MIN_DELIVERIES)."
            ),
        ),
    ),
    conditional_rules=(
        ConditionalRule(
            rule_id="delivery_requires_content_id",
            when_column="event_kind",
            when_values=(enums.EVENT_KIND_CONTENT_DELIVERY,),
            then_column="content_id",
            requirement=REQUIRE_NON_NULL,
            definition=(
                "A delivery with no content id cannot be attributed to a piece "
                "of content, so it cannot be a view of one."
            ),
        ),
    ),
    notes=("Scroll depth is deliberately not in this table. See SCROLL_DEPTH_SCOPE_NOTE.",),
)


CONTENT = TableSpec(
    name="content",
    required=True,
    feature_block="topic",
    purpose=(
        "The content dimension: what each piece of content is and which "
        "sections it belongs to. Its section list is what makes per-section "
        "reading measurable, with a view of content in n sections contributing "
        "1/n to each."
    ),
    grain="One row per piece of content.",
    dedup_key=("content_id",),
    null_behaviour=(
        "sections is null or empty exactly when section_resolution is "
        "'unresolved'. Unresolved is a declared outcome, not a gap: a reader "
        "whose reading all landed on unresolved content read something, and "
        "must not be reported as having read nothing."
    ),
    reader_reference_column=None,
    fields=(
        FieldSpec(
            name="content_id",
            arrow_type=ID,
            nullable=False,
            definition="Opaque identifier for the content, matching reader_event.content_id.",
        ),
        FieldSpec(
            name="content_type",
            arrow_type=ID,
            nullable=False,
            enum=enums.CONTENT_TYPES,
            definition=(
                "What kind of thing this is, in the publisher's own editorial "
                "taxonomy mapped onto the contract's vocabulary. The manifest "
                "says which of these types an article view may count."
            ),
        ),
        FieldSpec(
            name="section_resolution",
            arrow_type=ID,
            nullable=False,
            enum=enums.SECTION_RESOLUTIONS,
            definition=(
                "Whether this content's section metadata resolved to at least "
                "one usable section. Declared by the producer rather than "
                "inferred from an empty list, so 'we have no metadata' cannot "
                "be confused with 'we forgot the column'."
            ),
        ),
        FieldSpec(
            name="sections",
            arrow_type=SECTION_LIST,
            nullable=True,
            definition=(
                "The sections this content belongs to, as opaque section keys. "
                "Non-empty when resolved; null or empty when unresolved. Order "
                "carries no meaning and duplicates are not permitted."
            ),
        ),
        FieldSpec(
            name="published_ts",
            arrow_type=INSTANT,
            nullable=True,
            definition=(
                "When the content was first published, timezone-aware. Null "
                "when unknown. Not used for bucketing reader activity."
            ),
        ),
    ),
    conditional_rules=(
        ConditionalRule(
            rule_id="resolved_requires_sections",
            when_column="section_resolution",
            when_values=(enums.SECTION_RESOLUTION_RESOLVED,),
            then_column="sections",
            requirement=REQUIRE_NON_EMPTY_LIST,
            definition="Content declared resolved must name at least one section.",
        ),
        ConditionalRule(
            rule_id="unresolved_forbids_sections",
            when_column="section_resolution",
            when_values=(enums.SECTION_RESOLUTION_UNRESOLVED,),
            then_column="sections",
            requirement=REQUIRE_NULL_OR_EMPTY_LIST,
            definition=(
                "Content declared unresolved must not also carry sections; one "
                "of the two statements would be false."
            ),
        ),
    ),
)


SUBSCRIPTION_SPAN = TableSpec(
    name="subscription_span",
    required=True,
    feature_block="spine",
    purpose=(
        "Subscription state as a history of intervals, so a reader's status can "
        "be resolved as of any historical date. Status defines the spine -- "
        "which readers are fit and scored -- and is never a model feature."
    ),
    grain="One row per reader per state interval.",
    dedup_key=("reader_id", "start_ts"),
    null_behaviour=(
        "end_ts is null for an open span, and at most one span per reader may "
        "be open. payer_type is null when the billing system cannot "
        "distinguish payer types; null means unknown, never 'individual'."
    ),
    fields=(
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition="The reader whose state this is. Must exist in `reader`.",
        ),
        FieldSpec(
            name="state",
            arrow_type=ID,
            nullable=False,
            enum=enums.SUBSCRIPTION_STATES,
            definition=(
                "The reader's commercial state for the whole interval. Mapping "
                "a publisher's own billing states onto these seven is a "
                "business decision the publisher records; the contract fixes "
                "the vocabulary, not the mapping."
            ),
        ),
        FieldSpec(
            name="payer_type",
            arrow_type=ID,
            nullable=True,
            enum=enums.PAYER_TYPES,
            definition=(
                "Who pays for the subscription. Optional, because a state "
                "history alone cannot supply it. Never a model feature."
            ),
        ),
        FieldSpec(
            name="start_ts",
            arrow_type=INSTANT,
            nullable=False,
            definition="Instant the interval begins, inclusive, timezone-aware.",
        ),
        FieldSpec(
            name="end_ts",
            arrow_type=INSTANT,
            nullable=True,
            definition=(
                "Instant the interval ends, EXCLUSIVE, timezone-aware. Null "
                "means the span is still open. Intervals are half-open "
                "[start_ts, end_ts) so consecutive spans meet without "
                "overlapping and without a one-unit gap."
            ),
        ),
    ),
    notes=(
        "Population exclusions are manifest configuration, as a list of opaque "
        "reader ids. This table carries no personal field, so an exclusion "
        "predicate over a personal attribute is not expressible against it.",
    ),
)


EMAIL_CLICK = TableSpec(
    name="email_click",
    required=False,
    feature_block="email_cadence",
    purpose=(
        "Email clicks: the only email signal the models may use. A click is an "
        "intentional act by the reader."
    ),
    grain="One row per click event.",
    dedup_key=("event_id",),
    null_behaviour=(
        "campaign_id is null when the provider cannot attribute the click to a "
        "campaign. list_id is always present."
    ),
    event_time_column="event_ts",
    fields=(
        FieldSpec(
            name="event_id",
            arrow_type=ID,
            nullable=False,
            definition="Stable opaque identifier for this click event.",
        ),
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition="The reader who clicked. Must exist in `reader`.",
        ),
        FieldSpec(
            name="event_ts",
            arrow_type=INSTANT,
            nullable=False,
            definition=(
                "The instant of the click, timezone-aware. Not pre-bucketed: "
                "this is the input whose day boundary is most often inherited "
                "from a vendor's own zone."
            ),
        ),
        FieldSpec(
            name="list_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "Opaque identifier for the list or newsletter the clicked "
                "message belonged to. Present so a deployment can restrict the "
                "email cadence signal to specific lists."
            ),
        ),
        FieldSpec(
            name="campaign_id",
            arrow_type=ID,
            nullable=True,
            definition=(
                "Opaque identifier for the individual send. Present so distinct "
                "campaigns clicked can be counted as well as click events; "
                "which of the two a model uses is a modelling decision recorded "
                "as an open question, not a property of this table."
            ),
        ),
    ),
    notes=(
        "THE CLICK UNIT IS ONE ROW PER CLICK EVENT. Not one row per campaign "
        "clicked, not one row per recipient, not one row per day. A provider "
        "that supplies opens here is non-conformant, and no validator can "
        "detect it from the values -- which is exactly why opens have their own "
        "table with its own declared permitted use rather than a flag on this "
        "one.",
    ),
)


EMAIL_OPEN = TableSpec(
    name="email_open",
    required=False,
    feature_block="deliverability",
    purpose=(
        "Email opens, for reachability only. Machine opens inflate this signal "
        "and cannot be cleaned out of it, so an open says the message reached a "
        "reachable inbox and nothing about the reader's interest."
    ),
    grain="One row per open event.",
    dedup_key=("event_id",),
    null_behaviour="As email_click.",
    event_time_column="event_ts",
    fields=(
        FieldSpec(
            name="event_id",
            arrow_type=ID,
            nullable=False,
            definition="Stable opaque identifier for this open event.",
        ),
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition="The reader whose message was opened. Must exist in `reader`.",
        ),
        FieldSpec(
            name="event_ts",
            arrow_type=INSTANT,
            nullable=False,
            definition="The instant of the open, timezone-aware.",
        ),
        FieldSpec(
            name="list_id",
            arrow_type=ID,
            nullable=False,
            definition="Opaque identifier for the list or newsletter.",
        ),
        FieldSpec(
            name="campaign_id",
            arrow_type=ID,
            nullable=True,
            definition="Opaque identifier for the individual send. Null when unattributed.",
        ),
    ),
    notes=(
        "PERMITTED USE: reachability and deliverability reporting only. This "
        "table is a separate table, in a separate feature block, precisely so "
        "that 'opens are never a model feature' is structural rather than a "
        "comment on a column.",
    ),
)


COMMUNITY_ACTION = TableSpec(
    name="community_action",
    required=False,
    feature_block="community",
    purpose=(
        "Community participation: actions the reader performed on a comment "
        "surface. Splits into contribution (authoring) and reaction "
        "(responding to others) downstream."
    ),
    grain="One row per action.",
    dedup_key=("event_id",),
    null_behaviour=(
        "site_id is always present: a single-property deployment supplies one "
        "constant value rather than a null, so the column never has to be read "
        "as 'unknown'. target_content_id is null when the action was not "
        "attached to a piece of content."
    ),
    event_time_column="event_ts",
    fields=(
        FieldSpec(
            name="event_id",
            arrow_type=ID,
            nullable=False,
            definition="Stable opaque identifier for this action.",
        ),
        FieldSpec(
            name="reader_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "The reader who PERFORMED the action. Never the reader who "
                "received it. Must exist in `reader`."
            ),
        ),
        FieldSpec(
            name="event_ts",
            arrow_type=INSTANT,
            nullable=False,
            definition="The instant of the action, timezone-aware.",
        ),
        FieldSpec(
            name="action_kind",
            arrow_type=ID,
            nullable=False,
            enum=enums.COMMUNITY_ACTION_KINDS,
            definition=(
                "What the reader did. Every value is an action given or "
                "authored by this reader: 'like_given' is a like this reader "
                "handed out, not one they received. There is no received-side "
                "value, because a received reaction measures somebody else."
            ),
        ),
        FieldSpec(
            name="site_id",
            arrow_type=ID,
            nullable=False,
            definition=(
                "Opaque identifier for the community property the action "
                "happened on. Required so a multi-property deployment stays "
                "expressible; the engine sums across it by default."
            ),
        ),
        FieldSpec(
            name="target_content_id",
            arrow_type=ID,
            nullable=True,
            definition=(
                "The content the action was attached to, when there is one. "
                "Null for actions not tied to a piece of content."
            ),
        ),
    ),
)


#: Every table in the contract, in reading order.
TABLES: tuple[TableSpec, ...] = (
    READER,
    READER_EVENT,
    CONTENT,
    SUBSCRIPTION_SPAN,
    EMAIL_CLICK,
    EMAIL_OPEN,
    COMMUNITY_ACTION,
)

TABLES_BY_NAME: dict[str, TableSpec] = {table.name: table for table in TABLES}

REQUIRED_TABLES: tuple[TableSpec, ...] = tuple(t for t in TABLES if t.required)
OPTIONAL_TABLES: tuple[TableSpec, ...] = tuple(t for t in TABLES if not t.required)


# --- declared thresholds and exclusions -------------------------------------

#: Minimum content deliveries in a window below which any per-view rate derived
#: from engagement time is UNDEFINED rather than zero. Carried in the contract
#: because "nullable" alone is not enough: a time-per-view computed on one
#: delivery is noise, and reporting it as a small number is worse than
#: reporting nothing.
ENGAGEMENT_TIME_MIN_DELIVERIES = 3

SCROLL_DEPTH_SCOPE_NOTE = (
    "Scroll depth is deliberately OUT OF SCOPE. It is not a field in any table "
    "and a column whose name contains 'scroll' is rejected. It is excluded on "
    "evidence, not on principle: where it has been measured it was carried on "
    "several aggregates and read by nothing, and on app surfaces it is commonly "
    "not measurable at all, so a mixed-surface deployment would be comparing a "
    "real number against a hardcoded zero."
)

#: Column-name substrings that are refused with a specific reason rather than a
#: generic 'unexpected column', so the message names the actual problem.
FORBIDDEN_COLUMN_REASONS: tuple[tuple[str, str], ...] = (
    (
        "scroll",
        "scroll depth is declared out of scope by this contract",
    ),
    (
        "event_date",
        "a pre-bucketed calendar date re-imports the day-boundary defect this "
        "contract exists to prevent; supply the event instant instead",
    ),
    (
        "local_date",
        "a pre-bucketed calendar date re-imports the day-boundary defect this "
        "contract exists to prevent; supply the event instant instead",
    ),
    ("email", "the contract requires no personal data and must not carry an email address"),
    ("ip_address", "the contract requires no personal data and must not carry an IP address"),
    ("ip_addr", "the contract requires no personal data and must not carry an IP address"),
    ("phone", "the contract requires no personal data and must not carry a phone number"),
    ("first_name", "the contract requires no personal data and must not carry a person's name"),
    ("last_name", "the contract requires no personal data and must not carry a person's name"),
    ("full_name", "the contract requires no personal data and must not carry a person's name"),
    ("postal", "the contract requires no personal data and must not carry a postal address"),
    ("birth", "the contract requires no personal data and must not carry a date of birth"),
)

#: Field names that must never reach a model matrix, however they arrive. The
#: reference engine enforces this by name; the list lives here so the contract
#: and the engine cannot drift apart.
FORBIDDEN_MODEL_FEATURE_SOURCES: tuple[str, ...] = (
    "state",
    "payer_type",
    "email_open",
    "opens",
    "sends",
)


# --- type comparison --------------------------------------------------------


def types_compatible(declared: pa.DataType, actual: pa.DataType) -> bool:
    """Is ``actual`` an acceptable physical type for a ``declared`` field?

    Deliberately not ``declared == actual``. Two normalisations are needed, and
    both were chosen because the strict comparison refuses a healthy file:

    * **List child names.** Arrow names a list's child field ``item``; a
      Parquet round-trip commonly renames it ``element``. The child *name*
      carries no meaning, so only the value type is compared.
    * **Timestamp unit.** A producer writing milliseconds is not wrong. What
      *is* wrong is a timezone-naive timestamp, because it silently inherits
      whichever zone the producing system used -- so awareness is compared and
      the unit is not.

    Everything else is compared exactly. In particular an integer supplied for
    a float field, or a string supplied for a timestamp field, is a mismatch:
    coercing it is how a reader turns a label into a number and a date into
    nothing.
    """
    if pa.types.is_timestamp(declared) and pa.types.is_timestamp(actual):
        return (declared.tz is None) == (actual.tz is None)
    if pa.types.is_list(declared) and pa.types.is_list(actual):
        return types_compatible(declared.value_type, actual.value_type)
    return bool(declared.equals(actual))
