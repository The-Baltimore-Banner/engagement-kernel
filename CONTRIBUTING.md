# Contributing to engagement-kernel

Thank you for your interest. This guide is the entry point for anyone changing
this repository.

## Project status

This repository is **pre-release and not yet published**. It has no release, no
package on any index, and no stable interface. External contributions are not
being accepted yet; the guide is written now so that the rules are in place
before the code is.

## What must never land here

This repository is being assembled by extracting code from a private repository
that contains material which cannot be published. Before anything else, know what
is not allowed in a commit:

* a cloud account id, or any cloud resource identifier
* an internal issue-tracker key, ticket reference, or internal evidence document
* an internal hostname, database endpoint, or employee mail address
* a personal name in configuration, test data, or comments
* a credential of any kind, including one that looks fake

Work tracking does not live here either. Issues and tickets belong in the
tracker; this repository holds code, tests and documentation for the code.

`tools/leak_scan.py` enforces the first four of those and runs on every pull
request. Run it before you push:

```bash
python3 tools/leak_scan.py
```

If you maintain a private deny list of names, point the scan at it:

```bash
LEAK_SCAN_DENY_FILE=~/private/engagement-kernel-deny.toml python3 tools/leak_scan.py
```

The gate is not the last word. It catches patterns; it cannot tell you that a
whole file was an internal document. When you port a file, read it.

**Do not widen the gate to make a change pass.** Adding a path to the allowlist
in `tools/leak_scan.toml`, softening a rule, or making a CI step tolerate failure
are all changes to the security posture of a repository that is heading for
publication, and they get reviewed as such. If a rule fires on something
legitimate, say so in the pull request and let the allowlist entry be reviewed
on its own.

## Development setup

Requires Python 3.11 or newer.

```bash
git clone https://github.com/The-Baltimore-Banner/engagement-kernel.git
cd engagement-kernel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Installing the kernel must never pull a cloud SDK or a warehouse driver. Vendor
adapters are optional extras (`pip install -e ".[adapters-aws]"`), and
`tests/test_packaging.py` fails the build if a vendor dependency reaches the core
list.

## Making changes

1. Create a branch from `main` and keep the diff focused on one problem.
2. Respect the package boundaries: `contract` depends on nothing else here,
   `intermediate` builds on `contract`, and `engagement` and `content` build on
   `intermediate`.
3. Keep the reference engine portable. If a change needs a specific warehouse,
   a scheduler, or a cloud service, it belongs behind an optional adapter.
4. Add or update tests with the change.
5. Update the README when behaviour a user depends on changes.

## Validation

Before opening a pull request:

```bash
ruff check .
ruff format --check .
pytest
python3 tools/leak_scan.py
```

CI runs the same four checks plus gitleaks on every pull request. All of them
must pass.

## Pull requests

* Describe the intent, the risk, and how you validated the change.
* Say explicitly if the change touches the leak gate, the CI workflow, or
  packaging.
* Keep commits reviewable. Force-pushing your own branch before merge is fine.

## Reporting security issues

Do **not** open a public issue for a vulnerability. Follow
[SECURITY.md](SECURITY.md).

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
