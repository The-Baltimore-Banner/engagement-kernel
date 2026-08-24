"""The validator as an oracle: run an adopter's own broken variants against it.

An adopter's coding agent produces an adapter, a delivery and a mapping manifest.
The delivery validating proves the files conform. It does not prove the agent
understood what conformance *is* -- a delivery can pass because the agent got it
right, or because the parts it got wrong happen not to be checked.

So the spec asks for one more thing: a set of deliberately broken variants, each
with the defect the agent expects the validator to name. That inverts the burden.
An agent that can make the validator fail on purpose, for the reason it predicted,
has demonstrated that it read the rules rather than pattern-matched the example.

The obvious way to fake it is to break everything. Delete the files, watch the
validator fail, declare the check passed. So every case declares which files it
changed, and this module computes the actual difference against the baseline and
refuses a case whose blast radius is wider than declared. A one-line mutation to
``manifest.json`` and a rm -rf are both "the validator refused it"; only the first
one is evidence.

What this proves:

* the validator was actually run against those directories, here, not reported on;
* each variant violates the rule its author predicted, by code and by message;
* the mutation was surgical, so the refusal is attributable to it;
* the message a producer will actually read names the defect they will actually
  have.

What it does not prove: that the baseline delivery came out of the submitted
adapter, that the mapping manifest describes that adapter, or that any derivation
is semantically right. Those are not checkable from these inputs -- see
``mapping.py`` for the same boundary stated for the mapping lint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from engagement_kernel.contract.validate import EXIT_FINDINGS, validate_directory

CASES_FILENAME = "validation-cases.json"

#: A case set with fewer than this many negative cases is refused. One negative
#: case is a demonstration; the floor exists because the classes an adopter
#: actually hits are several and unrelated -- a missing declaration, a wrong
#: dtype, a naive timestamp -- and passing one says nothing about the others.
DEFAULT_MIN_NEGATIVE_CASES = 3

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNTRUSTED = 2


class OracleError(ValueError):
    """The case set could not be read, so no verdict is available."""


@dataclass(frozen=True)
class CaseOutcome:
    """What happened when one case was run."""

    case_id: str
    passed: bool
    problems: tuple[str, ...]
    observed_exit: int
    observed_codes: tuple[str, ...]

    def render(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [f"{verdict}  {self.case_id}  exit={self.observed_exit}"]
        for problem in self.problems:
            lines.append(f"        {problem}")
        return "\n".join(lines)


@dataclass(frozen=True)
class OracleReport:
    outcomes: tuple[CaseOutcome, ...]

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes)

    def render(self) -> str:
        lines = ["validator oracle", ""]
        lines.extend(outcome.render() for outcome in self.outcomes)
        lines.append("")
        failed = [o.case_id for o in self.outcomes if not o.passed]
        if failed:
            lines.append(f"FAIL: {len(failed)} case(s) did not behave as declared: {failed}")
        else:
            lines.append(f"PASS: {len(self.outcomes)} case(s) behaved exactly as declared")
        return "\n".join(lines)


def _digest_tree(root: Path) -> dict[str, str]:
    """Every file under ``root``, by relative path, with its content digest."""
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digests[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


def changed_files(baseline: Path, variant: Path) -> tuple[str, ...]:
    """Which files differ between two delivery directories.

    A file counts as changed if it was added, removed, or has different bytes.
    Comparing digests rather than mtimes matters: a variant produced by copying
    and editing has entirely fresh timestamps, so every file would look changed.
    """
    left = _digest_tree(baseline)
    right = _digest_tree(variant)
    names = set(left) | set(right)
    return tuple(sorted(name for name in names if left.get(name) != right.get(name)))


def load_cases(path: str | Path) -> dict:
    path = Path(path)
    if path.is_dir():
        path = path / CASES_FILENAME
    if not path.exists():
        raise OracleError(
            f"no case set at {path}. The case set is what turns 'the validator refused it' "
            f"into evidence; start from examples/mapping/{CASES_FILENAME}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise OracleError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OracleError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise OracleError(f"{path} must contain a JSON object")
    for key in ("baseline", "negative_cases"):
        if key not in raw:
            raise OracleError(f"{path} is missing required key {key!r}")
    if not isinstance(raw["negative_cases"], list):
        raise OracleError("negative_cases must be a list")
    return raw


def _validate(directory: Path) -> tuple[int, tuple[str, ...], str]:
    """Run the validator and return its exit code, its codes, and its rendered text."""
    if not directory.is_dir():
        return EXIT_UNTRUSTED, (), f"not a directory: {directory}"
    report = validate_directory(directory)
    text = report.render()
    codes = list(report.codes())
    if report.manifest_error is not None:
        # A manifest defect is refused before any table is read, so it carries no
        # per-table code. Surfacing it under a stable pseudo-code lets a case
        # assert on it the same way as any other class -- and the missing-
        # declaration class, which is the one an adopter hits first, lives here.
        codes.append("manifest_error")
    return report.exit_code, tuple(codes), text


def run_cases(cases: dict, root: str | Path, *, min_negative: int) -> OracleReport:
    """Run every declared case and check it behaved exactly as declared."""
    root = Path(root)
    outcomes: list[CaseOutcome] = []

    baseline_raw = cases["baseline"]
    if not isinstance(baseline_raw, dict) or "path" not in baseline_raw:
        raise OracleError("baseline must be an object with a 'path'")
    baseline_dir = root / baseline_raw["path"]
    expected_baseline_exit = int(baseline_raw.get("expected_exit", 0))

    exit_code, codes, text = _validate(baseline_dir)
    problems: list[str] = []
    if exit_code != expected_baseline_exit:
        problems.append(
            f"baseline exited {exit_code}, expected {expected_baseline_exit}. A case set whose "
            f"baseline does not conform proves nothing about the variants: every one of them "
            f"would be refused for the baseline's defect. Validator said:\n{text}"
        )
    outcomes.append(
        CaseOutcome(
            case_id="baseline",
            passed=not problems,
            problems=tuple(problems),
            observed_exit=exit_code,
            observed_codes=codes,
        )
    )

    negative = cases["negative_cases"]
    if len(negative) < min_negative:
        outcomes.append(
            CaseOutcome(
                case_id="<case count>",
                passed=False,
                problems=(
                    f"{len(negative)} negative case(s) declared, below the floor of "
                    f"{min_negative}. The classes an adopter hits first are unrelated to each "
                    f"other, so demonstrating one says nothing about the rest",
                ),
                observed_exit=0,
                observed_codes=(),
            )
        )

    for index, case in enumerate(negative):
        outcomes.append(_run_negative(case, index, root, baseline_dir))

    return OracleReport(outcomes=tuple(outcomes))


def _run_negative(case: object, index: int, root: Path, baseline_dir: Path) -> CaseOutcome:
    if not isinstance(case, dict):
        return CaseOutcome(
            case_id=f"negative_cases[{index}]",
            passed=False,
            problems=("must be an object",),
            observed_exit=0,
            observed_codes=(),
        )
    case_id = str(case.get("id") or f"negative_cases[{index}]")
    problems: list[str] = []

    for key in ("path", "expected_exit", "changed_files", "mutation"):
        if key not in case:
            problems.append(
                f"missing required key {key!r}. Without it the case is a claim rather than a check"
            )
    if problems:
        return CaseOutcome(
            case_id=case_id,
            passed=False,
            problems=tuple(problems),
            observed_exit=0,
            observed_codes=(),
        )

    case_dir = root / str(case["path"])
    expected_exit = int(case["expected_exit"])
    if expected_exit == 0:
        problems.append(
            "expected_exit is 0, so this is not a negative case. A variant the validator "
            "accepts demonstrates nothing about what it refuses"
        )

    exit_code, codes, text = _validate(case_dir)

    if exit_code != expected_exit:
        problems.append(f"exited {exit_code}, expected {expected_exit}. Validator said:\n{text}")

    required = [str(c) for c in case.get("required_codes", [])]
    for code in required:
        if code not in codes:
            problems.append(
                f"declared code {code!r} was not raised; the validator reported "
                f"{sorted(set(codes))}. "
                f"The variant fails for a reason other than the one its author predicted, which "
                f"is what an accidental break looks like"
            )
    for code in [str(c) for c in case.get("forbidden_codes", [])]:
        if code in codes:
            problems.append(
                f"forbidden code {code!r} was raised, so the mutation broke something it was "
                f"not supposed to touch"
            )
    if expected_exit == EXIT_FINDINGS and not required:
        problems.append(
            "no required_codes declared. 'The validator failed' is not a prediction; naming "
            "the code is"
        )

    for needle in [str(s) for s in case.get("required_message_contains", [])]:
        if needle not in text:
            problems.append(
                f"the validator's message does not contain {needle!r}. This is the assertion "
                f"that the refusal names the fix rather than only the violation, so it is "
                f"checked against the text a producer actually reads"
            )

    declared_changes = sorted(str(name) for name in case["changed_files"])
    actual_changes = sorted(changed_files(baseline_dir, case_dir))
    if actual_changes != declared_changes:
        problems.append(
            f"changed {actual_changes} against the baseline, but declares {declared_changes}. "
            f"The declared blast radius is what makes the refusal attributable: breaking "
            f"everything and observing a failure is not the same evidence as breaking one "
            f"thing and observing the failure it predicts"
        )
    if not actual_changes:
        problems.append(
            "is byte-identical to the baseline, so whatever the validator said about it, it "
            "did not say it about a mutation"
        )

    mutation = str(case.get("mutation", "")).strip()
    if len(mutation) < 20:
        problems.append(
            "the 'mutation' note is empty or near-empty. It is the one part of the case a "
            "reviewer reads to decide whether the defect class is worth checking at all"
        )

    return CaseOutcome(
        case_id=case_id,
        passed=not problems,
        problems=tuple(problems),
        observed_exit=exit_code,
        observed_codes=codes,
    )


# --- command line -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-check-oracle",
        description=(
            "Run a declared set of validator cases: one conforming baseline and several "
            "deliberately broken variants, each asserting the code and the message it "
            "expects, and each limited to the files it declares it changed."
        ),
    )
    parser.add_argument(
        "cases",
        help=f"path to {CASES_FILENAME}, or a directory containing one",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="resolve case paths against this directory (default: the case file's own)",
    )
    parser.add_argument(
        "--min-negative-cases",
        type=int,
        default=DEFAULT_MIN_NEGATIVE_CASES,
        help="floor on how many broken variants the set must declare",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases_path = Path(args.cases)
    if cases_path.is_dir():
        cases_path = cases_path / CASES_FILENAME
    root = Path(args.root) if args.root else cases_path.parent
    try:
        cases = load_cases(cases_path)
        report = run_cases(cases, root, min_negative=args.min_negative_cases)
    except OracleError as exc:
        print(f"case set could not be read: {exc}", file=sys.stderr)
        return EXIT_UNTRUSTED
    print(report.render())
    return EXIT_OK if report.passed else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
