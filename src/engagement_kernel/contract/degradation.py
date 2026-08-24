"""How the reference engine degrades when an optional input is missing.

The rule is one sentence: **an absent input selects a different feature set; it
never becomes a column of zeros.** Everything else here is bookkeeping in
service of that.

Filling a missing input with zeros is attractive because it keeps the matrix
rectangular, and it is wrong in a way that survives every check. A reader with
no community data is not a reader who never comments; a reader whose email feed
starts in the middle of the window is not a reader who stopped clicking. Zeros
say the second thing, models believe them, and the resulting clusters are
plausible. So instead: each optional input maps to a named feature block, the
blocks that are actually supported are resolved up front, and the run declares
which feature set it used.

Two absences are distinguished, because they degrade differently:

``not_deployed``
    The publisher does not deliver this input. The block is dropped for every
    window. Permanent, and a property of the deployment.

``not_yet_launched``
    The input exists now but the underlying product did not exist for part of
    the analysis period. The block is dropped only for windows that reach back
    past the floor, and supported for windows that do not. A run that ignores
    this reads real zeros out of a period when the product had not launched --
    which looks exactly like disengagement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from engagement_kernel.contract import enums
from engagement_kernel.contract.manifest import Manifest
from engagement_kernel.contract.spec import OPTIONAL_TABLES, REQUIRED_TABLES

#: Reasons a feature block can be unsupported. Each one is a different fact
#: about the deployment, and the run report carries whichever applies.
REASON_INPUT_NOT_DEPLOYED = "input_not_deployed"
REASON_INPUT_NOT_YET_LAUNCHED = "input_not_yet_launched"
REASON_WINDOW_PRECEDES_FLOOR = "window_precedes_availability_floor"
REASON_FILE_ABSENT = "declared_available_but_file_absent"


@dataclass(frozen=True)
class BlockOutcome:
    """Whether one feature block is supported, and if not, why not."""

    block: str
    source_table: str
    supported: bool
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FeatureSetPlan:
    """The feature set a run may use, given what the delivery actually contains."""

    feature_set_id: str
    outcomes: tuple[BlockOutcome, ...]

    @property
    def supported_blocks(self) -> tuple[str, ...]:
        return tuple(o.block for o in self.outcomes if o.supported)

    @property
    def dropped_blocks(self) -> tuple[BlockOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.supported)

    def describe(self) -> str:
        lines = [f"feature set: {self.feature_set_id}"]
        for outcome in self.outcomes:
            if outcome.supported:
                lines.append(f"  + {outcome.block} (from {outcome.source_table})")
            else:
                detail = f" -- {outcome.detail}" if outcome.detail else ""
                lines.append(f"  - {outcome.block}: {outcome.reason}{detail}")
        return "\n".join(lines)


#: Every optional feature block, and the short suffix that names its absence in
#: a feature-set id. Declared as data so the set of possible feature sets is
#: enumerable and documentable rather than emergent.
OPTIONAL_BLOCK_SUFFIXES: dict[str, str] = {
    "email_cadence": "no-email-cadence",
    "deliverability": "no-deliverability",
    "community": "no-community",
}

#: The feature-set id used when every optional block is supported.
FEATURE_SET_FULL = "full"


def _required_outcomes() -> tuple[BlockOutcome, ...]:
    return tuple(
        BlockOutcome(block=table.feature_block, source_table=table.name, supported=True)
        for table in REQUIRED_TABLES
    )


def feature_set_id(dropped_blocks: list[str]) -> str:
    """Deterministic id naming exactly which optional blocks are absent."""
    if not dropped_blocks:
        return FEATURE_SET_FULL
    ordered = [block for block in OPTIONAL_BLOCK_SUFFIXES if block in dropped_blocks]
    return "+".join(OPTIONAL_BLOCK_SUFFIXES[block] for block in ordered)


def resolve_feature_set(
    manifest: Manifest,
    *,
    present_tables: set[str] | None = None,
    window_start: date | None = None,
) -> FeatureSetPlan:
    """Decide which feature blocks a run may use.

    ``present_tables`` is which optional files were actually found; passing
    ``None`` trusts the manifest, which is what a planning call before a
    delivery arrives has to do. ``window_start`` is the earliest date the run
    will look at -- supply it and a ``not_yet_launched`` input is dropped only
    for the windows that actually reach back past its floor.
    """
    outcomes = list(_required_outcomes())
    dropped: list[str] = []

    for table in OPTIONAL_TABLES:
        availability = manifest.optional_inputs[table.name]
        block = table.feature_block
        if availability.status == enums.AVAILABILITY_NOT_DEPLOYED:
            outcomes.append(
                BlockOutcome(
                    block=block,
                    source_table=table.name,
                    supported=False,
                    reason=REASON_INPUT_NOT_DEPLOYED,
                    detail="the deployment does not deliver this input at all",
                )
            )
            dropped.append(block)
            continue
        if availability.status == enums.AVAILABILITY_NOT_YET_LAUNCHED:
            outcomes.append(
                BlockOutcome(
                    block=block,
                    source_table=table.name,
                    supported=False,
                    reason=REASON_INPUT_NOT_YET_LAUNCHED,
                    detail="the underlying product had not launched in the analysis period",
                )
            )
            dropped.append(block)
            continue
        if present_tables is not None and table.name not in present_tables:
            outcomes.append(
                BlockOutcome(
                    block=block,
                    source_table=table.name,
                    supported=False,
                    reason=REASON_FILE_ABSENT,
                    detail=(
                        "the manifest declares this input available but the file is not in "
                        "the delivery"
                    ),
                )
            )
            dropped.append(block)
            continue
        floor = availability.available_from
        if window_start is not None and floor is not None and window_start < floor:
            outcomes.append(
                BlockOutcome(
                    block=block,
                    source_table=table.name,
                    supported=False,
                    reason=REASON_WINDOW_PRECEDES_FLOOR,
                    detail=(
                        f"the window starts {window_start.isoformat()} but the input has no "
                        f"coverage before {floor.isoformat()}; reading that period as zero "
                        "would report a pre-launch gap as disengagement"
                    ),
                )
            )
            dropped.append(block)
            continue
        outcomes.append(BlockOutcome(block=block, source_table=table.name, supported=True))

    return FeatureSetPlan(feature_set_id=feature_set_id(dropped), outcomes=tuple(outcomes))


# --- what an adopter loses, rendered rather than restated --------------------

#: One sentence per optional feature block, saying what a run without it cannot
#: produce. Written here rather than in a document because a newsroom decides
#: whether to adopt this on the strength of these three sentences, and a table of
#: them maintained by hand in prose is a table that goes stale the first time a
#: block changes. ``render_optional_input_table`` emits the markdown; the doc
#: tests assert the emitted rows are what the documents actually carry, and that
#: every block declared in the contract has an entry here.
LOSS_BY_BLOCK: dict[str, str] = {
    "email_cadence": (
        "The loyalty block, whose one signal is how many of the last four weeks the "
        "reader clicked an email in. Habit shows up here and nowhere else: reading "
        "volume cannot distinguish a reader who returns weekly from one who arrived "
        "once and read a great deal. Clusters stay meaningful without it, and the "
        "returning-reader distinction gets weaker."
    ),
    "deliverability": (
        "Nothing in the model. Opens are deliberately not a model feature -- machine "
        "opens inflate them and cannot be cleaned out, so an open says a message "
        "reached a reachable inbox and nothing about interest. This input exists for "
        "reachability reporting, in its own table and its own block precisely so that "
        "'opens are never a feature' is structural rather than a promise. Omit it and "
        "the model is unchanged."
    ),
    "community": (
        "The community block: how many community actions the reader took in the "
        "window, and on how many distinct days. This is the clearest contribution "
        "signal in the model, so a deployment without it distinguishes heavy readers "
        "from participants less sharply. It is also the block most newsrooms will not "
        "have, which is why its absence is a first-class declaration rather than an "
        "obstacle."
    ),
}


def render_optional_input_table() -> str:
    """The optional inputs and what each one's absence costs, as a markdown table.

    Rows come from the contract's own table definitions, so an input added to or
    removed from the contract changes this table without anybody remembering to.
    """
    lines = [
        "| optional input | feature block | what a run without it loses |",
        "| --- | --- | --- |",
    ]
    for table in OPTIONAL_TABLES:
        loss = LOSS_BY_BLOCK.get(table.feature_block, "UNDOCUMENTED")
        lines.append(f"| `{table.name}` | `{table.feature_block}` | {loss} |")
    return "\n".join(lines)


def render_required_input_list() -> str:
    """The required inputs and their one-line purpose, as a markdown list."""
    lines = []
    for table in REQUIRED_TABLES:
        first_sentence = table.purpose.split(". ")[0].rstrip(".")
        lines.append(f"* **`{table.name}`** -- {first_sentence}.")
    return "\n".join(lines)
