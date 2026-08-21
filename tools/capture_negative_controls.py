#!/usr/bin/env python3
"""Build deliberately malformed deliveries and capture what the validator says.

A validator that has only ever seen good data has proven nothing. This script is
the evidence that it refuses bad data, and -- more importantly -- that it refuses
each kind of bad data *for its own reason*: every case below names the code, the
column and the row count it expects, and ``tests/test_negative_controls.py``
asserts the exact set of finding codes the case produces. A case that started
failing for an unrelated reason, or stopped failing at all, breaks the test
rather than quietly becoming decoration.

Each case starts from the conformant synthetic delivery in
``engagement_kernel.contract.demo``, applies exactly one mutation, and runs the
real validator over the result. The conformant base is checked too, in the test
suite: if the base did not pass, every case below would be meaningless.

Usage::

    python3 tools/capture_negative_controls.py            # print the evidence
    python3 tools/capture_negative_controls.py --write    # update the doc

The generated document is committed, and a test compares the committed text
against a fresh render, so it cannot go stale.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:  # running as a script, not an installed package
    sys.path.insert(0, str(REPO_ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from engagement_kernel.contract import demo, spec, validate  # noqa: E402
from engagement_kernel.contract.manifest import MANIFEST_FILENAME  # noqa: E402

DOC_RELPATH = "docs/validator-negative-controls.md"

#: The delivery directory is a temporary path, so it is normalised out of the
#: captured text -- otherwise the committed evidence would differ on every run
#: and the staleness test could not exist.
DIRECTORY_PLACEHOLDER = "<delivery>"


# --- the delivery under mutation --------------------------------------------


@dataclass
class Delivery:
    """A delivery about to be written to disk: tables, plus a manifest or none."""

    tables: dict[str, pa.Table]
    manifest: dict | None

    @classmethod
    def conformant(cls) -> Delivery:
        return cls(tables=demo.build_tables(), manifest=demo.build_manifest())

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, table in self.tables.items():
            pq.write_table(
                table, directory / spec.TABLES_BY_NAME[name].filename, compression="none"
            )
        if self.manifest is not None:
            (directory / MANIFEST_FILENAME).write_text(
                json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8"
            )


# --- mutation helpers -------------------------------------------------------


def _replace_column(
    table: pa.Table, column: str, values: list, arrow_type: pa.DataType | None = None
) -> pa.Table:
    index = table.schema.get_field_index(column)
    existing = table.schema.field(index)
    arrow_type = existing.type if arrow_type is None else arrow_type
    # Always nullable in the file. Parquet refuses to *write* a null into a
    # column its own schema marks non-nullable, which would make the
    # null-in-non-nullable controls untestable -- and would prove the wrong
    # thing anyway. Nullability in this contract is a rule the validator
    # enforces from the spec, not a flag it trusts from the file.
    return table.set_column(
        index,
        pa.field(column, arrow_type, nullable=True),
        pa.array(values, type=arrow_type),
    )


def _set_value(
    table: pa.Table, column: str, row: int, value: object, arrow_type: pa.DataType | None = None
) -> pa.Table:
    values = table.column(column).to_pylist()
    values[row] = value
    return _replace_column(table, column, values, arrow_type)


def _duplicate_row(table: pa.Table, row: int) -> pa.Table:
    return pa.concat_tables([table, table.slice(row, 1)])


def _add_column(table: pa.Table, column: str, filler: object = "x") -> pa.Table:
    return table.append_column(
        pa.field(column, pa.string(), nullable=True),
        pa.array([filler] * table.num_rows, type=pa.string()),
    )


def _row_index(table: pa.Table, column: str, value: object) -> int:
    return table.column(column).to_pylist().index(value)


# --- case declarations ------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One negative control.

    ``expected_codes`` is the **exact** set of finding codes the case must
    produce -- not a subset. A control that also trips something unrelated is a
    control that has stopped proving what it claims, and a few cases genuinely
    produce two codes because one defect really does imply the other; those say
    so, in ``note``.
    """

    case_id: str
    defect_class: str
    table: str
    mutation: str
    expected_codes: frozenset[str]
    primary_code: str | None
    expected_column: str | None
    expected_rows: int | None
    expected_exit: int
    mutate: Callable[[Delivery], None]
    note: str = ""
    expected_manifest_error: str = ""
    section: str = ""

    def run(self) -> validate.ValidationReport:
        delivery = Delivery.conformant()
        self.mutate(delivery)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            delivery.write(directory)
            return validate.validate_directory(directory)


CASES: list[Case] = []


def case(**kwargs: object) -> Callable[[Callable[[Delivery], None]], Callable[[Delivery], None]]:
    """Register a case, taking the mutation from the decorated function."""

    def register(func: Callable[[Delivery], None]) -> Callable[[Delivery], None]:
        codes = kwargs.pop("codes")
        assert isinstance(codes, tuple)
        primary = kwargs.pop("primary_code", None) or (codes[0] if codes else None)
        CASES.append(
            Case(
                mutate=func,
                expected_codes=frozenset(codes),
                primary_code=primary,
                **kwargs,  # type: ignore[arg-type]
            )
        )
        return func

    return register


V = validate

# --- reader -----------------------------------------------------------------


@case(
    case_id="reader.missing_required_column",
    section="reader",
    defect_class="missing required column",
    table="reader",
    mutation="drop the `id_grain` column",
    codes=(V.MISSING_REQUIRED_COLUMN,),
    expected_column="id_grain",
    expected_rows=9,
    expected_exit=V.EXIT_FINDINGS,
)
def _reader_missing_column(delivery: Delivery) -> None:
    delivery.tables["reader"] = delivery.tables["reader"].drop_columns(["id_grain"])


@case(
    case_id="reader.wrong_dtype",
    section="reader",
    defect_class="wrong dtype",
    table="reader",
    mutation="supply `reader_id` as int64 instead of string",
    codes=(V.COLUMN_TYPE_MISMATCH,),
    expected_column="reader_id",
    expected_rows=9,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The registry is unreadable when its key column has the wrong type, so no other "
        "table is checked for membership of it. That is why the type check runs before the "
        "value checks: the alternative is nine tables reporting orphaned ids."
    ),
)
def _reader_wrong_dtype(delivery: Delivery) -> None:
    table = delivery.tables["reader"]
    delivery.tables["reader"] = _replace_column(
        table, "reader_id", list(range(1, table.num_rows + 1)), pa.int64()
    )


@case(
    case_id="reader.null_in_non_nullable",
    section="reader",
    defect_class="null in a non-nullable field",
    table="reader",
    mutation="null out one `id_grain`",
    codes=(V.NULL_IN_NON_NULLABLE,),
    expected_column="id_grain",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _reader_null(delivery: Delivery) -> None:
    delivery.tables["reader"] = _set_value(delivery.tables["reader"], "id_grain", 3, None)


@case(
    case_id="reader.duplicate_dedup_key",
    section="reader",
    defect_class="duplicate on the stated dedup key",
    table="reader",
    mutation="append a second row for the first reader",
    codes=(V.DUPLICATE_DEDUP_KEY,),
    expected_column="reader_id",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
)
def _reader_duplicate(delivery: Delivery) -> None:
    delivery.tables["reader"] = _duplicate_row(delivery.tables["reader"], 0)


@case(
    case_id="reader.enum_out_of_range",
    section="reader",
    defect_class="out-of-range enum value",
    table="reader",
    mutation="set one `id_grain` to `session`",
    codes=(V.ENUM_VALUE_OUT_OF_RANGE, V.MIXED_READER_ID_GRAIN),
    expected_column="id_grain",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Two codes, and both are correct. The contract permits exactly one grain, so any "
        "out-of-vocabulary grain value in a registry that also holds the permitted one is "
        "simultaneously an unknown value and a mixed-grain column."
    ),
)
def _reader_enum(delivery: Delivery) -> None:
    delivery.tables["reader"] = _set_value(delivery.tables["reader"], "id_grain", 2, "session")


@case(
    case_id="reader.mixed_id_grain",
    section="reader",
    defect_class="mixed reader-id grain (one id column, two grains)",
    table="reader",
    mutation="declare two readers at `device_browser` grain alongside resolved people",
    codes=(V.MIXED_READER_ID_GRAIN, V.ENUM_VALUE_OUT_OF_RANGE),
    primary_code=V.MIXED_READER_ID_GRAIN,
    expected_column="id_grain",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The rejection this contract exists for. A device id and a person id in one column "
        "make every distinct-reader count and every cross-channel join meaningless, and "
        "nothing downstream can see it -- so it is refused here, by name, rather than "
        "discouraged in prose."
    ),
)
def _reader_mixed_grain(delivery: Delivery) -> None:
    table = delivery.tables["reader"]
    table = _set_value(table, "id_grain", 4, "device_browser")
    delivery.tables["reader"] = _set_value(table, "id_grain", 5, "device_browser")


@case(
    case_id="reader_event.namespaced_reader_id",
    section="reader",
    defect_class="namespaced reader id (the visible half of a mixed id space)",
    table="reader_event",
    mutation="prefix one event's `reader_id` with `login:`",
    codes=(V.NAMESPACED_READER_ID, V.UNKNOWN_READER_ID),
    expected_column="reader_id",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The second code is the referential check doing its job: a prefixed id is not the "
        "id that is in the registry. This is the mechanically visible signature of the "
        "mixed-grain defect above -- a prefix announces its own grain."
    ),
)
def _event_namespaced(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _set_value(
        delivery.tables["reader_event"], "reader_id", 0, f"login:{demo.READER_FULL_HISTORY}"
    )


@case(
    case_id="email_click.disjoint_id_space",
    section="reader",
    defect_class="a whole table keyed on a different id space",
    table="email_click",
    mutation="re-key every email click onto ids from another id space",
    codes=(V.DISJOINT_READER_ID_SPACE,),
    expected_column="reader_id",
    expected_rows=7,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Reported separately from a handful of unknown ids because the consequence differs: "
        "every reader looks email-inactive, which is indistinguishable from real "
        "disengagement unless the join failure is named."
    ),
)
def _email_disjoint(delivery: Delivery) -> None:
    table = delivery.tables["email_click"]
    delivery.tables["email_click"] = _replace_column(
        table, "reader_id", [f"crm-{index:04d}" for index in range(table.num_rows)]
    )


# --- reader_event -----------------------------------------------------------


@case(
    case_id="reader_event.missing_required_column",
    section="reader_event",
    defect_class="missing required column",
    table="reader_event",
    mutation="drop the `session_id` column",
    codes=(V.MISSING_REQUIRED_COLUMN,),
    expected_column="session_id",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Sessions are required as rows, not as a pre-aggregated count, so a delivery cannot "
        "satisfy this by supplying a number the validator has no way to check."
    ),
)
def _event_missing_column(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = delivery.tables["reader_event"].drop_columns(["session_id"])


@case(
    case_id="reader_event.wrong_dtype",
    section="reader_event",
    defect_class="wrong dtype",
    table="reader_event",
    mutation="supply `engagement_time_seconds` as int64 instead of float64",
    codes=(V.COLUMN_TYPE_MISMATCH,),
    expected_column="engagement_time_seconds",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
)
def _event_wrong_dtype(delivery: Delivery) -> None:
    table = delivery.tables["reader_event"]
    values = [
        None if v is None else int(v) for v in table.column("engagement_time_seconds").to_pylist()
    ]
    delivery.tables["reader_event"] = _replace_column(
        table, "engagement_time_seconds", values, pa.int64()
    )


@case(
    case_id="reader_event.timezone_naive_timestamp",
    section="reader_event",
    defect_class="timezone-naive timestamp (the day-boundary defect)",
    table="reader_event",
    mutation="strip the timezone from `event_ts`",
    codes=(V.TIMESTAMP_NOT_TIMEZONE_AWARE,),
    expected_column="event_ts",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "This is the defect the contract's shape exists to prevent, and it gets its own code "
        "rather than being reported as a generic type mismatch. A naive instant silently "
        "inherits whichever zone the producing system used; nothing downstream can recover "
        "the boundary, and every window is mis-bucketed by hours while looking plausible."
    ),
)
def _event_naive_timestamp(delivery: Delivery) -> None:
    table = delivery.tables["reader_event"]
    naive = [value.replace(tzinfo=None) for value in table.column("event_ts").to_pylist()]
    delivery.tables["reader_event"] = _replace_column(table, "event_ts", naive, pa.timestamp("us"))


@case(
    case_id="reader_event.null_in_non_nullable",
    section="reader_event",
    defect_class="null in a non-nullable field",
    table="reader_event",
    mutation="null out one `session_id`",
    codes=(V.NULL_IN_NON_NULLABLE,),
    expected_column="session_id",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _event_null(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _set_value(
        delivery.tables["reader_event"], "session_id", 0, None
    )


@case(
    case_id="reader_event.duplicate_dedup_key",
    section="reader_event",
    defect_class="duplicate on the stated dedup key",
    table="reader_event",
    mutation="re-deliver one event under the same `event_id`",
    codes=(V.DUPLICATE_DEDUP_KEY,),
    expected_column="event_id",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The reason the contract requires a stable event id at all: a re-delivery that "
        "reuses its id is caught here, and one that invents a new id is not distinguishable "
        "from a real second event by anything."
    ),
)
def _event_duplicate(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _duplicate_row(delivery.tables["reader_event"], 0)


@case(
    case_id="reader_event.enum_out_of_range",
    section="reader_event",
    defect_class="out-of-range enum value",
    table="reader_event",
    mutation="set one `channel` to `newsletter`",
    codes=(V.ENUM_VALUE_OUT_OF_RANGE,),
    expected_column="channel",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _event_enum(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _set_value(
        delivery.tables["reader_event"], "channel", 1, "newsletter"
    )


@case(
    case_id="reader_event.delivery_without_content_id",
    section="reader_event",
    defect_class="conditional requirement violated",
    table="reader_event",
    mutation="null the `content_id` of a delivery event",
    codes=(V.CONDITIONAL_FIELD_REQUIRED,),
    expected_column="content_id",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "A delivery with no content id cannot be attributed to a piece of content, so it "
        "cannot be a view of one. Nulling it would otherwise silently shrink every "
        "view-based measure."
    ),
)
def _event_conditional(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _set_value(
        delivery.tables["reader_event"], "content_id", 0, None
    )


@case(
    case_id="reader_event.negative_measure",
    section="reader_event",
    defect_class="negative value in a non-negative measure",
    table="reader_event",
    mutation="set one `engagement_time_seconds` to -5.0",
    codes=(V.NEGATIVE_MEASURE,),
    expected_column="engagement_time_seconds",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _event_negative(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _set_value(
        delivery.tables["reader_event"], "engagement_time_seconds", 0, -5.0
    )


@case(
    case_id="reader_event.prebucketed_date_column",
    section="reader_event",
    defect_class="forbidden column: a pre-bucketed calendar date",
    table="reader_event",
    mutation="add an `event_date` column alongside the instant",
    codes=(V.FORBIDDEN_COLUMN,),
    expected_column="event_date",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Refused by name, with its own reason, because this is how the day-boundary defect "
        "comes back: a producer keeps its convenient per-source date, the engine reads it, "
        "and the timezone the manifest declares stops being the one that decides a day."
    ),
)
def _event_prebucketed(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _add_column(
        delivery.tables["reader_event"], "event_date", "2026-02-16"
    )


@case(
    case_id="reader_event.scroll_column",
    section="reader_event",
    defect_class="forbidden column: an out-of-scope measure",
    table="reader_event",
    mutation="add a `total_scroll_pct` column",
    codes=(V.FORBIDDEN_COLUMN,),
    expected_column="total_scroll_pct",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Scroll depth is declared out of scope, so it is refused rather than ignored. On "
        "surfaces where it cannot be measured it arrives as a hardcoded zero, and a "
        "mixed-surface deployment then compares a real number against that zero."
    ),
)
def _event_scroll(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _add_column(
        delivery.tables["reader_event"], "total_scroll_pct", "0.0"
    )


@case(
    case_id="reader_event.personal_data_column",
    section="reader_event",
    defect_class="forbidden column: personal data",
    table="reader_event",
    mutation="add a column whose name announces personal data",
    codes=(V.FORBIDDEN_COLUMN,),
    expected_column="reader_email_hint",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The contract requires no personal data, so a column that announces it is refused "
        "on arrival. The check is on the column name, which catches the accident, not the "
        "adversary -- but the accident is the realistic case."
    ),
)
def _event_personal(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _add_column(
        delivery.tables["reader_event"], "reader_email_hint", "redacted"
    )


@case(
    case_id="reader_event.unexpected_column",
    section="reader_event",
    defect_class="unexpected column",
    table="reader_event",
    mutation="add a vendor column the contract does not declare",
    codes=(V.UNEXPECTED_COLUMN,),
    expected_column="referrer_medium",
    expected_rows=26,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Extra columns fail closed. A vendor-shaped table arrives one convenient field at a "
        "time, and the point of a contract is that the shape is agreed rather than inferred."
    ),
)
def _event_unexpected(delivery: Delivery) -> None:
    delivery.tables["reader_event"] = _add_column(
        delivery.tables["reader_event"], "referrer_medium", "organic"
    )


# --- content ----------------------------------------------------------------


@case(
    case_id="content.missing_required_column",
    section="content",
    defect_class="missing required column",
    table="content",
    mutation="drop the `sections` column",
    codes=(V.MISSING_REQUIRED_COLUMN,),
    expected_column="sections",
    expected_rows=10,
    expected_exit=V.EXIT_FINDINGS,
)
def _content_missing_column(delivery: Delivery) -> None:
    delivery.tables["content"] = delivery.tables["content"].drop_columns(["sections"])


@case(
    case_id="content.wrong_dtype",
    section="content",
    defect_class="wrong dtype",
    table="content",
    mutation="supply `sections` as a comma-joined string instead of a list",
    codes=(V.COLUMN_TYPE_MISMATCH,),
    expected_column="sections",
    expected_rows=10,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The shape that loses the 1/n attribution rule: a joined string has to be split by "
        "a convention nobody wrote down, and a section containing the separator disappears."
    ),
)
def _content_wrong_dtype(delivery: Delivery) -> None:
    table = delivery.tables["content"]
    joined = [
        None if value is None else ",".join(value) for value in table.column("sections").to_pylist()
    ]
    delivery.tables["content"] = _replace_column(table, "sections", joined, pa.string())


@case(
    case_id="content.null_in_non_nullable",
    section="content",
    defect_class="null in a non-nullable field",
    table="content",
    mutation="null out one `content_type`",
    codes=(V.NULL_IN_NON_NULLABLE,),
    expected_column="content_type",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _content_null(delivery: Delivery) -> None:
    delivery.tables["content"] = _set_value(delivery.tables["content"], "content_type", 0, None)


@case(
    case_id="content.duplicate_dedup_key",
    section="content",
    defect_class="duplicate on the stated dedup key",
    table="content",
    mutation="append a second row for the same `content_id`",
    codes=(V.DUPLICATE_DEDUP_KEY,),
    expected_column="content_id",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
)
def _content_duplicate(delivery: Delivery) -> None:
    delivery.tables["content"] = _duplicate_row(delivery.tables["content"], 0)


@case(
    case_id="content.enum_out_of_range",
    section="content",
    defect_class="out-of-range enum value",
    table="content",
    mutation="set one `section_resolution` to `partial`",
    codes=(V.ENUM_VALUE_OUT_OF_RANGE,),
    expected_column="section_resolution",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "`resolved` and `unresolved` are the whole vocabulary. A third value would make "
        "'we have no metadata' and 'we forgot the column' indistinguishable again."
    ),
)
def _content_enum(delivery: Delivery) -> None:
    delivery.tables["content"] = _set_value(
        delivery.tables["content"], "section_resolution", 0, "partial"
    )


@case(
    case_id="content.duplicate_section_in_list",
    section="content",
    defect_class="repeated section inside one content's list",
    table="content",
    mutation="repeat a section in one `sections` list",
    codes=(V.DUPLICATE_SECTION,),
    expected_column="sections",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "A view of content in n sections contributes 1/n to each. A repeat inflates that "
        "content's own share and breaks the reconciliation back to total views."
    ),
)
def _content_duplicate_section(delivery: Delivery) -> None:
    delivery.tables["content"] = _set_value(
        delivery.tables["content"], "sections", 0, ["news", "news"]
    )


@case(
    case_id="content.resolved_without_sections",
    section="content",
    defect_class="conditional requirement violated",
    table="content",
    mutation="declare content `resolved` and give it no sections",
    codes=(V.CONDITIONAL_FIELD_REQUIRED,),
    expected_column="sections",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _content_resolved_without_sections(delivery: Delivery) -> None:
    delivery.tables["content"] = _set_value(delivery.tables["content"], "sections", 0, None)


@case(
    case_id="content.unresolved_with_sections",
    section="content",
    defect_class="conditional prohibition violated",
    table="content",
    mutation="declare content `unresolved` and give it a section anyway",
    codes=(V.CONDITIONAL_FIELD_FORBIDDEN,),
    expected_column="sections",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note="One of the two statements would have to be false, so neither can be trusted.",
)
def _content_unresolved_with_sections(delivery: Delivery) -> None:
    table = delivery.tables["content"]
    row = _row_index(table, "content_id", "cnt-07")
    delivery.tables["content"] = _set_value(table, "sections", row, ["news"])


# --- subscription_span ------------------------------------------------------


@case(
    case_id="subscription_span.missing_required_column",
    section="subscription_span",
    defect_class="missing required column",
    table="subscription_span",
    mutation="drop the `payer_type` column",
    codes=(V.MISSING_REQUIRED_COLUMN,),
    expected_column="payer_type",
    expected_rows=16,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Nullable is not optional. The column has to be there, carrying nulls where the "
        "billing system cannot say -- otherwise 'unknown payer' and 'no such concept here' "
        "are the same absence."
    ),
)
def _span_missing_column(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = delivery.tables["subscription_span"].drop_columns(
        ["payer_type"]
    )


@case(
    case_id="subscription_span.wrong_dtype",
    section="subscription_span",
    defect_class="wrong dtype",
    table="subscription_span",
    mutation="supply `start_ts` as an ISO date string",
    codes=(V.COLUMN_TYPE_MISMATCH,),
    expected_column="start_ts",
    expected_rows=16,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Refused rather than parsed. A reader that coerces this is the reader that turns a "
        "state label into a number and a date into nothing -- which is why one typed reader "
        "serves the whole contract and coerces nothing."
    ),
)
def _span_wrong_dtype(delivery: Delivery) -> None:
    table = delivery.tables["subscription_span"]
    values = [value.date().isoformat() for value in table.column("start_ts").to_pylist()]
    delivery.tables["subscription_span"] = _replace_column(table, "start_ts", values, pa.string())


@case(
    case_id="subscription_span.null_in_non_nullable",
    section="subscription_span",
    defect_class="null in a non-nullable field",
    table="subscription_span",
    mutation="null out one `state`",
    codes=(V.NULL_IN_NON_NULLABLE,),
    expected_column="state",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _span_null(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = _set_value(
        delivery.tables["subscription_span"], "state", 0, None
    )


@case(
    case_id="subscription_span.duplicate_dedup_key",
    section="subscription_span",
    defect_class="duplicate on the stated dedup key",
    table="subscription_span",
    mutation="append a second span with the same `(reader_id, start_ts)`",
    codes=(V.DUPLICATE_DEDUP_KEY, V.OVERLAPPING_SPANS),
    expected_column="reader_id, start_ts",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Two codes, and both are true of the data: a duplicated interval is also an "
        "overlapping one. There is no mutation that duplicates the key without overlapping, "
        "which is itself worth knowing about this table."
    ),
)
def _span_duplicate(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = _duplicate_row(delivery.tables["subscription_span"], 0)


@case(
    case_id="subscription_span.enum_out_of_range",
    section="subscription_span",
    defect_class="out-of-range enum value",
    table="subscription_span",
    mutation="set one `state` to `churned`",
    codes=(V.ENUM_VALUE_OUT_OF_RANGE,),
    expected_column="state",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The publisher maps its own billing states onto the contract's seven. A state "
        "outside them is refused here rather than silently excluded from the population "
        "later, which is what an unrecognised label does to a spine filter."
    ),
)
def _span_enum(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = _set_value(
        delivery.tables["subscription_span"], "state", 1, "churned"
    )


@case(
    case_id="subscription_span.end_before_start",
    section="subscription_span",
    defect_class="interval that ends before it starts",
    table="subscription_span",
    mutation="move one `end_ts` earlier than its `start_ts`",
    codes=(V.SPAN_END_NOT_AFTER_START,),
    expected_column="end_ts",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
)
def _span_inverted(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = _set_value(
        delivery.tables["subscription_span"],
        "end_ts",
        0,
        datetime(2025, 8, 1, tzinfo=UTC),
    )


@case(
    case_id="subscription_span.multiple_open_spans",
    section="subscription_span",
    defect_class="more than one open interval for one reader",
    table="subscription_span",
    mutation="null the `end_ts` of an already-closed span",
    codes=(V.MULTIPLE_OPEN_SPANS, V.OVERLAPPING_SPANS),
    primary_code=V.MULTIPLE_OPEN_SPANS,
    expected_column="end_ts",
    expected_rows=2,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Two open spans make status as of a date ambiguous, and the ambiguity resolves "
        "differently depending on join order -- so the same delivery scores differently on "
        "different runs. The overlap code fires for the same reason."
    ),
)
def _span_multiple_open(delivery: Delivery) -> None:
    delivery.tables["subscription_span"] = _set_value(
        delivery.tables["subscription_span"], "end_ts", 1, None
    )


# --- optional inputs and the manifest ---------------------------------------


@case(
    case_id="email_click.event_before_availability_floor",
    section="optional inputs",
    defect_class="event before the input's declared availability floor",
    table="email_click",
    mutation="move one click to before the declared floor date",
    codes=(V.EVENT_BEFORE_AVAILABILITY_FLOOR,),
    expected_column="event_ts",
    expected_rows=1,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "Either the floor is wrong or the row is, and the difference decides whether a "
        "pre-launch period is a gap to be excluded or a real zero to be modelled."
    ),
)
def _email_before_floor(delivery: Delivery) -> None:
    delivery.tables["email_click"] = _set_value(
        delivery.tables["email_click"], "event_ts", 0, datetime(2025, 6, 1, 12, tzinfo=UTC)
    )


@case(
    case_id="community_action.file_present_but_declared_absent",
    section="optional inputs",
    defect_class="manifest and delivery contradict each other",
    table="community_action",
    mutation="declare the community input `not_deployed` while still shipping the file",
    codes=(V.FILE_PRESENT_BUT_DECLARED_ABSENT,),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The manifest is what the engine plans its feature set against, so the two "
        "statements cannot both stand. Trusting the file would silently re-add a feature "
        "block the run reported as dropped."
    ),
)
def _community_declared_absent(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["optional_inputs"]["community_action"] = {"status": "not_deployed"}


@case(
    case_id="email_click.file_absent_but_declared_available",
    section="optional inputs",
    defect_class="declared available, then not delivered",
    table="email_click",
    mutation="declare email clicks available and omit the file",
    codes=(V.FILE_ABSENT_BUT_DECLARED_AVAILABLE,),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_FINDINGS,
    note=(
        "The absence that must not be read as zero activity. Declared-and-missing is a "
        "delivery failure; not-deployed is a property of the deployment. They degrade "
        "differently, so they are reported differently."
    ),
)
def _email_absent(delivery: Delivery) -> None:
    delivery.tables.pop("email_click")


@case(
    case_id="reader_event.missing_required_table",
    section="optional inputs",
    defect_class="missing required table",
    table="reader_event",
    mutation="omit `reader_event.parquet` entirely",
    codes=(V.MISSING_REQUIRED_TABLE,),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_FINDINGS,
)
def _event_table_missing(delivery: Delivery) -> None:
    delivery.tables.pop("reader_event")


@case(
    case_id="manifest.absent",
    section="manifest",
    defect_class="no manifest at all",
    table="-",
    mutation="omit `manifest.json`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="no manifest.json",
    note=(
        "Exit 2, not 1, and no table is checked. Without the manifest there is no timezone, "
        "week anchor, article-view definition or availability floor to check against, so a "
        "pass would be a verdict about a question nobody asked."
    ),
)
def _manifest_absent(delivery: Delivery) -> None:
    delivery.manifest = None


@case(
    case_id="manifest.missing_timezone",
    section="manifest",
    defect_class="undeclared day boundary",
    table="-",
    mutation="remove `day_boundary_timezone`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="day_boundary_timezone",
    note=(
        "One of the two definitions this contract deliberately does not decide. There is no "
        "default, because the plausible answers differ by hours and the wrong one "
        "mis-buckets every window without anything visibly breaking."
    ),
)
def _manifest_no_timezone(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest.pop("day_boundary_timezone")


@case(
    case_id="manifest.unknown_timezone",
    section="manifest",
    defect_class="day boundary declared as something that is not a zone",
    table="-",
    mutation="set `day_boundary_timezone` to `EST-ish`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="is not a known IANA timezone",
)
def _manifest_bad_timezone(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["day_boundary_timezone"] = "EST-ish"


@case(
    case_id="manifest.missing_week_anchor",
    section="manifest",
    defect_class="undeclared week anchor",
    table="-",
    mutation="remove `week_anchor`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="week_anchor",
    note=(
        "Both conventions -- the week starts on a weekday, the week ends on one -- are in "
        "live use, and they differ by up to six days."
    ),
)
def _manifest_no_anchor(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest.pop("week_anchor")


@case(
    case_id="manifest.missing_article_view",
    section="manifest",
    defect_class="undeclared article-view definition",
    table="-",
    mutation="remove `article_view`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="article_view",
    note=(
        "The other definition this contract does not decide. The contract supplies the "
        "mechanism -- a delivery event, a resolvable content id, a content type -- and the "
        "publisher supplies the editorial selection, with an id so a published number can "
        "be traced to the definition it was produced under."
    ),
)
def _manifest_no_article_view(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest.pop("article_view")


@case(
    case_id="manifest.unknown_article_view_content_type",
    section="manifest",
    defect_class="article view defined over a content type the contract has no vocabulary for",
    table="-",
    mutation="add `explainer` to `article_view.content_types`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="unknown content types",
)
def _manifest_bad_article_view(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["article_view"]["content_types"].append("explainer")


@case(
    case_id="manifest.missing_scored_population",
    section="manifest",
    defect_class="undeclared scored population",
    table="-",
    mutation="remove `scored_population`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="scored_population",
    note=(
        "Subscription state is never a model feature; it decides who is scored at all. Two "
        "deployments with different entitled-state sets produce different distributions "
        "from identical data, and the scores do not say which happened."
    ),
)
def _manifest_no_population(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest.pop("scored_population")


@case(
    case_id="manifest.empty_entitled_states",
    section="manifest",
    defect_class="scored population that names no state",
    table="-",
    mutation="set `scored_population.entitled_states` to an empty list",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="at least one subscription state",
)
def _manifest_empty_population(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["scored_population"]["entitled_states"] = []


@case(
    case_id="manifest.optional_input_available_without_floor",
    section="manifest",
    defect_class="availability floor missing on an input declared available",
    table="-",
    mutation="declare email clicks available with no `available_from`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="must also declare available_from",
)
def _manifest_no_floor(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["optional_inputs"]["email_click"] = {"status": "available"}


@case(
    case_id="manifest.exclusion_is_a_personal_identifier",
    section="manifest",
    defect_class="population exclusion that is not an opaque id",
    table="-",
    mutation="put an address-shaped exclusion in `population_exclusions`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="opaque reader ids only",
    note=(
        "Exclusion lists are the one place a personal identifier has historically leaked "
        "into a population definition, so the entries are checked rather than trusted. A "
        "deployment resolves its policy to reader ids before the manifest sees it."
    ),
)
def _manifest_personal_exclusion(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["population_exclusions"] = ["someone" + "@" + "example.test"]


@case(
    case_id="manifest.declares_a_different_contract",
    section="manifest",
    defect_class="manifest for another contract",
    table="-",
    mutation="change `contract_name`",
    codes=(),
    expected_column=None,
    expected_rows=None,
    expected_exit=V.EXIT_UNTRUSTWORTHY,
    expected_manifest_error="declares a different contract",
    note=(
        "Table names are generic enough to collide. Without this check a directory produced "
        "for something else could validate on shape alone."
    ),
)
def _manifest_other_contract(delivery: Delivery) -> None:
    assert delivery.manifest is not None
    delivery.manifest["contract_name"] = "some-other-input-contract"


# --- rendering --------------------------------------------------------------


@dataclass
class CaseOutcome:
    case: Case
    report: validate.ValidationReport
    rendered: str = field(default="")


def run_case(case_obj: Case) -> CaseOutcome:
    report = case_obj.run()
    text = report.render().replace(report.directory, DIRECTORY_PLACEHOLDER)
    return CaseOutcome(case=case_obj, report=report, rendered=text)


def render_document(outcomes: Sequence[CaseOutcome]) -> str:
    lines: list[str] = [
        "# Validator negative controls",
        "",
        "**Generated file.** Produced by `python3 tools/capture_negative_controls.py --write`",
        "and checked against a fresh render by `tests/test_negative_controls.py`, so it cannot",
        "drift from the validator's actual output. Do not edit it by hand.",
        "",
        "Each case below starts from the conformant synthetic delivery in",
        "`engagement_kernel.contract.demo`, applies exactly one mutation, and runs the real",
        "validator over the result. The conformant base passes; that is asserted in the same",
        "test file, because a suite of failing fixtures proves nothing if the passing case",
        "would fail too.",
        "",
        "The test asserts the **exact** set of finding codes each case produces, not merely",
        "that something failed. Where a case legitimately produces two codes, the case says",
        "why. Exit status is part of the assertion: `1` means the delivery was read and does",
        "not conform, `2` means the verdict could not be trusted at all.",
        "",
        f"Cases: {len(outcomes)}.",
        "",
    ]

    sections: list[str] = []
    for outcome in outcomes:
        if outcome.case.section not in sections:
            sections.append(outcome.case.section)

    for section in sections:
        lines.append(f"## {section}")
        lines.append("")
        for outcome in outcomes:
            if outcome.case.section != section:
                continue
            item = outcome.case
            lines.append(f"### `{item.case_id}`")
            lines.append("")
            lines.append(f"- **defect class**: {item.defect_class}")
            lines.append(f"- **table**: `{item.table}`")
            lines.append(f"- **mutation**: {item.mutation}")
            if item.primary_code:
                lines.append(f"- **expected code**: `{item.primary_code}`")
            if len(item.expected_codes) > 1:
                extra = sorted(item.expected_codes - {item.primary_code})
                lines.append(f"- **also reported**: {', '.join(f'`{c}`' for c in extra)}")
            if item.expected_column:
                lines.append(f"- **expected column**: `{item.expected_column}`")
            if item.expected_rows is not None:
                lines.append(f"- **expected rows**: {item.expected_rows}")
            lines.append(f"- **expected exit status**: {item.expected_exit}")
            if item.note:
                lines.append(f"- **why it matters**: {item.note}")
            lines.append("")
            lines.append("```text")
            lines.extend(outcome.rendered.split("\n"))
            lines.append(f"$ echo $?  ->  {outcome.report.exit_code}")
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_document() -> str:
    return render_document([run_case(item) for item in CASES])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every negative control and render the captured evidence."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"write {DOC_RELPATH} instead of printing to stdout",
    )
    args = parser.parse_args(argv)
    text = build_document()
    if args.write:
        (REPO_ROOT / DOC_RELPATH).write_text(text, encoding="utf-8")
        print(f"wrote {DOC_RELPATH}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
