#!/usr/bin/env python3
"""Derive the cross-algorithm agreement bar on your own panel.

The engagement lane screens a candidate number of clusters by fitting the same k
with a second algorithm and asking whether the two agree. That screen needs a bar,
and the bar is the one threshold in this package that cannot honestly be inherited.

Why not
-------

A bar on an agreement statistic means nothing until you know what agreement the two
algorithms reach **by chance**. The intuition is that unstructured data produces
agreement near zero, so any bar comfortably above zero is safe. That intuition is
wrong. k-means and Ward share an objective -- both prefer compact, roughly
equal-variance groups -- so they cut a featureless cloud along similar surfaces and
agree at an adjusted Rand index around 0.26 to 0.31 on a population with **no
cluster structure at all**. And that chance level falls as k rises.

So the level depends on your panel: on its row count, on how many features it
carries, and on the correlation structure of your own readers. Measured on one
publisher's data, the same rule gave bars about 0.10 higher on a six-feature panel
than on a nine-feature one. A bar carried across feature spaces is too permissive by
about that much, and the run reports a pass it has not earned.

That is why the shipped defaults are labelled as one newsroom's measurement, and why
this tool exists.

The rule
--------

1. **A null replicate** is a matrix of your panel's own shape with no k-cluster
   structure in it. Two constructions:

   ``gaussian``
       A draw from ``N(mean(M), cov(M))``. Preserves the mean vector and the full
       covariance matrix: one unimodal population, correlated the way your readers
       actually are, with no clusters.
   ``permute``
       Each feature column independently shuffled. Preserves every feature's
       marginal distribution exactly and destroys all joint structure, including
       the correlations a real population certainly has.

   **``gaussian`` governs and ``permute`` is reported beside it.** This is a
   judgement, and it is the most consequential one here, so it is stated rather
   than buried. The hypothesis the screen exists to exclude is not "these features
   are independent noise" -- no real subscriber panel looks like that, and
   calibrating against it would be calibrating against a straw null. It is "this is
   one population with the ordinary correlation structure of reader behaviour, and
   the k groups are an artifact of cutting it." Only ``gaussian`` is that
   hypothesis. Destroying the correlations makes the cloud nearly isotropic, and on
   an isotropic cloud the two algorithms agree *strongly* because there is no
   competing structure to disagree about -- so ``permute`` yields a much higher and
   much less stable bar. Measured on one publisher's panel it ran 0.94 at k=3
   against ``gaussian``'s 0.46. High agreement there reflects an absence of
   structure, not a demanding standard.

   Anyone who disagrees with that choice should argue with this paragraph, not with
   the number it produces. ``--null permute`` runs it the other way and says so in
   the output.

2. For each candidate k, draw ``--replicates`` replicates per panel and compute on
   each one **exactly the statistic the screen computes** --
   :func:`engagement_kernel.engagement.selection.cross_algorithm_statistic`, the
   same function the lane calls, not a re-implementation.

3. Pool the governing null's replicates across every panel given, per k.

4. **The bar at k is the 95th percentile of that pooled distribution, rounded up to
   two decimal places.** One-sided 95%, which is the same confidence convention the
   selection rule already uses for its survival bound, so the two are not two
   different notions of confidence.

5. If the derived bars span less than 0.05 across k, a single scalar bar equal to
   their maximum is enough and the per-k table is noise. Otherwise adopt the table.

6. A k with no derived bar is not screened against an inherited number. It refuses.

What the bar then means: *a k passes the cross-algorithm screen when the two
algorithms agree more than they would on an unclustered population of the same size,
dimension and covariance, at a 5% false-certification rate.*

Why this cannot be tuned to pass
--------------------------------

The derivation never observes the real statistic. It reads only permuted and
simulated matrices; your panel's actual cross-algorithm agreement at any k is not an
input at any point. The derivation depends on your panel only through its shape and
its covariance. There is no path by which a verdict you would prefer could steer the
number.

Two controls, both of which must pass before a bar is emitted:

``positive``
    On a synthetic panel of k well-separated blobs the statistic must score close to
    1 and clear the derived bar. A statistic blind to real structure cannot
    calibrate anything.
``negative, held out``
    Replicates drawn under a **different seed** from the ones that set the
    percentile must clear the bar about 5% of the time. Measuring that rate on the
    replicates that defined the percentile would be circular and proves nothing.

What a run leaves behind
------------------------

With ``--out``, the evidence file recording every distribution the run measured is
written *before* the controls run and rewritten with their verdict afterwards. A run
that fails a control still emits no gates fragment -- that refusal is the whole point
of having controls -- but the distributions behind the refused bar cost the entire run
to measure and are worth the same either way, so they survive. ``controls.status``
says which state the file is in, so evidence left by a crashed or refused run cannot
be mistaken for a certified derivation.

Cost
----

``replicates * k * seeds`` k-means fits plus ``replicates * k`` hierarchical fits per
panel, and the hierarchical fit is quadratic in rows. Start with ``--replicates 20``
to see the shape; a freeze wants 100. ``--jobs`` parallelises across cells.

Examples
--------
    # See the shape, cheaply.
    python3 tools/derive_cross_algorithm_bars.py panel.parquet \\
        --k-grid 2,3,4,5,6 --replicates 20 --seeds 5

    # A freeze, then screen against what it found.
    python3 tools/derive_cross_algorithm_bars.py panel.parquet \\
        --k-min 2 --k-max 10 --replicates 100 --jobs 4 --out derivation/
    engagement-kernel-engagement-lane run delivery/ --bucket-map buckets.json \\
        --gates my-gates.toml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# Parallelism here is across (panel, k, null) cells. Leave the linear-algebra
# libraries a small budget per worker, or every worker grabs every core and they
# spend their time fighting each other rather than fitting.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# The package this tool calibrates, imported here rather than inside the three
# functions that use it. Two of those three call sites are reached only after the
# whole derivation has run: ``render_bars_toml`` is the last thing a successful run
# does, and ``positive_control`` runs after ``run_cells``.
#
# The second one is worse than it looks, and it is measured rather than reasoned:
# with ``--jobs`` above 1 the null replicates are computed in spawned workers, so
# ``engagement_kernel.engagement.selection`` is still absent from the *parent*
# process's ``sys.modules`` after ``run_cells(..., jobs=2)`` returns. The parent's
# first evaluation of that import is the positive control, at the end.
#
# A derivation is a long single-shot run with no resume -- cost is
# ``replicates * k * seeds`` fits per panel and the hierarchical fit is quadratic in
# rows -- so an import that can first be evaluated at the end can cost the entire
# run. One did: a 40-minute run derived every bar, passed the positive control, and
# then died at the emit step because the checkout it had been launched from was
# deleted while it ran, taking the ``src`` directory off ``sys.path`` with it.
# Nothing was written. At module scope that same condition refuses at second zero.
try:
    from engagement_kernel.engagement.gate_config import GATE_CONFIG_VERSION  # noqa: E402
    from engagement_kernel.engagement.selection import cross_algorithm_statistic  # noqa: E402
except ImportError as exc:
    raise SystemExit(
        f"engagement_kernel is not importable, so this run would have failed at its "
        f"emit step with the whole compute budget already spent. Refusing now instead: "
        f"{exc}. This tool looks for the package in {REPO_ROOT / 'src'}; run it from a "
        f"checkout whose src directory still exists, or install the package."
    ) from exc

NULLS = ("gaussian", "permute")
GOVERNING_NULL = "gaussian"
DEFAULT_QUANTILE = 0.95
DEFAULT_DERIVE_SEED = 20260825
#: Offset rather than a second literal, so a run that changes ``--rng-seed`` moves
#: the held-out draws with it and the control stays non-circular.
HOLDOUT_SEED_OFFSET = 6_000_000
DEFAULT_SEEDS = 20
#: The bars are worth a per-k table only if they differ by more than this across k.
SCALAR_BAR_SPREAD = 0.05
#: A 95th percentile implies 5% clearance. The bar is rounded *up*, which can only
#: push the held-out rate below nominal, and a pooled rate is a finite sample. Above
#: this the percentile has not transported to fresh draws at all.
HOLDOUT_MAX_CLEARANCE = 0.15
POSITIVE_CONTROL_ARI = 0.95
BARS_NAME = "cross_algorithm_bars.toml"
EVIDENCE_NAME = "derivation_evidence.json"
#: The ``controls.status`` an evidence file carries before the controls have run, so a
#: file left behind by a crashed run cannot be mistaken for a certified one.
CONTROLS_NOT_RUN = "not run"


def write_evidence(out: Path, evidence: dict) -> Path:
    """Everything the run measured, as a file, callable more than once per run."""
    path = out / EVIDENCE_NAME
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    return path


def load_panel(path: Path, drop_columns: tuple[str, ...] = ()) -> np.ndarray:
    """The numeric feature columns of one panel, as the screen would see them.

    Non-numeric columns are dropped without comment -- an identifier column is the
    normal case, not a problem. Named columns in ``drop_columns`` go too, which is
    how a numeric identifier or a numeric outcome field is kept out of the shape the
    null is calibrated against.
    """
    suffix = path.suffix.lower()
    if suffix in (".parquet", ".pq"):
        frame = pd.read_parquet(path)
    elif suffix in (".csv", ".txt"):
        frame = pd.read_csv(path)
    else:
        raise SystemExit(
            f"{path} is neither a parquet nor a csv file, and those are the two this tool "
            "reads. Export your fit matrix as one of them"
        )
    frame = frame.drop(columns=[c for c in drop_columns if c in frame.columns])
    values = frame.select_dtypes("number").to_numpy(dtype=float)
    if values.size == 0:
        raise SystemExit(f"{path} carries no numeric columns, so there is no panel to calibrate")
    if not np.isfinite(values).all():
        raise SystemExit(
            f"{path} carries missing or infinite values. The null is a draw from this "
            "panel's own covariance, and a covariance over missing values is not one -- "
            "impute or drop before deriving"
        )
    if values.shape[0] <= values.shape[1]:
        raise SystemExit(
            f"{path} has {values.shape[0]} rows and {values.shape[1]} features. A covariance "
            "matrix needs more rows than features to be a covariance matrix"
        )
    return values


def null_replicate(values: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    """A matrix of the same shape as ``values`` with no k-cluster structure."""
    if kind == "permute":
        out = values.copy()
        for column in range(out.shape[1]):
            out[:, column] = out[rng.permutation(out.shape[0]), column]
        return out
    if kind == "gaussian":
        return rng.multivariate_normal(
            values.mean(axis=0), np.cov(values, rowvar=False), size=values.shape[0]
        )
    raise ValueError(f"unknown null {kind!r}")


def cell_seed(base: int, label: str, k: int, kind: str) -> int:
    """A distinct, reproducible seed per cell, stable across runs and workers.

    Not :func:`hash`: string hashing is salted per interpreter, so a
    ``hash()``-derived seed reproduces within one run and silently changes on the
    next. A derived bar has to be re-derivable.
    """
    digest = hashlib.sha256(f"{label}|{k}|{kind}".encode()).digest()
    return base + int.from_bytes(digest[:4], "big") % 1_000_000


def _cell(task: tuple) -> dict:
    """One (panel, k, null) cell: ``replicates`` null draws, no real statistic."""
    label, path, drop_columns, k, kind, replicates, seed, n_seeds = task
    values = load_panel(Path(path), tuple(drop_columns))
    rng = np.random.default_rng(seed)
    aris = [
        cross_algorithm_statistic(null_replicate(values, kind, rng), k, n_seeds=n_seeds)
        for _ in range(replicates)
    ]
    return {"panel": label, "k": k, "null": kind, "aris": aris}


def run_cells(tasks: list[tuple], jobs: int) -> list[dict]:
    """Spawn, never fork: a pool forked after a threaded linear-algebra kernel has
    already run in the parent can deadlock with no error and no output."""
    if jobs <= 1:
        return [_cell(task) for task in tasks]
    context = multiprocessing.get_context("spawn")
    with context.Pool(jobs) as pool:
        return pool.map(_cell, tasks)


def ceil_2dp(value: float) -> float:
    """Round up to two decimals. Up, so rounding can only make the bar stricter."""
    return math.ceil(value * 100.0 - 1e-9) / 100.0


def summarise(aris: list[float], quantile: float) -> dict:
    a = np.asarray(aris, dtype=float)
    return {
        "n": int(a.size),
        "mean": float(a.mean()),
        "sd": float(a.std(ddof=1)) if a.size > 1 else 0.0,
        "min": float(a.min()),
        "q50": float(np.quantile(a, 0.50)),
        "quantile": float(np.quantile(a, quantile)),
        "max": float(a.max()),
    }


def positive_control(bars: dict[int, float], *, n_seeds: int, seed: int) -> dict:
    """A statistic that cannot see real structure cannot calibrate a bar."""
    k = min(bars)
    rng = np.random.default_rng(seed)
    centres = np.eye(k) * 10.0
    values = np.repeat(centres, 200, axis=0) + rng.normal(0.0, 1.0, size=(k * 200, k))
    ari = cross_algorithm_statistic(values, k, n_seeds=n_seeds)
    bar = bars[k]
    return {
        "k": k,
        "ari": ari,
        "bar": bar,
        "passed": bool(ari >= POSITIVE_CONTROL_ARI and ari >= bar),
        "detail": (
            f"{k} well-separated blobs scored {ari:.4f}; needs >= "
            f"{POSITIVE_CONTROL_ARI} and >= the derived bar {bar:.2f}"
        ),
    }


def negative_control(
    bars: dict[int, float],
    panels: list[tuple[str, str]],
    drop_columns: tuple[str, ...],
    *,
    replicates: int,
    n_seeds: int,
    jobs: int,
    seed: int,
) -> dict:
    """Held-out nulls, drawn under a different seed, must clear the bar about 5%."""
    ks = sorted(bars)
    tasks = [
        (
            label,
            path,
            drop_columns,
            k,
            GOVERNING_NULL,
            replicates,
            cell_seed(seed, label, k, GOVERNING_NULL),
            n_seeds,
        )
        for label, path in panels
        for k in ks
    ]
    results = run_cells(tasks, jobs)
    per_k = {}
    for k in ks:
        pooled = [ari for r in results if r["k"] == k for ari in r["aris"]]
        cleared = sum(1 for ari in pooled if ari >= bars[k])
        per_k[k] = {"draws": len(pooled), "cleared": cleared, "rate": cleared / len(pooled)}
    rates = [entry["rate"] for entry in per_k.values()]
    return {
        "per_k": {str(k): entry for k, entry in per_k.items()},
        "max_rate": max(rates),
        "passed": bool(max(rates) <= HOLDOUT_MAX_CLEARANCE),
        "detail": (
            "held-out clearance per k: "
            + ", ".join(f"k={k} {entry['rate']:.3f}" for k, entry in per_k.items())
            + f" (expected about 0.05, allowed up to {HOLDOUT_MAX_CLEARANCE})"
        ),
    }


def render_bars_toml(
    bars: dict[int, float],
    *,
    governing: str,
    quantile: float,
    replicates: int,
    panels: list[tuple[str, str]],
    shapes: dict[str, tuple[int, int]],
    scalar: float | None,
) -> str:
    """A gates-file fragment: paste it into your gates file, or use it as one."""
    described = ", ".join(
        f"{label} ({shapes[label][0]} rows x {shapes[label][1]} features)" for label, _ in panels
    )
    note = ""
    if scalar is not None:
        note = (
            f"#\n# The derived bars span less than {SCALAR_BAR_SPREAD} across k, so a single\n"
            f"# scalar bar of {scalar:g} carries the same information as the table below.\n"
        )
    rows = "\n".join(f"{k} = {bar:g}" for k, bar in sorted(bars.items()))
    return f"""\
# Cross-algorithm agreement bars, derived on this deployment's own panel.
#
# Rule: the {quantile:g} quantile of the {governing} null's cross-algorithm agreement,
# pooled over {replicates} replicates per panel per k, rounded up to two decimals.
# Generated by tools/derive_cross_algorithm_bars.py.
#
# Panel: {described}
#
# THE BAR DOES NOT TRANSPORT ACROSS FEATURE SPACES. Chance agreement rises as
# dimensionality falls, so a bar derived on this feature set is too permissive for a
# narrower one. If the fit feature set changes, derive again.
{note}
version = {GATE_CONFIG_VERSION}

[gates.cross_algorithm_ari_by_k]
{rows}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive_cross_algorithm_bars",
        description=(
            "Derive the cross-algorithm agreement bar on your own panel, by measuring "
            "what two clustering algorithms agree on when there is nothing to find."
        ),
        epilog="The governing null is a judgement, not a measurement. Read the module "
        "docstring before changing --null.",
    )
    parser.add_argument("panels", nargs="+", help="parquet or csv files holding fit matrices")
    parser.add_argument(
        "--k-grid",
        default=None,
        help="candidate cluster counts, comma separated. Need not be contiguous",
    )
    parser.add_argument("--k-min", type=int, default=2, help="smallest candidate k")
    parser.add_argument("--k-max", type=int, default=10, help="largest candidate k")
    parser.add_argument(
        "--replicates",
        type=int,
        default=100,
        help="null draws per panel per k. 20 to see the shape, 100 for a freeze",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=DEFAULT_SEEDS,
        help="starting points in the k-means champion protocol. Match the value the "
        "lane will run, or the statistic being calibrated is not the one being screened",
    )
    parser.add_argument(
        "--null",
        default=GOVERNING_NULL,
        choices=NULLS,
        help="which null governs the bar. The default is argued for in the module "
        "docstring; the other is reported alongside it either way",
    )
    parser.add_argument("--quantile", type=float, default=DEFAULT_QUANTILE)
    parser.add_argument("--rng-seed", type=int, default=DEFAULT_DERIVE_SEED)
    parser.add_argument(
        "--holdout-replicates",
        type=int,
        default=None,
        help="draws for the held-out negative control. Defaults to --replicates",
    )
    parser.add_argument(
        "--drop-columns",
        default="",
        help="comma-separated column names to exclude, for numeric identifiers or "
        "outcome fields that are not model inputs",
    )
    parser.add_argument("--jobs", type=int, default=1, help="parallel cells")
    parser.add_argument(
        "--out",
        default=None,
        help="directory for the gates fragment and the evidence file. Without it, "
        "both go to standard output",
    )
    return parser


def resolve_ks(args: argparse.Namespace) -> tuple[int, ...]:
    if args.k_grid:
        ks = tuple(sorted({int(piece) for piece in args.k_grid.split(",") if piece.strip()}))
    else:
        ks = tuple(range(args.k_min, args.k_max + 1))
    if not ks:
        raise SystemExit("no candidate cluster counts were named")
    if min(ks) < 2:
        raise SystemExit("a partition needs at least two clusters, so k below 2 is not a candidate")
    return ks


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ks = resolve_ks(args)
    drop_columns = tuple(c.strip() for c in args.drop_columns.split(",") if c.strip())
    panels = [(Path(path).stem, path) for path in args.panels]
    if len({label for label, _ in panels}) != len(panels):
        raise SystemExit(
            "two panels share a file name, and the per-cell seed is derived from it, so "
            "they would draw identical replicates. Rename one"
        )

    shapes: dict[str, tuple[int, int]] = {}
    for label, path in panels:
        values = load_panel(Path(path), drop_columns)
        shapes[label] = (int(values.shape[0]), int(values.shape[1]))
    dimensions = {d for _, d in shapes.values()}
    if len(dimensions) > 1:
        raise SystemExit(
            f"the panels carry different feature counts {sorted(dimensions)}. Chance "
            "agreement depends on dimensionality, so pooling across feature spaces would "
            "average two different quantities. Derive once per feature space"
        )

    # A quantile needs enough draws behind it to be an interpolation rather than the
    # maximum. Refused here rather than left to the held-out control, which does
    # catch it -- a 95th percentile of two draws clears about half the time -- but
    # reports it as "the bar did not transport", which names the symptom and not the
    # cause.
    pooled_draws = args.replicates * len(panels)
    minimum = math.ceil(1.0 / (1.0 - args.quantile))
    if pooled_draws < minimum:
        raise SystemExit(
            f"{args.replicates} replicates across {len(panels)} panel(s) pool to "
            f"{pooled_draws} draws per k, and the {args.quantile:g} quantile of fewer than "
            f"{minimum} draws is just their maximum. Raise --replicates to at least "
            f"{math.ceil(minimum / len(panels))}"
        )
    if pooled_draws < 100:
        print(
            f"note: {pooled_draws} pooled draws per k. Enough to see the shape; a bar you "
            "intend to freeze against wants 100 or more"
        )

    tasks = [
        (
            label,
            path,
            drop_columns,
            k,
            kind,
            args.replicates,
            cell_seed(args.rng_seed, label, k, kind),
            args.seeds,
        )
        for label, path in panels
        for k in ks
        for kind in NULLS
    ]
    print(
        f"deriving: {len(panels)} panel(s), k={list(ks)}, {args.replicates} replicates "
        f"per cell, both nulls, {len(tasks)} cells, jobs={args.jobs}"
    )
    started = time.time()
    results = run_cells(tasks, args.jobs)
    print(f"deriving: null replicates done in {time.time() - started:.0f}s")

    pooled: dict[str, dict[int, list[float]]] = {kind: {k: [] for k in ks} for kind in NULLS}
    for result in results:
        pooled[result["null"]][result["k"]].extend(result["aris"])

    raw = {k: float(np.quantile(pooled[args.null][k], args.quantile)) for k in ks}
    bars = {k: ceil_2dp(value) for k, value in raw.items()}
    other = [kind for kind in NULLS if kind != args.null]
    spread = max(bars.values()) - min(bars.values())
    scalar = max(bars.values()) if spread < SCALAR_BAR_SPREAD else None

    print(f"\nderived bars ({args.null} null governs, q={args.quantile:g})")
    print(f"  {'k':<4}{'bar':<8}{'raw':<10}{'null mean':<12}{'null sd':<10}{other[0]} bar")
    for k in ks:
        summary = summarise(pooled[args.null][k], args.quantile)
        alt = ceil_2dp(float(np.quantile(pooled[other[0]][k], args.quantile)))
        print(
            f"  {k:<4}{bars[k]:<8.2f}{raw[k]:<10.4f}{summary['mean']:<12.4f}"
            f"{summary['sd']:<10.4f}{alt:.2f}"
        )
    if scalar is not None:
        print(
            f"  spread across k is {spread:.4f}, under {SCALAR_BAR_SPREAD}: a single scalar "
            f"bar of {scalar:g} is enough"
        )
    else:
        print(f"  spread across k is {spread:.4f}: adopt the per-k table")

    evidence = {
        "tool": "tools/derive_cross_algorithm_bars.py",
        "governing_null": args.null,
        "reported_null": other[0],
        "quantile": args.quantile,
        "replicates_per_cell": args.replicates,
        "champion_seeds": args.seeds,
        "rng_seed": args.rng_seed,
        "panels": {
            label: {"rows": shapes[label][0], "features": shapes[label][1]} for label, _ in panels
        },
        "bar_by_k": {str(k): bars[k] for k in ks},
        "raw_quantile_by_k": {str(k): raw[k] for k in ks},
        "spread_across_k": spread,
        "scalar_bar": scalar,
        "null_distributions": {
            kind: {str(k): summarise(pooled[kind][k], args.quantile) for k in ks} for kind in NULLS
        },
        "controls": {"status": CONTROLS_NOT_RUN},
    }

    # Written *before* the controls run, and rewritten after with their verdict.
    #
    # A failed control refuses to emit a bar, and should: an uncontrolled bar is the
    # same kind of number as the inherited one it would replace. But the null
    # distributions behind it cost the entire run to measure and are worth the same
    # whether the controls pass or not. Writing them first means a refused run -- or
    # one killed partway through the controls -- leaves something to read instead of
    # nothing. Only the gates fragment waits for the verdict.
    out: Path | None = None
    if args.out is not None:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        print(f"\nwrote {write_evidence(out, evidence)} (measurements; controls not yet run)")

    print("\ncontrols")
    positive = positive_control(bars, n_seeds=args.seeds, seed=args.rng_seed)
    print(f"  positive  {'PASS' if positive['passed'] else 'FAIL'}  {positive['detail']}")
    negative = negative_control(
        bars,
        panels,
        drop_columns,
        replicates=args.holdout_replicates or args.replicates,
        n_seeds=args.seeds,
        jobs=args.jobs,
        seed=args.rng_seed + HOLDOUT_SEED_OFFSET,
    )
    print(f"  negative  {'PASS' if negative['passed'] else 'FAIL'}  {negative['detail']}")
    passed = bool(positive["passed"] and negative["passed"])
    evidence["controls"] = {
        "status": "passed" if passed else "failed",
        "positive": positive,
        "negative_holdout": negative,
    }
    if out is not None:
        write_evidence(out, evidence)
    if not passed:
        print(
            "\na control failed, so no bar is emitted. A bar from an uncontrolled "
            "derivation is the same kind of number as the one it would replace.",
            file=sys.stderr,
        )
        if out is not None:
            print(f"what the run measured is in {out / EVIDENCE_NAME}", file=sys.stderr)
        return 4

    fragment = render_bars_toml(
        bars,
        governing=args.null,
        quantile=args.quantile,
        replicates=args.replicates,
        panels=panels,
        shapes=shapes,
        scalar=scalar,
    )
    if out is None:
        print("\n" + fragment)
        return 0
    (out / BARS_NAME).write_text(fragment)
    print(f"\nwrote {out / BARS_NAME}")
    print(f"wrote {out / EVIDENCE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
