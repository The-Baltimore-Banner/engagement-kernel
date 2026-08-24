"""Fit every feature transform on the panel; apply them everywhere.

This is the fit/apply seam for the wide, block-weighted construction. One object
(:class:`FittedPipeline`) holds everything that was learned from the training panel,
and :func:`apply_pipeline` is the only way it is used. Keeping the two apart is what
makes the fitting population auditable: there is exactly one function that sees the
panel, and everything else takes a fitted object.

Not every feature needs a block. Recency, momentum, the channel-mix shares and
overall active days are single quantities with a meaning already -- there is nothing
for a within-block PCA to weight -- so they are calibrated directly. Wrapping each
in a one-input "block" would add a layer of indirection that gates a component
against a bar it cannot fail.

The redundancy decision at the end has a subtlety worth stating. Consistency and
habit both read the same four weekly bins, so on many populations they correlate
above the threshold and one has to go. Deciding that requires *applying* the
pipeline to the panel mid-fit, which is why this function calls its own apply -- and
why the flags are still unset at that point, so the check can see the column it is
about to decide the fate of.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from engagement_kernel.engagement import blocks as block_layer
from engagement_kernel.engagement import cross_channel, topics
from engagement_kernel.engagement.buckets import SectionBucketMap
from engagement_kernel.engagement.calibration import FeatureCalibration, fit_calibration
from engagement_kernel.engagement.imputation import neutral_impute
from engagement_kernel.engagement.transforms import recency_score


class PipelineError(ValueError):
    """The feature pipeline could not be fit or applied as declared."""


def activity_masks(frame: pd.DataFrame, anchors: dict[str, str]) -> pd.DataFrame:
    """Add a ``{channel}_active`` boolean per channel.

    These are the conditional fitting populations. Derived here rather than passed
    in so the mask a block is fit on and the mask its variance is checked on are the
    same expression.
    """
    out = frame.copy()
    for channel, anchor in anchors.items():
        if anchor not in out.columns:
            raise PipelineError(f"channel {channel!r} anchors on absent column {anchor!r}")
        out[f"{channel}_active"] = out[anchor].fillna(0) > 0
    return out


@dataclass
class FittedPipeline:
    """Everything learned from the training panel."""

    bucket_map: SectionBucketMap
    anchors: dict[str, str]
    channels: tuple[str, ...]
    blocks: dict[str, block_layer.FittedBlock]
    direct_calibrations: dict[str, FeatureCalibration]
    signal_tables: dict[str, FeatureCalibration]
    mix_calibrations: dict[str, FeatureCalibration]
    topic_calibrations: dict[str, FeatureCalibration]
    #: Channels whose consistency block was dropped as redundant with habit.
    consistency_dropped: dict[str, bool] = field(default_factory=dict)
    #: The measured correlation behind each of those decisions, so the drop is a
    #: recorded number rather than an absent column.
    consistency_corr: dict[str, float] = field(default_factory=dict)
    #: Topic columns that were constant on the fitting population -- usually the
    #: catch-all bucket of a taxonomy with no remainder. Dropped as a recorded
    #: decision; see :func:`~engagement_kernel.engagement.topics.degenerate_topic_columns`.
    topic_dropped: tuple[str, ...] = ()

    def include_consistency(self) -> dict[str, bool]:
        return {
            channel: not self.consistency_dropped.get(channel, False) for channel in self.channels
        }

    def describe_blocks(self) -> list[dict[str, object]]:
        return [model.describe() for model in self.blocks.values()]

    def dropped_columns(self) -> tuple[str, ...]:
        """Every column a declared block membership names that this fit did not build.

        Handed to
        :func:`~engagement_kernel.engagement.matrix.assert_column_manifest` as the
        documented drops, so an absent column is a recorded decision rather than a
        silent omission -- the failure that made a frozen matrix quietly short of three
        declared columns while its topic shares stopped summing to 1.
        """
        dropped = [
            f"{channel}_consistency"
            for channel, was_dropped in self.consistency_dropped.items()
            if was_dropped
        ]
        dropped.extend(
            name
            for name, model in self.blocks.items()
            if model.method == block_layer.METHOD_DROPPED
        )
        dropped.extend(self.topic_dropped)
        return tuple(dict.fromkeys(dropped))


def _direct_recency_columns(
    channels: tuple[str, ...], *, email: bool, community: bool
) -> dict[str, str]:
    out = {f"{channel}_recency": f"{channel}_recency_days" for channel in channels}
    if email:
        out["email_click_recency"] = "email_click_recency_days"
    if community:
        out["community_recency"] = "community_recency_days"
    out["overall_recency"] = "overall_recency_days"
    return out


def _direct_momentum_columns(channels: tuple[str, ...]) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {
        f"{channel}_momentum": (f"{channel}_momentum_4", f"{channel}_active")
        for channel in channels
    }
    # Overall momentum is defined for every clusterable reader by construction --
    # they have at least one active day somewhere -- so it needs no mask.
    out["overall_momentum"] = ("overall_momentum_4", None)
    return out


def _block_specs(
    channels: tuple[str, ...], *, email: bool, community: bool
) -> list[block_layer.BlockSpec]:
    specs: list[block_layer.BlockSpec] = []
    for channel in channels:
        specs.extend(block_layer.consumption_blocks(channel))
    if email:
        specs.extend(block_layer.email_blocks())
    if community:
        specs.extend(block_layer.community_blocks())
    return specs


def fit_pipeline(
    panel: pd.DataFrame,
    bucket_map: SectionBucketMap,
    anchors: dict[str, str],
    channels: tuple[str, ...],
    *,
    panel_content_active: pd.Series,
) -> FittedPipeline:
    """Fit every transform on the training panel."""
    has_email = "email" in anchors
    has_community = "community" in anchors
    with_masks = activity_masks(panel, anchors)

    fitted_blocks: dict[str, block_layer.FittedBlock] = {}
    for spec in _block_specs(channels, email=has_email, community=has_community):
        fitted_blocks[spec.name] = block_layer.fit_block(with_masks, spec)

    direct: dict[str, FeatureCalibration] = {}
    for name, days_column in _direct_recency_columns(
        channels, email=has_email, community=has_community
    ).items():
        direct[name] = fit_calibration(recency_score(with_masks[days_column]))
    for name, (column, mask_column) in _direct_momentum_columns(channels).items():
        values = (
            with_masks[column]
            if mask_column is None
            else with_masks.loc[with_masks[mask_column], column]
        )
        direct[name] = fit_calibration(values.dropna())
    direct["overall_active_days"] = fit_calibration(np.log1p(with_masks["overall_active_days_28d"]))

    signal_tables = cross_channel.fit_channel_signals(with_masks, anchors)
    panel_signals = cross_channel.channel_signals(with_masks, signal_tables, anchors)
    mix_channels = tuple(anchors)
    panel_mix = cross_channel.channel_mix(panel_signals, mix_channels)
    mix_calibrations = {
        cross_channel.channel_mix_column(channel): fit_calibration(
            panel_mix[cross_channel.channel_mix_column(channel)]
        )
        for channel in mix_channels
    }

    topic_calibrations = topics.fit_topic_block(with_masks, panel_content_active, bucket_map)
    topic_dropped = topics.degenerate_topic_columns(with_masks, panel_content_active, bucket_map)

    fitted = FittedPipeline(
        bucket_map=bucket_map,
        anchors=dict(anchors),
        channels=channels,
        blocks=fitted_blocks,
        direct_calibrations=direct,
        signal_tables=signal_tables,
        mix_calibrations=mix_calibrations,
        topic_calibrations=topic_calibrations,
        topic_dropped=topic_dropped,
    )

    features, _ = apply_pipeline(panel, fitted, content_active=panel_content_active)
    for channel in channels:
        name = f"{channel}_consistency"
        if fitted_blocks[name].method == block_layer.METHOD_DROPPED:
            fitted.consistency_dropped[channel] = True
            continue
        corr, drop = block_layer.redundancy_check(
            features[name], features[f"{channel}_habit"], with_masks[f"{channel}_active"]
        )
        fitted.consistency_dropped[channel] = drop
        fitted.consistency_corr[channel] = corr
    return fitted


def conditional_fitting_masks(
    frame: pd.DataFrame,
    fitted: FittedPipeline,
    *,
    content_active: pd.Series,
) -> dict[str, pd.Series]:
    """Each conditional column's fitting population, for the variance assertion."""
    data = activity_masks(frame, fitted.anchors)
    masks: dict[str, pd.Series] = {}
    for channel in fitted.channels:
        active = data[f"{channel}_active"]
        for suffix in ("depth", "momentum", "consistency"):
            masks[f"{channel}_{suffix}"] = active
    if "community" in fitted.anchors:
        masks["community_contribution_depth"] = data["community_active"]
    for column in topics.topic_block_columns(fitted.bucket_map):
        if column in fitted.topic_dropped:
            continue
        masks[column] = content_active.astype(bool)
    return masks


def apply_pipeline(
    frame: pd.DataFrame,
    fitted: FittedPipeline,
    *,
    content_active: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce the standardised feature frame and its imputation flags."""
    data = activity_masks(frame, fitted.anchors)
    out = pd.DataFrame(index=data.index)

    for name, model in fitted.blocks.items():
        if model.method == block_layer.METHOD_DROPPED:
            continue
        channel, _, suffix = name.partition("_")
        if suffix == "consistency" and fitted.consistency_dropped.get(channel, False):
            continue
        out[name] = block_layer.apply_block(model, data)

    has_email = "email" in fitted.anchors
    has_community = "community" in fitted.anchors
    for name, days_column in _direct_recency_columns(
        fitted.channels, email=has_email, community=has_community
    ).items():
        out[name] = fitted.direct_calibrations[name].z_clipped(recency_score(data[days_column]))
    for name, (column, _mask) in _direct_momentum_columns(fitted.channels).items():
        out[name] = fitted.direct_calibrations[name].z_clipped(data[column])
    out["overall_active_days"] = fitted.direct_calibrations["overall_active_days"].z_clipped(
        np.log1p(data["overall_active_days_28d"])
    )

    signals = cross_channel.channel_signals(data, fitted.signal_tables, fitted.anchors)
    mix = cross_channel.channel_mix(signals, tuple(fitted.anchors))
    for channel in fitted.anchors:
        column = cross_channel.channel_mix_column(channel)
        out[column] = fitted.mix_calibrations[column].z_clipped(mix[column])

    topic_features, topic_flags = topics.apply_topic_block(
        data, content_active, fitted.topic_calibrations, fitted.bucket_map
    )
    if fitted.topic_dropped:
        keep = [column for column in topic_features.columns if column not in fitted.topic_dropped]
        topic_features = topic_features[keep]
        topic_flags = topic_flags[[f"{column}_imputed_flag" for column in keep]]
    out = pd.concat([out, topic_features], axis=1)

    flag_frames = [topic_flags]
    for channel in fitted.channels:
        inactive = ~data[f"{channel}_active"]
        present = [
            name
            for name in (f"{channel}_depth", f"{channel}_momentum", f"{channel}_consistency")
            if name in out.columns
        ]
        if present:
            out, flags = neutral_impute(out, inactive, present)
            flag_frames.append(flags)
    if has_community and "community_contribution_depth" in out.columns:
        out, flags = neutral_impute(
            out, ~data["community_active"], ["community_contribution_depth"]
        )
        flag_frames.append(flags)

    return out, pd.concat(flag_frames, axis=1)
