"""Command line for the engagement lane.

Two commands, because the two things a person wants are different: run the lane over
a delivery, or generate a synthetic cohort big enough to run it on.

The run prints the resolved configuration before it does any work. That is
deliberate: every declaration that changes what the numbers mean is on screen before
the numbers exist, so a run against the wrong week anchor or the wrong scored
population is visible at the top of the log rather than inferred from the output.
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
    # Selection cost knobs. The defaults are the production settings, and they are
    # expensive on purpose: every candidate k is re-screened on many perturbed panels,
    # which is `draws * seeds` clustering fits per candidate. A first run against real
    # data wants a narrow sweep and few draws to see the shape; a freeze wants the
    # defaults. Exposed so the difference is a flag rather than an edit.
    run.add_argument("--k-min", type=int, default=None, help="smallest candidate k")
    run.add_argument("--k-max", type=int, default=None, help="largest candidate k")
    run.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="starting points per candidate k in the stability screen",
    )
    run.add_argument(
        "--perturbation-draws",
        type=int,
        default=None,
        help="perturbed panels each candidate k is re-screened on",
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
    from engagement_kernel.engagement.config import GateThresholds
    from engagement_kernel.engagement.outputs import write_outputs

    overrides: dict[str, object] = {}
    if args.surface is not None:
        overrides["surface"] = args.surface
    if args.k_min is not None or args.k_max is not None:
        low = args.k_min if args.k_min is not None else 3
        high = args.k_max if args.k_max is not None else low
        if high < low:
            raise SystemExit(f"--k-max {high} is below --k-min {low}")
        overrides["k_grid"] = tuple(range(low, high + 1))
    if args.seeds is not None:
        overrides["n_seeds"] = args.seeds
    if args.perturbation_draws is not None:
        overrides["gates"] = dataclasses.replace(
            GateThresholds(), selection_perturbation_draws=args.perturbation_draws
        )

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
    if args.command == "cohort":
        return _cohort(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
