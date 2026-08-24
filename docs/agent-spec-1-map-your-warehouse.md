# Agent spec 1 — map your warehouse onto the contract

> **This is the adopter path.** For working *on* this repository instead, see
> [Getting started for contributors](../README.md#getting-started-for-contributors).

This is the step with no shortcut. Everything else an adopter does is running a
command; this is deciding what your data *means* in someone else's vocabulary,
and only you have your schema.

So this repository does not ship a prose walkthrough of it. A walkthrough would
have to be written against a warehouse, and it would be ours — which is how a
portable contract quietly re-anchors on one vendor's shapes. It ships this
instead: a brief you hand to your own coding agent, in your own repository, with
your DDL in front of it. Your agent has the one thing no document we could write
has, which is your schema. What we supply is the vocabulary, the rules, and the
checks that catch a confident wrong answer.

**What you need before starting:** the four answers from
[the declarations questionnaire](declarations-questionnaire.md). The brief below
refuses to proceed without them, and that refusal is the point — an agent that
can invent your week anchor will.

---

## Before you delegate: what this cannot be delegated for

The agent produces the mapping. It does not decide the four declarations, and it
does not decide whether a source column *means* what you need it to mean. Those
are judgements about your organisation, and an agent holding your DDL is
confidently equipped to guess at both.

The checks in this repository are built on that assumption. They can prove your
mapping is complete, self-consistent and traceable. They cannot prove it is true.
Read [what the checks cannot check](#what-the-checks-cannot-check) before you
treat a green lint as an answer.

---

## The brief

Copy everything between the rules into your agent, in a repository that has your
warehouse DDL and your sample extracts. Replace the bracketed parts.

---

You are mapping our data warehouse onto an external input contract so that a
portable engagement-scoring engine can read our data. Work in this repository.

**Read these first, in this order.** They are the whole specification; do not
work from memory of similar contracts.

1. `docs/canonical-input-contract.md` — what the concepts mean, and the three
   properties everything else follows from.
2. `docs/contract-reference.md` — every table and field: type, nullability,
   enum, one-line definition.
3. `docs/validator-negative-controls.md` — what the validator refuses and its
   verbatim message for each defect. Read this before writing the adapter, not
   after it refuses you.
4. `examples/mapping/mapping-manifest.json` — a complete worked mapping for an
   invented newsroom. The *shape* of a good answer. Do not copy its content: it
   maps a warehouse that is not ours, and a mapping copied from another
   warehouse is the one artifact here that cannot be reused.

**Our answers to the four required declarations** — these are decided, and you
must not change, default, or "improve" them:

- `day_boundary_timezone`: [IANA name]
- `week_anchor`: [weekday] / [week_starts_on | week_ends_on]
- `article_view`: [content types] via [event kinds], definition id [id]
- `scored_population`: entitled states [states], definition id [id]

If any of those four is blank, **stop and ask.** Do not choose one. Each has no
default in the contract because every plausible default is wrong for some
publisher and wrong without anything visibly breaking — a wrong week anchor does
not produce an error, it produces numbers.

### Produce three things

**1. An adapter.** Queries and an export step, in whatever our stack already
runs. The contract does not care what produces the files. Match the conventions
already in this repository rather than introducing a new stack.

**2. A delivery directory.** The contract's Parquet files plus `manifest.json`.
Start from `examples/manifest-template.json` and fill in the four declarations
above and the per-input availability.

**3. A mapping manifest**, `mapping-manifest.json`, resolving **every** contract
field to exactly one of four outcomes. This is the artifact that gets reviewed by
a person, so it is the one to spend care on.

### The four outcomes, and the fifth you must not invent

**`rename`** — a column in our warehouse means the same thing. Mechanical. A type
or encoding change is still a rename; a change in what a row *counts* is not.
`page_loaded` → `article_view` is **not** a rename, however similar the names
look.

**`derive`** — our concept computed into theirs. Sessionising a flat event
stream, joining a billing table, resolving a device to a person. Record the
*rule*, with its thresholds, and cite the file that implements it.

**`declare_absent`** — legal only for the three optional tables, and only when
that table's availability says the input is not delivered. Use the contract's own
mechanism: `not_deployed` (we do not have this product) or `not_yet_launched` (we
have it now, but not for the whole analysis period), each with a one-sentence
statement.

**`gap`** — the field is not in our warehouse and cannot be derived. Record an
accountable **role** and a reference into our tracker, plus a concrete blocker.

**You are forbidden to synthesize a field.** There is no fifth outcome for a
value you decided to assume. If you cannot place a field, it is a `gap` — which
is a real answer that routes to a person, not a failure. Filling it with a
plausible constant is the single worst thing you can do in this task, because it
is the one error that produces no symptom at any later stage.

### When a field has no answer

Work down this list; do not stop early.

1. **Is the whole input optional?** Then the answer is a declaration, not data.
   `not_deployed` or `not_yet_launched` with a floor date. This is the designed
   outcome, not a workaround, and the engine will select a named alternate
   feature set and say so in its report.
2. **Is the field required but nullable?** Then null is a real answer with defined
   semantics, and the contract's definition of that null is authoritative. Read
   it. `payer_type` null means *unknown*, never `individual`.
   `engagement_time_seconds` null means *unmeasured*, never `0`. Writing the
   confident value converts a known unknown into a false known.
3. **Is it required and non-nullable?** Then a human decides between: fix the
   upstream feed; narrow the delivery window to a period where the field exists;
   or conclude our deployment cannot satisfy the contract yet. All three are real
   outcomes. Record it as a `gap` with an owner. Do not choose between them
   yourself.

### Then break it on purpose

Produce a `validation-cases.json` and at least three deliberately broken variants
of the delivery, each differing from the baseline in **exactly one file**, each
declaring the exit status, the finding code, and a phrase from the validator's
message that you expect.

This is not busywork and it is not a test of the validator. A delivery that
validates might validate because you got it right, or because the parts you got
wrong are not the parts that are checked. Predicting a refusal — and being right
about which one — is the cheapest available evidence that you read the rules
rather than pattern-matched the example. Pick defects that would plausibly happen
to us, not the easiest ones to construct.

`engagement-kernel-demo-oracle <dir>` writes a worked set of five for the demo
delivery. Read it for the shape.

### Report back

- The mapping manifest, and `engagement-kernel-lint-mapping` output.
- `engagement-kernel-validate` output for the delivery.
- `engagement-kernel-check-oracle` output for your case set.
- **Every field you resolved as `derive`, listed, with its rule.** These are what
  a human needs to review, and they will not read the whole adapter.
- **Every `gap`, with its owner.**
- **Anything you were tempted to guess.** Name it even if you resolved it
  correctly in the end. That list is more useful to us than the mapping.

---

## The checks

Three, and they run here rather than in your agent's context, which is the point:
an agent checking its own work is the arrangement that produces confident wrong
answers.

```bash
# 1. Does the delivery conform?
engagement-kernel-validate <delivery>

# 2. Is the mapping complete, consistent and traceable?
engagement-kernel-lint-mapping <dir> --adapter-bundle <adapter-dir>

# 3. Do the broken variants fail for the reasons they predict?
engagement-kernel-check-oracle <case-set-dir>
```

Warnings are not defects. The lint warns on every declared absence and every
gap, because those are the claims it cannot adjudicate — the worked example emits
two and is correct. `--warnings-as-errors` exists for a pipeline that wants a
sign-off enforced; it is the wrong default for a first run, and an agent told to
drive the warning count to zero will start converting declared absences into
invented data.

### What the mapping lint checks

| it refuses | because |
| --- | --- |
| a field left out | leaving one out is the only way to smuggle in a value nobody decided |
| an outcome outside the four | an unknown outcome is a default wearing a name |
| a `default` key beside a real outcome | same, one level down |
| `null`, `{}`, or a bare string as a resolution | treating one as "absent" is the silent default in its purest form |
| a duplicate JSON key | `json.loads` keeps the last value, so one accounting quietly replaces another |
| `declare_absent` on a required table or field | the contract's absence mechanism covers the three optional inputs only |
| `declare_absent` inside an available table | absence is a property of the whole input, not one column |
| a mapped field inside an undelivered table | the two statements contradict each other |
| `declare_absent` carrying a source | a field with a source is not absent |
| an absence with no stated reason | this is the outcome a reviewer most needs to be able to disagree with |
| an availability status outside the three | there is deliberately no status meaning "we have it but extraction was awkward" |
| a `gap` with no owner | an unassigned problem is how it stops being anybody's |
| a `gap` owned by `TBD`, `Data Team`, `unknown` | those are the absence of an owner, spelled |
| a `gap` owner who is a person or an email | record the **role**; it stays true across the staff change that makes a name stale, and this artifact is meant to be shareable |
| a derivation with no inputs | it computes the field out of nothing |
| a cited file that does not exist | the cheapest way to make an unimplemented derivation look implemented |
| a cited file whose digest has moved | the rationale beside it describes code that is no longer there |
| a field or table the contract does not define | an extra field is how a vendor-shaped table arrives one column at a time |

### What the oracle checks

Each case must produce the declared exit status, raise the declared finding code,
contain the declared phrase from the message, and **differ from the baseline in
exactly the files it declares**. That last one is what stops the obvious cheat:
deleting everything and observing a failure is not the same evidence as breaking
one thing and observing the failure it predicts. The baseline is validated first,
because a case set whose baseline does not conform proves nothing about its
variants — every one of them would be refused for the baseline's defect.

---

## What the checks cannot check

Stated as a property, because it follows from the inputs rather than from work not
yet done:

> Given the mapping manifest, the adapter snapshot and the delivery, no static
> check can determine whether a source column has the meaning claimed for it, nor
> whether the submitted adapter is what produced the delivery.

A column called `article_id` may be a CMS record, a canonical content entity, a
revision, or a URL slug. A correctly typed timestamp may be ingest time rather
than occurrence time. A valid delivery can be hand-edited after the adapter ran.
An `not_deployed` assertion and a named owner are claims about an organisation,
not computable facts.

So the division of labour is:

| | proves |
| --- | --- |
| `engagement-kernel-validate` | the files conform: schemas, types, nullability, vocabularies, keys, referential integrity, availability floors |
| `engagement-kernel-lint-mapping` | the mapping is complete, internally consistent, and traceable to code that exists at the digest recorded |
| `engagement-kernel-check-oracle` | the broken variants fail for the reasons predicted, attributably |
| **a person** | whether a rename is really semantic identity; whether a derivation matches the definition; whether `not_deployed` is truthful rather than convenient; whether the gaps are real and their owners accountable |

That last row is not a gap in the tooling. It is the work the tooling exists to
make finite: a reviewer reads a bounded list of explicit claims — 36 fields and
four declarations — instead of inferring the mapping from arbitrary adapter code.

Two specific things a reviewer should look at first, because the lint passes them
by construction:

* **A `derive` whose rationale is plausible prose that was never implemented.**
  The digest proves a file exists and has not moved. It does not prove that file
  does what the sentence beside it says.
* **An optional input declared absent because extracting it was inconvenient.**
  Structurally identical to one that genuinely does not exist. The lint warns on
  every declared absence for exactly this reason, and the warning is addressed to
  a human, not to the agent.

---

## A gap record, for reference

The worked example has no gaps — it is an example of a complete mapping, and
should stay one. This is the shape when you cannot get there:

```json
{
  "outcome": "gap",
  "owner": {
    "role": "Director of Data Platform",
    "tracking_ref": "RVB-4182"
  },
  "blocker": "Raw app events are retained for 30 days, so no historical source can cover the analysis window."
}
```

`role`, not a name: it stays accurate across the staff change that makes a name
stale, and this artifact is meant to be shareable. `tracking_ref` points into
your own tracker, where the person lives.

---

## Specs 2 and 3

Not written yet. Spec 2 (choose your feature set) and spec 3 (fit and gate) follow
the same pattern — the adopter's agent, our checks — and the checks they need
already exist: the two feature guards for spec 2, the gate suite for spec 3. Spec
1 ships first because it is the step that blocks every other one.
