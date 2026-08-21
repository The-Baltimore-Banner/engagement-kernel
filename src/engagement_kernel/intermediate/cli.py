"""Command line entry point for the intermediate build.

Prints the build report -- configuration, tables, what was not built and why, and
every check with its verdict -- and optionally writes the tables as Parquet. The
report is printed even on success, because "the build passed" is not the useful
output: which feature set it had, and which checks actually ran, is.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from engagement_kernel.contract.manifest import ManifestError
from engagement_kernel.intermediate.build import (
    MissingRequiredInput,
    build_delivery,
    write_result,
)
from engagement_kernel.intermediate.checks import IntermediateCheckError
from engagement_kernel.intermediate.config import BuildConfigError

#: The build produced tables and every check passed.
EXIT_OK = 0
#: The build ran and a check failed: the output is wrong in a stated way.
EXIT_CHECK_FAILED = 1
#: The build could not run at all -- no manifest, a missing required input, or a
#: configuration it refused to guess. Kept distinct from a check failure,
#: because "we could not look" and "we looked and it was wrong" are different
#: answers and a caller that conflates them treats the first as a pass.
EXIT_CANNOT_RUN = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-build-intermediate",
        description=(
            "Build the daily intermediate tables from a conforming delivery, in one "
            "in-process DuckDB session."
        ),
    )
    parser.add_argument("directory", help="Delivery directory: the Parquet files and manifest.")
    parser.add_argument(
        "--out",
        help="Write the built tables and the build report here. Omit to print the report only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the build report as JSON instead of text.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the statements this build would run, and exit without running them.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the build and return the exit status.

    Returns rather than exits, so the tests exercise this exact code path.
    """
    args = build_parser().parse_args(argv)

    if args.print_sql:
        return _print_sql(args.directory)

    try:
        result = build_delivery(args.directory)
    except IntermediateCheckError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CHECK_FAILED
    except (ManifestError, MissingRequiredInput, BuildConfigError, FileNotFoundError) as exc:
        print(f"cannot build: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if args.json:
        import json

        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(result.render())
    if args.out:
        for path in write_result(result, args.out):
            print(f"wrote {path}")
    return EXIT_OK


def _print_sql(directory: str) -> int:
    from engagement_kernel.contract.manifest import load_manifest
    from engagement_kernel.intermediate import session, sql
    from engagement_kernel.intermediate.config import BuildConfig

    try:
        manifest = load_manifest(directory)
        config = BuildConfig.from_manifest(manifest)
        available = frozenset(session.read_delivery(directory))
    except (ManifestError, BuildConfigError, FileNotFoundError) as exc:
        print(f"cannot build: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    for name, statement in sql.build_statements(config, available_inputs=available).items():
        print(f"-- {name}")
        print(statement.strip())
        print()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
