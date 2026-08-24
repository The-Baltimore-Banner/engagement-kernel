"""Assigning readers to clusters, with the confidence to say when it is a guess.

A nearest-centroid assignment always succeeds. That is the problem this module
exists to answer: every reader gets a label whether or not they resemble the
cluster they were put in, and a label with no confidence beside it reads as a fact.

So four numbers travel with every assignment:

* the distance to the assigned centroid;
* the distance to the second-nearest;
* the margin between them, which is the honest confidence -- a reader sitting
  equidistant between two clusters has been assigned by a rounding error;
* where that distance sits in the *training* distribution of distances for that
  cluster, which says whether the reader looks like the readers the cluster was
  built from.

The last of those is what turns into the out-of-distribution flags, and it is a
per-cluster comparison for a reason: a diffuse cluster of light readers has larger
typical distances than a tight cluster of heavy ones, so one global distance
threshold would flag the whole of the diffuse cluster and none of the tight one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Percentiles of the training distance distribution the flags fire at.
OOD_PERCENTILE = 0.95
OOD_SEVERE_PERCENTILE = 0.99


class AssignmentError(ValueError):
    """An assignment could not be made against the centroids supplied."""


@dataclass(frozen=True)
class CentroidAssignments:
    labels: np.ndarray
    distance: np.ndarray
    second_distance: np.ndarray
    margin: np.ndarray


def nearest_centroid(matrix: np.ndarray, centroids: np.ndarray) -> CentroidAssignments:
    """Assign each row to its nearest centroid, keeping the runner-up."""
    if matrix.ndim != 2 or centroids.ndim != 2:
        raise AssignmentError("both the matrix and the centroids must be two-dimensional")
    if matrix.shape[1] != centroids.shape[1]:
        raise AssignmentError(
            f"the matrix has {matrix.shape[1]} features and the centroids have "
            f"{centroids.shape[1]}. A frozen model applied in a different feature space "
            "produces distances that are arithmetically fine and mean nothing"
        )
    diffs = matrix[:, None, :] - centroids[None, :, :]
    distances = np.sqrt((diffs**2).sum(axis=2))
    order = np.argsort(distances, axis=1)
    labels = order[:, 0]
    rows = np.arange(len(matrix))
    nearest = distances[rows, labels]
    if centroids.shape[0] > 1:
        second = distances[rows, order[:, 1]]
    else:
        # One cluster: there is no runner-up, and the margin is not zero -- zero
        # would read as "maximally ambiguous" when the assignment is in fact forced.
        second = np.full_like(nearest, np.inf)
    return CentroidAssignments(
        labels=labels, distance=nearest, second_distance=second, margin=second - nearest
    )


@dataclass(frozen=True)
class OODThresholds:
    """Per-cluster distance thresholds, fit on the training population."""

    percentile_95: dict[int, float]
    percentile_99: dict[int, float]
    training_distances: dict[int, np.ndarray]

    def to_dict(self) -> dict[str, object]:
        return {
            "percentile_95": {str(k): v for k, v in self.percentile_95.items()},
            "percentile_99": {str(k): v for k, v in self.percentile_99.items()},
            "training_distances": {str(k): v.tolist() for k, v in self.training_distances.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict) -> OODThresholds:
        return cls(
            percentile_95={int(k): float(v) for k, v in payload["percentile_95"].items()},
            percentile_99={int(k): float(v) for k, v in payload["percentile_99"].items()},
            training_distances={
                int(k): np.asarray(v, dtype=float) for k, v in payload["training_distances"].items()
            },
        )


def fit_ood_thresholds(training_matrix: np.ndarray, centroids: np.ndarray) -> OODThresholds:
    """Fit the per-cluster distance thresholds on the training population."""
    assignments = nearest_centroid(training_matrix, centroids)
    p95: dict[int, float] = {}
    p99: dict[int, float] = {}
    distances: dict[int, np.ndarray] = {}
    for cluster in range(centroids.shape[0]):
        cluster_distances = assignments.distance[assignments.labels == cluster]
        if cluster_distances.size == 0:
            raise AssignmentError(
                f"no training row was assigned to cluster {cluster}, so it has no distance "
                "distribution. An empty cluster in a frozen model will still take readers "
                "at scoring time, with no basis for calling any of them typical"
            )
        p95[cluster] = float(np.quantile(cluster_distances, OOD_PERCENTILE))
        p99[cluster] = float(np.quantile(cluster_distances, OOD_SEVERE_PERCENTILE))
        distances[cluster] = np.sort(cluster_distances)
    return OODThresholds(percentile_95=p95, percentile_99=p99, training_distances=distances)


def confidence_and_ood(
    assignments: CentroidAssignments,
    thresholds: OODThresholds,
) -> pd.DataFrame:
    """The published confidence columns and the two out-of-distribution flags."""
    n = len(assignments.labels)
    relative = np.empty(n)
    ood = np.zeros(n, dtype=int)
    severe = np.zeros(n, dtype=int)
    for index in range(n):
        cluster = int(assignments.labels[index])
        training = thresholds.training_distances.get(cluster)
        if training is None or training.size == 0:
            raise AssignmentError(
                f"cluster {cluster} has no training distance distribution to compare against"
            )
        relative[index] = (
            np.searchsorted(training, assignments.distance[index], side="right") / training.size
        )
        if assignments.distance[index] > thresholds.percentile_95[cluster]:
            ood[index] = 1
        if assignments.distance[index] > thresholds.percentile_99[cluster]:
            severe[index] = 1
    return pd.DataFrame(
        {
            "cluster_index": assignments.labels,
            "cluster_distance": assignments.distance,
            "cluster_second_distance": assignments.second_distance,
            "cluster_confidence_margin": assignments.margin,
            "within_cluster_distance_pct": relative,
            "out_of_distribution": ood,
            "out_of_distribution_severe": severe,
        }
    )


def median_pairwise_ari(labelings: list[np.ndarray]) -> float:
    """Median pairwise adjusted Rand index across a set of labelings.

    One number for "do different starting points find the same groups". The median
    rather than the mean, because a single pathological seed should not decide the
    verdict either way.
    """
    from sklearn.metrics import adjusted_rand_score

    scores = [
        adjusted_rand_score(labelings[i], labelings[j])
        for i in range(len(labelings))
        for j in range(i + 1, len(labelings))
    ]
    return float(np.median(scores)) if scores else 1.0
