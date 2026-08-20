"""Packaging invariants.

The portability promise of this repository is a packaging property, so it is
asserted rather than described: installing the kernel must not pull a cloud SDK,
a warehouse driver, or a hosted query client. Vendor adapters are optional
extras. If someone adds a convenient import and a matching core dependency, this
test fails before the promise quietly stops being true.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

EXPECTED_CORE = {"duckdb", "pandas", "pyarrow", "scikit-learn"}
EXPECTED_PACKAGES = ("contract", "intermediate", "engagement", "content")

# Substrings that mean "this dependency ties us to one vendor or one cloud".
VENDOR_MARKERS = (
    "boto",
    "aws",
    "athena",
    "s3fs",
    "redshift",
    "snowflake",
    "bigquery",
    "google-cloud",
    "azure",
    "databricks",
)


def _load() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\[<>=!~;\s]", requirement, maxsplit=1)[0].strip().lower()


def test_core_dependencies_are_exactly_the_four_pinned_libraries() -> None:
    project = _load()["project"]
    assert {_requirement_name(item) for item in project["dependencies"]} == EXPECTED_CORE


def test_core_dependencies_carry_a_version_floor() -> None:
    project = _load()["project"]
    for requirement in project["dependencies"]:
        assert re.search(r"[<>=~]=", requirement), f"unpinned core dependency: {requirement}"


def test_no_vendor_dependency_in_core_packaging() -> None:
    project = _load()["project"]
    for requirement in project["dependencies"]:
        name = _requirement_name(requirement)
        offenders = [marker for marker in VENDOR_MARKERS if marker in name]
        assert not offenders, f"{name} is vendor-specific and must be an optional extra"


def test_vendor_adapters_live_in_optional_extras() -> None:
    extras = _load()["project"]["optional-dependencies"]
    adapter_extras = [name for name in extras if name.startswith("adapters")]
    assert adapter_extras, "expected at least one adapters-* extra"
    assert "dev" in extras


def test_requires_python_matches_the_ruff_target() -> None:
    data = _load()
    assert data["project"]["requires-python"] == ">=3.11"
    assert data["tool"]["ruff"]["target-version"] == "py311"


def test_the_four_packages_exist_and_import() -> None:
    for name in EXPECTED_PACKAGES:
        module = importlib.import_module(f"engagement_kernel.{name}")
        assert module.__doc__, f"engagement_kernel.{name} should say what it is for"


def test_ruff_and_pytest_are_configured() -> None:
    tool = _load()["tool"]
    assert tool["ruff"]["line-length"] > 0
    assert tool["ruff"]["lint"]["select"]
    assert tool["pytest"]["ini_options"]["testpaths"] == ["tests"]
