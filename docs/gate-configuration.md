# The gates are yours: setting the thresholds and the cluster count

Every number this engine screens against has a default, and every one of those
defaults is a number some other newsroom measured on some other population. This
document is how you replace them.

It is a companion to three others and does not restate them. [The adopter
path](adopter-path.md) is the six steps to a first run. [The engagement
lane](engagement-lane.md) is what the lane produces and what each output means. [The
canonical input contract](canonical-input-contract.md) is the shape of the data you
deliver. This document is only about the numbers the engine judges that data
against.

## The short version

```bash
# 1. Write the current thresholds out as a file you own.
engagement-kernel-engagement-lane gates-template my-gates.toml

# 2. Derive the one threshold that cannot honestly be inherited, on your own panel.
python3 tools/derive_cross_algorithm_bars.py my-fit-matrix.parquet \
    --k-min 2 --k-max 10 --replicates 100 --jobs 4 --out derivation/
#    then paste derivation/cross_algorithm_bars.toml into my-gates.toml

# 3. Run against your own numbers.
engagement-kernel-engagement-lane run delivery/ \
    --bucket-map buckets.json --gates my-gates.toml
```

Omitting `--gates` runs this package's defaults, unchanged. The file is a way to say
something, never a thing you must say to get the engine's own behaviour.

## Why this document exists

Two docstrings in this engine said the right thing and were not backed by anything.
`GateThresholds` said every default was "a starting point for a deployment that has
not yet measured its own distribution". The gate module said "several of these
thresholds start as reasonable guesses that a deployment is supposed to replace".
Both are true. Neither was supported: setting one meant writing Python against a
library whose interfaces change without notice, and the command line offered four
flags, all of them framed as ways to make a first run cheaper.

That combination is more prescriptive than prescribing outright, because it reads as
an invitation and behaves as a fixed value. An adopter who walked the adopter path
reached a gated model and was never told the gates were theirs.

## The gates file

TOML, versioned, and yours. Keep it in your own version control next to your
manifest and your section bucket map.

```toml
version = 1

[gates]
seed_ari = 0.70
selection_survival_floor = 0.50

[gates.cross_algorithm_ari_by_k]
2 = 0.51
3 = 0.46
4 = 0.42

[lane]
k_grid = [2, 3, 4]
```

Four things to know about how it is read.

**Anything you leave out keeps the package default.** A file that sets one threshold
changes one threshold.

**A key this reader does not recognise is refused, not ignored.** A misspelled
threshold that silently does nothing is the characteristic failure of configuration
files, and it fails in the worst direction: the run reports the default's verdict
while you believe you set something. The refusal names the near-miss.

**The version is required.** It is the only way a later change in what a key *means*
can be caught rather than silently misread.

**Declaring `[gates.cross_algorithm_ari_by_k]` replaces the table rather than adding
to it.** This is deliberate. A bar measured on somebody else's panel is not something
to inherit by accident, so if you declare bars, you declare the ones you intend to
screen against and every other k refuses.

`gates-template` writes the defaults out with the reasoning for each one beside it,
and the file it writes loads back to exactly those defaults. So `diff` against a
fresh template shows precisely what your deployment changed.

## Choosing the number of clusters

`k_grid` is the set of candidate cluster counts the engine screens. It does not have
to be contiguous — `--k-grid 4,6,8` screens exactly those three — and there is no
upper bound in the engine.

**Two clusters is a legitimate answer.** A small readership, or one that splits
sharply into subscribers who read and subscribers who do not, may genuinely have two
segments. Until recently this engine refused `k=2` outright, because it shipped bars
for `k=3` through `k=10` and refused any k it had no bar for. The refusal was right
and the range was not: it was the range one newsroom happened to sweep.

What the engine still refuses, and should: **a k with no cross-algorithm bar declared
for it.** Screening a candidate against a number nobody measured for it is the one
thing that screen must not do. So to add a k, declare its bar — the refusal message
says how, and the next section is how to find the number.

Choosing the range is a judgement, and the two ends are different judgements. The
floor is whatever number of segments could be real for your audience; two, usually.
The ceiling is where the segments stop being things a person can act on, and the
smallest-cluster floor will usually bind before interpretability does. Neither end is
a property of the method.

## Deriving the cross-algorithm bar

This is the one threshold in the engine that cannot honestly be inherited, and the
reason is a measurement rather than a preference.

The screen fits your candidate k with a second algorithm and asks whether the two
agree. A bar on an agreement statistic means nothing until you know what agreement
the two reach **by chance**. The intuition is that unstructured data produces
agreement near zero, so any bar comfortably above zero is safe. That intuition is
wrong: k-means and Ward share an objective, so they cut a featureless cloud along
similar surfaces and agree at an adjusted Rand index around 0.26 to 0.31 on a
population with no cluster structure at all. And that chance level *falls* as k
rises, so a single flat bar is the wrong shape as well as the wrong level — it
certifies high k too easily and refuses low k too readily.

Chance agreement depends on your row count, on how many features you carry, and on
the correlation structure of your own readers. On one publisher's data the same
derivation gave bars about 0.10 higher on a six-feature panel than on a nine-feature
one. **A bar carried across feature spaces reports a pass it has not earned.** If
your fit feature set changes, derive again.

```bash
python3 tools/derive_cross_algorithm_bars.py my-fit-matrix.parquet \
    --k-min 2 --k-max 10 --replicates 100 --jobs 4 --out derivation/
```

It builds replicates of your own panel with the structure taken out, measures what
the two algorithms agree on there, and takes the 95th percentile. It emits a gates
fragment you can paste in, and an evidence file recording every distribution it
measured. Read the tool's module docstring before changing `--null`: which null
governs is a judgement, it is the most consequential one in the derivation, and it
is argued for there rather than assumed.

The derivation never observes your panel's real cross-algorithm agreement at any k.
It reads only replicates, and depends on your panel only through its shape and its
covariance — so there is no path by which a verdict you would prefer could steer the
number. Two controls must pass before it emits anything: a positive control that the
statistic can see real structure at all, and a **held-out** negative control that the
percentile transports to draws it was not derived from.

`replicates * k * seeds` clustering fits per panel, and the hierarchical fit is
quadratic in rows. Start at `--replicates 20` to see the shape; a bar you intend to
freeze against wants 100.

## What the shipped bars are

The eight bars this package ships were derived on two freezes of one publisher's
nine-feature subscriber panel, 4,571 rows each, pooled over a five-matrix corpus of
the same feature space. They are labelled as such in `config.SHIPPED_BAR_PROVENANCE`,
in the rendered gates template, and in this document.

They are a working default so that a first run produces something, and they are not a
recommendation. **Per-k-ness is the portable lesson. The levels are not.**

## Which numbers are configurable, and which are not

Not every number in the lane should be a knob, and the honest thing is to say which
are which rather than leave the line implicit. Every quantitative choice sits in
exactly one group below. The tables are rendered from
`engagement_kernel.engagement.parameters`, and a test asserts this document carries
the render verbatim and that every constant named still exists in the code.

<!-- triage:begin -->

### Promoted to configuration

| Where | What | Note |
|---|---|---|
| `engagement.config:GateThresholds.seed_ari` | Seed-stability floor | Median pairwise agreement across starting points. The run reports the realised value beside it. |
| `engagement.config:GateThresholds.cross_algorithm_ari_by_k` | Cross-algorithm agreement bar, per candidate k | The one threshold that cannot honestly be inherited: chance agreement depends on your row count, your dimensionality and your population's own correlation structure. Derive it with tools/derive_cross_algorithm_bars.py. |
| `engagement.config:GateThresholds.centroid_distinctness_corr` | Centroid distinctness ceiling | Above this, two clusters are one cluster reported twice. |
| `engagement.config:GateThresholds.tiny_cluster_floor` | Smallest share a cluster may hold | Keep it equal to the persistence share, or a cluster can be both too small to matter and required to persist. |
| `engagement.config:GateThresholds.major_cluster_share` | Share at which a cluster must persist across seeds | The other half of the pair above. |
| `engagement.config:GateThresholds.topic_coverage_floor` | Resolved-reading share the topic taxonomy needs | Blocks the topic block, not the whole run. |
| `engagement.config:GateThresholds.t4_retention` | Label retention across a disjoint window | How much churn between two non-overlapping windows is acceptable in your audience, which is a question about your audience. |
| `engagement.config:GateThresholds.t4_profile_similarity` | Centroid profile correlation across a disjoint window |  |
| `engagement.config:GateThresholds.selection_survival_floor` | Survival-rate floor for a candidate k | How reproducible a verdict has to be before it counts. A majority, with confidence, is the shipped answer. |
| `engagement.config:GateThresholds.selection_perturbation_draws` | Perturbed panels per candidate k | Cost, not strictness: fewer draws is a noisier verdict, never a more permissive one. |
| `engagement.config:GateThresholds.selection_perturbation_row_fraction` | Rows dropped per perturbed panel | How small a change in the panel the verdict has to survive. |
| `engagement.config:GateThresholds.selection_rng_seed` | Seed for the perturbation draws | Reproducibility, not strictness. |
| `engagement.config:LaneConfig.k_grid` | Candidate cluster counts | Any set of counts from two upwards, contiguous or not. Two is a legitimate answer for a small or sharply split audience. |
| `engagement.config:LaneConfig.n_seeds` | Starting points per candidate k | Cost, not strictness. |
| `engagement.config:LaneConfig.content_active_min_views` | Resolved views before a reader has a topic mix |  |
| `engagement.config:LaneConfig.content_active_min_sections` | Distinct sections before a reader has a topic mix | Two is the floor the code enforces: at one, every reader with a single view has a mix that is 100% one bucket, which is not a preference. |
| `engagement.config:LaneConfig.z_clip` | Standard deviations at which a standardised feature is clipped | Its default is calibration.Z_CLIP_DEFAULT. |
| `engagement.config:LaneConfig.panel_seed` | Seed for the training-panel sample | Versioned with the model: the panel is the fitting population, so a different seed is a different model. |
| `engagement.config:BlockWeights` | How much model distance each semantic block owns | Used only by the block-weighted construction. |

### Deliberately fixed

| Where | What | Note |
|---|---|---|
| `engagement.config:EMAIL_CLICK_UNIT` | What one email-click row counts | A click event, not distinct campaigns clicked. A definition that belongs to a model version, not to a run: counting the other way is buildable and is a different model, so it would be a version change rather than a flag. |
| `engagement.guards:FORBIDDEN_INPUT_PATTERNS` | Signals that may never be clustering inputs | Behaviour-only clustering is the method, not a setting. A configurable version of this rule is a way to turn off the thing the method rests on. |
| `engagement.selection:WILSON_Z` | Confidence level of the survival bound | One-sided 95%, and fixed because it is a convention rather than a threshold about your data. The bar derivation uses the same 95%, so moving one without the other would leave the selection rule holding two different notions of confidence. |
| `engagement.matrix:VARIANCE_BOUNDS` | Per-feature variance the assembled matrix must show | An assertion that standardisation did what it says, not a statement about readers: a z-scored column with a variance of 0.3 means the transform is wrong. Widening it would silence the check rather than fix what it caught. |
| `engagement.panel:PANEL_RULE` | One reader-week per reader per calendar month | Part of what the fitting population is, so it travels with the model version alongside the panel seed rather than as a runtime knob. |

### Deferred, with the reason

| Where | What | Note |
|---|---|---|
| `engagement.windows:TRAILING_WINDOW_DAYS` | Width of the long feature window, 28 days | The largest remaining prescription in this lane, and it should be the deployment's. Deferred because the width is woven into the feature vocabulary itself -- feature names carry it, the weekly bins tile it, the temporal gate's disjoint gap is derived from it -- so promoting it means the feature names become generated rather than declared. Until then a newsroom on a different publishing cadence cannot express one, and no document should suggest otherwise. |
| `engagement.windows:WEEK_BIN_COUNT` | Weekly bins the long window is cut into, 4 | The same prescription as the window width and not separable from it: the bins have to tile the window exactly. It is also the disjoint gap the temporal gate requires, which used to be a bare 4 and a bare 28 written out again in the selection module -- one prescription stated in three places and revisable in none. Now derived from here. |
| `engagement.blocks:DENSE_MIN_EXPLAINED_VARIANCE` | Block-quality thresholds, five of them | With SPARSE_MIN_EXPLAINED_VARIANCE, DENSE_MIN_ANCHOR_CORR, SPARSE_MIN_ANCHOR_CORR and REDUNDANCY_CORR_THRESHOLD. They decide how a semantic block is summarised rather than whether a result may be published, and they have never been measured on a second population. A knob offered before anyone has a second measurement is a knob with no basis for turning it; the first adopter run that reports these values is what promotes them. |
| `engagement.calibration:PCT_CLIP_LO` | Winsorisation bounds for the fitted transform | With PCT_CLIP_HI. Changing them changes what the frozen transform is, so they belong to a model version rather than to a run -- but unlike the click unit they are a tuning choice rather than a definition, so they should end up declared in a versioned freeze rather than fixed here. |

<!-- triage:end -->

The deferred group is the part worth reading twice. Those are prescriptions, they are
named as prescriptions, and the largest of them — the 28-day feature window — is one
that another document in this repository once invited an adopter to change while the
code could not express a different one. A prescription nobody has written down reads
as a property of the method.

## What did not change

No default value moved. The point of this work was to make the numbers reachable, not
to re-tune them, so a run that does not pass `--gates` produces the same verdicts it
produced before any of this existed. If your own derivation disagrees with a shipped
default, that is a finding about your panel, which is exactly what it is for.
