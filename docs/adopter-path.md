# Running the engine on your own data

> **This is the adopter path** — for a newsroom that wants to score its own
> readers. It assumes you have never read this repository's source and do not
> intend to.
>
> If you are here to change the engine rather than run it, you want
> [Getting started for contributors](../README.md#getting-started-for-contributors)
> instead. That path starts with an editable install and the test suite; this one
> does not, and the two are not interchangeable.

Six steps. The first three run on data we supply, so you can see the whole machine
work before you have produced anything. The last three are your own data.

Budget an afternoon for steps 1–3 and days-to-weeks for 4–6, almost all of it in
step 4 — which is your warehouse, not our software.

---

## Before you start: can you even use this?

Two things people wrongly conclude they fail.

**"Seven tables is too many."** Four are required, three are optional, and a
newsroom with no app, no comments and no email newsletter can still run this. An
absent input is *declared* absent and the engine selects a named alternate feature
set — it never fills the gap with zeros. See
[what you lose per omitted input](../README.md#four-required-inputs-three-optional);
one of the three costs the model nothing at all.

**"We don't have sessions / a content taxonomy / attention time."** Several
contract fields are things a warehouse computes rather than stores. That is a
derivation, which is step 4's job, and the worked example in
[`examples/mapping/`](../examples/mapping) shows what one looks like.

The genuine blocker is different, and worth knowing now rather than in step 4:
**anonymous traffic is out of scope.** A reader id must identify one resolved
person. If your reading data is mostly unresolved browsers, this contract cannot
express that, and minting one reader per browser to get past the validator would
make every distinct-reader count and every cross-channel join meaningless. There
is no workaround at this contract version.

---

## 1. Install it

There is no release and no package on any index yet, so this is a clone. Saying so
plainly beats implying a `pip install engagement-kernel` that does not exist.

```bash
git clone https://github.com/The-Baltimore-Banner/engagement-kernel.git
cd engagement-kernel

python3 -m venv .venv && source .venv/bin/activate
pip install .
```

Python 3.11 or newer. That pulls DuckDB, pandas, PyArrow, scikit-learn and SciPy —
no cloud SDK, no warehouse driver, no service to provision. If a cloud SDK ever
turns up in that install, the build is broken and CI says so.

Note `pip install .` and not `pip install -e ".[dev]"`. The `dev` extra is the
contributor path: it adds the linter and the test framework, which you do not need
to run the engine.

Check it took:

```bash
engagement-kernel-validate --help
```

## 2. Watch it work on data you did not produce

```bash
engagement-kernel-demo-dataset /tmp/demo
engagement-kernel-validate /tmp/demo
```

That is a conforming delivery: nine invented readers, every value synthetic,
carrying worked examples of the cases that are easy to get wrong — including an
event near local midnight on every channel. Expect `PASS: every table conforms`.

Now build the daily intermediate tables:

```bash
engagement-kernel-build-intermediate /tmp/demo --out /tmp/demo-intermediate
ls /tmp/demo-intermediate
```

**Seven daily aggregates, built in one in-process DuckDB session with no
warehouse and no credentials.** If you got here, the engine runs on your machine
and the rest is your data.

Add `--print-sql` to see exactly what it ran. Nothing is hidden and there is no
service in the middle.

## 3. Watch the model work

The demo delivery is nine readers, which is a conforming delivery and far too
small to fit a model on. So there is a second generator for that:

```bash
engagement-kernel-cohort /tmp/cohort --readers 400
engagement-kernel-engagement-lane run /tmp/cohort \
    --bucket-map /tmp/cohort/section_buckets.json \
    --output-dir /tmp/cohort-out
```

That fits the engagement model, selects a cluster count, freezes the fit and
scores every complete week. It prints the resolved declarations before it does any
work — the week anchor, the day boundary, the article-view definition, the scored
population — so a run against the wrong ones is visible at the top of the log
rather than inferred from the output afterwards.

Expect it to **refuse to publish**. The interpretability gate fails until a person
has reviewed the clusters and named them, which is the intended default rather
than a bug: a cluster nobody can describe is not a segment, and the gate exists so
that shipping one takes a deliberate act. `--interpretability-reviewed` is that
act, and it is not for a first run.

## 4. Map your warehouse onto the contract

**This is the hard step, and the only one where we cannot help directly.**

The input is a contract, not a database: the kernel reads a canonical set of
columnar files, and anything that produces those files is a valid source. That is
genuinely liberating and it puts the whole burden on you.

We do not ship a prose walkthrough of it, on purpose. It would have to be written
against a warehouse and it would be ours, which is how a portable contract quietly
re-anchors on one vendor's shapes. Instead:

**4a. Answer the four questions** in
[the declarations questionnaire](declarations-questionnaire.md). Do this first and
do not skip to the export. Two of the four are editorial and half of one is
commercial, so this is a page to take to other people rather than work through
alone. Nothing runs until they are answered, and the answers change what the
numbers mean rather than whether the code executes.

**4b. Hand [agent spec 1](agent-spec-1-map-your-warehouse.md) to your own coding
agent**, in your own repository, with your DDL in front of it. It produces an
adapter, a delivery and a mapping manifest in which every contract field resolves
to exactly one of rename, derive, declare-absent or gap.

**4c. Check what comes back**, with three commands that run here rather than in
your agent's context:

```bash
engagement-kernel-validate <your-delivery>
engagement-kernel-lint-mapping <your-mapping-dir> --adapter-bundle <your-adapter> --warnings-as-errors
engagement-kernel-check-oracle <your-case-set>
```

Read [what the checks cannot check](agent-spec-1-map-your-warehouse.md#what-the-checks-cannot-check)
before you treat three green commands as an answer. They prove your mapping is
complete, consistent and traceable. They cannot prove it is true, and the two
places they are weakest — a plausible derivation that was never implemented, and
an optional input declared absent because extracting it was awkward — are both
things a person has to look at.

## 5. Build your intermediates

```bash
engagement-kernel-build-intermediate <your-delivery> --out <your-intermediate>
```

Same command as step 2. If step 4 was done properly this is uneventful, which is
the intended shape of the whole design: the interesting decisions are all upstream
of here, in the contract and the declarations, and this step is arithmetic.

## 6. Fit and score

```bash
engagement-kernel-engagement-lane run <your-delivery> \
    --bucket-map <your-section-buckets.json> --output-dir <out>
```

Two things you supply that the engine will not invent:

**A section bucket map.** Your section taxonomy is yours — however many sections
it has, named whatever your CMS calls them. The map from sections to topic buckets
is a file your deployment owns and versions. There is no default and the engine
refuses to guess one. `/tmp/cohort/section_buckets.json` from step 3 shows the
shape.

**A person who reads the clusters.** See step 3 on the interpretability gate.

A first run against real data wants a narrow sweep and few draws, to see the
shape before paying for a freeze:

```bash
engagement-kernel-engagement-lane run <delivery> --bucket-map <map> \
    --k-min 3 --k-max 6 --seeds 3 --perturbation-draws 3
```

The defaults are production settings and expensive on purpose: every candidate
cluster count is re-screened on many perturbed panels.

---

## Where this path stops

At scored weeks and frozen cluster assignments, written as files.

It does not yet cover **publishing** those outputs — loading them back into your
warehouse, or the segment surface a downstream team would read. Those are
deliberately out of scope for now: the engine's outputs are files, and you load
them with the same tooling that produced the delivery in the first place. When
published outputs land, this document gains a step 7 rather than pointing you
somewhere else.

One lane follows the intermediate build, not two. The content-persona lane
(topic clustering) was dropped rather than deferred quietly; topic *features* in
the engagement model are unaffected, and there is nothing missing from the path
above because of it.

---

## If you get stuck

The validator's refusals are written to name the fix and not only the violation —
that is a maintained property with a
[standing check behind it](adopter-first-contact-messages.md), not a courtesy. If
you hit one that does not tell you what to do, that is a defect worth reporting.

| document | what it is for |
| --- | --- |
| [declarations-questionnaire.md](declarations-questionnaire.md) | the four decisions, as questions with owners |
| [agent-spec-1-map-your-warehouse.md](agent-spec-1-map-your-warehouse.md) | the brief for your coding agent, and the checks on its output |
| [canonical-input-contract.md](canonical-input-contract.md) | what the concepts mean, and why the contract refuses what it refuses |
| [contract-reference.md](contract-reference.md) | every field: type, nullability, enum, definition |
| [validator-negative-controls.md](validator-negative-controls.md) | every defect the validator catches, with its verbatim message |
| [intermediate-tables.md](intermediate-tables.md) | the seven daily aggregates, and four derivations where the obvious rewrite is wrong |
| [engagement-lane.md](engagement-lane.md) | what the model publishes, and the guards on what may become a feature |
| [adopter-first-contact-messages.md](adopter-first-contact-messages.md) | the four failures you are most likely to hit first |
