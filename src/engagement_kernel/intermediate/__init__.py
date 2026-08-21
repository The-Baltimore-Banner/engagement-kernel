"""The daily intermediate tables, built from the canonical contract by DuckDB.

This package turns a conforming delivery into the seven daily tables the
modelling lanes read. It runs in one in-process DuckDB session over columnar
files, with no warehouse, no credentials and no network.

Read in this order:

:mod:`~engagement_kernel.intermediate.tables`
    What is built, at what grain, with which deduplication key -- and
    :data:`~engagement_kernel.intermediate.tables.NOT_BUILT`, the list of tables
    the upstream system produces that this one deliberately does not, each with
    its reason.
:mod:`~engagement_kernel.intermediate.config`
    The three things the build must be told and will not guess: the timezone
    that defines a day, what an article view means, and the sentinel for
    unresolved section metadata.
:mod:`~engagement_kernel.intermediate.sql`
    The statements, and the four derivations where the obvious rewrite is wrong.
:mod:`~engagement_kernel.intermediate.checks`
    What the build asserts about its own output, on every run rather than in a
    test helper.
:mod:`~engagement_kernel.intermediate.build`
    The runner and the build report.
"""

from engagement_kernel.intermediate.build import (
    BuildResult,
    MissingRequiredInput,
    build_delivery,
    build_from_arrow,
    write_result,
)
from engagement_kernel.intermediate.checks import CheckResult, IntermediateCheckError
from engagement_kernel.intermediate.config import BuildConfig, BuildConfigError
from engagement_kernel.intermediate.tables import (
    DEDUPLICATION_LAYER_NOTE,
    NOT_BUILT,
    OUTPUTS,
    OUTPUTS_BY_NAME,
    PUBLISHED_OUTPUTS,
)

__all__ = [
    "DEDUPLICATION_LAYER_NOTE",
    "NOT_BUILT",
    "OUTPUTS",
    "OUTPUTS_BY_NAME",
    "PUBLISHED_OUTPUTS",
    "BuildConfig",
    "BuildConfigError",
    "BuildResult",
    "CheckResult",
    "IntermediateCheckError",
    "MissingRequiredInput",
    "build_delivery",
    "build_from_arrow",
    "write_result",
]
