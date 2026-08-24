# Rehearsal log — and why this is not the walkthrough

> **This is the adopter path**, or rather a record of walking it. For working *on*
> this repository instead, see
> [Getting started for contributors](../README.md#getting-started-for-contributors).

## What this is not

The adopter path is only proven by a person who has not worked on this repository
following it on a clean machine and reaching a built intermediate table. **This is
not that.** It was performed by an author of the repository, and an author already
knows the answers — which is precisely the knowledge the exercise is supposed to
be missing. Every stall a stranger would hit at a fork in the wording, an implicit
assumption, or a term used before it is defined is invisible from here.

So read this as a rehearsal: it removes the mechanical failures a stranger would
otherwise have wasted a morning on, and it removes none of the comprehension
failures. Those are what the real walkthrough is for, and it is still outstanding.

What a rehearsal can settle is narrower and still worth having: whether the
commands as written, in the order written, on a clean machine, do what the
document says they do. Four times, they did not.

## Setup

A fresh `git clone` into an empty directory, a new virtualenv, `pip install .`
from the clone, and then the document followed literally — copying each command
rather than typing what I knew it should be.

## What worked

* `pip install .` from a clean clone into a fresh venv: no failures, no cloud SDK,
  no compiler needed.
* `engagement-kernel-demo-dataset` then `engagement-kernel-validate`: `PASS: every
  table conforms`.
* `engagement-kernel-build-intermediate`: **seven daily aggregates and a build
  report** — the target the walkthrough has to reach. Reachable from a clean clone
  in three commands after the install.
* `engagement-kernel-demo-oracle` then `engagement-kernel-check-oracle`: six cases,
  all as declared.
* The engagement lane at default settings: a champion cluster count, 8,752 scored
  reader-weeks, a frozen bundle, and the interpretability gate correctly blocking
  publication.

## The four stalls, and what changed

### 1. The command the document recommended failed on the document's own example

Step 4c said to run the mapping lint with `--warnings-as-errors`. Run that against
the worked example this repository ships and it prints `FAIL: 0 error(s), 2
warning(s)`.

Both halves of that are bad. The advice was wrong: the lint warns on every
declared absence, declaring an absence is the *supported* answer, so any adopter
without all three optional inputs fails that flag while doing everything right.
And the output was wrong — "FAIL" beside "0 error(s)" reads as a defect in the
linter.

Worse than either, it points an adopter's coding agent at the wrong target. An
agent told to drive the warning count to zero has one obvious move available, and
it is to convert a declared absence into invented data.

**Changed:** dropped the flag from the recommended command in both documents, with
a paragraph saying warnings are addressed to a reviewer and are not defects. The
flag still exists for a pipeline that wants a sign-off enforced. The verdict line
now reads `FAIL under --warnings-as-errors: 0 error(s), 2 warning(s). The mapping
is structurally sound...` and names what a reviewer is being asked to do.

### 2. The suggested first-run settings produced no model

Step 6 offered `--k-min 3 --k-max 6 --seeds 3 --perturbation-draws 3` as a cheap
first look. On the 400-reader demo cohort that reports `champion k: none` and
freezes nothing.

That is the lane working correctly — no candidate cleared the stability screens,
so it refused to publish a fit it could not stand behind. But the document
presented those numbers as a reasonable first run and said nothing about the
outcome, so an adopter who followed it would reach an empty result with no way to
tell a correct refusal from a broken install.

**Changed:** the narrow sweep now carries the expectation. `champion k: none` is
information, `k_selection.parquet` records why each candidate was rejected, and
the defaults find a champion on the same cohort where the narrow sweep does not —
so widen before concluding anything about your data.

### 3. Four minutes of silence

The lane at default settings ran for 3m50s on 400 readers and saturated every
core, printing nothing between the configuration banner and the results. The
document said the defaults were "expensive on purpose" and left the reader to
discover what that meant while looking at a still terminal.

**Changed:** step 3 now states the runtime and that it has not hung.

### 4. "Refuse to publish" read as "produce nothing"

The document said to expect the lane to *refuse to publish*. It does — and it also
writes every output table, which is not what "refuse" suggests. An adopter could
reasonably see the files and conclude the gate had not worked.

**Changed:** the distinction is now explicit, with the gate line quoted verbatim.
The tables are written and readable; publication is what is blocked.

## What a stranger would still hit, that this cannot find

Listed because naming the blind spot is more useful than pretending it is small.

* **Vocabulary.** "Delivery", "spine", "feature block", "surface", "champion k",
  "the lane" all appear before they are defined. I cannot tell which of those
  stopped anybody, because none of them stopped me.
* **Step 4 is a cliff.** Steps 1–3 are commands; step 4 is a project. Whether the
  document conveys that shift, or whether an adopter arrives at it expecting
  another command, is exactly the kind of thing only an outside reader can report.
* **Whether the four questions get asked.** The design intends the questionnaire
  to be taken to other people. Whether a reader actually leaves their desk with it
  — rather than filling in something plausible to keep moving — is the single most
  important unknown here, and it is unobservable from inside.
* **Whether "you can start with all three absent" is believed.** It is stated on
  the front page and in three other places. Stating it and it landing are
  different things.

## What would settle it

One person who has not worked on this repository, a clean machine, and the
instruction to follow [the adopter path](adopter-path.md) from the top and stop at
the built intermediate table. Log every point they stall, ask, or guess. Fix or
document each one. Anything they had to ask a person is a defect in the document,
including — especially — the questions that feel too obvious to write down.
