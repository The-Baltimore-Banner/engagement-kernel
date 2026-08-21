# engagement-kernel

Portable engagement scoring and clustering machinery for a news organisation's
subscriber and content data, built to run anywhere a directory of columnar files
can be read.

## Status: pre-release, not yet published

**This repository is pre-release. It is not published, not publicly available,
and not yet usable.** There is no release, no package on any index, and no
stable interface. The scaffold and CI gate, the canonical input contract and its
validator, and the daily intermediate build are in place; the modelling code --
scoring, subscriber clustering and content personas -- has not been added yet.

Nothing here should be treated as a supported dependency. Interfaces, package
names and file layouts will change without notice until a first release is cut.

## What this will be

The engagement model in use today runs inside a private, cloud-coupled pipeline.
This repository is the portable version of the same machinery: the scoring and
clustering logic, separated from the warehouse, the scheduler and the vendor SDKs
it currently sits inside, so that another newsroom can run it against its own
data without adopting our infrastructure.

Two decisions follow from that goal:

* **The input is a contract, not a database.** The kernel reads a canonical set of
  columnar files. Anything that produces those files is a valid source.
* **The reference engine is DuckDB.** It runs on a laptop, in CI, and in a
  container, with no service to provision. Vendor-specific loaders are optional
  adapters that translate a source into the canonical contract; they are extras,
  never core dependencies. Installing the kernel pulls no cloud SDK, and
  `tests/test_packaging.py` fails the build if that ever stops being true.

## What is here now

```bash
# Validate a delivery against the input contract.
engagement-kernel-validate <delivery-dir>

# Write the synthetic demo delivery.
engagement-kernel-demo-dataset <dir>

# Build the daily intermediate tables from a delivery.
engagement-kernel-build-intermediate <delivery-dir> [--out <dir>] [--print-sql]
```

* **The input contract.** Seven tables, each with a grain, a deduplication key,
  a stated null behaviour and a one-line definition per field, plus a validator
  that refuses data which would produce quietly wrong numbers. Start at
  [`docs/canonical-input-contract.md`](docs/canonical-input-contract.md); the
  evidence that the validator actually refuses each class of bad input is in
  [`docs/validator-negative-controls.md`](docs/validator-negative-controls.md).
* **The daily intermediate build.** Seven daily aggregates, produced in one
  in-process DuckDB session with no warehouse and no credentials. See
  [`docs/intermediate-tables.md`](docs/intermediate-tables.md) for the grains and
  for the four derivations where the obvious rewrite is wrong, and
  [`docs/intermediate-negative-controls.md`](docs/intermediate-negative-controls.md)
  for the captured proof that each one is caught when broken on purpose.
* **A synthetic demo delivery** in [`examples/demo-delivery/`](examples/demo-delivery),
  every value invented, carrying worked examples of the cases that are easy to
  get wrong -- including an event near local midnight on every channel.

## Layout

```
src/engagement_kernel/
  contract/      canonical columnar input contract: schemas, validation, loading
  intermediate/  derived per-subscriber and per-period tables
  engagement/    engagement scoring and subscriber clustering
  content/       content-side measures and content-to-engagement joins
tools/           repository tooling, including the leak scan described below
tests/           test suite
```

The four packages are deliberately layered: `contract` knows nothing about the
others, `intermediate` builds on `contract`, and `engagement` and `content` build
on `intermediate`.

## Getting started

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .          # lint
ruff format --check . # formatting
pytest                # tests
python3 tools/leak_scan.py            # the leak gate, see below
python3 tools/import_closure_check.py # portability, see below
```

Every one of those runs in CI on every pull request.

## The import-closure check

`tests/test_packaging.py` asserts the *declared* dependencies stay free of cloud
SDKs. That is necessary and not sufficient: a module can import a vendor library
that happens to be installed for some other reason, and every check still passes
on the machine where it is installed.

So `tools/import_closure_check.py` does the opposite. It blocks every vendor
import by name, then imports every module in the package, resolves every declared
console script, and runs the whole intermediate build over the demo delivery. It
runs as its own CI job, installed from the **core** dependencies only -- the test
extra could pull a transitive vendor library and satisfy exactly the import the
check exists to refuse.

## The leak scan

This repository is being assembled by extracting code from a private repository
that is full of things that must never be published: a production cloud account
id, cloud resource identifiers, internal issue-tracker keys, internal hostnames,
and employee names in test data. Making a public repository private again does
not un-index what has already been crawled, so the gate exists before the content
does.

`tools/leak_scan.py` fails the build on any of:

| rule | what it catches |
| --- | --- |
| `aws-account` | a 12-digit cloud account id |
| `aws-arn` | a cloud resource identifier |
| `internal-ticket` | an internal issue-tracker key |
| `deny-hostname` | a denied hostname or mail domain |
| `deny-name` | a denied personal name |

It is stdlib-only, so it needs no install step. It scans tracked files plus
untracked files git would not ignore, reads them as bytes so binary and
oddly-encoded files are covered, checks filenames as well as contents, and
reports findings as `rule path:line` without ever echoing the matched value --
CI logs are public.

The terms live in `tools/leak_scan.toml`. Employee names are deliberately **not**
in it: the names are themselves the confidential data, and committing hashes of
low-entropy human names protects nothing. Supply them at scan time from a file
kept outside the tree:

```bash
LEAK_SCAN_DENY_FILE=~/private/engagement-kernel-deny.toml python3 tools/leak_scan.py
```

A named-but-missing deny file is a hard error, not a warning, so the strictest
part of the policy cannot quietly stop applying.

Exit status is meaningful: `0` clean, `1` findings, `2` the scan could not be
trusted. Fixtures that must contain deliberate violations are allowlisted by
path, and the allowlist is proven non-vacuous by a test that scans the same
fixture with `--ignore-allowlist` and requires it to fail.

Gitleaks also runs in CI. The two do different jobs: gitleaks looks for
credential shapes across git history, the leak scan looks for this
organisation's identifiers in the current tree.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Because this repository is pre-release
and closed, external contributions are not being accepted yet.

## Security

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.

## License

[Apache License 2.0](LICENSE).
