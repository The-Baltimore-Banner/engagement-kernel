# The daily intermediate tables

This is the layer between a conforming delivery and the modelling lanes. It
takes the seven contract tables and produces seven daily aggregates, in one
in-process DuckDB session, with no warehouse, no credentials and no network.

```
engagement-kernel-build-intermediate examples/demo-delivery
engagement-kernel-build-intermediate <delivery> --out <dir>     # write Parquet
engagement-kernel-build-intermediate <delivery> --print-sql      # read the SQL
```

Three exit statuses, and the third is the one that matters: `0` built and every
check passed, `1` built and a check failed, `2` could not run at all. "We could
not look" and "we looked and it was wrong" are different answers, and a caller
that collapses them treats the first as a pass.

---

## What is built

| Table | Grain | Key | Published |
|---|---|---|---|
| `content_dimension` | content | `content_id` | internal |
| `reader_content_day` | reader × channel × content × day | `reader_id, channel, content_id, local_date` | internal |
| `reader_channel_day` | reader × channel × day | `reader_id, channel, local_date` | yes |
| `reader_section_day` | reader × section × day | `reader_id, section, local_date` | yes |
| `reader_email_day` | list × reader × day | `list_id, reader_id, local_date` | yes, optional input |
| `reader_community_day` | reader × day | `reader_id, local_date` | yes, optional input |
| `subscription_state_interval` | reader × interval | `reader_id, start_ts` | yes |

Every key is asserted on every build, not in a test helper. A documented grain
that nothing enforces is how two rows per key arrive: every count downstream
multiplies, and no single number looks wrong.

Two tables are internal. They exist because something else needs them —
`reader_content_day` because fractional section attribution needs a per-content
row to divide, `content_dimension` because the section list has to come from
somewhere — and neither is part of the surface the modelling lanes read. Saying
so here stops them being treated as outputs by accident.

`local_date` is the one column this build produces and the contract *refuses on
input*. The asymmetry is the design: a producer supplying a pre-bucketed date has
already applied some timezone and no validator can recover which one, so the
contract rejects the column by name; the engine then produces exactly that column
once, in the one declared zone. Feed an output back in as a delivery and it is
refused — correctly, because it would be bucketed twice.

---

## The derivations where the obvious rewrite is wrong

Each of these has a check that runs on every build and a negative control in
[`intermediate-negative-controls.md`](intermediate-negative-controls.md) proving
the check fails for its own reason.

### One timezone, applied once, to every channel

Every local day comes from `AT TIME ZONE` on a timezone-aware instant, with the
zone taken from the manifest. There is no default and no fallback.

This is the largest silent-wrongness risk in the system being replaced, which
converts web and app to the publisher's zone and applies **no conversion at all**
to email and community. For an ISO-8601 `Z` timestamp that makes email days UTC —
four to five hours ahead of the others — so an evening click is attributed to the
next day and a Saturday-evening click lands in the following week's bin. Nothing
visibly breaks. Every window is mis-bucketed for one channel and every number
stays plausible.

Two DuckDB specifics are load-bearing here:

- **A bare `CAST(instant AS DATE)` is evaluated in the session timezone**, which
  defaults to the host's. The same query returns different days on two machines,
  and on a developer's laptop set to the publisher's own zone it returns the right
  answer for the wrong reason — the worst case, because the defect ships. The
  build pins the session zone to UTC so that a stray cast is visibly wrong
  instead of accidentally right, and `tests/test_intermediate_timezone.py` runs
  the whole build under three different session zones and requires identical
  output.
- **DuckDB's Python row API converts a timezone-aware timestamp through `pytz`**,
  which is not a dependency of this package — so a query selecting an instant
  raises `ModuleNotFoundError` on a clean install, from inside DuckDB, naming a
  module nobody imported. Every read in this build goes through Arrow, which
  needs nothing extra.

What the checks cannot prove is that the zone in the manifest is the zone the
publisher meant. Nothing in the data can prove that, which is why the manifest
has to declare it and why there is no default.

### Channel-day sessions is a maximum, not a sum

`reader_channel_day.sessions` is the count of distinct sessions per reader per
channel per local day. It is computed once at the event layer, carried onto every
per-content row as `distinct_sessions_day`, and recovered at the channel grain
with `MAX`.

It is **not** `SUM(sessions)` over the per-content rows. A reader who read three
articles in one visit has one session and three per-content sessions of one.
Summing gives three: larger, entirely plausible, and every views-per-session rate
divides by it — so a deep reader is reported as a habitual short visitor and no
total anywhere is wrong.

The check recomputes the expected count from the event layer, which sits upstream
of the statement that produces the column, so it is not restating the aggregation
it is checking.

### Section attribution is fractional

A view of content filed under *n* sections contributes `1/n` to each section, so
a day's section views sum exactly to that day's views. Full weight per section
would multiply a reader's day by however many sections the desk happened to file
the piece under — an inflation that correlates with editorial filing habits and
not with anything the reader did.

The reconciliation is asserted, per reader-day, with a `FULL OUTER JOIN`: a
reader-day present on one side only is a mismatch and not an absence. That is what
catches attribution being *dropped* rather than merely mis-weighted.

### Unresolved metadata is its own outcome

Content whose section metadata did not resolve maps to a sentinel section,
`__unresolved__` — never to zero, and never to a real section. The downstream
reason codes depend on telling "we do not know what this reader read" apart from
"this reader read nothing", and those become the same number the moment
unresolved reading is dropped.

Three input shapes mean unresolved, and all three reach the sentinel: a row
declaring `section_resolution = 'unresolved'` with a null section list, the same
with an empty list, and a `content_id` with no row in `content` at all. The
declared resolution is tested first, so the sentinel is traceable to a statement
the producer made rather than inferred from a list that happened to be empty.

**A delivery event whose content row is absent still counts as a view.** The
contract states that an unmatched `content_id` means the metadata did not resolve
and is not an error, so the alternative is to drop the view — which is exactly
the collapse this section exists to prevent. The consequence is stated rather
than hidden: such a view is counted without its content type being confirmed, and
its whole volume is visible as sentinel rows in `reader_section_day`.

Two failures are in scope and they are opposites. Dropping unresolved reading
reports a reader as having read nothing. Folding it into a real section reports
them as interested in whatever bucket was chosen — and that version keeps every
total intact, so the reconciliation check above cannot see it at all. Hence a
separate check, and two separate controls.

---

## The article-view predicate

Required configuration, resolved once, from the manifest. Three conditions: the
event is of a kind the definition admits, it names a piece of content, and that
content's type is one the definition counts.

There is no default, and an unset or empty selection raises rather than assuming
one. A placeholder would produce a build that runs and is wrong, which is the
failure this repository exists to prevent; an *empty* selection is worse still,
because the build succeeds and reports every reader as inactive, which is
indistinguishable from a quiet publisher.

Two consequences worth stating plainly:

- **Events on content outside the definition are not counted anywhere in this
  build.** A reader who only watches video reads as inactive. That is a property
  of the article-view definition, not of this code, and the upstream system
  behaves the same way — but it is the kind of thing that gets discovered from a
  cluster nobody can explain.
- **A reader-channel-day with no qualifying view produces no row at all.** A
  channel active day is a day with at least one qualifying view, so a row of
  zeros would turn a day of no reading into a day of reading nothing.

The definition carries an id, and the id is in the build report. Changing which
content types count changes every view-based feature, so a published number has
to be traceable to the definition it was produced under.

---

## Optional inputs are absent, not zero

`reader_email_day` and `reader_community_day` depend on optional contract inputs.
When an input is missing the table is **not built**, and the build report names it
along with what its absence costs. Nothing is filled with zeros.

A reader with no community feed is not a reader who never commented; a reader
whose email feed starts mid-window is not a reader who stopped clicking. Zeros say
the second thing, models believe them, and the resulting clusters are plausible.

The email table's two inputs are independently optional, so a delivery with clicks
and no opens gets a table with a `clicks` column and no `opens` column — the
column is absent from the schema rather than present and zero, because zero opens
and no open feed are different facts and only one of them is about the reader.

---

## What is deliberately not built

Carried verbatim from the census that produced the decision. Six tables, and no
lane reads any of them. `NOT_BUILT` in
`src/engagement_kernel/intermediate/tables.py` holds the same list as data, so
the decision is visible to a reader of the code and not only of the docs.

| Table | Upstream columns | Why not |
|---|---|---|
| `user_author_day` | 11 | Zero lane references. Author-level preference is not in any published model. |
| `user_content_type_day` | 11 | Zero lane references. |
| `user_device_day` | 12 | Zero lane references, and the most expensive table in the build: it re-reads the raw web and app event feeds from scratch rather than deriving from the consumption table. Dropping it alone removes a second full scan of the raw event feeds. |
| `person_day_activity_v1` | 60+ | Built by the local pipeline, declared in no contract, consumed by no lane. |
| `email_user_day` (v1) | 8 | Superseded by the v2 email table. Its engagement fields have been zero since its source feed stopped loading, and before that it under-captured clicks non-deterministically. |
| `web_user_content_day`, `app_user_content_day` | 12 each | The only consumer was a business-segment overlay reading a views anchor, which the channel table supplies equivalently. |

Columns dropped from tables that *are* built:

- **Scroll depth**, everywhere. The contract declares it out of scope and rejects
  a column whose name contains it. Excluded on evidence rather than principle:
  where it was measured it sat on five aggregates and was read by nothing, it is
  banned by pattern in two independent downstream guards, and on app surfaces it
  is commonly not measurable at all — so a mixed-surface deployment would compare
  a real number against a hardcoded zero.
- **Email sends.** The upstream table carries them and the live modelling lane
  forbids them by name in two independent guards, so the column exists only to be
  rejected.
- **The community site identifier.** The downstream lane sums across it and never
  groups on it, and a single-property deployment carries one constant value, so
  keeping it in the key would split every reader-day for no reader.
- **The subscription table's registration state and provenance string.** Both are
  emitted as the same literal on every row upstream: constants that read like
  data.
- **Author and content-type lists on the content dimension.** The only tables that
  read them are on the not-built list.

### No event-deduplication layer

Stated rather than left as an absence, because an absent layer nobody wrote down
reads later as an oversight — and the obvious fix is to add a pass that hides the
guarantee it duplicates.

The draft of this work budgeted for a deduplication layer on the assumption the
contract might carry raw-ish events needing a dedupe on reader, event name,
timestamp and a session discriminator, preferring the row with more engagement
time. The contract as landed makes that unnecessary: `reader_event` declares a
non-nullable `event_id` as its deduplication key, requires a re-delivered event to
reuse that id, and the contract validator refuses a delivery with a repeated key.
Events therefore arrive pre-deduplicated, and a dedupe here could only ever be a
no-op.

### Not built, and waiting on a decision

`reader_email_day` counts **click events**. Whether the modelling unit should be
click events or distinct campaigns clicked is an open editorial question, and the
contract does not force it — `email_click` is event-grained and carries a
`campaign_id`, so both units are derivable. Answering it as campaigns needs a
`distinct_campaigns_clicked` column here.

It is deliberately not added in advance of the decision, because a column carrying
an unmade choice gets read as if the choice were made. The decision belongs to the
modelling owner and has to move with the model version.

---

## Dialect notes: Trino to DuckDB

Everything in the original translated. The risk was never expressibility; it was
silent semantic drift, and it concentrated in two places.

| Original | DuckDB | Note |
|---|---|---|
| `cardinality(x)` | `len(x)` | Both return null on a null list, so the null has to be handled before the division rather than after. |
| `CROSS JOIN UNNEST(x) AS t(v)` | same | **Unnest over an empty list emits no row.** That is the mechanism behind the "unresolved collapsed to zero" control: routing unresolved content to an empty list does not produce a null section, it produces no section, and the reading disappears with nothing raising. |
| `filter`/`transform` lambdas over a delimited string | not needed | The contract delivers `sections` as a real list, so the split-trim-filter chain the original needed has no equivalent here to get wrong. |
| `IS NOT DISTINCT FROM` in joins | same | Not used: every join key in this build is non-nullable by contract, which is a stronger guarantee than null-safe equality. |
| `date_parse(s, '%Y%m%d')` | not needed | No compact date strings: the contract carries instants. |
| `from_unixtime(...) AT TIME ZONE z` | `instant AT TIME ZONE z` | No epoch arithmetic, because the contract's timestamps are already typed. |
| `SUM(COALESCE(t, 0.0))` | `SUM(t)` | Deliberately **not** translated literally. The original coalesces unmeasured attention to zero; the contract says a null means not measured and must never be read as 0.0. So the sum skips nulls, is null when nothing was measured, and `measured_time_deliveries` carries the honest denominator. |
| `SUM(int_col)` | `CAST(SUM(int_col) AS BIGINT)` | A DuckDB sum over an integer widens to a 38-digit decimal, which arrives in Arrow as `decimal128` and in pandas as a column of `Decimal` objects: arithmetic against a float raises, and the obvious fix is a silent `astype` somewhere downstream. |

---

## Evidence

- `tests/test_intermediate_build.py` — the build end to end on the demo delivery,
  every declared grain, every column, and the values behind each derivation.
- `tests/test_intermediate_timezone.py` — a near-midnight event on **every**
  channel landing on the expected local day, checked against Python's own
  `zoneinfo` rather than against DuckDB; and the whole build run under three
  session timezones with identical output.
- `tests/test_intermediate_negative_controls.py` — every mutation trips exactly
  the checks it declares, and the committed evidence document matches a fresh
  render.
- `docs/intermediate-negative-controls.md` — the captured evidence.
- `tools/import_closure_check.py` — every module imports and the whole build runs
  with every cloud SDK's import blocked. Run as its own CI job.
