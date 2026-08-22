"""The recorded editorial declarations are a claim in two places. Keep them one.

``docs/publisher-declarations.md`` states one publisher's answers to the four
declarations the contract refuses to default, and
``examples/publisher-declarations/baltimore-banner.json`` carries the same values
as data. A document and a data file saying the same thing in two places is a
drift waiting to happen: the usual outcome is that somebody edits the file, the
prose keeps the old value, and a reader believes whichever they happened to open.

So the values are asserted in three directions here:

* the JSON is **valid against the contract** -- not merely well-formed. A
  declaration naming a timezone that does not exist, a weekday outside the
  vocabulary or a content type the contract has never heard of is refused by the
  same code paths a real manifest goes through.
* the document's table **equals** the JSON, key by key. The table is delimited by
  HTML comment markers so this test reads exactly the rows it means to and cannot
  quietly match some other table that happens to look similar.
* the deployment's article-view selection **differs** from the synthetic demo
  delivery's. That is not a style preference. The demo exists so a consumer that
  reads the selection from the wrong place is visibly wrong; if the two are ever
  made identical, that property is gone and no other test would notice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from engagement_kernel.contract import enums
from engagement_kernel.contract.manifest import ArticleViewDefinition, WeekAnchor

REPO_ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = REPO_ROOT / "examples" / "publisher-declarations" / "baltimore-banner.json"
DOC = REPO_ROOT / "docs" / "publisher-declarations.md"
DEMO_MANIFEST = REPO_ROOT / "examples" / "demo-delivery" / "manifest.json"

TABLE_START = "<!-- declarations-table:start -->"
TABLE_END = "<!-- declarations-table:end -->"


@pytest.fixture(scope="module")
def declared() -> dict:
    return json.loads(DECLARATIONS.read_text(encoding="utf-8"))


# --- the file is valid against the contract, not merely parseable -------------


def test_the_declared_timezone_is_a_real_iana_zone(declared: dict) -> None:
    name = declared["day_boundary_timezone"]
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - the assertion is the point
        pytest.fail(f"day_boundary_timezone {name!r} is not a known IANA timezone")


def test_the_declared_week_anchor_is_accepted_by_the_contract(declared: dict) -> None:
    anchor = WeekAnchor(**declared["week_anchor"])
    assert anchor.weekday in enums.WEEKDAYS
    assert anchor.position in enums.WEEK_ANCHOR_POSITIONS


def test_the_declared_article_view_is_accepted_by_the_contract(declared: dict) -> None:
    raw = declared["article_view"]
    view = ArticleViewDefinition(
        definition_id=raw["definition_id"],
        content_types=tuple(raw["content_types"]),
        event_kinds=tuple(raw["event_kinds"]),
    )
    assert view.definition_id
    assert set(view.content_types) <= set(enums.CONTENT_TYPES)
    assert set(view.event_kinds) <= set(enums.READER_EVENT_KINDS)


def test_the_declaration_file_carries_only_contract_shaped_keys(declared: dict) -> None:
    """Anything not prefixed with an underscore must splice into a manifest."""
    manifest_keys = {"day_boundary_timezone", "week_anchor", "article_view"}
    provenance_keys = {"declaration_set_id", "decided_on"}
    unexpected = {
        key
        for key in declared
        if not key.startswith("_") and key not in manifest_keys | provenance_keys
    }
    assert not unexpected, (
        f"keys that are neither contract-shaped nor provenance: {sorted(unexpected)}. "
        "The file's whole purpose is that its public keys splice into a manifest "
        "unchanged, so a new key needs a leading underscore or a manifest home."
    )


# --- the document agrees with the file ----------------------------------------


def _doc_table() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(TABLE_START) + len(TABLE_START)
    end = text.index(TABLE_END)
    rows: dict[str, str] = {}
    for line in text[start:end].splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in ("declaration", "") or set(cells[0]) <= {"-"}:
            continue
        key = cells[0].strip("`")
        rows[key] = cells[1]
    return rows


def _backticked(value: str) -> str:
    return f"`{value}`"


def test_the_document_table_is_present_and_not_empty() -> None:
    """A vacuous table would make every comparison below pass for free."""
    rows = _doc_table()
    assert len(rows) == 6, f"expected six declaration rows, read {len(rows)}: {sorted(rows)}"


def test_the_document_table_matches_the_declaration_file(declared: dict) -> None:
    rows = _doc_table()
    expected = {
        "day_boundary_timezone": _backticked(declared["day_boundary_timezone"]),
        "week_anchor.weekday": _backticked(declared["week_anchor"]["weekday"]),
        "week_anchor.position": _backticked(declared["week_anchor"]["position"]),
        "article_view.definition_id": _backticked(declared["article_view"]["definition_id"]),
        "article_view.content_types": ", ".join(
            _backticked(value) for value in declared["article_view"]["content_types"]
        ),
        "article_view.event_kinds": ", ".join(
            _backticked(value) for value in declared["article_view"]["event_kinds"]
        ),
    }
    assert rows == expected


def test_the_documents_own_links_resolve() -> None:
    """A record nobody can follow is the failure mode this document exists to fix."""
    text = DOC.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:)([^)#]+)", text):
        resolved = (DOC.parent / target).resolve()
        assert resolved.exists(), f"{DOC.name} links to a path that does not exist: {target}"


# --- the demo stays a discriminator ------------------------------------------


def test_the_demo_delivery_declares_a_different_article_view(declared: dict) -> None:
    demo_view = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))["article_view"]
    deployment_view = declared["article_view"]
    assert demo_view["definition_id"] != deployment_view["definition_id"]
    assert set(demo_view["content_types"]) != set(deployment_view["content_types"]), (
        "The synthetic demo delivery and this deployment must not declare the same "
        "article-view selection. The difference is what makes a consumer that reads "
        "the selection from the demo instead of from its own manifest visibly wrong."
    )
