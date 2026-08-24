"""Engagement scoring and subscriber clustering over the intermediate tables.

The second publication lane, and now the only one in scope: it turns the daily
intermediate tables into a weekly engagement score and a behavioural cluster per
reader. Everything it needs about the publisher's own decisions comes from the
delivery's manifest -- the day boundary, the week anchor, what an article view is,
and which readers are scored -- so a run cannot quietly disagree with the data it
was given.

Read in this order:

:mod:`~engagement_kernel.engagement.config`
    What one run has to be told, and what it refuses to guess. Also where the
    surface is resolved from what the delivery actually contains, and where the
    email click-unit decision is recorded.
:mod:`~engagement_kernel.engagement.windows`
    The week grid, read from the manifest. Two week-anchor conventions are in live
    use and they differ by up to six days, so this module has no default.
:mod:`~engagement_kernel.engagement.guards`
    The two layers that decide what may become a model feature. Read this before
    trusting any number the rest of the package produces.
:mod:`~engagement_kernel.engagement.spine`
    Which readers are scored, resolved once from subscription-state history.
:mod:`~engagement_kernel.engagement.atomics`, :mod:`~engagement_kernel.engagement.topics`,
:mod:`~engagement_kernel.engagement.cross_channel`
    The weekly measures: per channel, per topic bucket, and across channels.
:mod:`~engagement_kernel.engagement.features`
    Assembly: spine first, activity joined onto it, row count asserted.
:mod:`~engagement_kernel.engagement.surfaces`
    The small frozen feature spaces the models are fit in.
:mod:`~engagement_kernel.engagement.selection`
    Choosing k, and refusing to choose one when nothing survives.
:mod:`~engagement_kernel.engagement.freeze`, :mod:`~engagement_kernel.engagement.scoring`
    The frozen bundle, and applying it to a week without re-fitting anything.
:mod:`~engagement_kernel.engagement.lane`
    The whole thing, end to end.

The topic lane -- content personas -- is deliberately out of scope for this version.
Topic *features* are not: the engagement model's feature space contains a topic
block, so the section bucket map, the topic atomics and the section-day intermediate
all live here. Topic-derived outputs are expected to be reconsidered in a future
version; nothing about them was judged unsound.
"""

from engagement_kernel.engagement.buckets import (
    BucketMapError,
    SectionBucketMap,
    check_completeness,
    load_bucket_map,
)
from engagement_kernel.engagement.config import (
    EMAIL_CLICK_UNIT,
    SURFACE_INTENSITY,
    SURFACE_JOINT,
    GateThresholds,
    LaneConfig,
    LaneConfigError,
    resolve_surface,
)
from engagement_kernel.engagement.features import WeeklyInputs, build_weekly_features
from engagement_kernel.engagement.freeze import FrozenBundle, FrozenBundleError, FrozenSurface
from engagement_kernel.engagement.guards import (
    ForbiddenInput,
    ForbiddenModelColumn,
    assert_no_forbidden_inputs,
    assert_no_forbidden_model_columns,
    inspect_model_columns,
)
from engagement_kernel.engagement.lane import (
    LaneError,
    LaneResult,
    inputs_from_build,
    read_intermediate,
    resolve_config,
    run_from_delivery,
    run_lane,
)
from engagement_kernel.engagement.outputs import NOT_PORTED_COLUMNS, OUTPUTS, census_frame
from engagement_kernel.engagement.windows import WeekGrid, WindowBounds, WindowError

__all__ = [
    "EMAIL_CLICK_UNIT",
    "NOT_PORTED_COLUMNS",
    "OUTPUTS",
    "SURFACE_INTENSITY",
    "SURFACE_JOINT",
    "BucketMapError",
    "ForbiddenInput",
    "ForbiddenModelColumn",
    "FrozenBundle",
    "FrozenBundleError",
    "FrozenSurface",
    "GateThresholds",
    "LaneConfig",
    "LaneConfigError",
    "LaneError",
    "LaneResult",
    "SectionBucketMap",
    "WeekGrid",
    "WeeklyInputs",
    "WindowBounds",
    "WindowError",
    "assert_no_forbidden_inputs",
    "assert_no_forbidden_model_columns",
    "build_weekly_features",
    "census_frame",
    "check_completeness",
    "inputs_from_build",
    "inspect_model_columns",
    "load_bucket_map",
    "read_intermediate",
    "resolve_config",
    "resolve_surface",
    "run_from_delivery",
    "run_lane",
]
