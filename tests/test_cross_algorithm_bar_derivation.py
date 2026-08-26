"""Deriving the cross-algorithm bar, and the measurement that makes it necessary.

The screen this calibrates asks whether two algorithms agree, and a bar on an
agreement statistic is meaningless until you know what agreement they reach by
chance. The whole design rests on one measured fact, so it is measured here rather
than asserted in a comment: **two algorithms that share an objective agree well above
zero on a population with no cluster structure at all.**

Three other properties are tested because each one, if it broke, would leave a
plausible-looking number behind.

*The tool measures the screen's own statistic.* If it re-implemented it, it would
calibrate something the lane does not compute. So the shared function is checked
against what the screen reports.

*The nulls are what they claim to be.* The governing null preserves the panel's
correlation structure and the alternative destroys it. That difference is the whole
argument for which one governs, so it is measured, not stated.

*The controls gate the emission.* A derivation whose positive control fails must emit
nothing, because a bar from an uncontrolled derivation is the same kind of number as
the one it replaces.

*A long run cannot be lost to something checkable at the start, and cannot be lost
in full.* This is the one section here that exists because of an incident rather than
a design argument: a forty-minute run derived every bar, passed the positive control,
and then died on an import at its emit step, writing nothing. So two things are held.
No import in the tool sits inside a function, checked against the syntax tree rather
than against prose; and a run whose package cannot be imported is shown to stop before
the compute by actually breaking the import in a subprocess, with the same invocation
against a working package as the control.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import derive_cross_algorithm_bars as derive
import numpy as np
import pytest

from engagement_kernel.engagement.config import GateThresholds
from engagement_kernel.engagement.selection import (
    _run_screens,
    cross_algorithm_statistic,
)

SEEDS = 3


@pytest.fixture(scope="module")
def structured() -> np.ndarray:
    """A panel with real structure: three separated blobs in four dimensions."""
    rng = np.random.default_rng(11)
    centres = np.array([[0.0, 0.0, 0.0, 0.0], [6.0, 6.0, 0.0, 0.0], [0.0, 0.0, 6.0, 6.0]])
    return np.repeat(centres, 100, axis=0) + rng.normal(0.0, 0.6, size=(300, 4))


@pytest.fixture(scope="module")
def correlated() -> np.ndarray:
    """One population, no clusters, correlated the way real behaviour is."""
    rng = np.random.default_rng(23)
    base = rng.normal(size=(400, 1))
    noise = rng.normal(scale=0.3, size=(400, 4))
    return base + noise


# --- the same code path, checked rather than claimed --------------------------


def test_the_derivation_measures_the_screens_own_statistic(structured: np.ndarray) -> None:
    """A re-implementation would calibrate a number the lane never computes."""
    gates = GateThresholds()
    _, statistics = _run_screens(structured, 3, SEEDS, gates)
    assert cross_algorithm_statistic(structured, 3, n_seeds=SEEDS) == pytest.approx(
        statistics["cross_algorithm"]
    )


# --- the measurement the whole design rests on -------------------------------


def test_two_algorithms_agree_well_above_zero_on_a_structureless_panel(
    correlated: np.ndarray,
) -> None:
    """The finding that makes an inherited bar unsafe.

    If chance agreement really sat near zero, any bar comfortably above zero would be
    safe and none of this machinery would be needed. It does not.
    """
    rng = np.random.default_rng(7)
    aris = [
        cross_algorithm_statistic(
            derive.null_replicate(correlated, "gaussian", rng), 3, n_seeds=SEEDS
        )
        for _ in range(5)
    ]
    assert float(np.mean(aris)) > 0.15, (
        "chance agreement measured near zero here, which would undermine the reason "
        f"this bar is derived at all: {aris}"
    )


def test_the_statistic_can_still_see_real_structure(structured: np.ndarray) -> None:
    """The other half of the same point: it is not simply always high."""
    assert cross_algorithm_statistic(structured, 3, n_seeds=SEEDS) > 0.95


# --- the nulls are what the argument says they are ---------------------------


def _mean_offdiagonal_correlation(values: np.ndarray) -> float:
    corr = np.corrcoef(values, rowvar=False)
    off = corr[~np.eye(corr.shape[0], dtype=bool)]
    return float(np.abs(off).mean())


def test_the_governing_null_preserves_correlation_and_the_alternative_destroys_it(
    correlated: np.ndarray,
) -> None:
    """Measured, because this difference is the entire argument for which null governs.

    Permutation tests "these features are independent noise", a hypothesis nobody
    holds about a real reader panel. The Gaussian reference tests "one correlated
    population, cut into k pieces", which is the hypothesis the screen exists to
    exclude.
    """
    rng = np.random.default_rng(3)
    original = _mean_offdiagonal_correlation(correlated)
    gaussian = _mean_offdiagonal_correlation(derive.null_replicate(correlated, "gaussian", rng))
    permuted = _mean_offdiagonal_correlation(derive.null_replicate(correlated, "permute", rng))
    assert original > 0.8, "the fixture is not correlated, so the test measures nothing"
    assert gaussian > 0.7, f"the Gaussian null lost the correlation structure: {gaussian}"
    assert permuted < 0.2, f"the permutation null kept the correlation structure: {permuted}"


def test_a_null_replicate_keeps_the_panels_shape(correlated: np.ndarray) -> None:
    rng = np.random.default_rng(5)
    for kind in derive.NULLS:
        assert derive.null_replicate(correlated, kind, rng).shape == correlated.shape


def test_an_unknown_null_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown null"):
        derive.null_replicate(np.zeros((4, 2)), "shuffle", np.random.default_rng(1))


# --- rounding is one-directional ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.4561, 0.46), (0.3086, 0.31), (0.31, 0.31), (0.3100001, 0.32), (0.0, 0.0)],
)
def test_the_bar_rounds_up(raw: float, expected: float) -> None:
    """Up, so rounding can only make the screen stricter than the measurement."""
    assert derive.ceil_2dp(raw) == pytest.approx(expected)


# --- the seed is re-derivable ------------------------------------------------


def test_the_cell_seed_is_stable_across_interpreters() -> None:
    """Not `hash()`: that is salted per process, so a bar would not re-derive."""
    assert derive.cell_seed(100, "panel", 5, "gaussian") == derive.cell_seed(
        100, "panel", 5, "gaussian"
    )
    assert derive.cell_seed(100, "panel", 5, "gaussian") != derive.cell_seed(
        100, "panel", 6, "gaussian"
    )
    assert derive.cell_seed(100, "panel", 5, "gaussian") != derive.cell_seed(
        100, "panel", 5, "permute"
    )


# --- refusals that protect the number ----------------------------------------


def _write_panel(path, values: np.ndarray) -> str:
    import pandas as pd

    frame = pd.DataFrame(values, columns=[f"f{i}" for i in range(values.shape[1])])
    frame.to_csv(path, index=False)
    return str(path)


def test_too_few_replicates_is_refused_by_name(tmp_path, correlated: np.ndarray) -> None:
    """The held-out control does catch this, but reports it as a failure to transport."""
    panel = _write_panel(tmp_path / "panel.csv", correlated)
    with pytest.raises(SystemExit, match="just their maximum"):
        derive.main([panel, "--k-grid", "3", "--replicates", "4", "--seeds", str(SEEDS)])


def test_panels_of_different_widths_are_refused(tmp_path, correlated: np.ndarray) -> None:
    """Chance agreement depends on dimensionality, so pooling would average two things."""
    wide = _write_panel(tmp_path / "wide.csv", correlated)
    narrow = _write_panel(tmp_path / "narrow.csv", correlated[:, :2])
    with pytest.raises(SystemExit, match="different feature counts"):
        derive.main([wide, narrow, "--k-grid", "3", "--replicates", "20"])


def test_two_panels_with_the_same_name_are_refused(tmp_path, correlated: np.ndarray) -> None:
    """The per-cell seed comes from the file name, so they would draw identically."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    one = _write_panel(first / "panel.csv", correlated)
    two = _write_panel(second / "panel.csv", correlated)
    with pytest.raises(SystemExit, match="share a file name"):
        derive.main([one, two, "--k-grid", "3", "--replicates", "20"])


def test_a_panel_with_missing_values_is_refused(tmp_path, correlated: np.ndarray) -> None:
    """A covariance over missing values is not a covariance."""
    holed = correlated.copy()
    holed[0, 0] = np.nan
    panel = _write_panel(tmp_path / "panel.csv", holed)
    with pytest.raises(SystemExit, match="missing or infinite"):
        derive.main([panel, "--k-grid", "3", "--replicates", "20"])


def test_a_single_cluster_is_not_a_candidate(tmp_path, correlated: np.ndarray) -> None:
    panel = _write_panel(tmp_path / "panel.csv", correlated)
    with pytest.raises(SystemExit, match="at least two clusters"):
        derive.main([panel, "--k-grid", "1", "--replicates", "20"])


def test_a_failing_control_emits_no_bar(tmp_path, monkeypatch, correlated: np.ndarray) -> None:
    """The gate on the emission, exercised by making the positive control unpassable.

    Without this the controls are decoration: nothing would prove that a failure
    actually stops the number from being written. What a refused run *may* leave is
    its measurements, so this asserts the absence of the fragment specifically rather
    than the absence of the output directory.
    """
    monkeypatch.setattr(derive, "POSITIVE_CONTROL_ARI", 1.5)
    panel = _write_panel(tmp_path / "panel.csv", correlated)
    out = tmp_path / "out"
    code = derive.main(
        [
            panel,
            "--k-grid",
            "3",
            "--replicates",
            "20",
            "--seeds",
            str(SEEDS),
            "--out",
            str(out),
        ]
    )
    assert code == 4
    assert not (out / derive.BARS_NAME).exists(), "a bar was written despite a failed control"


# --- end to end --------------------------------------------------------------


def test_what_the_tool_emits_is_a_gates_file(tmp_path, correlated: np.ndarray) -> None:
    """The output is not a report to read and retype. It is configuration to use."""
    from engagement_kernel.engagement.gate_config import parse_gate_config

    panel = _write_panel(tmp_path / "panel.csv", correlated)
    out = tmp_path / "out"
    code = derive.main(
        [
            panel,
            "--k-grid",
            "2,3",
            "--replicates",
            "20",
            "--seeds",
            str(SEEDS),
            "--out",
            str(out),
        ]
    )
    assert code == 0

    fragment = (out / "cross_algorithm_bars.toml").read_text()
    config = parse_gate_config(fragment)
    assert sorted(config.gates.cross_algorithm_ari_by_k) == [2, 3]
    for k in (2, 3):
        assert 0.0 <= config.gates.cross_algorithm_bar(k) <= 1.0
    # It says whose panel it came from and that it does not travel.
    assert "does not transport across feature spaces" in fragment.lower()
    assert f"{correlated.shape[0]} rows" in fragment

    evidence = json.loads((out / "derivation_evidence.json").read_text())
    assert evidence["governing_null"] == "gaussian"
    assert evidence["controls"]["positive"]["passed"] is True
    assert evidence["controls"]["negative_holdout"]["passed"] is True
    # Both nulls recorded, so the judgement in the rule can be re-examined without
    # re-running anything.
    assert set(evidence["null_distributions"]) == set(derive.NULLS)


# --- what a long run cannot lose ---------------------------------------------

TOOL_PATH = Path(derive.__file__).resolve()
REPO_SRC = TOOL_PATH.parents[1] / "src"


def test_no_import_in_the_tool_is_deferred_into_a_function() -> None:
    """Read the syntax tree, not the prose, and not the import's own line number.

    The rule this holds is about *when* an import is first evaluated, and the thing
    that made it expensive was that two of the three deferred imports were reached
    only after the derivation had finished. Function-scope is the property that
    allows that, so function-scope is what is banned -- a positional rule such as
    "above ``run_cells``" would pass for an import moved one line up and still
    deferred.

    Stated as a whole-file ban rather than a ban on importing this package, because
    the reason has nothing to do with which package it is. Anyone who later needs a
    deferred import here has to argue with this test, which is the intent.
    """
    tree = ast.parse(TOOL_PATH.read_text())
    deferred = [
        f"{holder.name}() line {node.lineno}"
        for holder in ast.walk(tree)
        if isinstance(holder, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for node in ast.walk(holder)
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    assert deferred == [], (
        "these imports are first evaluated when their function runs, which for this "
        f"tool can be after a run's entire compute budget is spent: {deferred}"
    )


def test_the_names_the_late_imports_provided_are_module_attributes() -> None:
    """The complement to the syntax check: the names are actually there now."""
    assert isinstance(derive.GATE_CONFIG_VERSION, int)
    assert callable(derive.cross_algorithm_statistic)


def _run_isolated_tool(
    tmp_path, panel: str, pythonpath: str, *, where: str
) -> subprocess.CompletedProcess:
    """The tool run as its own process from a directory with no ``src`` beside it.

    Copied rather than invoked in place, so ``REPO_ROOT / "src"`` -- the path the tool
    puts on ``sys.path`` at import time -- does not exist. That is the state the real
    incident was in: the checkout the run was launched from had been deleted, so the
    only thing deciding whether the package could be imported was ``PYTHONPATH``.
    """
    binary_dir = tmp_path / where / "bin"
    binary_dir.mkdir(parents=True)
    copied = binary_dir / "derive.py"
    shutil.copy(TOOL_PATH, copied)
    assert not (tmp_path / where / "src").exists()

    environment = dict(os.environ)
    environment["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [
            sys.executable,
            str(copied),
            panel,
            "--k-grid",
            "2",
            "--replicates",
            "20",
            "--seeds",
            str(SEEDS),
            "--jobs",
            "1",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=300,
    )


def test_a_run_that_cannot_import_the_package_stops_before_the_compute(
    tmp_path, correlated: np.ndarray
) -> None:
    """Demonstrated by breaking the import, not inferred from where the line sits.

    "The import is at the top of the file" is a claim about text. "The run refuses
    before spending the compute" is a claim about behaviour, and only the second one
    is what this ticket was about, so the second one is what is measured: the same
    copied script, the same panel and the same argv, run twice, differing only in
    whether ``engagement_kernel`` can be imported.

    The control matters as much as the case. Without it, a tool that failed at
    startup for some unrelated reason -- a typo, a missing flag -- would satisfy the
    broken half and prove nothing.
    """
    panel = _write_panel(tmp_path / "panel.csv", correlated)

    shadow = tmp_path / "shadow"
    (shadow / "engagement_kernel").mkdir(parents=True)
    (shadow / "engagement_kernel" / "__init__.py").write_text(
        'raise ImportError("simulated: the package tree this run was launched from is gone")\n'
    )
    broken = _run_isolated_tool(tmp_path, panel, str(shadow), where="unimportable")

    assert broken.returncode != 0
    # First the property that matters: it stops before the first cell is computed.
    # Asserted ahead of the message, so that restoring the deferred imports is caught
    # here rather than on the wording of a refusal that never had to be reached.
    assert "deriving:" not in broken.stdout, broken.stdout
    assert "derived bars" not in broken.stdout, broken.stdout
    assert "engagement_kernel is not importable" in broken.stderr
    # It names the cause it was given rather than only its own summary of it.
    assert "the package tree this run was launched from is gone" in broken.stderr

    working = _run_isolated_tool(tmp_path, panel, str(REPO_SRC), where="importable")

    assert working.returncode == 0, working.stderr
    assert "deriving:" in working.stdout
    assert "derived bars" in working.stdout


def _derive_with_out(tmp_path, correlated: np.ndarray) -> tuple[str, Path]:
    return _write_panel(tmp_path / "panel.csv", correlated), tmp_path / "out"


def test_a_failing_control_still_leaves_what_the_run_measured(
    tmp_path, monkeypatch, correlated: np.ndarray
) -> None:
    """A refusal to publish a bar is not a reason to discard the distributions.

    The bar is withheld because it is uncertified. The null distributions behind it
    are the same measurements either way, and they cost the whole run.
    """
    monkeypatch.setattr(derive, "POSITIVE_CONTROL_ARI", 1.5)
    panel, out = _derive_with_out(tmp_path, correlated)
    code = derive.main(
        [panel, "--k-grid", "3", "--replicates", "20", "--seeds", str(SEEDS), "--out", str(out)]
    )

    assert code == 4
    assert not (out / derive.BARS_NAME).exists()

    evidence = json.loads((out / derive.EVIDENCE_NAME).read_text())
    assert evidence["controls"]["status"] == "failed"
    assert evidence["controls"]["positive"]["passed"] is False
    # The measurements, not just the verdict.
    assert set(evidence["null_distributions"]) == set(derive.NULLS)
    assert evidence["bar_by_k"]["3"] > 0.0


def test_a_run_that_dies_in_the_controls_still_leaves_what_it_measured(
    tmp_path, monkeypatch, correlated: np.ndarray
) -> None:
    """The ordering, not just the failure path.

    A control that *returns* a failure can be handled after the fact; a run that
    dies, or is killed, cannot. So the evidence has to be on disk before the controls
    are entered, and the only way to show it is there at that moment is to never let
    the controls finish. ``controls.status`` marks the file as uncertified, so what a
    crashed run leaves cannot be read as a derivation that passed.
    """

    def killed(*args, **kwargs):
        raise RuntimeError("killed partway through the controls")

    monkeypatch.setattr(derive, "positive_control", killed)
    panel, out = _derive_with_out(tmp_path, correlated)
    with pytest.raises(RuntimeError, match="killed partway"):
        derive.main(
            [panel, "--k-grid", "3", "--replicates", "20", "--seeds", str(SEEDS), "--out", str(out)]
        )

    evidence = json.loads((out / derive.EVIDENCE_NAME).read_text())
    assert evidence["controls"] == {"status": derive.CONTROLS_NOT_RUN}
    assert set(evidence["null_distributions"]) == set(derive.NULLS)
    assert not (out / derive.BARS_NAME).exists()


def test_a_run_without_out_writes_nothing(tmp_path, correlated: np.ndarray) -> None:
    """The pre-write is bounded by ``--out``, so a look-only run stays look-only."""
    panel = _write_panel(tmp_path / "panel.csv", correlated)
    before = sorted(child.name for child in tmp_path.iterdir())
    code = derive.main([panel, "--k-grid", "2", "--replicates", "20", "--seeds", str(SEEDS)])

    assert code == 0
    assert sorted(child.name for child in tmp_path.iterdir()) == before
