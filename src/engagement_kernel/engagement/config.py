"""Everything one run of the engagement lane has to be told.

Most of it comes from the delivery's own manifest, and that is the point: the
timezone, the week anchor, the article-view definition and the scored population
travel with the data instead of living in a deployment config that can drift away
from it. What is left -- the section taxonomy, the fitting thresholds, the gate
levels -- is deployment configuration, declared here with the reasoning for each
default.

Two resolutions in this module are worth reading before anything else.

**The surface is resolved from what the delivery actually contains.** The richest
clustering surface uses community participation and email cadence, and both come
from optional inputs. A delivery that does not carry them cannot be scored on it,
and the wrong response is to fill the missing channels with zeros: a reader with
no community feed is not a reader who never commented, and a model fit on that
distinction learns the shape of the publisher's vendor contracts. So an absent
optional input selects a *named alternate surface* instead, which is the answer
the contract promises for exactly this case. :func:`resolve_surface` does that
once, and the chosen surface name is part of the model version.

**The email click unit is decided, and it is decided as click events.** Not
distinct campaigns clicked. The system this ports from carried a docstring
claiming the campaign meaning long after its table counted events, so the
decision is recorded here as data (:data:`EMAIL_CLICK_UNIT`) and folded into the
model version -- because it does move the numbers, and where it moves them is not
where the old framing said. The cadence axis is *invariant* to the unit: it counts
weeks with a non-zero bin, and any week containing one click has a non-zero bin
under either unit. What moves is click *volume*, which reaches the model through
the intensity block, and is log-transformed on the way. So the decision is real,
it belongs to a model version, and anybody debugging a cadence difference by
looking at the click unit is looking in the wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engagement_kernel.contract import spec
from engagement_kernel.contract.manifest import Manifest, ScoredPopulation
from engagement_kernel.engagement.buckets import SectionBucketMap
from engagement_kernel.engagement.windows import WeekGrid

#: The unit one row of ``email_click`` counts, as this lane reads it.
#:
#: Decided: a click event. The contract's ``email_click`` table declares the same
#: unit in its own notes, and it also carries ``campaign_id``, so counting
#: distinct campaigns clicked remains buildable -- it is a different decision, not
#: a missing input. It would be a model-version change.
EMAIL_CLICK_UNIT = "click_event"

#: Surface names. Each is a distinct feature set, not a degraded version of
#: another, which is why an absent input selects one rather than blanking columns.
SURFACE_JOINT = "joint"
SURFACE_INTENSITY = "intensity"

#: Optional contract inputs the joint surface requires.
JOINT_SURFACE_REQUIRED_INPUTS: tuple[str, ...] = ("email_click", "community_action")


class LaneConfigError(ValueError):
    """The lane was asked to run without something it cannot guess."""


@dataclass(frozen=True)
class GateThresholds:
    """The publication gates, as declared numbers.

    They live here, in versioned configuration, rather than in the modules that
    evaluate them. The system this ports from set several of these in the same
    stage that selected the models they gate, which is not a gate -- it is a
    description of what the run happened to produce.

    Every default below is a starting point for a deployment that has not yet
    measured its own distribution, and the run reports the realised value beside
    the threshold so the first look at real data is also the prompt to set it.
    """

    #: Median pairwise adjusted Rand index across seeds. Below this the clusters
    #: are an artifact of the initialisation.
    seed_ari: float = 0.70
    #: Agreement required between k-means and a hierarchical fit of the same k.
    #:
    #: Per k, and derived rather than inherited, because two algorithms that share
    #: an objective agree well above zero on a population with no structure at all
    #: -- and that chance level *falls* as k rises, so one flat number is the wrong
    #: shape as well as the wrong level. A k with no entry is refused rather than
    #: screened against a number nobody measured for it.
    cross_algorithm_ari_by_k: dict[int, float] = field(
        default_factory=lambda: {
            3: 0.46,
            4: 0.42,
            5: 0.38,
            6: 0.35,
            7: 0.34,
            8: 0.33,
            9: 0.32,
            10: 0.31,
        }
    )
    #: Correlation above which two cluster centroid profiles are not distinct.
    centroid_distinctness_corr: float = 0.90
    #: Smallest share a cluster may hold. Aligned with the persistence share below
    #: it so a cluster cannot be simultaneously too small to matter and required
    #: to persist.
    tiny_cluster_floor: float = 0.01
    #: Share at which a cluster counts as one that must persist across seeds.
    major_cluster_share: float = 0.01
    #: Share of resolved reading below which the topic taxonomy is not trustworthy.
    #: Blocks the topic block, not the whole run.
    topic_coverage_floor: float = 0.80
    #: Label retention required between a week and the week four later.
    #:
    #: Four, not one. Adjacent weeks share 21 of their 28 window days, so
    #: adjacent-week agreement is mechanically high whatever the model does; it is
    #: monitoring, and gating on it would certify a model for arithmetic it cannot
    #: avoid.
    t4_retention: float = 0.45
    #: Correlation required between matched centroid profiles four weeks apart.
    t4_profile_similarity: float = 0.80
    #: How many perturbed panels each candidate k is re-screened on, and how much
    #: of the panel is dropped in each. A verdict read off the single matrix a run
    #: happened to assemble is not reproducible: on a real refit, one candidate's
    #: seed stability was 0.97 on its own matrix and 0.49 on every two-row drop of
    #: it, and being the smallest survivor it became the champion.
    selection_perturbation_draws: int = 50
    selection_perturbation_row_fraction: float = 0.001
    #: One-sided 95% lower bound the survival rate must clear. A majority: the k
    #: has to survive more panels than not, with confidence.
    selection_survival_floor: float = 0.50
    selection_rng_seed: int = 20260824

    def cross_algorithm_bar(self, k: int) -> float:
        try:
            return self.cross_algorithm_ari_by_k[k]
        except KeyError:
            raise LaneConfigError(
                f"no cross-algorithm agreement bar declared for k={k}; declared bars cover "
                f"k={sorted(self.cross_algorithm_ari_by_k)}. Screening k={k} would compare "
                "it against a number nobody measured for it -- declare a bar or leave k "
                "out of the sweep"
            ) from None


@dataclass(frozen=True)
class BlockWeights:
    """How much of the model distance each semantic block is allowed to own.

    Only used by the block-weighted construction. The surfaces this lane freezes
    by default are already in z-space and carry no block weighting, so these are
    here for the alternate construction and for anyone comparing the two.
    """

    consumption: float = 0.40
    email_click: float = 0.125
    community: float = 0.075
    cross_channel: float = 0.25
    topic: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return {
            "consumption": self.consumption,
            "email_click": self.email_click,
            "community": self.community,
            "cross_channel": self.cross_channel,
            "topic": self.topic,
        }


def resolve_surface(manifest: Manifest) -> str:
    """Which clustering surface this delivery can support.

    The joint surface needs email clicks and community actions. Both are optional
    inputs, and "absent" here includes ``not_yet_launched``: an input whose
    product did not exist for part of the analysis period would read as zero
    activity across that period, which is a statement about the reader that
    nobody made.
    """
    for name in JOINT_SURFACE_REQUIRED_INPUTS:
        availability = manifest.optional_inputs.get(name)
        if availability is None or not availability.is_available:
            return SURFACE_INTENSITY
    return SURFACE_JOINT


@dataclass(frozen=True)
class LaneConfig:
    """The resolved parameters of one engagement-lane run."""

    #: The delivery's declarations, carried whole. Everything derived from them is
    #: derived once, here, so no downstream module re-reads the manifest and no
    #: two modules can disagree about what it said.
    manifest: Manifest
    week_grid: WeekGrid
    bucket_map: SectionBucketMap
    surface: str
    #: Lists the email cadence signal is restricted to; ``None`` means every list
    #: the delivery carries.
    #:
    #: **No default list id.** The system this ports from carried a real
    #: third-party list identifier as a function default. That id is deployment
    #: configuration for exactly one publisher, it is meaningless to anybody else,
    #: and a public repository is the last place for it. ``None`` is the honest
    #: default: use what you were sent.
    email_list_ids: tuple[str, ...] | None = None
    #: Resolved views and distinct sections a reader needs in the window before
    #: their topic mix is treated as a mix rather than an accident. Two sections is
    #: the smallest number at which "mix" means anything.
    content_active_min_views: int = 3
    content_active_min_sections: int = 2
    #: Seed for the training-panel sample. Versioned with the model: the panel is
    #: the fitting population, so a different seed is a different model.
    panel_seed: int = 20260824
    z_clip: float = 5.0
    block_weights: BlockWeights = field(default_factory=BlockWeights)
    gates: GateThresholds = field(default_factory=GateThresholds)
    k_grid: tuple[int, ...] = (3, 4, 5, 6, 7, 8)
    #: Seeds per candidate k in the stability screen.
    n_seeds: int = 20

    def __post_init__(self) -> None:
        if self.surface not in (SURFACE_JOINT, SURFACE_INTENSITY):
            raise LaneConfigError(f"unknown surface {self.surface!r}")
        if self.content_active_min_views < 1:
            raise LaneConfigError(
                "content_active_min_views must be at least 1: a reader with no resolved "
                "reading has no topic mix to describe"
            )
        if self.content_active_min_sections < 2:
            raise LaneConfigError(
                "content_active_min_sections must be at least 2. At 1 every reader with a "
                "single view is 'content active' with a mix that is 100% one bucket, which "
                "is not a preference"
            )
        if self.email_list_ids is not None and not self.email_list_ids:
            raise LaneConfigError(
                "email_list_ids is an empty tuple, which restricts the cadence signal to no "
                "list at all and reports every reader as never clicking. Pass None to use "
                "every list in the delivery"
            )
        if not self.k_grid:
            raise LaneConfigError("k_grid is empty, so no candidate model would be screened")
        if min(self.k_grid) < 2:
            raise LaneConfigError("k_grid contains a k below 2")
        for k in self.k_grid:
            # Fail here rather than midway through a sweep that has already spent
            # minutes fitting the candidates before it.
            self.gates.cross_algorithm_bar(k)

    @classmethod
    def from_manifest(
        cls,
        manifest: Manifest,
        bucket_map: SectionBucketMap,
        **overrides: object,
    ) -> LaneConfig:
        """Resolve the run configuration from the delivery's manifest.

        The surface is resolved from the manifest unless the caller names one, and
        a caller who names the joint surface on a delivery that cannot support it
        is refused rather than quietly downgraded.
        """
        surface = str(overrides.pop("surface", resolve_surface(manifest)))
        if surface == SURFACE_JOINT and resolve_surface(manifest) != SURFACE_JOINT:
            missing = [
                name
                for name in JOINT_SURFACE_REQUIRED_INPUTS
                if not (
                    (availability := manifest.optional_inputs.get(name))
                    and availability.is_available
                )
            ]
            raise LaneConfigError(
                f"the joint surface needs {missing}, which this delivery declares absent. "
                "Building it anyway would read the missing channels as zero activity; the "
                f"{SURFACE_INTENSITY!r} surface is the declared alternate"
            )
        return cls(
            manifest=manifest,
            week_grid=WeekGrid.from_manifest(manifest),
            bucket_map=bucket_map,
            surface=surface,
            **overrides,  # type: ignore[arg-type]
        )

    # --- derived, so nothing re-reads the manifest --------------------------

    @property
    def scored_population(self) -> ScoredPopulation:
        return self.manifest.scored_population

    @property
    def entitled_states(self) -> tuple[str, ...]:
        return self.manifest.scored_population.entitled_states

    @property
    def population_exclusions(self) -> tuple[str, ...]:
        return self.manifest.population_exclusions

    @property
    def channels(self) -> tuple[str, ...]:
        """The reader-event channels, from the contract's own vocabulary.

        Derived rather than written out. The per-channel feature names are built
        from this, so a channel added to the contract gets features instead of
        being silently dropped from every window count.
        """
        return spec.enums.READER_EVENT_CHANNELS

    def feature_version(self) -> str:
        """A version string for the feature set this configuration produces.

        Carries the declarations that change the numbers: the article-view and
        scored-population definitions, the week anchor, the bucket map, the click
        unit and the surface. Two runs whose outputs are not comparable get
        different strings here, which is what makes a comparison across them an
        explicit act rather than an accident.
        """
        return "|".join(
            (
                f"contract={self.manifest.contract_version}",
                f"article_view={self.manifest.article_view.definition_id}",
                f"population={self.manifest.scored_population.definition_id}",
                f"week={self.week_grid.week_end_day}",
                f"tz={self.manifest.day_boundary_timezone}",
                f"buckets={self.bucket_map.version}",
                f"click_unit={EMAIL_CLICK_UNIT}",
                f"surface={self.surface}",
            )
        )

    def describe(self) -> str:
        lists = "every list in the delivery"
        if self.email_list_ids is not None:
            lists = ", ".join(self.email_list_ids)
        return "\n".join(
            (
                f"day boundary timezone : {self.manifest.day_boundary_timezone}",
                f"week grid             : {self.week_grid.describe()}",
                f"article view          : {self.manifest.article_view.definition_id}",
                f"scored population     : {self.scored_population.definition_id} "
                f"({', '.join(self.entitled_states)})",
                f"population exclusions : {len(self.population_exclusions)}",
                f"surface               : {self.surface}",
                f"bucket map            : v{self.bucket_map.version}, "
                f"{self.bucket_map.n_buckets} buckets",
                f"email click unit      : {EMAIL_CLICK_UNIT}",
                f"email lists           : {lists}",
                f"content-active floor  : {self.content_active_min_views} resolved views, "
                f"{self.content_active_min_sections} sections",
                f"candidate k           : {', '.join(str(k) for k in self.k_grid)}",
                f"feature version       : {self.feature_version()}",
            )
        )
