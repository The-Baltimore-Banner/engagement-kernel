"""Command line for the engagement lane.

Two commands, because the two things a person wants are different: run the lane over
a delivery, or generate a synthetic cohort big enough to run it on.

The run prints the resolved configuration before it does any work. That is
deliberate: every declaration that changes what the numbers mean is on screen before
the numbers exist, so a run against the wrong week anchor or the wrong scored
population is visible at the top of the log rather than inferred from the output.

Two kinds of knob, kept apart on purpose
----------------------------------------

``--seeds`` and ``--perturbation-draws`` buy a **cheaper verdict on the same
screens**. Lowering them makes the answer noisier, never more permissive, and a
first look at real data wants them low.

``--gates`` and ``--k-grid`` set **what your deployment considers good enough**.
They change the verdict itself.

These were once presented together as "selection cost knobs", which is how an
adopter came away believing the thresholds were the engine's rather than theirs.
They are yours: ``gates-template`` writes the current values out as a file to edit,
and ``tools/derive_cross_algorithm_bars.py`` derives the one threshold that cannot
honestly be inherited at all.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from pathlib import Path

EXIT_OK = 0
EXIT_NO_MODEL = 3
EXIT_GATED = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engagement-kernel-engagement-lane",
        description=(
            "Fit the engagement model on a conforming delivery and score every complete "
            "week. Reads the day boundary, week anchor, article-view definition and "
            "scored population from the delivery's own manifest."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the lane over a delivery")
    run.add_argument("delivery", help="directory holding the contract delivery and its manifest")
    run.add_argument(
        "--bucket-map",
        required=True,
        help="path to the section bucket map (JSON). No default: the section taxonomy is "
        "the publisher's own and the engine will not invent one",
    )
    run.add_argument("--output-dir", default=None, help="write the output tables here")
    run.add_argument(
        "--max-weeks",
        type=int,
        default=None,
        help="score only the most recent N complete weeks",
    )
    run.add_argument(
        "--surface",
        default=None,
        choices=("intensity", "joint"),
        help="override the surface resolved from the manifest. Naming a surface the "
        "delivery cannot support is refused rather than downgraded",
    )
    run.add_argument(
        "--interpretability-reviewed",
        action="store_true",
        help="record that a person has reviewed and named the clusters. Without it the "
        "interpretability gate fails, which is the intended default",
    )
    # Two groups, because conflating them is how an adopter comes away believing the
    # thresholds belong to the engine. Cost buys a faster answer to the same
    # question; a threshold changes the question.
    cost = run.add_argument_group(
        "cheaper verdict, same screens",
        "Lower these for a first look at real data. They make the verdict noisier, "
        "never more permissive: every candidate k is re-screened on `draws * seeds` "
        "clustering fits, and the defaults are what a freeze should use.",
    )
    cost.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="starting points per candidate k in the stability screen",
    )
    cost.add_argument(
        "--perturbation-draws",
        type=int,
        default=None,
        help="perturbed panels each candidate k is re-screened on",
    )

    thresholds = run.add_argument_group(
        "your deployment's thresholds",
        "These decide what counts as good enough, and they are yours rather than "
        "this package's. `gates-template` writes the current values out as a file to "
        "edit; tools/derive_cross_algorithm_bars.py derives the cross-algorithm bar "
        "on your own panel, which is the one threshold that cannot be inherited.",
    )
    thresholds.add_argument(
        "--gates",
        default=None,
        help="path to a gates file (TOML) holding this deployment's own thresholds. "
        "Omit it to run this package's defaults, which are one other newsroom's "
        "measurements",
    )
    thresholds.add_argument(
        "--k-grid",
        default=None,
        help="candidate cluster counts to screen, comma separated. Need not be "
        "contiguous: --k-grid 4,6,8 screens exactly those three. Any k of 2 or more "
        "is allowed, given a bar declared for it in the gates file",
    )
    thresholds.add_argument(
        "--k-min",
        type=int,
        default=None,
        help="smallest candidate k, for a contiguous sweep. Not usable with --k-grid",
    )
    thresholds.add_argument(
        "--k-max",
        type=int,
        default=None,
        help="largest candidate k, for a contiguous sweep. Not usable with --k-grid",
    )
    run.add_argument(
        "--already-built",
        action="store_true",
        help="the directory holds intermediate tables rather than a delivery",
    )
    run.add_argument(
        "--manifest-dir",
        default=None,
        help="with --already-built, where to read the manifest from",
    )

    template = sub.add_parser(
        "gates-template",
        help="write this package's gate thresholds out as an editable gates file",
        description="Write the current gate thresholds as a commented TOML file. The "
        "file rendered unedited reproduces today's defaults exactly, so a diff against "
        "it shows precisely what a deployment changed.",
    )
    template.add_argument(
        "path",
        nargs="?",
        default=None,
        help="where to write it. Omitted, it goes to standard output",
    )

    cohort = sub.add_parser(
        "cohort", help="write a synthetic cohort delivery large enough to fit on"
    )
    cohort.add_argument("directory")
    cohort.add_argument("--readers", type=int, default=400)
    cohort.add_argument("--seed", type=int, default=20260824)
    return parser


def _run(args: argparse.Namespace) -> int:
    from engagement_kernel.contract.manifest import load_manifest
    from engagement_kernel.engagement import lane
    from engagement_kernel.engagement.buckets import load_bucket_map
    from engagement_kernel.engagement.config import GateThresholds, LaneConfig
    from engagement_kernel.engagement.gate_config import GateConfig, load_gate_config
    from engagement_kernel.engagement.outputs import write_outputs

    overrides: dict[str, object] = {}
    if args.surface is not None:
        overrides["surface"] = args.surface

    # The gates file is the base, and the flags amend it. The order matters: this
    # used to build a fresh GateThresholds() for --perturbation-draws, which was
    # harmless only for as long as nothing else could supply gates. It would have
    # silently discarded this whole file the moment one could.
    gate_config = GateConfig(gates=GateThresholds())
    if args.gates is not None:
        gate_config = load_gate_config(args.gates)
        overrides.update(gate_config.lane_overrides)
    gates = gate_config.gates
    if args.perturbation_draws is not None:
        gates = dataclasses.replace(gates, selection_perturbation_draws=args.perturbation_draws)
    overrides["gates"] = gates

    if args.k_grid is not None and (args.k_min is not None or args.k_max is not None):
        raise SystemExit(
            "--k-grid names the candidate counts outright, so it cannot be combined with "
            "--k-min or --k-max"
        )
    if args.k_grid is not None:
        overrides["k_grid"] = _parse_k_grid(args.k_grid)
    elif args.k_min is not None or args.k_max is not None:
        # A missing end comes from the grid already in force -- the gates file's if it
        # declared one, otherwise the package default. It used to fall back to a bare
        # 3, so `--k-max 6` silently re-declared the floor and `--k-min 5` collapsed
        # the sweep to a single k.
        base = overrides.get("k_grid") or LaneConfig.__dataclass_fields__["k_grid"].default
        low = args.k_min if args.k_min is not None else min(base)
        high = args.k_max if args.k_max is not None else max(base)
        if high < low:
            raise SystemExit(f"--k-max {high} is below --k-min {low}")
        overrides["k_grid"] = tuple(range(low, high + 1))
    if args.seeds is not None:
        overrides["n_seeds"] = args.seeds

    bucket_map = load_bucket_map(args.bucket_map)
    if args.already_built:
        manifest_dir = args.manifest_dir or args.delivery
        manifest = load_manifest(manifest_dir)
        config = lane.resolve_config(manifest, bucket_map, **overrides)
        inputs = lane.read_intermediate(args.delivery)
    else:
        manifest = load_manifest(args.delivery)
        config = lane.resolve_config(manifest, bucket_map, **overrides)
        inputs = lane.inputs_from_build(args.delivery)

    print(config.describe())
    print(gate_config.describe())
    print()
    result = lane.run_lane(
        inputs,
        config,
        interpretability_reviewed=args.interpretability_reviewed,
        max_weeks=args.max_weeks,
    )
    print(result.summary())

    if args.output_dir is not None:
        for path in write_outputs(result.tables, args.output_dir):
            print(f"wrote {path}")
        if result.bundle is not None:
            bundle_path = Path(args.output_dir) / "frozen_bundle.json"
            result.bundle.save(bundle_path)
            print(f"wrote {bundle_path}")

    if not result.froze_a_model:
        print(
            "no model was frozen: no candidate k survived the selection screens. See the "
            "k_selection table",
            file=sys.stderr,
        )
        return EXIT_NO_MODEL
    if result.decision is not None and not result.decision.publish_labels:
        print(f"labels are gated: {result.decision.describe()}", file=sys.stderr)
        return EXIT_GATED
    return EXIT_OK


def _parse_k_grid(text: str) -> tuple[int, ...]:
    """``"4,6,8"`` -> ``(4, 6, 8)``. Refuses rather than skips a value it cannot read."""
    grid: list[int] = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            grid.append(int(piece))
        except ValueError:
            raise SystemExit(
                f"--k-grid takes cluster counts separated by commas, so {piece!r} is not one "
                "of them (for example: --k-grid 4,6,8)"
            ) from None
    if not grid:
        raise SystemExit("--k-grid named no candidate cluster counts")
    return tuple(grid)


def _gates_template(args: argparse.Namespace) -> int:
    from engagement_kernel.engagement.gate_config import gate_config_template

    text = gate_config_template()
    if args.path is None:
        print(text, end="")
        return EXIT_OK
    path = Path(args.path)
    path.write_text(text)
    print(f"wrote {path}")
    return EXIT_OK


def _cohort(args: argparse.Namespace) -> int:
    from engagement_kernel.engagement.cohort import CohortSpec, write_cohort

    spec = CohortSpec(n_readers=args.readers, seed=args.seed)
    for path in write_cohort(args.directory, spec):
        print(path)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "gates-template":
        return _gates_template(args)
    if args.command == "cohort":
        return _cohort(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
