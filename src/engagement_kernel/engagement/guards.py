"""The two guards that decide what may become a model feature.

Everything else in this package computes numbers. This module decides which
numbers are allowed to reach a model at all, and it is the reason the rest can
be trusted: port the feature and model layers without these and the easiest
outcome is a run that produces plausible, wrong clusters -- clusters of
subscription status wearing the name of a behavioural segment.

There are two layers, at two different depths, and they are not redundant.

**The input guard** (:func:`assert_no_forbidden_inputs`) runs at the atomic
layer, on the daily frames as they arrive. It is pattern-only and deliberately
vocabulary-independent: it refuses a column whose *name* says it carries scroll
depth, an email open, an email send or a share widget, whatever the publisher
calls the rest of its columns. This layer catches an adapter that widened a
daily table.

**The model guard** (:func:`assert_no_forbidden_model_columns`) runs at the
matrix, on the assembled feature columns. It has to catch two different classes
of thing, and that is why it has both a name list and a pattern list:

* *patterns* catch families -- anything with ``scroll``, ``open``, ``send``, a
  flag suffix, a rate suffix, an outcome word;
* *names* catch specific columns whose names say nothing suspicious. The
  load-bearing example is subscription state. ``state`` matches no pattern any
  reviewer would write down, it is a perfectly ordinary column name, and it is
  the single most damaging thing that could reach this matrix.

The asymmetry matters for testing this file, and it is the reason the negative
controls in ``tests/test_engagement_guards.py`` are shaped the way they are. The
pattern list survives translation from one publisher's vocabulary to another
untouched -- ``*scroll*`` means the same thing everywhere. **The name list does
not.** The system this ports from named these columns in its own vocabulary, and
a literal copy of that list could never match a column produced from this
contract: it would load, it would run on every build, and it would match nothing
forever. A control that seeds only scroll, sends and opens exercises the
patterns and would pass against exactly that dead name list. Seeding a
subscription-state column is the control that proves the names were translated
rather than copied.

So the name list is not written out here at all. It is **derived** from the
contract and the intermediate tables:

* every field name the contract declares, because no raw input field is ever a
  model feature -- they are keys, instants, enums and states, and the features
  are derived names like ``web_views_28d``;
* every deduplication-key column of the intermediate tables, for the same
  reason;
* a short declared set of things this lane itself computes that must stay
  profile-only or metadata.

Deriving the first two means a field added to the contract is refused by this
guard on the day it lands, rather than on the day somebody remembers to add it
here.

One thing the guard does **not** refuse deserves stating, because it looks like a
hole and is not. ``topic_entropy_28d`` is a forbidden name; the standardised
surface dimension ``z__topic_entropy_28d`` is permitted. The distinction is about
what else is in the matrix. In the block-weighted construction the topic block
already carries a share per bucket, and entropy is a deterministic function of
exactly those shares -- so as a block feature it adds no information and spends a
second block weight on the same signal. The intensity and joint surfaces carry no
bucket shares at all, so there entropy is the only breadth-of-taste dimension and
it is a declared, validated part of the surface. The raw atomic stays refused
either way, which is what stops it arriving by accident.

Both matrix constructions in this package are guarded. That is a deliberate
change from the system this ports from, where the guard ran inside the
block-weighted builder while the surface that was actually frozen and published
was assembled by a different function that never called it -- so on the live
configuration the model guard protected a matrix nobody shipped.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from engagement_kernel.contract import spec
from engagement_kernel.intermediate import tables

# --- the input guard --------------------------------------------------------

#: Substrings that must never appear in a daily frame feeding the atomic layer.
#:
#: Pattern-only and deliberately so: this layer runs before any vocabulary
#: translation, on whatever an adapter produced, so a name-based rule would be
#: guessing at the adapter's naming. Each entry is a family the contract has
#: already ruled out:
#:
#: ``scroll``
#:     Declared out of scope by the contract (:data:`spec.SCROLL_DEPTH_SCOPE_NOTE`).
#:     Not measurable on app surfaces, so a mixed-surface deployment would
#:     compare a real number against a hardcoded zero.
#: ``open``
#:     Email opens are reachability only. Machine opens inflate them and cannot
#:     be cleaned out, so an open says a message reached a live inbox and
#:     nothing about the reader.
#: ``send``
#:     A send measures the publisher's behaviour, not the reader's.
#: ``share``
#:     A share-widget count measures a button, and on the surfaces where it was
#:     measured it was read by nothing.
FORBIDDEN_INPUT_PATTERNS: tuple[str, ...] = ("scroll", "open", "send", "share")


class ForbiddenInput(ValueError):
    """A daily frame carried a column that must not reach the atomic layer."""


def assert_no_forbidden_inputs(columns: object, *, where: str) -> None:
    """Refuse a daily frame whose columns name a forbidden signal family.

    ``where`` names the frame in the message. A guard that says only "forbidden
    column" leaves whoever hits it grepping four builders to find which one.
    """
    offenders: list[tuple[str, str]] = []
    for column in columns:
        lowered = str(column).lower()
        for pattern in FORBIDDEN_INPUT_PATTERNS:
            if pattern in lowered:
                offenders.append((str(column), pattern))
                break
    if offenders:
        detail = ", ".join(f"{name!r} (contains {pattern!r})" for name, pattern in offenders)
        raise ForbiddenInput(
            f"{where} carries columns that must never enter the atomic layer: {detail}. "
            "These signals are excluded by the contract, not filtered later: a column "
            "that reaches the atomics is one standardisation away from being a feature"
        )


# --- the model guard: patterns ----------------------------------------------

#: Column-name globs refused at the model matrix.
#:
#: Translated onto this contract's vocabulary rather than copied. Two entries
#: from the system this ports from are deliberately absent and one is renamed:
#:
#: * ``*share_button*`` and ``*30d_decayed*`` are gone. Neither is expressible
#:   against this contract -- there is no share-widget input and no decayed
#:   aggregate -- and a guard against a column that cannot exist is a rule whose
#:   only possible effect is to look reassuring.
#: * ``*unknown_section*`` becomes ``*unresolved*``, because that is what this
#:   build calls unresolved section metadata
#:   (:data:`engagement_kernel.intermediate.config.DEFAULT_UNRESOLVED_SECTION`).
#:   Keeping the old spelling would have left the rule matching nothing.
FORBIDDEN_MODEL_PATTERNS: tuple[str, ...] = (
    # Flags are diagnostics. An imputation flag in particular tells the model
    # which rows were imputed, which is the one thing neutral imputation exists
    # to keep out of the distance function.
    "*_imputed_flag",
    "*_flag",
    # Identity. Every key in this system ends in _id, and no derived feature
    # does, so this is a wide net over a space with nothing legitimate in it.
    "*_id",
    # Signal families the contract rules out. Kept as patterns as well as being
    # unbuildable, because an adapter or a future feature could name one.
    "*scroll*",
    "*open*",
    "*send*",
    # Subscription economics. Never behaviour: a cluster of these is a cluster
    # of the billing system.
    "*entitle*",
    "*payer*",
    "*revenue*",
    "*renewal*",
    "*payment*",
    # Outcomes. A feature carrying the outcome makes every downstream validation
    # tautological.
    "*churn*",
    "*retention*",
    "*tenure*",
    # Rates whose denominators are not carried alongside them.
    "*_rate",
    # Granular per-section shares are a profile surface. They are high-cardinality
    # and publisher-specific, so they would dominate a distance function with a
    # taxonomy rather than a behaviour.
    "section_share_*",
    # Unresolved metadata is a coverage measure, not a preference.
    "*unresolved*",
)


# --- the model guard: names, derived ----------------------------------------


def _contract_field_names() -> frozenset[str]:
    """Every field name the contract declares.

    None is ever a model feature: they are keys, instants, enums and commercial
    states, and the model's columns are derived names. Deriving this set means a
    field added to the contract is refused here without anyone editing this file.
    """
    return frozenset(name for table in spec.TABLES for name in table.field_names)


def _intermediate_key_names() -> frozenset[str]:
    """Every deduplication-key column of the intermediate tables.

    Grain keys, by definition: a model column equal to one of these would be
    clustering on the shape of the table rather than on the reader.
    """
    return frozenset(name for table in tables.OUTPUTS for name in table.dedup_key)


#: Columns this lane itself computes that must never be model features.
#:
#: The only hand-written part of the name list, because these names exist nowhere
#: else to derive them from. Each one is a number this package produces on
#: purpose and publishes, and each would be wrong as a clustering input:
#:
#: ``as_of_week_end``
#:     The snapshot date. A model feature would be clustering on the calendar.
#: ``entitled_days_in_window``
#:     Spine metadata that decides who is scored, never how they behave.
#: ``state``, ``payer_type``
#:     Also in the contract-derived set. Named again here because these two are
#:     the whole reason the name list exists, and a reader of this file should
#:     not have to follow a derivation to find that out.
#: ``start_date``, ``end_date``, ``is_open``
#:     Subscription-span geometry from the intermediate build.
#: ``channel_entropy``, ``topic_entropy_28d``, ``top_bucket_share_28d``
#:     Profile-only summaries. Each is a deterministic function of shares that
#:     are already in the matrix, so as a feature it adds no information and
#:     double-counts the block it came from.
#: ``resolved_view_share_28d``
#:     A coverage measure. It moves with metadata quality, not with the reader.
#: the ``*_version`` names
#:     Provenance. A version is constant within a run, so it contributes nothing
#:     but would still consume a block weight.
DECLARED_FORBIDDEN_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "as_of_week_end",
        "entitled_days_in_window",
        "state",
        "payer_type",
        "start_date",
        "end_date",
        "is_open",
        "channel_entropy",
        "topic_entropy_28d",
        "top_bucket_share_28d",
        "resolved_view_share_28d",
        "contract_version",
        "feature_version",
        "model_version",
        "bucket_map_version",
    }
)


def forbidden_model_names() -> frozenset[str]:
    """The assembled name list: contract fields, grain keys, declared extras."""
    return _contract_field_names() | _intermediate_key_names() | DECLARED_FORBIDDEN_MODEL_NAMES


#: Which of the three sources a name came from, for the guard's message and for
#: the negative controls that have to prove the name list is doing work.
NAME_SOURCE_CONTRACT = "contract_field"
NAME_SOURCE_GRAIN_KEY = "intermediate_grain_key"
NAME_SOURCE_DECLARED = "declared_lane_column"


def _name_source(name: str) -> str | None:
    if name in _contract_field_names():
        return NAME_SOURCE_CONTRACT
    if name in _intermediate_key_names():
        return NAME_SOURCE_GRAIN_KEY
    if name in DECLARED_FORBIDDEN_MODEL_NAMES:
        return NAME_SOURCE_DECLARED
    return None


@dataclass(frozen=True)
class GuardFinding:
    """One refused column, and which rule refused it.

    The rule is carried, not just the verdict. A negative control that only
    asserts "this was rejected" cannot tell a working name list from a dead one
    when a pattern would have caught the column anyway -- so the controls assert
    on :attr:`rule` and this field is what makes that possible.
    """

    column: str
    #: ``"name"`` or ``"pattern"``.
    rule: str
    #: The pattern that matched, or the source the name was derived from.
    detail: str

    def __str__(self) -> str:
        return f"{self.column!r} refused by {self.rule} rule ({self.detail})"


class ForbiddenModelColumn(ValueError):
    """The model matrix carried a column that must never be a feature."""

    def __init__(self, findings: list[GuardFinding]) -> None:
        self.findings = findings
        detail = "; ".join(str(finding) for finding in findings)
        super().__init__(
            f"forbidden model-matrix columns: {detail}. These are refused rather than "
            "dropped: a matrix that quietly discards a column produces a model whose "
            "feature set nobody declared"
        )


def inspect_model_columns(columns: object) -> list[GuardFinding]:
    """Report every column that must not be a model feature, and why.

    Names are checked before patterns so a column caught by both is attributed to
    the name rule, which is the one whose translation cannot be assumed.
    """
    names = forbidden_model_names()
    findings: list[GuardFinding] = []
    for raw in columns:
        column = str(raw)
        if column in names:
            source = _name_source(column) or NAME_SOURCE_DECLARED
            findings.append(GuardFinding(column=column, rule="name", detail=source))
            continue
        for pattern in FORBIDDEN_MODEL_PATTERNS:
            if fnmatch.fnmatch(column, pattern):
                findings.append(GuardFinding(column=column, rule="pattern", detail=pattern))
                break
    return findings


def assert_no_forbidden_model_columns(columns: object) -> None:
    """Raise unless every column is permitted as a model feature."""
    findings = inspect_model_columns(columns)
    if findings:
        raise ForbiddenModelColumn(findings)
