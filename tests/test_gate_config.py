"""A deployment's own gate thresholds, and the two defects that were in the way.

The point of this file is not that a TOML reader works. It is that three specific
failures cannot come back.

**Prescription by omission.** Every threshold had a default and no supported way to
change it, while two docstrings said the defaults were placeholders a deployment
should replace. So the tests here assert the *mechanism*, and they assert it from the
adopter's side: a file, a flag, and a k this package ships no bar for.

**A default that moved without anybody deciding to move it.** The parameterization
is worthless if it also re-tuned the engine, because then no existing verdict means
what it used to. So the shipped values are pinned against a literal table, and the
rendered template has to load back to exactly them.

**A mutable gate.** The bar table was a plain dict on a frozen dataclass, so
inserting a bar in place silently worked -- the only route to an undeclared k that
existed, and one that changed the gates every other holder of that instance was
already screening against.
"""

from __future__ import annotations

import dataclasses

import pytest

from engagement_kernel.engagement import lane
from engagement_kernel.engagement.config import (
    BAR_DECLARATION_HINT,
    GateThresholds,
    LaneConfig,
    LaneConfigError,
)
from engagement_kernel.engagement.gate_config import (
    GATE_CONFIG_VERSION,
    GATE_FIELDS,
    GateConfigError,
    gate_config_template,
    load_gate_config,
    parse_gate_config,
    render_gate_config,
)

#: The shipped defaults, written out. Not read from ``GateThresholds`` -- a test that
#: reads the values it checks passes whatever they become, which is the opposite of
#: what this one is for. BBA1's parameterization work committed to moving no default,
#: and this is where that commitment is kept.
SHIPPED_DEFAULTS = {
    "seed_ari": 0.70,
    "centroid_distinctness_corr": 0.90,
    "tiny_cluster_floor": 0.01,
    "major_cluster_share": 0.01,
    "topic_coverage_floor": 0.80,
    "t4_retention": 0.45,
    "t4_profile_similarity": 0.80,
    "selection_perturbation_draws": 50,
    "selection_perturbation_row_fraction": 0.001,
    "selection_survival_floor": 0.50,
    "selection_rng_seed": 20260824,
}
SHIPPED_BARS = {3: 0.46, 4: 0.42, 5: 0.38, 6: 0.35, 7: 0.34, 8: 0.33, 9: 0.32, 10: 0.31}


# --- no default moved --------------------------------------------------------


def test_no_shipped_default_moved() -> None:
    gates = GateThresholds()
    for name, expected in SHIPPED_DEFAULTS.items():
        assert getattr(gates, name) == expected, f"{name} moved"
    assert dict(gates.cross_algorithm_ari_by_k) == SHIPPED_BARS


def test_every_threshold_field_is_covered_by_this_file() -> None:
    """So a field added later cannot slip past the pin above unnoticed."""
    assert set(GATE_FIELDS) == set(SHIPPED_DEFAULTS)


def test_the_template_loads_back_to_the_shipped_defaults() -> None:
    """ "Omitting the file reproduces today's defaults" -- asserted, not asserted about."""
    config = parse_gate_config(gate_config_template())
    assert config.gates == GateThresholds()
    assert dict(config.lane_overrides) == {}


# --- the mutable-gate defect -------------------------------------------------


def test_the_bar_table_cannot_be_mutated_in_place() -> None:
    """It used to succeed, which is how an undeclared k was reachable at all."""
    gates = GateThresholds()
    with pytest.raises(TypeError):
        gates.cross_algorithm_ari_by_k[12] = 0.29  # type: ignore[index]
    with pytest.raises(LaneConfigError):
        gates.cross_algorithm_bar(12)


def test_a_bar_table_passed_in_is_frozen_too() -> None:
    """Not only the default. A caller's own dict must not stay a live handle."""
    mine = {4: 0.42, 6: 0.35}
    gates = dataclasses.replace(GateThresholds(), cross_algorithm_ari_by_k=mine)
    mine[8] = 0.33
    assert 8 not in gates.cross_algorithm_ari_by_k
    with pytest.raises(TypeError):
        gates.cross_algorithm_ari_by_k[8] = 0.33  # type: ignore[index]


def test_with_bars_returns_a_new_instance() -> None:
    gates = GateThresholds()
    amended = gates.with_bars({2: 0.55})
    assert amended.cross_algorithm_bar(2) == 0.55
    assert dict(gates.cross_algorithm_ari_by_k) == SHIPPED_BARS


# --- the refusal now teaches -------------------------------------------------


def test_the_refusal_says_how_to_declare_a_bar() -> None:
    """An adopter reading only the error must be able to act on it."""
    with pytest.raises(LaneConfigError) as raised:
        GateThresholds().cross_algorithm_bar(2)
    message = str(raised.value)
    assert BAR_DECLARATION_HINT in message
    assert "--gates" in message
    assert "derive_cross_algorithm_bars.py" in message


# --- any k of two or more ----------------------------------------------------


@pytest.mark.parametrize("k", [2, 12])
def test_declaring_a_bar_makes_an_unshipped_k_screenable(manifest, bucket_map, k: int) -> None:
    """The blocker this work existed to remove: k=2 and k=12 both used to die here.

    Through ``LaneConfig`` rather than through ``GateThresholds`` alone, because the
    refusal that stopped a run was the grid validation in ``__post_init__`` -- it
    happened before any fitting, so a newsroom whose audience splits in two never got
    as far as a model.
    """
    with pytest.raises(LaneConfigError):
        lane.resolve_config(manifest, bucket_map, k_grid=(k,))
    gates = GateThresholds().with_bars({k: 0.40})
    config = lane.resolve_config(manifest, bucket_map, k_grid=(k,), gates=gates)
    assert config.k_grid == (k,)
    assert config.gates.cross_algorithm_bar(k) == 0.40


def test_a_bar_below_two_clusters_is_refused() -> None:
    with pytest.raises(LaneConfigError, match="at least two"):
        GateThresholds().with_bars({1: 0.40})


# --- the file ----------------------------------------------------------------


def _document(body: str) -> str:
    return f"version = {GATE_CONFIG_VERSION}\n{body}"


def test_a_file_sets_a_threshold() -> None:
    config = parse_gate_config(_document("[gates]\nseed_ari = 0.55\n"))
    assert config.gates.seed_ari == 0.55
    # And changes nothing else.
    assert config.gates.selection_survival_floor == SHIPPED_DEFAULTS["selection_survival_floor"]


def test_a_file_can_declare_a_whole_bar_table() -> None:
    config = parse_gate_config(
        _document("[gates.cross_algorithm_ari_by_k]\n2 = 0.55\n4 = 0.42\n12 = 0.28\n")
    )
    assert dict(config.gates.cross_algorithm_ari_by_k) == {2: 0.55, 4: 0.42, 12: 0.28}
    # Declaring a table replaces it rather than adding to it: a bar this package
    # measured on somebody else's panel is not something to inherit by accident.
    with pytest.raises(LaneConfigError):
        config.gates.cross_algorithm_bar(5)


def test_a_non_contiguous_grid_comes_out_of_a_file() -> None:
    config = parse_gate_config(_document("[lane]\nk_grid = [4, 6, 8]\n"))
    assert config.lane_overrides["k_grid"] == (4, 6, 8)


def test_the_version_is_required() -> None:
    with pytest.raises(GateConfigError, match="declares no version"):
        parse_gate_config("[gates]\nseed_ari = 0.55\n")


def test_a_version_this_reader_does_not_know_is_refused() -> None:
    with pytest.raises(GateConfigError, match="version"):
        parse_gate_config("version = 99\n[gates]\nseed_ari = 0.55\n")


def test_an_unknown_threshold_is_refused_and_names_the_near_miss() -> None:
    """The characteristic failure of configuration files, refused rather than ignored."""
    with pytest.raises(GateConfigError) as raised:
        parse_gate_config(_document("[gates]\nseed_arri = 0.55\n"))
    assert "seed_ari" in str(raised.value)


def test_an_unknown_top_level_table_is_refused() -> None:
    with pytest.raises(GateConfigError, match="gates file has no field"):
        parse_gate_config(_document("[gate]\nseed_ari = 0.55\n"))


def test_an_empty_bar_table_is_refused() -> None:
    """It would refuse every candidate k, so the run could not produce a model."""
    with pytest.raises(GateConfigError, match="empty"):
        parse_gate_config(_document("[gates.cross_algorithm_ari_by_k]\n"))


def test_a_threshold_outside_its_range_is_refused_by_the_file() -> None:
    with pytest.raises(LaneConfigError, match="between 0 and 1"):
        parse_gate_config(_document("[gates]\nseed_ari = 1.4\n"))


def test_zero_perturbation_draws_is_refused() -> None:
    with pytest.raises(LaneConfigError, match="at least one"):
        parse_gate_config(_document("[gates]\nselection_perturbation_draws = 0\n"))


def test_a_whole_number_field_refuses_a_fraction() -> None:
    with pytest.raises(GateConfigError, match="whole number"):
        parse_gate_config(_document("[gates]\nselection_perturbation_draws = 2.5\n"))


def test_a_bar_key_that_is_not_a_cluster_count_is_refused() -> None:
    with pytest.raises(GateConfigError, match="bare key"):
        parse_gate_config(_document('[gates.cross_algorithm_ari_by_k]\n"five" = 0.38\n'))


def test_block_weights_come_from_the_file() -> None:
    config = parse_gate_config(_document("[lane.block_weights]\ntopic = 0.05\n"))
    weights = config.lane_overrides["block_weights"]
    assert weights.topic == 0.05
    assert weights.consumption == 0.40


def test_a_missing_file_says_the_flag_is_optional(tmp_path) -> None:
    with pytest.raises(GateConfigError, match="Omit --gates"):
        load_gate_config(tmp_path / "absent.toml")


def test_a_broken_file_is_refused_as_one(tmp_path) -> None:
    path = tmp_path / "gates.toml"
    path.write_text("version = = 1\n")
    with pytest.raises(GateConfigError, match="not readable as TOML"):
        load_gate_config(path)


def test_a_rendered_file_records_where_the_shipped_bars_came_from() -> None:
    """An adopter must not be able to read them as a derived universal."""
    text = render_gate_config().lower()
    assert "one newsroom's measurement" in text
    assert "derive_cross_algorithm_bars.py" in text
    assert "not the method" in text


def test_a_rendered_file_round_trips_a_deployment_s_own_values() -> None:
    """So a deployment can keep its file rendered rather than hand-maintained."""
    mine = dataclasses.replace(
        GateThresholds(), seed_ari=0.6, cross_algorithm_ari_by_k={2: 0.51, 3: 0.44}
    )
    text = render_gate_config(mine, lane_overrides={"k_grid": (2, 3), "n_seeds": 8})
    reloaded = parse_gate_config(text)
    assert reloaded.gates == mine
    assert reloaded.lane_overrides["k_grid"] == (2, 3)
    assert reloaded.lane_overrides["n_seeds"] == 8


def test_a_file_reaches_the_lane_config(manifest, bucket_map, tmp_path) -> None:
    """End to end: a file on disk decides what the run screens and how hard."""
    path = tmp_path / "gates.toml"
    path.write_text(
        _document(
            "[gates]\nseed_ari = 0.55\n\n"
            "[gates.cross_algorithm_ari_by_k]\n2 = 0.50\n4 = 0.42\n\n"
            "[lane]\nk_grid = [2, 4]\nn_seeds = 6\n"
        )
    )
    config = load_gate_config(path)
    resolved = LaneConfig.from_manifest(
        manifest, bucket_map, gates=config.gates, **dict(config.lane_overrides)
    )
    assert resolved.k_grid == (2, 4)
    assert resolved.n_seeds == 6
    assert resolved.gates.seed_ari == 0.55
    assert resolved.gates.cross_algorithm_bar(2) == 0.50
