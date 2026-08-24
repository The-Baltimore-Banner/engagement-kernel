"""The mapping lint's job is to make one artifact impossible to fake cheaply.

Its checks are structural, so the only way to know they are worth anything is to
mutate a passing mapping into each of the shapes a confident, wrong agent actually
produces and require the lint to refuse it. Every test below starts from the
committed worked example, changes exactly one thing, and asserts on the code --
not merely that something failed, because a structural lint on a nested document
fails for adjacent reasons very easily, and "it failed" would pass even if the
check under test had been deleted.

The ordering matters in one place: the worked example must pass first. A suite of
negative controls over a mapping that never passed would be measuring nothing --
every mutation would "fail" because the baseline already did.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from engagement_kernel.contract import mapping, spec

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "mapping"
EXAMPLE = EXAMPLE_DIR / mapping.MAPPING_FILENAME
BUNDLE = EXAMPLE_DIR / "adapter"


@pytest.fixture(scope="module")
def worked() -> dict:
    return mapping.load_mapping_document(EXAMPLE)


def _codes(document: dict, *, bundle: Path | None = BUNDLE) -> list[str]:
    report = mapping.lint_mapping(document, bundle_root=bundle)
    return [finding.code for finding in report.errors]


# --- the baseline, first ----------------------------------------------------


def test_the_worked_example_passes_with_its_bundle(worked: dict) -> None:
    report = mapping.lint_mapping(worked, bundle_root=BUNDLE)
    assert report.errors == (), report.render()
    assert report.bundle_checked


def test_the_worked_example_accounts_for_every_contract_field(worked: dict) -> None:
    report = mapping.lint_mapping(worked, bundle_root=BUNDLE)
    total = sum(len(table.fields) for table in spec.TABLES)
    assert sum(report.outcome_counts.values()) == total
    # Every outcome except 'gap' is exercised. The example is deliberately
    # complete, so a gap appearing here would mean the example regressed into an
    # illustration of an unfinished mapping.
    assert report.outcome_counts["gap"] == 0
    for outcome in (mapping.OUTCOME_RENAME, mapping.OUTCOME_DERIVE, mapping.OUTCOME_DECLARE_ABSENT):
        assert report.outcome_counts[outcome] > 0


def test_the_only_warnings_are_the_ones_a_human_must_judge(worked: dict) -> None:
    report = mapping.lint_mapping(worked, bundle_root=BUNDLE)
    codes = {finding.code for finding in report.warnings}
    # Precisely the two absences. If the shared-implementation warning fired here
    # it would be noise: one query per table legitimately emits that table's
    # columns, and a warning channel that cries wolf on a good mapping is one
    # nobody reads.
    assert codes == {mapping.WARN_OPTIONAL_INPUT_ABSENT}, report.render()
    assert len(report.warnings) == 2


def test_a_missing_bundle_is_reported_rather_than_passing_quietly(worked: dict) -> None:
    report = mapping.lint_mapping(worked, bundle_root=None)
    assert report.errors == ()
    assert mapping.WARN_NO_ADAPTER_BUNDLE in {f.code for f in report.warnings}
    # And it is an error under --warnings-as-errors, because a pass that checked
    # none of the code claims is not the same pass.
    assert not report.passed(warnings_are_errors=True)


# --- the fifth outcome: a silent default -----------------------------------


def test_a_field_left_out_entirely_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    del doc["tables"]["reader_event"]["fields"]["session_id"]
    assert mapping.MISSING_FIELD in _codes(doc)


def test_a_table_left_out_entirely_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    del doc["tables"]["community_action"]
    # An optional input you do not deliver is *declared* absent. Omitting it is a
    # different statement, and the one that reads as zero activity downstream.
    assert mapping.MISSING_TABLE in _codes(doc)


def test_an_invented_fifth_outcome_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["reader"]["fields"]["id_grain"] = {
        "outcome": "default",
        "value": "resolved_person",
    }
    assert mapping.INVALID_OUTCOME in _codes(doc)


def test_a_default_smuggled_in_beside_a_real_outcome_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    entry = doc["tables"]["subscription_span"]["fields"]["payer_type"]
    entry["default"] = "individual"
    assert mapping.UNKNOWN_KEY in _codes(doc)


def test_a_null_resolution_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["fields"]["published_ts"] = None
    assert mapping.BAD_TYPE in _codes(doc)


def test_an_empty_object_is_not_an_outcome(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["fields"]["published_ts"] = {}
    assert mapping.INVALID_OUTCOME in _codes(doc)


def test_a_duplicated_key_is_refused_rather_than_silently_last_wins(tmp_path: Path) -> None:
    # json.loads keeps the last value. In an artifact whose entire claim is that
    # every field is accounted for exactly once, that is one accounting quietly
    # replacing another.
    path = tmp_path / mapping.MAPPING_FILENAME
    path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")
    with pytest.raises(mapping.MappingError, match="twice"):
        mapping.load_mapping_document(path)


# --- declarations ----------------------------------------------------------


@pytest.mark.parametrize("key", mapping.DECLARATION_KEYS)
def test_every_required_declaration_must_be_accounted_for(worked: dict, key: str) -> None:
    doc = copy.deepcopy(worked)
    del doc["declarations"][key]
    codes = _codes(doc)
    assert mapping.MISSING_DECLARATION in codes, f"{key} went unnoticed"


def test_the_declaration_list_tracks_the_manifests_own_required_keys() -> None:
    # If a fifth undefaulted declaration is ever added to the contract, this is
    # the test that notices the lint is still checking four.
    from engagement_kernel.contract import manifest

    structural = {"contract_name", "contract_version", "optional_inputs"}
    substantive = set(manifest._REQUIRED_KEYS) - structural
    assert substantive == set(mapping.DECLARATION_KEYS)


def test_a_declaration_owned_by_nobody_in_particular_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["declarations"]["scored_population"]["owner_role"] = "TBD"
    assert mapping.PLACEHOLDER_VALUE in _codes(doc)


def test_a_declaration_owner_that_is_a_person_is_refused(worked: dict) -> None:
    # The artifact is meant to be shareable, and a schema whose happy path is a
    # name and an address collects personal data by default. A role also survives
    # the staff change that makes a name stale.
    doc = copy.deepcopy(worked)
    doc["declarations"]["week_anchor"]["owner_role"] = "dana.reyes@riverbend.example"
    assert mapping.PERSONAL_IDENTIFIER in _codes(doc)


# --- absence must be declared, and consistent -----------------------------


def test_declaring_a_required_table_absent_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["availability"] = {"status": "not_deployed", "statement": "x" * 40}
    assert mapping.REQUIRED_TABLE_NOT_AVAILABLE in _codes(doc)


def test_declaring_a_required_field_absent_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["reader"]["fields"]["id_grain"] = {"outcome": "declare_absent"}
    assert mapping.ABSENT_IN_REQUIRED_TABLE in _codes(doc)


def test_a_field_absent_from_an_available_table_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["email_click"]["fields"]["campaign_id"] = {"outcome": "declare_absent"}
    # Absence is a property of the whole input. One absent column inside a
    # delivered input means one of the two statements is wrong.
    assert mapping.ABSENT_IN_AVAILABLE_TABLE in _codes(doc)


def test_a_mapped_field_inside_an_undelivered_table_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["community_action"]["fields"]["site_id"] = {
        "outcome": "rename",
        "source": {"system": "w", "relation": "r", "column": "c"},
        "implementation": {"path": "readers.sql", "sha256": _digest("readers.sql")},
        "rationale": "a rationale long enough to clear the floor",
    }
    assert mapping.PRESENT_IN_UNAVAILABLE_TABLE in _codes(doc)


def test_declare_absent_carrying_a_source_contradicts_itself(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["email_open"]["fields"]["list_id"] = {
        "outcome": "declare_absent",
        "source": {"system": "esp", "relation": "esp.open", "column": "list_uuid"},
    }
    assert mapping.ABSENT_WITH_EXTRA_KEYS in _codes(doc)


def test_an_absence_with_no_reason_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    del doc["tables"]["community_action"]["availability"]["statement"]
    # This is the outcome a reviewer most needs to be able to disagree with, and
    # the lint cannot tell "we do not have this product" from "extracting it was
    # inconvenient". So it insists somebody writes down which one they mean.
    assert mapping.MISSING_ABSENCE_STATEMENT in _codes(doc)


def test_an_invented_availability_status_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["community_action"]["availability"]["status"] = "too_expensive"
    assert mapping.INVALID_AVAILABILITY_STATUS in _codes(doc)


def test_an_available_input_without_a_coverage_floor_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    del doc["tables"]["email_click"]["availability"]["available_from"]
    assert mapping.AVAILABLE_WITHOUT_FLOOR in _codes(doc)


# --- gaps ------------------------------------------------------------------


def _gap_entry(**overrides: object) -> dict:
    entry: dict = {
        "outcome": "gap",
        "owner": {
            "role": "Director of Data Platform",
            "tracking_ref": "RVB-4182",
        },
        "blocker": (
            "Raw app events are retained for 30 days, so no historical source can cover "
            "the analysis window."
        ),
    }
    entry.update(overrides)
    return entry


def _with_gap(worked: dict, **overrides: object) -> dict:
    doc = copy.deepcopy(worked)
    doc["tables"]["reader_event"]["fields"]["engagement_time_seconds"] = _gap_entry(**overrides)
    return doc


def test_a_well_formed_gap_passes_but_warns(worked: dict) -> None:
    report = mapping.lint_mapping(_with_gap(worked), bundle_root=BUNDLE)
    assert report.errors == (), report.render()
    # A well-formed gap is a complete *disclosure* and an incomplete *delivery*.
    # Passing the lint and being ready to run are different claims.
    assert mapping.WARN_GAP_PRESENT in {f.code for f in report.warnings}


def test_a_gap_with_no_owner_is_refused(worked: dict) -> None:
    doc = _with_gap(worked)
    del doc["tables"]["reader_event"]["fields"]["engagement_time_seconds"]["owner"]
    assert mapping.GAP_WITHOUT_OWNER in _codes(doc)


@pytest.mark.parametrize("role", ["TBD", "the team", "Data Team", "unknown", "?", ""])
def test_a_gap_owned_by_a_placeholder_is_refused(worked: dict, role: str) -> None:
    doc = _with_gap(worked)
    doc["tables"]["reader_event"]["fields"]["engagement_time_seconds"]["owner"]["role"] = role
    assert mapping.PLACEHOLDER_VALUE in _codes(doc), f"{role!r} was accepted as an owner"


def test_a_gap_owner_who_is_a_person_is_refused(worked: dict) -> None:
    doc = _with_gap(worked)
    doc["tables"]["reader_event"]["fields"]["engagement_time_seconds"]["owner"]["role"] = (
        "dana.reyes@riverbend.example"
    )
    assert mapping.PERSONAL_IDENTIFIER in _codes(doc)


def test_a_gap_with_no_real_blocker_is_refused(worked: dict) -> None:
    doc = _with_gap(worked, blocker="n/a")
    assert mapping.PLACEHOLDER_VALUE in _codes(doc)


def test_a_gap_with_a_near_empty_blocker_is_refused(worked: dict) -> None:
    doc = _with_gap(worked, blocker="see above")
    assert mapping.GAP_WITHOUT_BLOCKER in _codes(doc)


# --- adapter traceability --------------------------------------------------


def _digest(name: str) -> str:
    return hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest()


def test_a_cited_file_that_does_not_exist_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["fields"]["published_ts"]["implementation"]["path"] = "nowhere.sql"
    # Citing a file nobody can open is the cheapest way to make an unimplemented
    # derivation look implemented.
    assert mapping.IMPLEMENTATION_FILE_MISSING in _codes(doc)


def test_a_cited_file_that_changed_after_the_mapping_was_written_is_refused(
    worked: dict,
) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["fields"]["published_ts"]["implementation"]["sha256"] = "0" * 64
    assert mapping.IMPLEMENTATION_HASH_MISMATCH in _codes(doc)


def test_a_path_reaching_outside_the_bundle_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["content"]["fields"]["published_ts"]["implementation"]["path"] = (
        "../../../etc/passwd"
    )
    assert mapping.IMPLEMENTATION_PATH_ESCAPES in _codes(doc)


def test_a_derivation_with_no_inputs_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["reader_event"]["fields"]["session_id"]["inputs"] = []
    assert mapping.DERIVE_WITHOUT_INPUTS in _codes(doc)


def test_a_rename_with_no_source_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    del doc["tables"]["reader"]["fields"]["reader_id"]["source"]
    assert mapping.MISSING_KEY in _codes(doc)


# --- contract identity -----------------------------------------------------


def test_a_mapping_for_another_contract_version_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["contract"]["version"] = "0.9.0"
    # The field list this mapping accounts for is not the field list being
    # checked, so a pass would be an accident.
    assert mapping.CONTRACT_MISMATCH in _codes(doc)


def test_an_unreadable_schema_version_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["schema_version"] = 99
    assert mapping.UNKNOWN_SCHEMA_VERSION in _codes(doc)


def test_a_table_the_contract_does_not_define_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["page_views"] = {"fields": {}}
    assert mapping.UNKNOWN_TABLE in _codes(doc)


def test_a_field_the_contract_does_not_define_is_refused(worked: dict) -> None:
    doc = copy.deepcopy(worked)
    doc["tables"]["reader_event"]["fields"]["scroll_depth_pct"] = {"outcome": "declare_absent"}
    assert mapping.UNKNOWN_FIELD in _codes(doc)


def test_commentary_keys_are_allowed_because_they_cannot_smuggle_a_value(
    worked: dict,
) -> None:
    doc = copy.deepcopy(worked)
    doc["_a_note"] = "an artifact meant to be hand-edited should be able to explain itself"
    doc["tables"]["reader"]["_a_note"] = "and in place, not only at the top"
    assert _codes(doc) == []


# --- the CLI ---------------------------------------------------------------


def test_the_cli_passes_on_the_worked_example(capsys: pytest.CaptureFixture[str]) -> None:
    code = mapping.main([str(EXAMPLE_DIR), "--adapter-bundle", str(BUNDLE)])
    assert code == mapping.EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_the_cli_reports_an_unreadable_mapping_separately_from_a_failing_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / mapping.MAPPING_FILENAME).write_text("{not json", encoding="utf-8")
    code = mapping.main([str(tmp_path)])
    # 2, not 1: there is nothing to have an opinion about, which is a different
    # answer from "this mapping is wrong".
    assert code == mapping.EXIT_UNTRUSTED
    assert "could not be read" in capsys.readouterr().err


def test_the_cli_emits_machine_readable_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = mapping.load_mapping_document(EXAMPLE)
    del doc["tables"]["reader_event"]["fields"]["session_id"]
    path = tmp_path / mapping.MAPPING_FILENAME
    path.write_text(json.dumps(doc), encoding="utf-8")
    code = mapping.main([str(path), "--json"])
    assert code == mapping.EXIT_FINDINGS
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert mapping.MISSING_FIELD in {f["code"] for f in payload["findings"]}
