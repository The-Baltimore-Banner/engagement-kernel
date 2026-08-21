"""One typed reader for the whole contract.

Every table is read the same way, by the same code, with types taken from the
contract rather than inferred from the file or coerced on the way in. That is a
deliberate design choice with a specific failure in view.

The obvious shortcut is a reader that guesses: read the file, then coerce every
column that is not on a small allowlist of "id-ish" names to a number. It works
for aggregate tables, whose columns really are all measures, and it quietly
destroys everything else -- a state label becomes NaN, a date becomes NaN, and a
table of intervals becomes unreadable through the very reader that is supposed
to read the whole contract. The workaround is then a second reader for the
awkward table, and now two readers disagree about what a column is.

So this reader coerces nothing. It reads the file as Arrow, reports what it
found, and lets the validator compare that against the declared schema. A file
whose types do not match is refused with a message naming the column, not
patched into something loadable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from engagement_kernel.contract.spec import TableSpec


class TableReadError(RuntimeError):
    """The file exists but could not be read as Parquet at all."""


@dataclass(frozen=True)
class TableRead:
    """What a read produced: the table, its path, and its row count."""

    spec: TableSpec
    path: Path
    table: pa.Table

    @property
    def n_rows(self) -> int:
        return self.table.num_rows

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(self.table.schema.names)

    def column(self, name: str) -> pa.ChunkedArray:
        return self.table.column(name)

    def arrow_type(self, name: str) -> pa.DataType:
        return self.table.schema.field(name).type


class TypedReader:
    """Reads contract tables from a directory of Parquet files.

    ``strict_types`` is not an option here on purpose. There is no lenient mode
    to fall into, because a lenient mode is where the coercion goes.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path_for(self, spec: TableSpec) -> Path:
        return self.directory / spec.filename

    def exists(self, spec: TableSpec) -> bool:
        return self.path_for(spec).exists()

    def read(self, spec: TableSpec) -> TableRead:
        """Read one table verbatim. Raises :class:`TableReadError` if unreadable."""
        path = self.path_for(spec)
        try:
            table = pq.read_table(path)
        except FileNotFoundError as exc:
            raise TableReadError(f"{path} does not exist") from exc
        except (OSError, pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise TableReadError(f"{path} could not be read as Parquet: {exc}") from exc
        return TableRead(spec=spec, path=path, table=table)
