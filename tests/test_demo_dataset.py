"""The demo delivery is a claim about itself. This is where the claim is checked.

``demo.py`` states, in prose and in data, what its rows are for: which instants
fall on which calendar day in which zone, which content is unresolved and in which
of the three permitted shapes, which reader has no subscription row. Every one of
those is an assertion a reader of the file will believe. So each is recomputed
here from the data rather than trusted.

The delivery also has to pass the contract's own validator. It is the worked
example a producer copies, and a non-conforming example teaches the wrong thing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engagement_kernel.contract import demo, enums, spec, validate
from engagement_kernel.contract.manifest import MANIFEST_FILENAME, parse_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = REPO_ROOT / "examples" / "demo-delivery"


def test_the_delivery_passes_the_contracts_own_validator() -> None:
    report = validate.validate_directory(COMMITTED)
    assert report.exit_code == 0, report.render()


def test_the_committed_files_match_what_the_generator_produces(tmp_path: Path) -> None:
    """The delivery is committed *and* generated, so the two can drift.

    A committed fixture nobody can regenerate is a fixture nobody dares change;
    one that has drifted from its generator is worse, because the file and the
    code that documents it disagree.
    """
    demo.write_delivery(tmp_path)
    import pyarrow.parquet as pq

    for table in spec.TABLES:
        fresh = pq.read_table(tmp_path / table.filename)
        committed = pq.read_table(COMMITTED / table.filename)
        assert fresh.equals(committed), f"{table.filename} has drifted from demo.py"
    assert json.loads((tmp_path / MANIFEST_FILENAME).read_text()) == json.loads(
        (COMMITTED / MANIFEST_FILENAME).read_text()
    )


@pytest.mark.parametrize(
    "example", demo.DAY_BOUNDARY_EVENTS, ids=lambda item: f"{item.table}:{item.event_id}"
)
def test_every_day_boundary_claim_is_recomputed_from_the_instant(
    example: demo.DayBoundaryExample,
) -> None:
    instant = datetime.fromisoformat(example.instant).replace(tzinfo=ZoneInfo("UTC"))
    for zone, claimed in example.local_dates.items():
        actual = instant.astimezone(ZoneInfo(zone)).date().isoformat()
        assert actual == claimed, f"{example.event_id} in {zone}: claims {claimed}, is {actual}"


@pytest.mark.parametrize(
    "example", demo.DAY_BOUNDARY_EVENTS, ids=lambda item: f"{item.table}:{item.event_id}"
)
def test_every_day_boundary_example_is_actually_in_the_delivery(
    example: demo.DayBoundaryExample,
) -> None:
    """A worked example naming a row that does not exist teaches a lie."""
    tables = demo.build_tables()
    assert example.table in tables, example.table
    assert example.event_id in tables[example.table].column("event_id").to_pylist()


@pytest.mark.parametrize(
    "example", demo.DAY_BOUNDARY_EVENTS, ids=lambda item: f"{item.table}:{item.event_id}"
)
def test_every_day_boundary_example_says_why_it_is_there(
    example: demo.DayBoundaryExample,
) -> None:
    assert len(example.why) > 40, f"{example.event_id} does not say what it is for"


def test_the_boundary_examples_cover_every_instant_bearing_channel() -> None:
    """Reader events alone would let the exact upstream defect through.

    That system converts web and app and leaves email and community in the
    vendor's zone, so a dataset with a near-midnight row on reader events only
    cannot distinguish a build that converts everything from one that converts
    the channel somebody happened to be looking at.
    """
    covered = {example.table for example in demo.DAY_BOUNDARY_EVENTS}
    assert {"reader_event", "email_open", "community_action"} <= covered


def test_at_least_one_example_changes_week_as_well_as_day() -> None:
    """The failure that survives every plausibility check downstream."""
    changes_week = []
    for example in demo.DAY_BOUNDARY_EVENTS:
        instant = datetime.fromisoformat(example.instant).replace(tzinfo=ZoneInfo("UTC"))
        local = instant.astimezone(ZoneInfo(demo.DEMO_TIMEZONE))
        utc = instant.astimezone(ZoneInfo("UTC"))
        if local.isocalendar()[:2] != utc.isocalendar()[:2] or (
            local.isoweekday() == 7 and utc.isoweekday() == 1
        ):
            changes_week.append(example.event_id)
    assert changes_week


def test_one_example_is_a_control_that_does_not_move() -> None:
    """Otherwise the moving examples could be an artefact of the harness."""
    controls = [
        example
        for example in demo.DAY_BOUNDARY_EVENTS
        if len(set(example.local_dates.values())) == 1
    ]
    assert controls, "no non-moving control among the day-boundary examples"


def test_all_three_shapes_of_unresolved_content_are_present() -> None:
    """A null list, an empty list, and no row at all. All conformant."""
    content = demo.build_tables()["content"].to_pylist()
    unresolved = [
        row for row in content if row["section_resolution"] == enums.SECTION_RESOLUTION_UNRESOLVED
    ]
    assert any(row["sections"] is None for row in unresolved)
    assert any(row["sections"] == [] for row in unresolved)

    known = {row["content_id"] for row in content}
    referenced = {
        row["content_id"]
        for row in demo.build_tables()["reader_event"].to_pylist()
        if row["content_id"] is not None
    }
    assert referenced - known == {demo.CONTENT_WITH_NO_METADATA_ROW}


def test_multi_section_content_exists_so_fractional_attribution_is_visible() -> None:
    content = demo.build_tables()["content"].to_pylist()
    assert any(row["sections"] and len(row["sections"]) > 1 for row in content)


def test_null_engagement_time_is_present_and_is_not_zero() -> None:
    """Null means not measured. A fixture with only zeros could not show that."""
    events = demo.build_tables()["reader_event"].to_pylist()
    assert any(row["engagement_time_seconds"] is None for row in events)
    assert any(row["engagement_time_seconds"] == 0.0 for row in events), (
        "a measured zero is a different fact from an unmeasured null, and the delivery "
        "carries both so a consumer that conflates them can be caught"
    )


def test_one_reader_has_no_subscription_row_at_all() -> None:
    """A third case, distinct from never-paid: state unknown."""
    spans = {row["reader_id"] for row in demo.build_tables()["subscription_span"].to_pylist()}
    readers = {row["reader_id"] for row in demo.build_tables()["reader"].to_pylist()}
    assert demo.READER_NO_SUBSCRIPTION_ROW in readers
    assert demo.READER_NO_SUBSCRIPTION_ROW not in spans


def test_the_renewal_spans_meet_exactly_without_overlapping() -> None:
    """A reader whose second paid span is read as their first has wrong tenure."""
    spans = [
        row
        for row in demo.build_tables()["subscription_span"].to_pylist()
        if row["reader_id"] == demo.READER_FULL_HISTORY
    ]
    spans.sort(key=lambda row: row["start_ts"])
    assert len(spans) == 5
    for earlier, later in zip(spans, spans[1:], strict=False):
        assert earlier["end_ts"] == later["start_ts"], "half-open spans must meet exactly"
    assert spans[-1]["end_ts"] is None
    assert sum(1 for row in spans if row["end_ts"] is None) == 1


def test_the_manifest_declares_everything_the_contract_has_no_default_for() -> None:
    manifest = parse_manifest(demo.build_manifest())
    assert manifest.day_boundary_timezone == demo.DEMO_TIMEZONE
    assert manifest.article_view.definition_id
    assert manifest.article_view.content_types
    assert manifest.scored_population.entitled_states
    assert set(manifest.optional_inputs) == {table.name for table in spec.OPTIONAL_TABLES}


def test_the_population_exclusion_is_an_opaque_id_and_names_a_real_reader() -> None:
    manifest = parse_manifest(demo.build_manifest())
    readers = {row["reader_id"] for row in demo.build_tables()["reader"].to_pylist()}
    assert manifest.population_exclusions
    for entry in manifest.population_exclusions:
        assert "@" not in entry
        assert entry in readers, "an exclusion naming nobody excludes nobody"
