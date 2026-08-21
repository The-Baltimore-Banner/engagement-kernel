#!/usr/bin/env python3
"""Prove the package imports, and the build runs, with every cloud SDK blocked.

The portability promise of this repository is that the reference engine runs on
columnar files with no vendor SDK, no warehouse driver and no credentials.
``tests/test_packaging.py`` asserts the *declared* dependencies stay clean, which
is necessary and not sufficient: a module can import a vendor library that
happens to be installed for some other reason, and every check still passes on
the machine where it is installed.

So this script does the opposite. It installs an import hook that refuses every
blocked top-level module, then:

1. imports every module in the package, via ``pkgutil.walk_packages`` rather than
   a hand-written list, because a hand-written list is missing the module
   somebody added last week;
2. resolves every declared console script to its function, because an entry point
   that cannot be imported is a broken command that no import of the library
   would reveal;
3. runs the whole intermediate build over the committed demo delivery and
   requires every check to pass.

The third step is the one that makes this more than an import test. A build that
imports cleanly and then needs a network call at query time is not portable, and
the failure would surface as a timeout in somebody else's environment.

Run it directly. It prints what it did and exits non-zero with a named reason.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

PACKAGE = "engagement_kernel"
DELIVERY = REPO_ROOT / "examples" / "demo-delivery"

#: Top-level modules that must not be importable from anywhere in the package.
#: Vendor SDKs, warehouse drivers and hosted query clients. Blocked by *name*
#: rather than by absence, so the check is the same whether or not they happen to
#: be installed on the machine running it.
BLOCKED: frozenset[str] = frozenset(
    {
        "awswrangler",
        "azure",
        "boto3",
        "botocore",
        "databricks",
        "google",
        "pyathena",
        "redshift_connector",
        "s3fs",
        "snowflake",
        "sqlalchemy",
    }
)

#: Bridge between the two vendor guards, because they speak different languages.
#:
#: ``tests/test_packaging.py`` refuses vendor substrings in *distribution* names
#: declared as dependencies. This module refuses *import* roots. They are not the
#: same strings -- BigQuery ships as ``google-cloud-bigquery`` and imports as
#: ``google.cloud.bigquery`` -- so a test comparing the two lists directly would
#: pass or fail on a coincidence. Declaring the mapping means a marker added to
#: one guard and not the other is caught by name.
PACKAGING_MARKER_IMPORTS: dict[str, tuple[str, ...]] = {
    "athena": ("pyathena",),
    "aws": ("boto3", "botocore", "awswrangler"),
    "azure": ("azure",),
    "bigquery": ("google",),
    "boto": ("boto3", "botocore"),
    "databricks": ("databricks",),
    "google-cloud": ("google",),
    "redshift": ("redshift_connector",),
    "s3fs": ("s3fs",),
    "snowflake": ("snowflake",),
}

EXIT_OK = 0
EXIT_VIOLATION = 1


class BlockedImport(ImportError):
    """A blocked module was imported. Named so the traceback says what happened."""


class _Blocker:
    """A meta path finder that refuses the blocked names.

    First on ``sys.meta_path``, so it sees the request before anything can
    satisfy it -- including a module already installed in the environment.
    """

    def find_module(self, fullname: str, path: object = None):  # noqa: D102 - legacy API
        return self.find_spec(fullname, path)

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        root = fullname.split(".", 1)[0]
        if root in BLOCKED:
            raise BlockedImport(
                f"{fullname!r} was imported, and {root!r} is a vendor dependency this package "
                "must run without. The reference engine has to work on columnar files with no "
                "cloud SDK; move the import into an optional adapter"
            )
        return None


def _console_scripts() -> dict[str, str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"].get("scripts", {})


def _import_every_module() -> list[str]:
    package = importlib.import_module(PACKAGE)
    imported = [PACKAGE]
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{PACKAGE}."):
        importlib.import_module(info.name)
        imported.append(info.name)
    return imported


def _resolve_console_scripts() -> list[str]:
    resolved = []
    for name, target in _console_scripts().items():
        module_name, _, attribute = target.partition(":")
        module = importlib.import_module(module_name)
        if not callable(getattr(module, attribute, None)):
            raise BlockedImport(
                f"console script {name!r} points at {target!r}, which is not callable. "
                "The command is installed and broken, and nothing but this would notice"
            )
        resolved.append(f"{name} -> {target}")
    return resolved


def _run_the_build() -> str:
    from engagement_kernel.intermediate import build_delivery

    result = build_delivery(DELIVERY)
    failed = result.failed_checks
    if failed:
        raise BlockedImport(
            f"the build ran with no cloud SDK but failed checks: {', '.join(failed)}"
        )
    if not result.tables:
        raise BlockedImport("the build produced no tables, so nothing was actually exercised")
    return ", ".join(f"{name}={table.num_rows}" for name, table in result.tables.items())


def main() -> int:
    sys.meta_path.insert(0, _Blocker())
    try:
        modules = _import_every_module()
        scripts = _resolve_console_scripts()
        tables = _run_the_build()
    except BlockedImport as exc:
        print(f"IMPORT CLOSURE FAILED: {exc}", file=sys.stderr)
        return EXIT_VIOLATION
    except Exception as exc:  # noqa: BLE001 - any failure here is a real failure
        print(f"IMPORT CLOSURE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VIOLATION

    print(f"blocked modules      : {', '.join(sorted(BLOCKED))}")
    print(f"modules imported     : {len(modules)}")
    for name in modules:
        print(f"  {name}")
    print(f"console scripts      : {len(scripts)}")
    for entry in scripts:
        print(f"  {entry}")
    print(f"demo build           : {tables}")
    print("import closure holds: no cloud SDK was reachable and the build still ran")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
