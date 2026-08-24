"""The engagement lane, end to end: fit a model once, then score every week.

One function does the whole thing (:func:`run_lane`) and it reads top to bottom in
the order the work has to happen. The order is the argument:

1. resolve the configuration from the delivery's manifest and the deployment's
   bucket map -- nothing below this line guesses at a declaration;
2. build every complete week's features, spine first;
3. sample the training panel from the full-window rows, and balance it by month;
4. fit the surface on the panel's content-active subset;
5. sweep candidate k, screen each on perturbed panels, and take the smallest
   survivor -- or freeze nothing;
6. freeze: centroids, calibrations, distance thresholds, measure parameters, and the
   lineage the bundle is only valid against;
7. score every week with that frozen bundle, re-fitting nothing;
8. compare labels four weeks apart, run the gates, and write the tables.

Step 5 can end with no champion, and that is a real outcome rather than an error to
route around. A model that failed its own selection screens is worse than no model,
because its labels would be published and acted on. When it happens the run still
writes the k-selection table and the gate report -- the two things somebody needs to
diagnose it -- and returns without a bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from engagement_kernel.contract.manifest import Manifest, load_manifest
from engagement_kernel.engagement import gates as gate_layer
from engagement_kernel.engagement import measures as measures_layer
from engagement_kernel.engagement import outputs as output_layer
from engagement_kernel.engagement import panel as panel_layer
from engagement_kernel.engagement import scoring, selection, surfaces
from engagement_kernel.engagement.assignment import fit_ood_thresholds
from engagement_kernel.engagement.buckets import SectionBucketMap, load_bucket_map
from engagement_kernel.engagement.config import LaneConfig
from engagement_kernel.engagement.features import (
    WeeklyInputs,
    build_weekly_features,
    stack_weeks,
)
from engagement_kernel.engagement.freeze import FrozenBundle, FrozenSurface, rank_labels
from engagement_kernel.engagement.spine import assert_sources_fresh, fit_cohort_mask
from engagement_kernel.intermediate import build_delivery
from engagement_kernel.intermediate.tables import LOCAL_DATE_COLUMN

READER_KEY = "reader_id"
WEEK_KEY = "as_of_week_end"

#: Raw atomics reported in the cluster profile, over and above the per-channel views.
#:
#: In the units a newsroom thinks in, on purpose: "reads eleven articles a week"
#: is a sentence somebody can act on and "+1.4 standard deviations" is not.
PROFILE_ATOMICS: tuple[str, ...] = (
    "overall_active_days_28d",
    "resolved_section_views_28d",
    "distinct_sections_28d",
    "topic_entropy_28d",
    "email_clicks_28d",
    "email_click_active_weeks_4",
    "community_actions_28d",
)


class LaneError(ValueError):
    """The lane could not run as configured."""


@dataclass
class LaneResult:
    """Everything one run produced, and the record of what it decided."""

    config: LaneConfig
    weeks: list[date]
    bundle: FrozenBundle | None
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    decision: gate_layer.PublicationDecision | None = None
    champion_k: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def froze_a_model(self) -> bool:
        return self.bundle is not None

    def summary(self) -> str:
        lines = [self.config.describe(), ""]
        lines.append(f"weeks scored          : {len(self.weeks)}")
        if self.weeks:
            lines.append(
                f"period                : {self.weeks[0].isoformat()} .. "
                f"{self.weeks[-1].isoformat()}"
            )
        lines.append(
            f"champion k            : {self.champion_k if self.champion_k is not None else 'none'}"
        )
        if self.decision is not None:
            lines.append(f"publication           : {self.decision.describe()}")
        for name, frame in sorted(self.tables.items()):
            lines.append(f"  {name:24s} {len(frame):8d} rows")
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


def _latest_local_date(frames: dict[str, pd.DataFrame]) -> dict[str, date]:
    out: dict[str, date] = {}
    for name, frame in frames.items():
        if frame is None or frame.empty or LOCAL_DATE_COLUMN not in frame.columns:
            continue
        out[name] = pd.to_datetime(frame[LOCAL_DATE_COLUMN]).max().date()
    return out


def read_intermediate(directory: str | Path) -> WeeklyInputs:
    """Load the intermediate tables from a directory of Parquet files.

    An optional table's *absence* is carried as ``None`` rather than an empty frame,
    because the two mean different things and the surface resolution depends on the
    difference.
    """
    target = Path(directory)

    def read(name: str, required: bool) -> pd.DataFrame | None:
        path = target / f"{name}.parquet"
        if not path.exists():
            if required:
                raise LaneError(f"the intermediate build produced no {name}.parquet")
            return None
        return pd.read_parquet(path)

    return WeeklyInputs(
        subscription_state_interval=read("subscription_state_interval", True),
        reader_channel_day=read("reader_channel_day", True),
        reader_section_day=read("reader_section_day", True),
        reader_email_day=read("reader_email_day", False),
        reader_community_day=read("reader_community_day", False),
    )


def inputs_from_build(directory: str | Path) -> WeeklyInputs:
    """Run the intermediate build over a delivery and hand back its tables in memory."""
    result = build_delivery(directory)
    failed = result.failed_checks
    if failed:
        raise LaneError(
            f"the intermediate build failed its own checks: {', '.join(failed)}. The "
            "engagement lane is not run on tables that did not pass"
        )
    tables = {name: table.to_pandas() for name, table in result.tables.items()}
    return WeeklyInputs(
        subscription_state_interval=tables["subscription_state_interval"],
        reader_channel_day=tables["reader_channel_day"],
        reader_section_day=tables["reader_section_day"],
        reader_email_day=tables.get("reader_email_day"),
        reader_community_day=tables.get("reader_community_day"),
    )


def resolve_config(
    manifest: Manifest,
    bucket_map: SectionBucketMap,
    **overrides: object,
) -> LaneConfig:
    return LaneConfig.from_manifest(manifest, bucket_map, **overrides)


def _week_ends(inputs: WeeklyInputs, config: LaneConfig) -> list[date]:
    """Every week end whose whole 28-day window lies inside the delivery's coverage."""
    frames = [inputs.reader_channel_day, inputs.reader_section_day]
    if inputs.reader_email_day is not None:
        frames.append(inputs.reader_email_day)
    if inputs.reader_community_day is not None:
        frames.append(inputs.reader_community_day)
    dates = pd.concat([frame[LOCAL_DATE_COLUMN] for frame in frames], ignore_index=True)
    start = pd.to_datetime(dates).min().date()
    end = pd.to_datetime(dates).max().date()
    return config.week_grid.week_ends_with_full_window(start, end)


def run_lane(
    inputs: WeeklyInputs,
    config: LaneConfig,
    *,
    interpretability_reviewed: bool = False,
    max_weeks: int | None = None,
) -> LaneResult:
    """Fit once, score every week, gate, and report."""
    weeks = _week_ends(inputs, config)
    if max_weeks is not None:
        weeks = weeks[-max_weeks:]
    if len(weeks) < 2:
        raise LaneError(
            f"only {len(weeks)} complete week(s) have a full 28-day feature window inside "
            "this delivery. A single week cannot support a panel sampled one row per "
            "reader per month, and it cannot support any temporal check"
        )

    weekly = [build_weekly_features(inputs, week_end, config) for week_end in weeks]
    stacked = stack_weeks(weekly)
    notes: list[str] = []

    # --- the training panel -------------------------------------------------
    eligible = stacked.loc[fit_cohort_mask(stacked) & ~stacked["no_recent_flag"].astype(bool)]
    if eligible.empty:
        raise LaneError(
            "no reader-week is both entitled for a whole window and active in it, so there "
            "is nothing to fit on"
        )
    sampled = panel_layer.sample_panel(eligible, seed=config.panel_seed)
    balanced, balance_record = panel_layer.balance_by_month(sampled, seed=config.panel_seed)
    panel_layer.validate_panel(balanced, baseline_end=weeks[-1], min_rows=max(20, len(weeks)))
    fit_population = panel_layer.content_active_subset(balanced)
    if fit_population.empty:
        raise LaneError(
            "no panel row is content-active, so the surface has nothing to be fit on. "
            "Either section metadata is not resolving or the content-active floor is above "
            "what this delivery supports"
        )
    notes.append(
        f"panel: {len(sampled)} sampled, {len(balanced)} after month balancing, "
        f"{len(fit_population)} content-active"
    )

    # --- the surface --------------------------------------------------------
    space = surfaces.fit_surface(fit_population, config)
    fit_matrix = surfaces.build_surface_matrix(fit_population, space)
    variances = surfaces.surface_variances(fit_matrix)
    lower, upper = 0.5, 2.0
    variance_ok = all(lower <= value <= upper for value in variances.values())
    if not variance_ok:
        notes.append(
            "surface variances outside "
            f"[{lower}, {upper}]: "
            + ", ".join(
                f"{column}={value:.3f}"
                for column, value in variances.items()
                if not (lower <= value <= upper)
            )
        )

    # --- candidate k --------------------------------------------------------
    candidates = selection.sweep_k(fit_matrix, config.k_grid, config.gates, n_seeds=config.n_seeds)
    champion_k, k_table = selection.select_k(candidates, config.gates)
    champion = next((item for item in candidates if item.k == champion_k), None)
    if champion is not None:
        selection.assert_champion_derived(champion.k, candidates, config.gates)

    tables: dict[str, pd.DataFrame] = {"k_selection": k_table}
    result = LaneResult(
        config=config, weeks=weeks, bundle=None, tables=tables, champion_k=champion_k, notes=notes
    )

    stale = assert_sources_fresh(_latest_local_date(_named_inputs(inputs)), weeks[-1])
    smallest_share: float | None = None
    survival_bound: float | None = None
    if champion is not None:
        _, counts = np.unique(champion.labels, return_counts=True)
        smallest_share = float((counts / counts.sum()).min())
        survival_bound = champion.stability.survival_lower_bound if champion.stability else None

    if champion is None:
        notes.append(
            "no candidate k survived the selection screens, so nothing was frozen and no "
            "labels were produced. The k-selection table records why"
        )
        reports = [
            gate_layer.data_quality_gates(
                spine=stacked,
                stale_sources=stale,
                resolved_view_share=float(np.mean([w.resolved_view_share for w in weekly])),
                completeness=weekly[-1].completeness,
                matrix_finite=True,
                variance_in_bounds=variance_ok,
                gates=config.gates,
            ),
            gate_layer.model_quality_gates(
                champion_k=None,
                survival_lower_bound=None,
                smallest_cluster_share=None,
                interpretability_reviewed=interpretability_reviewed,
                gates=config.gates,
            ),
        ]
        result.decision = gate_layer.evaluate_publication(reports)
        tables["gate_report"] = gate_layer.gate_frame(reports)
        return result

    # --- freeze -------------------------------------------------------------
    matrix_values = fit_matrix[space.feature_columns].to_numpy(dtype=float)
    ood = fit_ood_thresholds(matrix_values, champion.centroids)
    labels = rank_labels(champion.centroids, space.feature_columns)
    model_version = f"{config.surface}-k{champion.k}-{config.bucket_map.version}"

    layout = measures_layer.build_layout(
        config.channels,
        has_email=inputs.has_email,
        has_community=inputs.has_community,
    )
    measure_params = measures_layer.fit_measures(
        measures_layer.build_signal_matrix(fit_population, layout),
        champion.labels,
        layout,
        model_version,
    )

    frozen_surface = FrozenSurface(
        name=config.surface,
        k=champion.k,
        centroids=champion.centroids,
        feature_columns=list(space.feature_columns),
        ood=ood,
        label_map=labels,
        seed_ari=champion.seed_report.median_ari,
        survival_lower_bound=survival_bound or 0.0,
    )
    bundle = FrozenBundle(
        model_version=model_version,
        frozen_at=datetime.now(tz=UTC).date().isoformat(),
        lineage=config.feature_version(),
        surface_space=space,
        main=frozen_surface,
        training_panel={
            "rule": panel_layer.PANEL_RULE,
            "seed": config.panel_seed,
            "n_rows": len(balanced),
            "n_content_active": len(fit_population),
            "baseline_end": weeks[-1].isoformat(),
            "month_balance": balance_record.to_dict(orient="records"),
        },
        bucket_map_version=config.bucket_map.version,
        bucket_map_snapshot=config.bucket_map.snapshot(),
        measures_params=measure_params.to_dict(),
        k_selection={
            "grid": list(config.k_grid),
            "champion": champion.k,
            "derived": True,
            "survival_lower_bound": survival_bound,
            "surface_variances": variances,
        },
        notes={"surface_resolution": config.surface, "lane_notes": list(notes)},
    )
    bundle.validate()
    result.bundle = bundle

    # --- score every week ---------------------------------------------------
    feature_version = config.feature_version()
    score_frames: list[pd.DataFrame] = []
    measure_frames: list[pd.DataFrame] = []
    for week in weekly:
        frame = week.frame.reset_index(drop=True)
        no_recent = week.no_recent.reset_index(drop=True)
        score_frames.append(
            scoring.score_week(frame, bundle, no_recent=no_recent, feature_version=feature_version)
        )
        measure_frames.append(scoring.score_measures(frame, bundle))
    scores = pd.concat(score_frames, ignore_index=True)
    measure_scores = pd.concat(measure_frames, ignore_index=True)

    profile_columns = tuple(
        column
        for column in (
            *(f"{channel}_views_28d" for channel in config.channels),
            *PROFILE_ATOMICS,
        )
        if column in stacked.columns
    )
    profile = scoring.cluster_profile(
        scores.loc[scores[WEEK_KEY] == weeks[-1]],
        stacked.loc[stacked[WEEK_KEY] == weeks[-1]],
        profile_columns,
    )

    # --- temporal ------------------------------------------------------------
    t4_retention, t4_similarity = _temporal(scores, weeks, stacked, bundle)

    # --- gates ---------------------------------------------------------------
    imputation = pd.Series(dtype="float64")
    reports = [
        gate_layer.data_quality_gates(
            spine=stacked,
            stale_sources=stale,
            resolved_view_share=float(np.mean([w.resolved_view_share for w in weekly])),
            completeness=weekly[-1].completeness,
            matrix_finite=True,
            variance_in_bounds=variance_ok,
            gates=config.gates,
        ),
        gate_layer.model_quality_gates(
            champion_k=champion.k,
            survival_lower_bound=survival_bound,
            smallest_cluster_share=smallest_share,
            interpretability_reviewed=interpretability_reviewed,
            gates=config.gates,
        ),
        gate_layer.temporal_gates(
            t4_retention=t4_retention,
            t4_profile_similarity=t4_similarity,
            gates=config.gates,
        ),
    ]
    if not imputation.empty:  # pragma: no cover - the surface path has no imputed block
        reports.append(
            gate_layer.feature_quality_gates(
                blocks=[],
                imputation_share=imputation,
                content_active_share=float(stacked["content_active_flag"].mean()),
            )
        )
    result.decision = gate_layer.evaluate_publication(reports)

    tables.update(
        {
            "reader_week_features": stacked,
            "reader_week_cluster": scores,
            "reader_week_measures": measure_scores,
            "cluster_profile": profile,
            "gate_report": gate_layer.gate_frame(reports),
        }
    )
    return result


def _named_inputs(inputs: WeeklyInputs) -> dict[str, pd.DataFrame]:
    named = {
        "reader_channel_day": inputs.reader_channel_day,
        "reader_section_day": inputs.reader_section_day,
    }
    if inputs.reader_email_day is not None:
        named["reader_email_day"] = inputs.reader_email_day
    if inputs.reader_community_day is not None:
        named["reader_community_day"] = inputs.reader_community_day
    return named


def _temporal(
    scores: pd.DataFrame,
    weeks: list[date],
    stacked: pd.DataFrame,
    bundle: FrozenBundle,
) -> tuple[float | None, float | None]:
    """Label retention and centroid-profile similarity four weeks apart.

    Four and not one. Adjacent weeks share 21 of their 28 window days, so their
    agreement is mechanically high whatever the model does -- gating on it would
    certify the model for arithmetic it cannot avoid. Returns ``(None, None)`` when
    the run is too short to make a non-overlapping comparison, and the gate reports
    that as un-evaluated rather than as a pass.
    """
    if len(weeks) < 5:
        return None, None
    early, late = weeks[-5], weeks[-1]
    left = scores.loc[scores[WEEK_KEY] == early].set_index(READER_KEY)["cluster_label"]
    right = scores.loc[scores[WEEK_KEY] == late].set_index(READER_KEY)["cluster_label"]
    comparison = selection.temporal_comparison(left, right, weeks_apart=4)

    # Profile similarity: the mean correlation between each cluster's mean feature
    # vector in the two weeks. Computed on the frozen labels, so it asks whether the
    # clusters still describe the same behaviour rather than whether readers moved.
    columns = [
        column for column in bundle.surface_space.source_columns if column in stacked.columns
    ]
    similarity: float | None = None
    if columns:
        profiles = []
        for week in (early, late):
            joined = stacked.loc[stacked[WEEK_KEY] == week].merge(
                scores.loc[scores[WEEK_KEY] == week][[READER_KEY, "cluster_index"]],
                on=READER_KEY,
                how="inner",
            )
            profiles.append(joined.groupby("cluster_index")[columns].mean())
        shared = profiles[0].index.intersection(profiles[1].index)
        if len(shared):
            correlations = []
            for cluster in shared:
                left_vector = profiles[0].loc[cluster].to_numpy(dtype=float)
                right_vector = profiles[1].loc[cluster].to_numpy(dtype=float)
                if np.std(left_vector) == 0 or np.std(right_vector) == 0:
                    continue
                correlations.append(float(np.corrcoef(left_vector, right_vector)[0, 1]))
            if correlations:
                similarity = float(np.mean(correlations))
    return comparison.retention, similarity


def run_from_delivery(
    delivery: str | Path,
    bucket_map_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    interpretability_reviewed: bool = False,
    max_weeks: int | None = None,
    **config_overrides: object,
) -> LaneResult:
    """Build the intermediate tables from a delivery and run the lane over them."""
    manifest = load_manifest(delivery)
    bucket_map = load_bucket_map(bucket_map_path)
    config = resolve_config(manifest, bucket_map, **config_overrides)
    inputs = inputs_from_build(delivery)
    result = run_lane(
        inputs,
        config,
        interpretability_reviewed=interpretability_reviewed,
        max_weeks=max_weeks,
    )
    if output_dir is not None:
        output_layer.write_outputs(result.tables, output_dir)
        if result.bundle is not None:
            result.bundle.save(Path(output_dir) / "frozen_bundle.json")
    return result
