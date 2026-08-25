"""Which numbers in this lane are the deployment's, and which are not.

Every quantitative choice in the lane sits in one of three groups, and this module
is the census. It exists because the alternative -- leaving the line implicit --
had a measurable cost: the repository's own docstrings said the thresholds were
placeholders a deployment should replace, while the only way to replace one was to
write Python against a pre-release library. An adopter reading that came away
believing the numbers were the engine's. They were not; nobody had built the way to
say so.

The three groups, and what each one commits to:

``PROMOTED``
    The deployment's, settable from a gates file with no Python. Shipping a default
    for one of these is a convenience, not a recommendation.

``FIXED``
    Deliberately not configurable. Each of these is either a definition that belongs
    to a model version rather than to a run, or an assertion that the code did what
    it says. A knob here would let a deployment turn off the thing the method rests
    on, or silence a check rather than fix what it caught.

``DEFERRED``
    Should be the deployment's and is not yet. Each entry carries the reason and what
    would change it. Naming these is the point: an unlisted prescription reads as a
    property of the method, and this lane's largest remaining one -- the 28-day
    window -- is exactly the sort a document elsewhere in this repository invited an
    adopter to change without the code being able to express it.

The census is rendered into ``docs/gate-configuration.md`` and a test asserts the
document carries it verbatim, so the two cannot drift. A second test resolves every
``where`` against the module it names, so an entry cannot survive the constant it
describes being renamed or deleted.
"""

from __future__ import annotations

from dataclasses import dataclass

from engagement_kernel.engagement.config import GateThresholds

PROMOTED_GROUP = "Promoted to configuration"
FIXED_GROUP = "Deliberately fixed"
DEFERRED_GROUP = "Deferred, with the reason"

TRIAGE_BEGIN_MARKER = "<!-- triage:begin -->"
TRIAGE_END_MARKER = "<!-- triage:end -->"


@dataclass(frozen=True)
class Parameter:
    """One quantitative choice, and where it lives."""

    #: Dotted path, ``module:attribute``, resolvable by the test that keeps this
    #: census honest. ``None`` for a group of related constants named by their
    #: module.
    where: str
    what: str
    note: str


PROMOTED: tuple[Parameter, ...] = (
    Parameter(
        where="engagement.config:GateThresholds.seed_ari",
        what="Seed-stability floor",
        note="Median pairwise agreement across starting points. The run reports the "
        "realised value beside it.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.cross_algorithm_ari_by_k",
        what="Cross-algorithm agreement bar, per candidate k",
        note="The one threshold that cannot honestly be inherited: chance agreement "
        "depends on your row count, your dimensionality and your population's own "
        "correlation structure. Derive it with tools/derive_cross_algorithm_bars.py.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.centroid_distinctness_corr",
        what="Centroid distinctness ceiling",
        note="Above this, two clusters are one cluster reported twice.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.tiny_cluster_floor",
        what="Smallest share a cluster may hold",
        note="Keep it equal to the persistence share, or a cluster can be both too "
        "small to matter and required to persist.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.major_cluster_share",
        what="Share at which a cluster must persist across seeds",
        note="The other half of the pair above.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.topic_coverage_floor",
        what="Resolved-reading share the topic taxonomy needs",
        note="Blocks the topic block, not the whole run.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.t4_retention",
        what="Label retention across a disjoint window",
        note="How much churn between two non-overlapping windows is acceptable in "
        "your audience, which is a question about your audience.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.t4_profile_similarity",
        what="Centroid profile correlation across a disjoint window",
        note="",
    ),
    Parameter(
        where="engagement.config:GateThresholds.selection_survival_floor",
        what="Survival-rate floor for a candidate k",
        note="How reproducible a verdict has to be before it counts. A majority, "
        "with confidence, is the shipped answer.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.selection_perturbation_draws",
        what="Perturbed panels per candidate k",
        note="Cost, not strictness: fewer draws is a noisier verdict, never a more permissive one.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.selection_perturbation_row_fraction",
        what="Rows dropped per perturbed panel",
        note="How small a change in the panel the verdict has to survive.",
    ),
    Parameter(
        where="engagement.config:GateThresholds.selection_rng_seed",
        what="Seed for the perturbation draws",
        note="Reproducibility, not strictness.",
    ),
    Parameter(
        where="engagement.config:LaneConfig.k_grid",
        what="Candidate cluster counts",
        note="Any set of counts from two upwards, contiguous or not. Two is a "
        "legitimate answer for a small or sharply split audience.",
    ),
    Parameter(
        where="engagement.config:LaneConfig.n_seeds",
        what="Starting points per candidate k",
        note="Cost, not strictness.",
    ),
    Parameter(
        where="engagement.config:LaneConfig.content_active_min_views",
        what="Resolved views before a reader has a topic mix",
        note="",
    ),
    Parameter(
        where="engagement.config:LaneConfig.content_active_min_sections",
        what="Distinct sections before a reader has a topic mix",
        note="Two is the floor the code enforces: at one, every reader with a single "
        "view has a mix that is 100% one bucket, which is not a preference.",
    ),
    Parameter(
        where="engagement.config:LaneConfig.z_clip",
        what="Standard deviations at which a standardised feature is clipped",
        note="Its default is calibration.Z_CLIP_DEFAULT.",
    ),
    Parameter(
        where="engagement.config:LaneConfig.panel_seed",
        what="Seed for the training-panel sample",
        note="Versioned with the model: the panel is the fitting population, so a "
        "different seed is a different model.",
    ),
    Parameter(
        where="engagement.config:BlockWeights",
        what="How much model distance each semantic block owns",
        note="Used only by the block-weighted construction.",
    ),
)

FIXED: tuple[Parameter, ...] = (
    Parameter(
        where="engagement.config:EMAIL_CLICK_UNIT",
        what="What one email-click row counts",
        note="A click event, not distinct campaigns clicked. A definition that belongs "
        "to a model version, not to a run: counting the other way is buildable and is "
        "a different model, so it would be a version change rather than a flag.",
    ),
    Parameter(
        where="engagement.guards:FORBIDDEN_INPUT_PATTERNS",
        what="Signals that may never be clustering inputs",
        note="Behaviour-only clustering is the method, not a setting. A configurable "
        "version of this rule is a way to turn off the thing the method rests on.",
    ),
    Parameter(
        where="engagement.selection:WILSON_Z",
        what="Confidence level of the survival bound",
        note="One-sided 95%, and fixed because it is a convention rather than a "
        "threshold about your data. The bar derivation uses the same 95%, so moving "
        "one without the other would leave the selection rule holding two different "
        "notions of confidence.",
    ),
    Parameter(
        where="engagement.matrix:VARIANCE_BOUNDS",
        what="Per-feature variance the assembled matrix must show",
        note="An assertion that standardisation did what it says, not a statement "
        "about readers: a z-scored column with a variance of 0.3 means the transform "
        "is wrong. Widening it would silence the check rather than fix what it caught.",
    ),
    Parameter(
        where="engagement.panel:PANEL_RULE",
        what="One reader-week per reader per calendar month",
        note="Part of what the fitting population is, so it travels with the model "
        "version alongside the panel seed rather than as a runtime knob.",
    ),
)

DEFERRED: tuple[Parameter, ...] = (
    Parameter(
        where="engagement.windows:TRAILING_WINDOW_DAYS",
        what="Width of the long feature window, 28 days",
        note="The largest remaining prescription in this lane, and it should be the "
        "deployment's. Deferred because the width is woven into the feature "
        "vocabulary itself -- feature names carry it, the weekly bins tile it, the "
        "temporal gate's disjoint gap is derived from it -- so promoting it means the "
        "feature names become generated rather than declared. Until then a newsroom "
        "on a different publishing cadence cannot express one, and no document should "
        "suggest otherwise.",
    ),
    Parameter(
        where="engagement.windows:WEEK_BIN_COUNT",
        what="Weekly bins the long window is cut into, 4",
        note="The same prescription as the window width and not separable from it: "
        "the bins have to tile the window exactly. It is also the disjoint gap the "
        "temporal gate requires, which used to be a bare 4 and a bare 28 written out "
        "again in the selection module -- one prescription stated in three places and "
        "revisable in none. Now derived from here.",
    ),
    Parameter(
        where="engagement.blocks:DENSE_MIN_EXPLAINED_VARIANCE",
        what="Block-quality thresholds, five of them",
        note="With SPARSE_MIN_EXPLAINED_VARIANCE, DENSE_MIN_ANCHOR_CORR, "
        "SPARSE_MIN_ANCHOR_CORR and REDUNDANCY_CORR_THRESHOLD. They decide how a "
        "semantic block is summarised rather than whether a result may be published, "
        "and they have never been measured on a second population. A knob offered "
        "before anyone has a second measurement is a knob with no basis for turning "
        "it; the first adopter run that reports these values is what promotes them.",
    ),
    Parameter(
        where="engagement.calibration:PCT_CLIP_LO",
        what="Winsorisation bounds for the fitted transform",
        note="With PCT_CLIP_HI. Changing them changes what the frozen transform is, "
        "so they belong to a model version rather than to a run -- but unlike the "
        "click unit they are a tuning choice rather than a definition, so they should "
        "end up declared in a versioned freeze rather than fixed here.",
    ),
)

ALL_GROUPS: tuple[tuple[str, tuple[Parameter, ...]], ...] = (
    (PROMOTED_GROUP, PROMOTED),
    (FIXED_GROUP, FIXED),
    (DEFERRED_GROUP, DEFERRED),
)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def triage_markdown() -> str:
    """The census as three markdown tables, rendered from the declarations above."""
    lines = [TRIAGE_BEGIN_MARKER]
    for title, entries in ALL_GROUPS:
        lines.extend(
            [
                "",
                f"### {title}",
                "",
                "| Where | What | Note |",
                "|---|---|---|",
            ]
        )
        lines.extend(
            f"| `{entry.where}` | {_cell(entry.what)} | {_cell(entry.note)} |" for entry in entries
        )
    lines.extend(["", TRIAGE_END_MARKER])
    return "\n".join(lines)


def render_triage_into(document: str) -> str:
    """Replace the triage block in a document with a fresh render."""
    start = document.find(TRIAGE_BEGIN_MARKER)
    end = document.find(TRIAGE_END_MARKER)
    if start < 0 or end < 0:
        raise ValueError(
            f"the document carries no triage block; expected {TRIAGE_BEGIN_MARKER} and "
            f"{TRIAGE_END_MARKER}"
        )
    return document[:start] + triage_markdown() + document[end + len(TRIAGE_END_MARKER) :]


# --- the gate census, rendered so no document has to retype a threshold -------

GATES_BEGIN_MARKER = "<!-- gates:begin -->"
GATES_END_MARKER = "<!-- gates:end -->"


@dataclass(frozen=True)
class Gate:
    """One publication gate, named by the field that holds its level."""

    #: Attribute on :class:`~engagement_kernel.engagement.config.GateThresholds`.
    field: str
    name: str
    meaning: str


#: The gates a methodology document has to describe, in the order it describes
#: them. The *levels* are never written here -- they are read off
#: ``GateThresholds`` at render time, so a prose table cannot drift from the code
#: and nobody has to retype a number to keep a document current.
#:
#: This matters because it already went wrong. The document this renders into
#: previously typed every level as a literal. One of them -- the flat
#: cross-algorithm bar -- was retired and replaced by a per-k derivation, and the
#: prose went on stating the retired number for seven weeks, because nothing
#: connected the two.
GATES: tuple[Gate, ...] = (
    Gate(
        field="seed_ari",
        name="Seed stability",
        meaning="Median pairwise agreement between fits of the same k from different "
        "starting points. Below it, the groups are a property of where the fitting "
        "started rather than of the readers.",
    ),
    Gate(
        field="cross_algorithm_ari_by_k",
        name="Cross-algorithm agreement",
        meaning="Agreement between the production labeller and an independent second "
        "algorithm at the same k. Per k, and derived on your own panel -- the one "
        "threshold here that cannot be inherited. See section 9.3.",
    ),
    Gate(
        field="centroid_distinctness_corr",
        name="Centroid distinctness",
        meaning="Correlation above which two cluster profiles are one cluster reported twice.",
    ),
    Gate(
        field="tiny_cluster_floor",
        name="Smallest cluster share",
        meaning="Below it, a cluster is an incidental group rather than a segment.",
    ),
    Gate(
        field="major_cluster_share",
        name="Cluster persistence share",
        meaning="The share at which a cluster must appear under every seed. Kept equal "
        "to the floor above on purpose.",
    ),
    Gate(
        field="t4_retention",
        name="Temporal retention",
        meaning="Share of readers keeping their label between two windows far enough "
        "apart to share no days.",
    ),
    Gate(
        field="t4_profile_similarity",
        name="Temporal profile similarity",
        meaning="Correlation between matched cluster profiles across the same gap.",
    ),
    Gate(
        field="selection_survival_floor",
        name="Selection survival",
        meaning="One-sided lower bound the all-screens survival rate must clear across "
        "perturbed panels. What makes the verdict reproducible rather than a property "
        "of the one matrix a run assembled. See section 9.2.",
    ),
    Gate(
        field="topic_coverage_floor",
        name="Topic coverage",
        meaning="Share of reading whose section resolves. Blocks the topic block alone, "
        "not the whole run.",
    ),
)


def _render_level(gates: GateThresholds, field: str) -> str:
    """One gate's shipped level, formatted, never typed."""
    value = getattr(gates, field)
    if field == "cross_algorithm_ari_by_k":
        low, high = min(value), max(value)
        return (
            f"per k: {len(value)} bars, {value[low]:g} at k={low} falling to "
            f"{value[high]:g} at k={high}"
        )
    return f"{value:g}"


def gate_table_markdown(gates: GateThresholds | None = None) -> str:
    """The gates as a markdown table, levels read from the code.

    Rendered rather than written, because a methodology document that retypes a
    threshold becomes a second source of truth for it and the copy is the one
    people read.
    """
    gates = gates or GateThresholds()
    lines = [
        GATES_BEGIN_MARKER,
        "",
        "| Gate | Field | This package's level | What it means |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {_cell(gate.name)} | `{gate.field}` | {_render_level(gates, gate.field)} | "
        f"{_cell(gate.meaning)} |"
        for gate in GATES
    )
    lines.extend(["", GATES_END_MARKER])
    return "\n".join(lines)


def render_gates_into(document: str) -> str:
    """Replace the gate table in a document with a fresh render."""
    start = document.find(GATES_BEGIN_MARKER)
    end = document.find(GATES_END_MARKER)
    if start < 0 or end < 0:
        raise ValueError(
            f"the document carries no gate table; expected {GATES_BEGIN_MARKER} and "
            f"{GATES_END_MARKER}"
        )
    return document[:start] + gate_table_markdown() + document[end + len(GATES_END_MARKER) :]
