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
