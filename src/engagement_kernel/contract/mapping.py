"""The mapping manifest: how an adopter's warehouse was carried onto the contract.

A conforming delivery proves that the *files* are right. It proves nothing about
whether the right *data* went into them. A delivery whose ``reader_event`` rows
are page loads rather than article views validates perfectly and answers a
different question, and no amount of schema checking can tell the difference.

So the contract asks for a second artifact alongside the delivery: a declarative
record of how every field got there. Each contract field resolves to exactly one
of four outcomes.

``rename``
    A column in the source means the same thing. Mechanical: a type or encoding
    change is still a rename, a change in what the row *counts* is not.

``derive``
    The source concept computed into ours -- sessionising events, joining a
    billing table, resolving a device id to a person. Carries the rule, and a
    pointer to the code that implements it.

``declare_absent``
    Only legal for the three optional tables, and only when the table's
    availability says the input is not delivered. The existing manifest
    mechanism, restated per field so that every field has one visible outcome.

``gap``
    No mapping exists and none is derivable. Carries an accountable role and a
    reference into the adopter's own tracker. The one outcome that is a decision
    rather than an answer.

**There is a fifth outcome this file exists to make inexpressible: a silent
default.** An agent asked to map an unfamiliar warehouse onto an unfamiliar
contract will, at the first field it cannot place, reach for the most plausible
value -- UTC, Monday, "all paying states" -- and produce an artifact that looks
complete. Every structural rule below is in service of making that impossible to
write down: the outcome vocabulary is closed, every key set is closed, every
contract field must appear exactly once, and an absence has to name which
declared absence it is.

What this lint proves, stated as a property rather than a hope:

    Given the mapping manifest, the adapter snapshot and the delivery, no static
    check can determine whether a source column has the meaning claimed for it,
    nor whether the submitted adapter is what produced the delivery.

That is not a gap to be closed later; it follows from the inputs. The lint is
uncompromising about detectable incompleteness and contradiction, and explicit
that it does not adjudicate semantic truth. What it buys is that a human review
inspects a bounded list of explicit claims instead of inferring the mapping from
arbitrary adapter code.

**No personal data.** The accountable party for a gap is recorded as a *role*
and a tracking reference, never a name or an address. A schema whose happy path
is a person's name and email is a schema that collects personal data by default,
and every filled-in copy would carry real ones. A role also survives the staff
turnover that makes a name stale, which is what accountability actually needs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from engagement_kernel.contract import enums
from engagement_kernel.contract import spec as contract_spec
from engagement_kernel.contract.manifest import MANIFEST_FILENAME

MAPPING_FILENAME = "mapping-manifest.json"

#: The mapping manifest's own version, separate from the contract version. A
#: mapping manifest written against one shape of this file is not readable
#: against another, and saying so beats guessing.
MAPPING_SCHEMA_VERSION = 1

# --- the outcome vocabulary -------------------------------------------------

OUTCOME_RENAME = "rename"
OUTCOME_DERIVE = "derive"
OUTCOME_DECLARE_ABSENT = "declare_absent"
OUTCOME_GAP = "gap"

#: Closed on purpose. A field whose outcome is not one of these four is refused
#: rather than ignored, because "ignored" is how a fifth outcome arrives.
OUTCOMES: tuple[str, ...] = (
    OUTCOME_RENAME,
    OUTCOME_DERIVE,
    OUTCOME_DECLARE_ABSENT,
    OUTCOME_GAP,
)

#: The four declarations the contract refuses to default. Derived from the
#: manifest module's own required keys rather than restated, so a fifth
#: declaration cannot be added to the contract without this lint noticing.
DECLARATION_KEYS: tuple[str, ...] = (
    "day_boundary_timezone",
    "week_anchor",
    "article_view",
    "scored_population",
)

# --- finding codes ----------------------------------------------------------

# Structure and parse.
INVALID_JSON = "invalid_json"
DUPLICATE_KEY = "duplicate_key"
UNKNOWN_SCHEMA_VERSION = "unknown_schema_version"
UNKNOWN_KEY = "unknown_key"
MISSING_KEY = "missing_key"
BAD_TYPE = "bad_type"
CONTRACT_MISMATCH = "contract_mismatch"

# Exhaustiveness.
MISSING_TABLE = "missing_table"
UNKNOWN_TABLE = "unknown_table"
MISSING_FIELD = "missing_field"
UNKNOWN_FIELD = "unknown_field"
MISSING_DECLARATION = "missing_declaration"
UNKNOWN_DECLARATION = "unknown_declaration"
INVALID_OUTCOME = "invalid_outcome"

# Availability and absence.
REQUIRED_TABLE_NOT_AVAILABLE = "required_table_not_available"
INVALID_AVAILABILITY_STATUS = "invalid_availability_status"
AVAILABLE_WITHOUT_FLOOR = "available_without_floor"
UNAVAILABLE_WITH_FLOOR = "unavailable_with_floor"
ABSENT_IN_AVAILABLE_TABLE = "absent_in_available_table"
PRESENT_IN_UNAVAILABLE_TABLE = "present_in_unavailable_table"
ABSENT_IN_REQUIRED_TABLE = "absent_in_required_table"
MISSING_ABSENCE_STATEMENT = "missing_absence_statement"

# Per-outcome shape.
RENAME_WITHOUT_SOURCE = "rename_without_source"
DERIVE_WITHOUT_INPUTS = "derive_without_inputs"
MISSING_IMPLEMENTATION = "missing_implementation"
MISSING_RATIONALE = "missing_rationale"
GAP_WITHOUT_OWNER = "gap_without_owner"
GAP_WITHOUT_BLOCKER = "gap_without_blocker"
PLACEHOLDER_VALUE = "placeholder_value"
PERSONAL_IDENTIFIER = "personal_identifier"
ABSENT_WITH_EXTRA_KEYS = "absent_with_extra_keys"

# Adapter traceability.
IMPLEMENTATION_FILE_MISSING = "implementation_file_missing"
IMPLEMENTATION_HASH_MISMATCH = "implementation_hash_mismatch"
IMPLEMENTATION_PATH_ESCAPES = "implementation_path_escapes"

# Warnings.
WARN_GAP_PRESENT = "gap_present"
WARN_SHARED_IMPLEMENTATION = "shared_implementation"
WARN_OPTIONAL_INPUT_ABSENT = "optional_input_absent"
WARN_NO_ADAPTER_BUNDLE = "no_adapter_bundle"

#: Strings that mean the author has not answered. Matched case-insensitively
#: against the whole stripped value, so a rationale that merely *contains* the
#: word "team" is left alone -- this rejects an unanswered field, not a word.
_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "?",
        "??",
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "tbd",
        "tba",
        "todo",
        "to do",
        "to be determined",
        "unknown",
        "unspecified",
        "fixme",
        "xxx",
        "team",
        "the team",
        "data team",
        "data",
        "engineering",
        "eng",
        "it",
        "someone",
        "somebody",
        "anyone",
        "us",
        "me",
        "answer_required",
        "answer-required",
        "<answer>",
        "fill me in",
        "changeme",
        "change me",
        "example",
        "placeholder",
    }
)

#: Substrings that mean a value is a person rather than a role. The gap owner is
#: the one place a personal identifier would otherwise land, so it is checked
#: rather than trusted -- the same reasoning as the manifest's exclusion list.
_PERSONAL_MARKERS: tuple[str, ...] = ("@", "http://", "https://", "tel:", "+1 ", "mailto:")

#: A blocker or rationale shorter than this is presence without content. The
#: floor is deliberately low: it catches "n/a" and "see above", and it is not a
#: quality measure. Nothing here can tell a good rationale from a fluent one.
_MIN_PROSE_LENGTH = 20


class MappingError(ValueError):
    """The mapping manifest could not be read at all.

    Distinct from a finding: a finding says the mapping is wrong, this says
    there is nothing to have an opinion about.
    """


@dataclass(frozen=True)
class Finding:
    """One defect, located precisely enough to fix without searching."""

    code: str
    where: str
    message: str
    warning: bool = False

    def render(self) -> str:
        level = "WARN " if self.warning else "ERROR"
        return f"{level} {self.code} {self.where}: {self.message}"


# --- strict JSON ------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """Refuse a JSON object that names the same key twice.

    ``json.loads`` keeps the last value silently. In an artifact whose whole
    purpose is that every field is accounted for exactly once, a duplicate key
    is how one accounting quietly replaces another.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise MappingError(
                f"the mapping manifest names the key {key!r} twice in one object. JSON "
                "keeps the last value silently, so one of the two mappings you wrote is "
                "being discarded. Remove the duplicate"
            )
        seen.add(key)
    return dict(pairs)


def load_mapping_document(path: str | Path) -> dict:
    """Read a mapping manifest into plain data, or raise ``MappingError``."""
    path = Path(path)
    if not path.exists():
        raise MappingError(
            f"no mapping manifest at {path}. The delivery says what the files contain; "
            f"the mapping manifest says where it came from. Start from "
            f"examples/mapping/{MAPPING_FILENAME}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError) as exc:
        raise MappingError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MappingError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise MappingError(f"{path} must contain a JSON object at the top level")
    return raw


# --- small shared checks ----------------------------------------------------


def _closed_keys(
    obj: dict,
    allowed: Iterable[str],
    required: Iterable[str],
    where: str,
) -> list[Finding]:
    findings: list[Finding] = []
    allowed_set = set(allowed)
    # Underscore-prefixed keys are commentary. They are safe to allow precisely
    # because the closed-key rule is not what stops a smuggled default -- the
    # required-key and exhaustiveness checks are, and a comment cannot satisfy
    # either. A hand-editable artifact is much better off able to explain itself
    # in place, which is why the delivery manifest and its template do the same.
    present = {key for key in obj if not (isinstance(key, str) and key.startswith("_"))}
    for key in sorted(present - allowed_set):
        findings.append(
            Finding(
                code=UNKNOWN_KEY,
                where=f"{where}.{key}",
                message=(
                    f"{key!r} is not a key this schema defines. Unknown keys are refused "
                    f"rather than ignored: an ignored key is how a defaulted value arrives "
                    f"looking like a declared one. Permitted here: {sorted(allowed_set)}"
                ),
            )
        )
    for key in required:
        if key not in obj:
            findings.append(
                Finding(
                    code=MISSING_KEY,
                    where=f"{where}.{key}",
                    message=f"required key {key!r} is missing",
                )
            )
    return findings


def _check_prose(value: object, where: str, *, code: str) -> list[Finding]:
    if not isinstance(value, str):
        return [
            Finding(
                code=BAD_TYPE,
                where=where,
                message=f"must be a string, got {type(value).__name__}",
            )
        ]
    stripped = value.strip()
    if stripped.lower() in _PLACEHOLDERS:
        return [
            Finding(
                code=PLACEHOLDER_VALUE,
                where=where,
                message=(
                    f"{stripped!r} is a placeholder, not an answer. This field is here "
                    "because the decision it records cannot be inferred from anything else "
                    "in the delivery"
                ),
            )
        ]
    if len(stripped) < _MIN_PROSE_LENGTH:
        return [
            Finding(
                code=code,
                where=where,
                message=(
                    f"is {len(stripped)} characters, below the {_MIN_PROSE_LENGTH}-character "
                    "floor. The floor catches an empty answer; it cannot tell a good "
                    "explanation from a fluent one, so a reviewer still has to read this"
                ),
            )
        ]
    return []


def _check_identifier(value: object, where: str) -> list[Finding]:
    if not isinstance(value, str):
        return [
            Finding(
                code=BAD_TYPE,
                where=where,
                message=f"must be a string, got {type(value).__name__}",
            )
        ]
    stripped = value.strip()
    if stripped.lower() in _PLACEHOLDERS:
        return [
            Finding(
                code=PLACEHOLDER_VALUE,
                where=where,
                message=f"{stripped!r} is a placeholder, not an identifier",
            )
        ]
    return []


def _check_role(value: object, where: str) -> list[Finding]:
    findings = _check_identifier(value, where)
    if findings or not isinstance(value, str):
        return findings
    lowered = value.strip().lower()
    offenders = [marker for marker in _PERSONAL_MARKERS if marker in lowered]
    if offenders:
        return [
            Finding(
                code=PERSONAL_IDENTIFIER,
                where=where,
                message=(
                    f"contains {offenders!r}, which makes it a person or an address rather "
                    "than a role. Record the accountable role -- 'Director of Audience "
                    "Analytics' -- and put the person in your own tracker. A role stays "
                    "accurate across the staff change that makes a name stale, and this "
                    "artifact is meant to be shareable"
                ),
            )
        ]
    return []


_SOURCE_KEYS = ("system", "relation", "column")


def _check_source_ref(value: object, where: str) -> list[Finding]:
    if not isinstance(value, dict):
        return [
            Finding(
                code=BAD_TYPE,
                where=where,
                message=(
                    "must be an object naming the source: "
                    "{'system': ..., 'relation': ..., 'column': ...}"
                ),
            )
        ]
    findings = _closed_keys(value, _SOURCE_KEYS, _SOURCE_KEYS, where)
    for key in _SOURCE_KEYS:
        if key in value:
            findings.extend(_check_identifier(value[key], f"{where}.{key}"))
    return findings


_IMPL_REQUIRED = ("path", "sha256")
_IMPL_ALLOWED = ("path", "sha256", "symbol")


def _check_implementation(
    value: object, where: str, *, bundle_root: Path | None
) -> tuple[list[Finding], str | None]:
    """Check an implementation reference. Returns findings and the cited path."""
    if not isinstance(value, dict):
        return (
            [
                Finding(
                    code=BAD_TYPE,
                    where=where,
                    message="must be an object: {'path': ..., 'sha256': ...}",
                )
            ],
            None,
        )
    findings = _closed_keys(value, _IMPL_ALLOWED, _IMPL_REQUIRED, where)
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        findings.append(
            Finding(
                code=BAD_TYPE,
                where=f"{where}.path",
                message="must be a non-empty path, relative to the adapter bundle root",
            )
        )
        return findings, None
    cited = raw_path.strip()
    candidate = Path(cited)
    if candidate.is_absolute() or ".." in candidate.parts:
        findings.append(
            Finding(
                code=IMPLEMENTATION_PATH_ESCAPES,
                where=f"{where}.path",
                message=(
                    f"{cited!r} is absolute or reaches outside the bundle. Implementation "
                    "paths are relative to the adapter bundle root so the reference still "
                    "resolves on the machine that reviews it"
                ),
            )
        )
        return findings, cited
    digest = value.get("sha256")
    if isinstance(digest, str):
        normalised = digest.strip().lower()
        if len(normalised) != 64 or any(ch not in "0123456789abcdef" for ch in normalised):
            findings.append(
                Finding(
                    code=BAD_TYPE,
                    where=f"{where}.sha256",
                    message="must be a 64-character lowercase hex SHA-256 digest",
                )
            )
        elif bundle_root is not None:
            findings.extend(_check_bundle_file(bundle_root, cited, normalised, f"{where}"))
    elif "sha256" in value:
        findings.append(
            Finding(
                code=BAD_TYPE,
                where=f"{where}.sha256",
                message=f"must be a string, got {type(digest).__name__}",
            )
        )
    if "symbol" in value:
        findings.extend(_check_identifier(value["symbol"], f"{where}.symbol"))
    return findings, cited


def _check_bundle_file(bundle_root: Path, cited: str, digest: str, where: str) -> list[Finding]:
    target = bundle_root / cited
    if not target.is_file():
        return [
            Finding(
                code=IMPLEMENTATION_FILE_MISSING,
                where=f"{where}.path",
                message=(
                    f"{cited!r} is not a file under the adapter bundle. A mapping that cites "
                    "code nobody can open is a claim, not a record -- and citing a file that "
                    "does not exist is the cheapest way to make an unimplemented derivation "
                    "look implemented"
                ),
            )
        ]
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != digest:
        return [
            Finding(
                code=IMPLEMENTATION_HASH_MISMATCH,
                where=f"{where}.sha256",
                message=(
                    f"{cited!r} hashes to {actual[:12]}… but the mapping records "
                    f"{digest[:12]}…. The file changed after the mapping was written, so the "
                    "rationale beside it describes code that is no longer there. Re-hash it "
                    "and re-read the rationale before you do"
                ),
            )
        ]
    return []


# --- per-field resolution ---------------------------------------------------

_RENAME_ALLOWED = ("outcome", "source", "implementation", "rationale")
_RENAME_REQUIRED = ("outcome", "source", "implementation", "rationale")
_DERIVE_ALLOWED = ("outcome", "inputs", "implementation", "rationale")
_DERIVE_REQUIRED = ("outcome", "inputs", "implementation", "rationale")
_GAP_ALLOWED = ("outcome", "owner", "blocker")
_GAP_REQUIRED = ("outcome", "owner", "blocker")
_OWNER_KEYS = ("role", "tracking_ref")


@dataclass
class _FieldContext:
    where: str
    table_available: bool
    table_required: bool
    bundle_root: Path | None


def _check_field_resolution(
    entry: object, ctx: _FieldContext
) -> tuple[list[Finding], str | None, str | None]:
    """Check one field's resolution record.

    Returns the findings, the outcome (or ``None`` if unreadable) and the cited
    implementation path, so the caller can report shared implementations.
    """
    where = ctx.where
    if not isinstance(entry, dict):
        return (
            [
                Finding(
                    code=BAD_TYPE,
                    where=where,
                    message=(
                        "must be an object carrying an 'outcome'. A null, an empty object or "
                        "a bare string is not an outcome, and treating one as 'absent' is "
                        "exactly the silent default this artifact refuses"
                    ),
                )
            ],
            None,
            None,
        )
    outcome = entry.get("outcome")
    if outcome not in OUTCOMES:
        return (
            [
                Finding(
                    code=INVALID_OUTCOME,
                    where=f"{where}.outcome",
                    message=(
                        f"{outcome!r} is not one of {list(OUTCOMES)}. Every contract field "
                        "resolves to exactly one of those four. There is deliberately no "
                        "fifth outcome for a value you decided to assume"
                    ),
                )
            ],
            None,
            None,
        )

    findings: list[Finding] = []
    impl_path: str | None = None

    if outcome == OUTCOME_DECLARE_ABSENT:
        extra = sorted(set(entry) - {"outcome"})
        if extra:
            findings.append(
                Finding(
                    code=ABSENT_WITH_EXTRA_KEYS,
                    where=where,
                    message=(
                        f"declare_absent carries {extra}, which contradicts itself: a field "
                        "with a source is not absent. The table's availability record is the "
                        "explanation; the field record only says which fields it covers"
                    ),
                )
            )
        if ctx.table_required:
            findings.append(
                Finding(
                    code=ABSENT_IN_REQUIRED_TABLE,
                    where=where,
                    message=(
                        "declare_absent is not available for a required table. The contract's "
                        "absence mechanism covers the three optional inputs only. If a "
                        "required field is genuinely unobtainable the outcome is 'gap', which "
                        "names an owner instead of closing the question"
                    ),
                )
            )
        elif ctx.table_available:
            findings.append(
                Finding(
                    code=ABSENT_IN_AVAILABLE_TABLE,
                    where=where,
                    message=(
                        "this field is declare_absent but its table's availability says "
                        "'available'. Absence is a property of the whole input, not of one "
                        "column: either the table is not delivered, or this field has a "
                        "source. Fix whichever statement is wrong"
                    ),
                )
            )
        return findings, outcome, None

    if not ctx.table_available:
        findings.append(
            Finding(
                code=PRESENT_IN_UNAVAILABLE_TABLE,
                where=where,
                message=(
                    f"resolves to {outcome!r} but its table is declared not delivered. Every "
                    "field of an undelivered input is declare_absent; a mapped field inside "
                    "one means the input is actually available"
                ),
            )
        )

    if outcome == OUTCOME_RENAME:
        findings.extend(_closed_keys(entry, _RENAME_ALLOWED, _RENAME_REQUIRED, where))
        if "source" in entry:
            findings.extend(_check_source_ref(entry["source"], f"{where}.source"))
        if "rationale" in entry:
            findings.extend(
                _check_prose(entry["rationale"], f"{where}.rationale", code=MISSING_RATIONALE)
            )
        if "implementation" in entry:
            impl_findings, impl_path = _check_implementation(
                entry["implementation"], f"{where}.implementation", bundle_root=ctx.bundle_root
            )
            findings.extend(impl_findings)
        elif "source" in entry:
            findings.append(
                Finding(
                    code=MISSING_IMPLEMENTATION,
                    where=where,
                    message="a rename still has code behind it; cite the file that emits it",
                )
            )
    elif outcome == OUTCOME_DERIVE:
        findings.extend(_closed_keys(entry, _DERIVE_ALLOWED, _DERIVE_REQUIRED, where))
        inputs = entry.get("inputs")
        if isinstance(inputs, list):
            if not inputs:
                findings.append(
                    Finding(
                        code=DERIVE_WITHOUT_INPUTS,
                        where=f"{where}.inputs",
                        message=(
                            "a derivation with no inputs computes this field out of nothing. "
                            "Name at least one source it reads"
                        ),
                    )
                )
            for index, item in enumerate(inputs):
                findings.extend(_check_source_ref(item, f"{where}.inputs[{index}]"))
        elif "inputs" in entry:
            findings.append(
                Finding(
                    code=BAD_TYPE,
                    where=f"{where}.inputs",
                    message="must be a list of source references",
                )
            )
        if "rationale" in entry:
            findings.extend(
                _check_prose(entry["rationale"], f"{where}.rationale", code=MISSING_RATIONALE)
            )
        if "implementation" in entry:
            impl_findings, impl_path = _check_implementation(
                entry["implementation"], f"{where}.implementation", bundle_root=ctx.bundle_root
            )
            findings.extend(impl_findings)
    elif outcome == OUTCOME_GAP:
        findings.extend(_closed_keys(entry, _GAP_ALLOWED, _GAP_REQUIRED, where))
        if "owner" not in entry:
            findings.append(
                Finding(
                    code=GAP_WITHOUT_OWNER,
                    where=where,
                    message=(
                        "a gap with no owner is an unassigned problem, which is how it stops "
                        "being anybody's. Name the accountable role and a reference into your "
                        "own tracker"
                    ),
                )
            )
        elif isinstance(entry["owner"], dict):
            owner = entry["owner"]
            findings.extend(_closed_keys(owner, _OWNER_KEYS, _OWNER_KEYS, f"{where}.owner"))
            if "role" in owner:
                findings.extend(_check_role(owner["role"], f"{where}.owner.role"))
            if "tracking_ref" in owner:
                findings.extend(
                    _check_identifier(owner["tracking_ref"], f"{where}.owner.tracking_ref")
                )
        else:
            findings.append(
                Finding(
                    code=BAD_TYPE,
                    where=f"{where}.owner",
                    message="must be an object: {'role': ..., 'tracking_ref': ...}",
                )
            )
        if "blocker" in entry:
            findings.extend(
                _check_prose(entry["blocker"], f"{where}.blocker", code=GAP_WITHOUT_BLOCKER)
            )

    return findings, outcome, impl_path


# --- table availability -----------------------------------------------------

_AVAILABILITY_ALLOWED = ("status", "available_from", "statement")


def _check_availability(
    entry: object, table: contract_spec.TableSpec, where: str
) -> tuple[list[Finding], bool]:
    """Check one optional table's availability record. Returns the availability."""
    if not isinstance(entry, dict):
        return (
            [
                Finding(
                    code=BAD_TYPE,
                    where=where,
                    message="must be an object carrying a 'status'",
                )
            ],
            False,
        )
    findings = _closed_keys(entry, _AVAILABILITY_ALLOWED, ("status",), where)
    status = entry.get("status")
    if status not in enums.AVAILABILITY_STATUSES:
        findings.append(
            Finding(
                code=INVALID_AVAILABILITY_STATUS,
                where=f"{where}.status",
                message=(
                    f"{status!r} is not one of {list(enums.AVAILABILITY_STATUSES)}. There is "
                    "deliberately no status meaning 'we have it but extracting it was hard': "
                    "the three statuses are facts about the deployment, and the engine "
                    "degrades differently for each"
                ),
            )
        )
        return findings, False

    available = status == enums.AVAILABILITY_AVAILABLE
    floor = entry.get("available_from")
    if available and floor is None:
        findings.append(
            Finding(
                code=AVAILABLE_WITHOUT_FLOOR,
                where=f"{where}.available_from",
                message=(
                    "an input declared 'available' must also say from when. Without a floor "
                    "date a window reaching back before the input existed reads real zeros, "
                    "which looks exactly like disengagement"
                ),
            )
        )
    if not available and floor is not None:
        findings.append(
            Finding(
                code=UNAVAILABLE_WITH_FLOOR,
                where=f"{where}.available_from",
                message=(
                    f"status is {status!r} but a coverage floor is declared. A floor describes "
                    "delivered coverage; an undelivered input has none"
                ),
            )
        )
    if not available:
        statement = entry.get("statement")
        if statement is None:
            findings.append(
                Finding(
                    code=MISSING_ABSENCE_STATEMENT,
                    where=f"{where}.statement",
                    message=(
                        f"declaring {table.name!r} {status!r} needs one sentence saying why. "
                        "This is the outcome a reviewer most needs to be able to disagree "
                        "with, and the lint cannot tell 'we do not have this product' from "
                        "'extracting this was inconvenient'"
                    ),
                )
            )
        else:
            findings.extend(
                _check_prose(statement, f"{where}.statement", code=MISSING_ABSENCE_STATEMENT)
            )
    return findings, available


# --- declarations -----------------------------------------------------------

_DECLARATION_KEYS = ("owner_role", "decision_ref")


def _check_declaration(entry: object, key: str, where: str) -> list[Finding]:
    """Check one required declaration's provenance record.

    The *value* is deliberately not recorded here. It lives in the delivery's own
    ``manifest.json``, and duplicating it would create two statements that can
    disagree. What this records is who decided and where the decision is written
    down -- the part the delivery manifest cannot carry.
    """
    if not isinstance(entry, dict):
        return [
            Finding(
                code=BAD_TYPE,
                where=where,
                message=(
                    "must be an object: {'owner_role': ..., 'decision_ref': ...}. The answer "
                    f"itself belongs in the delivery's {MANIFEST_FILENAME}; what belongs here "
                    "is who owns it"
                ),
            )
        ]
    findings = _closed_keys(entry, _DECLARATION_KEYS, _DECLARATION_KEYS, where)
    if "owner_role" in entry:
        findings.extend(_check_role(entry["owner_role"], f"{where}.owner_role"))
    if "decision_ref" in entry:
        findings.extend(_check_identifier(entry["decision_ref"], f"{where}.decision_ref"))
    return findings


# --- the report -------------------------------------------------------------


@dataclass(frozen=True)
class MappingReport:
    """The verdict, with enough detail to act on without re-running."""

    findings: tuple[Finding, ...]
    outcome_counts: dict[str, int]
    bundle_checked: bool

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.warning)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.warning)

    def passed(self, *, warnings_are_errors: bool = False) -> bool:
        if self.errors:
            return False
        return not (warnings_are_errors and self.warnings)

    def render(self, *, warnings_are_errors: bool = False) -> str:
        lines = [
            f"mapping manifest: {contract_spec.CONTRACT_NAME} {contract_spec.CONTRACT_VERSION}",
            "adapter bundle: " + ("checked" if self.bundle_checked else "not supplied"),
            "outcomes: "
            + ", ".join(f"{name}={self.outcome_counts.get(name, 0)}" for name in OUTCOMES),
            "",
        ]
        for finding in self.findings:
            lines.append(finding.render())
        if self.findings:
            lines.append("")
        n_err = len(self.errors)
        n_warn = len(self.warnings)
        if self.passed(warnings_are_errors=warnings_are_errors):
            verdict = f"PASS: every contract field is accounted for ({n_warn} warning(s))"
        else:
            verdict = f"FAIL: {n_err} error(s), {n_warn} warning(s)"
        lines.append(verdict)
        return "\n".join(lines)


def lint_mapping(
    document: dict,
    *,
    bundle_root: str | Path | None = None,
) -> MappingReport:
    """Check a mapping manifest against the contract's own table definitions.

    ``bundle_root`` enables the traceability checks: every cited implementation
    file must exist under it and hash to the digest recorded beside it. Without
    it the mapping's claims about code are unfalsifiable, which is why its
    absence is reported as a warning rather than passing quietly.
    """
    findings: list[Finding] = []
    root = Path(bundle_root) if bundle_root is not None else None
    if root is not None and not root.is_dir():
        raise MappingError(f"adapter bundle root is not a directory: {root}")

    findings.extend(
        _closed_keys(
            document,
            ("schema_version", "contract", "submission", "adapter", "declarations", "tables"),
            ("schema_version", "contract", "declarations", "tables"),
            "<root>",
        )
    )

    version = document.get("schema_version")
    if version != MAPPING_SCHEMA_VERSION:
        findings.append(
            Finding(
                code=UNKNOWN_SCHEMA_VERSION,
                where="<root>.schema_version",
                message=(
                    f"is {version!r}; this build reads mapping schema "
                    f"{MAPPING_SCHEMA_VERSION}. A mapping written against another shape of "
                    "this file cannot be checked against this one"
                ),
            )
        )

    contract_block = document.get("contract")
    if isinstance(contract_block, dict):
        findings.extend(
            _closed_keys(contract_block, ("name", "version"), ("name", "version"), "contract")
        )
        name = contract_block.get("name")
        declared_version = contract_block.get("version")
        if name is not None and name != contract_spec.CONTRACT_NAME:
            findings.append(
                Finding(
                    code=CONTRACT_MISMATCH,
                    where="contract.name",
                    message=(f"is {name!r}, this build implements {contract_spec.CONTRACT_NAME!r}"),
                )
            )
        if declared_version is not None and declared_version != contract_spec.CONTRACT_VERSION:
            findings.append(
                Finding(
                    code=CONTRACT_MISMATCH,
                    where="contract.version",
                    message=(
                        f"is {declared_version!r}, this build implements "
                        f"{contract_spec.CONTRACT_VERSION!r}. The field list this mapping "
                        "accounts for is not the field list being checked"
                    ),
                )
            )
    elif "contract" in document:
        findings.append(
            Finding(
                code=BAD_TYPE,
                where="contract",
                message="must be an object: {'name': ..., 'version': ...}",
            )
        )

    # --- declarations: all four, none defaulted ---
    declarations = document.get("declarations")
    if isinstance(declarations, dict):
        for key in DECLARATION_KEYS:
            if key not in declarations:
                findings.append(
                    Finding(
                        code=MISSING_DECLARATION,
                        where=f"declarations.{key}",
                        message=(
                            f"{key!r} is one of the {len(DECLARATION_KEYS)} declarations the "
                            "contract refuses to default, and it is unaccounted for here. "
                            "There is no state meaning 'we used the obvious value': every "
                            "plausible default is wrong for somebody and wrong invisibly, "
                            "which is why it is required. See "
                            "docs/declarations-questionnaire.md for the question and who "
                            "owns the answer"
                        ),
                    )
                )
            else:
                findings.extend(_check_declaration(declarations[key], key, f"declarations.{key}"))
        for key in sorted(set(declarations) - set(DECLARATION_KEYS)):
            findings.append(
                Finding(
                    code=UNKNOWN_DECLARATION,
                    where=f"declarations.{key}",
                    message=(
                        f"{key!r} is not one of the contract's required declarations: "
                        f"{list(DECLARATION_KEYS)}"
                    ),
                )
            )
    elif "declarations" in document:
        findings.append(
            Finding(
                code=BAD_TYPE,
                where="declarations",
                message=f"must be an object keyed by {list(DECLARATION_KEYS)}",
            )
        )

    # --- tables: every table once, every field once ---
    outcome_counts: dict[str, int] = {name: 0 for name in OUTCOMES}
    implementation_users: dict[str, list[str]] = {}
    absent_inputs: list[str] = []
    gaps: list[str] = []

    tables = document.get("tables")
    if isinstance(tables, dict):
        for table in contract_spec.TABLES:
            if table.name not in tables:
                findings.append(
                    Finding(
                        code=MISSING_TABLE,
                        where=f"tables.{table.name}",
                        message=(
                            f"the contract defines {table.name!r} and this mapping does not "
                            f"account for it. {'Required' if table.required else 'Optional'} "
                            "inputs both need a record: an optional input you do not deliver "
                            "is declared absent, which is a different statement from being "
                            "left out"
                        ),
                    )
                )
                continue
            table_findings, available = _check_table(
                tables[table.name],
                table,
                bundle_root=root,
                outcome_counts=outcome_counts,
                implementation_users=implementation_users,
                gaps=gaps,
            )
            findings.extend(table_findings)
            if not table.required and not available:
                absent_inputs.append(table.name)
        for name in sorted(set(tables) - {t.name for t in contract_spec.TABLES}):
            findings.append(
                Finding(
                    code=UNKNOWN_TABLE,
                    where=f"tables.{name}",
                    message=(
                        f"{name!r} is not a table in this contract. Permitted: "
                        f"{[t.name for t in contract_spec.TABLES]}"
                    ),
                )
            )
    elif "tables" in document:
        findings.append(
            Finding(
                code=BAD_TYPE,
                where="tables",
                message="must be an object keyed by contract table name",
            )
        )

    # --- warnings: the things a reviewer has to judge ---
    if root is None:
        findings.append(
            Finding(
                code=WARN_NO_ADAPTER_BUNDLE,
                where="<root>",
                message=(
                    "no adapter bundle was supplied, so every claim about implementing code "
                    "went unchecked. Re-run with --adapter-bundle before treating this pass "
                    "as evidence"
                ),
                warning=True,
            )
        )
    for name in absent_inputs:
        findings.append(
            Finding(
                code=WARN_OPTIONAL_INPUT_ABSENT,
                where=f"tables.{name}",
                message=(
                    f"{name!r} is declared not delivered. This is a supported outcome and the "
                    "run will select a named alternate feature set -- but whether the input "
                    "is genuinely unavailable or merely awkward to extract is not something "
                    "any check here can see. A reviewer decides"
                ),
                warning=True,
            )
        )
    for where in gaps:
        findings.append(
            Finding(
                code=WARN_GAP_PRESENT,
                where=where,
                message=(
                    "a well-formed gap is still an unmapped field. The mapping is complete as "
                    "a disclosure and the delivery is not complete as data"
                ),
                warning=True,
            )
        )
    for path, users in sorted(implementation_users.items()):
        # Counting citations would flag every well-organised adapter: one query
        # per table legitimately emits that whole table's columns, which is most
        # of them. What is actually a smell is one file cited across many
        # *tables* -- the shape a mapping takes when every entry was pointed at
        # the export script and the rationales written afterwards.
        tables_cited = {user.split(".")[1] for user in users if user.startswith("tables.")}
        if len(tables_cited) > 2:
            findings.append(
                Finding(
                    code=WARN_SHARED_IMPLEMENTATION,
                    where=path,
                    message=(
                        f"is cited by fields in {len(tables_cited)} different tables "
                        f"({sorted(tables_cited)}). One file emitting one table's columns is "
                        "normal and is not flagged; one file behind several tables is either a "
                        "genuinely shared transform worth reading, or the signature of "
                        "rationales attached to whatever code was to hand"
                    ),
                    warning=True,
                )
            )

    return MappingReport(
        findings=tuple(findings),
        outcome_counts=outcome_counts,
        bundle_checked=root is not None,
    )


def _check_table(
    entry: object,
    table: contract_spec.TableSpec,
    *,
    bundle_root: Path | None,
    outcome_counts: dict[str, int],
    implementation_users: dict[str, list[str]],
    gaps: list[str],
) -> tuple[list[Finding], bool]:
    where = f"tables.{table.name}"
    if not isinstance(entry, dict):
        return (
            [Finding(code=BAD_TYPE, where=where, message="must be an object with a 'fields' map")],
            False,
        )

    allowed = ("fields",) if table.required else ("availability", "fields")
    required = ("fields",) if table.required else ("availability", "fields")
    findings = _closed_keys(entry, allowed, required, where)

    available = True
    if table.required:
        if "availability" in entry:
            findings.append(
                Finding(
                    code=REQUIRED_TABLE_NOT_AVAILABLE,
                    where=f"{where}.availability",
                    message=(
                        f"{table.name!r} is a required input, so it has no availability record "
                        "to make. The contract's availability mechanism -- and the engine's "
                        "degradation for it -- is defined for the three optional inputs only. "
                        "A required input is delivered or the delivery does not conform"
                    ),
                )
            )
    elif "availability" in entry:
        availability_findings, available = _check_availability(
            entry["availability"], table, f"{where}.availability"
        )
        findings.extend(availability_findings)

    fields = entry.get("fields")
    if not isinstance(fields, dict):
        if "fields" in entry:
            findings.append(
                Finding(
                    code=BAD_TYPE,
                    where=f"{where}.fields",
                    message="must be an object keyed by contract field name",
                )
            )
        return findings, available

    for field_spec in table.fields:
        field_where = f"{where}.fields.{field_spec.name}"
        if field_spec.name not in fields:
            findings.append(
                Finding(
                    code=MISSING_FIELD,
                    where=field_where,
                    message=(
                        f"the contract defines {field_spec.name!r} on {table.name!r} and this "
                        "mapping does not say where it came from. Every field gets exactly one "
                        "of the four outcomes; leaving one out is the only way to smuggle in a "
                        "value nobody decided"
                    ),
                )
            )
            continue
        field_findings, outcome, impl_path = _check_field_resolution(
            fields[field_spec.name],
            _FieldContext(
                where=field_where,
                table_available=available,
                table_required=table.required,
                bundle_root=bundle_root,
            ),
        )
        findings.extend(field_findings)
        if outcome is not None:
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            if outcome == OUTCOME_GAP:
                gaps.append(field_where)
        if impl_path is not None:
            implementation_users.setdefault(impl_path, []).append(field_where)

    for name in sorted(set(fields) - set(table.field_names)):
        findings.append(
            Finding(
                code=UNKNOWN_FIELD,
                where=f"{where}.fields.{name}",
                message=(
                    f"{name!r} is not a field of {table.name!r}. The validator refuses an "
                    "extra column in the delivery for the same reason: an extra field is how "
                    "a vendor-shaped table arrives one column at a time"
                ),
            )
        )

    return findings, available


# --- command line -----------------------------------------------------------

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_UNTRUSTED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-lint-mapping",
        description=(
            "Check a mapping manifest: every contract field accounted for exactly once, "
            "every absence declared rather than assumed, every gap owned. Structural only -- "
            "it cannot tell you whether a source column means what the mapping claims."
        ),
    )
    parser.add_argument(
        "mapping",
        help=f"path to the {MAPPING_FILENAME}, or a directory containing one",
    )
    parser.add_argument(
        "--adapter-bundle",
        default=None,
        help=(
            "root of the adapter source, so cited implementation files can be checked to "
            "exist and to hash to the digest recorded beside them"
        ),
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="fail on warnings too. Warnings mark what a human still has to judge",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit findings as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.mapping)
    if path.is_dir():
        path = path / MAPPING_FILENAME
    try:
        document = load_mapping_document(path)
        report = lint_mapping(document, bundle_root=args.adapter_bundle)
    except MappingError as exc:
        print(f"mapping manifest could not be read: {exc}", file=sys.stderr)
        return EXIT_UNTRUSTED
    if args.as_json:
        print(
            json.dumps(
                {
                    "passed": report.passed(warnings_are_errors=args.warnings_as_errors),
                    "outcome_counts": report.outcome_counts,
                    "bundle_checked": report.bundle_checked,
                    "findings": [
                        {
                            "code": f.code,
                            "where": f.where,
                            "message": f.message,
                            "warning": f.warning,
                        }
                        for f in report.findings
                    ],
                },
                indent=2,
            )
        )
    else:
        print(report.render(warnings_are_errors=args.warnings_as_errors))
    return EXIT_OK if report.passed(warnings_are_errors=args.warnings_as_errors) else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
