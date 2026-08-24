"""What the lane publishes, and the record of what it deliberately does not.

Six output tables. The set is short for the same reason the intermediate set is:
a census of the system this replaces found most of its declared outputs were
built, stored, mirrored and read by nothing.

:data:`NOT_PORTED_COLUMNS` is the other half of that census, carried here in full.
It is column-level rather than table-level -- the table-level decisions live in
:data:`engagement_kernel.intermediate.tables.NOT_BUILT` -- and it is in code rather
than only in prose because "we decided not to carry this" is a fact a reader of the
code needs. Without it, the question "why is there no scroll column?" gets answered
by somebody adding one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from engagement_kernel.intermediate.tables import NOT_BUILT


@dataclass(frozen=True)
class OutputTable:
    """One table this lane writes."""

    name: str
    grain: str
    purpose: str
    #: False for a table that exists to be read by a person rather than by code.
    machine_consumed: bool = True


OUTPUTS: tuple[OutputTable, ...] = (
    OutputTable(
        name="reader_week_features",
        grain="One row per scored reader per week.",
        purpose=(
            "The atomic feature mart: the spine plus every windowed measure. The input "
            "to any further analysis, and the table that answers 'why did this reader "
            "get that label'."
        ),
    ),
    OutputTable(
        name="reader_week_cluster",
        grain="One row per scored reader per week.",
        purpose=(
            "The published label, with its confidence, its raw component id and its "
            "out-of-distribution flags. The component id is carried because labels are "
            "many-to-one and may be renamed; the id is the identity that survives."
        ),
    ),
    OutputTable(
        name="reader_week_measures",
        grain="One row per scored reader per week.",
        purpose=(
            "The three engagement measures, their within-week percentiles and the "
            "per-block sub-scores."
        ),
    ),
    OutputTable(
        name="cluster_profile",
        grain="One row per cluster.",
        purpose=(
            "Mean raw atomics and population share per cluster. The table the "
            "interpretability review reads, in the units a newsroom thinks in."
        ),
        machine_consumed=False,
    ),
    OutputTable(
        name="k_selection",
        grain="One row per candidate k.",
        purpose=(
            "Every candidate's screen statistics, its perturbation survival rate and "
            "why it failed. Written whether or not a k survived -- especially then, "
            "because a run that freezes nothing otherwise leaves nothing to diagnose."
        ),
        machine_consumed=False,
    ),
    OutputTable(
        name="gate_report",
        grain="One row per gate check.",
        purpose=(
            "Every check, its verdict, the realised value against its threshold, and "
            "what a failure blocks. Written on every run so a threshold can be set "
            "from measurements instead of guessed at twice."
        ),
        machine_consumed=False,
    ),
)

OUTPUTS_BY_NAME: dict[str, OutputTable] = {table.name: table for table in OUTPUTS}


@dataclass(frozen=True)
class NotPortedColumns:
    """Columns the upstream system carried and this lane deliberately does not."""

    source: str
    columns: str
    reason: str


#: The column-level not-porting list, from the census that produced it.
NOT_PORTED_COLUMNS: tuple[NotPortedColumns, ...] = (
    NotPortedColumns(
        source="every daily consumption table",
        columns="every scroll-percentage column",
        reason=(
            "Declared out of scope by the contract. Excluded on evidence rather than "
            "principle: where it was measured it was carried on several aggregates and "
            "read by nothing, and on app surfaces it is commonly not measurable at all, "
            "so a mixed-surface deployment would compare a real number against a "
            "hardcoded zero."
        ),
    ),
    NotPortedColumns(
        source="the daily channel table",
        columns="the anonymous browser id, and the mixed-grain person id",
        reason=(
            "The contract has exactly one reader id at one declared grain. A browser id "
            "is not a reader, and a column mixing grains makes every distinct-reader "
            "count meaningless."
        ),
    ),
    NotPortedColumns(
        source="the daily email table",
        columns=(
            "sends, the two last-activity timestamps, and the three reconciliation diagnostics"
        ),
        reason=(
            "Sends measure the publisher, not the reader, and are forbidden by name in "
            "both guards -- so the column would exist only to be rejected. The "
            "timestamps and diagnostics are artifacts of the vendor reconciliation this "
            "lane does not do."
        ),
    ),
    NotPortedColumns(
        source="the daily comment table",
        columns="the site name",
        reason=(
            "The contract carries an opaque site id and the lane sums across it. A "
            "human-readable property name is neither needed nor safe to publish."
        ),
    ),
    NotPortedColumns(
        source="the subscription history",
        columns="registration state, and the provenance source",
        reason=("Both were the same literal on every row. Constants that read like data."),
    ),
    NotPortedColumns(
        source="the content dimension",
        columns="nine of its fourteen columns",
        reason=(
            "Nine had no reader at all, and two of those nine were forbidden from ever "
            "reaching a model. The tables that would have read the author and "
            "content-type lists are themselves not built."
        ),
    ),
)


#: The one atomic column removed relative to the source lane, and why.
#:
#: Kept separate from the census because it is not a census finding -- it is a
#: decision this port made -- and a reader comparing the two systems column by
#: column will otherwise look for it.
PORT_REMOVED_DEFAULTS: tuple[str, ...] = (
    "The hardcoded vendor mailing-list identifier that restricted the email cadence "
    "signal. It was a function default in the source. A real third-party list id is "
    "deployment configuration for one publisher, it means nothing to an adopter, and "
    "it has no business in a public repository. The restriction is still available as "
    "configuration; there is no default.",
)


def census_frame() -> pd.DataFrame:
    """The whole not-ported census -- tables and columns -- as one frame."""
    rows = [
        {
            "level": "table",
            "source": entry.name,
            "dropped": f"{entry.upstream_columns} columns",
            "reason": entry.reason,
        }
        for entry in NOT_BUILT
    ]
    rows.extend(
        {
            "level": "column",
            "source": entry.source,
            "dropped": entry.columns,
            "reason": entry.reason,
        }
        for entry in NOT_PORTED_COLUMNS
    )
    return pd.DataFrame(rows)


#: Markers the rendered census sits between in ``docs/engagement-lane.md``.
#:
#: The census is rendered from the declarations above rather than retyped into the
#: document, and a test asserts the document contains the render verbatim. Retyping
#: it means the prose and the code drift, and the prose is what somebody reads before
#: deciding to rebuild one of these tables.
CENSUS_BEGIN_MARKER = "<!-- census:begin -->"
CENSUS_END_MARKER = "<!-- census:end -->"


def _cell(text: str) -> str:
    """One markdown table cell: no pipes, no newlines."""
    return " ".join(text.split()).replace("|", "\\|")


def census_markdown() -> str:
    """The not-porting census as two markdown tables, rendered from the declarations."""
    lines = [
        CENSUS_BEGIN_MARKER,
        "",
        "### Tables not built",
        "",
        "| Table | Upstream columns | Why not |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{entry.name}` | {_cell(entry.upstream_columns)} | {_cell(entry.reason)} |"
        for entry in NOT_BUILT
    )
    lines.extend(
        [
            "",
            "### Columns not carried",
            "",
            "| From | Dropped | Why |",
            "|---|---|---|",
        ]
    )
    lines.extend(
        f"| {_cell(entry.source)} | {_cell(entry.columns)} | {_cell(entry.reason)} |"
        for entry in NOT_PORTED_COLUMNS
    )
    lines.extend(["", CENSUS_END_MARKER])
    return "\n".join(lines)


def render_census_into(document: str) -> str:
    """Replace the census block in a document with a fresh render.

    Used by the test that keeps the two in step, so the fix for a stale document is
    one command rather than a hand transcription.
    """
    start = document.find(CENSUS_BEGIN_MARKER)
    end = document.find(CENSUS_END_MARKER)
    if start < 0 or end < 0:
        raise ValueError(
            f"the document carries no census block; expected {CENSUS_BEGIN_MARKER} and "
            f"{CENSUS_END_MARKER}"
        )
    return document[:start] + census_markdown() + document[end + len(CENSUS_END_MARKER) :]


def write_outputs(tables: dict[str, pd.DataFrame], directory: str | Path) -> list[Path]:
    """Write the run's tables as Parquet, refusing an undeclared table name.

    Refusing rather than accepting: a table written under a name nothing declares is
    a surface somebody will build a report on, and it will disappear the next time
    the writer is refactored.
    """
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    unknown = sorted(set(tables) - set(OUTPUTS_BY_NAME))
    if unknown:
        raise ValueError(
            f"undeclared output tables: {unknown}. Declare them in outputs.OUTPUTS or do "
            "not write them"
        )
    written: list[Path] = []
    for name, frame in tables.items():
        path = target / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        written.append(path)
    return sorted(written)
