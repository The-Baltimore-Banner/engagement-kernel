"""Hold every commit message to the internal-identifier rules.

WHY THIS EXISTS. ``tools/leak_scan.py`` enumerates the tree with
``git ls-files --cached`` (see ``enumerate_paths``), so it scans **file content
at HEAD**. It has never seen a commit message. Meanwhile this repository's own
convention deliberately routes ticket references into commit messages and pull
request descriptions -- so the one place internal identifiers are *expected* to
accumulate is the one place the gate did not look.

That asymmetry only matters because this repository is destined to be public.
Publishing exposes every commit and every message. A name or an internal
hostname in a message survives publication and cannot be removed without
rewriting history, so it has to be caught while the message can still be
amended: at write time, in CI, on the branch.

WHAT IS AND IS NOT REPORTED. ``internal-ticket`` is **waived** here. BBA1- keys
in this repository's history are an accepted decision (news-detector-meta
``status/decision-log.md`` DEC-155, 2026-08-24): a bare key discloses little on
its own, and rewriting history to remove them costs more than it buys. The
waiver is scoped to commit messages -- the tree scan still refuses a ticket key
in a file, which is a different and unsettled question. Every other rule
applies in full.

HOW THIS AVOIDS BEING VACUOUS. An audit that reports "nothing found" is
worthless unless something *could* have been found. Two independent guards:

1. A synthetic sentinel document, scanned as its own file, must trip every
   pattern rule. If the sentinel comes back clean the scanner is not reading
   what it is handed, and this exits 2 rather than reporting a clean history.
   The sentinel is synthetic on purpose -- deriving the control from real
   history would make it fail the day a legitimate commit stops matching, which
   trains people to delete controls.
2. ``deny-name`` has no pattern to sentinel-test, because its terms are
   confidential and live outside the tree. Its liveness is asserted instead by
   ``--require-deny-names``, which fails on an empty term list rather than
   reporting a clean scan. That split is deliberate: a pattern rule proves
   itself by firing, a data-driven rule proves itself by having data.

Exit codes match ``leak_scan.py``: 0 clean, 1 findings, 2 the verdict could not
be trusted.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from leak_scan import (  # noqa: E402
    RULE_INTERNAL_TICKET,
    Config,
    Finding,
    ScanError,
    assert_deny_terms_loaded,
    describe_deny_terms,
    load_config,
    scan_paths,
)

# Waived for commit messages only. See the module docstring.
WAIVED_RULES = frozenset({RULE_INTERNAL_TICKET})

SENTINEL_NAME = "sentinel.txt"
DUMP_NAME = "commit-messages.txt"

# Assembled from fragments so this file does not trip the tree scan that runs
# over it -- the same technique leak_scan.py uses on its own patterns.
_SENTINEL_TICKET = "BBA1" + "-9999"
_SENTINEL_ACCOUNT = "9" * 12
_SENTINEL_ARN = "arn:" + "aws:iam::" + _SENTINEL_ACCOUNT + ":role/sentinel"
_SENTINEL_HOSTNAME = "sentinel." + "amazonaws" + ".com"

SENTINEL_TEXT = "\n".join(
    (
        "Synthetic control document. Every line below is invented and matches no",
        "real resource. If the scan does not flag these, it is not reading input.",
        f"ticket {_SENTINEL_TICKET}",
        f"account {_SENTINEL_ACCOUNT}",
        f"arn {_SENTINEL_ARN}",
        f"host {_SENTINEL_HOSTNAME}",
        "",
    )
)

# Rules the sentinel is built to trip. deny-name is absent on purpose: its terms
# are confidential, so there is nothing to put in a committed control document.
SENTINEL_RULES = ("internal-ticket", "aws-account", "aws-arn", "deny-hostname")


def dump_commit_messages(root: Path, dest: Path) -> int:
    """Write every commit message on every ref to ``dest``; return the count."""
    fmt = "%n=== %H (%an, %ad) ===%n%s%n%b"
    try:
        proc = subprocess.run(
            ["git", "log", "--all", "--format=" + fmt, "--date=short"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        count = subprocess.run(
            ["git", "rev-list", "--all", "--count"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is always present in CI
        raise ScanError("git executable not found; cannot read commit messages") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise ScanError(f"git log failed: {detail}") from exc
    dest.write_bytes(proc.stdout)
    return int(count.stdout.strip() or 0)


def run_sentinel(workdir: Path, config: Config) -> list[str]:
    """Return the sentinel rules that failed to fire (empty means healthy)."""
    (workdir / SENTINEL_NAME).write_text(SENTINEL_TEXT, encoding="utf-8")
    findings, _notes, _scanned = scan_paths(workdir, [SENTINEL_NAME], config)
    fired = {finding.rule for finding in findings}
    return [rule for rule in SENTINEL_RULES if rule not in fired]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on internal identifiers in commit messages.")
    parser.add_argument("--root", help="repository root (default: cwd)")
    parser.add_argument("--config", help="leak-scan config (default: <root>/tools/leak_scan.toml)")
    parser.add_argument(
        "--deny-file",
        help="out-of-tree deny list; defaults to $LEAK_SCAN_DENY_FILE when set",
    )
    parser.add_argument(
        "--require-deny-names",
        type=int,
        default=0,
        help=(
            "refuse to report a verdict unless the deny list loaded at least N "
            "name terms. CI passes 1: there the alternative is a green job with "
            "deny-name silently inert"
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd()
    config_path = Path(args.config) if args.config else root / "tools" / "leak_scan.toml"
    deny_raw = args.deny_file or os.environ.get("LEAK_SCAN_DENY_FILE")
    deny_file = Path(deny_raw) if deny_raw else None

    try:
        config = load_config(config_path, deny_file)
        assert_deny_terms_loaded(config, args.require_deny_names)
    except ScanError as exc:
        print(f"commit-message-scan: ERROR {exc}", file=sys.stderr)
        print(
            "commit-message-scan: the scan did not complete; treat this as a failure.",
            file=sys.stderr,
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="commit-msg-scan-") as tmp:
        workdir = Path(tmp)

        missing = run_sentinel(workdir, config)
        if missing:
            print(
                "commit-message-scan: ERROR the synthetic control did not fire for: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            print(
                "commit-message-scan: the scanner is not reading what it is handed, so a "
                "clean history verdict would be meaningless. Treat this as a failure.",
                file=sys.stderr,
            )
            return 2

        try:
            commits = dump_commit_messages(root, workdir / DUMP_NAME)
        except ScanError as exc:
            print(f"commit-message-scan: ERROR {exc}", file=sys.stderr)
            return 2

        findings, _notes, scanned = scan_paths(workdir, [DUMP_NAME], config)

    reportable = [f for f in findings if f.rule not in WAIVED_RULES]
    waived = [f for f in findings if f.rule in WAIVED_RULES]

    if scanned == 0:
        print("commit-message-scan: ERROR the dump was not scanned.", file=sys.stderr)
        return 2

    print(f"commit-message-scan: control OK, all {len(SENTINEL_RULES)} pattern rules fired")
    print(f"commit-message-scan: {commits} commit message(s) on all refs")
    print(f"commit-message-scan: {describe_deny_terms(config)}")
    if waived:
        print(
            f"commit-message-scan: {len(waived)} internal-ticket match(es) waived "
            "-- ticket keys in history are an accepted decision (DEC-155)"
        )

    if reportable:
        for finding in _stable(reportable):
            print(f"commit-message-scan: FAIL {finding.rule} commit message line {finding.line}")
        print(
            f"commit-message-scan: {len(reportable)} finding(s). These are in git history "
            "and WILL be public. Amend the commit now -- after publication the only fix "
            "is a history rewrite.",
            file=sys.stderr,
        )
        print(
            "commit-message-scan: matched values are not printed on purpose; "
            "run `git log --all` and read the line above.",
            file=sys.stderr,
        )
        return 1

    print("commit-message-scan: OK no internal identifiers in any commit message")
    return 0


def _stable(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.line, f.rule))


if __name__ == "__main__":
    raise SystemExit(main())
