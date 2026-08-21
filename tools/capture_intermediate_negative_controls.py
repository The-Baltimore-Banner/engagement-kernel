#!/usr/bin/env python3
"""Break each load-bearing derivation on purpose, and capture what the build says.

A build whose checks have only ever seen correct SQL has proven nothing. This
script is the evidence that each check fails, that it fails **for its own
reason**, and that the reason is legible in the message rather than inferable
from a stack trace.

How a case works. The real build plan is generated from the real demo delivery,
one named statement has one substring replaced, and the real build runs. Nothing
is stubbed and no check is bypassed, because a control that replaces the code
under test measures the replacement.

Three ways a case can be invalid, and each is reported as an invalid case rather
than a pass:

*The mutation did not apply.* If the substring is not in the generated SQL the
substitution is a no-op, the build is simply correct, and a naive harness records
"no failure" as if the check had been exonerated. So the substring is asserted
before the build runs.

*The mutation did not compile.* A statement DuckDB rejects raises a database
error, not a check failure. That proves the SQL was malformed, not that the
check works -- the check never ran.

*The mutation tripped a different set of checks than expected.* The expected set
is declared per case and compared exactly. A case that starts failing for an
extra reason, or stops tripping its own check, breaks rather than quietly
becoming decoration.

The second section captures refusals rather than mutations: the build asked to
run without a value it will not guess. Those are not SQL at all, and they are
here because "fails loudly when unset" is a claim that needs the same evidence.

Usage::

    python3 tools/capture_intermediate_negative_controls.py           # print
    python3 tools/capture_intermediate_negative_controls.py --write   # update the doc

The generated document is committed and a test compares it against a fresh
render, so it cannot go stale.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:  # running as a script, not an installed package
    sys.path.insert(0, str(REPO_ROOT / "src"))

from engagement_kernel.contract import demo  # noqa: E402
from engagement_kernel.contract.manifest import (  # noqa: E402
    ManifestError,
    parse_manifest,
)
from engagement_kernel.intermediate import build, checks, sql, tables  # noqa: E402
from engagement_kernel.intermediate.config import BuildConfig, BuildConfigError  # noqa: E402

DOC_RELPATH = "docs/intermediate-negative-controls.md"

#: The delivery the controls run against: the committed synthetic demo.
DELIVERY = REPO_ROOT / "examples" / "demo-delivery"


# --- case declarations ------------------------------------------------------


@dataclass(frozen=True)
class Mutation:
    """One deliberately wrong derivation."""

    case_id: str
    title: str
    #: Which statement to edit. A name the build does not run is an error, not a
    #: silent skip.
    statement: str
    find: str
    replace: str
    #: What the wrong version would look like in production if nothing caught it.
    consequence: str
    #: Exactly which checks must fail. Compared as a set, both directions.
    expected_failures: tuple[str, ...]
    #: Why the expected set is what it is, when that needs saying.
    note: str = ""


@dataclass(frozen=True)
class Refusal:
    """One build the code declines to run, and the message it declines with."""

    case_id: str
    title: str
    why: str
    run: Callable[[], object]
    expected_exception: type[Exception]


def _dedup_check(table_name: str) -> str:
    return f"{checks.CHECK_DEDUP_KEYS}[{table_name}]"


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        case_id="sessions-summed-not-maximised",
        title="Channel-day sessions summed instead of maximised",
        statement=tables.READER_CHANNEL_DAY.name,
        find="MAX(distinct_sessions_day) AS sessions",
        replace="CAST(SUM(sessions) AS BIGINT) AS sessions",
        consequence=(
            "A reader who read three articles in one visit is recorded as having had three "
            "sessions. The number is larger, entirely plausible, and every views-per-session "
            "rate divides by it -- so the reader looks like a habitual short visitor instead of "
            "one deep reader, and no total anywhere is wrong."
        ),
        expected_failures=(checks.CHECK_SESSIONS_MAXIMISED,),
    ),
    Mutation(
        case_id="section-attribution-full-weight",
        title="Full weight to every section instead of 1/n",
        statement=tables.READER_SECTION_DAY.name,
        find="1.0 / len(w.effective_sections) AS section_weight",
        replace="1.0 AS section_weight",
        consequence=(
            "A view of an article filed under two sections becomes two views. Readers of "
            "heavily cross-filed content get inflated topic profiles, and the inflation "
            "correlates with the desk that files under most sections rather than with anything "
            "the reader did."
        ),
        expected_failures=(checks.CHECK_SECTION_ATTRIBUTION,),
    ),
    Mutation(
        case_id="unresolved-collapsed-to-zero",
        title="Unresolved metadata dropped instead of routed to the sentinel",
        statement=tables.READER_SECTION_DAY.name,
        find="ELSE ['__unresolved__']",
        replace="ELSE []",
        consequence=(
            "A reader whose reading was all on content nobody could categorise is reported as "
            "having read nothing. The downstream reason codes cannot then tell a metadata gap "
            "from a quiet reader, and the two get the same treatment."
        ),
        expected_failures=(
            checks.CHECK_SECTION_ATTRIBUTION,
            checks.CHECK_UNRESOLVED_SENTINEL,
        ),
        note=(
            "Two checks, and both are correct. Dropping the rows loses the views, so the day "
            "no longer reconciles; and it loses the sentinel, so the unresolved volume has "
            "nowhere to be. The next case is the version that reconciles perfectly."
        ),
    ),
    Mutation(
        case_id="unresolved-folded-into-a-real-section",
        title="Unresolved metadata filed under a real section",
        statement=tables.READER_SECTION_DAY.name,
        find="ELSE ['__unresolved__']",
        replace="ELSE ['news']",
        consequence=(
            "Reading nobody could categorise is reported as interest in whichever section was "
            "chosen. Every total in the build still reconciles exactly, the section is a real "
            "one, and the topic profile is wrong in a direction somebody picked."
        ),
        expected_failures=(checks.CHECK_UNRESOLVED_SENTINEL,),
        note=(
            "This is the case the reconciliation check cannot see, which is why the sentinel "
            "has a check of its own. Views are conserved; only their destination is wrong."
        ),
    ),
    Mutation(
        case_id="day-boundary-from-the-session-zone",
        title="Reader events bucketed by a bare cast instead of the configured zone",
        statement=sql.EVENT_LAYER,
        find="CAST(e.event_ts AT TIME ZONE 'America/New_York' AS DATE)",
        replace="CAST(e.event_ts AS DATE)",
        consequence=(
            "Reading days come from whatever zone the session happens to be set to. On a "
            "developer's machine set to the publisher's own zone the answer is right, so the "
            "defect ships; in CI, or in another office, evening reads move to the next day and "
            "Sunday-evening reads move to the next week's bin."
        ),
        expected_failures=(checks.CHECK_DAY_BOUNDARY_EVENTS,),
        note=(
            "Only one check fails, and that is the point: every measure downstream is bucketed "
            "consistently by the wrong day, so the sessions, attribution and sentinel checks "
            "all agree with each other. Internal consistency is not correctness here."
        ),
    ),
    Mutation(
        case_id="email-day-left-unconverted",
        title="Email days left in the vendor's own zone",
        statement=tables.READER_EMAIL_DAY.name,
        find=(
            "CAST(event_ts AT TIME ZONE 'America/New_York' AS DATE) AS local_date,\n"
            "      1 AS click"
        ),
        replace="CAST(event_ts AS DATE) AS local_date,\n      1 AS click",
        consequence=(
            "This is the defect the upstream system actually has: web and app converted, email "
            "not converted at all. One channel's whole history sits hours away from the "
            "others, an evening click lands on the next day, and a Saturday-evening click "
            "lands in the following week's bin."
        ),
        expected_failures=(checks.CHECK_DAY_BOUNDARY_EMAIL,),
    ),
    Mutation(
        case_id="email-open-day-left-unconverted",
        title="Email clicks converted but opens left in the vendor's own zone",
        statement=tables.READER_EMAIL_DAY.name,
        find=(
            "CAST(event_ts AT TIME ZONE 'America/New_York' AS DATE) AS local_date,\n"
            "      0 AS click"
        ),
        replace="CAST(event_ts AS DATE) AS local_date,\n      0 AS click",
        consequence=(
            "The half-converted version, and the more likely one: somebody fixes the channel "
            "they were looking at and leaves its neighbour. Reachability then reports a "
            "different set of days from engagement on the same feed."
        ),
        expected_failures=(checks.CHECK_DAY_BOUNDARY_EMAIL,),
        note=(
            "This case only discriminates because the demo delivery carries an open near "
            "local midnight on a day the same reader did not click. Without such a row the "
            "check would pass whether or not the conversion was applied, and the control "
            "would be reported as invalid rather than as evidence."
        ),
    ),
    Mutation(
        case_id="community-day-left-unconverted",
        title="Community days left in the vendor's own zone",
        statement=tables.READER_COMMUNITY_DAY.name,
        find="CAST(event_ts AT TIME ZONE 'America/New_York' AS DATE) AS local_date",
        replace="CAST(event_ts AS DATE) AS local_date",
        consequence=(
            "The other half of the same upstream defect. Community is the smallest-variance "
            "feature block, so a shifted day does not weaken the signal so much as move it "
            "onto the wrong day of the week."
        ),
        expected_failures=(checks.CHECK_DAY_BOUNDARY_COMMUNITY,),
    ),
    Mutation(
        case_id="section-grain-split-by-content",
        title="Section table grouped by content as well, breaking its grain",
        statement=tables.READER_SECTION_DAY.name,
        find="GROUP BY reader_id, section, local_date",
        replace="GROUP BY reader_id, section, local_date, content_id",
        consequence=(
            "Several rows per reader-section-day. Every window that sums this table is still "
            "correct, because the sum is unchanged -- but every one that counts rows, or joins "
            "to it, multiplies. A documented grain that nothing enforces is how this arrives."
        ),
        expected_failures=(_dedup_check(tables.READER_SECTION_DAY.name),),
        note=(
            "The reconciliation and sentinel checks both pass, because the views are conserved "
            "and merely spread over more rows. Only the grain is wrong, and only the grain "
            "check sees it."
        ),
    ),
)


def _no_article_view() -> object:
    return BuildConfig(day_boundary_timezone="America/New_York", article_view=None)  # type: ignore[arg-type]


@dataclass(frozen=True)
class _SelectsNothing:
    """An article view that counts nothing, which the contract cannot express.

    ``ArticleViewDefinition`` refuses an empty selection in its own
    ``__post_init__``, so the contract type genuinely cannot be built this way --
    which is the right place for the rule and is captured as its own case below.
    This stand-in exists to reach the build's *second* guard, the one that would
    catch an article view arriving from somewhere other than a manifest. A guard
    with no way to be tested is a guard nobody can tell is still wired up.
    """

    definition_id: str = "counts-nothing"
    content_types: tuple[str, ...] = ()
    event_kinds: tuple[str, ...] = ()


def _empty_article_view() -> object:
    return BuildConfig(
        day_boundary_timezone="America/New_York",
        article_view=_SelectsNothing(),  # type: ignore[arg-type]
    )


def _manifest_with_an_empty_article_view() -> object:
    raw = demo.build_manifest()
    raw["article_view"]["content_types"] = []
    return parse_manifest(raw)


def _manifest_without_article_view() -> object:
    raw = demo.build_manifest()
    del raw["article_view"]
    return parse_manifest(raw)


def _manifest_without_timezone() -> object:
    raw = demo.build_manifest()
    raw["day_boundary_timezone"] = ""
    return parse_manifest(raw)


def _delivery_missing_a_required_table() -> object:
    arrow_tables = demo.build_tables()
    del arrow_tables["content"]
    manifest = parse_manifest(demo.build_manifest())
    return build.build_from_arrow(
        arrow_tables, BuildConfig.from_manifest(manifest), manifest=manifest
    )


def _override_naming_a_statement_that_never_runs() -> object:
    arrow_tables = demo.build_tables()
    manifest = parse_manifest(demo.build_manifest())
    return build.build_from_arrow(
        arrow_tables,
        BuildConfig.from_manifest(manifest),
        manifest=manifest,
        statement_overrides={"user_device_day": "SELECT 1"},
    )


REFUSALS: tuple[Refusal, ...] = (
    Refusal(
        case_id="article-view-unset",
        title="No article-view definition at all",
        why=(
            "A placeholder here produces a build that runs and is wrong, which is the failure "
            "this repository exists to prevent. There is no default and no fallback."
        ),
        run=_no_article_view,
        expected_exception=BuildConfigError,
    ),
    Refusal(
        case_id="article-view-selects-nothing",
        title="An article-view definition that counts no content type",
        why=(
            "An empty selection is worse than an absent one: the build succeeds and reports "
            "every reader as inactive, which is indistinguishable from a quiet publisher."
        ),
        run=_empty_article_view,
        expected_exception=BuildConfigError,
    ),
    Refusal(
        case_id="manifest-article-view-selects-nothing",
        title="A manifest whose article-view block counts no content type",
        why=(
            "The contract refuses it at the point the definition is parsed, so an empty "
            "selection cannot reach the build at all. This is where the rule belongs: the "
            "build's own guard below is the backstop for a config built by hand."
        ),
        run=_manifest_with_an_empty_article_view,
        expected_exception=ManifestError,
    ),
    Refusal(
        case_id="manifest-without-article-view",
        title="A delivery whose manifest omits the article-view block",
        why=(
            "The definition has to travel with the data, so a published number can be traced "
            "to the definition it was produced under."
        ),
        run=_manifest_without_article_view,
        expected_exception=ManifestError,
    ),
    Refusal(
        case_id="manifest-without-timezone",
        title="A delivery whose manifest declares no day-boundary timezone",
        why=(
            "The two plausible guesses differ by hours. Guessing mis-buckets every window "
            "without anything visibly breaking."
        ),
        run=_manifest_without_timezone,
        expected_exception=ManifestError,
    ),
    Refusal(
        case_id="missing-required-input",
        title="A delivery missing a required contract table",
        why=(
            "Nothing is built. An empty stand-in would let the build finish and report a "
            "publisher with no content."
        ),
        run=_delivery_missing_a_required_table,
        expected_exception=build.MissingRequiredInput,
    ),
    Refusal(
        case_id="override-of-a-statement-that-never-runs",
        title="A control aimed at a statement this build does not run",
        why=(
            "This one guards the harness rather than the build. A mutation aimed at a "
            "statement that never executes would pass for free and read as evidence."
        ),
        run=_override_naming_a_statement_that_never_runs,
        expected_exception=KeyError,
    ),
)


# --- running ----------------------------------------------------------------


@dataclass
class MutationOutcome:
    """What one mutation actually did."""

    mutation: Mutation
    #: Set when the case is not usable as evidence at all.
    invalid_reason: str | None = None
    failed_checks: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""

    @property
    def valid(self) -> bool:
        return self.invalid_reason is None

    @property
    def as_expected(self) -> bool:
        return self.valid and set(self.failed_checks) == set(self.mutation.expected_failures)


def _statements_for_delivery() -> tuple[dict[str, str], BuildConfig, dict]:
    from engagement_kernel.contract.manifest import load_manifest
    from engagement_kernel.intermediate import session

    manifest = load_manifest(DELIVERY)
    config = BuildConfig.from_manifest(manifest)
    arrow_tables = session.read_delivery(DELIVERY)
    statements = sql.build_statements(config, available_inputs=frozenset(arrow_tables))
    return statements, config, {"manifest": manifest, "arrow_tables": arrow_tables}


def run_mutation(mutation: Mutation) -> MutationOutcome:
    """Apply one mutation to the real plan and run the real build."""
    import duckdb

    statements, config, context = _statements_for_delivery()
    if mutation.statement not in statements:
        return MutationOutcome(
            mutation,
            invalid_reason=(
                f"the build does not run a statement named {mutation.statement!r}, so this "
                "case would pass for free"
            ),
        )
    original = statements[mutation.statement]
    if mutation.find not in original:
        return MutationOutcome(
            mutation,
            invalid_reason=(
                f"the text to replace is not in the generated {mutation.statement!r} "
                "statement, so the substitution is a no-op and the build under test is the "
                "correct one"
            ),
        )
    mutated = original.replace(mutation.find, mutation.replace, 1)
    if mutated == original:  # pragma: no cover - unreachable given the check above
        return MutationOutcome(mutation, invalid_reason="the substitution changed nothing")

    try:
        build.build_from_arrow(
            context["arrow_tables"],
            config,
            manifest=context["manifest"],
            statement_overrides={mutation.statement: mutated},
        )
    except checks.IntermediateCheckError as exc:
        return MutationOutcome(
            mutation,
            failed_checks=tuple(item.name for item in exc.failures),
            message=str(exc),
        )
    except duckdb.Error as exc:
        return MutationOutcome(
            mutation,
            invalid_reason=(
                "the mutated statement did not compile, so no check ran and nothing about the "
                f"checks was proven: {exc}"
            ),
        )
    return MutationOutcome(
        mutation,
        invalid_reason=(
            "the build completed and every check passed. The derivation was broken and nothing "
            "noticed, which is the finding"
        ),
    )


@dataclass
class RefusalOutcome:
    refusal: Refusal
    raised: str | None
    message: str

    @property
    def as_expected(self) -> bool:
        return self.raised == self.refusal.expected_exception.__name__


def run_refusal(refusal: Refusal) -> RefusalOutcome:
    try:
        refusal.run()
    except Exception as exc:  # noqa: BLE001 - the type is the evidence
        return RefusalOutcome(refusal, type(exc).__name__, str(exc))
    return RefusalOutcome(refusal, None, "no exception was raised")


def run_baseline() -> tuple[bool, str]:
    """The positive control: the unmutated build passes every check.

    Without this the whole document is unreadable. A harness in which every case
    fails might be catching real defects, or might be failing for a reason that
    has nothing to do with the mutations.
    """
    try:
        result = build.build_delivery(DELIVERY)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return False, f"{type(exc).__name__}: {exc}"
    return True, result.render()


# --- rendering --------------------------------------------------------------


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def render() -> str:
    lines: list[str] = [
        "# Intermediate build: negative controls",
        "",
        "Generated by `tools/capture_intermediate_negative_controls.py`. Do not edit by hand;",
        "a test compares this file against a fresh render.",
        "",
        "Each case below takes the real build plan for the committed synthetic demo delivery,",
        "replaces one substring in one named statement, and runs the real build. Nothing is",
        "stubbed and no check is bypassed. The expected set of failing checks is declared per",
        "case and compared exactly, so a case that starts failing for an extra reason -- or",
        "stops failing at all -- breaks rather than quietly becoming decoration.",
        "",
        "Three ways a case would be worthless, each reported rather than passed: the substring",
        "was not in the SQL, so nothing was mutated; the mutated SQL did not compile, so no",
        "check ran; or the build completed cleanly, which means the derivation was broken and",
        "nothing noticed.",
        "",
    ]

    ok, baseline = run_baseline()
    lines += [
        "## Baseline: the unmutated build",
        "",
        (
            "The positive control. Without it, a document in which everything fails proves "
            "nothing about the mutations."
        ),
        "",
        f"**Every check passes: {'yes' if ok else 'NO -- the rest of this document is void'}**",
        "",
        "```",
        baseline,
        "```",
        "",
    ]

    lines += ["## Mutations", ""]
    for mutation in MUTATIONS:
        outcome = run_mutation(mutation)
        lines += [
            f"### {mutation.title}",
            "",
            f"- **case** `{mutation.case_id}`",
            f"- **statement** `{mutation.statement}`",
            "- **mutation**",
            "",
            "```diff",
            f"- {mutation.find}",
            f"+ {mutation.replace}",
            "```",
            "",
            f"**What the wrong version does.** {mutation.consequence}",
            "",
        ]
        if mutation.note:
            lines += [f"**On the expected failures.** {mutation.note}", ""]
        expected = ", ".join(f"`{name}`" for name in mutation.expected_failures)
        lines += [f"- **expected failures** {expected}"]
        if not outcome.valid:
            lines += [
                "- **result** INVALID CONTROL",
                "",
                f"> {outcome.invalid_reason}",
                "",
            ]
            continue
        actual = ", ".join(f"`{name}`" for name in outcome.failed_checks)
        lines += [
            f"- **actual failures** {actual}",
            f"- **as expected** {'yes' if outcome.as_expected else 'NO'}",
            "",
            "```",
            outcome.message,
            "```",
            "",
        ]

    lines += [
        "## Refusals",
        "",
        (
            "Not mutations. These are builds the code declines to run because it was not told "
            "something it will not guess. They are captured here for the same reason the "
            'mutations are: "fails loudly when unset" is a claim, and a claim needs the '
            "message."
        ),
        "",
    ]
    for refusal in REFUSALS:
        outcome = run_refusal(refusal)
        lines += [
            f"### {refusal.title}",
            "",
            f"- **case** `{refusal.case_id}`",
            f"- **expected** `{refusal.expected_exception.__name__}`",
            f"- **raised** `{outcome.raised}`",
            f"- **as expected** {'yes' if outcome.as_expected else 'NO'}",
            "",
            f"**Why it refuses.** {refusal.why}",
            "",
            "```",
            outcome.message,
            "```",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="Update the committed document.")
    args = parser.parse_args(argv)
    text = render()
    if args.write:
        path = REPO_ROOT / DOC_RELPATH
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
