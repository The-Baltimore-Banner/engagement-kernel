"""Validate a directory of Parquet files against the canonical input contract.

The validator's job is not to say "pass" or "fail". It is to say *which table*,
*which column*, *how many rows*, and *what is wrong with them* -- because a
validator that reports "validation failed" has proven only that something
happened, and a producer cannot act on it.

Two design rules follow from that, and both are load-bearing:

**Every check has its own code and its own message.** A missing column, a wrong
type, a null in a non-nullable field, a duplicate on the deduplication key and
an out-of-range enum value are five different defects. They are reported as five
different codes, each naming the column and the row count, so a fixture built to
trigger one of them fails *for that reason* and not incidentally. This is what
makes the negative controls in ``tests/`` evidence rather than decoration.

**Checks are ordered so a failure cannot mask a different failure's message.**
A column whose type is wrong is not then checked for nulls, enum membership or
duplicates: those checks would fail too, for a reason that is not the reason,
and the report would name the wrong problem.

The validator fails closed. An unexpected column is an error, not a shrug: an
extra column is how a vendor-shaped table arrives one field at a time, and
several extras have specific, named reasons for being refused -- a pre-bucketed
calendar date, a scroll measure, anything that looks like personal data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from engagement_kernel.contract import spec as contract_spec
from engagement_kernel.contract.manifest import Manifest, ManifestError, load_manifest
from engagement_kernel.contract.reader import TableRead, TableReadError, TypedReader
from engagement_kernel.contract.spec import (
    FORBIDDEN_COLUMN_REASONS,
    REQUIRE_NON_EMPTY_LIST,
    REQUIRE_NON_NULL,
    REQUIRE_NULL_OR_EMPTY_LIST,
    TABLES,
    ConditionalRule,
    FieldSpec,
    TableSpec,
    types_compatible,
)

# --- finding codes ----------------------------------------------------------

MISSING_REQUIRED_TABLE = "MISSING_REQUIRED_TABLE"
UNREADABLE_TABLE = "UNREADABLE_TABLE"
FILE_ABSENT_BUT_DECLARED_AVAILABLE = "FILE_ABSENT_BUT_DECLARED_AVAILABLE"
FILE_PRESENT_BUT_DECLARED_ABSENT = "FILE_PRESENT_BUT_DECLARED_ABSENT"
MISSING_REQUIRED_COLUMN = "MISSING_REQUIRED_COLUMN"
UNEXPECTED_COLUMN = "UNEXPECTED_COLUMN"
FORBIDDEN_COLUMN = "FORBIDDEN_COLUMN"
COLUMN_TYPE_MISMATCH = "COLUMN_TYPE_MISMATCH"
TIMESTAMP_NOT_TIMEZONE_AWARE = "TIMESTAMP_NOT_TIMEZONE_AWARE"
NULL_IN_NON_NULLABLE = "NULL_IN_NON_NULLABLE"
ENUM_VALUE_OUT_OF_RANGE = "ENUM_VALUE_OUT_OF_RANGE"
DUPLICATE_DEDUP_KEY = "DUPLICATE_DEDUP_KEY"
CONDITIONAL_FIELD_REQUIRED = "CONDITIONAL_FIELD_REQUIRED"
CONDITIONAL_FIELD_FORBIDDEN = "CONDITIONAL_FIELD_FORBIDDEN"
NEGATIVE_MEASURE = "NEGATIVE_MEASURE"
MIXED_READER_ID_GRAIN = "MIXED_READER_ID_GRAIN"
NAMESPACED_READER_ID = "NAMESPACED_READER_ID"
UNKNOWN_READER_ID = "UNKNOWN_READER_ID"
DISJOINT_READER_ID_SPACE = "DISJOINT_READER_ID_SPACE"
DUPLICATE_SECTION = "DUPLICATE_SECTION"
SPAN_END_NOT_AFTER_START = "SPAN_END_NOT_AFTER_START"
OVERLAPPING_SPANS = "OVERLAPPING_SPANS"
MULTIPLE_OPEN_SPANS = "MULTIPLE_OPEN_SPANS"
EVENT_BEFORE_AVAILABILITY_FLOOR = "EVENT_BEFORE_AVAILABILITY_FLOOR"

#: A namespace separator in a reader id. ``login:123`` and ``anon:abc`` in one
#: column is the canonical shape of two grains sharing a key, and the prefix is
#: the only part of it that is mechanically visible.
READER_ID_NAMESPACE_SEPARATOR = ":"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_UNTRUSTWORTHY = 2


# --- result types -----------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One defect: where it is, how many rows it affects, and what it is."""

    table: str
    code: str
    message: str
    column: str | None = None
    row_count: int | None = None

    def render(self) -> str:
        parts = [self.code, self.table]
        if self.column:
            parts.append(f"column={self.column}")
        if self.row_count is not None:
            parts.append(f"rows={self.row_count}")
        return f"{' '.join(parts)}: {self.message}"


@dataclass(frozen=True)
class TableResult:
    """Per-table verdict."""

    table: str
    present: bool
    n_rows: int | None
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings)


@dataclass(frozen=True)
class ValidationReport:
    """The whole verdict, per table, plus whatever went wrong with the manifest."""

    directory: str
    contract_version: str
    manifest_error: str | None
    results: tuple[TableResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.manifest_error is None and all(r.passed for r in self.results)

    @property
    def exit_code(self) -> int:
        if self.manifest_error is not None:
            return EXIT_UNTRUSTWORTHY
        return EXIT_OK if self.passed else EXIT_FINDINGS

    def findings(self) -> tuple[Finding, ...]:
        return tuple(f for r in self.results for f in r.findings)

    def findings_for(self, table: str) -> tuple[Finding, ...]:
        return tuple(f for r in self.results if r.table == table for f in r.findings)

    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings())

    def render(self) -> str:
        lines = [
            f"contract: {contract_spec.CONTRACT_NAME} {self.contract_version}",
            f"directory: {self.directory}",
        ]
        if self.manifest_error is not None:
            lines.append("")
            lines.append(f"MANIFEST {self.manifest_error}")
            lines.append("")
            lines.append("no table was checked: the manifest declares what to check against")
            return "\n".join(lines)
        lines.append("")
        for result in self.results:
            rows = "absent" if not result.present else f"{result.n_rows} rows"
            verdict = "PASS" if result.passed else "FAIL"
            lines.append(f"{verdict}  {result.table:<20} {rows}")
            for finding in result.findings:
                lines.append(f"        {finding.render()}")
        lines.append("")
        total = len(self.findings())
        lines.append("PASS: every table conforms" if self.passed else f"FAIL: {total} finding(s)")
        return "\n".join(lines)


# --- helpers ----------------------------------------------------------------


def _count_true(mask: pa.ChunkedArray | pa.Array) -> int:
    """Rows where ``mask`` is true. Nulls in the mask are not true."""
    if len(mask) == 0:
        return 0
    filled = pc.fill_null(mask, False)
    return int(pc.sum(pc.cast(filled, pa.int64())).as_py() or 0)


def _forbidden_reason(column: str) -> str | None:
    lowered = column.lower()
    for token, reason in FORBIDDEN_COLUMN_REASONS:
        if token in lowered:
            return reason
    return None


def _duplicate_rows(read: TableRead, key: tuple[str, ...]) -> tuple[int, int]:
    """(rows sitting on a duplicated key, number of duplicated key values)."""
    projected = read.table.select(list(key))
    grouped = projected.group_by(list(key)).aggregate([([], "count_all")])
    counts = grouped.column("count_all")
    duplicated = pc.greater(counts, 1)
    n_keys = _count_true(duplicated)
    if n_keys == 0:
        return 0, 0
    rows = int(pc.sum(pc.if_else(duplicated, counts, 0)).as_py() or 0)
    return rows, n_keys


# --- per-table checks -------------------------------------------------------


def _check_columns(read: TableRead) -> tuple[list[Finding], set[str]]:
    """Column presence and shape. Returns findings plus the usable column set."""
    findings: list[Finding] = []
    declared = set(read.spec.field_names)
    actual = set(read.column_names)

    for name in read.spec.field_names:
        if name not in actual:
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=MISSING_REQUIRED_COLUMN,
                    column=name,
                    row_count=read.n_rows,
                    message=(
                        f"the contract requires column {name!r} and the file does not have "
                        f"it; columns present: {sorted(actual)}"
                    ),
                )
            )

    for name in read.column_names:
        if name in declared:
            continue
        reason = _forbidden_reason(name)
        if reason is not None:
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=FORBIDDEN_COLUMN,
                    column=name,
                    row_count=read.n_rows,
                    message=f"column {name!r} is refused: {reason}",
                )
            )
        else:
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=UNEXPECTED_COLUMN,
                    column=name,
                    row_count=read.n_rows,
                    message=(
                        f"column {name!r} is not in the contract. Extra columns are refused "
                        "so a vendor-shaped table cannot arrive one field at a time; put "
                        "provenance in the manifest instead"
                    ),
                )
            )

    usable = actual & declared
    return findings, usable


def _check_field_types(read: TableRead, usable: set[str]) -> tuple[list[Finding], set[str]]:
    """Type checks. Returns findings plus the columns whose values are worth reading."""
    findings: list[Finding] = []
    typed_ok: set[str] = set()
    for spec_field in read.spec.fields:
        if spec_field.name not in usable:
            continue
        actual_type = read.arrow_type(spec_field.name)
        # Checked before the general type comparison, not after: a naive
        # timestamp is *also* a type mismatch, and if the generic check ran
        # first this defect would be reported under a generic code and its own
        # message -- the one that says why awareness matters -- would be
        # unreachable.
        if (
            pa.types.is_timestamp(spec_field.arrow_type)
            and pa.types.is_timestamp(actual_type)
            and actual_type.tz is None
        ):
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=TIMESTAMP_NOT_TIMEZONE_AWARE,
                    column=spec_field.name,
                    row_count=read.n_rows,
                    message=(
                        f"column {spec_field.name!r} is a timezone-naive timestamp "
                        f"({actual_type}); the contract declares {spec_field.arrow_type}. "
                        "Attach the zone the producing system actually stored these instants "
                        "in -- for most warehouses that is UTC -- at the point you write the "
                        "file. Do NOT localise them to the timezone you declared as the day "
                        "boundary: the engine applies that itself, so doing it here shifts "
                        "every instant twice, and the second shift is invisible because the "
                        "column then looks correct. If you do not know which zone the source "
                        "stored, that is the defect, and guessing it here is how it stops "
                        "being findable. A naive instant inherits whichever zone the producing "
                        "system happened to use, which is exactly the day-boundary error this "
                        "contract exists to refuse"
                    ),
                )
            )
            continue
        if not types_compatible(spec_field.arrow_type, actual_type):
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=COLUMN_TYPE_MISMATCH,
                    column=spec_field.name,
                    row_count=read.n_rows,
                    message=(
                        f"column {spec_field.name!r} is {actual_type}, the contract declares "
                        f"{spec_field.arrow_type}. Cast it to {spec_field.arrow_type} in the "
                        "query or job that writes this file, and look at what the cast does to "
                        "the values before you trust it -- a string column that will not cast "
                        "cleanly is carrying something the contract has no field for, and the "
                        "fix is upstream rather than a cast. The validator will not coerce it "
                        "for you: coercing is how a label becomes a number and a date becomes "
                        "nothing, silently, in the direction that keeps the pipeline running"
                    ),
                )
            )
            continue
        typed_ok.add(spec_field.name)
    return findings, typed_ok


def _check_values(read: TableRead, typed_ok: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for spec_field in read.spec.fields:
        if spec_field.name not in typed_ok:
            continue
        column = read.column(spec_field.name)
        if not spec_field.nullable and column.null_count:
            findings.append(
                Finding(
                    table=read.spec.name,
                    code=NULL_IN_NON_NULLABLE,
                    column=spec_field.name,
                    row_count=int(column.null_count),
                    message=(
                        f"column {spec_field.name!r} is declared non-nullable and holds "
                        f"{column.null_count} null value(s)"
                    ),
                )
            )
        if spec_field.enum is not None:
            findings.extend(_check_enum(read, spec_field, column))
        if spec_field.non_negative and len(column):
            negatives = _count_true(pc.less(column, 0))
            if negatives:
                findings.append(
                    Finding(
                        table=read.spec.name,
                        code=NEGATIVE_MEASURE,
                        column=spec_field.name,
                        row_count=negatives,
                        message=(
                            f"column {spec_field.name!r} holds {negatives} negative value(s); "
                            "the measure is a non-negative quantity"
                        ),
                    )
                )
    return findings


def _check_enum(read: TableRead, spec_field: FieldSpec, column: pa.ChunkedArray) -> list[Finding]:
    assert spec_field.enum is not None
    if len(column) == 0:
        return []
    permitted = pa.array(list(spec_field.enum), type=pa.string())
    # Nulls are excluded explicitly. `is_in` reports a null input as *not* in
    # the set rather than as null, so without the `is_valid` guard every null in
    # a nullable enum column -- a payer type the billing system cannot state,
    # for one -- is reported as an out-of-range value whose offending value is
    # nothing. Nullability is a separate check with its own code.
    outside = _count_true(
        pc.and_(
            pc.is_valid(column),
            pc.invert(pc.fill_null(pc.is_in(column, value_set=permitted), False)),
        )
    )
    if not outside:
        return []
    observed = sorted(
        {
            value
            for value in column.to_pylist()
            if value is not None and value not in spec_field.enum
        }
    )
    return [
        Finding(
            table=read.spec.name,
            code=ENUM_VALUE_OUT_OF_RANGE,
            column=spec_field.name,
            row_count=outside,
            message=(
                f"column {spec_field.name!r} holds {outside} row(s) whose value is outside the "
                f"contract vocabulary. Permitted: {list(spec_field.enum)}. Found: {observed}"
            ),
        )
    ]


def _check_dedup_key(read: TableRead, typed_ok: set[str]) -> list[Finding]:
    key = read.spec.dedup_key
    if not set(key).issubset(typed_ok):
        return []
    rows, n_keys = _duplicate_rows(read, key)
    if not rows:
        return []
    return [
        Finding(
            table=read.spec.name,
            code=DUPLICATE_DEDUP_KEY,
            column=", ".join(key),
            row_count=rows,
            message=(
                f"the deduplication key ({', '.join(key)}) is not unique: {n_keys} key value(s) "
                f"cover {rows} row(s). A duplicate on this key double-counts every measure "
                "derived from the table"
            ),
        )
    ]


def _check_conditional(read: TableRead, rule: ConditionalRule, typed_ok: set[str]) -> list[Finding]:
    if rule.when_column not in typed_ok or rule.then_column not in typed_ok:
        return []
    when = read.column(rule.when_column)
    selected = pc.fill_null(
        pc.is_in(when, value_set=pa.array(list(rule.when_values), type=pa.string())), False
    )
    if _count_true(selected) == 0:
        return []
    then_values = read.column(rule.then_column).to_pylist()
    selected_flags = selected.to_pylist()

    def _empty(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, list):
            return len(value) == 0
        return False

    if rule.requirement in (REQUIRE_NON_NULL, REQUIRE_NON_EMPTY_LIST):
        offending = sum(
            1
            for flag, value in zip(selected_flags, then_values, strict=True)
            if flag and _empty(value)
        )
        if offending:
            return [
                Finding(
                    table=read.spec.name,
                    code=CONDITIONAL_FIELD_REQUIRED,
                    column=rule.then_column,
                    row_count=offending,
                    message=(
                        f"rule {rule.rule_id!r}: {offending} row(s) with "
                        f"{rule.when_column} in {list(rule.when_values)} have no "
                        f"{rule.then_column}. {rule.definition}"
                    ),
                )
            ]
        return []

    if rule.requirement == REQUIRE_NULL_OR_EMPTY_LIST:
        offending = sum(
            1
            for flag, value in zip(selected_flags, then_values, strict=True)
            if flag and not _empty(value)
        )
        if offending:
            return [
                Finding(
                    table=read.spec.name,
                    code=CONDITIONAL_FIELD_FORBIDDEN,
                    column=rule.then_column,
                    row_count=offending,
                    message=(
                        f"rule {rule.rule_id!r}: {offending} row(s) with "
                        f"{rule.when_column} in {list(rule.when_values)} carry a "
                        f"{rule.then_column} they must not. {rule.definition}"
                    ),
                )
            ]
        return []
    raise AssertionError(f"unknown conditional requirement {rule.requirement!r}")


def _check_reader_ids(
    read: TableRead, typed_ok: set[str], registry: set[str] | None
) -> list[Finding]:
    """Grain, namespace prefixes, and membership of the reader registry."""
    findings: list[Finding] = []
    spec = read.spec

    if spec.name == contract_spec.READER.name and "id_grain" in typed_ok:
        grains = {v for v in read.column("id_grain").to_pylist() if v is not None}
        if len(grains) > 1:
            supported = set(contract_spec.READER.field_by_name("id_grain").enum or ())
            offending = _count_true(
                pc.invert(
                    pc.fill_null(
                        pc.is_in(
                            read.column("id_grain"),
                            value_set=pa.array(sorted(supported), type=pa.string()),
                        ),
                        True,
                    )
                )
            )
            findings.append(
                Finding(
                    table=spec.name,
                    code=MIXED_READER_ID_GRAIN,
                    column="id_grain",
                    row_count=offending or read.n_rows,
                    message=(
                        f"the reader registry mixes identity grains: {sorted(grains)}. One "
                        "reader id column may hold exactly one grain. Two grains in one "
                        "column make every distinct-reader count and every cross-channel "
                        "join meaningless, and no downstream check can see it"
                    ),
                )
            )

    id_column = spec.reader_reference_column or (
        "reader_id" if spec.name == contract_spec.READER.name else None
    )
    if id_column is None or id_column not in typed_ok:
        return findings

    values = read.column(id_column).to_pylist()
    namespaced = [v for v in values if v is not None and READER_ID_NAMESPACE_SEPARATOR in v]
    if namespaced:
        prefixes = sorted({v.split(READER_ID_NAMESPACE_SEPARATOR, 1)[0] for v in namespaced})
        findings.append(
            Finding(
                table=spec.name,
                code=NAMESPACED_READER_ID,
                column=id_column,
                row_count=len(namespaced),
                message=(
                    f"{len(namespaced)} reader id(s) carry a namespace prefix "
                    f"({prefixes}). A prefixed id announces its own grain, which is the "
                    "signature of two id spaces sharing one column. Reader ids in this "
                    "contract are opaque and single-grain"
                ),
            )
        )

    if registry is None or spec.name == contract_spec.READER.name:
        return findings

    present = [v for v in values if v is not None]
    orphans = [v for v in present if v not in registry]
    if not orphans:
        return findings
    if len(orphans) == len(present) and present:
        findings.append(
            Finding(
                table=spec.name,
                code=DISJOINT_READER_ID_SPACE,
                column=id_column,
                row_count=len(orphans),
                message=(
                    f"not one of the {len(orphans)} reader id(s) in this table appears in the "
                    "reader registry. This input is keyed on a different id space, so joining "
                    "it to reading activity would produce readers who look single-channel "
                    "because the join missed"
                ),
            )
        )
    else:
        findings.append(
            Finding(
                table=spec.name,
                code=UNKNOWN_READER_ID,
                column=id_column,
                row_count=len(orphans),
                message=(
                    f"{len(orphans)} reader id(s) are not in the reader registry. Every table "
                    "must reference the same declared id space"
                ),
            )
        )
    return findings


def _check_sections(read: TableRead, typed_ok: set[str]) -> list[Finding]:
    if read.spec.name != contract_spec.CONTENT.name or "sections" not in typed_ok:
        return []
    offending = 0
    for value in read.column("sections").to_pylist():
        if isinstance(value, list) and len(set(value)) != len(value):
            offending += 1
    if not offending:
        return []
    return [
        Finding(
            table=read.spec.name,
            code=DUPLICATE_SECTION,
            column="sections",
            row_count=offending,
            message=(
                f"{offending} row(s) repeat a section. A view of content in n sections "
                "contributes 1/n to each, so a repeated section inflates that content's own "
                "share and breaks the reconciliation to total views"
            ),
        )
    ]


def _check_spans(read: TableRead, typed_ok: set[str]) -> list[Finding]:
    if read.spec.name != contract_spec.SUBSCRIPTION_SPAN.name:
        return []
    if not {"reader_id", "start_ts", "end_ts"}.issubset(typed_ok):
        return []
    findings: list[Finding] = []
    rows = read.table.select(["reader_id", "start_ts", "end_ts"]).to_pylist()

    inverted = [r for r in rows if r["end_ts"] is not None and r["end_ts"] <= r["start_ts"]]
    if inverted:
        findings.append(
            Finding(
                table=read.spec.name,
                code=SPAN_END_NOT_AFTER_START,
                column="end_ts",
                row_count=len(inverted),
                message=(
                    f"{len(inverted)} span(s) end at or before they start. Intervals are "
                    "half-open [start_ts, end_ts), so end_ts must be strictly later"
                ),
            )
        )

    by_reader: dict[str, list[dict]] = {}
    for row in rows:
        if row["reader_id"] is not None and row["start_ts"] is not None:
            by_reader.setdefault(row["reader_id"], []).append(row)

    overlapping = 0
    multiple_open = 0
    for spans in by_reader.values():
        open_spans = [s for s in spans if s["end_ts"] is None]
        if len(open_spans) > 1:
            multiple_open += len(open_spans)
        ordered = sorted(spans, key=lambda s: s["start_ts"])
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous["end_ts"] is None or previous["end_ts"] > current["start_ts"]:
                overlapping += 1
    if overlapping:
        findings.append(
            Finding(
                table=read.spec.name,
                code=OVERLAPPING_SPANS,
                column="start_ts, end_ts",
                row_count=overlapping,
                message=(
                    f"{overlapping} span pair(s) overlap for the same reader. A reader has one "
                    "state at a time, and overlapping spans make status as of a date ambiguous"
                ),
            )
        )
    if multiple_open:
        findings.append(
            Finding(
                table=read.spec.name,
                code=MULTIPLE_OPEN_SPANS,
                column="end_ts",
                row_count=multiple_open,
                message=(
                    f"{multiple_open} open span(s) share a reader. At most one span per reader "
                    "may have a null end_ts"
                ),
            )
        )
    return findings


def _check_availability_floor(
    read: TableRead, typed_ok: set[str], manifest: Manifest
) -> list[Finding]:
    spec = read.spec
    if spec.required or spec.event_time_column is None:
        return []
    if spec.event_time_column not in typed_ok:
        return []
    availability = manifest.optional_inputs[spec.name]
    floor = availability.available_from
    if floor is None or read.n_rows == 0:
        return []
    floor_ts = datetime.combine(floor, time.min, tzinfo=manifest.zoneinfo())
    column = read.column(spec.event_time_column)
    threshold = pa.scalar(floor_ts, type=column.type)
    early = _count_true(pc.less(column, threshold))
    if not early:
        return []
    return [
        Finding(
            table=spec.name,
            code=EVENT_BEFORE_AVAILABILITY_FLOOR,
            column=spec.event_time_column,
            row_count=early,
            message=(
                f"{early} row(s) fall before the declared availability floor "
                f"{floor.isoformat()} ({manifest.day_boundary_timezone}). Either the floor is "
                "wrong or the rows are, and the difference decides whether a pre-launch "
                "period is a gap or a real zero"
            ),
        )
    ]


def _validate_table(read: TableRead, manifest: Manifest, registry: set[str] | None) -> TableResult:
    findings, usable = _check_columns(read)
    type_findings, typed_ok = _check_field_types(read, usable)
    findings.extend(type_findings)
    findings.extend(_check_values(read, typed_ok))
    findings.extend(_check_dedup_key(read, typed_ok))
    for rule in read.spec.conditional_rules:
        findings.extend(_check_conditional(read, rule, typed_ok))
    findings.extend(_check_reader_ids(read, typed_ok, registry))
    findings.extend(_check_sections(read, typed_ok))
    findings.extend(_check_spans(read, typed_ok))
    findings.extend(_check_availability_floor(read, typed_ok, manifest))
    return TableResult(
        table=read.spec.name,
        present=True,
        n_rows=read.n_rows,
        findings=tuple(findings),
    )


def _read_registry(reader: TypedReader) -> set[str] | None:
    """The set of declared reader ids, or ``None`` if the registry is unusable."""
    spec = contract_spec.READER
    if not reader.exists(spec):
        return None
    try:
        read = reader.read(spec)
    except TableReadError:
        return None
    if "reader_id" not in read.column_names:
        return None
    if not types_compatible(pa.string(), read.arrow_type("reader_id")):
        return None
    return {v for v in read.column("reader_id").to_pylist() if v is not None}


def validate_directory(directory: str | Path) -> ValidationReport:
    """Validate every contract table in ``directory``."""
    directory = Path(directory)
    try:
        manifest = load_manifest(directory)
    except ManifestError as exc:
        return ValidationReport(
            directory=str(directory),
            contract_version=contract_spec.CONTRACT_VERSION,
            manifest_error=str(exc),
        )

    reader = TypedReader(directory)
    registry = _read_registry(reader)
    results: list[TableResult] = []

    for spec in TABLES:
        exists = reader.exists(spec)
        declared_available = spec.required or manifest.optional_inputs[spec.name].is_available

        if not exists:
            results.append(_absent_result(spec, declared_available))
            continue
        if not declared_available:
            status = manifest.optional_inputs[spec.name].status
            results.append(
                TableResult(
                    table=spec.name,
                    present=True,
                    n_rows=None,
                    findings=(
                        Finding(
                            table=spec.name,
                            code=FILE_PRESENT_BUT_DECLARED_ABSENT,
                            message=(
                                f"the file is in the delivery but the manifest declares this "
                                f"input {status!r}. The two statements cannot both be true, and "
                                "the manifest is what the engine plans against"
                            ),
                        ),
                    ),
                )
            )
            continue
        try:
            read = reader.read(spec)
        except TableReadError as exc:
            results.append(
                TableResult(
                    table=spec.name,
                    present=True,
                    n_rows=None,
                    findings=(Finding(table=spec.name, code=UNREADABLE_TABLE, message=str(exc)),),
                )
            )
            continue
        results.append(_validate_table(read, manifest, registry))

    return ValidationReport(
        directory=str(directory),
        contract_version=manifest.contract_version,
        manifest_error=None,
        results=tuple(results),
    )


def _absent_result(spec: TableSpec, declared_available: bool) -> TableResult:
    if spec.required:
        return TableResult(
            table=spec.name,
            present=False,
            n_rows=None,
            findings=(
                Finding(
                    table=spec.name,
                    code=MISSING_REQUIRED_TABLE,
                    message=(
                        f"{spec.filename} is required by the contract and is not in the "
                        f"delivery. Its grain: {spec.grain} Unique on "
                        f"({', '.join(spec.dedup_key)}). Note what the fix is *not*: the "
                        "manifest's availability mechanism covers the three optional inputs "
                        "only, so there is no way to declare a required input absent. Either "
                        "the file is produced, or the delivery cannot conform yet -- and the "
                        "second is a real answer, reached by narrowing the window to a period "
                        "the input covers or by concluding this deployment is not ready. "
                        "docs/contract-reference.md has the field list"
                    ),
                ),
            ),
        )
    if declared_available:
        return TableResult(
            table=spec.name,
            present=False,
            n_rows=None,
            findings=(
                Finding(
                    table=spec.name,
                    code=FILE_ABSENT_BUT_DECLARED_AVAILABLE,
                    message=(
                        "the manifest declares this optional input 'available' but the file is "
                        "not in the delivery. An input declared available and then missing "
                        "would be read as zero activity rather than as an absent input"
                    ),
                ),
            ),
        )
    return TableResult(table=spec.name, present=False, n_rows=None)


# --- command line -----------------------------------------------------------

_EPILOG = """\
exit status:
  0  every table conforms
  1  the delivery was read and does not conform; each finding names the table,
     the column and the number of rows affected
  2  the verdict could not be trusted -- the manifest is absent or invalid, so
     there is no timezone, week anchor, article-view definition or availability
     floor to check against
"""


def report_to_dict(report: ValidationReport) -> dict:
    """The report as plain JSON-able data, for a producer's own CI."""
    return {
        "contract_name": contract_spec.CONTRACT_NAME,
        "contract_version": report.contract_version,
        "directory": report.directory,
        "passed": report.passed,
        "manifest_error": report.manifest_error,
        "tables": [
            {
                "table": result.table,
                "present": result.present,
                "rows": result.n_rows,
                "passed": result.passed,
                "findings": [
                    {
                        "code": finding.code,
                        "column": finding.column,
                        "rows": finding.row_count,
                        "message": finding.message,
                    }
                    for finding in result.findings
                ],
            }
            for result in report.results
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-validate",
        description=(
            "Validate a delivery directory of Parquet files, plus its manifest.json, "
            "against the canonical engagement-kernel input contract."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        help="directory holding the contract's Parquet files and manifest.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of text (same exit status)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a directory and print the per-table verdict.

    Returns the exit status rather than calling ``sys.exit``, so the same code
    path is exercised by the tests that assert on the messages.
    """
    args = build_parser().parse_args(argv)
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"not a directory: {directory}", file=sys.stderr)
        return EXIT_UNTRUSTWORTHY
    report = validate_directory(directory)
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
    else:
        print(report.render())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
