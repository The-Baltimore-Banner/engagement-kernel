"""Build a worked oracle example: one conforming delivery and five broken variants.

This exists so that the first thing an adopter does with the oracle is watch it
work on data they did not have to produce. Two commands and they have seen the
whole mechanism: a baseline that conforms, five surgical mutations, and a checker
that refuses any of them if the refusal is not the one its author predicted.

**The variants are built, not committed.** Committing five near-copies of a
Parquet delivery would put the baseline in two places, and the day the demo
generator changes, every variant differs from the baseline in every file. The
oracle's blast-radius check would then report the whole tree as changed and the
failure would look like a bug in the checker. Deriving each variant from the
current baseline makes that impossible by construction.

The five mutations are not arbitrary. They are the classes an adopter actually
hits first -- a missing declaration, a naive timestamp, a wrong dtype, a required
input that is not there, and a column the contract refuses -- so this doubles as
the standing check that each of those refusals still names the fix and not only
the violation. Each case asserts a phrase from the *fix* half of the message, so
rewriting one of those messages back into a bare violation report fails here.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from engagement_kernel.contract import demo
from engagement_kernel.contract.manifest import MANIFEST_FILENAME
from engagement_kernel.contract.oracle import CASES_FILENAME

BASELINE_DIRNAME = "delivery"
NEGATIVE_DIRNAME = "negative"


def _recast(path: Path, column: str, arrow_type: pa.DataType) -> None:
    table = pq.read_table(path)
    index = table.schema.get_field_index(column)
    table = table.set_column(
        index, pa.field(column, arrow_type), table.column(index).cast(arrow_type)
    )
    pq.write_table(table, path)


def _drop_declaration(directory: Path, key: str) -> None:
    path = directory / MANIFEST_FILENAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop(key, None)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def _add_forbidden_column(path: Path) -> None:
    table = pq.read_table(path)
    table = table.append_column(
        "scroll_depth_pct", pa.array([50.0] * table.num_rows, type=pa.float64())
    )
    pq.write_table(table, path)


def build(directory: str | Path) -> dict:
    """Write the baseline, the five variants, and the case set that binds them."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    baseline = root / BASELINE_DIRNAME
    if baseline.exists():
        shutil.rmtree(baseline)
    demo.write_delivery(baseline)

    negatives = root / NEGATIVE_DIRNAME
    if negatives.exists():
        shutil.rmtree(negatives)
    negatives.mkdir(parents=True)

    cases: list[dict] = []

    # 1. A required declaration is absent. Refused before any table is read, so
    #    it carries no per-table code -- the class an adopter meets first, and
    #    the one whose message has to carry the question rather than the key name.
    case = negatives / "missing-declaration"
    shutil.copytree(baseline, case)
    _drop_declaration(case, "scored_population")
    cases.append(
        {
            "id": "missing-declaration",
            "path": f"{NEGATIVE_DIRNAME}/missing-declaration",
            "expected_exit": 2,
            "required_codes": ["manifest_error"],
            "required_message_contains": ["commercial", "declarations-questionnaire"],
            "changed_files": [MANIFEST_FILENAME],
            "mutation": (
                "scored_population removed from the manifest. Nothing is checked without it, "
                "so the exit is 2 rather than 1: there is no verdict to give, not a failing "
                "one. The message has to name the decision and its owner, because the key "
                "name alone tells a first-time reader nothing about what to go and find out."
            ),
        }
    )

    # 2. A naive timestamp. The defect the contract exists to refuse, and the one
    #    whose obvious fix is wrong in a way that looks right.
    case = negatives / "naive-timestamp"
    shutil.copytree(baseline, case)
    _recast(case / "reader_event.parquet", "event_ts", pa.timestamp("us"))
    cases.append(
        {
            "id": "naive-timestamp",
            "path": f"{NEGATIVE_DIRNAME}/naive-timestamp",
            "expected_exit": 1,
            "required_codes": ["TIMESTAMP_NOT_TIMEZONE_AWARE"],
            "forbidden_codes": ["COLUMN_TYPE_MISMATCH"],
            "required_message_contains": ["Do NOT localise"],
            "changed_files": ["reader_event.parquet"],
            "mutation": (
                "The timezone stripped from reader_event.event_ts. Asserts the message warns "
                "against the plausible wrong fix -- localising to the declared day boundary, "
                "which the engine applies itself, so doing it here shifts every instant twice "
                "and the second shift is invisible. forbidden_codes keeps this case honest: a "
                "type mismatch here would mean the recast broke the column rather than only "
                "its zone."
            ),
        }
    )

    # 3. A wrong dtype.
    case = negatives / "wrong-dtype"
    shutil.copytree(baseline, case)
    _recast(case / "reader_event.parquet", "engagement_time_seconds", pa.string())
    cases.append(
        {
            "id": "wrong-dtype",
            "path": f"{NEGATIVE_DIRNAME}/wrong-dtype",
            "expected_exit": 1,
            "required_codes": ["COLUMN_TYPE_MISMATCH"],
            "required_message_contains": ["Cast it to"],
            "changed_files": ["reader_event.parquet"],
            "mutation": (
                "engagement_time_seconds written as a string. A measure arriving as text is "
                "what an untyped export produces, and the refusal has to say where the cast "
                "belongs -- in the job that writes the file -- rather than leaving the reader "
                "to conclude the validator should have done it."
            ),
        }
    )

    # 4. A required input that is not there.
    case = negatives / "missing-required-table"
    shutil.copytree(baseline, case)
    (case / "content.parquet").unlink()
    cases.append(
        {
            "id": "missing-required-table",
            "path": f"{NEGATIVE_DIRNAME}/missing-required-table",
            "expected_exit": 1,
            "required_codes": ["MISSING_REQUIRED_TABLE"],
            "required_message_contains": ["no way to declare a required input absent"],
            "changed_files": ["content.parquet"],
            "mutation": (
                "content.parquet deleted. The message has to close off the wrong fix an "
                "adopter will reach for first, which is to declare the input absent the way "
                "the three optional inputs can be -- that mechanism does not extend to "
                "required inputs, and looking for it is a wasted afternoon."
            ),
        }
    )

    # 5. A column the contract refuses. The classic helpful-agent defect: it adds
    #    a measure that looks useful and is deliberately out of scope.
    case = negatives / "forbidden-column"
    shutil.copytree(baseline, case)
    _add_forbidden_column(case / "reader_event.parquet")
    cases.append(
        {
            "id": "forbidden-column",
            "path": f"{NEGATIVE_DIRNAME}/forbidden-column",
            "expected_exit": 1,
            "required_codes": ["FORBIDDEN_COLUMN"],
            "changed_files": ["reader_event.parquet"],
            "mutation": (
                "A scroll-depth column added to reader_event. This is the mistake a coding "
                "agent makes on purpose and in good faith: the source has the measure, so it "
                "helpfully includes it. The contract refuses it by name, which is why an "
                "adopter's agent needs to meet the refusal during mapping rather than after "
                "a model has been fit on it."
            ),
        }
    )

    case_set = {
        "_what_this_is": (
            "A worked case set for engagement-kernel-check-oracle. The baseline conforms; "
            "each negative case declares the exit status, the finding code, a phrase from the "
            "message and the files it touched, and the checker refuses the case if any of "
            "those is not what actually happened. The changed-files list is the part that "
            "matters: 'the validator refused it' is not evidence unless the refusal is "
            "attributable to one deliberate mutation."
        ),
        "baseline": {"path": BASELINE_DIRNAME, "expected_exit": 0},
        "negative_cases": cases,
    }
    (root / CASES_FILENAME).write_text(json.dumps(case_set, indent=2) + "\n", encoding="utf-8")
    return case_set


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-demo-oracle",
        description=(
            "Write a worked validator-oracle example: a conforming delivery, five surgically "
            "broken variants, and the case set that says what each one should be refused for. "
            "Run engagement-kernel-check-oracle over the result."
        ),
    )
    parser.add_argument("directory", help="directory to write the example into")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    case_set = build(args.directory)
    root = Path(args.directory)
    print(f"baseline:  {root / BASELINE_DIRNAME}")
    for case in case_set["negative_cases"]:
        print(f"negative:  {root / case['path']}  ({case['id']})")
    print(f"case set:  {root / CASES_FILENAME}")
    print()
    print(f"Now check it:  engagement-kernel-check-oracle {root}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
