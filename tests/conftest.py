"""Shared fixtures: one synthetic cohort, built once for the whole session.

The cohort is generated rather than committed, and generated once rather than per
test, because generating it is the cheap part and building the intermediate tables
and fitting a model over it is not. Everything downstream is read-only.

The sweep is deliberately narrow and the perturbation shallow. These fixtures exist
to prove the lane runs and that its assertions fire, not to produce a model anybody
would publish -- and a full sweep at production settings would put minutes into every
test run for no extra coverage.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from engagement_kernel.contract.manifest import load_manifest
from engagement_kernel.engagement import lane
from engagement_kernel.engagement.buckets import load_bucket_map
from engagement_kernel.engagement.cohort import BUCKET_MAP_FILENAME, CohortSpec, write_cohort
from engagement_kernel.engagement.config import GateThresholds, LaneConfig

#: Small enough to be quick, large enough that the panel, the percentile tables and
#: the stability screen all have something to work with.
COHORT_READERS = 120
TEST_K_GRID = (3, 4, 5)
TEST_SEEDS = 5
TEST_DRAWS = 5
TEST_MAX_WEEKS = 8


@pytest.fixture(scope="session")
def cohort_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("cohort")
    write_cohort(directory, CohortSpec(n_readers=COHORT_READERS))
    return directory


@pytest.fixture(scope="session")
def bucket_map(cohort_dir: Path):
    return load_bucket_map(cohort_dir / BUCKET_MAP_FILENAME)


@pytest.fixture(scope="session")
def manifest(cohort_dir: Path):
    return load_manifest(cohort_dir)


@pytest.fixture(scope="session")
def test_gates() -> GateThresholds:
    return dataclasses.replace(GateThresholds(), selection_perturbation_draws=TEST_DRAWS)


@pytest.fixture(scope="session")
def lane_config(manifest, bucket_map, test_gates) -> LaneConfig:
    return lane.resolve_config(
        manifest, bucket_map, k_grid=TEST_K_GRID, n_seeds=TEST_SEEDS, gates=test_gates
    )


@pytest.fixture(scope="session")
def weekly_inputs(cohort_dir: Path):
    return lane.inputs_from_build(cohort_dir)


@pytest.fixture(scope="session")
def lane_result(weekly_inputs, lane_config):
    return lane.run_lane(weekly_inputs, lane_config, max_weeks=TEST_MAX_WEEKS)
