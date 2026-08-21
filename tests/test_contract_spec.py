"""Invariants of the contract declaration itself.

These are cheap assertions about the spec, and each one guards a promise the
documentation makes to a producer. A promise that only a person can check stops
being true the first time someone adds a field in a hurry.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from engagement_kernel.contract import degradation, enums, spec
from engagement_kernel.contract.validate import _forbidden_reason

ALL_FIELDS = [(table, field) for table in spec.TABLES for field in table.fields]


def test_the_table_set_is_internally_consistent() -> None:
    assert set(spec.TABLES_BY_NAME) == {table.name for table in spec.TABLES}
    assert spec.REQUIRED_TABLES + spec.OPTIONAL_TABLES != ()
    assert set(spec.REQUIRED_TABLES).isdisjoint(spec.OPTIONAL_TABLES)
    assert len(spec.REQUIRED_TABLES) + len(spec.OPTIONAL_TABLES) == len(spec.TABLES)


def test_every_table_states_its_grain_dedup_key_and_null_behaviour() -> None:
    for table in spec.TABLES:
        assert table.purpose.strip()
        assert table.grain.strip()
        assert table.null_behaviour.strip()
        assert table.dedup_key, f"{table.name} declares no deduplication key"


def test_dedup_key_columns_exist_and_are_never_nullable() -> None:
    """A nullable key cannot deduplicate: null never equals null."""
    for table in spec.TABLES:
        for column in table.dedup_key:
            field = table.field_by_name(column)
            assert field is not None, f"{table.name} dedup key names a missing column {column}"
            assert not field.nullable, f"{table.name}.{column} is a nullable dedup key"


@pytest.mark.parametrize(("table", "field"), ALL_FIELDS, ids=lambda item: getattr(item, "name", ""))
def test_every_field_carries_a_semantic_definition(
    table: spec.TableSpec, field: spec.FieldSpec
) -> None:
    assert len(field.definition.strip()) > 20, f"{table.name}.{field.name} has no real definition"


def test_no_declared_column_is_refused_by_the_contracts_own_column_rules() -> None:
    """The forbidden-substring list must not shadow a required column.

    A declared column whose name contained `email`, `scroll` or `date` would be
    refused by the validator on arrival, so a conformant delivery could never
    pass. Nothing catches that except this test.
    """
    for table, field in ALL_FIELDS:
        reason = _forbidden_reason(field.name)
        assert reason is None, f"{table.name}.{field.name} is refused by the contract: {reason}"


def test_no_table_carries_a_calendar_date_column() -> None:
    """The whole point: instants, not pre-bucketed days."""
    for table, field in ALL_FIELDS:
        assert not field.name.endswith("_date"), f"{table.name}.{field.name} is a calendar date"
        assert not pa.types.is_date(field.arrow_type), f"{table.name}.{field.name} is a date type"


def test_every_timestamp_field_is_declared_timezone_aware() -> None:
    for table, field in ALL_FIELDS:
        if pa.types.is_timestamp(field.arrow_type):
            assert field.arrow_type.tz is not None, f"{table.name}.{field.name} is naive"


def test_every_enum_vocabulary_is_non_empty_and_has_no_repeats() -> None:
    for table, field in ALL_FIELDS:
        if field.enum is None:
            continue
        assert field.enum, f"{table.name}.{field.name} declares an empty enum"
        assert len(set(field.enum)) == len(field.enum), f"{table.name}.{field.name} repeats a value"


def test_arrow_schema_matches_the_declared_fields() -> None:
    for table in spec.TABLES:
        schema = table.arrow_schema()
        assert schema.names == list(table.field_names)
        for field in table.fields:
            assert schema.field(field.name).nullable == field.nullable
            assert schema.field(field.name).type.equals(field.arrow_type)


def test_exactly_one_reader_id_grain_is_permitted() -> None:
    """Not a style choice: every window feature counts distinct readers."""
    assert enums.READER_ID_GRAINS == (enums.GRAIN_RESOLVED_PERSON,)
    grain_field = spec.READER.field_by_name("id_grain")
    assert grain_field is not None
    assert grain_field.enum == enums.READER_ID_GRAINS


def test_every_community_action_is_performed_by_the_reader() -> None:
    """No received-side value. A received reaction measures somebody else."""
    for kind in enums.COMMUNITY_ACTION_KINDS:
        assert kind.endswith(("_created", "_given")), kind
        assert "received" not in kind


def test_email_opens_and_clicks_are_separated_structurally() -> None:
    assert spec.EMAIL_CLICK.feature_block != spec.EMAIL_OPEN.feature_block
    assert "opens" in spec.FORBIDDEN_MODEL_FEATURE_SOURCES
    assert "sends" in spec.FORBIDDEN_MODEL_FEATURE_SOURCES
    # The click unit is stated on the table, because no validator can check it.
    assert any("ONE ROW PER CLICK EVENT" in note for note in spec.EMAIL_CLICK.notes)
    assert any("PERMITTED USE" in note for note in spec.EMAIL_OPEN.notes)


def test_subscription_state_and_payer_type_may_never_be_model_features() -> None:
    assert "state" in spec.FORBIDDEN_MODEL_FEATURE_SOURCES
    assert "payer_type" in spec.FORBIDDEN_MODEL_FEATURE_SOURCES


def test_subscription_spans_are_intervals_with_a_nullable_end_and_a_payer_type() -> None:
    table = spec.SUBSCRIPTION_SPAN
    start = table.field_by_name("start_ts")
    end = table.field_by_name("end_ts")
    payer = table.field_by_name("payer_type")
    assert start is not None and not start.nullable
    assert end is not None and end.nullable
    assert payer is not None and payer.nullable and payer.enum == enums.PAYER_TYPES
    assert table.dedup_key == ("reader_id", "start_ts")


def test_engagement_time_is_nullable_and_carries_a_rate_floor() -> None:
    field = spec.READER_EVENT.field_by_name("engagement_time_seconds")
    assert field is not None
    assert field.nullable, "null means not measured, which is not measured-and-zero"
    assert field.non_negative
    assert spec.ENGAGEMENT_TIME_MIN_DELIVERIES >= 2


def test_scroll_depth_is_out_of_scope_and_refused_by_name() -> None:
    assert "OUT OF SCOPE" in spec.SCROLL_DEPTH_SCOPE_NOTE
    assert _forbidden_reason("total_scroll_pct") is not None
    assert "scroll" in (_forbidden_reason("scroll_depth_max") or "")


def test_pre_bucketed_date_columns_are_refused_with_their_own_reason() -> None:
    for name in ("event_date", "local_date"):
        reason = _forbidden_reason(name)
        assert reason is not None
        assert "day-boundary" in reason


def test_personal_data_columns_are_refused() -> None:
    for name in ("reader_email", "ip_address", "first_name", "postal_code", "birth_date"):
        assert _forbidden_reason(name) is not None, name


def test_every_optional_table_has_a_named_feature_block_that_can_be_dropped() -> None:
    """Degradation is by named block, so a block with no name cannot be dropped."""
    for table in spec.OPTIONAL_TABLES:
        assert table.feature_block in degradation.OPTIONAL_BLOCK_SUFFIXES, table.name
    for table in spec.REQUIRED_TABLES:
        assert table.feature_block not in degradation.OPTIONAL_BLOCK_SUFFIXES, table.name


def test_only_the_registry_declines_a_reader_reference() -> None:
    for table in spec.TABLES:
        if table.reader_reference_column is None:
            assert table.name in (spec.READER.name, spec.CONTENT.name)
        else:
            assert table.field_by_name(table.reader_reference_column) is not None


def test_conditional_rules_reference_columns_that_exist() -> None:
    for table in spec.TABLES:
        for rule in table.conditional_rules:
            assert table.field_by_name(rule.when_column) is not None, rule.rule_id
            assert table.field_by_name(rule.then_column) is not None, rule.rule_id
            assert rule.requirement in (
                spec.REQUIRE_NON_NULL,
                spec.REQUIRE_NON_EMPTY_LIST,
                spec.REQUIRE_NULL_OR_EMPTY_LIST,
            )


def test_types_compatible_accepts_a_parquet_round_trip_and_refuses_a_coercion() -> None:
    # A list's child field is named `item` by Arrow and often `element` after a
    # Parquet round-trip. The child name carries no meaning.
    declared = pa.list_(pa.field("item", pa.string()))
    round_tripped = pa.list_(pa.field("element", pa.string()))
    assert spec.types_compatible(declared, round_tripped)
    # Milliseconds instead of microseconds is not wrong.
    assert spec.types_compatible(pa.timestamp("us", tz="UTC"), pa.timestamp("ms", tz="UTC"))
    # A naive timestamp is the defect the contract exists to refuse.
    assert not spec.types_compatible(pa.timestamp("us", tz="UTC"), pa.timestamp("us"))
    # An integer for a float, or a string for an instant, is a coercion.
    assert not spec.types_compatible(pa.float64(), pa.int64())
    assert not spec.types_compatible(pa.timestamp("us", tz="UTC"), pa.string())
    assert not spec.types_compatible(pa.list_(pa.string()), pa.string())


def test_contract_is_named_and_versioned() -> None:
    assert spec.CONTRACT_NAME
    assert spec.CONTRACT_VERSION.count(".") == 2
