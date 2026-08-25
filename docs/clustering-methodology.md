# Behavioural subscriber clustering: the method, and why it is built this way

This is the reasoning layer. It is for the person who has read what the engine
does and wants to know *why* — why behaviour-only inputs, why the number of
clusters is derived rather than chosen, why an instrumentation gap gets two
different remedies depending on which kind it is, and which of the numbers in
here are theirs to set.

It is deliberately not a description of this software. Five documents already do
that, and this one defers to them rather than restating them:

| For | Read |
|---|---|
| The six steps to a first run | [adopter-path.md](adopter-path.md) |
| What the model publishes, and the guards on what may become a feature | [engagement-lane.md](engagement-lane.md) |
| The shape of the data you deliver, and what each concept means | [canonical-input-contract.md](canonical-input-contract.md) |
| The four decisions only your newsroom can make, as questions with owners | [declarations-questionnaire.md](declarations-questionnaire.md) |
| Setting your own thresholds and candidate cluster counts | [gate-configuration.md](gate-configuration.md) |

The method is reproducible without this engine. Everything below is expressed as
a rule you could implement in any language against any warehouse. Where this
package makes a particular choice, it says so and says why, and the choice is
separable from the method.

**A note on the numbers in this document.** Every threshold named here is named
by the field that holds it, and every level shown is rendered from the code
rather than typed into the prose — because a methodology document that retypes a
threshold becomes a second source of truth for it, and the copy is the one people
read. The levels are one deployment's. Which of them are yours to set, and how,
is [gate-configuration.md](gate-configuration.md).

---

## 1. Purpose and audience

This method builds interpretable behavioural subscriber segments from first-party
engagement data, for a newsroom whose available variables are similar in meaning
but not identical in name, source system, or completeness to anybody else's.

The goal is not to reproduce a set of column names. It is to reproduce the
analytical structure:

- build subscriber-week behavioural snapshots;
- cluster on behaviour-only signals;
- handle instrumentation breaks explicitly, and differently depending on what
  kind of break they are;
- derive the number of clusters from pre-declared screens rather than choosing it;
- establish that the screens' verdict is reproducible before believing it;
- name clusters by behavioural fingerprint rather than by number;
- keep algorithmic clusters separate from leadership-facing segments;
- join donor, subscription, revenue, churn, renewal and every other outcome only
  after assignment.

The output is a segmentation surface that can support audience strategy, product
analysis, donor or membership profiling and stakeholder communication without
turning outcomes into model inputs.

## 2. Core principles

**Behaviour first.** Use observed first-party behaviour as clustering inputs.
Never subscription status, revenue, payment, entitlement, donation, renewal,
churn, lifecycle state, marketing label or an editorially desired persona.
Outcomes are for validation and interpretation *after* clusters are assigned.

This is the one principle that has to be enforced rather than intended, because
the pressure to violate it is constant and the violation is invisible in the
output: a model that had donor status as an input produces clusters that separate
donors, and the finding reads as a discovery. This package enforces it with two
guards over the feature names themselves, described in
[engagement-lane.md](engagement-lane.md). Whatever you build, build the
equivalent.

**Freeze what you publish.** Fit transformations, centres, scales and centroids
once for a named version. Score future subscribers by projecting onto the frozen
version. Do not recalibrate means, scales or centroids on every scoring run — a
model that re-fits on each run has no versions, so nothing can be compared across
time and no result can be reproduced.

**Make data-quality boundaries explicit.** If a source changed, broke, backfilled
or became reliable only after a known date, that is an instrumentation floor and
it belongs in the record. Section 8 is about the two different kinds and why they
need different handling.

**Keep clusters and segments distinct.** A cluster is what the algorithm found. A
segment is a human-facing grouping after documented overlays or collapses.
Collapsing is legitimate; collapsing without saying so is how an algorithmic
result and an editorial preference become indistinguishable six months later.

**Prefer stable and interpretable over clever.** A simple model with strong
screens is better for newsroom collaboration than an opaque one that is hard to
explain, reproduce or govern. This is not modesty. The binding constraint on a
segmentation is whether anyone acts on it, and nobody acts on a group they cannot
describe.

## 3. The portable data model

Use a subscriber-week panel: one row per subscriber per week-ending date.

| Field role | Required? | Description |
|---|---|---|
| Subscriber key | yes | Stable person or account identifier used for behavioural aggregation. |
| Week-ending date | yes | The snapshot's week anchor. See below — this is not a free choice. |
| Fit eligibility | yes | Whether this row is in the population the taxonomy is fit on. |
| Scoring eligibility | yes | Whether this row may be projected onto a frozen taxonomy. |
| Per-source observed flags | strongly recommended | What distinguishes true zero behaviour from missing instrumentation. |

Build the panel from atomic behavioural facts — article views, app views, active
days, section or topic visits, comments, reactions, email clicks, push clicks —
never from outcome labels. This package's own version of that shape, field by
field, is [canonical-input-contract.md](canonical-input-contract.md).

### The week anchor is a declaration, not a convention

The obvious advice is "pick one weekday and use it consistently". That advice is
insufficient, and the reason is worth the paragraph.

In the system this method is ported from, **two week-anchor conventions were in
live use at once.** One lane ran weeks that *end* on a Sunday; another ran weeks
that *start* on a Sunday. The same calendar date is week-ending in one and
mid-week in the other, so the two disagree by up to six days. Nothing in that
system stated that both existed. There was no shared helper, and no test covered
the difference.

Consider what a wrong inheritance does. Every weekly bin shifts by six days
against the day boundary. Every count is still plausible. Every distribution
still looks like a distribution. No test fails. The model fits, the clusters are
interpretable, the segments get named — and they describe weeks nobody declared.

So the anchor is **data that travels with the delivery**, declared as a weekday
*plus which end of the week it sits on*, and the code carries no default for
anything to fall back on. This package refuses to run without it; the module that
resolves it deliberately has no module-level default. If you build your own, the
lesson is the absent default rather than the particular weekday.

Windows, once the anchor is fixed:

| Window | Definition | Use |
|---|---|---|
| Current week | the seven days ending on the anchor | Recent activity, temporal checks. |
| Trailing window | four whole weeks ending on the anchor | The main behavioural features. |
| Weekly bins | four non-overlapping seven-day bins inside the trailing window | Cadence and habit features. |

Four whole weeks, so the bins tile the window exactly — a thirty-day window
leaves two days in no bin.

**On a different publishing cadence.** If your newsroom's natural rhythm is daily
or monthly rather than weekly, the *concept* ports: one consistent snapshot
grain, a short current period, and a longer trailing period that smooths
day-to-day volatility. Be aware that this package's implementation does not yet
express a different one — the window width and its four bins are fixed in code.
That is a prescription, it is named as one in
[gate-configuration.md](gate-configuration.md), and it is the largest remaining
one. The method is more portable than this implementation of it.

## 4. Feature families

Map your local variables into conceptual roles before writing any model code.

| Family | What it measures | Good candidates | Never an input |
|---|---|---|---|
| Consumption or intensity | How much content the subscriber consumes. | Article views, app views, session days, active days, engaged reads. | Paywall hits, subscription tier, campaign exposure, raw email volume sent. |
| Breadth or diversity | How widely they range across coverage. | Distinct sections, distinct topics, distinct authors, section or topic entropy. | Editorial tags missing for large shares of content with no coverage correction. |
| Participation or community | Whether they take participatory action. | Comments, replies, reactions, community posts, moderated contributions. | Passive exposure, unverified social impressions, platform artifacts. |
| Cadence or loyalty | Whether behaviour repeats across recent weeks. | Weeks with reads, weeks with clicks, return weeks. | Sends, impressions, opens alone. |
| Reachability only | Whether they *can* be reached, engaged or not. | Email opens, push impressions, delivery status. | Never in clustering. Overlays and diagnostics only. |
| Outcomes | Business or mission results. | Donations, renewals, churn, upgrades, event attendance, survey responses. | Never in clustering, transformations, cluster-count selection or naming. |

The variables differ by newsroom. The test that transfers is whether a candidate
answers a behavioural question that exists *before* any desired outcome is known.

The reachability row is the one people argue with, so: an open is a statement
about deliverability and rendering, not about a person. Treating it as engagement
means the most engaged readers are the ones whose mail client loads images.

## 5. The variable mapping worksheet

Fill this in before fitting anything. The columns matter as much as the rows —
especially the last two, which are where a clean feature surface is usually lost.

| Construct | Your variable | Transformation | Observation-quality check | Allowed fallback | Forbidden substitute |
|---|---|---|---|---|---|
| Web consumption | | `log1p`, then standardise | Tracking stable across the fit window; no large missing periods. | Total views across web and app if the platform split is unavailable. | Subscription plan, paywall meter state. |
| App consumption | | `log1p`, then standardise | App instrumentation coverage known and stable. | Omit if there is no app product. | App install alone. |
| Active days | | `log1p`, then standardise | Day-level facts reliably deduplicated. | Active weeks if daily data is unavailable. | Marketing send count. |
| Breadth | | `log1p`, then standardise | Section or topic coverage high enough not to bias. | Distinct sections if topic classification is incomplete. | Preferred vertical chosen at signup. |
| Diversity | | standardise the entropy | Enough events per row for diversity to mean anything. | Omit if the taxonomy is too sparse. | Most recent topic alone. |
| Participation volume | | `log1p`, then standardise | Community source has a known launch and reliability date. | Comment active days if action counts are noisy. | Reading comments without acting. |
| Participation cadence | | `log1p`, then standardise | Action timestamps reliable. | Omit if there is no participation product. | Account age. |
| Click cadence | | standardise the active-week count | Capture stable after any known floor. See section 8. | Active reading weeks if clicks are unavailable. | Opens alone. |

If a construct has no credible local variable, **leave it out.** A smaller clean
surface beats a larger one that mixes behaviour with business labels.

### Declare what one row of each source counts

This is the row of the worksheet that is easiest to skip and most expensive to
skip, so it gets its own heading.

Take email clicks. "Click cadence" can mean weeks in which the reader produced at
least one click *event*, or weeks in which they clicked at least one distinct
*campaign*. Both are defensible. They are different numbers, and a document
describing one while the table computes the other is a defect that survives every
check — which is exactly what happened in the system this ports from, where a
docstring claimed the campaign meaning long after the table counted events.

This package decides it, as data rather than as prose: one row of email click is
**a click event, not a distinct campaign clicked** (`EMAIL_CLICK_UNIT`), and that
decision is **folded into the model version** string, because it moves the
numbers.

What is worth knowing is *where* it moves them, because the intuition is wrong.
**The cadence axis is invariant to the unit.** Cadence counts weeks containing a
non-zero bin, and any week containing one click has a non-zero bin under either
definition. So are the click-day counts. What actually moves is click *volume*,
which reaches the model through the intensity family and is **log-transformed** on
the way — so the effect is real, bounded, and not where the old framing said it
was. Anyone debugging a cadence difference by looking at the click unit is
looking in the wrong place.

Do this for every source: write down what one row counts, put it in the version,
and state which axes it moves.

## 6. Eligibility and the spine

Define the clustering universe before modelling. Typical choices: paying digital
subscribers; registered users with an active subscription during the fit period;
active members of a membership programme; all known users with a stable
identifier.

Write down:

- whether the fit universe is point-in-time or a current snapshot projected
  backward;
- whether institutional, group, guest, trial and anonymous users are included;
- whether users with no recent observed behaviour are fit on, or handled by
  overlay;
- **whether a missing source row means zero behaviour or unknown behaviour.**

Then:

1. Build a complete subscriber-by-week spine over the fit universe.
2. Left join behavioural aggregates by source and week.
3. Carry observed flags by source wherever you can.
4. Convert missing to zero **only** where the source was known to be observed for
   that subscriber and period.
5. Keep unobserved source periods out of calibration, or out of the affected
   feature — never silently zero.

Step 5 is the one that matters, and section 8 is why.

The questions in this section are four of the decisions
[declarations-questionnaire.md](declarations-questionnaire.md) asks with named
owners, because they are not analyst choices.

## 7. Transformations and calibration

For count-like variables:

```text
z_feature = z_score(log1p(raw_count))
```

For bounded or already continuous variables such as entropy:

```text
z_feature = z_score(raw_value)
```

For cadence:

```text
cadence   = count of weekly bins in the trailing window containing a meaningful action
z_cadence = z_score(cadence)
```

Calibration rules:

- Fit each feature's centre and spread on the documented fit population.
- Store the centre, spread, feature list and **feature order** as versioned
  artifacts. Order, because a matrix assembled in a different column order scores
  every reader against the wrong centroid and raises nothing.
- Use the frozen calibration for all future scoring.
- **If a signal has a source-specific quality floor, fit only that signal's centre
  and spread on the post-floor rows.**
- Do not refit every signal on a short post-floor window unless every signal
  shares the same instrumentation problem.

That fourth rule is the most portable lesson available from the re-freeze this
method came out of. A defect in one signal family should not force the whole model
to forget valid history from every other family. The instinct is to move the
entire fit window forward to the date the broken signal became trustworthy, and it
is the wrong instinct: it throws away good data to compensate for bad, and shrinks
the fit population for reasons unrelated to most of the features.

## 8. Instrumentation floors, and the two kinds

An instrumentation floor is a date before which a signal cannot be trusted as
comparable to later observations.

Common causes: event tracking launched on a known date; identity stitching
changed; vendor backfill behaviour changed; a table started deduplicating
correctly; a click, comment, app or push pipeline was incomplete before a fix;
taxonomy coverage improved after a workflow change.

How to find them: plot weekly active users by source; plot weekly action counts by
source; compare raw source counts against curated-table counts; look for ramps
that match deployment or backfill timelines; and then ask the question that
actually decides it — *is this plausibly audience behaviour, or is it
instrumentation?*

### The distinction that changes the remedy

This is the part most treatments of the subject get wrong, including the earlier
version of this document. "Instrumentation floor" names two different defects that
need two different remedies, and one joint fit window frequently cannot satisfy
both.

**An under-capture ramp.** The data exists across the whole period and is not
comparable across it. In the case behind this method, raw email clicks were
present for well over a year, but the count of weekly distinct clickers ramped
from roughly two thousand to roughly thirteen thousand before plateauing. Nothing
was missing. Everything was undercounted, by an amount that changed every week.

The remedy is a **signal-specific calibration floor**: fit that signal's centre and
spread on post-floor rows only, and leave every other feature on its full valid
history. The signal's *values* stay in the panel; what changes is the distribution
they are standardised against.

**A structural absence.** The data does not exist. In the same case, the
community-participation source had **zero rows** before its integration went live.
Not undercounted — absent. No backfill recovers a comment that was never recorded.

The remedy is not calibration, because there is nothing to calibrate. It is
**consumer-side feature handling**, and there are three honest options: start the
analysis window at the floor; drop the community family from the fit; or impute
and carry a flag that says you did. What is not an option is the zero. A reader
with no community feed is not a reader who never commented, and a model fit on
that distinction learns the shape of the publisher's vendor contracts.

This package expresses the difference in the delivery's own manifest rather than in
analyst judgement: an input declared available with a coverage floor is the ramp
case, and `not_yet_launched` with a floor date is the structural case — a block
dropped only for windows reaching back past the floor. `not_deployed` is the
permanent version. The mechanics are in
[canonical-input-contract.md](canonical-input-contract.md).

### And they will not line up

Here is the honest part, and it is the reason this section exists rather than a
single handling rule.

In the case behind this method the two floors were about **six months apart**.
There is **no single joint fit window** that satisfies both cleanly. Start at the
later floor and you discard half a year of good consumption, breadth and cadence
history to accommodate one family. Start at the earlier one and the community
family is structurally absent across the first stretch of the panel.

Neither choice is wrong. What is wrong is making it silently. State which floor
governs the window, state what the other one costs, and record the choice with the
model version — because the two surfaces this decision produces are not
comparable, and the only thing that will make that visible later is having written
it down.

**And do not choose a floor by looking for the most flattering segment story.** The
floor date is a data-quality finding. If which floor you pick changes the headline,
that is a fact about the fragility of the headline.

## 9. Deriving the number of clusters

This is the section the method lives or dies on, and it is where the earlier
version of this document was most out of date.

The production labeller is k-means on the standardised matrix. The comparator is
Ward agglomerative clustering, or any independent distance-based method. What
matters is the selection rule around them.

### The rule

1. Freeze the candidate feature matrix and the preprocessing version.
2. Fit every candidate k in your declared range.
3. For each k, fit from many starting points.
4. Fit the same k with the independent comparator.
5. Screen each k on seed stability, cross-algorithm agreement and centroid
   distinctness.
6. **Re-run those screens on many perturbed panels and require the survival rate
   to clear a bound.** Section 9.2.
7. The champion is the **smallest** surviving k.
8. If nothing survives, publish no taxonomy. That is a result.
9. Never use donor, subscription, revenue, churn or renewal outcomes to choose k.

Step 7 is not a preference for parsimony. A larger k always fits better, so "which
k looks best" has one answer and it is always the largest. Taking the smallest
survivor is what makes the screens the decision rather than a formality. And the
champion is therefore *derived*: nobody picks it, and a deployment that wants a
different k may only have one that also survives every screen.

### 9.1 The screens

<!-- gates:begin -->

| Gate | Field | This package's level | What it means |
|---|---|---|---|
| Seed stability | `seed_ari` | 0.7 | Median pairwise agreement between fits of the same k from different starting points. Below it, the groups are a property of where the fitting started rather than of the readers. |
| Cross-algorithm agreement | `cross_algorithm_ari_by_k` | per k: 8 bars, 0.46 at k=3 falling to 0.31 at k=10 | Agreement between the production labeller and an independent second algorithm at the same k. Per k, and derived on your own panel -- the one threshold here that cannot be inherited. See section 9.3. |
| Centroid distinctness | `centroid_distinctness_corr` | 0.9 | Correlation above which two cluster profiles are one cluster reported twice. |
| Smallest cluster share | `tiny_cluster_floor` | 0.01 | Below it, a cluster is an incidental group rather than a segment. |
| Cluster persistence share | `major_cluster_share` | 0.01 | The share at which a cluster must appear under every seed. Kept equal to the floor above on purpose. |
| Temporal retention | `t4_retention` | 0.45 | Share of readers keeping their label between two windows far enough apart to share no days. |
| Temporal profile similarity | `t4_profile_similarity` | 0.8 | Correlation between matched cluster profiles across the same gap. |
| Selection survival | `selection_survival_floor` | 0.5 | One-sided lower bound the all-screens survival rate must clear across perturbed panels. What makes the verdict reproducible rather than a property of the one matrix a run assembled. See section 9.2. |
| Topic coverage | `topic_coverage_floor` | 0.8 | Share of reading whose section resolves. Blocks the topic block alone, not the whole run. |

<!-- gates:end -->

The levels in that table are **one deployment's**, **rendered from** this
package's defaults so this page cannot drift from its code. Setting your own is
[gate-configuration.md](gate-configuration.md).

Two of them need their reasoning stated rather than their number.

**Temporal retention and profile similarity are measured across a gap wide enough
that the two windows share no days.** Adjacent weeks share three quarters of a
four-week window, so adjacent-week agreement is mechanically high whatever the
model does. Gating on it would certify a model for arithmetic it cannot avoid.
Measure it, by all means — as monitoring, labelled as monitoring.

**The smallest-cluster floor and the persistence share are the same number on
purpose.** If the floor for mattering is lower than the share at which a cluster
must persist across seeds, a cluster can be simultaneously too small to matter and
required to persist, and the two screens contradict each other.

### 9.2 The screens' verdict is not automatically reproducible

This is the single most important thing in this document, and it was absent from
the earlier version.

A screen computed on the one matrix a run happened to assemble is not a
reproducible verdict. Measured on a real refit of just over five thousand rows:
dropping **two rows** at random moved one candidate's seed-stability from **0.97**
on the full matrix to between **0.49 and 0.66** on every one of twenty arbitrary
two-row drops. The cross-algorithm statistic moved as far in the other direction,
spanning 0.30 to 0.71 at one k across the same drops. **The set of surviving k
changed in 20 of 20 trials.**

Two rows in five thousand. And the candidate whose stability collapsed was, being
the smallest survivor, the champion — so the published number of clusters rested on
a statistic that a rounding error in the panel would have changed.

None of that is estimator noise. It is a real property of the matrix at a
resolution finer than a pipeline can hold between freezes: the panel is a sample,
and the next equally valid panel is two rows different.

The remedy does not touch a threshold. Every screen keeps its level. What changes
is the object the verdict is about:

> Re-screen each candidate k on many **perturbed panels** — the same panel with a
> tiny fraction of rows dropped — and admit it only if the **one-sided 95%** lower
> bound on its all-screens survival rate clears a floor.

A lower bound rather than a point estimate, because the question is whether the
evidence supports the claim, not whether the sample happened to favour it. Fifty
survivals out of fifty still yields a bound below one, which is the point: strong
evidence, not proof. Use the **Wilson** interval rather than the normal
approximation, which is worst exactly where this is used — rates near zero and one.

The floor is a majority: the candidate has to survive more panels than not, with
confidence. And the diagnosis matters as much as the verdict, so report each
screen's own pass rate separately. A k where every screen holds on its own but
rarely all at once has a distinct failure mode from a k that fails one screen
outright, and only the per-screen rates tell them apart.

**This costs `draws × seeds` extra fits per candidate** — minutes rather than
seconds. A freeze is a rare event, and the thing it replaces is a verdict that did
not reproduce, which is the more expensive of the two.

### 9.3 The cross-algorithm bar: derive it, do not inherit it

The cross-algorithm screen asks whether an independent algorithm finds the same
groups. It needs a bar, and this is the one threshold in the method that **cannot
honestly be carried from anybody else's analysis.** The reason is a measurement,
not a preference.

A bar on an agreement statistic means nothing until you know what agreement the two
algorithms reach **by chance**. The intuition is that unstructured data produces
agreement near zero, so any bar comfortably above zero is safe.

The intuition is wrong. k-means and Ward share an objective — both prefer compact,
roughly equal-variance groups — so they cut a featureless cloud along similar
surfaces. Measured against a null population with **no cluster structure at all**,
they agree at an adjusted Rand index of roughly **0.26 to 0.31** on average. A bar
that was thought to sit comfortably above chance turned out to sit one to two null
standard deviations above a chance level nobody had established.

Two consequences, and they are separate.

**A flat bar is the wrong shape.** Chance agreement *falls* as k rises, and the
spread of the null narrows with it. So one number demands the same absolute
agreement at a high k as at a low one while the floor beneath it has dropped and
the null's own spread has fallen by a factor of three. A flat bar certifies high k
too easily and refuses low k too readily. The bar has to be **per k**.

**And it is the wrong level for anybody else.** Chance agreement depends on the
**row count**, on the **dimensionality**, and on the population's own **correlation
structure** — not on k alone. Measured on one publisher's data, the same derivation
gave bars about 0.10 higher on a **six-feature** panel than on a **nine-feature**
one. A bar **carried across feature spaces** reports a pass it has not earned. If
your feature set changes, **derive again**.

So derive it. The rule:

1. A **null replicate** is a matrix of your panel's own shape with no k-cluster
   structure in it.
2. Two constructions are worth knowing. **Column permutation** shuffles each
   feature independently: it preserves every marginal exactly and destroys all
   joint structure. A **covariance-matched draw** samples from a distribution with
   your panel's mean vector and full covariance: one unimodal population,
   correlated the way your readers actually are, with no clusters in it.
3. **The covariance-matched null governs.** This is a judgement and it is the most
   consequential one in the derivation, so state it and defend it rather than
   letting it follow from convenience. The hypothesis the screen exists to exclude
   is not "these features are independent noise" — no real subscriber panel looks
   like that, and calibrating against it would be calibrating against a straw null.
   It is "this is one population with the ordinary correlation structure of reader
   behaviour, and the k groups are an artifact of cutting it."
4. On each replicate compute **exactly the statistic the screen computes**, from
   the same code path. A derivation that re-implements the statistic calibrates
   something else.
5. **The bar at k is the upper 95th percentile of the pooled null distribution**,
   rounded up. One-sided 95%, matching the confidence convention the survival bound
   in 9.2 already uses, so the selection rule holds one notion of confidence rather
   than two.
6. If the derived bars span very little across k, a single scalar equal to their
   maximum carries the same information and the per-k table is noise.
7. **A k with no derived bar is not screened against an inherited number. It
   refuses.**

What the bar then means: *a k passes when the two algorithms agree more than they
would on an unclustered population of the same size, dimension and covariance, at a
5% false-certification rate.*

Why the derivation cannot be tuned to pass: it **never observes the real
statistic**. It reads only replicates, and depends on your panel solely through its
shape and its covariance. Your panel's actual agreement at any k is not an input at
any point, so there is no path by which a verdict you would prefer could steer the
number.

**Two controls before you use it.** A *positive* control: on a synthetic panel of k
well-separated blobs the statistic must score near one and clear the derived bar —
a statistic blind to real structure cannot calibrate anything. And a *negative,
non-circular* control: **held-out** replicates drawn under a different seed from
the ones that set the percentile must clear the bar at about the complement of the
quantile. Measuring that rate on the replicates that defined the percentile is
**circular** and proves nothing.

In this package, `tools/derive_cross_algorithm_bars.py` implements all of it and
refuses to emit a bar unless both controls pass.

**One warning about what a correctly calibrated bar does.** It is *weaker* than the
flat bar it replaces, often much weaker. That is not a regression. The apparent
selectivity of an inherited bar was noise — the same measurement in 9.2 shows those
verdicts flipping on a two-row change — so what a derivation replaces is a screen
that looked strong and was arbitrary with one that is weak and correct. Expect seed
stability and centroid distinctness to do most of the work. Knowing which screen is
actually binding is worth more than a comforting number.

### 9.4 Choosing the candidate range

**This method prescribes no candidate grid, and neither should your write-up of
it.** The range is a **declaration per deployment**, and the two ends are different
judgements.

**The floor** is the smallest number of segments that could be real for your
audience. For most newsrooms that is **two**. A small readership, or one that
splits sharply into subscribers who read and subscribers who merely pay, may
genuinely have two groups, and a method that cannot express that answer is not
neutral about it. **Two clusters is a legitimate result**, not a configuration
error.

**The ceiling** is where segments stop being things a person can act on. In
practice the smallest-cluster floor usually binds before interpretability does:
past some k, the extra clusters are small splits of an existing group rather than
new archetypes. Set the ceiling by what your organisation can hold in its head and
let the screens refuse the rest.

Neither end is a property of the method. This package's default sweep — visible in
`LaneConfig.k_grid` — is **one newsroom's range** and appears as an example, not a
recommendation. Declaring your own, and declaring a bar for each k in it, is
[gate-configuration.md](gate-configuration.md).

## 10. Naming clusters

Never name a cluster by its number. Cluster ids are arbitrary and change between
runs; two fits can find identical partitions and number them differently.

Name from centroid fingerprints:

1. Identify which feature family each centroid over-indexes on.
2. Give it a behavioural name that describes that fingerprint.
3. Check the name still holds across time and on projected samples.
4. Avoid names implying value or business outcome.

Portable examples: web loyalists; app loyalists; community participants;
newsletter regulars; broad explorers; light readers.

Describe behaviour, not worth. "Light readers" is both more accurate and more
usable than "low value" — the first suggests an action, the second suggests a
write-off, and the model knows nothing about worth.

**A cluster nobody can describe is not a segment.** This package makes that a
publication gate: without a recorded human review the run writes its tables and
declines to publish labels. Shipping an unnamed cluster has to be a deliberate act.

## 11. Collapsing clusters, and overlays

The algorithm may find more clusters than stakeholders need. Collapsing two into
one leadership-facing segment is legitimate when all of these hold:

- they are adjacent in centroid space;
- the distinction is intensity rather than behaviour *type*;
- both would produce the same recommended action;
- the collapse is documented and reproducible.

Collapse *after* selection, never by choosing a smaller k. Those are different
operations: the first is an editorial simplification of a result that survived its
screens, and the second is a modelling decision made for a presentational reason.

Overlays are rule-based groups outside the clustered core, and they make a business
readout exhaustive without polluting the model: *reachable only* — recent opens,
impressions or deliveries but no modelled engagement; *dormant* — no recent
observed modelled engagement; *insufficient data* — cannot be scored because a
required source is unobserved or identity is unresolved.

Reachability signals may define an overlay. They may never define a cluster.

## 12. Outcome validation

Join outcomes *after* assignment: donations and donor conversion, renewal and
churn, upgrades and downgrades, event attendance, later newsletter signup, survey
response.

Rules:

- Join only after cluster assignment.
- Do not revisit feature choices, cluster count or names to improve outcome
  separation. This is the rule the whole method is arranged to protect, and it is
  broken by iterating, not by cheating.
- Present differences as correlation, not causation.
- Include denominators and missingness.
- Report current and future outcomes separately where you can.

Outcome validation answers *do these behaviour groups differ in useful ways?* It
does not answer *did being in this group cause the outcome?*

## 13. The engagement score, and why there is more than one

A segmentation can coexist with a continuous engagement score. Derive it from the
same behaviour-only matrix and keep it out of cluster selection.

Three constructions are reproducible from that matrix:

| Construction | Method | Its weakness |
|---|---|---|
| Principal component | First principal component of the standardised signals, sign-oriented so higher means more engaged. | Dominated by whichever family has the most correlated columns. |
| Variance-weighted composite | Weight family means by the share of each family's variance the clusters explain. | Overweights whichever family best separates clusters, which is circular if the clusters are the thing being explained. |
| Block-structured | A sub-score per family, each re-standardised, then averaged with equal weight. | Says every behavioural dimension counts the same, which is a value judgement — just an explicit one. |

**The earlier version of this document recommended the third. That advice was
wrong, and the reason it was wrong is the interesting part.**

**Publish all three.** They **agree at the extremes and disagree in the middle**,
and where they disagree the reason is *interpretable*: a heavy commenter who reads
little ranks far higher on the block-structured score than on the principal
component, because the first says participation counts as much as volume and the
second says the dominant correlated block wins. That disagreement is a real finding
about a real reader. Publishing one number hides it behind a false precision, and
the reader who most needs explaining is exactly the reader the three constructions
disagree about.

Persist the block-structured score's **sub-scores**, not only the composite, for
the same reason: a composite that can be read as its parts can be argued with.

Percentiles are the one thing to compute within the scored population rather than
against a frozen reference — "top 10% of engaged readers" means top 10% of the
people who are here now, and a percentile against a frozen distribution drifts as
the audience grows until it describes a population that no longer exists.

This package publishes all three, with the sub-scores; see
[engagement-lane.md](engagement-lane.md).

## 14. Reproduction checklist

**Before modelling**

- [ ] Define the fit population and the scoring population.
- [ ] Build the subscriber-week spine.
- [ ] Declare the week anchor — weekday *and* which end of the week it sits on.
- [ ] Map local variables into families.
- [ ] Declare what one row of each source counts.
- [ ] Mark the forbidden outcome and entitlement variables.
- [ ] Identify each source's instrumentation floor, and **which kind it is**.
- [ ] Declare transformations, the candidate k range, and every screen level.
- [ ] **Derive the cross-algorithm bar on your own panel**, and record the controls
      that let you use it.

**During modelling**

- [ ] Fit transformations on the declared fit population.
- [ ] Apply source-specific floors only where justified; state which floor governs
      the window and what the others cost.
- [ ] Fit every candidate k in the declared range.
- [ ] Fit from many starting points.
- [ ] Fit the independent comparator.
- [ ] **Re-screen every candidate on perturbed panels and read the verdict off the
      survival bound**, not off the single matrix.
- [ ] Take the smallest surviving k. If none survives, freeze nothing.
- [ ] Freeze feature order, centres, scales, centroids, labels and version
      metadata.

**After modelling**

- [ ] Name clusters from centroid fingerprints.
- [ ] Collapse only with documented evidence, and only after selection.
- [ ] Add overlays for reachable-only, dormant and insufficient-data readers.
- [ ] Join outcomes only now.
- [ ] Produce a readout showing data health, feature distributions, screen results
      **with per-screen survival rates**, centroid profiles, temporal stability,
      segment shares and outcome profiles.

**Publish only when**

- [ ] the inputs are behaviour-only;
- [ ] the instrumentation floors are documented, by kind;
- [ ] the chosen k survives the screens **on perturbed panels**;
- [ ] the cross-algorithm bar was derived on this panel, not inherited;
- [ ] the labels are stable and a person has reviewed and named them;
- [ ] the readout is reproducible from frozen artifacts;
- [ ] the outcome analysis is labelled post-hoc.

## 15. A minimal readout outline

1. **Guardrails and status** — population, dates, families, forbidden inputs, known
   caveats.
2. **Data health** — source coverage, instrumentation floors *and their kinds*, row
   counts, missingness.
3. **Feature health** — distributions before and after transformation.
4. **Bar derivation** — the null, the derived bars, both controls.
5. **Model selection** — the candidate range, each screen, the per-screen and joint
   survival rates over perturbed panels, and the champion.
6. **Cluster interpretation** — centroid fingerprints, family profiles,
   plain-English names.
7. **Segment binding** — collapses and overlays.
8. **Stability** — retention and profile similarity across a *disjoint* gap.
9. **Engagement scores** — all three, and where they disagree.
10. **Outcome profiling** — joined only here.
11. **Caveats** — data limitations, survivorship, and what must be frozen to serve.

## 16. One newsroom's case

This appendix records how the method mapped to the case it was developed on. It is
evidence that the method has been run, not a template to copy. Every variable below
is that publisher's; none of it is a requirement.

**Population and grain.** Subscriber by week-ending date. Active individual paid
subscribers. A four-week trailing feature window.

**Families as instantiated**

| Family | That publisher's variables |
|---|---|
| Consumption or intensity | Web views, app views, section views, active days. |
| Breadth or diversity | Distinct sections, topic entropy. |
| Participation or community | Community action volume, community active days. |
| Cadence or loyalty | Recent weeks containing email clicks. |
| Reachability overlay | Email opens, as an "inbox only" business bucket. |
| Outcomes | Donor status and donor rate, joined only after assignment. |

**The two floors.** Email click capture was an under-capture ramp: the data existed
throughout and was not comparable across it, so only the email cadence signal's
calibration was fit on post-floor weeks and every other family kept its fuller
valid history. Community capture was a structural absence: zero rows before the
integration went live. The two floors sat about six months apart. Section 8 is that
experience generalised.

**Selection, as it stands on the published surface.** The currently published
version of that model has a champion of **five clusters, derived** — the smallest k
that survived every screen under the perturbation rule in 9.2. It **needs no
hand-set cluster count**, and the product override that reaching five clusters once
required is **no longer load-bearing**.

**The superseded readout, and why it is worth recording.** An earlier version of
that model published **six clusters**, of which two were light-reader groups
subsequently collapsed into one leadership-facing segment. That readout is
**superseded**, and the reason is instructive rather than embarrassing: the sixth
cluster's survival rested on precisely the kind of statistic later shown to **flip
on a two-row change** in five thousand. The narrative — "six clusters survived, so
we collapsed two of them" — was a description of one matrix, not a finding. It is
recorded here because the earlier version of this document presented it as the
published surface, and because reading a superseded k as a result is the most
likely way to misuse this method.

**Donor profiling — read the scope of this carefully.**

**The robust claim**, which holds on both surfaces: **more engaged readers are more
likely to donate.** Correlational, unsurprising, and stable.

The specific ranking is *not* robust, and was **computed on the segments of the
earlier**, six-cluster version — **not on the published surface**. On those
segments, correcting the unstable email signal moved the community segment to the
leading donor rate and the earlier email-led donor headline did not survive. That
is a real finding about that correction on those segments. It is **not** a
statement about the published surface, which re-froze at five clusters with names
re-bound by membership, and it **has not been recomputed** there.

The two are separated here on purpose. A version-specific ranking restated flatly
becomes a fact about the world, and this one is a fact about a superseded
segmentation. If you take one thing from this appendix, take the habit of writing
the surface a conclusion was computed on next to the conclusion.

## 17. Provenance

This method was synthesised from a standalone clustering specification, a notebook
readout, and the clustering contracts and implementation notes of the system this
engine ports from. Those documents are internal to that publisher and are not
distributed with this package; the main body above is self-contained so that
nothing in it depends on reading them.

Where a claim in this document rests on a measurement, the measurement was made on
that publisher's own panels, and the sizes and shapes are stated inline — because a
measured claim whose population is unstated is not a measured claim.

What the reader is expected to take from this document is the structure and the
reasoning. Every number in it is either rendered from this package's code, or
labelled with the panel it was measured on, or named as **yours to set**.
