"""The oracle turns "the validator refused it" into evidence, or it is theatre.

The check an adopter's agent is asked to pass -- produce a broken variant and show
the validator refusing it -- has an obvious cheat: break everything. Delete the
files, observe a failure, declare the check passed. So the oracle requires each
case to declare which files it touched and computes the real difference itself.

That makes the interesting tests here adversarial rather than confirmatory. The
three cheats below are the ones somebody would actually reach for, and each must
be caught for the mechanism to be worth shipping:

* widen the blast radius, so the refusal is no longer attributable to one change;
* break something else, so the refusal is real but not the predicted one;
* change nothing, so the "variant" is the baseline wearing a different name.

The last block is the one that earns its keep over time. Each case asserts a
phrase from the *fix* half of a validator message, so rewriting one of those
messages back into a bare violation report -- which is the natural direction of
drift, since a bare report is shorter and reads as more precise -- fails here
rather than in a review nobody scheduled.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest

from engagement_kernel.contract import oracle, oracle_demo


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("oracle")
    with contextlib.redirect_stdout(io.StringIO()):
        oracle_demo.build(root)
    return root


@pytest.fixture
def scratch(demo_root: Path, tmp_path: Path) -> Path:
    """A fresh writable copy, so a mutating test cannot affect its neighbours."""
    target = tmp_path / "case-set"
    shutil.copytree(demo_root, target)
    return target


def _run(
    root: Path, *, min_negative: int = oracle.DEFAULT_MIN_NEGATIVE_CASES
) -> oracle.OracleReport:
    cases = oracle.load_cases(root / oracle.CASES_FILENAME)
    return oracle.run_cases(cases, root, min_negative=min_negative)


def _outcome(report: oracle.OracleReport, case_id: str) -> oracle.CaseOutcome:
    for outcome in report.outcomes:
        if outcome.case_id == case_id:
            return outcome
    raise AssertionError(f"no outcome for {case_id}: {[o.case_id for o in report.outcomes]}")


# --- the worked case set passes ---------------------------------------------


def test_the_demo_case_set_behaves_exactly_as_declared(demo_root: Path) -> None:
    report = _run(demo_root)
    assert report.passed, report.render()


def test_the_demo_covers_the_classes_an_adopter_hits_first(demo_root: Path) -> None:
    cases = oracle.load_cases(demo_root / oracle.CASES_FILENAME)
    ids = {case["id"] for case in cases["negative_cases"]}
    # These four are the AC-named first-contact classes; the fifth is the
    # helpful-agent defect, which is the one an adopter's coding agent produces
    # rather than one their warehouse does.
    assert {
        "missing-declaration",
        "naive-timestamp",
        "wrong-dtype",
        "missing-required-table",
        "forbidden-column",
    } <= ids


def test_the_baseline_is_checked_before_the_variants(demo_root: Path) -> None:
    report = _run(demo_root)
    assert report.outcomes[0].case_id == "baseline"
    assert report.outcomes[0].observed_exit == 0


# --- the cheats -------------------------------------------------------------


def test_a_case_that_breaks_more_than_it_declares_is_refused(scratch: Path) -> None:
    (scratch / "negative" / "naive-timestamp" / "email_open.parquet").unlink()
    report = _run(scratch)
    outcome = _outcome(report, "naive-timestamp")
    assert not outcome.passed
    assert any("blast radius" in problem for problem in outcome.problems)


def test_breaking_everything_is_refused_rather_than_counted_as_a_refusal(
    scratch: Path,
) -> None:
    case_dir = scratch / "negative" / "wrong-dtype"
    for parquet in case_dir.glob("*.parquet"):
        parquet.unlink()
    report = _run(scratch)
    outcome = _outcome(report, "wrong-dtype")
    assert not outcome.passed
    # Caught three independent ways: the predicted code is absent, the message
    # assertion misses, and the blast radius is the whole tree. Any one would do;
    # three means the cheat has nowhere to go.
    assert len(outcome.problems) >= 3, outcome.problems


def test_a_variant_identical_to_the_baseline_is_refused(scratch: Path) -> None:
    case_dir = scratch / "negative" / "forbidden-column"
    shutil.rmtree(case_dir)
    shutil.copytree(scratch / "delivery", case_dir)
    report = _run(scratch)
    outcome = _outcome(report, "forbidden-column")
    assert not outcome.passed


def test_a_case_that_fails_for_the_wrong_reason_is_refused(scratch: Path) -> None:
    # Same one-file blast radius, a genuine refusal -- and not the declared one.
    case_dir = scratch / "negative" / "wrong-dtype"
    manifest = case_dir / "manifest.json"
    raw = json.loads((scratch / "delivery" / "manifest.json").read_text())
    raw["day_boundary_timezone"] = "Mars/Olympus_Mons"
    shutil.copy(scratch / "delivery" / "reader_event.parquet", case_dir / "reader_event.parquet")
    manifest.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    report = _run(scratch)
    outcome = _outcome(report, "wrong-dtype")
    assert not outcome.passed
    assert any("other than the one its author predicted" in p for p in outcome.problems)


def test_a_case_set_with_a_failing_baseline_is_refused(scratch: Path) -> None:
    (scratch / "delivery" / "reader.parquet").unlink()
    report = _run(scratch)
    outcome = _outcome(report, "baseline")
    assert not outcome.passed
    # And the reason is stated: with a non-conforming baseline every variant
    # would be refused for the baseline's defect, so the set proves nothing.
    assert any("proves nothing about the variants" in p for p in outcome.problems)


def test_a_case_that_predicts_only_failure_is_refused(scratch: Path) -> None:
    cases = json.loads((scratch / oracle.CASES_FILENAME).read_text())
    for case in cases["negative_cases"]:
        if case["id"] == "wrong-dtype":
            case.pop("required_codes")
    (scratch / oracle.CASES_FILENAME).write_text(json.dumps(cases), encoding="utf-8")
    report = _run(scratch)
    outcome = _outcome(report, "wrong-dtype")
    assert not outcome.passed
    assert any("is not a prediction" in p for p in outcome.problems)


def test_one_negative_case_is_below_the_floor(scratch: Path) -> None:
    cases = json.loads((scratch / oracle.CASES_FILENAME).read_text())
    cases["negative_cases"] = cases["negative_cases"][:1]
    (scratch / oracle.CASES_FILENAME).write_text(json.dumps(cases), encoding="utf-8")
    report = _run(scratch)
    assert not report.passed


def test_a_case_with_no_mutation_note_is_refused(scratch: Path) -> None:
    cases = json.loads((scratch / oracle.CASES_FILENAME).read_text())
    for case in cases["negative_cases"]:
        if case["id"] == "naive-timestamp":
            case["mutation"] = "broke it"
    (scratch / oracle.CASES_FILENAME).write_text(json.dumps(cases), encoding="utf-8")
    report = _run(scratch)
    assert not _outcome(report, "naive-timestamp").passed


# --- the message assertions are load-bearing --------------------------------


@pytest.mark.parametrize(
    ("case_id", "phrase"),
    [
        ("naive-timestamp", "Do NOT localise"),
        ("wrong-dtype", "Cast it to"),
        ("missing-required-table", "no way to declare a required input absent"),
        ("missing-declaration", "commercial"),
    ],
)
def test_each_first_contact_message_still_names_the_fix(
    demo_root: Path, case_id: str, phrase: str
) -> None:
    """The standing guard on the rewritten messages.

    Asserted here as well as inside the case set, so the property survives
    somebody editing the case set: a rewrite that drops the fix from a message
    has to defeat both to land.
    """
    cases = oracle.load_cases(demo_root / oracle.CASES_FILENAME)
    case = next(c for c in cases["negative_cases"] if c["id"] == case_id)
    assert phrase in case.get("required_message_contains", []), (
        f"{case_id} no longer asserts the fix phrase"
    )
    report = _run(demo_root)
    assert _outcome(report, case_id).passed


def test_the_changed_file_diff_ignores_timestamps(scratch: Path) -> None:
    # A variant made by copying has entirely fresh mtimes, so a diff by
    # modification time would report every file as changed and the blast-radius
    # check would be useless. Content digests are what make it work.
    case_dir = scratch / "negative" / "naive-timestamp"
    for path in case_dir.rglob("*"):
        if path.is_file():
            path.touch()
    assert oracle.changed_files(scratch / "delivery", case_dir) == ("reader_event.parquet",)


# --- the CLI ---------------------------------------------------------------


def test_the_cli_passes_on_the_demo(demo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert oracle.main([str(demo_root)]) == oracle.EXIT_OK
    assert "behaved exactly as declared" in capsys.readouterr().out


def test_the_cli_separates_an_unreadable_case_set_from_a_failing_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert oracle.main([str(tmp_path)]) == oracle.EXIT_UNTRUSTED
    assert "could not be read" in capsys.readouterr().err
