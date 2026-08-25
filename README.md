# engagement-kernel

Portable engagement scoring and clustering machinery for a news organisation's
subscriber and content data, built to run anywhere a directory of columnar files
can be read.

## Status: pre-release

**This repository is public, and pre-release.** There is no release, no package
on any index, and no stable interface. What is in place: the scaffold and CI
gate, the canonical input contract and its validator, the daily intermediate
build, and the engagement lane -- a weekly engagement score and a behavioural
cluster per reader. The content-persona lane is not being built.

It **is** runnable end to end, on the synthetic data in `examples/` or on a
delivery you produce, which is what [the adopter path](docs/adopter-path.md) is
for. "Pre-release" is about distribution and interface stability, not about
whether the engine works.

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

# Write a synthetic cohort large enough to fit a model on.
engagement-kernel-cohort <dir> [--readers N]

# Fit the engagement model and score every complete week.
engagement-kernel-engagement-lane run <delivery-dir> --bucket-map <map.json> [--output-dir <dir>]

# Check a mapping manifest: every contract field accounted for, every gap owned.
engagement-kernel-lint-mapping <dir> [--adapter-bundle <dir>] [--warnings-as-errors]

# Write a worked validator-oracle example, then check it.
engagement-kernel-demo-oracle <dir>
engagement-kernel-check-oracle <dir>
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
* **The engagement lane.** The modelling layer: a weekly engagement score and a
  behavioural cluster per reader, fit once and applied thereafter, on pandas and
  scikit-learn with no warehouse and no vendor SDK.
  [`docs/engagement-lane.md`](docs/engagement-lane.md) covers what it publishes,
  the two guards that decide what may become a model feature, and -- in full --
  the census of what deliberately did not come across.
  [`docs/clustering-methodology.md`](docs/clustering-methodology.md) is the layer
  underneath all of that: why the method is built this way -- why behaviour-only
  inputs, why the number of clusters is derived rather than chosen, why an
  instrumentation gap gets two different remedies depending on which kind it is,
  and which of the numbers are the adopting newsroom's to set. It is written to be
  reproducible without this engine.
  [`docs/gate-configuration.md`](docs/gate-configuration.md) is how a deployment
  sets its own thresholds and its own candidate cluster counts, including the one
  threshold that cannot honestly be inherited -- the cross-algorithm agreement bar,
  which depends on your row count, your dimensionality and your readers' own
  correlation structure, and which `tools/derive_cross_algorithm_bars.py` measures
  on your panel. It also says which numbers in the lane are deliberately *not*
  configurable, and which are prescriptions still waiting to be lifted.
  [`docs/engagement-lane-parity.md`](docs/engagement-lane-parity.md) says why
  parity is stated structurally rather than numerically, including the email day
  shift that makes numeric email parity unavailable by construction.
  [`docs/engagement-lane-negative-controls.md`](docs/engagement-lane-negative-controls.md)
  carries the controls and the evidence that each one fails when the thing it
  protects is broken.
* **The adopter path.** [`docs/adopter-path.md`](docs/adopter-path.md) is the
  six-step sequence for running the engine on your own data, and
  [`docs/declarations-questionnaire.md`](docs/declarations-questionnaire.md) turns
  the four undefaulted declarations into questions with an owner named per
  question -- one of them commercial rather than technical. The genuinely hard
  step, mapping your warehouse onto the contract, ships as
  [`docs/agent-spec-1-map-your-warehouse.md`](docs/agent-spec-1-map-your-warehouse.md):
  a brief for your own coding agent, whose oracle is this repository's validator.
  A prose walkthrough would have to be written against one warehouse and it would
  be ours, which is how a portable contract quietly re-anchors on one vendor's
  shapes.
* **The declarations you have to make.** Four things the contract refuses to
  default -- what counts as an article view, what to do when the signal is only
  partly present, which timezone defines a day, and which weekday anchors a
  week -- because every plausible default is wrong for somebody and wrong
  silently. [`docs/publisher-declarations.md`](docs/publisher-declarations.md)
  says what each one changes, and records one publisher's answers as a worked
  example.
* **A synthetic demo delivery** in [`examples/demo-delivery/`](examples/demo-delivery),
  every value invented, carrying worked examples of the cases that are easy to
  get wrong -- including an event near local midnight on every channel.

## Two paths through this repository

They are different documents and the difference is not cosmetic. Following the
wrong one is the most common way to conclude this software is harder to use than
it is.

| | you want | start at |
| --- | --- | --- |
| **Adopter** | to score your own readers | [**docs/adopter-path.md**](docs/adopter-path.md) |
| **Contributor** | to change the engine | [Getting started for contributors](#getting-started-for-contributors) |

The adopter path does not begin with an editable install and the test suite. It
begins with the four decisions the contract will not make for you, because those
are what everything downstream means, and because they are decisions somebody
other than the person running the port often has to make.

## Four required inputs, three optional

<!-- input-shape:begin -->
A delivery is **one directory**: seven Parquet files -- **four required, three
optional** -- and a `manifest.json`.

The four you must produce:

* **`reader`** -- The reader registry.
* **`reader_event`** -- Web and app reading activity, one row per event, with the instant it happened.
* **`content`** -- The content dimension: what each piece of content is and which sections it belongs to.
* **`subscription_span`** -- Subscription state as a history of intervals, so a reader's status can be resolved as of any historical date.

The three you may declare absent:

| optional input | feature block | what a run without it loses |
| --- | --- | --- |
| `email_click` | `email_cadence` | The loyalty block, whose one signal is how many of the last four weeks the reader clicked an email in. Habit shows up here and nowhere else: reading volume cannot distinguish a reader who returns weekly from one who arrived once and read a great deal. Clusters stay meaningful without it, and the returning-reader distinction gets weaker. |
| `email_open` | `deliverability` | Nothing in the model. Opens are deliberately not a model feature -- machine opens inflate them and cannot be cleaned out, so an open says a message reached a reachable inbox and nothing about interest. This input exists for reachability reporting, in its own table and its own block precisely so that 'opens are never a feature' is structural rather than a promise. Omit it and the model is unchanged. |
| `community_action` | `community` | The community block: how many community actions the reader took in the window, and on how many distinct days. This is the clearest contribution signal in the model, so a deployment without it distinguishes heavy readers from participants less sharply. It is also the block most newsrooms will not have, which is why its absence is a first-class declaration rather than an obstacle. |
<!-- input-shape:end -->

This is on the front page because of how often it is read the other way round. A
newsroom with no app, no comments product or no email newsletter sees "seven
tables" and concludes the engine is not for them. It is: an absent input is
*declared* absent -- `not_deployed` if you will never have it, `not_yet_launched`
with a floor date if the product is newer than your analysis window -- and the
engine answers by selecting a named alternate feature set. **Nothing is ever
filled with zeros.** A reader with no community data is not a reader who never
comments, and a model told otherwise produces clusters that are plausible and
wrong.

You can start with all three absent.

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

## Getting started for contributors

**This is the contributor path** -- for changing the engine. To *run* it on your
own data, you want [docs/adopter-path.md](docs/adopter-path.md), which starts
somewhere else entirely and does not need the `dev` extra.

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

A named-but-missing deny file is a hard error, not a warning. That is necessary
and it is **not sufficient**, and the gap between the two is worth reading,
because this gate spent months in it. A file that is *missing* raises. A file that
exists and yields no names does not: an empty file, a bare `[deny]` table and
`names = []` all load silently with zero terms, so `deny-name` compiles no
patterns and matches nothing — while the scan reports a clean tree and exits 0.
A clean scan and an inert rule are indistinguishable by exit code.

So a run that depends on the rule asserts the terms arrived:

```bash
python3 tools/leak_scan.py --require-deny-names 1
```

CI passes exactly that, and every run now prints `deny terms loaded: N hostname
term(s), M name term(s)` so the gate's strength is visible rather than assumed.
See [`docs/leak-scan-provisioning.md`](docs/leak-scan-provisioning.md) for the
Actions secret that supplies the terms, and for where the rule does not apply
(fork pull requests cannot receive a secret).

Exit status is meaningful: `0` clean, `1` findings, `2` the scan could not be
trusted. Fixtures that must contain deliberate violations are allowlisted by
path, and the allowlist is proven non-vacuous by a test that scans the same
fixture with `--ignore-allowlist` and requires it to fail.

Gitleaks also runs in CI. The two do different jobs: gitleaks looks for
credential shapes across git history, the leak scan looks for this
organisation's identifiers in the current tree.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Forks and pull requests are open. The
interface is pre-release and moves without notice, so raise anything large before
building it. The most useful contribution right now is a report from walking
[the adopter path](docs/adopter-path.md) and saying where you stalled.

## Security

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.

## License

[Apache License 2.0](LICENSE).
