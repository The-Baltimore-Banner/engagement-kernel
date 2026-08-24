"""Negative controls for the commit-message scan.

Every test here makes the gate FAIL on purpose. A gate that has only ever been
observed passing is indistinguishable from a gate that cannot fail, and this
one guards something unrecoverable: once a commit is published, a name in its
message can only be removed by rewriting history.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import scan_commit_messages as cms  # noqa: E402
from leak_scan import load_config  # noqa: E402

CONFIG = TOOLS / "leak_scan.toml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """A throwaway repo, so no test can touch the real history."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("hello\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "a clean subject line")
    return repo


def _run(repo: Path, *extra: str) -> int:
    return cms.main(["--root", str(repo), "--config", str(CONFIG), *extra])


def test_clean_history_passes(scratch_repo: Path) -> None:
    assert _run(scratch_repo) == 0


@pytest.mark.parametrize(
    "message",
    [
        "fix the search host sentinel.amazonaws.com",
        "see https://example.atlassian.net/browse/X for context",
        "deployed to box.internal overnight",
        "role arn:aws:iam::123456789012:role/thing",
        "account 123456789012 was wrong",
    ],
)
def test_a_violation_in_a_commit_message_is_caught(scratch_repo: Path, message: str) -> None:
    """The whole point: these are invisible to the HEAD-only tree scan."""
    _git(scratch_repo, "commit", "-q", "--allow-empty", "-m", message)
    assert _run(scratch_repo) == 1


def test_a_ticket_key_in_a_message_is_waived_not_reported(scratch_repo: Path) -> None:
    """DEC-155: accepted decision. Waived here, still refused in the tree scan."""
    _git(scratch_repo, "commit", "-q", "--allow-empty", "-m", "feat(BBA1-1234): a thing")
    assert _run(scratch_repo) == 0


def test_a_violation_in_a_message_body_is_caught(scratch_repo: Path) -> None:
    """Bodies matter as much as subjects, and are where prose accumulates."""
    _git(
        scratch_repo,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "an innocuous subject",
        "-m",
        "but the body mentions sentinel.amazonaws.com",
    )
    assert _run(scratch_repo) == 1


def test_a_violation_on_an_unmerged_branch_is_caught(scratch_repo: Path) -> None:
    """`git log --all`: a bad message is exposed by publication even unmerged."""
    _git(scratch_repo, "checkout", "-q", "-b", "side")
    _git(scratch_repo, "commit", "-q", "--allow-empty", "-m", "host sentinel.amazonaws.com")
    _git(scratch_repo, "checkout", "-q", "main")
    assert _run(scratch_repo) == 1


def test_missing_deny_file_refuses_rather_than_reporting_clean(
    scratch_repo: Path, tmp_path: Path
) -> None:
    assert _run(scratch_repo, "--deny-file", str(tmp_path / "nope.toml")) == 2


def test_require_deny_names_refuses_when_no_names_are_loaded(scratch_repo: Path) -> None:
    """The in-tree config holds no names on purpose, so this must refuse."""
    assert _run(scratch_repo, "--require-deny-names", "1") == 2


def test_deny_name_fires_when_terms_are_supplied(scratch_repo: Path, tmp_path: Path) -> None:
    """deny-name cannot be sentinel-tested, so prove it works with a fake term."""
    deny = tmp_path / "deny.toml"
    deny.write_text('[deny]\nnames = ["Zaphod Beeblebrox"]\nhostnames = []\n')
    _git(scratch_repo, "commit", "-q", "--allow-empty", "-m", "thanks Zaphod Beeblebrox")
    assert _run(scratch_repo, "--deny-file", str(deny), "--require-deny-names", "1") == 1


def test_sentinel_failure_refuses_rather_than_reporting_clean(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the control cannot fire, a clean verdict must NOT be reported.

    This is the control on the control. Neutering the sentinel simulates a
    scanner that reads nothing -- exactly the failure an empty audit result
    cannot distinguish from success.
    """
    monkeypatch.setattr(cms, "SENTINEL_TEXT", "nothing interesting here\n")
    assert _run(scratch_repo) == 2


def test_sentinel_trips_every_rule_it_claims_to(tmp_path: Path) -> None:
    config = load_config(CONFIG, None)
    assert cms.run_sentinel(tmp_path, config) == []


def test_internal_ticket_is_the_only_waived_rule() -> None:
    """A widened waiver is how this gate would quietly stop protecting anything."""
    assert cms.WAIVED_RULES == frozenset({"internal-ticket"})
