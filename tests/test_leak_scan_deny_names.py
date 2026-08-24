"""The deny-name rule must not be able to go inert behind a green check again.

This is the regression suite for a gate that was wired up, documented, tested and
**doing nothing** for months. Four of the leak scanner's five rules worked; the
fifth -- the one aimed at the likeliest real leak, a maintainer copying code across
from the private tree and bringing an employee name with it -- compiled zero
patterns, matched nothing, and reported success every time.

The thing that made it survive is worth stating, because it is what these tests are
shaped around: **a clean scan and an inert rule produce the same exit code.** Both
are 0. No amount of checking the scanner's result could tell them apart.

So the controls here are in two halves.

*The rule fires when it has terms* (:func:`test_the_rule_fires_on_a_denied_term`)
-- the positive control. Without it, every test below would pass against a scanner
that refused everything or scanned nothing.

*The scan refuses to report a verdict when it has none*
(:func:`test_every_empty_deny_shape_is_refused`) -- and that one is parametrised
over the four shapes an unset or fumbled secret actually produces, because three of
them do **not** raise on their own. A missing file does. An empty file, a bare
``[deny]`` table and ``names = []`` all load silently with zero names, which is
precisely how an empty Actions secret reproduces the original bug while the job
stays green.

No real name appears anywhere in this file, and none may be added. The terms below
are invented, and they work as controls for exactly the same reason a real one
would: the rule matches terms it was given, and it does not care where they came
from.
"""

from __future__ import annotations

from pathlib import Path

import leak_scan
import pytest

#: Invented, and deliberately implausible as a real person. A control term that
#: could collide with a genuine name would make a failure ambiguous.
INVENTED_NAME = "Zzyzx Quibblewort"

#: The four ways a deny file can arrive carrying no names. Only the first raises on
#: its own; the other three are the silent ones, and they are why the term-count
#: assertion exists at all.
EMPTY_DENY_SHAPES: tuple[tuple[str, str | None], ...] = (
    ("missing", None),
    ("empty file", ""),
    ("bare table", "[deny]\n"),
    ("explicitly empty list", "[deny]\nnames = []\n"),
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A committed-config stand-in: hostnames, and no names -- as the real one is."""
    path = tmp_path / "leak_scan.toml"
    path.write_text('[deny]\nhostnames = ["example.invalid"]\nnames = []\n')
    return path


def _deny_file(tmp_path: Path, body: str | None) -> Path:
    path = tmp_path / "deny.toml"
    if body is not None:
        path.write_text(body)
    return path


def test_a_populated_deny_file_loads_its_names(config_path: Path, tmp_path: Path) -> None:
    """The positive control for loading. Everything else is meaningless without it."""
    deny = _deny_file(tmp_path, f'[deny]\nnames = ["{INVENTED_NAME}"]\n')
    config = leak_scan.load_config(config_path, deny)
    assert config.names == (INVENTED_NAME,)
    leak_scan.assert_deny_terms_loaded(config, 1)


@pytest.mark.parametrize(("label", "body"), EMPTY_DENY_SHAPES[1:], ids=lambda v: str(v)[:24])
def test_the_silent_shapes_really_are_silent(
    config_path: Path, tmp_path: Path, label: str, body: str
) -> None:
    """The premise of this whole file, asserted rather than assumed.

    If these ever started raising on their own, the term-count assertion would be
    redundant -- and a reader deleting it as redundant would be right. They do not,
    so it is not.
    """
    config = leak_scan.load_config(config_path, _deny_file(tmp_path, body))
    assert config.names == (), f"{label} unexpectedly carried name terms"


@pytest.mark.parametrize(("label", "body"), EMPTY_DENY_SHAPES, ids=lambda v: str(v)[:24])
def test_every_empty_deny_shape_is_refused(
    config_path: Path, tmp_path: Path, label: str, body: str | None
) -> None:
    """None of the four may reach a verdict when names are required."""
    deny = _deny_file(tmp_path, body)
    with pytest.raises(leak_scan.ScanError) as exc:
        config = leak_scan.load_config(config_path, deny)
        leak_scan.assert_deny_terms_loaded(config, 1)
    message = str(exc.value)
    assert "not found" in message or "0 name term(s)" in message, (
        f"{label} failed for an unexpected reason: {message}"
    )


def test_the_refusal_names_inertness_rather_than_a_clean_tree(
    config_path: Path, tmp_path: Path
) -> None:
    """The message has to say what is wrong, because the alternative reads as success.

    A failure saying only "scan failed" would send whoever hit it looking for a leak
    in the tree. The tree is fine. The gate was not.
    """
    config = leak_scan.load_config(config_path, _deny_file(tmp_path, "[deny]\n"))
    with pytest.raises(leak_scan.ScanError) as exc:
        leak_scan.assert_deny_terms_loaded(config, 1)
    message = str(exc.value)
    assert "NOT a clean tree" in message
    assert leak_scan.RULE_DENY_NAME in message
    assert leak_scan.DENY_FILE_ENV_VAR in message


def test_the_assertion_is_off_by_default(config_path: Path, tmp_path: Path) -> None:
    """A local run without the out-of-tree file must still work.

    The requirement belongs to CI, where an inert rule is a merge gate lying. On a
    laptop it would just stop anyone scanning at all.
    """
    config = leak_scan.load_config(config_path, _deny_file(tmp_path, "[deny]\n"))
    leak_scan.assert_deny_terms_loaded(config, 0)


def test_the_rule_fires_on_a_denied_term(config_path: Path, tmp_path: Path) -> None:
    """The positive control for the *rule*, not just the loading.

    Without this, a scanner that had stopped matching anything at all would pass
    every other test here.
    """
    root = tmp_path / "tree"
    root.mkdir()
    offender = root / "notes.md"
    offender.write_text(f"Reviewed by {INVENTED_NAME}.\n")

    deny = _deny_file(tmp_path, f'[deny]\nnames = ["{INVENTED_NAME}"]\n')
    config = leak_scan.load_config(config_path, deny)
    findings, _notes, scanned = leak_scan.scan_paths(root, ["notes.md"], config)

    assert scanned == 1
    assert [finding.rule for finding in findings] == [leak_scan.RULE_DENY_NAME]
    assert findings[0].relpath == "notes.md"
    assert findings[0].line == 1


def test_the_same_file_is_invisible_without_the_term(config_path: Path, tmp_path: Path) -> None:
    """The discrimination proof: identical tree, empty deny list, clean verdict.

    This is the state CI was in. It is what "green" meant.
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "notes.md").write_text(f"Reviewed by {INVENTED_NAME}.\n")

    config = leak_scan.load_config(config_path, _deny_file(tmp_path, "[deny]\nnames = []\n"))
    findings, _notes, _scanned = leak_scan.scan_paths(root, ["notes.md"], config)
    assert findings == [], "the rule matched with no terms loaded, which cannot happen"


def test_a_finding_never_carries_the_matched_value(config_path: Path, tmp_path: Path) -> None:
    """Firing this rule in a public build log must not publish the name.

    Already true by design -- ``Finding.__str__`` prints rule, path and line -- and
    asserted because the whole point of making the rule fire is that its output ends
    up somewhere readable.
    """
    root = tmp_path / "tree"
    root.mkdir()
    (root / "notes.md").write_text(f"Reviewed by {INVENTED_NAME}.\n")
    config = leak_scan.load_config(
        config_path, _deny_file(tmp_path, f'[deny]\nnames = ["{INVENTED_NAME}"]\n')
    )
    findings, _notes, _scanned = leak_scan.scan_paths(root, ["notes.md"], config)
    rendered = str(findings[0])
    assert INVENTED_NAME not in rendered
    assert "notes.md" in rendered


def test_the_committed_config_still_carries_no_names() -> None:
    """The design must not be 'fixed' by putting names in the tree.

    Names are the confidential data; hashes of them are no better, because human
    names are low-entropy and enumerable. If this fails, the fix was the wrong one.
    """
    root = Path(__file__).resolve().parents[1]
    committed = leak_scan.load_config(root / leak_scan.DEFAULT_CONFIG_RELPATH, None)
    assert committed.names == ()
    text = (root / leak_scan.DEFAULT_CONFIG_RELPATH).read_text()
    assert "names = []" in text


def test_the_loaded_counts_are_reported(config_path: Path, tmp_path: Path) -> None:
    """Every run says how much deny material it had.

    A gate reporting only its verdict cannot be used to tell a strong configuration
    from a weak one -- which is the condition that let this defect live.
    """
    config = leak_scan.load_config(
        config_path, _deny_file(tmp_path, f'[deny]\nnames = ["{INVENTED_NAME}"]\n')
    )
    described = leak_scan.describe_deny_terms(config)
    assert "1 name term(s)" in described
    assert "hostname term(s)" in described
    assert INVENTED_NAME not in described
