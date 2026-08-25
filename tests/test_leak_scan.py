"""Tests for tools/leak_scan.py.

Two rules shape this file.

First, every test that claims a rule fires runs the **committed entrypoint** as a
subprocess against a real file in a real git tree, and checks the exit status and
the reported rule id. Asserting against a regex re-typed inside the test would
prove only that the test can write a regex.

Second, none of the strings under test appears here contiguously. They are
assembled from fragments, because this file is itself scanned. A test module full
of literal violations would have to be allowlisted, and an allowlisted test
module is a place to hide a real one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import leak_scan

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "tools" / "leak_scan.py"
CONFIG = REPO_ROOT / "tools" / "leak_scan.toml"
FIXTURE = "tests/fixtures/leak_scan/intentional_violations.txt"

# Invented values, assembled from fragments. None of these identifies anything.
FAKE_ACCOUNT = "0" * 6 + "1" * 6
FAKE_ARN = "arn" + ":" + "aws" + ":s3:::not-a-real-bucket"
FAKE_TICKET = "BBA" + "1" + "-4242"
DENIED_HOSTNAME = "amazonaws" + ".com"
FAKE_NAME = "Zzyzx Quibblesworth"

# 40 hex characters with twelve consecutive digits inside, which is what a
# SHA-pinned GitHub Action looks like and must not be reported. Split across
# fragments for the same reason as the values above: written as one literal it
# makes the scan flag this very file, which is how the rule first proved itself.
SHA_LIKE = "abc" + "123456" + "789012" + "d" + "e" * 24


def _run(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _tmp_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    for relpath, text in files.items():
        target = tmp_path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _scan_tmp(tmp_path: Path, files: dict[str, str], extra: list[str] | None = None):
    root = _tmp_repo(tmp_path, files)
    args = ["--root", str(root), "--config", str(CONFIG), *(extra or [])]
    return _run(args)


# --- the committed tree ------------------------------------------------------


def test_clean_tree_passes() -> None:
    """The exact command CI runs, with no arguments, against this repository."""
    result = _run([])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "leak-scan: OK" in result.stdout


# --- each rule fires --------------------------------------------------------


def test_account_id_is_detected(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {"notes.md": f"account {FAKE_ACCOUNT} here\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL aws-account notes.md:1" in result.stdout


def test_arn_is_detected(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {"infra.tf": f'bucket = "{FAKE_ARN}"\n'})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL aws-arn infra.tf:1" in result.stdout


def test_ticket_key_is_detected(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {"doc.md": f"first line\nsee {FAKE_TICKET}\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL internal-ticket doc.md:2" in result.stdout


def test_ticket_key_is_detected_underscored(tmp_path: Path) -> None:
    """The form that used to pass a clean scan with the identifier present.

    The rule required a hyphen, so an underscored key -- what a key becomes in a
    filename, and filenames get pasted into prose -- matched nothing. A real
    document reached a review carrying one behind a green gate.
    """
    underscored = FAKE_TICKET.replace("-", "_")
    result = _scan_tmp(tmp_path, {"doc.md": f"see {underscored} for the finding\n"})
    assert result.returncode == 1
    assert "FAIL internal-ticket doc.md:1" in result.stdout


def test_a_ticket_key_with_no_separator_is_not_matched(tmp_path: Path) -> None:
    """The widening is one character, not a licence to match any digits after the prefix.

    Recorded because the obvious next step -- making the separator optional -- would
    flag a bare prefix followed by any number, and this repository has no way to
    tell that from an ordinary token.
    """
    joined = FAKE_TICKET.replace("-", "")
    result = _scan_tmp(tmp_path, {"doc.md": f"see {joined}\n"})
    assert result.returncode == 0


def test_ticket_key_is_detected_lowercase(tmp_path: Path) -> None:
    """Branch names carry the key in lowercase; a lowercase key leaks the same."""
    result = _scan_tmp(tmp_path, {"doc.md": f"branch feat/{FAKE_TICKET.lower()}-thing\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL internal-ticket doc.md:1" in result.stdout


def test_denied_hostname_from_committed_config_is_detected(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {"conf.yaml": f"host: db.eu-west-1.rds.{DENIED_HOSTNAME}\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL deny-hostname conf.yaml:1" in result.stdout


def test_identifier_in_a_filename_is_detected(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {f"export-{FAKE_ACCOUNT}.csv": "nothing sensitive inside\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FAIL aws-account export-{FAKE_ACCOUNT}.csv" in result.stdout


# --- employee names come from outside the tree -------------------------------


def test_name_from_out_of_tree_deny_file_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    deny_file = tmp_path / "private-deny.toml"
    deny_file.write_text(f'[deny]\nnames = ["{FAKE_NAME}"]\n', encoding="utf-8")
    _tmp_repo(repo, {"survey.csv": f"respondent,{FAKE_NAME}\n"})
    result = _run(
        [
            "--root",
            str(repo),
            "--config",
            str(CONFIG),
            "--deny-file",
            str(deny_file),
        ]
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL deny-name survey.csv:1" in result.stdout


def test_deny_terms_do_not_match_inside_a_longer_word(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    deny_file = tmp_path / "private-deny.toml"
    deny_file.write_text('[deny]\nnames = ["Mark"]\n', encoding="utf-8")
    _tmp_repo(repo, {"README.md": "Marketing and benchmarking are unaffected.\n"})
    result = _run(["--root", str(repo), "--config", str(CONFIG), "--deny-file", str(deny_file)])
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_deny_file_is_an_error_not_a_clean_pass(tmp_path: Path) -> None:
    """A named-but-absent deny list must not silently downgrade the policy."""
    repo = _tmp_repo(tmp_path, {"README.md": "clean\n"})
    result = _run(
        [
            "--root",
            str(repo),
            "--config",
            str(CONFIG),
            "--deny-file",
            str(tmp_path / "does-not-exist.toml"),
        ]
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "leak-scan: ERROR" in result.stderr


# --- the boundary choice is deliberate --------------------------------------


def test_sha_pinned_action_is_not_reported_as_an_account_id(tmp_path: Path) -> None:
    result = _scan_tmp(tmp_path, {"ci.yml": f"uses: actions/checkout@{SHA_LIKE}\n"})
    assert result.returncode == 0, result.stdout + result.stderr


# --- reporting hygiene ------------------------------------------------------


def test_output_never_echoes_the_matched_value(tmp_path: Path) -> None:
    """CI logs for this repository will be public."""
    result = _scan_tmp(
        tmp_path,
        {"notes.md": f"{FAKE_ACCOUNT}\n{FAKE_ARN}\n{FAKE_TICKET}\n"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert FAKE_ACCOUNT not in combined
    assert FAKE_ARN not in combined
    assert FAKE_TICKET not in combined


# --- the allowlist is real, and narrow --------------------------------------


def test_fixture_allowlist_is_not_vacuous() -> None:
    """The allowlisted fixture must really contain findings, or it proves nothing."""
    allowlisted = _run(["--paths", FIXTURE])
    assert allowlisted.returncode == 0, allowlisted.stdout + allowlisted.stderr

    forced = _run(["--ignore-allowlist", "--paths", FIXTURE])
    assert forced.returncode == 1, forced.stdout + forced.stderr
    assert "FAIL aws-account" in forced.stdout
    assert "FAIL aws-arn" in forced.stdout


def test_config_file_waiver_covers_only_the_deny_rules() -> None:
    config = leak_scan.load_config(CONFIG, None)
    relpath = "tools/leak_scan.toml"
    assert leak_scan.is_allowlisted(relpath, leak_scan.RULE_DENY_HOSTNAME, config)
    assert leak_scan.is_allowlisted(relpath, leak_scan.RULE_DENY_NAME, config)
    for rule in (
        leak_scan.RULE_AWS_ACCOUNT,
        leak_scan.RULE_AWS_ARN,
        leak_scan.RULE_INTERNAL_TICKET,
    ):
        assert not leak_scan.is_allowlisted(relpath, rule, config)


def test_allowlist_does_not_leak_to_neighbouring_paths() -> None:
    config = leak_scan.load_config(CONFIG, None)
    assert not leak_scan.is_allowlisted(
        "tests/test_leak_scan.py", leak_scan.RULE_AWS_ACCOUNT, config
    )
    assert not leak_scan.is_allowlisted("tools/leak_scan.py", leak_scan.RULE_DENY_HOSTNAME, config)


def test_committed_config_ships_no_employee_names() -> None:
    """The names are the sensitive data; they belong in an out-of-tree file."""
    config = leak_scan.load_config(CONFIG, None)
    assert config.names == ()
    assert config.hostnames, "the committed deny list should not be empty"


def test_broken_config_is_an_error_not_a_clean_pass(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[deny]\nhostnames = "not-a-list"\n', encoding="utf-8")
    repo = _tmp_repo(tmp_path / "repo", {"README.md": "clean\n"})
    result = _run(["--root", str(repo), "--config", str(bad)])
    assert result.returncode == 2, result.stdout + result.stderr
