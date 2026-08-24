# Engagement lane: negative controls

A guard that passes on a clean tree proves nothing. This document records the controls
for the two claims in this lane that would otherwise be assertions in prose — that the
feature guards refuse what they say they refuse, and that the window layer reads the
declared week anchor — together with the evidence that each control **discriminates**.

Discriminating means: the control was shown to fail when the thing it protects was
broken. A control that passes both before and after a mutation is decoration, and the
two mutations below were run, their output recorded, and the code restored from a
file backup.

---

## 1. The feature guards

`tests/test_engagement_guards.py`

### The controls

Four forbidden columns are seeded and each must be refused:

| Seeded column | Refused by | What it would have been |
|---|---|---|
| `web_scroll_depth_28d` | pattern `*scroll*` | a scroll measure, declared out of scope by the contract |
| `email_sends_28d` | pattern `*send*` | the publisher's behaviour, not the reader's |
| `email_opens_28d` | pattern `*open*` | reachability only; machine opens inflate it and cannot be cleaned out |
| `state` | **name** | subscription state — the discriminating control |

### Why the fourth one is the only one that proves anything

The guards were ported from a system with a different vocabulary, and the two halves
of the guard behave differently under translation:

* **The pattern list survives translation untouched.** `*scroll*` means the same thing
  in any vocabulary.
* **The name list does not.** A literal copy of the original list names columns this
  contract cannot produce. It would load, run on every build, and match nothing
  forever.

So a control that seeds only scroll, sends and opens exercises the patterns, and
**would pass against exactly that dead name list.** It proves half the guard. Seeding
a subscription-state column is what proves the names were translated, because `state`
matches no pattern any reviewer would write down — it is an entirely ordinary column
name, and it is the single most damaging thing that could reach the matrix.

`test_the_pattern_list_alone_would_pass_the_state_column` makes that explicit: it
builds the counterfeit guard — the patterns with a dead name list — and requires it to
catch exactly three of the four. If `state` ever starts matching a pattern, that test
fails, because the discrimination would have been lost and the other controls would no
longer be evidence of anything.

### Discrimination evidence

`forbidden_model_names()` was replaced with an untranslated list naming four columns
from the source system's vocabulary — the literal-copy failure mode. Six tests failed:

```
FAILED test_every_seeded_forbidden_column_is_refused[state-name-...]
FAILED test_the_error_message_names_the_offending_column[state-name-...]
FAILED test_the_pattern_list_alone_would_pass_the_state_column
FAILED test_the_name_list_is_derived_from_the_contract_not_written_out
FAILED test_the_raw_entropy_atomic_is_refused_but_its_surface_dimension_is_not
FAILED test_the_matrix_builder_refuses_a_seeded_state_column
6 failed, 11 passed
```

Two things in that result are the point:

1. **The three scroll/sends/opens parametrisations still passed.** That is the
   asymmetry, demonstrated rather than asserted: an untranslated guard passes them.
2. The matrix-builder control failed with
   `ValueError: could not convert string to float: 'active'`. With the guard dead, the
   matrix simply tried to compute a distance against a subscription state. That is the
   *lucky* outcome — the column happened to be a string. Had the publisher encoded
   state as an integer code, it would have been standardised, weighted, and clustered
   on, and every number downstream would have been finite and plausible.

### A positive control

`test_the_guard_permits_the_real_model_columns` asserts that every column the lane
actually builds is permitted. Without it, the guard could be tightened into uselessness
and every test above would still pass — right up to the first run that refused its own
matrix.

### The one deliberate asymmetry

`topic_entropy_28d` is refused; `z__topic_entropy_28d` is permitted. Asserted so it
stays deliberate. In the block-weighted construction the topic block already carries a
share per bucket, and entropy is a deterministic function of exactly those shares — so
as a block feature it adds nothing and spends a second block weight on the same signal.
The intensity and joint surfaces carry no bucket shares, so there it is the only
breadth-of-taste dimension and is a declared part of the surface. The raw atomic stays
refused either way.

---

## 2. The week anchor

`tests/test_engagement_windows.py`

### The control

The claim is that the window layer takes its week anchor from the delivery's manifest
and not from ported code. That needs a control, because a module that ignored the
manifest and used a hardcoded Sunday would pass every test written against a
Sunday-anchored delivery — and the delivery this repository ships is Sunday-anchored.

Two forms, both present:

* **Unit.** Same synthetic day range, two declarations, and the bins must differ:
  `week_ends_on Sunday` versus `week_starts_on Sunday`, which resolve to weeks ending
  Sunday and Saturday respectively.
* **End to end.** Real intermediate tables from the synthetic cohort, one declaration
  changed, and real readers' weekly bin features must come out different. This one
  exercises the manifest, the config resolution, the atomic builders and the bin
  arithmetic together, so a hardcoded anchor anywhere in that path fails it.

### Discrimination evidence

`WeekGrid.from_anchor` was mutated to ignore `anchor.position` and always resolve to
Sunday. Five tests failed:

```
FAILED test_week_ends_on_resolves_to_that_weekday
FAILED test_week_starts_on_resolves_to_the_day_before
FAILED test_the_two_conventions_disagree_about_the_same_date
FAILED test_changing_the_declared_anchor_moves_the_weekly_bins
FAILED test_changing_the_declared_anchor_moves_a_readers_bin_values
5 failed, 8 passed
```

### And the absence that makes it stick

`test_the_grid_has_no_module_level_default` asserts there is no
`DEFAULT_WEEK_END_DAY`-style constant anywhere in the module, and that
`WeekGrid.from_anchor()` cannot be called without an argument. A default is how the
wrong convention gets inherited: one caller omits the argument, and the whole lane
silently runs the other lane's week.

---

## 3. The bucket-map completeness rule

`tests/test_engagement_buckets.py`

`test_the_completeness_rule_fails_on_an_incomplete_map` removes a bucket from the map
while a fifth of observed reading is in it. Both halves of the rule fire — the section
is named as unmapped, and the catch-all crosses its declared ceiling — and the report
names the section.

The paired assertion is what makes it a control rather than a demonstration: the
*complete* map is checked against the *same* shares and passes. So the failure above is
a property of the map, not of the numbers chosen to test it.

A second control covers the rule's tolerance in the other direction: a section holding
0.1% of reading is not a completeness failure. A rule that fired on the whole long tail
would be disabled within a week of a real deployment.

---

## 4. What is *not* controlled, and why

Stated so an absence is a recorded decision rather than an oversight.

* **Numeric parity against the source system.** Unavailable by construction. See
  [`engagement-lane-parity.md`](engagement-lane-parity.md).
* **The interpretability gate.** It has no automatable evidence by design — its whole
  content is that a person looked at the clusters and named them. What *is* asserted is
  that it fails by default (`test_the_gates_produce_a_verdict_and_the_manual_one_fails_by_default`),
  because a gate defaulting to a pass would publish cluster names nobody had reviewed.
* **The per-reader instability share.** Reported, not gated. No threshold for it has
  ever been measured, and setting one from the run that produced it would not be a
  gate. It is computed on *matched* labels — cluster ids are arbitrary, and comparing
  two labellings without matching them first reports nearly every row as unstable even
  when the partitions are identical, which yields a diagnostic that reads 1.0 at every
  candidate `k` and says nothing.
