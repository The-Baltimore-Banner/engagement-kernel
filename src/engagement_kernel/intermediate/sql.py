"""The build, as named DuckDB statements.

Each statement is generated from the resolved :class:`BuildConfig` and returned
in a registry keyed by statement name, so three things are possible that a wall
of inline SQL does not allow: the exact text can be printed for review, one
statement can be replaced by a deliberately wrong variant to prove a check
catches it, and a reader can find the one place a derivation lives.

Four derivations here are load-bearing, and in each one the obvious rewrite is
wrong in a way that produces a plausible number:

**One timezone, applied once, to every channel.** Every local day in this build
comes from ``AT TIME ZONE`` on a timezone-aware instant, with the zone name
inlined from the manifest. Note what is *not* used: a bare
``CAST(instant AS DATE)``. In DuckDB that cast is evaluated in the *session*
timezone, so the same query returns different days on two machines, and on a
developer's laptop set to the publisher's own zone it returns the right answer
for the wrong reason. The build also pins the session zone to UTC, so a stray
cast that slips in later produces an obviously wrong day rather than a silently
correct one.

**Distinct sessions is a per-reader-day count carried down, then maximised.**
Not the sum of the per-content counts. A reader who read three articles in one
visit has one session and three per-content sessions of one.

**Section attribution is fractional.** A view of content in *n* sections
contributes ``1/n`` to each, so a day's section views sum to the day's views.

**Every output is ordered by its deduplication key.** Not cosmetic. A DuckDB
``GROUP BY`` returns rows in whatever order the plan produced them, which varies
between runs and between machines -- so without an explicit order two builds of
the same delivery produce byte-different Parquet files, a reproducibility claim
becomes unverifiable, and a test comparing two runs fails for a reason that has
nothing to do with the numbers.

**Unresolved metadata routes to a sentinel.** Three input shapes mean
"unresolved": a declared-unresolved row with a null section list, the same with
an empty list, and a content id with no row in ``content`` at all. All three
reach the sentinel, and a view of unresolved content is still a view -- dropping
it would turn "we do not know what they read" into "they read nothing", which is
the distinction the downstream reason codes are built on.
"""

from __future__ import annotations

from engagement_kernel.contract import enums, spec
from engagement_kernel.intermediate import tables
from engagement_kernel.intermediate.config import BuildConfig

#: View names the contract inputs are registered under.
INPUT_VIEW_PREFIX = "input_"

#: Working table holding one row per reader event with the local day, the
#: article-view verdict and the joined content metadata. Not an output.
EVENT_LAYER = "ek_event_layer"

#: Working table holding the per-reader-per-channel-per-day distinct session
#: count over view events. Not an output.
SESSION_LAYER = "ek_session_layer"


def input_view(table_name: str) -> str:
    return f"{INPUT_VIEW_PREFIX}{table_name}"


def _quote(value: str) -> str:
    """Single-quote a literal for inlining, doubling any embedded quote.

    Every value inlined by this module comes from the contract's own closed
    vocabularies or from an IANA timezone name the manifest already validated,
    so this is a correctness guard rather than the security boundary -- but a
    manifest is a file somebody edits, and an unescaped quote would turn a
    malformed timezone name into malformed SQL.
    """
    return "'" + value.replace("'", "''") + "'"


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(_quote(value) for value in values)


def local_date_expr(column: str, timezone: str) -> str:
    """The one expression in this build that turns an instant into a day."""
    return f"CAST({column} AT TIME ZONE {_quote(timezone)} AS DATE)"


def article_view_predicate(config: BuildConfig, *, event: str = "e", content: str = "c") -> str:
    """The article-view definition, resolved once, as one SQL predicate.

    Three conditions, and one deliberate asymmetry.

    The conditions: the event is of a kind the definition admits; it names a
    piece of content; and that content's type is one the definition counts.

    The asymmetry: when the content id has **no row** in ``content`` the type is
    unknown, and the delivery still counts as a view. The contract states
    plainly that an unmatched content id means the metadata did not resolve and
    is not an error, so the alternative is to drop the view -- which collapses
    "unresolved metadata" into "no reading", the exact distinction the section
    table exists to preserve. The volume is not hidden: every one of these views
    lands on the unresolved sentinel section, where it can be counted.
    """
    kinds = _in_list(config.article_view.event_kinds)
    types = _in_list(config.article_view.content_types)
    return (
        f"{event}.event_kind IN ({kinds})\n"
        f"      AND {event}.content_id IS NOT NULL\n"
        f"      AND ({content}.content_type IS NULL OR {content}.content_type IN ({types}))"
    )


def sections_expr(config: BuildConfig, *, alias: str = "c") -> str:
    """The effective section list for a piece of content, sentinel included.

    The declared ``section_resolution`` is tested first, so the sentinel is
    traceable to a statement the producer made rather than inferred from a list
    that happened to be empty. The null-or-empty test that follows is not
    redundant belt-and-braces: it is what makes a content row absent from
    ``content`` altogether -- where every joined column is null, including the
    resolution -- land on the sentinel as well.
    """
    sentinel = _quote(config.unresolved_section)
    resolved = _quote(enums.SECTION_RESOLUTION_RESOLVED)
    return (
        f"CASE\n"
        f"        WHEN {alias}.section_resolution = {resolved}\n"
        f"         AND {alias}.sections IS NOT NULL\n"
        f"         AND len({alias}.sections) > 0\n"
        f"        THEN {alias}.sections\n"
        f"        ELSE [{sentinel}]\n"
        f"      END"
    )


# --- the statements ---------------------------------------------------------


def _event_layer(config: BuildConfig) -> str:
    return f"""
CREATE OR REPLACE TABLE {EVENT_LAYER} AS
SELECT
    e.event_id,
    e.reader_id,
    e.event_ts,
    {local_date_expr("e.event_ts", config.day_boundary_timezone)} AS local_date,
    e.channel,
    e.event_kind,
    e.content_id,
    e.session_id,
    e.engagement_time_seconds,
    c.content_id IS NOT NULL AS content_metadata_present,
    c.content_type,
    c.section_resolution,
    c.sections,
    ({article_view_predicate(config)}) AS is_view
FROM {input_view("reader_event")} e
LEFT JOIN {input_view("content")} c
  ON c.content_id = e.content_id
"""


def _session_layer(config: BuildConfig) -> str:
    """Distinct sessions per reader per channel per local day, over views only.

    Computed here, once, at the reader-day grain -- which is the whole reason the
    channel table can recover it with a maximum instead of a sum. Restricted to
    view events because the anchor of a consumption day is a view: counting
    sessions that contained only interactions would credit a session in which
    the reader opened nothing.
    """
    del config  # the predicate is already resolved into the event layer
    return f"""
CREATE OR REPLACE TABLE {SESSION_LAYER} AS
SELECT
    reader_id,
    channel,
    local_date,
    COUNT(DISTINCT session_id) AS distinct_sessions_day
FROM {EVENT_LAYER}
WHERE is_view
GROUP BY reader_id, channel, local_date
"""


def _content_dimension(config: BuildConfig) -> str:
    del config
    return f"""
CREATE OR REPLACE TABLE {tables.CONTENT_DIMENSION.name} AS
SELECT
    content_id,
    content_type,
    section_resolution,
    sections
FROM {input_view("content")}
ORDER BY content_id
"""


def _reader_content_day(config: BuildConfig) -> str:
    """Per-content consumption, with the reader-day session count carried in.

    ``events`` counts every event attributable to this content today, including
    interactions, which is why it is computed without the view filter while
    every other measure is computed with it.
    """
    del config
    return f"""
CREATE OR REPLACE TABLE {tables.READER_CONTENT_DAY.name} AS
WITH per_content AS (
  SELECT
      reader_id,
      channel,
      content_id,
      local_date,
      COUNT(*) FILTER (WHERE is_view) AS views,
      COUNT(DISTINCT session_id) FILTER (WHERE is_view) AS sessions,
      SUM(engagement_time_seconds) FILTER (WHERE is_view) AS total_time_seconds,
      COUNT(engagement_time_seconds) FILTER (WHERE is_view) AS measured_time_deliveries,
      COUNT(*) AS events
  FROM {EVENT_LAYER}
  WHERE content_id IS NOT NULL
  GROUP BY reader_id, channel, content_id, local_date
)
SELECT
    p.reader_id,
    p.channel,
    p.content_id,
    p.local_date,
    p.views,
    p.sessions,
    s.distinct_sessions_day,
    p.total_time_seconds,
    p.measured_time_deliveries,
    p.events
FROM per_content p
JOIN {SESSION_LAYER} s
  ON s.reader_id = p.reader_id
 AND s.channel = p.channel
 AND s.local_date = p.local_date
WHERE p.views > 0
ORDER BY p.reader_id, p.channel, p.content_id, p.local_date
"""


def _reader_channel_day(config: BuildConfig) -> str:
    """The channel table. ``MAX(distinct_sessions_day)``, never ``SUM(sessions)``.

    The carried count is identical on every row of the reader-channel-day, so
    the maximum recovers it exactly. Writing ``SUM(sessions)`` instead is the
    single easiest mistake in this build and the hardest to see afterwards: it
    is larger, it is plausible, and a views-per-session feature divides by it.
    """
    del config
    # The casts are not decoration. A DuckDB SUM over an integer widens to a
    # 38-digit decimal, which arrives in Arrow as decimal128 and in pandas as a
    # column of Decimal objects: arithmetic against a float raises, and the
    # obvious fix is a silent astype somewhere downstream.
    return f"""
CREATE OR REPLACE TABLE {tables.READER_CHANNEL_DAY.name} AS
SELECT
    reader_id,
    channel,
    local_date,
    CAST(SUM(views) AS BIGINT) AS views,
    MAX(distinct_sessions_day) AS sessions,
    SUM(total_time_seconds) AS total_time_seconds,
    CAST(SUM(measured_time_deliveries) AS BIGINT) AS measured_time_deliveries,
    CAST(SUM(events) AS BIGINT) AS events
FROM {tables.READER_CONTENT_DAY.name}
GROUP BY reader_id, channel, local_date
ORDER BY reader_id, channel, local_date
"""


def _reader_section_day(config: BuildConfig) -> str:
    """Fractional section attribution.

    ``1.0 / len(sections)`` is computed before the unnest, so every section of a
    piece of content gets the same weight and the weights sum to exactly one per
    view. The sentinel list has one element, so a view of unresolved content
    carries its whole weight to the sentinel -- which is what makes the day's
    section views reconcile to the day's views even when metadata is missing.

    No channel in the grain, matching the table's declared grain: the same
    article read on two surfaces is one topic interest.
    """
    return f"""
CREATE OR REPLACE TABLE {tables.READER_SECTION_DAY.name} AS
WITH weighted AS (
  SELECT
      d.reader_id,
      d.local_date,
      d.content_id,
      d.views,
      d.total_time_seconds,
      {sections_expr(config)} AS effective_sections
  FROM {tables.READER_CONTENT_DAY.name} d
  LEFT JOIN {input_view("content")} c
    ON c.content_id = d.content_id
),
exploded AS (
  SELECT
      w.reader_id,
      w.local_date,
      w.content_id,
      w.views,
      w.total_time_seconds,
      1.0 / len(w.effective_sections) AS section_weight,
      s.section
  FROM weighted w
  CROSS JOIN UNNEST(w.effective_sections) AS s(section)
)
SELECT
    reader_id,
    section,
    local_date,
    SUM(views * section_weight) AS section_views,
    SUM(total_time_seconds * section_weight) AS section_time_seconds,
    COUNT(DISTINCT content_id) AS distinct_content_ids
FROM exploded
GROUP BY reader_id, section, local_date
ORDER BY reader_id, section, local_date
"""


def _reader_email_day(config: BuildConfig, *, clicks: bool, opens: bool) -> str:
    """Daily email activity, carrying only the inputs that were delivered.

    The union-then-sum shape is on purpose. A full outer join of two
    aggregations has to reconcile three key columns across a pair of nullable
    sides, and every one of those coalesces is a place a null key silently
    becomes its own group. Tagging each row with which measure it is and summing
    once has no null keys at all.

    When one input is absent its column is not emitted. Not emitted as zero: a
    reader with no open feed has not failed to open anything.
    """
    if not clicks and not opens:  # pragma: no cover - the caller skips the table
        raise ValueError("reader_email_day needs at least one of email_click, email_open")
    tz = config.day_boundary_timezone
    parts = []
    if clicks:
        parts.append(
            f"""  SELECT
      reader_id,
      list_id,
      {local_date_expr("event_ts", tz)} AS local_date,
      1 AS click,
      0 AS open_event
  FROM {input_view("email_click")}"""
        )
    if opens:
        parts.append(
            f"""  SELECT
      reader_id,
      list_id,
      {local_date_expr("event_ts", tz)} AS local_date,
      0 AS click,
      1 AS open_event
  FROM {input_view("email_open")}"""
        )
    measures = []
    if clicks:
        measures.append("    CAST(SUM(click) AS BIGINT) AS clicks")
    if opens:
        measures.append("    CAST(SUM(open_event) AS BIGINT) AS opens")
    union = "\n  UNION ALL\n".join(parts)
    # Joined before the f-string: nesting the same quote character, and a
    # backslash escape, inside an f-string expression is a syntax error before
    # Python 3.12, and this package supports 3.11.
    measure_block = ",\n".join(measures)
    return f"""
CREATE OR REPLACE TABLE {tables.READER_EMAIL_DAY.name} AS
WITH tagged AS (
{union}
)
SELECT
    list_id,
    reader_id,
    local_date,
{measure_block}
FROM tagged
GROUP BY list_id, reader_id, local_date
ORDER BY list_id, reader_id, local_date
"""


def _reader_community_day(config: BuildConfig) -> str:
    """Five counts of actions the reader performed, summed across properties."""
    counts = ",\n".join(
        f"    COUNT(*) FILTER (WHERE action_kind = {_quote(kind)}) AS {column}"
        for kind, column in tables.COMMUNITY_ACTION_COLUMNS.items()
    )
    return f"""
CREATE OR REPLACE TABLE {tables.READER_COMMUNITY_DAY.name} AS
SELECT
    reader_id,
    {local_date_expr("event_ts", config.day_boundary_timezone)} AS local_date,
{counts}
FROM {input_view("community_action")}
GROUP BY reader_id, local_date
ORDER BY reader_id, local_date
"""


def _subscription_state_interval(config: BuildConfig) -> str:
    """Spans, verbatim, plus their local calendar days.

    Both instants and local dates are carried. The instants are the truth; the
    dates exist so a window expressed in local days can be resolved against a
    span without some other layer re-deriving the boundary in a different zone,
    which is how a one-day entitlement error appears on one channel only.
    """
    tz = config.day_boundary_timezone
    return f"""
CREATE OR REPLACE TABLE {tables.SUBSCRIPTION_STATE_INTERVAL.name} AS
SELECT
    reader_id,
    state,
    payer_type,
    start_ts,
    end_ts,
    {local_date_expr("start_ts", tz)} AS start_date,
    {local_date_expr("end_ts", tz)} AS end_date,
    end_ts IS NULL AS is_open
FROM {input_view("subscription_span")}
ORDER BY reader_id, start_ts
"""


#: Statement names, in execution order. The two working layers are not outputs;
#: everything after them is, and each output table's statement is named after
#: the table so a mutation can be aimed at one derivation.
STATEMENT_EVENT_LAYER = EVENT_LAYER
STATEMENT_SESSION_LAYER = SESSION_LAYER


def build_statements(
    config: BuildConfig,
    *,
    available_inputs: frozenset[str],
) -> dict[str, str]:
    """Every statement this build will run, in order, keyed by name.

    ``available_inputs`` names the contract tables actually present. A table
    whose optional input is missing is left out of the plan entirely rather than
    built empty, so its absence is visible in the build report instead of
    arriving downstream as a reader who never clicked.
    """
    statements: dict[str, str] = {
        STATEMENT_EVENT_LAYER: _event_layer(config),
        STATEMENT_SESSION_LAYER: _session_layer(config),
        tables.CONTENT_DIMENSION.name: _content_dimension(config),
        tables.READER_CONTENT_DAY.name: _reader_content_day(config),
        tables.READER_CHANNEL_DAY.name: _reader_channel_day(config),
        tables.READER_SECTION_DAY.name: _reader_section_day(config),
    }

    clicks = "email_click" in available_inputs
    opens = "email_open" in available_inputs
    if clicks or opens:
        statements[tables.READER_EMAIL_DAY.name] = _reader_email_day(
            config, clicks=clicks, opens=opens
        )
    if "community_action" in available_inputs:
        statements[tables.READER_COMMUNITY_DAY.name] = _reader_community_day(config)
    statements[tables.SUBSCRIPTION_STATE_INTERVAL.name] = _subscription_state_interval(config)
    return statements


#: Contract tables that must be present for the build to run at all.
REQUIRED_INPUTS: tuple[str, ...] = tuple(table.name for table in spec.REQUIRED_TABLES)
