"""The DuckDB session: how it is opened, what is registered, how it is read.

Small, and worth its own module because three of the decisions in it are the
difference between a portable build and one that behaves differently on the
machine that wrote it.

**The session timezone is pinned to UTC.** DuckDB evaluates a bare
``CAST(timestamptz AS DATE)`` in the session zone, which defaults to the host's.
So the same query returns different calendar days on two laptops, and on a
laptop set to the publisher's own zone it returns the *right* answer for the
wrong reason -- the worst case, because it hides the bug until the build runs in
CI or in another office. Pinning to UTC does not make a stray cast correct; it
makes it visibly wrong.

**Results come back as Arrow, never as Python rows.** DuckDB's row API converts
a timezone-aware timestamp through ``pytz``, which is not a dependency of this
package, so a query that selects an instant raises ``ModuleNotFoundError`` on a
clean install -- from inside DuckDB, naming a module nobody imported. The Arrow
path needs nothing extra and keeps the instants as instants.

**Inputs are registered as views over Arrow tables read from Parquet**, one view
per contract table, named with a prefix. Nothing in the build reads a file path,
so the same statements run over an in-memory delivery in a test and over a
directory on disk in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from engagement_kernel.contract.reader import TypedReader
from engagement_kernel.contract.spec import TABLES, TABLES_BY_NAME
from engagement_kernel.intermediate.sql import input_view

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

#: The session zone. Not the day-boundary zone -- see the module docstring.
SESSION_TIMEZONE = "UTC"


def connect(*, connection: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    """Open an in-process connection with the session zone pinned."""
    import duckdb as _duckdb

    con = _duckdb.connect() if connection is None else connection
    con.execute(f"SET TimeZone='{SESSION_TIMEZONE}'")
    return con


def register_arrow_inputs(
    con: duckdb.DuckDBPyConnection, arrow_tables: dict[str, pa.Table]
) -> frozenset[str]:
    """Register contract tables held in memory, returning which were registered."""
    registered = set()
    for name, table in arrow_tables.items():
        if name not in TABLES_BY_NAME:
            raise KeyError(f"{name!r} is not a table in the input contract")
        con.register(input_view(name), table)
        registered.add(name)
    return frozenset(registered)


def read_delivery(directory: str | Path) -> dict[str, pa.Table]:
    """Read every contract table present in a delivery directory.

    Absent optional tables are simply not in the result. Nothing is
    substituted for them: an absent input changes which outputs exist, and a
    zero-row stand-in would instead change what the numbers mean.
    """
    reader = TypedReader(directory)
    found: dict[str, pa.Table] = {}
    for table_spec in TABLES:
        if reader.exists(table_spec):
            found[table_spec.name] = reader.read(table_spec).table
    return found


def fetch(con: duckdb.DuckDBPyConnection, statement: str) -> pa.Table:
    """Run a query and return Arrow.

    The two spellings exist because the current DuckDB names this
    ``to_arrow_table`` and deprecates the ``fetch_arrow_table`` that the
    supported floor version provides. Both are tried rather than pinning a
    newer floor for a method rename.
    """
    cursor = con.execute(statement)
    to_arrow = getattr(cursor, "to_arrow_table", None)
    if to_arrow is None:  # pragma: no cover - older DuckDB
        to_arrow = cursor.fetch_arrow_table
    return to_arrow()


def rows(con: duckdb.DuckDBPyConnection, statement: str) -> list[dict[str, Any]]:
    """Run a query and return its rows as dicts, via Arrow."""
    return fetch(con, statement).to_pylist()


def scalar(con: duckdb.DuckDBPyConnection, statement: str) -> Any:
    """Run a query expected to return exactly one row and one column."""
    table = fetch(con, statement)
    if table.num_rows != 1 or table.num_columns != 1:
        raise ValueError(
            f"expected one row and one column, got {table.num_rows}x{table.num_columns}"
        )
    return table.column(0).to_pylist()[0]
