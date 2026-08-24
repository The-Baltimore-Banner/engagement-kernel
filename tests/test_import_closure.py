"""The import-closure check, and proof that its blocker actually blocks.

``tools/import_closure_check.py`` reports that no cloud SDK was reachable. On a
machine where none is installed, a blocker that did nothing at all would report
exactly the same thing -- so the check needs a positive control, and this is it.

The check itself runs as its own CI job rather than as a test, deliberately: it
has to run against an install of the *core* dependencies only, and the test job
installs the dev extra.
"""

from __future__ import annotations

import import_closure_check as closure
import pytest


def test_the_blocker_refuses_a_blocked_module() -> None:
    """The positive control. Without it, a no-op blocker passes silently."""
    with pytest.raises(closure.BlockedImport) as exc:
        closure._Blocker().find_spec("boto3")
    assert "boto3" in str(exc.value)
    assert "optional adapter" in str(exc.value)


def test_the_blocker_refuses_a_submodule_of_a_blocked_module() -> None:
    """Blocking the root name is not enough if submodules slip through."""
    with pytest.raises(closure.BlockedImport):
        closure._Blocker().find_spec("google.cloud.bigquery")


def test_the_blocker_lets_everything_else_through() -> None:
    """A blocker that refused everything would also pass the tests above."""
    assert closure._Blocker().find_spec("pyarrow") is None
    assert closure._Blocker().find_spec("duckdb") is None
    assert closure._Blocker().find_spec("engagement_kernel.intermediate") is None


def test_every_packaging_vendor_marker_maps_to_a_blocked_import_root() -> None:
    """The two guards must not drift apart.

    ``test_packaging`` refuses vendor substrings in declared *distribution*
    names; this module refuses *import* roots. The two are different strings --
    BigQuery ships as ``google-cloud-bigquery`` and imports as
    ``google.cloud.bigquery`` -- so the mapping is declared rather than inferred,
    and this asserts it is complete in both directions.
    """
    from test_packaging import VENDOR_MARKERS

    unmapped = [m for m in VENDOR_MARKERS if m not in closure.PACKAGING_MARKER_IMPORTS]
    assert unmapped == [], f"refused at packaging time with no import root declared: {unmapped}"

    stale = [m for m in closure.PACKAGING_MARKER_IMPORTS if m not in VENDOR_MARKERS]
    assert stale == [], f"mapped from a marker the packaging guard no longer names: {stale}"

    for marker, roots in closure.PACKAGING_MARKER_IMPORTS.items():
        for root in roots:
            assert root in closure.BLOCKED, f"{marker} maps to {root}, which is not blocked"


def test_the_check_passes_on_this_repository() -> None:
    """The whole thing, end to end. Slower than the rest and worth it."""
    assert closure.main() == closure.EXIT_OK


def test_every_declared_console_script_resolves() -> None:
    """An entry point that cannot be imported is an installed, broken command."""
    scripts = closure._console_scripts()
    assert set(scripts) == {
        "engagement-kernel-validate",
        "engagement-kernel-demo-dataset",
        "engagement-kernel-build-intermediate",
        "engagement-kernel-engagement-lane",
        "engagement-kernel-cohort",
    }
    assert closure._resolve_console_scripts()
