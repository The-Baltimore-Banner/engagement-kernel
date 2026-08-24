"""The publication gates: what has to be true before a label is published.

A gate here is not a warning. Each one names a condition under which the published
numbers would be wrong in a way nobody downstream could detect, and a failure blocks
publication of the surface it applies to.

Two things about the shape are deliberate.

**A gate reports the realised value beside its threshold, pass or fail.** A gate
report that says only "passed" cannot be used to set the threshold, and several of
these thresholds start as reasonable guesses that a deployment is supposed to
replace with a measured number. Reporting the value on every run is what makes that
possible without a separate investigation.

**A topic-coverage failure blocks the topic block, not the whole run.** The topic
block is conditional and imputed-safe: a reader with no resolvable reading gets the
baseline, and the rest of the model is unaffected. Blocking the engagement labels
because section metadata degraded would take down the thing that still works. Every
other hard failure blocks everything, because everything else feeds the distance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from engagement_kernel.engagement.buckets import CompletenessReport
from engagement_kernel.engagement.config import GateThresholds

#: What a failing check stops. ``"none"`` is a check that is reported and never
#: blocks -- an alarm, not a gate, and labelled as one so nobody has to guess.
BLOCKS_ALL = "all"
BLOCKS_TOPIC = "topic_only"
BLOCKS_NOTHING = "none"


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str = ""
    blocks: str = BLOCKS_ALL


@dataclass
class GateReport:
    family: str
    checks: list[GateCheck] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", blocks: str = BLOCKS_ALL) -> None:
        self.checks.append(GateCheck(name=name, passed=passed, detail=detail, blocks=blocks))

    @property
    def failures(self) -> list[GateCheck]:
        return [
            check for check in self.checks if not check.passed and check.blocks != BLOCKS_NOTHING
        ]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "family": self.family,
                    "check": check.name,
                    "passed": check.passed,
                    "blocks": check.blocks,
                    "detail": check.detail,
                }
                for check in self.checks
            ]
        )


@dataclass(frozen=True)
class PublicationDecision:
    publish_labels: bool
    publish_topic_block: bool
    blocking_failures: list[str]
    topic_only_failures: list[str]

    def describe(self) -> str:
        if self.publish_labels and self.publish_topic_block:
            return "every gate passed"
        parts = []
        if self.blocking_failures:
            parts.append(f"blocking: {', '.join(self.blocking_failures)}")
        if self.topic_only_failures:
            parts.append(f"topic block only: {', '.join(self.topic_only_failures)}")
        return "; ".join(parts)


def data_quality_gates(
    *,
    spine: pd.DataFrame,
    stale_sources: list[str],
    resolved_view_share: float,
    completeness: CompletenessReport,
    matrix_finite: bool,
    variance_in_bounds: bool,
    gates: GateThresholds,
) -> GateReport:
    """What has to hold about the data before any model result is meaningful."""
    report = GateReport("data_quality")
    duplicates = bool(spine.duplicated(subset=["reader_id", "as_of_week_end"]).any())
    report.add(
        "no_duplicate_spine_rows",
        not duplicates,
        detail="one row per reader per week" if not duplicates else "duplicate reader-weeks",
    )
    report.add(
        "sources_reach_the_week_end",
        not stale_sources,
        detail="; ".join(stale_sources) if stale_sources else "every input reaches the week end",
    )
    report.add(
        "topic_metadata_coverage",
        resolved_view_share >= gates.topic_coverage_floor,
        detail=f"resolved view share {resolved_view_share:.4f} against floor "
        f"{gates.topic_coverage_floor}",
        blocks=BLOCKS_TOPIC,
    )
    report.add(
        "bucket_map_completeness",
        completeness.passed,
        detail=completeness.describe(),
        blocks=BLOCKS_TOPIC,
    )
    report.add("model_matrix_finite", matrix_finite)
    report.add("baseline_variance_in_bounds", variance_in_bounds)
    return report


def feature_quality_gates(
    *,
    blocks: list[dict[str, object]],
    imputation_share: pd.Series,
    content_active_share: float,
    tolerance: float = 0.01,
) -> GateReport:
    """What has to hold about the features themselves.

    The reconciliation at the end is the one that catches a real class of error: the
    topic block's imputation share must equal the share of readers who are not
    content-active. If it does not, the mask used to fit the block and the mask used
    to impute it have come apart, and every topic feature is then standardised
    against the wrong population -- which nothing else here would notice.
    """
    report = GateReport("feature_quality")
    dropped = [str(block["name"]) for block in blocks if block["method"] == "dropped"]
    forced = [
        str(block["name"])
        for block in blocks
        if block["method"] == "pca" and block["gate_failures"]
    ]
    report.add(
        "no_forced_pca_blocks",
        not forced,
        detail=f"components kept despite failing their gates: {forced}" if forced else "",
    )
    report.add(
        "dropped_blocks_recorded",
        True,
        detail=f"dropped, with a recorded fallback: {dropped}" if dropped else "none dropped",
        blocks=BLOCKS_NOTHING,
    )
    topic_shares = imputation_share[imputation_share.index.str.startswith("topic_")]
    expected = 1.0 - content_active_share
    reconciled = bool(((topic_shares - expected).abs() <= tolerance).all())
    report.add(
        "topic_imputation_share_reconciles",
        reconciled,
        detail=f"expected {expected:.4f}; observed {topic_shares.round(4).to_dict()}",
        blocks=BLOCKS_TOPIC,
    )
    return report


def model_quality_gates(
    *,
    champion_k: int | None,
    survival_lower_bound: float | None,
    smallest_cluster_share: float | None,
    interpretability_reviewed: bool,
    gates: GateThresholds,
) -> GateReport:
    """What has to hold about the fitted model."""
    report = GateReport("model_quality")
    report.add(
        "a_k_survived_selection",
        champion_k is not None,
        detail=f"champion k={champion_k}" if champion_k is not None else "no k survived",
    )
    if survival_lower_bound is not None:
        report.add(
            "champion_survives_perturbed_panels",
            survival_lower_bound >= gates.selection_survival_floor,
            detail=f"survival lower bound {survival_lower_bound:.4f} against floor "
            f"{gates.selection_survival_floor}",
        )
    if smallest_cluster_share is not None:
        report.add(
            "no_unintended_micro_clusters",
            smallest_cluster_share >= gates.tiny_cluster_floor,
            detail=f"smallest cluster share {smallest_cluster_share:.4f} against floor "
            f"{gates.tiny_cluster_floor}",
        )
    # Deliberately not automatable, and deliberately not defaulted to True. A model
    # whose clusters nobody has looked at and named is not ready to be published
    # under names people will act on.
    report.add(
        "interpretability_reviewed",
        interpretability_reviewed,
        detail="a person has reviewed and named the clusters"
        if interpretability_reviewed
        else "no recorded interpretability review",
    )
    return report


def temporal_gates(
    *,
    t4_retention: float | None,
    t4_profile_similarity: float | None,
    gates: GateThresholds,
) -> GateReport:
    """What has to hold about the labels over time.

    Both quantities are ``None`` on a run with fewer than five scored weeks. That is
    reported as an un-evaluated check rather than a pass: a temporal gate that
    silently passes for want of data is the worst of the three outcomes.
    """
    report = GateReport("temporal")
    if t4_retention is None:
        report.add(
            "four_week_retention",
            False,
            detail="not evaluated: fewer than five scored weeks, so there is no "
            "non-overlapping comparison to make",
            blocks=BLOCKS_NOTHING,
        )
    else:
        report.add(
            "four_week_retention",
            t4_retention >= gates.t4_retention,
            detail=f"retention {t4_retention:.4f} against floor {gates.t4_retention}",
        )
    if t4_profile_similarity is None:
        report.add(
            "four_week_profile_similarity",
            False,
            detail="not evaluated: no non-overlapping comparison available",
            blocks=BLOCKS_NOTHING,
        )
    else:
        report.add(
            "four_week_profile_similarity",
            t4_profile_similarity >= gates.t4_profile_similarity,
            detail=f"similarity {t4_profile_similarity:.4f} against floor "
            f"{gates.t4_profile_similarity}",
        )
    return report


def evaluate_publication(reports: list[GateReport]) -> PublicationDecision:
    """Combine the gate families into a per-surface publication decision."""
    blocking: list[str] = []
    topic_only: list[str] = []
    for report in reports:
        for check in report.failures:
            label = f"{report.family}.{check.name}"
            if check.blocks == BLOCKS_TOPIC:
                topic_only.append(label)
            else:
                blocking.append(label)
    return PublicationDecision(
        publish_labels=not blocking,
        publish_topic_block=not blocking and not topic_only,
        blocking_failures=blocking,
        topic_only_failures=topic_only,
    )


def gate_frame(reports: list[GateReport]) -> pd.DataFrame:
    """Every check from every family, as one frame for the run's output."""
    frames = [report.to_frame() for report in reports if report.checks]
    if not frames:
        return pd.DataFrame(columns=["family", "check", "passed", "blocks", "detail"])
    return pd.concat(frames, ignore_index=True)
