"""The frozen model bundle: everything a weekly run needs, and nothing it may re-fit.

A weekly score must not re-fit anything. If it did, the space would move every week
and a reader whose behaviour never changed would drift between clusters as the
population around them shifted -- so a cluster id would mean "how this reader
compares to this week's audience" rather than "which kind of reader this is". The
bundle is what makes the difference enforceable rather than aspirational: it holds
the parameters, and the scoring path takes a bundle and a week.

Two fields in here are less obvious than the rest and both exist because of a
failure that produced plausible numbers.

``lineage`` records the *feature version* the bundle was fit under -- the
declarations that change what the numbers mean: the article-view definition, the
scored population, the week anchor, the timezone, the bucket map, the click unit and
the surface. A frozen calibration is only valid against the lineage it was fit on: a
z-score is taken against a mean and standard deviation from one specific
construction, and two constructions of the same feature can differ by more than a
factor of two. Recording the lineage lets a weekly run *refuse* the combination that
is otherwise silent -- a bundle fit under one set of declarations being fed rows
built under another, because a deployment changed a manifest and nothing checked.

``bucket_map_snapshot`` records the mapping, not just its version. A version string
proves two runs disagree; the snapshot says how. The topic block is a vector over
those buckets, and a bundle that carried only ``v3`` could not tell you which
sections ``v3`` put in ``news``.

``label_map`` is many-to-one on purpose. Several raw components can legitimately
collapse into one published name -- three tiers of light reader are still light
readers -- so the values need not be unique. The raw component id is the
relabel-proof identity and is published beside the name for exactly that reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from engagement_kernel.engagement.assignment import OODThresholds
from engagement_kernel.engagement.surfaces import SurfaceSpace


class FrozenBundleError(ValueError):
    """A frozen bundle is internally inconsistent, or does not fit the data offered."""


@dataclass
class FrozenSurface:
    """One frozen clustering surface."""

    name: str
    k: int
    centroids: np.ndarray
    feature_columns: list[str]
    ood: OODThresholds
    #: Raw component id -> the published label. Many-to-one is permitted.
    label_map: dict[int, str]
    seed_ari: float
    survival_lower_bound: float

    def validate(self) -> None:
        if self.centroids.shape[0] != self.k:
            raise FrozenBundleError(
                f"{self.name}: {self.centroids.shape[0]} centroids for k={self.k}"
            )
        if self.centroids.shape[1] != len(self.feature_columns):
            raise FrozenBundleError(
                f"{self.name}: centroids are {self.centroids.shape[1]} wide but the surface "
                f"declares {len(self.feature_columns)} features. Scoring would compute "
                "distances in a space the model was not fit in"
            )
        if set(self.label_map) != set(range(self.k)):
            raise FrozenBundleError(
                f"{self.name}: label_map must name every component id 0..{self.k - 1}; a "
                "component with no label would publish a bare integer as a segment name"
            )
        empty = [index for index, label in self.label_map.items() if not str(label).strip()]
        if empty:
            raise FrozenBundleError(f"{self.name}: components {empty} have empty labels")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "k": self.k,
            "centroids": self.centroids.tolist(),
            "feature_columns": list(self.feature_columns),
            "ood": self.ood.to_dict(),
            "label_map": {str(k): v for k, v in self.label_map.items()},
            "seed_ari": self.seed_ari,
            "survival_lower_bound": self.survival_lower_bound,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> FrozenSurface:
        return cls(
            name=str(payload["name"]),
            k=int(payload["k"]),
            centroids=np.asarray(payload["centroids"], dtype=float),
            feature_columns=list(payload["feature_columns"]),
            ood=OODThresholds.from_dict(payload["ood"]),
            label_map={int(k): str(v) for k, v in payload["label_map"].items()},
            seed_ari=float(payload["seed_ari"]),
            survival_lower_bound=float(payload["survival_lower_bound"]),
        )


@dataclass
class FrozenBundle:
    """The complete versioned artifact one weekly run applies."""

    model_version: str
    frozen_at: str
    #: The feature version this was fit under. See the module docstring.
    lineage: str
    surface_space: SurfaceSpace
    main: FrozenSurface
    training_panel: dict[str, object]
    bucket_map_version: str
    bucket_map_snapshot: dict[str, list[str]]
    measures_params: dict[str, object] = field(default_factory=dict)
    k_selection: dict[str, object] = field(default_factory=dict)
    notes: dict[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        self.main.validate()
        if self.main.feature_columns != self.surface_space.feature_columns:
            raise FrozenBundleError(
                "the frozen surface's feature columns differ from the calibrated surface's. "
                "The centroids and the calibrations would then describe different spaces, "
                "and every distance would be arithmetically valid and meaningless"
            )
        for required in ("rule", "seed"):
            if required not in self.training_panel:
                raise FrozenBundleError(f"training_panel is missing {required!r}")
        if self.measures_params:
            weights = self.measures_params.get("block_weights", {})
            if weights and abs(sum(weights.values()) - 1.0) > 1e-6:
                raise FrozenBundleError("frozen measure block weights must sum to 1")

    def assert_lineage(self, feature_version: str) -> None:
        """Refuse to score rows built under different declarations.

        The check that has no alternative. Every other mismatch between a bundle and
        a delivery shows up as a missing column or a shape error; this one produces a
        complete set of plausible scores against a calibration fit on something else.
        """
        if feature_version != self.lineage:
            raise FrozenBundleError(
                "this bundle was fit under a different feature version.\n"
                f"  bundle : {self.lineage}\n"
                f"  offered: {feature_version}\n"
                "The calibrations are z-scores against that construction, so applying them "
                "here would produce a full set of scores with nothing wrong on the face of "
                "them. Re-fit, or score with the bundle that matches"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_version": self.model_version,
            "frozen_at": self.frozen_at,
            "lineage": self.lineage,
            "surface_space": self.surface_space.to_dict(),
            "main": self.main.to_dict(),
            "training_panel": self.training_panel,
            "bucket_map_version": self.bucket_map_version,
            "bucket_map_snapshot": self.bucket_map_snapshot,
            "measures_params": self.measures_params,
            "k_selection": self.k_selection,
            "notes": self.notes,
        }

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> FrozenBundle:
        payload = json.loads(Path(path).read_text())
        bundle = cls(
            model_version=str(payload["model_version"]),
            frozen_at=str(payload["frozen_at"]),
            lineage=str(payload["lineage"]),
            surface_space=SurfaceSpace.from_dict(payload["surface_space"]),
            main=FrozenSurface.from_dict(payload["main"]),
            training_panel=dict(payload["training_panel"]),
            bucket_map_version=str(payload["bucket_map_version"]),
            bucket_map_snapshot={k: list(v) for k, v in payload["bucket_map_snapshot"].items()},
            measures_params=dict(payload.get("measures_params", {})),
            k_selection=dict(payload.get("k_selection", {})),
            notes=dict(payload.get("notes", {})),
        )
        bundle.validate()
        return bundle


def rank_labels(
    centroids: np.ndarray,
    feature_columns: list[str],
    *,
    prefix: str = "tier",
) -> dict[int, str]:
    """Provisional labels, ordered by the centroid's mean position on the surface.

    Every surface dimension is a z-score oriented so higher means more activity, so
    the mean of a centroid's coordinates is a defensible ordering of "how engaged".

    These are **placeholders and they are named like placeholders.** A published
    segment name is an editorial and commercial decision that a person makes after
    looking at the clusters -- the interpretability gate is what stops a run
    publishing these -- and calling one ``tier_1`` rather than ``Casual readers``
    makes it obvious that nobody has looked yet.
    """
    if centroids.shape[1] != len(feature_columns):
        raise FrozenBundleError("centroid width does not match the feature columns")
    order = np.argsort(centroids.mean(axis=1))
    labels: dict[int, str] = {}
    for rank, component in enumerate(order, start=1):
        labels[int(component)] = f"{prefix}_{rank}"
    return labels
