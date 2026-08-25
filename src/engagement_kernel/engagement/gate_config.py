"""A deployment's own gate thresholds, as a file it owns.

Every threshold in :class:`~engagement_kernel.engagement.config.GateThresholds`
carries a default, and every one of those defaults is a number some other
newsroom measured on some other population. Two docstrings in this package
already said so -- that the defaults are "a starting point for a deployment that
has not yet measured its own distribution", that they are "reasonable guesses
that a deployment is supposed to replace". Neither said how, and there was no
how: setting one meant writing Python against a pre-release library. That is a
slower way of prescribing than prescribing outright, because it reads as an
invitation and behaves as a fixed value.

This module is the how. A gates file is TOML, the deployment owns it, it goes in
their own version control next to their manifest and their bucket map, and the
lane reads it with ``--gates``.

Three properties are deliberate.

**Absence is not a special case.** No gates file means the package defaults,
unchanged, exactly as before this module existed. The file is a way to say
something, never a thing you must say to get today's behaviour.

**An unknown key is refused, not ignored.** A misspelled threshold that silently
does nothing is the characteristic failure of configuration files, and it fails
in the worst direction: the run reports the default's verdict while its operator
believes they set something. So every key is checked against the fields that
exist, and the refusal names the near-miss.

**The version is required.** Not decoration: it is the only way a future change
in what a key *means* can be caught rather than silently misread. A file with no
version is refused rather than assumed to be version 1.
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engagement_kernel.engagement.config import (
    SHIPPED_BAR_PROVENANCE,
    BlockWeights,
    GateThresholds,
    LaneConfigError,
)

#: The only format version this package reads. Bump it when a key changes
#: meaning, never when one is added: an added key is caught by the unknown-key
#: refusal on an older reader, which is the right failure.
GATE_CONFIG_VERSION = 1

#: Scalar threshold fields, in the order they are rendered.
GATE_FIELDS: tuple[str, ...] = tuple(
    f.name for f in dataclasses.fields(GateThresholds) if f.name != "cross_algorithm_ari_by_k"
)

#: Lane parameters that are the deployment's to set but are not gates: they shape
#: the fitting population and the candidate sweep rather than deciding whether a
#: result may be published.
LANE_FIELDS: tuple[str, ...] = (
    "content_active_min_views",
    "content_active_min_sections",
    "z_clip",
    "panel_seed",
    "n_seeds",
    "k_grid",
)

BLOCK_WEIGHT_FIELDS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(BlockWeights))

_INT_FIELDS = frozenset(
    {
        "selection_perturbation_draws",
        "selection_rng_seed",
        "content_active_min_views",
        "content_active_min_sections",
        "panel_seed",
        "n_seeds",
    }
)

BARS_TABLE = "cross_algorithm_ari_by_k"


class GateConfigError(LaneConfigError):
    """The gates file could not be read as one.

    A subclass of :class:`LaneConfigError` so a caller that already handles a
    refusal to run without something it cannot guess handles this too -- an
    unreadable gates file is the same class of problem as a missing bucket map.
    """


@dataclass(frozen=True)
class GateConfig:
    """What one gates file said."""

    gates: GateThresholds
    #: Keyword overrides for :class:`~engagement_kernel.engagement.config.LaneConfig`.
    #: Empty when the file declared no ``[lane]`` table, which is the common case.
    lane_overrides: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None

    def describe(self) -> str:
        parts = [f"gates file          : {self.source or '<none>'}"]
        if self.lane_overrides:
            named = ", ".join(sorted(self.lane_overrides))
            parts.append(f"lane overrides      : {named}")
        return "\n".join(parts)


def _suggest(name: str, known: tuple[str, ...]) -> str:
    """Name the near-miss, because a typo is the likeliest cause of an unknown key."""
    import difflib

    close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    if close:
        return f" -- did you mean {close[0]!r}?"
    return ""


def _reject_unknown(table: Mapping[str, Any], known: tuple[str, ...], where: str) -> None:
    for name in table:
        if name not in known:
            raise GateConfigError(
                f"{where} has no field {name!r}{_suggest(name, known)}. A key this reader "
                "does not recognise is refused rather than ignored: ignoring it would run "
                "the default while its author believed they had set something. Known fields: "
                f"{', '.join(known)}"
            )


def _coerce(name: str, value: Any) -> Any:
    if isinstance(value, bool):
        raise GateConfigError(f"{name} is a number, not a boolean")
    if name in _INT_FIELDS:
        if not isinstance(value, int):
            raise GateConfigError(f"{name} is a whole number, so {value!r} is not a value it takes")
        return int(value)
    if name == "k_grid":
        if not isinstance(value, list) or not value:
            raise GateConfigError(
                "k_grid is a non-empty list of candidate cluster counts, for example "
                "k_grid = [4, 6, 8]. It does not have to be contiguous"
            )
        grid = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                raise GateConfigError(f"k_grid holds cluster counts, so {item!r} is not one")
            grid.append(int(item))
        return tuple(grid)
    if not isinstance(value, (int, float)):
        raise GateConfigError(f"{name} is a number, so {value!r} is not a value it takes")
    return float(value)


def _read_bars(table: Mapping[str, Any]) -> dict[int, float]:
    """The bar table, whose keys are cluster counts written as TOML bare keys.

    ``3 = 0.46`` rather than a quoted key, because that is how a person writes it
    and TOML permits digits in a bare key. The key arrives here as the string
    ``"3"`` either way.
    """
    bars: dict[int, float] = {}
    for raw_k, raw_bar in table.items():
        try:
            k = int(raw_k)
        except ValueError:
            raise GateConfigError(
                f"[gates.{BARS_TABLE}] is keyed by the number of clusters, so {raw_k!r} is "
                "not a key it can have. Write the count as a bare key: 3 = 0.46"
            ) from None
        if isinstance(raw_bar, bool) or not isinstance(raw_bar, (int, float)):
            raise GateConfigError(
                f"the bar for k={k} is {raw_bar!r}, which is not an agreement level"
            )
        bars[k] = float(raw_bar)
    if not bars:
        raise GateConfigError(
            f"[gates.{BARS_TABLE}] is present but empty. An empty bar table refuses every "
            "candidate k, which is a run that cannot produce a model. Leave the table out "
            "to keep the shipped bars, or declare one entry per k you intend to screen"
        )
    return bars


def parse_gate_config(text: str, *, source: str | None = None) -> GateConfig:
    """Read a gates document. Separate from the file read so a string can be tested."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise GateConfigError(f"the gates file is not readable as TOML: {error}") from None

    _reject_unknown(document, ("version", "gates", "lane"), "the gates file")

    if "version" not in document:
        raise GateConfigError(
            "the gates file declares no version. Add `version = "
            f"{GATE_CONFIG_VERSION}` at the top. It is required rather than assumed so that "
            "a later change in what a key means is refused instead of silently misread"
        )
    version = document["version"]
    if version != GATE_CONFIG_VERSION:
        raise GateConfigError(
            f"the gates file declares version {version!r}, and this package reads version "
            f"{GATE_CONFIG_VERSION}"
        )

    gates = GateThresholds()
    gate_table = document.get("gates", {})
    if not isinstance(gate_table, dict):
        raise GateConfigError("[gates] is a table of thresholds")
    _reject_unknown(gate_table, (*GATE_FIELDS, BARS_TABLE), "[gates]")
    replacements: dict[str, Any] = {}
    for name in GATE_FIELDS:
        if name in gate_table:
            replacements[name] = _coerce(name, gate_table[name])
    if BARS_TABLE in gate_table:
        bars = gate_table[BARS_TABLE]
        if not isinstance(bars, dict):
            raise GateConfigError(
                f"[gates.{BARS_TABLE}] is a table of one bar per candidate k, for example 3 = 0.46"
            )
        replacements[BARS_TABLE] = _read_bars(bars)
    if replacements:
        # `replace` re-runs GateThresholds.__post_init__, so a value outside its
        # range is refused here rather than at the gate it would have decided.
        gates = dataclasses.replace(gates, **replacements)

    lane_overrides: dict[str, Any] = {}
    lane_table = document.get("lane", {})
    if not isinstance(lane_table, dict):
        raise GateConfigError("[lane] is a table of lane parameters")
    _reject_unknown(lane_table, (*LANE_FIELDS, "block_weights"), "[lane]")
    for name in LANE_FIELDS:
        if name in lane_table:
            lane_overrides[name] = _coerce(name, lane_table[name])
    if "block_weights" in lane_table:
        weights = lane_table["block_weights"]
        if not isinstance(weights, dict):
            raise GateConfigError("[lane.block_weights] is a table of block weights")
        _reject_unknown(weights, BLOCK_WEIGHT_FIELDS, "[lane.block_weights]")
        lane_overrides["block_weights"] = dataclasses.replace(
            BlockWeights(), **{name: _coerce(name, value) for name, value in weights.items()}
        )

    return GateConfig(gates=gates, lane_overrides=lane_overrides, source=source)


def load_gate_config(path: str | Path) -> GateConfig:
    """Read a gates file. A missing path is an error, an absent flag is not."""
    file = Path(path)
    try:
        text = file.read_text()
    except OSError as error:
        raise GateConfigError(
            f"cannot read the gates file {file}: {error}. Omit --gates entirely to run on "
            "this package's own defaults"
        ) from None
    return parse_gate_config(text, source=str(file))


def render_gate_config(
    gates: GateThresholds | None = None,
    *,
    lane_overrides: Mapping[str, Any] | None = None,
) -> str:
    """Render a gates file, with the reasoning for each threshold beside it.

    The comments are the point. A file of bare numbers is a file nobody can revise
    six months later, because the question is never "what is the number" but "what
    would have to be true for it to be different".
    """
    gates = gates or GateThresholds()
    lane_overrides = dict(lane_overrides or {})
    bars = "\n".join(f"{k} = {bar:g}" for k, bar in sorted(gates.cross_algorithm_ari_by_k.items()))
    lane_lines = ""
    if lane_overrides:
        rendered = []
        for name in LANE_FIELDS:
            if name not in lane_overrides:
                continue
            value = lane_overrides[name]
            if name == "k_grid":
                rendered.append(f"{name} = [{', '.join(str(k) for k in value)}]")
            else:
                rendered.append(f"{name} = {value:g}")
        lane_lines = "\n".join(rendered)
    lane_block = f"\n[lane]\n{lane_lines}\n" if lane_lines else ""
    return f"""\
# Gate thresholds for one deployment of the engagement lane.
#
# This file is yours. Every number below is a number some other newsroom measured
# on some other population, and the run prints the realised value beside each
# threshold so your own first run tells you where yours sit. Keep this file in your
# own version control, next to your manifest and your section bucket map, and pass
# it with --gates.
#
# Omitting --gates entirely runs the package defaults. This file rendered
# unedited IS those defaults, so a diff against it shows exactly what you changed.

version = {GATE_CONFIG_VERSION}

[gates]
# Median pairwise adjusted Rand index across seeds. Below this the clusters are an
# artifact of where the fitting started rather than a property of the readers.
seed_ari = {gates.seed_ari:g}

# Correlation above which two cluster centroid profiles are not distinct -- one
# cluster reported twice.
centroid_distinctness_corr = {gates.centroid_distinctness_corr:g}

# Smallest share a cluster may hold, and the share at which a cluster counts as
# one that must persist across seeds. Keep them equal, or a cluster can be
# simultaneously too small to matter and required to persist.
tiny_cluster_floor = {gates.tiny_cluster_floor:g}
major_cluster_share = {gates.major_cluster_share:g}

# Share of resolved reading below which the topic taxonomy is not trustworthy.
# Blocks the topic block, not the whole run.
topic_coverage_floor = {gates.topic_coverage_floor:g}

# Label retention required between a week and the week four later, and the
# correlation required between matched centroid profiles across the same gap.
# Four weeks, not one: adjacent weeks share 21 of their 28 window days, so
# adjacent-week agreement is high whatever the model does.
t4_retention = {gates.t4_retention:g}
t4_profile_similarity = {gates.t4_profile_similarity:g}

# Reproducibility of the verdict, not its strictness. Each candidate k is
# re-screened on this many panels the pipeline could equally have built -- the same
# panel with this fraction of rows dropped -- and survives only if the one-sided
# 95% lower bound on its all-screens survival rate clears the floor. Fewer draws is
# a cheaper and noisier verdict, not a more permissive one.
selection_perturbation_draws = {gates.selection_perturbation_draws:d}
selection_perturbation_row_fraction = {gates.selection_perturbation_row_fraction:g}
selection_survival_floor = {gates.selection_survival_floor:g}
selection_rng_seed = {gates.selection_rng_seed:d}

# Agreement required between k-means and a hierarchical fit at the same k, one bar
# per candidate k. A k with no bar here is refused rather than screened against a
# number nobody measured for it.
#
# THESE VALUES ARE NOT THE METHOD. Per-k-ness is the portable part: two algorithms
# that share an objective agree well above zero on a population with no structure
# at all, and that chance level falls as k rises, so one flat number is the wrong
# shape as well as the wrong level. The levels below are not portable. They are
# {SHIPPED_BAR_PROVENANCE}
#
# Add a row to screen a k this package ships no bar for -- two clusters is a
# legitimate answer for a small or sharply split audience, and so is twelve.
[gates.{BARS_TABLE}]
{bars}
{lane_block}"""


def gate_config_template() -> str:
    """The defaults, rendered. Loading this must reproduce them exactly."""
    return render_gate_config()
