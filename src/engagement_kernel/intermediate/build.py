"""Run the build: one DuckDB session, one pass, one report.

The whole thing happens in a single in-process session with no network and no
credentials. That is not an incidental property -- it is the portability claim,
and ``tools/import_closure_check.py`` asserts it in CI by running this build with
every cloud SDK's import blocked.

What comes back is a :class:`BuildResult`: the tables, the configuration they
were built under, which optional inputs were absent and what that cost, and
every check with its verdict. The report is part of the output rather than
something printed and discarded, because "which feature set did this run
actually have" is a question about a number that gets asked months later.

One field deserves its own warning. ``statement_overrides`` replaces a named
statement with different SQL, and it exists so the negative controls can run the
*real* build with exactly one derivation wrong -- a control that stubs out the
code proves nothing about the code. Any override is recorded in
:attr:`BuildResult.mutated_statements`, so a result produced under one can never
be mistaken for a clean build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq

from engagement_kernel.contract import spec
from engagement_kernel.contract.degradation import FeatureSetPlan, resolve_feature_set
from engagement_kernel.contract.manifest import Manifest, load_manifest
from engagement_kernel.intermediate import checks, session, sql, tables
from engagement_kernel.intermediate.config import BuildConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb


class MissingRequiredInput(RuntimeError):
    """A required contract table is not in the delivery.

    Distinct from a check failure: nothing was built, so there is no output to
    have an opinion about. Raised with the table named, because the alternative
    -- an empty view and a build that completes -- reports a publisher with no
    readers.
    """


@dataclass(frozen=True)
class OmittedOutput:
    """An output that was not built, and what its absence costs."""

    table: str
    missing_inputs: tuple[str, ...]
    consequence: str


@dataclass(frozen=True)
class BuildResult:
    """Everything one build produced, plus everything it declined to produce."""

    config: BuildConfig
    feature_set: FeatureSetPlan
    tables: dict[str, pa.Table]
    omitted: tuple[OmittedOutput, ...]
    check_results: tuple[checks.CheckResult, ...]
    #: Statement names replaced before execution. Empty on any real build.
    mutated_statements: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        return not self.mutated_statements

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.check_results if not item.passed)

    def table(self, name: str) -> pa.Table:
        return self.tables[name]

    def render(self) -> str:
        lines = [
            "engagement-kernel intermediate build",
            "",
            self.config.describe(),
            "",
            self.feature_set.describe(),
            "",
            "tables built:",
        ]
        for name, table in self.tables.items():
            spec_entry = tables.OUTPUTS_BY_NAME[name]
            kind = "published" if spec_entry.published else "internal"
            lines.append(f"  {name} ({kind}): {table.num_rows} rows")
        if self.omitted:
            lines.append("")
            lines.append("tables not built:")
            for item in self.omitted:
                lines.append(
                    f"  {item.table}: missing {', '.join(item.missing_inputs)} -- "
                    f"{item.consequence}"
                )
        lines.append("")
        lines.append("checks:")
        lines.extend(f"  {item.render()}" for item in self.check_results)
        if self.mutated_statements:
            lines.append("")
            lines.append(
                "MUTATED -- this build ran with replaced SQL and is not a valid result: "
                + ", ".join(self.mutated_statements)
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "day_boundary_timezone": self.config.day_boundary_timezone,
            "article_view_definition_id": self.config.article_view.definition_id,
            "unresolved_section": self.config.unresolved_section,
            "feature_set_id": self.feature_set.feature_set_id,
            "tables": {
                name: {
                    "rows": table.num_rows,
                    "published": tables.OUTPUTS_BY_NAME[name].published,
                    "grain": tables.OUTPUTS_BY_NAME[name].grain,
                    "dedup_key": list(tables.OUTPUTS_BY_NAME[name].dedup_key),
                }
                for name, table in self.tables.items()
            },
            "omitted": [
                {
                    "table": item.table,
                    "missing_inputs": list(item.missing_inputs),
                    "consequence": item.consequence,
                }
                for item in self.omitted
            ],
            "checks": [
                {"name": item.name, "passed": item.passed, "subject": item.subject}
                for item in self.check_results
            ],
            "mutated_statements": list(self.mutated_statements),
        }


#: What is lost when an optional input is absent. Stated per table, because
#: "degraded" on its own invites somebody to fill the gap with zeros.
_OMISSION_CONSEQUENCE: dict[str, str] = {
    tables.READER_EMAIL_DAY.name: (
        "no email signal is available to any window. A reader with no email feed is not a "
        "reader who never clicked, so the table is absent rather than zero"
    ),
    tables.READER_COMMUNITY_DAY.name: (
        "no community signal is available. Community is the smallest-variance feature block, "
        "so zeros here would not weaken a fit so much as destabilise it"
    ),
}


def _missing_required(available: frozenset[str]) -> tuple[str, ...]:
    return tuple(name for name in sql.REQUIRED_INPUTS if name not in available)


def _omissions(available: frozenset[str]) -> tuple[OmittedOutput, ...]:
    omitted = []
    for table in tables.OUTPUTS:
        if not table.optional_inputs:
            continue
        missing = tuple(name for name in table.optional_inputs if name not in available)
        # The email table needs only one of its two inputs to be worth building.
        if not missing or len(missing) < len(table.optional_inputs):
            continue
        omitted.append(
            OmittedOutput(
                table=table.name,
                missing_inputs=missing,
                consequence=_OMISSION_CONSEQUENCE.get(table.name, "the table is not built"),
            )
        )
    return tuple(omitted)


def build_from_arrow(
    arrow_tables: dict[str, pa.Table],
    config: BuildConfig,
    *,
    window_start: date | None = None,
    manifest: Manifest | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
    statement_overrides: dict[str, str] | None = None,
) -> BuildResult:
    """Build from contract tables already in memory.

    ``manifest`` is optional here only because a test may build from tables
    without one; when it is supplied the feature-set plan reflects the declared
    availability floors, which is what a real run needs.
    """
    con = session.connect(connection=connection)
    available = session.register_arrow_inputs(con, arrow_tables)
    missing = _missing_required(available)
    if missing:
        raise MissingRequiredInput(
            f"the delivery is missing required contract table(s): {', '.join(missing)}. "
            "Nothing was built: an empty stand-in would report a publisher with no readers"
        )

    statements = sql.build_statements(config, available_inputs=available)
    overrides = statement_overrides or {}
    unknown = sorted(set(overrides) - set(statements))
    if unknown:
        raise KeyError(
            f"statement_overrides names statements this build does not run: {unknown}. "
            "A control aimed at a statement that never executes passes for free"
        )
    statements.update(overrides)

    for statement in statements.values():
        con.execute(statement)

    built = tuple(name for name in statements if name in tables.OUTPUTS_BY_NAME)
    results = checks.run_checks(con, config, built=built, available_inputs=available)
    checks.raise_for_failures(results)

    materialised = {
        name: session.fetch(con, f"SELECT * FROM {name}")  # noqa: S608 - name from OUTPUTS
        for name in built
    }
    if manifest is not None:
        plan = resolve_feature_set(
            manifest, present_tables=set(available), window_start=window_start
        )
    else:
        plan = resolve_feature_set(
            _implied_manifest(config, available), present_tables=set(available)
        )
    return BuildResult(
        config=config,
        feature_set=plan,
        tables=materialised,
        omitted=_omissions(available),
        check_results=results,
        mutated_statements=tuple(sorted(overrides)),
    )


def _implied_manifest(config: BuildConfig, available: frozenset[str]) -> Manifest:
    """A manifest standing in for one that was never supplied, for tests only.

    Every optional input present is declared available from the earliest date
    the calendar has, which is exactly the assumption a caller who supplied no
    manifest has already made. Spelling it out here keeps the feature-set plan
    on one code path instead of two.
    """
    from engagement_kernel.contract.manifest import InputAvailability, WeekAnchor

    return Manifest(
        contract_version=spec.CONTRACT_VERSION,
        day_boundary_timezone=config.day_boundary_timezone,
        week_anchor=WeekAnchor(weekday="Sunday", position="week_ends_on"),
        article_view=config.article_view,
        scored_population=_placeholder_population(),
        optional_inputs={
            table.name: (
                InputAvailability(status="available", available_from=date.min)
                if table.name in available
                else InputAvailability(status="not_deployed", available_from=None)
            )
            for table in spec.OPTIONAL_TABLES
        },
    )


def _placeholder_population():
    from engagement_kernel.contract.manifest import ScoredPopulation

    return ScoredPopulation(
        definition_id="unspecified-no-manifest-supplied",
        entitled_states=("active",),
    )


def build_delivery(
    directory: str | Path,
    *,
    window_start: date | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
    statement_overrides: dict[str, str] | None = None,
    config_overrides: dict[str, object] | None = None,
) -> BuildResult:
    """Build the intermediate tables from a delivery directory.

    The manifest is read first and the configuration comes from it, so there is
    no path where the build runs with a timezone or an article-view definition
    that did not travel with the data.
    """
    manifest = load_manifest(directory)
    config = BuildConfig.from_manifest(manifest, **(config_overrides or {}))
    arrow_tables = session.read_delivery(directory)
    return build_from_arrow(
        arrow_tables,
        config,
        window_start=window_start,
        manifest=manifest,
        connection=connection,
        statement_overrides=statement_overrides,
    )


def write_result(result: BuildResult, directory: str | Path) -> list[Path]:
    """Write every built table as Parquet, plus the build report as JSON."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, table in result.tables.items():
        path = out / f"{name}.parquet"
        pq.write_table(table, path, compression="none")
        written.append(path)
    report = out / "build-report.json"
    report.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", "utf-8")
    written.append(report)
    return written
