# The engagement lane

This is the modelling layer: it turns the daily intermediate tables into a weekly
engagement score and a behavioural cluster per reader. It runs on pandas,
scikit-learn and NumPy over columnar files, with no warehouse, no query service, no
credentials and no network — and that claim is checked in continuous integration by
running the whole lane with every cloud SDK blocked by name.

```
engagement-kernel-cohort /tmp/cohort --readers 400          # a synthetic cohort
engagement-kernel-engagement-lane run /tmp/cohort \
    --bucket-map /tmp/cohort/section_buckets.json \
    --output-dir /tmp/cohort-out
```

Four exit statuses, and the middle two are the ones that matter: `0` fit, scored and
every gate passed; `3` no candidate number of clusters survived selection, so nothing
was frozen; `4` a model was frozen but a gate blocks publication; `1` could not run.
"No model" and "a gated model" are different answers and a caller that collapses them
publishes the second.

### What a run costs, and how to make the first one cheap

Selection is the expensive part, on purpose. Every candidate `k` is re-screened on 50
perturbed panels with 20 starting points each, which is a thousand clustering fits per
candidate — on a 120-reader cohort with a six-wide sweep that is a couple of minutes,
and it scales with the sweep rather than with the audience, because the panel is capped
at one reader-week per reader per month.

That is the right cost for a **freeze**. It is the wrong cost for a first look at your
own data, so the knobs are flags rather than source edits:

```
engagement-kernel-engagement-lane run <delivery> --bucket-map <map.json> \
    --k-min 3 --k-max 5 --seeds 5 --perturbation-draws 5     # seconds, not minutes
```

Lower settings give a *faster* verdict, not a weaker one in any hidden sense: the
screens and their thresholds are unchanged, and the survival bound simply has fewer
draws behind it — which the one-sided Wilson bound accounts for by being wider. Freeze
on the defaults.

---

## What it produces

| Table | Grain | For |
|---|---|---|
| `reader_week_features` | reader × week | the atomic feature mart; answers "why did this reader get that label" |
| `reader_week_cluster` | reader × week | the label, its confidence, its raw component id, its out-of-distribution flags |
| `reader_week_measures` | reader × week | three engagement measures, their within-week percentiles, per-block sub-scores |
| `cluster_profile` | cluster | mean raw atomics and population share — the table a person reads to name a cluster |
| `k_selection` | candidate k | every candidate's screen statistics and why it failed |
| `gate_report` | gate check | every check, its verdict, and the realised value against its threshold |

`k_selection` and `gate_report` are written **whether or not a model was frozen**, and
especially when one was not: a run that freezes nothing otherwise leaves nothing
behind to diagnose the refusal against, and the matrix has to be rebuilt from scratch
to ask why.

---

## What the publisher declares, and what the engine will not guess

Everything that changes what the numbers *mean* comes from the delivery's own
manifest, so a run cannot quietly disagree with the data it was given:

| Declaration | What it decides |
|---|---|
| `day_boundary_timezone` | which calendar day every event belongs to, on every channel |
| `week_anchor` | which weekday ends a week |
| `article_view` | which deliveries count as a view |
| `scored_population` | which subscription states are in the scored population |

Two more are deployment configuration, and neither has a default:

* **the section bucket map** — a JSON file the deployment owns and versions. There is
  no default because a map invented by the engine would name buckets no editor
  recognises. See [Section buckets](#section-buckets).
* **the email list restriction** — which lists the cadence signal counts. `None`
  means every list in the delivery, which is the honest default: use what you were
  sent.

### The week anchor is read, never assumed

`engagement_kernel/engagement/windows.py` carries no `DEFAULT_WEEK_END_DAY` and there
is nothing for a caller to fall back on. That is a deliberate response to a live
defect in the system this ports from: **two week-anchor conventions exist there and
they differ by up to six days.**

* One lane runs weeks that **end** on Sunday — Monday through Sunday. This is the
  serving lane, and it is the convention the port replaced.
* Another runs weeks that **start** on Sunday — Sunday through Saturday. This is a
  research lane whose outputs are not serving.

They never meet: no shared helper, no file that joins both, different week column
names. Nothing in that repository states that both exist or tests the distinction. A
port that silently inherited the research convention would produce four weekly bins
shifted by six days against the day boundary, every count would still be plausible,
and no existing test would notice.

So the manifest declares a weekday plus which end of the week it sits on, this
package resolves that pair into a week-ending weekday exactly once, and
`tests/test_engagement_windows.py` changes the declaration and requires the bins to
move. Hardcoding the anchor breaks five of those tests — verified by doing it.

### The email click unit is decided

**A click event. Not distinct campaigns clicked.** Recorded as data
(`config.EMAIL_CLICK_UNIT`), named in this document, and folded into the model version
— because it does move the numbers.

Where it moves them is not where an earlier framing said, and the difference matters
to anyone debugging it:

* the **cadence axis is invariant** to the unit. It counts weeks with a non-zero bin,
  and any week containing at least one click has a non-zero bin under either unit.
* the **click-day counts are invariant** too: they count distinct dates.
* only **click volume** moves — the 7-day and 28-day click counts — and both are
  log-transformed on the way into the model, which damps the difference further.

So the decision is real and it belongs to a model version, but it reaches the clusters
through the intensity block rather than through cadence. The source repository's
docstring claimed the campaign meaning long after its table counted events; the port
does not carry that forward, and `tests/test_engagement_measures.py` asserts the
invariance as arithmetic so the claim stays true.

---

## Section buckets

The bucket map is versioned deployment configuration: a JSON file mapping the
publisher's own sections to topic buckets, plus a catch-all. Three rules, and they are
the invariants:

1. **Completeness.** Every section carrying a non-trivial share of reading must be
   mapped. Checked against *observed reading*, not against the file, because a map can
   be complete on paper and stale in fact.
2. **A declared catch-all ceiling.** The catch-all is a remainder, not a bucket. The
   map declares the share above which it stops being one.
3. **Bucket names must survive the model guard.** Names become column names, so a name
   the guard would refuse is caught at load time, where the message can name the file.

**The bucket count is declared, not fixed.** The source system hardcoded a range of 8
to 12 buckets and refused anything outside it, which would turn away a newsroom with
five sections. That is a portability defect rather than a validation rule: completeness
and the catch-all ceiling are the real invariants and they hold at any count. Bounds
are still supported, because a publisher who has decided their taxonomy has ten
buckets should be able to say so and have a refit that produced eleven fail.

**A catch-all with no remainder is dropped on the record.** Map your whole taxonomy
and nothing falls into the catch-all, so its share is zero for every reader. The map is
correct; the column carries no information — it cannot influence an assignment and
would still take a share of the topic block's weight. Left alone it fails the
unit-variance assertion several layers downstream, with a message about standardisation
populations rather than about the taxonomy. So `fit_pipeline` names it at fit time and
records it as a documented drop, and an adopter whose taxonomy has no remainder does
not have to debug a variance assertion to find that out. This is a real case rather
than a hypothetical: the repository's own synthetic cohort maps every section it
publishes, and it is what surfaced the defect.

---

## The two guards

`engagement_kernel/engagement/guards.py` decides what may become a model feature, and
it is the reason the rest of the lane can be trusted. Port the feature and model
layers without it and the easiest outcome is a run that produces plausible, wrong
clusters — clusters of subscription status wearing the name of a behavioural segment.

* **The input guard** runs on the daily frames. Pattern-only and
  vocabulary-independent: it refuses a column whose name says it carries scroll depth,
  an email open, an email send or a share widget.
* **The model guard** runs on the assembled feature columns and has both a pattern
  list and a name list, because they catch different things. Patterns catch families.
  Names catch columns whose names say nothing suspicious — and the load-bearing
  example is subscription state, which matches no pattern any reviewer would write
  down and is the single most damaging thing that could reach the matrix.

The name list is **derived** from the contract's own field names and the intermediate
tables' grain keys, not written out, so a field added to the contract is refused on
the day it lands.

Both matrix constructions in this package are guarded. That is a change from the
source, where the guard ran inside the block-weighted builder while the surface that
was actually frozen and published was assembled by a different function that never
called it — so on the live configuration the model guard protected a matrix nobody
shipped.

See [`engagement-lane-negative-controls.md`](engagement-lane-negative-controls.md) for
the controls, including the one that proves the name list was translated rather than
copied.

---

## What did not come across

Carried verbatim from the census that produced the decision, and **rendered from
the declarations in code** rather than retyped: the table-level list lives in
`intermediate.tables.NOT_BUILT`, the column-level list in
`engagement.outputs.NOT_PORTED_COLUMNS`, and a test asserts this document contains
the render verbatim. Retyping it is how the prose and the code drift, and the prose
is what somebody reads before deciding to rebuild one of these.

<!-- census:begin -->

### Tables not built

| Table | Upstream columns | Why not |
|---|---|---|
| `user_author_day` | 11 | Zero lane references. Author-level preference is not in any published model. |
| `user_content_type_day` | 11 | Zero lane references. Content-type preference is in no published model, and the type itself is already on the content dimension for the one reader that needs it -- the article-view predicate. |
| `user_device_day` | 12 | Zero lane references, and the most expensive table in the build: it re-reads the raw web and app event feeds from scratch rather than deriving from the consumption table. Dropping it alone removes a second full scan of the raw event feeds. |
| `person_day_activity_v1` | 60+ | Built by the local pipeline, declared in no contract, consumed by no lane. |
| `email_user_day (v1)` | 8 | Superseded by the v2 email table. Its engagement fields have been zero since its source feed stopped loading, and before that it under-captured clicks non-deterministically. |
| `web_user_content_day, app_user_content_day` | 12 each | The only consumer was a business-segment overlay reading a views anchor, which the channel table supplies equivalently. This build has no per-content published table at all: views are counted from the events, so there is nothing to reconcile against. |

### Columns not carried

| From | Dropped | Why |
|---|---|---|
| every daily consumption table | every scroll-percentage column | Declared out of scope by the contract. Excluded on evidence rather than principle: where it was measured it was carried on several aggregates and read by nothing, and on app surfaces it is commonly not measurable at all, so a mixed-surface deployment would compare a real number against a hardcoded zero. |
| the daily channel table | the anonymous browser id, and the mixed-grain person id | The contract has exactly one reader id at one declared grain. A browser id is not a reader, and a column mixing grains makes every distinct-reader count meaningless. |
| the daily email table | sends, the two last-activity timestamps, and the three reconciliation diagnostics | Sends measure the publisher, not the reader, and are forbidden by name in both guards -- so the column would exist only to be rejected. The timestamps and diagnostics are artifacts of the vendor reconciliation this lane does not do. |
| the daily comment table | the site name | The contract carries an opaque site id and the lane sums across it. A human-readable property name is neither needed nor safe to publish. |
| the subscription history | registration state, and the provenance source | Both were the same literal on every row. Constants that read like data. |
| the content dimension | nine of its fourteen columns | Nine had no reader at all, and two of those nine were forbidden from ever reaching a model. The tables that would have read the author and content-type lists are themselves not built. |

<!-- census:end -->

### Whole subsystems dropped

* **The Athena serving job, the warehouse kernel source, the serving DDL, writer,
  gates and monitoring, the metric emitters and the warehouse mirror.** None is
  portable and none is needed for a reference engine.
* **The upstream typed reader.** It numeric-coerces every column outside a small id
  allowlist, which means it cannot read the subscription-span table at all. The lane
  reads through the contract's own typed reader instead.
* **The two subscriber-universe mechanisms** — an entitlement-span table on the
  fitting path and a publisher-specific subscriber taxonomy on the serving path.
  Replaced by one contract-driven resolution as of a date. Two resolutions of one
  question is one too many: they can disagree, and when they do the model is fit on
  one population and scored on another.
* **The four hardcoded population-exclusion predicates**, two of them matching
  fragments of personal email addresses. The contract carries no personal field, so
  such a predicate is not expressible against it. Exclusions are a list of opaque
  reader ids in the manifest, and an entry that looks like a personal identifier is
  refused.
* **The hardcoded vendor mailing-list identifier** that restricted the cadence signal.
  It was a function default. A real third-party list id is deployment configuration
  for one publisher, it means nothing to an adopter, and it has no business in a
  public repository.

### And one thing that did come across, against expectation

Dropping the topic-cluster lane did **not** drop topic features. The engagement
model's feature space contains a topic block, and the weekly feature assembly requires
a bucket map as an argument — so removing the persona lane removed the *owner* of the
bucket map without removing the dependency on it. The map, the topic atomics and the
consumption of the section-day intermediate therefore live in this lane.

Topic-derived *outputs* — content personas — are deferred to a future version rather
than rejected. Nothing about them was judged unsound.

---

## What replaced the subscriber taxonomy

The source fit on individually-paying subscribers only and *projected* labels onto
guests and institutions. That taxonomy is one the contract deliberately cannot
express: `payer_type` is optional, and a publisher whose billing system cannot
distinguish payer types must supply null.

What replaces it is **window completeness**. The fit runs on readers entitled for the
whole 28-day feature window; readers entitled for only part of it are scored and carry
`projected_flag`. That distinction is always expressible against the contract, it is
the one that actually matters to the arithmetic — a partial window has fewer days to
accumulate activity in — and it does not require the publisher to have a payer
taxonomy at all. Fitting on a mixture of window lengths would teach the model that
recent subscribers are light readers.

---

## Absent optional inputs select a different feature set

The richer clustering surface uses community participation and email cadence, and both
come from optional contract inputs. A delivery that does not carry them cannot be
scored on it, and the wrong response is to fill the missing channels with zeros: a
reader with no community feed is not a reader who never commented, and a model fit on
that distinction learns the shape of the publisher's vendor contracts.

So an absent optional input selects a **named alternate surface** — the six-dimension
magnitude surface — which is the answer the contract promises for exactly this case.
Naming the richer surface on a delivery that cannot support it is refused rather than
silently downgraded.

| Surface | Dimensions | Needs |
|---|---|---|
| `intensity` | 6 | required inputs only |
| `joint` | 9 | `email_click` **and** `community_action` available |

---

## Parity

Parity is stated structurally, not numerically, and
[`engagement-lane-parity.md`](engagement-lane-parity.md) says why — including the email
day shift that makes numeric email parity unavailable *by construction* rather than
merely unmeasured.
