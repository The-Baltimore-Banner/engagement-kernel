"""What the build asserts about its own output, and why each assertion exists.

Every check here guards a derivation whose wrong version produces a number that
looks right. That is the selection criterion: a check that only catches a crash
is not worth running, because a crash announces itself.

The checks run on every build, not only in tests. A grain that nothing enforces
is how two rows per key arrive and every downstream count quietly doubles; a
reconciliation that lives in a test helper is how the upstream system ended up
with an attribution check that had never once run in production.

All of them run before any raises, so one failure cannot mask another, and the
raised error names every check that failed with its own detail. That matters for
the negative controls: a control has to fail *for its own reason*, and the only
way to show that is to see exactly which checks it tripped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engagement_kernel.intermediate import sql, tables
from engagement_kernel.intermediate.config import BuildConfig
from engagement_kernel.intermediate.session import rows

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

#: Names, so a test or a control can refer to a check without matching prose.
CHECK_DEDUP_KEYS = "dedup_keys_are_unique"
CHECK_SESSIONS_MAXIMISED = "sessions_are_maximised_not_summed"
CHECK_SECTION_ATTRIBUTION = "section_attribution_reconciles"
CHECK_UNRESOLVED_SENTINEL = "unresolved_metadata_is_its_own_outcome"
CHECK_SECTION_NEVER_NULL = "section_is_never_null"
CHECK_DAY_BOUNDARY_EVENTS = "one_timezone_applied_to_reader_events"
CHECK_DAY_BOUNDARY_EMAIL = "one_timezone_applied_to_email"
CHECK_DAY_BOUNDARY_COMMUNITY = "one_timezone_applied_to_community"

#: How many offending rows a failure detail names. Enough to debug from, few
#: enough that a wholly broken build does not print a table.
SAMPLE_LIMIT = 5

#: Absolute tolerance for the fractional-attribution reconciliation. The weights
#: are reciprocals of small integers, so a real mismatch is off by a whole view
#: or a clean fraction of one -- orders of magnitude above this.
RECONCILIATION_TOLERANCE = 1e-9


class IntermediateCheckError(RuntimeError):
    """The build produced output that fails one of its own invariants.

    Raised rather than reported, because every check here guards a number that
    something downstream will use. Handing back a table that failed one of them
    is worse than failing: the numbers are plausible.
    """

    def __init__(self, failures: tuple[CheckResult, ...]) -> None:
        self.failures = failures
        names = ", ".join(item.name for item in failures)
        detail = "\n\n".join(item.render() for item in failures)
        super().__init__(f"intermediate build failed {len(failures)} check(s): {names}\n\n{detail}")


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict."""

    name: str
    passed: bool
    #: What the check looked at, stated whether it passed or failed, so a
    #: passing report is readable evidence rather than a row of ticks.
    subject: str
    detail: str = ""
    sample: tuple[dict[str, Any], ...] = ()

    def render(self) -> str:
        head = f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.subject}"
        if self.passed and not self.detail:
            return head
        lines = [head]
        if self.detail:
            lines.append(f"  {self.detail}")
        for row in self.sample:
            lines.append(f"    {row}")
        return "\n".join(lines)


def _sample(con: duckdb.DuckDBPyConnection, statement: str) -> tuple[dict[str, Any], ...]:
    """The first few offending rows, in a stable order.

    ``ORDER BY ALL`` is not decoration. A bare ``LIMIT`` over a group-by returns
    whichever rows the plan produced first, which varies between runs -- so the
    same defect prints different examples each time, the captured evidence
    document is different on every render, and the staleness test that keeps that
    document honest cannot exist.
    """
    ordered = f"SELECT * FROM (\n{statement}\n) ORDER BY ALL LIMIT {SAMPLE_LIMIT}"
    return tuple(rows(con, ordered))


def _count(con: duckdb.DuckDBPyConnection, statement: str) -> int:
    result = rows(con, f"SELECT COUNT(*) AS n FROM (\n{statement}\n)")
    return int(result[0]["n"])


# --- the checks -------------------------------------------------------------


def check_dedup_keys(con: duckdb.DuckDBPyConnection, built: tuple[str, ...]) -> list[CheckResult]:
    """Every built table has exactly one row per its declared grain.

    One check per table rather than one for all of them, so a failure names the
    table whose grain is wrong instead of reporting that something, somewhere,
    is duplicated.
    """
    results = []
    for name in built:
        table = tables.OUTPUTS_BY_NAME[name]
        key = ", ".join(table.dedup_key)
        offenders = (
            f"SELECT {key}, COUNT(*) AS rows_per_key\n"
            f"FROM {name}\nGROUP BY {key}\nHAVING COUNT(*) > 1"
        )
        duplicated = _count(con, offenders)
        results.append(
            CheckResult(
                name=f"{CHECK_DEDUP_KEYS}[{name}]",
                passed=duplicated == 0,
                subject=f"{name} keyed on ({key}) -- {table.grain}",
                detail=(
                    ""
                    if duplicated == 0
                    else (
                        f"{duplicated} key value(s) appear on more than one row. Every count "
                        "built on this table is multiplied by the duplication, and no single "
                        "number looks wrong"
                    )
                ),
                sample=() if duplicated == 0 else _sample(con, offenders),
            )
        )
    return results


def check_sessions_are_maximised_not_summed(
    con: duckdb.DuckDBPyConnection,
) -> CheckResult:
    """The channel table's session count is the reader-day distinct count.

    The oracle is recomputed from the event layer, which sits *upstream* of the
    statement that produces the column -- so this check does not merely restate
    the aggregation it is checking. Summing the per-content session counts
    instead gives a larger number that no downstream feature rejects; the
    views-per-session rate simply comes out lower and entirely believable.
    """
    channel = tables.READER_CHANNEL_DAY.name
    offenders = f"""
SELECT
    d.reader_id,
    d.channel,
    d.local_date,
    d.sessions AS emitted_sessions,
    o.expected_sessions
FROM {channel} d
JOIN (
  SELECT reader_id, channel, local_date, COUNT(DISTINCT session_id) AS expected_sessions
  FROM {sql.EVENT_LAYER}
  WHERE is_view
  GROUP BY reader_id, channel, local_date
) o
  ON o.reader_id = d.reader_id
 AND o.channel = d.channel
 AND o.local_date = d.local_date
WHERE d.sessions <> o.expected_sessions
"""
    wrong = _count(con, offenders)
    return CheckResult(
        name=CHECK_SESSIONS_MAXIMISED,
        passed=wrong == 0,
        subject=(
            f"{channel}.sessions equals the distinct session count per reader, channel and "
            "local day, recomputed from the event layer"
        ),
        detail=(
            ""
            if wrong == 0
            else (
                f"{wrong} reader-channel-day row(s) carry a session count that is not the "
                "distinct session count. The usual cause is summing the per-content sessions "
                "column instead of maximising the carried per-day count"
            )
        ),
        sample=() if wrong == 0 else _sample(con, offenders),
    )


def check_section_attribution_reconciles(
    con: duckdb.DuckDBPyConnection,
) -> CheckResult:
    """A day's section views sum to that day's views, exactly.

    This is what fractional attribution *means*: a view of content filed under n
    sections is one view, divided, not n views. The oracle is the event layer
    again, so the reconciliation is against the underlying views rather than
    against another aggregate that could be wrong the same way.
    """
    section = tables.READER_SECTION_DAY.name
    offenders = f"""
SELECT
    s.reader_id,
    s.local_date,
    s.attributed_views,
    o.actual_views,
    s.attributed_views - o.actual_views AS difference
FROM (
  SELECT reader_id, local_date, SUM(section_views) AS attributed_views
  FROM {section}
  GROUP BY reader_id, local_date
) s
FULL OUTER JOIN (
  SELECT reader_id, local_date, COUNT(*) AS actual_views
  FROM {sql.EVENT_LAYER}
  WHERE is_view
  GROUP BY reader_id, local_date
) o
  ON o.reader_id = s.reader_id
 AND o.local_date = s.local_date
WHERE s.reader_id IS NULL
   OR o.reader_id IS NULL
   OR abs(s.attributed_views - o.actual_views) > {RECONCILIATION_TOLERANCE}
"""
    wrong = _count(con, offenders)
    return CheckResult(
        name=CHECK_SECTION_ATTRIBUTION,
        passed=wrong == 0,
        subject=(
            f"{section} attributed views reconcile to the day's actual views for every reader, "
            f"within {RECONCILIATION_TOLERANCE}"
        ),
        detail=(
            ""
            if wrong == 0
            else (
                f"{wrong} reader-day(s) do not reconcile. A full-weight attribution multiplies "
                "a reader's day by the number of sections their content happened to be filed "
                "under; a dropped row loses the reading entirely. The FULL OUTER JOIN is what "
                "catches the second case: a reader-day missing from one side is a mismatch, "
                "not an absence"
            )
        ),
        sample=() if wrong == 0 else _sample(con, offenders),
    )


def check_unresolved_metadata_is_its_own_outcome(
    con: duckdb.DuckDBPyConnection, config: BuildConfig
) -> CheckResult:
    """Views of unresolved content land on the sentinel, all of them, only there.

    Two failures are in scope and they are opposites. Dropping unresolved
    reading reports a reader who read things nobody could categorise as a reader
    who read nothing. Folding it into a real section reports them as interested
    in whatever bucket was chosen. Both keep the day's total views intact, which
    is why the reconciliation check above cannot see the second one at all.
    """
    section = tables.READER_SECTION_DAY.name
    sentinel = config.unresolved_section.replace("'", "''")
    unresolved_predicate = (
        "is_view AND (\n"
        "        NOT content_metadata_present\n"
        "     OR section_resolution <> 'resolved'\n"
        "     OR sections IS NULL\n"
        "     OR len(sections) = 0\n"
        "   )"
    )
    offenders = f"""
SELECT
    COALESCE(a.reader_id, e.reader_id) AS reader_id,
    COALESCE(a.local_date, e.local_date) AS local_date,
    a.sentinel_views,
    e.unresolved_views
FROM (
  SELECT reader_id, local_date, SUM(section_views) AS sentinel_views
  FROM {section}
  WHERE section = '{sentinel}'
  GROUP BY reader_id, local_date
) a
FULL OUTER JOIN (
  SELECT reader_id, local_date, COUNT(*) AS unresolved_views
  FROM {sql.EVENT_LAYER}
  WHERE {unresolved_predicate}
  GROUP BY reader_id, local_date
) e
  ON e.reader_id = a.reader_id
 AND e.local_date = a.local_date
WHERE a.reader_id IS NULL
   OR e.reader_id IS NULL
   OR abs(a.sentinel_views - e.unresolved_views) > {RECONCILIATION_TOLERANCE}
"""
    wrong = _count(con, offenders)
    return CheckResult(
        name=CHECK_UNRESOLVED_SENTINEL,
        passed=wrong == 0,
        subject=(
            f"every view of content whose section metadata did not resolve lands on "
            f"'{config.unresolved_section}', and nothing else does"
        ),
        detail=(
            ""
            if wrong == 0
            else (
                f"{wrong} reader-day(s) disagree. A row present on the left only means real "
                "reading was attributed to the sentinel; a row present on the right only means "
                "unresolved reading was dropped or filed under a real section. The second is "
                "invisible to every total in this build"
            )
        ),
        sample=() if wrong == 0 else _sample(con, offenders),
    )


def check_section_is_never_null(con: duckdb.DuckDBPyConnection) -> CheckResult:
    """No null section survives into the section table.

    Cheap, and it guards the same distinction from the other side: a null
    section is dropped by every ``GROUP BY`` downstream, so a null here becomes
    unresolved reading vanishing without anything raising.
    """
    section = tables.READER_SECTION_DAY.name
    offenders = f"SELECT reader_id, local_date FROM {section} WHERE section IS NULL"
    wrong = _count(con, offenders)
    return CheckResult(
        name=CHECK_SECTION_NEVER_NULL,
        passed=wrong == 0,
        subject=f"{section}.section is never null",
        detail="" if wrong == 0 else f"{wrong} row(s) carry a null section",
        sample=() if wrong == 0 else _sample(con, offenders),
    )


def _day_boundary_offenders(
    source_sql: str, emitted: str, config: BuildConfig, keys: tuple[str, ...]
) -> str:
    """Compare an emitted local day against the configured zone, per key.

    ``source_sql`` is a SELECT producing the key columns and an ``event_ts``,
    supplied by the caller rather than named as a view, for two reasons. The
    channel table only holds days that contained a qualifying view, so the
    comparison has to be against the viewing events and not against every event.
    And the email table is fed by two independent optional inputs, so its
    expected days are the union of whichever ones were delivered -- comparing
    against clicks alone would report every open-only day as a mismatch.

    The comparison recomputes the day with this module's own ``AT TIME ZONE``
    expression built from the config, so it is independent of the statement that
    produced the column: a statement edited to cast the instant directly, or to
    name a different zone, disagrees with it. What it does not prove is that the
    zone in the manifest is the zone the publisher meant -- nothing in the data
    can prove that, which is why the manifest has to declare it.
    """
    key_list = ", ".join(keys)
    expected_day = sql.local_date_expr("event_ts", config.day_boundary_timezone)
    join_on = " AND ".join(f"x.{key} = o.{key}" for key in keys)
    # Every key is coalesced, not only the first: a row present on one side only
    # has nulls on the other, and a sample that showed only the reader would not
    # say which channel or which list the shifted day belonged to.
    key_select = ",\n    ".join(f"COALESCE(o.{key}, x.{key}) AS {key}" for key in keys)
    return f"""
SELECT
    {key_select},
    o.local_date AS emitted_local_date,
    x.local_date AS expected_local_date
FROM (SELECT DISTINCT {key_list}, local_date FROM {emitted}) o
FULL OUTER JOIN (
  SELECT DISTINCT {key_list}, {expected_day} AS local_date
  FROM (
{source_sql}
  ) src
) x
  ON {join_on}
 AND x.local_date = o.local_date
WHERE o.{keys[0]} IS NULL OR x.{keys[0]} IS NULL
"""


def check_day_boundary(
    con: duckdb.DuckDBPyConnection,
    config: BuildConfig,
    *,
    name: str,
    source_sql: str,
    emitted: str,
    keys: tuple[str, ...],
    subject: str,
) -> CheckResult:
    """One channel's local days match the configured zone, key by key."""
    offenders = _day_boundary_offenders(source_sql, emitted, config, keys)
    wrong = _count(con, offenders)
    return CheckResult(
        name=name,
        passed=wrong == 0,
        subject=subject,
        detail=(
            ""
            if wrong == 0
            else (
                f"{wrong} key-and-day combination(s) appear on one side only, so this channel's "
                f"days were not bucketed in {config.day_boundary_timezone}. This is the defect "
                "that shifts one channel's whole history by hours while every number stays "
                "plausible"
            )
        ),
        sample=() if wrong == 0 else _sample(con, offenders),
    )


def run_checks(
    con: duckdb.DuckDBPyConnection,
    config: BuildConfig,
    *,
    built: tuple[str, ...],
    available_inputs: frozenset[str],
) -> tuple[CheckResult, ...]:
    """Run every check that applies to what was actually built.

    A check whose table was not built is skipped rather than passed. That is the
    difference between "we looked and it was fine" and "there was nothing to
    look at", and reporting the second as the first is how an empty audit
    becomes a clean bill of health.
    """
    results: list[CheckResult] = list(check_dedup_keys(con, built))

    if tables.READER_CHANNEL_DAY.name in built:
        results.append(check_sessions_are_maximised_not_summed(con))
        results.append(
            check_day_boundary(
                con,
                config,
                name=CHECK_DAY_BOUNDARY_EVENTS,
                source_sql=(
                    f"    SELECT reader_id, channel, event_ts FROM {sql.EVENT_LAYER}\n"
                    "    WHERE is_view"
                ),
                emitted=tables.READER_CHANNEL_DAY.name,
                keys=("reader_id", "channel"),
                subject=(
                    "every reader-channel day in the channel table is a day the configured "
                    f"zone ({config.day_boundary_timezone}) produces from the event instants"
                ),
            )
        )
    if tables.READER_SECTION_DAY.name in built:
        results.append(check_section_attribution_reconciles(con))
        results.append(check_unresolved_metadata_is_its_own_outcome(con, config))
        results.append(check_section_is_never_null(con))
    if tables.READER_EMAIL_DAY.name in built:
        # The expected days are the union of whichever email inputs were
        # delivered. Reading only clicks would flag every open-only day.
        email_sources = [
            f"    SELECT reader_id, list_id, event_ts FROM {sql.input_view(name)}"
            for name in ("email_click", "email_open")
            if name in available_inputs
        ]
        email_source_sql = "\n    UNION ALL\n".join(email_sources)
        results.append(
            check_day_boundary(
                con,
                config,
                name=CHECK_DAY_BOUNDARY_EMAIL,
                source_sql=email_source_sql,
                emitted=tables.READER_EMAIL_DAY.name,
                keys=("reader_id", "list_id"),
                subject=(
                    "every reader-and-list day in the email table is a day the configured zone "
                    "produces from the click instants -- the channel the upstream system left "
                    "unconverted"
                ),
            )
        )
    if tables.READER_COMMUNITY_DAY.name in built:
        results.append(
            check_day_boundary(
                con,
                config,
                name=CHECK_DAY_BOUNDARY_COMMUNITY,
                source_sql=(
                    f"    SELECT reader_id, event_ts FROM {sql.input_view('community_action')}"
                ),
                emitted=tables.READER_COMMUNITY_DAY.name,
                keys=("reader_id",),
                subject=(
                    "every reader day in the community table is a day the configured zone "
                    "produces from the action instants -- the other unconverted channel"
                ),
            )
        )
    return tuple(results)


def raise_for_failures(results: tuple[CheckResult, ...]) -> None:
    """Raise once, naming every failure, or return quietly."""
    failures = tuple(item for item in results if not item.passed)
    if failures:
        raise IntermediateCheckError(failures)
