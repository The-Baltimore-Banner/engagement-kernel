# Parity: what can be compared, and what cannot

This document exists so that the first comparison against the system this lane was
ported from is not debugged as a regression when it fails for the right reason.

**Parity is asserted structurally. It is not claimed numerically.** That is a
conclusion, not a shortcut, and the rest of this file is the argument for it.

---

## Why cluster membership is not comparable

Every feature reaching a model here is a z-score against a mean and standard deviation
fitted on a training panel. The centroids live in that standardised space. So the
space is a function of:

* the panel — which reader-weeks were sampled, which is a function of the seed, the
  entitlement rule and the calendar;
* the derivation of every input feature — a view counted from events against a
  declared article-view definition is not the same number as a view read from a
  pre-aggregated daily table;
* the day boundary and the week anchor, which decide which events fall in which bin.

Change any of those and the space moves. The centroids move with it, and reader
assignments near a boundary flip. This is not a defect to be fixed by tightening the
port; it is why a frozen model bundle exists at all, and why it records the lineage it
was fit under and **refuses** to score rows built under different declarations. The
refusal is the useful artifact. Numeric equality across constructions is the thing
that was never available.

A concrete illustration of the magnitude: two constructions of the same email click
signal, on the same underlying vendor data, disagreed by more than a factor of two on
distinct weekly clickers. A calibration fit on one and applied to the other produces a
complete set of plausible scores and no error.

---

## What is asserted instead

All of these are in `tests/test_engagement_lane.py`, and each is a property that would
be violated by a real defect rather than by a change of data source.

| Assertion | The defect it catches |
|---|---|
| The surface has exactly its declared feature columns, in the declared order | A frozen centroid is a vector in that order. A silently absent column makes every distance wrong by a fixed amount, and no single number looks off. |
| Row counts equal the spine, per week | A fanned-out join double-counts every measure on the duplicated readers while each individual number stays plausible. A dropped join loses inactive readers, so the population tracks engagement instead of entitlement and every average rises. |
| Topic bucket shares sum to 1 for every content-active reader | A bucket lost between the map and the matrix. The symptom is a topic block that under-weights whatever fell out. This exact failure happened upstream: a frozen matrix omitted three declared columns, and because the assembly reconciled its block membership down to whatever columns existed, nothing complained. |
| Every surface column's variance is within `[0.5, 2.0]` on its own fitting population | The fit and the apply saw different rows. Produces a full set of finite, wrong features. |
| Every reader in the scored population gets exactly one label | An absent row is indistinguishable downstream from a reader who is not a subscriber. |
| The no-recent segment carries no distance | It never entered the fit, so a confidence number would be about nothing. |
| Gate outcomes are produced, with the realised value beside every threshold | A gate reporting only pass/fail cannot be used to set its own threshold — and several of these thresholds start as reasonable guesses a deployment is meant to replace with a measurement. |

---

## The email day shift

**Numeric email parity is unavailable by construction, not merely unmeasured.** This
is the one to expect a failure from, and it is the reason this section exists.

The live email tables in the source system apply **no timezone conversion at all**.
The vendor's timestamps are bucketed into days in whatever zone the vendor sent, and
the aggregate carries that as its day. This contract refuses a pre-bucketed date
outright and requires the instant, so the engine applies the declared zone once, to
every channel.

For a publisher declaring an Eastern day boundary, that means:

* an **evening click** — after 19:00 Eastern, i.e. after midnight UTC — moves back one
  day relative to the unconverted table;
* a **Saturday-evening click** moves back a day *and therefore across the week
  boundary*, into the previous week's bin.

Both shifts change the features that feed the model:

* `email_click_days_7d` / `_28d`, which count distinct dates;
* `email_click_recency_days`, which reads the last active date;
* the four weekly bins, and therefore `email_click_active_weeks_4` — the cadence axis.

So a first parity run will show email features that differ, and the difference is the
port being **correct**. Reproducing the old numbers would require re-importing the
day-boundary defect the contract exists to prevent.

The shift is not small in the tail. A newsletter sent in the early evening puts a large
share of its clicks in the affected window, and the Saturday case moves a whole week's
worth of cadence for those readers.

---

## Community parity is unverifiable, not merely unavailable

A weaker statement than the email one and worth keeping separate, because it changes
what an investigation can conclude.

The source's community aggregate parses a vendor partition string into a date with no
zone conversion, and the zone that partition was written in is undocumented. So:

* a **mismatch** cannot be attributed — it could be the day boundary, or it could be
  the vendor's own bucketing, and there is no way to tell from the data;
* a **match** proves nothing either, because it would be consistent with the vendor
  having used the declared zone by coincidence.

There is no experiment available here that distinguishes the hypotheses. Recording
that is more useful than a comparison whose result cannot be interpreted.

---

## What a useful comparison looks like

Given the above, the comparison worth running is not centroid equality. It is:

1. **Distributional.** Are the marginal distributions of the raw atomics — views,
   sessions, active days, clicks — the same shape? These are counts, not standardised
   scores, so they are comparable across constructions, and a real port defect shows
   up here as a shifted or truncated distribution.
2. **Rank correlation on the engagement measures.** The three measures are monotone
   summaries. A high rank correlation says the two systems agree about who is engaged
   even where they disagree about which archetype somebody belongs to.
3. **Cluster-profile correspondence.** Do the two systems find groups with the same
   *shape* — a heavy multi-channel group, a single-channel group, an email-habit
   group? Matched by centroid profile rather than by id.
4. **Population size.** The scored population should match to within the difference
   the entitlement resolution accounts for, and that difference should be
   enumerable — one number, explainable.

A failure in any of those is a port defect. A difference in cluster membership is not
evidence of one.
