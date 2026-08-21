"""The intermediate build, end to end and derivation by derivation.

The negative controls prove the checks fail on wrong SQL. These prove the right
SQL produces the right numbers, which is the other half: a build that failed
every check would also pass a suite that only asserted "no exception".

Where a test asserts a number, the number is worked out in the docstring from the
demo delivery's own rows, so a future change to the fixture that breaks it says
which reading it broke rather than only which assertion.
"""

from __future__ import annotations

from datetime import date

import pytest

from engagement_kernel.contract import demo, spec
from engagement_kernel.contract.manifest import parse_manifest
from engagement_kernel.intermediate import build, checks, tables
from engagement_kernel.intermediate.config import BuildConfig

DELIVERY = "examples/demo-delivery"


@pytest.fixture(scope="module")
def result() -> build.BuildResult:
    return build.build_delivery(DELIVERY)


def _rows(result: build.BuildResult, name: str) -> list[dict]:
    return result.table(name).to_pylist()


def _one(result: build.BuildResult, name: str, **match) -> dict:
    found = [row for row in _rows(result, name) if all(row[k] == v for k, v in match.items())]
    assert len(found) == 1, f"expected exactly one {name} row for {match}, got {len(found)}"
    return found[0]


# --- the build runs ---------------------------------------------------------


def test_every_table_builds_from_the_demo_delivery(result: build.BuildResult) -> None:
    assert set(result.tables) == {table.name for table in tables.OUTPUTS}
    for name, table in result.tables.items():
        assert table.num_rows > 0, f"{name} built empty, which proves nothing"


def test_the_build_is_clean_and_every_check_passed(result: build.BuildResult) -> None:
    assert result.clean
    assert result.failed_checks == ()
    assert result.check_results, "a build that ran no checks is not a build that passed"


def test_every_output_column_is_declared_and_every_declared_column_exists(
    result: build.BuildResult,
) -> None:
    """The declaration in ``tables`` is the documentation. It has to be true."""
    for name, table in result.tables.items():
        declared = tables.OUTPUTS_BY_NAME[name].column_names
        assert tuple(table.schema.names) == declared, name


def test_no_measure_arrives_as_a_decimal(result: build.BuildResult) -> None:
    """A DuckDB sum over an integer widens to decimal128 unless it is cast.

    In pandas that is a column of ``Decimal`` objects: arithmetic against a float
    raises, and the obvious fix is a silent ``astype`` somewhere downstream.
    """
    for name, table in result.tables.items():
        for field in table.schema:
            assert "decimal" not in str(field.type), f"{name}.{field.name} is {field.type}"


def test_no_output_carries_a_scroll_column(result: build.BuildResult) -> None:
    """Declared out of scope by the contract, and banned downstream by pattern."""
    for name, table in result.tables.items():
        for column in table.schema.names:
            assert "scroll" not in column.lower(), f"{name}.{column}"


def test_the_email_table_carries_no_sends_column(result: build.BuildResult) -> None:
    columns = set(result.table(tables.READER_EMAIL_DAY.name).schema.names)
    assert not {"sends", "sent_to", "sent"} & columns


#: Columns that match the contract's forbidden-model-feature list and are
#: nevertheless correct here, each with the reason it is allowed. The contract's
#: list is about what may reach a *model matrix*; these tables are upstream of
#: that, and two signals have a stated non-modelling use. Written as an
#: allowlist so each exception is a decision somebody made rather than a name
#: that happened not to be checked.
PERMITTED_FORBIDDEN_SOURCES: dict[tuple[str, str], str] = {
    (tables.SUBSCRIPTION_STATE_INTERVAL.name, "state"): (
        "state defines the spine -- which readers are fit and scored -- and is never a feature"
    ),
    (tables.SUBSCRIPTION_STATE_INTERVAL.name, "payer_type"): (
        "carried with the span for the same reason as state, and equally never a feature"
    ),
    (tables.READER_EMAIL_DAY.name, "opens"): (
        "reachability and deliverability reporting only, which is the permitted use the "
        "contract declares on the email_open table"
    ),
}


def test_every_forbidden_model_feature_source_on_an_output_is_a_declared_exception(
    result: build.BuildResult,
) -> None:
    """The contract names fields that must never reach a model matrix.

    Three columns here match those names and belong here anyway. The test is not
    that no match exists -- it is that every match is one of the three, so a new
    one has to be argued for rather than merely added.
    """
    matched = {
        (name, column)
        for name, table in result.tables.items()
        for column in table.schema.names
        for forbidden in spec.FORBIDDEN_MODEL_FEATURE_SOURCES
        if forbidden in column
    }
    assert matched == set(PERMITTED_FORBIDDEN_SOURCES)


def test_the_permitted_exceptions_are_not_stale(result: build.BuildResult) -> None:
    """An allowlist entry for a column that no longer exists is not an exception.

    It is a rule quietly covering nothing, and the next real match inherits its
    permission.
    """
    for (table_name, column), reason in PERMITTED_FORBIDDEN_SOURCES.items():
        assert table_name in result.tables, table_name
        assert column in result.table(table_name).schema.names, f"{table_name}.{column}"
        assert len(reason) > 30


# --- grains -----------------------------------------------------------------


@pytest.mark.parametrize("table", tables.OUTPUTS, ids=lambda item: item.name)
def test_every_declared_grain_holds(result: build.BuildResult, table: tables.OutputTable) -> None:
    """Asserted here as well as in the build, and not redundantly.

    The build's own check is what a consumer gets. This one is what catches a
    change that removes the build's check.
    """
    rows = _rows(result, table.name)
    keys = [tuple(row[column] for column in table.dedup_key) for row in rows]
    assert len(set(keys)) == len(keys), f"{table.name} has duplicate {table.dedup_key} values"


def test_no_dedup_key_column_is_ever_null(result: build.BuildResult) -> None:
    """A null key cannot deduplicate: null never equals null."""
    for table in tables.OUTPUTS:
        for row in _rows(result, table.name):
            for column in table.dedup_key:
                assert row[column] is not None, f"{table.name}.{column}"


# --- sessions: a maximum, not a sum -----------------------------------------


def test_channel_day_sessions_is_the_distinct_count_not_the_sum() -> None:
    """rdr-a1c7, web, 2026-02-16 is the case that separates the two.

    Three views that day: cnt-01 in ses-0001, cnt-02 in ses-0001, and cnt-02
    again in ses-0018 (evt-boundary-1, which is 2026-02-16 in the publisher's
    zone). So two distinct sessions, and per-content session counts of 1 and 2 --
    which sum to three. The right answer is 2 and the plausible wrong answer is
    3.
    """
    result = build.build_delivery(DELIVERY)
    channel = _one(
        result,
        tables.READER_CHANNEL_DAY.name,
        reader_id=demo.READER_FULL_HISTORY,
        channel="web",
        local_date=date(2026, 2, 16),
    )
    assert channel["sessions"] == 2
    assert channel["views"] == 3

    per_content = [
        row
        for row in _rows(result, tables.READER_CONTENT_DAY.name)
        if row["reader_id"] == demo.READER_FULL_HISTORY
        and row["channel"] == "web"
        and row["local_date"] == date(2026, 2, 16)
    ]
    assert sum(row["sessions"] for row in per_content) == 3, "the fixture no longer discriminates"
    assert {row["distinct_sessions_day"] for row in per_content} == {2}


# --- fractional section attribution -----------------------------------------


def test_a_two_section_view_splits_in_half(result: build.BuildResult) -> None:
    """rdr-f6b2 read cnt-02 once on 2026-02-24. cnt-02 is filed under two sections.

    So half a view to news and half to education, and the day's section views sum
    to the one view that actually happened.
    """
    rows = [
        row
        for row in _rows(result, tables.READER_SECTION_DAY.name)
        if row["reader_id"] == demo.READER_NEVER_PAID_QUIET
    ]
    assert {row["section"] for row in rows} == {"news", "education"}
    assert all(row["section_views"] == pytest.approx(0.5) for row in rows)
    assert sum(row["section_views"] for row in rows) == pytest.approx(1.0)


def test_section_views_reconcile_to_channel_views_for_every_reader_day(
    result: build.BuildResult,
) -> None:
    """The reconciliation, recomputed here from the published tables.

    The build checks it against the event layer. This checks it against the
    channel table, which is the number a consumer will actually compare against,
    and summed across channels because the section grain has no channel.
    """
    from collections import defaultdict

    by_day: dict[tuple, float] = defaultdict(float)
    for row in _rows(result, tables.READER_CHANNEL_DAY.name):
        by_day[(row["reader_id"], row["local_date"])] += row["views"]
    section_by_day: dict[tuple, float] = defaultdict(float)
    for row in _rows(result, tables.READER_SECTION_DAY.name):
        section_by_day[(row["reader_id"], row["local_date"])] += row["section_views"]
    assert set(by_day) == set(section_by_day)
    for key, views in by_day.items():
        assert section_by_day[key] == pytest.approx(views), key


# --- unresolved metadata ----------------------------------------------------


def test_all_three_shapes_of_unresolved_metadata_reach_the_sentinel(
    result: build.BuildResult,
) -> None:
    """The demo delivery carries every conformant way to say "we do not know".

    cnt-07 declares unresolved with a null section list; cnt-08 declares
    unresolved with an empty one; and cnt-99 has no row in ``content`` at all.
    Only cnt-07 and cnt-99 are read as views here, because cnt-08 is a gallery
    and this publisher's article-view definition counts articles and liveblogs --
    so the reader's cnt-08 delivery is not a view of anything.
    """
    sentinel = result.config.unresolved_section
    rows = [
        row for row in _rows(result, tables.READER_SECTION_DAY.name) if row["section"] == sentinel
    ]
    assert rows, "the fixture no longer exercises unresolved metadata at all"
    assert {row["reader_id"] for row in rows} == {demo.READER_INSTITUTIONAL}
    assert {row["local_date"] for row in rows} == {date(2026, 2, 17), date(2026, 2, 19)}
    assert all(row["section_views"] == pytest.approx(1.0) for row in rows)


def test_a_delivery_whose_content_row_is_absent_is_still_a_view(
    result: build.BuildResult,
) -> None:
    """cnt-99 has no content row, so its type cannot be confirmed.

    It is counted anyway. Dropping it would report a reader who read something
    nobody could categorise as having read nothing, which is the distinction the
    sentinel exists to keep. The decision is deliberate and stated in the docs;
    this is where it is enforced.
    """
    channel = _one(
        result,
        tables.READER_CHANNEL_DAY.name,
        reader_id=demo.READER_INSTITUTIONAL,
        channel="web",
        local_date=date(2026, 2, 17),
    )
    assert channel["views"] == 2, "one resolved view plus one whose metadata never resolved"


def test_the_sentinel_is_not_a_real_section(result: build.BuildResult) -> None:
    real_sections = set()
    for row in result.table(tables.CONTENT_DIMENSION.name).to_pylist():
        real_sections.update(row["sections"] or [])
    assert result.config.unresolved_section not in real_sections


# --- engagement time: null is not zero --------------------------------------


def test_unmeasured_attention_stays_null_and_is_never_summed_as_zero(
    result: build.BuildResult,
) -> None:
    """rdr-b2d8, web, 2026-02-19 has one view and no measurement at all.

    The upstream build coalesces unmeasured attention to 0.0. The contract says a
    null means not measured and must never be read as 0.0, so the sum is null and
    the measured-deliveries count is the honest denominator.
    """
    row = _one(
        result,
        tables.READER_CHANNEL_DAY.name,
        reader_id=demo.READER_INSTITUTIONAL,
        channel="web",
        local_date=date(2026, 2, 19),
    )
    assert row["total_time_seconds"] is None
    assert row["measured_time_deliveries"] == 0
    assert row["views"] == 1


def test_measured_deliveries_never_exceeds_views(result: build.BuildResult) -> None:
    for row in _rows(result, tables.READER_CHANNEL_DAY.name):
        assert row["measured_time_deliveries"] <= row["views"]


def test_the_time_rate_floor_comes_from_the_contract(result: build.BuildResult) -> None:
    assert result.config.engagement_time_min_deliveries == spec.ENGAGEMENT_TIME_MIN_DELIVERIES


# --- rows of zeros are never invented ---------------------------------------


def test_a_day_with_events_but_no_qualifying_view_produces_no_row(
    result: build.BuildResult,
) -> None:
    """rdr-c3e9 read a podcast and a newsletter, and this publisher counts neither.

    So the reader has no consumption rows at all. That is the intended outcome: a
    channel active day is a day with a qualifying view, and a row of zeros would
    turn a day of no reading into a day of reading nothing.
    """
    readers = {row["reader_id"] for row in _rows(result, tables.READER_CHANNEL_DAY.name)}
    assert demo.READER_REGISTERED_THEN_TRIAL not in readers


def test_views_is_at_least_one_on_every_consumption_row(result: build.BuildResult) -> None:
    for name in (tables.READER_CONTENT_DAY.name, tables.READER_CHANNEL_DAY.name):
        for row in _rows(result, name):
            assert row["views"] >= 1, f"{name} carries a zero-view row"


# --- community --------------------------------------------------------------


def test_community_counts_are_actions_given_and_cover_every_contract_kind(
    result: build.BuildResult,
) -> None:
    from engagement_kernel.contract import enums

    columns = set(result.table(tables.READER_COMMUNITY_DAY.name).schema.names)
    for kind in enums.COMMUNITY_ACTION_KINDS:
        assert tables.COMMUNITY_ACTION_COLUMNS[kind] in columns
    assert not any("received" in column for column in columns)


def test_community_sums_across_sites_rather_than_splitting_by_them(
    result: build.BuildResult,
) -> None:
    assert "site_id" not in result.table(tables.READER_COMMUNITY_DAY.name).schema.names


def test_community_counts_match_the_delivery(result: build.BuildResult) -> None:
    """rdr-a1c7 on 2026-02-16: one post, one reply, one like given, nothing else."""
    row = _one(
        result,
        tables.READER_COMMUNITY_DAY.name,
        reader_id=demo.READER_FULL_HISTORY,
        local_date=date(2026, 2, 16),
    )
    assert row["posts_created"] == 1
    assert row["replies_created"] == 1
    assert row["likes_given"] == 1
    assert row["dislikes_given"] == 0
    assert row["flags_given"] == 0


# --- subscription spans -----------------------------------------------------


def test_spans_are_carried_verbatim_with_local_dates_added(
    result: build.BuildResult,
) -> None:
    rows = [
        row
        for row in _rows(result, tables.SUBSCRIPTION_STATE_INTERVAL.name)
        if row["reader_id"] == demo.READER_FULL_HISTORY
    ]
    assert len(rows) == 5, "the renewal case is five half-open spans that meet exactly"
    assert sum(1 for row in rows if row["is_open"]) == 1
    open_span = next(row for row in rows if row["is_open"])
    assert open_span["end_ts"] is None
    assert open_span["end_date"] is None
    assert open_span["state"] == "active"


def test_the_span_table_drops_the_two_upstream_constants(
    result: build.BuildResult,
) -> None:
    columns = set(result.table(tables.SUBSCRIPTION_STATE_INTERVAL.name).schema.names)
    assert "registration_state" not in columns
    assert "source" not in columns


def test_a_reader_with_no_span_is_absent_rather_than_defaulted(
    result: build.BuildResult,
) -> None:
    """State unknown is not a state. rdr-g7c3 has activity and no subscription row."""
    readers = {row["reader_id"] for row in _rows(result, tables.SUBSCRIPTION_STATE_INTERVAL.name)}
    assert demo.READER_NO_SUBSCRIPTION_ROW not in readers


def test_payer_type_null_means_unknown_and_is_not_filled_in(
    result: build.BuildResult,
) -> None:
    rows = [
        row
        for row in _rows(result, tables.SUBSCRIPTION_STATE_INTERVAL.name)
        if row["reader_id"] == demo.READER_NEVER_PAID
    ]
    assert rows and all(row["payer_type"] is None for row in rows)


# --- optional inputs: absent, not zero --------------------------------------


def test_a_delivery_without_community_omits_the_table_rather_than_zeroing_it() -> None:
    arrow = demo.build_tables()
    del arrow["community_action"]
    manifest = parse_manifest(demo.build_manifest())
    result = build.build_from_arrow(arrow, BuildConfig.from_manifest(manifest))
    assert tables.READER_COMMUNITY_DAY.name not in result.tables
    omitted = {item.table: item for item in result.omitted}
    assert tables.READER_COMMUNITY_DAY.name in omitted
    assert omitted[tables.READER_COMMUNITY_DAY.name].missing_inputs == ("community_action",)
    assert "destabilise" in omitted[tables.READER_COMMUNITY_DAY.name].consequence


def test_a_delivery_with_clicks_and_no_opens_omits_the_opens_column() -> None:
    """The column is absent, not zero.

    Zero opens and no open feed are different facts and only one of them is about
    the reader.
    """
    arrow = demo.build_tables()
    del arrow["email_open"]
    manifest = parse_manifest(demo.build_manifest())
    result = build.build_from_arrow(arrow, BuildConfig.from_manifest(manifest))
    columns = set(result.table(tables.READER_EMAIL_DAY.name).schema.names)
    assert "clicks" in columns
    assert "opens" not in columns


def test_a_delivery_with_neither_email_input_omits_the_table() -> None:
    arrow = demo.build_tables()
    del arrow["email_open"]
    del arrow["email_click"]
    manifest = parse_manifest(demo.build_manifest())
    result = build.build_from_arrow(arrow, BuildConfig.from_manifest(manifest))
    assert tables.READER_EMAIL_DAY.name not in result.tables


def test_a_missing_required_input_stops_the_build_entirely() -> None:
    arrow = demo.build_tables()
    del arrow["content"]
    manifest = parse_manifest(demo.build_manifest())
    with pytest.raises(build.MissingRequiredInput) as exc:
        build.build_from_arrow(arrow, BuildConfig.from_manifest(manifest))
    assert "content" in str(exc.value)
    assert "Nothing was built" in str(exc.value)


# --- the not-built list is a real decision, kept in the code -----------------


def test_the_not_built_list_names_every_table_and_says_why() -> None:
    assert len(tables.NOT_BUILT) == 6
    for entry in tables.NOT_BUILT:
        assert entry.name.strip()
        assert entry.upstream_columns.strip()
        assert len(entry.reason) > 30, f"{entry.name} has no real reason recorded"


def test_no_not_built_table_is_actually_built() -> None:
    built = {table.name for table in tables.OUTPUTS}
    for entry in tables.NOT_BUILT:
        for name in entry.name.split(","):
            assert name.strip().split(" ")[0] not in built


def test_the_deduplication_layer_note_states_why_there_is_no_dedup_layer() -> None:
    note = tables.DEDUPLICATION_LAYER_NOTE
    assert "event_id" in note
    assert "pre-deduplicated" in note
    assert "no-op" in note


def test_the_contract_really_does_guarantee_pre_deduplicated_events() -> None:
    """The note above is a claim about the contract. This is the claim checked.

    If the contract ever stopped keying reader events on a non-nullable event id,
    the note would become false and the missing dedup layer would become a defect.
    """
    reader_event = spec.TABLES_BY_NAME["reader_event"]
    assert reader_event.dedup_key == ("event_id",)
    assert reader_event.field_by_name("event_id").nullable is False


# --- the report -------------------------------------------------------------


def test_the_report_names_the_article_view_definition_it_ran_under(
    result: build.BuildResult,
) -> None:
    payload = result.to_dict()
    assert payload["article_view_definition_id"] == demo.DEMO_ARTICLE_VIEW_DEFINITION_ID
    assert payload["day_boundary_timezone"] == demo.DEMO_TIMEZONE
    assert payload["feature_set_id"] == "full"
    assert payload["mutated_statements"] == []


def test_a_mutated_build_says_so_in_its_report() -> None:
    """A result produced under an override must never look like a clean one."""
    from engagement_kernel.intermediate import sql

    arrow = demo.build_tables()
    manifest = parse_manifest(demo.build_manifest())
    config = BuildConfig.from_manifest(manifest)
    statements = sql.build_statements(config, available_inputs=frozenset(arrow))
    harmless = statements[tables.CONTENT_DIMENSION.name].replace(
        "content_id,\n    content_type", "content_id,\n    content_type"
    )
    result = build.build_from_arrow(
        arrow,
        config,
        manifest=manifest,
        statement_overrides={tables.CONTENT_DIMENSION.name: harmless},
    )
    assert not result.clean
    assert result.mutated_statements == (tables.CONTENT_DIMENSION.name,)
    assert "MUTATED" in result.render()


def test_an_override_naming_an_unrun_statement_is_refused() -> None:
    arrow = demo.build_tables()
    manifest = parse_manifest(demo.build_manifest())
    with pytest.raises(KeyError) as exc:
        build.build_from_arrow(
            arrow,
            BuildConfig.from_manifest(manifest),
            statement_overrides={"user_device_day": "SELECT 1"},
        )
    assert "passes for free" in str(exc.value)


# --- the checks are wired in ------------------------------------------------


def test_every_named_check_actually_ran(result: build.BuildResult) -> None:
    """A check nobody runs is documentation.

    Named explicitly rather than counted, because a check silently dropped from
    ``run_checks`` would keep any count-based assertion passing.
    """
    ran = {item.name for item in result.check_results}
    expected = {
        checks.CHECK_SESSIONS_MAXIMISED,
        checks.CHECK_SECTION_ATTRIBUTION,
        checks.CHECK_UNRESOLVED_SENTINEL,
        checks.CHECK_SECTION_NEVER_NULL,
        checks.CHECK_DAY_BOUNDARY_EVENTS,
        checks.CHECK_DAY_BOUNDARY_EMAIL,
        checks.CHECK_DAY_BOUNDARY_COMMUNITY,
    }
    assert expected <= ran
    for table in tables.OUTPUTS:
        assert f"{checks.CHECK_DEDUP_KEYS}[{table.name}]" in ran


def test_a_check_whose_table_was_not_built_is_skipped_not_passed() -> None:
    """ "Nothing to look at" must not be reported as "we looked and it was fine"."""
    arrow = demo.build_tables()
    del arrow["community_action"]
    manifest = parse_manifest(demo.build_manifest())
    result = build.build_from_arrow(arrow, BuildConfig.from_manifest(manifest))
    ran = {item.name for item in result.check_results}
    assert checks.CHECK_DAY_BOUNDARY_COMMUNITY not in ran
    assert checks.CHECK_DAY_BOUNDARY_EMAIL in ran
