"""The command line: its exit codes, and the cost knobs behind them.

The exit codes are the interface. `0` fit and published, `3` no model was frozen, `4`
a model was frozen and a gate blocks its labels — and the last two are the ones a
caller must not collapse, because collapsing them publishes a gated model. They are
tested at the function level with the lane stubbed, so the assertion is about the
mapping rather than about a two-minute clustering run.

The cost knobs are tested because their absence has a shape. At production settings
every candidate `k` is re-screened on 50 perturbed panels with 20 starting points each
— on a 120-reader cohort that is minutes, and it is minutes for the right reason. An
adopter's *first* run wants to see the shape of their data, not a freeze-quality
verdict, and without a flag the only way to get one is to edit the source.

The threshold flags are tested for a different reason. A cost knob and a threshold
were once presented together as "selection cost knobs", and the flags behaved that
way too: `--perturbation-draws` built a *fresh* default gate set rather than amending
the one in force, which was harmless only for as long as nothing else could supply
one. So the tests below supply a gates file and a knob together and assert both
survive, and they assert `--k-min` no longer re-declares a floor of its own.
"""

from __future__ import annotations

import pytest

from engagement_kernel.engagement import cli, lane
from engagement_kernel.engagement.gates import PublicationDecision


def _args(**overrides):
    parser = cli.build_parser()
    argv = [
        "run",
        overrides.pop("delivery", "/nowhere"),
        "--bucket-map",
        overrides.pop("bucket_map", "/nowhere/map.json"),
        *overrides.pop("extra", []),
    ]
    return parser.parse_args(argv)


def test_the_bucket_map_is_required() -> None:
    """No default taxonomy, enforced at the argument parser."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run", "/nowhere"])


def test_a_backwards_k_range_is_refused() -> None:
    args = _args(extra=["--k-min", "6", "--k-max", "4"])
    with pytest.raises(SystemExit, match="below"):
        cli._run(args)


@pytest.fixture
def stubbed(monkeypatch, cohort_dir, bucket_map, lane_config):
    """Run ``_run`` against the real config resolution but a substituted lane."""

    def install(result):
        monkeypatch.setattr(lane, "inputs_from_build", lambda directory: object())
        monkeypatch.setattr(lane, "run_lane", lambda *args, **kwargs: result)
        return _args(
            delivery=str(cohort_dir),
            bucket_map=str(cohort_dir / "section_buckets.json"),
        )

    return install


def test_a_gated_model_exits_four_not_zero(stubbed, lane_config) -> None:
    """The distinction a caller must not collapse: a frozen model whose labels are gated."""
    result = lane.LaneResult(
        config=lane_config,
        weeks=[],
        bundle=object(),  # type: ignore[arg-type]
        tables={},
        decision=PublicationDecision(
            publish_labels=False,
            publish_topic_block=False,
            blocking_failures=["model_quality.interpretability_reviewed"],
            topic_only_failures=[],
        ),
        champion_k=3,
    )
    assert cli._run(stubbed(result)) == cli.EXIT_GATED


def test_no_frozen_model_exits_three(stubbed, lane_config) -> None:
    result = lane.LaneResult(config=lane_config, weeks=[], bundle=None, tables={}, champion_k=None)
    assert cli._run(stubbed(result)) == cli.EXIT_NO_MODEL


def test_a_published_model_exits_zero(stubbed, lane_config) -> None:
    result = lane.LaneResult(
        config=lane_config,
        weeks=[],
        bundle=object(),  # type: ignore[arg-type]
        tables={},
        decision=PublicationDecision(
            publish_labels=True,
            publish_topic_block=True,
            blocking_failures=[],
            topic_only_failures=[],
        ),
        champion_k=3,
    )
    assert cli._run(stubbed(result)) == cli.EXIT_OK


def test_the_cost_knobs_reach_the_configuration(monkeypatch, cohort_dir) -> None:
    """A first run has to be able to be cheap without editing the source."""
    captured: dict[str, object] = {}

    def capture(inputs, config, **kwargs):
        captured["k_grid"] = config.k_grid
        captured["n_seeds"] = config.n_seeds
        captured["draws"] = config.gates.selection_perturbation_draws
        return lane.LaneResult(config=config, weeks=[], bundle=None, tables={}, champion_k=None)

    monkeypatch.setattr(lane, "inputs_from_build", lambda directory: object())
    monkeypatch.setattr(lane, "run_lane", capture)
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--k-min", "3", "--k-max", "4", "--seeds", "4", "--perturbation-draws", "3"],
    )
    cli._run(args)
    assert captured["k_grid"] == (3, 4)
    assert captured["n_seeds"] == 4
    assert captured["draws"] == 3


def test_the_cohort_command_writes_a_conforming_delivery(tmp_path) -> None:
    """The generator is reachable as a command, and what it writes validates."""
    from engagement_kernel.contract.validate import validate_directory

    target = tmp_path / "cohort"
    assert cli.main(["cohort", str(target), "--readers", "60"]) == cli.EXIT_OK
    report = validate_directory(target)
    assert report.passed, [str(finding) for finding in report.findings()]
    assert (target / "section_buckets.json").exists()


# --- the thresholds are the deployment's ------------------------------------


def _gates_file(tmp_path, body: str):
    from engagement_kernel.engagement.gate_config import GATE_CONFIG_VERSION

    path = tmp_path / "gates.toml"
    path.write_text(f"version = {GATE_CONFIG_VERSION}\n{body}")
    return path


def _capture(monkeypatch) -> dict:
    captured: dict[str, object] = {}

    def capture(inputs, config, **kwargs):
        captured["config"] = config
        return lane.LaneResult(config=config, weeks=[], bundle=None, tables={}, champion_k=None)

    monkeypatch.setattr(lane, "inputs_from_build", lambda directory: object())
    monkeypatch.setattr(lane, "run_lane", capture)
    return captured


def test_a_gates_file_and_a_cost_knob_both_survive(monkeypatch, tmp_path, cohort_dir) -> None:
    """The defect this closes: the knob used to rebuild a default and drop the file.

    Supplied together on purpose. Either one alone passed before this change; it was
    the combination that silently discarded everything the file said.
    """
    captured = _capture(monkeypatch)
    gates = _gates_file(
        tmp_path,
        "[gates]\nseed_ari = 0.55\nselection_survival_floor = 0.66\n\n"
        "[gates.cross_algorithm_ari_by_k]\n2 = 0.51\n3 = 0.46\n",
    )
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--gates", str(gates), "--perturbation-draws", "3", "--k-grid", "2,3"],
    )
    cli._run(args)
    config = captured["config"]
    assert config.gates.selection_perturbation_draws == 3, "the knob was dropped"
    assert config.gates.seed_ari == 0.55, "the gates file was dropped"
    assert config.gates.selection_survival_floor == 0.66
    assert config.gates.cross_algorithm_bar(2) == 0.51


def test_a_non_contiguous_grid_is_expressible(monkeypatch, tmp_path, cohort_dir) -> None:
    """`{4, 6, 8}` needed library code before this; now it is a flag."""
    captured = _capture(monkeypatch)
    gates = _gates_file(
        tmp_path, "[gates.cross_algorithm_ari_by_k]\n4 = 0.42\n6 = 0.35\n8 = 0.33\n"
    )
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--gates", str(gates), "--k-grid", "4,6,8"],
    )
    cli._run(args)
    assert captured["config"].k_grid == (4, 6, 8)


def test_a_grid_from_the_file_needs_no_flag(monkeypatch, tmp_path, cohort_dir) -> None:
    captured = _capture(monkeypatch)
    gates = _gates_file(
        tmp_path,
        "[gates.cross_algorithm_ari_by_k]\n2 = 0.51\n5 = 0.38\n\n[lane]\nk_grid = [2, 5]\n",
    )
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--gates", str(gates)],
    )
    cli._run(args)
    assert captured["config"].k_grid == (2, 5)


def test_the_two_ways_of_naming_a_grid_cannot_be_combined() -> None:
    args = _args(extra=["--k-grid", "4,6", "--k-min", "3"])
    with pytest.raises(SystemExit, match="cannot be combined"):
        cli._run(args)


def test_a_grid_value_that_is_not_a_number_is_refused() -> None:
    args = _args(extra=["--k-grid", "4,six"])
    with pytest.raises(SystemExit, match="not one of them"):
        cli._run(args)


def test_k_min_alone_no_longer_collapses_the_sweep(monkeypatch, cohort_dir) -> None:
    """It used to set the ceiling equal to the floor and screen a single k."""
    captured = _capture(monkeypatch)
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--k-min", "5"],
    )
    cli._run(args)
    assert captured["config"].k_grid == (5, 6, 7, 8)


def test_k_max_alone_no_longer_re_declares_the_floor(monkeypatch, cohort_dir) -> None:
    """It used to fall back to a bare 3 rather than to the grid in force."""
    captured = _capture(monkeypatch)
    args = _args(
        delivery=str(cohort_dir),
        bucket_map=str(cohort_dir / "section_buckets.json"),
        extra=["--k-max", "5"],
    )
    cli._run(args)
    assert captured["config"].k_grid == (3, 4, 5)


def test_the_template_command_writes_a_file_that_loads(tmp_path) -> None:
    """The affordance an adopter meets first: something to edit."""
    from engagement_kernel.engagement.config import GateThresholds
    from engagement_kernel.engagement.gate_config import load_gate_config

    path = tmp_path / "gates.toml"
    assert cli.main(["gates-template", str(path)]) == cli.EXIT_OK
    assert load_gate_config(path).gates == GateThresholds()


def _run_help() -> str:
    """The `run` help, with the usage block dropped.

    Dropped because every flag is named there too, so an index into the whole text
    finds the usage line and the ordering assertion below passes on any parser.
    """
    import argparse

    parser = cli.build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    text = action.choices["run"].format_help()
    body = text.split("options:", 1)[-1]
    assert "--seeds" in body, "the help body no longer lists the flags"
    return body


def test_the_help_separates_cost_from_threshold() -> None:
    """Conflating them is why an adopter never learns the gates are theirs."""
    text = _run_help()
    assert "cheaper verdict, same screens" in text
    assert "your deployment's thresholds" in text
    cost = text.index("cheaper verdict, same screens")
    thresholds = text.index("your deployment's thresholds")
    assert cost < thresholds
    for flag in ("--seeds", "--perturbation-draws"):
        assert cost < text.index(flag) < thresholds, f"{flag} is not in the cost group"
    for flag in ("--gates", "--k-grid", "--k-min", "--k-max"):
        assert text.index(flag) > thresholds, f"{flag} is not in the threshold group"


def test_the_threshold_group_points_at_the_derivation_tool() -> None:
    """The one threshold that cannot be inherited needs its way out named here."""
    text = _run_help()
    assert "derive_cross_algorithm_bars.py" in text
    assert "gates-template" in text
