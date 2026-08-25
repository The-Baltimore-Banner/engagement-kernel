"""Choosing k, and refusing to choose one when nothing survives.

Three screens decide whether a candidate number of clusters is admissible, and none
of them asks whether the clusters look interesting:

**Seed stability.** Fit the same k from many starting points. If the groups move,
they are a property of the initialisation. Also checked: that the *set* of
substantial clusters is the same across seeds -- a cluster that appears under some
seeds and not others is not a cluster.

**Cross-algorithm agreement.** Fit the same k with a different algorithm and ask
whether it finds the same groups. The bar is per-k and derived, because two
algorithms that share an objective agree well above zero on a population with no
structure at all -- and that chance level *falls* as k rises. A single flat number
inherited from a specification is the wrong shape as well as the wrong level: it
certifies high k too easily and refuses low k too readily.

The bars this package ships are one newsroom's derivation on one panel, and chance
agreement depends on the row count, the dimensionality and the population's own
correlation structure. So they are a working default, not the method: derive your
own with ``tools/derive_cross_algorithm_bars.py`` and declare them in a gates file.
A k with no declared bar is refused either way.

**Centroid distinctness.** Two clusters whose centroid profiles correlate above the
threshold are one cluster reported twice.

Then the champion is the **smallest** surviving k, derived rather than chosen. A
larger k always fits better, so "which k looks best" has one answer and it is
always the largest; preferring the smallest survivor is what makes the screens the
decision rather than a formality.

The part worth reading closely
------------------------------

A screen read off the single matrix a run happened to assemble is not a
reproducible verdict. Measured on a real refit: one candidate's seed-stability was
0.97 on its own fit matrix and 0.49 on every one of twenty arbitrary two-row drops
of that same matrix -- and being the smallest survivor, it became the champion. The
cross-algorithm statistic moved as far in the other direction, spanning 0.30 to
0.71 at one k across the same drops. Neither movement is estimator noise; both are
real properties of the matrix at a resolution finer than the pipeline can hold
between freezes.

So each candidate is re-screened on many perturbed panels -- the same panel with a
tiny fraction of rows dropped -- and survives only if the one-sided 95% lower bound
on its all-screens survival rate clears the floor. The screens and their thresholds
are unchanged. What changed is that the verdict is now a statement about the
panels the pipeline could equally have built, rather than about the one it did.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engagement_kernel.engagement.assignment import median_pairwise_ari
from engagement_kernel.engagement.config import GateThresholds
from engagement_kernel.engagement.windows import TRAILING_WINDOW_DAYS, WEEK_BIN_COUNT

#: z for a one-sided 95% bound.
WILSON_Z = 1.645

SCREEN_SEED_STABILITY = "seed_stability"
SCREEN_CROSS_ALGORITHM = "cross_algorithm"
SCREEN_DISTINCTNESS = "distinctness"
SCREEN_NAMES: tuple[str, ...] = (
    SCREEN_SEED_STABILITY,
    SCREEN_CROSS_ALGORITHM,
    SCREEN_DISTINCTNESS,
)


class SelectionError(ValueError):
    """Selection was asked for a verdict it cannot support."""


def wilson_lower_bound(successes: int, draws: int, *, z: float = WILSON_Z) -> float:
    """One-sided Wilson lower bound on a pass rate.

    The normal approximation is worst exactly where this is used -- rates near 0 and
    1 -- so the Wilson interval is the right one. Fifty successes out of fifty still
    returns a bound below 1, which is the point: it is strong evidence, not proof.
    """
    if draws <= 0:
        raise SelectionError("a survival rate needs at least one draw")
    p = successes / draws
    denominator = 1.0 + z * z / draws
    centre = p + z * z / (2.0 * draws)
    half_width = z * np.sqrt(p * (1.0 - p) / draws + z * z / (4.0 * draws * draws))
    return float(max(0.0, (centre - half_width) / denominator))


def algorithm_agreement(labels_a: np.ndarray, labels_b: np.ndarray) -> float:
    from sklearn.metrics import adjusted_rand_score

    return float(adjusted_rand_score(labels_a, labels_b))


def cross_algorithm_statistic(values: np.ndarray, k: int, *, n_seeds: int) -> float:
    """Exactly the number the cross-algorithm screen compares against its bar.

    Public because deriving the bar means measuring this statistic on panels with
    no cluster structure, and a derivation that re-implements the statistic
    calibrates something else. ``tools/derive_cross_algorithm_bars.py`` calls this;
    a test asserts it returns what :func:`_run_screens` reports, so "the same code
    path" is checked rather than asserted in a comment.
    """
    champion, _ = _kmeans_champion(values, k, n_seeds)
    return algorithm_agreement(champion.labels_, _hierarchical_labels(values, k))


def centroid_distinctness_violations(centroids: np.ndarray, threshold: float) -> int:
    """Pairs of clusters whose centroid profiles correlate above the threshold."""
    violations = 0
    for i in range(len(centroids)):
        for j in range(i + 1, len(centroids)):
            left, right = centroids[i], centroids[j]
            if np.std(left) == 0 or np.std(right) == 0:
                continue
            if np.corrcoef(left, right)[0, 1] > threshold:
                violations += 1
    return violations


@dataclass(frozen=True)
class SeedStability:
    median_ari: float
    unstable_row_share: float
    major_cluster_persistent: bool
    major_cluster_count_min: int
    major_cluster_count_max: int

    def passes(self, gates: GateThresholds) -> bool:
        return self.median_ari >= gates.seed_ari and self.major_cluster_persistent


def match_labels(reference: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Relabel ``other`` to agree with ``reference`` wherever it can.

    Cluster ids are arbitrary: two fits can find identical partitions and number
    them differently. Comparing them row by row without matching first therefore
    reports almost every row as unstable *even when the partitions are identical* --
    a diagnostic that reads 1.0 at every k, including where seed agreement is
    perfect, and so says nothing. Matching is by maximum total overlap over the
    contingency table.
    """
    from scipy.optimize import linear_sum_assignment

    reference_ids = np.unique(reference)
    other_ids = np.unique(other)
    overlap = np.zeros((len(other_ids), len(reference_ids)), dtype=float)
    for i, left in enumerate(other_ids):
        for j, right in enumerate(reference_ids):
            overlap[i, j] = np.sum((other == left) & (reference == right))
    rows, columns = linear_sum_assignment(-overlap)
    mapping = {int(other_ids[i]): int(reference_ids[j]) for i, j in zip(rows, columns, strict=True)}
    return np.array([mapping.get(int(value), int(value)) for value in other])


def seed_stability(labelings: list[np.ndarray], gates: GateThresholds) -> SeedStability:
    """Agreement across seeds, and whether the substantial clusters are the same set."""
    median_ari = median_pairwise_ari(labelings)
    reference = labelings[0]
    matched = [reference, *(match_labels(reference, other) for other in labelings[1:])]
    stacked = np.stack(matched)
    n_rows = stacked.shape[1]
    stable = np.array(
        [len(np.unique(stacked[:, index])) == 1 for index in range(n_rows)], dtype=bool
    )

    def major_count(labels: np.ndarray) -> int:
        _, counts = np.unique(labels, return_counts=True)
        return int((counts / len(labels) >= gates.major_cluster_share).sum())

    counts = {major_count(labels) for labels in labelings}
    return SeedStability(
        median_ari=median_ari,
        # Reported rather than gated: a threshold for it has never been measured, and
        # setting one from the run that produced it would not be a gate. It is
        # computed on *matched* labels, so it means what it says.
        unstable_row_share=float(1.0 - stable.mean()),
        major_cluster_persistent=len(counts) == 1,
        major_cluster_count_min=min(counts),
        major_cluster_count_max=max(counts),
    )


def _kmeans_champion(values: np.ndarray, k: int, n_seeds: int):
    """Best-inertia k-means over ``n_seeds`` single-start fits, plus every labelling.

    One start per seed rather than scikit-learn's internal restarts, because the
    per-seed labellings *are* the stability input: letting the library pick the best
    of ten internally would hide exactly the variation being measured.
    """
    from sklearn.cluster import KMeans

    labelings: list[np.ndarray] = []
    best = None
    for seed in range(n_seeds):
        model = KMeans(n_clusters=k, init="k-means++", n_init=1, random_state=seed)
        labels = model.fit_predict(values)
        labelings.append(labels)
        if best is None or model.inertia_ < best.inertia_:
            best = model
    if best is None:  # pragma: no cover - n_seeds is validated upstream
        raise SelectionError("no seeds were fit")
    return best, labelings


def _hierarchical_labels(values: np.ndarray, k: int) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering

    return AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(values)


def _run_screens(
    values: np.ndarray,
    k: int,
    n_seeds: int,
    gates: GateThresholds,
) -> tuple[dict[str, bool], dict[str, float]]:
    champion, labelings = _kmeans_champion(values, k, n_seeds)
    stability = seed_stability(labelings, gates)
    cross = algorithm_agreement(champion.labels_, _hierarchical_labels(values, k))
    violations = centroid_distinctness_violations(
        champion.cluster_centers_, gates.centroid_distinctness_corr
    )
    passed = {
        SCREEN_SEED_STABILITY: stability.passes(gates),
        SCREEN_CROSS_ALGORITHM: cross >= gates.cross_algorithm_bar(k),
        SCREEN_DISTINCTNESS: violations == 0,
    }
    statistics = {
        SCREEN_SEED_STABILITY: stability.median_ari,
        SCREEN_CROSS_ALGORITHM: cross,
        SCREEN_DISTINCTNESS: float(violations),
    }
    return passed, statistics


@dataclass(frozen=True)
class ScreenOutcome:
    name: str
    pass_rate: float
    pass_rate_lower_bound: float
    mean: float
    std: float


@dataclass(frozen=True)
class SelectionStability:
    """Would this k have survived on a panel the pipeline could equally have built?"""

    k: int
    draws: int
    rows_dropped: int
    row_fraction: float
    rng_seed: int
    screens: tuple[ScreenOutcome, ...]
    survival_rate: float
    survival_lower_bound: float

    def fragile_screens(self, floor: float) -> list[str]:
        """The screens whose own pass rate cannot clear the floor -- the diagnosis."""
        return [screen.name for screen in self.screens if screen.pass_rate_lower_bound < floor]


def selection_stability(
    values: np.ndarray,
    k: int,
    gates: GateThresholds,
    *,
    n_seeds: int,
) -> SelectionStability:
    """Re-run the screens on perturbed panels and bound the survival rate."""
    n_rows = values.shape[0]
    rows_dropped = max(1, int(round(n_rows * gates.selection_perturbation_row_fraction)))
    if rows_dropped >= n_rows:
        raise SelectionError(
            f"the perturbation would drop {rows_dropped} of {n_rows} rows, leaving nothing "
            "to screen"
        )
    rng = np.random.default_rng(gates.selection_rng_seed + k)
    per_screen: dict[str, list[float]] = {name: [] for name in SCREEN_NAMES}
    per_screen_passed: dict[str, int] = dict.fromkeys(SCREEN_NAMES, 0)
    survivals = 0

    for _ in range(gates.selection_perturbation_draws):
        drop = rng.choice(n_rows, size=rows_dropped, replace=False)
        keep = np.setdiff1d(np.arange(n_rows), drop, assume_unique=False)
        passed, statistics = _run_screens(values[keep], k, n_seeds, gates)
        for name in SCREEN_NAMES:
            per_screen[name].append(statistics[name])
            per_screen_passed[name] += int(passed[name])
        survivals += int(all(passed.values()))

    draws = gates.selection_perturbation_draws
    screens = tuple(
        ScreenOutcome(
            name=name,
            pass_rate=per_screen_passed[name] / draws,
            pass_rate_lower_bound=wilson_lower_bound(per_screen_passed[name], draws),
            mean=float(np.mean(per_screen[name])),
            std=float(np.std(per_screen[name], ddof=0)),
        )
        for name in SCREEN_NAMES
    )
    return SelectionStability(
        k=k,
        draws=draws,
        rows_dropped=rows_dropped,
        row_fraction=gates.selection_perturbation_row_fraction,
        rng_seed=gates.selection_rng_seed,
        screens=screens,
        survival_rate=survivals / draws,
        survival_lower_bound=wilson_lower_bound(survivals, draws),
    )


@dataclass
class KCandidate:
    """One candidate k, with what the screens said about it."""

    k: int
    #: The statistics on the unperturbed matrix. Reported, never selected on, so a
    #: sweep stays comparable with one run before the perturbation was added.
    seed_report: SeedStability
    cross_algorithm_ari: float
    centroids: np.ndarray
    labels: np.ndarray
    silhouette: float
    size_balance: float
    distinctness_violations: int
    stability: SelectionStability | None = field(default=None)

    def screen_failures(self, gates: GateThresholds) -> list[str]:
        if self.stability is None:
            raise SelectionError(
                f"k={self.k} has no perturbation record, so its screens could only be read "
                "off the one matrix this run assembled -- which is the failure the "
                "perturbation exists to close. Build candidates through sweep_k"
            )
        failures = self.stability.fragile_screens(gates.selection_survival_floor)
        if not failures and self.stability.survival_lower_bound < gates.selection_survival_floor:
            # Every screen holds on its own, but on different draws, so the k rarely
            # clears all three at once. Still a failure, and named for what it is.
            failures.append("joint_reproducibility")
        return failures


def sweep_k(
    matrix: pd.DataFrame,
    k_values: Iterable[int],
    gates: GateThresholds,
    *,
    n_seeds: int,
) -> list[KCandidate]:
    """Fit and screen every candidate k."""
    from sklearn.metrics import silhouette_score

    values = matrix.to_numpy(dtype=float)
    candidates: list[KCandidate] = []
    for k in sorted(set(k_values)):
        if k >= values.shape[0]:
            raise SelectionError(
                f"k={k} is not below the {values.shape[0]} rows being fit; a cluster per "
                "row is not a model"
            )
        champion, labelings = _kmeans_champion(values, k, n_seeds)
        report = seed_stability(labelings, gates)
        cross = algorithm_agreement(champion.labels_, _hierarchical_labels(values, k))
        _, counts = np.unique(champion.labels_, return_counts=True)
        shares = counts / counts.sum()
        candidates.append(
            KCandidate(
                k=k,
                seed_report=report,
                cross_algorithm_ari=cross,
                centroids=champion.cluster_centers_,
                labels=champion.labels_,
                silhouette=float(silhouette_score(values, champion.labels_)),
                size_balance=float(shares.min() / shares.max()),
                distinctness_violations=centroid_distinctness_violations(
                    champion.cluster_centers_, gates.centroid_distinctness_corr
                ),
                stability=selection_stability(values, k, gates, n_seeds=n_seeds),
            )
        )
    return candidates


def select_k(
    candidates: list[KCandidate],
    gates: GateThresholds,
) -> tuple[int | None, pd.DataFrame]:
    """Screen every candidate and return the smallest survivor, with the table.

    ``None`` when nothing survives, and that is a real outcome rather than an error
    to route around: freezing a model that failed its own screens is worse than
    shipping nothing, because the labels would be published and used.
    """
    rows: list[dict[str, object]] = []
    survivors: list[int] = []
    for candidate in sorted(candidates, key=lambda item: item.k):
        failures = candidate.screen_failures(gates)
        row: dict[str, object] = {
            "k": candidate.k,
            "seed_median_ari": candidate.seed_report.median_ari,
            "unstable_row_share": candidate.seed_report.unstable_row_share,
            "major_clusters_min": candidate.seed_report.major_cluster_count_min,
            "major_clusters_max": candidate.seed_report.major_cluster_count_max,
            "cross_algorithm_ari": candidate.cross_algorithm_ari,
            "cross_algorithm_bar": gates.cross_algorithm_bar(candidate.k),
            "distinctness_violations": candidate.distinctness_violations,
            "silhouette": candidate.silhouette,
            "size_balance": candidate.size_balance,
        }
        if candidate.stability is not None:
            row["survival_rate"] = candidate.stability.survival_rate
            row["survival_lower_bound"] = candidate.stability.survival_lower_bound
            for screen in candidate.stability.screens:
                row[f"{screen.name}_pass_rate"] = screen.pass_rate
                row[f"{screen.name}_mean"] = screen.mean
        row["failures"] = ",".join(failures)
        row["survives"] = not failures
        rows.append(row)
        if not failures:
            survivors.append(candidate.k)
    return (min(survivors) if survivors else None), pd.DataFrame(rows)


def assert_champion_derived(
    champion_k: int,
    candidates: list[KCandidate],
    gates: GateThresholds,
) -> None:
    """The champion must be the smallest surviving k, not a hand-set one.

    A deployment may deliberately freeze a different k -- but only one that also
    survives every screen. A non-surviving override is refused rather than recorded,
    because the labels would be published either way and the record would be the
    only thing saying they should not be trusted.
    """
    derived, _ = select_k(candidates, gates)
    if derived is None:
        raise SelectionError(
            f"k={champion_k} was named as the champion but no k survived the screens"
        )
    if champion_k != derived:
        surviving = [c.k for c in candidates if not c.screen_failures(gates)]
        if champion_k not in surviving:
            raise SelectionError(
                f"k={champion_k} does not survive the screens (surviving: {surviving}); "
                "refusing to freeze a model that failed its own selection rule"
            )


# --- temporal stability -----------------------------------------------------


@dataclass(frozen=True)
class TemporalComparison:
    """Label agreement between two snapshots, for rows labelled in both."""

    retention: float
    transitions: pd.DataFrame
    n_rows: int
    weeks_apart: int
    #: Only a non-overlapping comparison is evidence. Adjacent weeks share 21 of
    #: their 28 window days, so their agreement is mechanically high whatever the
    #: model does. The gap that makes them disjoint is the window's own width in
    #: weeks, taken from :data:`~engagement_kernel.engagement.windows.WEEK_BIN_COUNT`
    #: rather than written out again -- it was a bare 4 here and a bare 28 below,
    #: which is the same prescription stated in three places and revisable in none.
    is_gate: bool
    overlap_caveat: str | None


def temporal_comparison(
    labels_t: pd.Series,
    labels_t_plus: pd.Series,
    *,
    weeks_apart: int,
) -> TemporalComparison:
    joined = pd.DataFrame({"t": labels_t, "t_plus": labels_t_plus}).dropna()
    retention = float((joined["t"] == joined["t_plus"]).mean()) if len(joined) else 0.0
    transitions = (
        pd.crosstab(joined["t"], joined["t_plus"], normalize="index")
        if len(joined)
        else pd.DataFrame()
    )
    caveat = None
    days_per_week = TRAILING_WINDOW_DAYS // WEEK_BIN_COUNT
    if weeks_apart < WEEK_BIN_COUNT:
        shared = TRAILING_WINDOW_DAYS - days_per_week * weeks_apart
        caveat = (
            f"the two windows share {shared} of {TRAILING_WINDOW_DAYS} days, so this "
            "agreement is mechanically inflated -- monitoring, not evidence"
        )
    return TemporalComparison(
        retention=retention,
        transitions=transitions,
        n_rows=len(joined),
        weeks_apart=weeks_apart,
        is_gate=weeks_apart >= WEEK_BIN_COUNT,
        overlap_caveat=caveat,
    )
