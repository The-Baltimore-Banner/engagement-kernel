#!/usr/bin/env python3
"""Fail the build when internal identifiers appear in the tree.

This repository is the public, cloud-agnostic extraction of a private internal
repository. Content is copied in deliberately, file by file, and the private
source is full of things that must never be published: a production cloud
account id, cloud resource identifiers, internal issue-tracker keys, internal
hostnames, and employee names. Making a public repository private again does not
un-index what has already been crawled, so the check has to run before content
lands, not after.

Design notes worth knowing before you change this file:

* It is stdlib-only and takes no install step, so it can run as the first job in
  CI and locally with the same command.
* It scans **bytes**, not decoded text, so binary files, unusual encodings and
  files without a known extension are all covered rather than silently skipped.
* It scans the relative path as well as the contents: an identifier can be in a
  filename.
* It scans **itself**. None of the strings this file searches for appears in it
  contiguously -- each is assembled from fragments at import time -- so the
  scanner needs no self-exemption and therefore opens no blind spot.
* Findings are reported as ``rule path:line`` and never echo the matched value.
  CI logs for this repository will be public; a gate that prints the secret it
  found is a leak of its own.
* Exit status is meaningful: ``0`` clean, ``1`` findings, ``2`` the scan itself
  could not be trusted (bad config, unreadable tree, git failure). A negative
  control that exits ``2`` has not proven detection works -- it has proven the
  scanner is broken.

Usage::

    python3 tools/leak_scan.py                     # what CI runs
    python3 tools/leak_scan.py --paths README.md   # one or more paths
    python3 tools/leak_scan.py --ignore-allowlist  # diagnostic only
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_RELPATH = "tools/leak_scan.toml"
DENY_FILE_ENV_VAR = "LEAK_SCAN_DENY_FILE"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REPORTED_FINDINGS = 200

# Rule ids. These are the only strings printed for a finding, so they have to be
# self-explanatory on their own.
RULE_AWS_ACCOUNT = "aws-account"
RULE_AWS_ARN = "aws-arn"
RULE_INTERNAL_TICKET = "internal-ticket"
RULE_DENY_HOSTNAME = "deny-hostname"
RULE_DENY_NAME = "deny-name"

ALL_RULES = (
    RULE_AWS_ACCOUNT,
    RULE_AWS_ARN,
    RULE_INTERNAL_TICKET,
    RULE_DENY_HOSTNAME,
    RULE_DENY_NAME,
)

# Assembled from fragments so this file does not trip its own rules. Do not
# "tidy" these into single literals -- that would force an allowlist entry for
# the scanner, which is exactly the blind spot the design avoids.
_ARN_MARKER = "arn" + ":" + "aws"
_TICKET_PREFIX = "BBA" + "1"

# A 12-digit run bounded by non-alphanumerics. The boundaries are deliberately
# alphanumeric rather than digit-only: a 40-character hex commit SHA (every
# SHA-pinned GitHub Action is one) regularly contains twelve consecutive decimal
# characters, and a digit-only boundary flags every one of them. The tradeoff is
# that an account id glued to a letter, as in "acct123456789012x", is missed.
_ACCOUNT_RE = re.compile(rb"(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])")
_ARN_RE = re.compile(re.escape(_ARN_MARKER).encode(), re.IGNORECASE)
_TICKET_RE = re.compile(
    rb"(?<![A-Za-z0-9])" + _TICKET_PREFIX.encode() + rb"-[0-9]+(?![0-9])",
    re.IGNORECASE,
)


class ScanError(Exception):
    """The scan could not be completed and its result must not be trusted."""


@dataclass(frozen=True)
class AllowlistEntry:
    """One allowlist stanza: which paths, and which rules are waived there."""

    patterns: tuple[re.Pattern[str], ...]
    rules: frozenset[str]
    description: str

    def waives(self, relpath: str, rule: str) -> bool:
        if "*" not in self.rules and rule not in self.rules:
            return False
        return any(pattern.search(relpath) for pattern in self.patterns)


@dataclass
class Config:
    hostnames: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    allowlist: tuple[AllowlistEntry, ...] = ()
    sources: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Finding:
    rule: str
    relpath: str
    line: int

    def __str__(self) -> str:
        where = self.relpath if self.line == 0 else f"{self.relpath}:{self.line}"
        return f"{self.rule} {where}"


def _term_pattern(term: str) -> re.Pattern[bytes]:
    """Compile a deny term with alphanumeric boundaries where they make sense.

    The boundary is applied only on an end that is itself alphanumeric. That
    matters: an email-domain term such as ``@example.com`` starts with ``@``, and
    a leading boundary would require the character before the ``@`` to be a
    non-alphanumeric -- which is never true in a real address. The trailing
    boundary is what stops ``Mark`` from matching ``Marketing``.
    """
    if not term:
        raise ScanError("deny list contains an empty term")
    pattern = re.escape(term).encode()
    if term[0].isalnum():
        pattern = rb"(?<![A-Za-z0-9])" + pattern
    if term[-1].isalnum():
        pattern = pattern + rb"(?![A-Za-z0-9])"
    return re.compile(pattern, re.IGNORECASE)


def _read_toml(path: Path, what: str) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ScanError(f"{what} not found: {path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScanError(f"{what} could not be read: {path}: {exc}") from exc


def _string_list(raw: object, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ScanError(f"{where} must be a list of strings")
    return tuple(raw)


def _parse_allowlist(raw: object, where: str) -> tuple[AllowlistEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ScanError(f"{where} must be a list of tables")
    entries: list[AllowlistEntry] = []
    for index, item in enumerate(raw):
        label = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise ScanError(f"{label} must be a table")
        patterns = _string_list(item.get("paths"), f"{label}.paths")
        if not patterns:
            raise ScanError(f"{label}.paths must list at least one path pattern")
        rules = _string_list(item.get("rules"), f"{label}.rules")
        if not rules:
            raise ScanError(f'{label}.rules must list rule ids, or ["*"] for all')
        unknown = [rule for rule in rules if rule != "*" and rule not in ALL_RULES]
        if unknown:
            raise ScanError(f"{label}.rules names unknown rule(s): {', '.join(unknown)}")
        try:
            compiled = tuple(re.compile(pattern) for pattern in patterns)
        except re.error as exc:
            raise ScanError(f"{label}.paths is not a valid regex: {exc}") from exc
        entries.append(
            AllowlistEntry(
                patterns=compiled,
                rules=frozenset(rules),
                description=str(item.get("description", "")),
            )
        )
    return tuple(entries)


def load_config(config_path: Path, deny_file: Path | None) -> Config:
    """Load the committed config, then merge an optional out-of-tree deny list.

    The committed config holds no sensitive values. Employee names are not in it
    and must not be added to it: the names are themselves the confidential data,
    and this repository is destined to be public. Supply them through
    ``--deny-file`` or ``LEAK_SCAN_DENY_FILE``, pointing at a file kept outside
    the tree. A deny file that is named but missing is an error, not a warning --
    otherwise the strictest part of the policy would quietly stop applying.
    """
    raw = _read_toml(config_path, "leak-scan config")
    deny = raw.get("deny", {})
    if not isinstance(deny, dict):
        raise ScanError("[deny] must be a table")
    hostnames = _string_list(deny.get("hostnames"), "[deny].hostnames")
    names = _string_list(deny.get("names"), "[deny].names")
    allowlist = _parse_allowlist(raw.get("allowlist"), "[[allowlist]]")
    sources = [str(config_path)]

    if deny_file is not None:
        extra = _read_toml(deny_file, "leak-scan deny file")
        extra_deny = extra.get("deny", {})
        if not isinstance(extra_deny, dict):
            raise ScanError("deny file: [deny] must be a table")
        hostnames += _string_list(extra_deny.get("hostnames"), "deny file [deny].hostnames")
        names += _string_list(extra_deny.get("names"), "deny file [deny].names")
        sources.append(str(deny_file))

    return Config(
        hostnames=hostnames,
        names=names,
        allowlist=allowlist,
        sources=tuple(sources),
    )


def _rules_for(config: Config) -> list[tuple[str, re.Pattern[bytes]]]:
    rules: list[tuple[str, re.Pattern[bytes]]] = [
        (RULE_AWS_ACCOUNT, _ACCOUNT_RE),
        (RULE_AWS_ARN, _ARN_RE),
        (RULE_INTERNAL_TICKET, _TICKET_RE),
    ]
    rules += [(RULE_DENY_HOSTNAME, _term_pattern(term)) for term in config.hostnames]
    rules += [(RULE_DENY_NAME, _term_pattern(term)) for term in config.names]
    return rules


def is_allowlisted(relpath: str, rule: str, config: Config) -> bool:
    return any(entry.waives(relpath, rule) for entry in config.allowlist)


def enumerate_paths(root: Path) -> list[str]:
    """Tracked files plus untracked files git would not ignore.

    Including untracked-but-not-ignored files is deliberate: a seeded violation
    in a new file is caught before it is ever committed, and it removes a whole
    class of vacuous negative control where the seed lands in a file the scan
    never looks at.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ScanError("git executable not found; cannot enumerate the tree") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise ScanError(f"git ls-files failed in {root}: {detail}") from exc
    seen: dict[str, None] = {}
    for chunk in completed.stdout.split(b"\0"):
        if chunk:
            seen.setdefault(chunk.decode("utf-8", "surrogateescape"), None)
    return sorted(seen)


def _line_of(blob: bytes, offset: int) -> int:
    return blob.count(b"\n", 0, offset) + 1


def scan_paths(
    root: Path,
    relpaths: list[str],
    config: Config,
    ignore_allowlist: bool = False,
) -> tuple[list[Finding], list[str], int]:
    """Return (findings, notes, files_scanned)."""
    rules = _rules_for(config)
    findings: list[Finding] = []
    notes: list[str] = []
    scanned = 0

    for relpath in relpaths:
        applicable = [
            (rule, pattern)
            for rule, pattern in rules
            if ignore_allowlist or not is_allowlisted(relpath, rule, config)
        ]
        if not applicable:
            continue

        # The path itself can carry an identifier. Reported at line 0.
        path_bytes = relpath.encode("utf-8", "surrogateescape")
        for rule, pattern in applicable:
            if pattern.search(path_bytes):
                findings.append(Finding(rule=rule, relpath=relpath, line=0))

        absolute = root / relpath
        try:
            if absolute.is_symlink():
                notes.append(f"not scanned (symlink): {relpath}")
                continue
            if not absolute.is_file():
                # Deleted or replaced between enumeration and read, or a gitlink.
                notes.append(f"not scanned (not a regular file): {relpath}")
                continue
            size = absolute.stat().st_size
            if size > MAX_FILE_BYTES:
                notes.append(f"not scanned (larger than {MAX_FILE_BYTES} bytes): {relpath}")
                continue
            blob = absolute.read_bytes()
        except OSError as exc:
            raise ScanError(f"could not read {relpath}: {exc}") from exc

        scanned += 1
        for rule, pattern in applicable:
            for match in pattern.finditer(blob):
                findings.append(
                    Finding(rule=rule, relpath=relpath, line=_line_of(blob, match.start()))
                )

    findings.sort(key=lambda f: (f.relpath, f.line, f.rule))
    return findings, notes, scanned


def _resolve_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ScanError("git executable not found; pass --root explicitly") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise ScanError(f"not inside a git repository: {detail}") from exc
    return Path(completed.stdout.decode().strip()).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leak_scan",
        description="Fail on internal identifiers in the working tree.",
    )
    parser.add_argument("--root", help="repository root (default: git toplevel)")
    parser.add_argument("--config", help=f"config file (default: <root>/{DEFAULT_CONFIG_RELPATH})")
    parser.add_argument(
        "--deny-file",
        help=(
            "extra out-of-tree deny list (TOML with a [deny] table); "
            f"defaults to ${DENY_FILE_ENV_VAR} when that is set"
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="scan only these repo-relative paths instead of the whole tree",
    )
    parser.add_argument(
        "--ignore-allowlist",
        action="store_true",
        help="diagnostic: apply every rule to every file, ignoring the allowlist",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _resolve_root(args.root)
        config_path = Path(args.config) if args.config else root / DEFAULT_CONFIG_RELPATH
        raw_deny_file = args.deny_file or os.environ.get(DENY_FILE_ENV_VAR) or None
        config = load_config(config_path, Path(raw_deny_file) if raw_deny_file else None)
        relpaths = list(args.paths) if args.paths else enumerate_paths(root)
        findings, notes, scanned = scan_paths(
            root, relpaths, config, ignore_allowlist=args.ignore_allowlist
        )
    except ScanError as exc:
        print(f"leak-scan: ERROR {exc}", file=sys.stderr)
        print("leak-scan: the scan did not complete; treat this as a failure.", file=sys.stderr)
        return 2

    for note in notes:
        print(f"leak-scan: WARN {note}")

    if not findings:
        print(
            f"leak-scan: OK no findings in {scanned} file(s) (config: {', '.join(config.sources)})"
        )
        return 0

    for finding in findings[:MAX_REPORTED_FINDINGS]:
        print(f"leak-scan: FAIL {finding}")
    suppressed = len(findings) - MAX_REPORTED_FINDINGS
    if suppressed > 0:
        print(f"leak-scan: FAIL ... and {suppressed} more finding(s) not listed")

    rules_fired = sorted({finding.rule for finding in findings})
    files = sorted({finding.relpath for finding in findings})
    print(
        f"leak-scan: {len(findings)} finding(s) in {len(files)} file(s) "
        f"of {scanned} scanned; rules fired: {', '.join(rules_fired)}"
    )
    print("leak-scan: matched values are not printed on purpose; open the file and line above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
