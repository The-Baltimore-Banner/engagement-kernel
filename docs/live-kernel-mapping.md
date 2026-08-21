# Mapping the live kernel tables onto the contract

This note is for whoever ports the modelling code. It says which tables in our
existing pipeline become which contract tables, which ones do **not** map and
why, and -- the part that matters most -- which translations change a number's
meaning on the way across.

It is written without vendor product names, hostnames or account identifiers on
purpose; where a vendor concept matters it is described by what it is. Nothing
here reproduces our population-exclusion predicates: see the last section.

## Scope: the live seam, not the convenient one

The contract is modelled on the **five-table seam the live subscriber-clustering
lane actually reads**, plus the three additional columns the serving-side
business-segment overlay reads:

| live input | read by |
| --- | --- |
| `user_channel_day` | the fit path and the serving path |
| `email_user_day_v2` | the fit path (clicks) and the serving overlay (opens) |
| `user_comment_day` | the fit path |
| `user_section_day` | the fit path *and* the topic-cluster lane |
| `subscription_state_history` | the fit path (the subscriber spine) |
| `web_user_content_day.views`, `app_user_content_day.views`, `email_user_day_v2.opens` | the serving overlay only |

There is a second, genuinely cloud-free local seam in the codebase that loads
three files. **It is the wrong one.** It belongs to a lane no scheduled job
runs; it is missing four of the seven inputs the published lanes read; it reads
the retired first-generation email table, whose engagement fields have been zero
since mid-2026; it consumes a signal the live lane bans by name; and its
active-day arithmetic double-counts a reader who was active on two channels the
same day, then clips at seven, so the habit feature saturates. Building the
contract on it would have looked simpler and shipped a quietly wrong kernel.

## Table-to-table mapping

### `user_channel_day` → `reader_event`

The live table is one row per reader per channel per **pre-bucketed day**, with
`views`, `sessions`, `total_time_seconds`, `events`. The contract's
`reader_event` is one row per **event** with an instant, and the engine derives
all four measures.

This is the largest single change, and it is the reason the contract exists in
this shape. Three consequences to plan for:

- **`views` is not a column any more.** It is a count of delivery events
  selected by the manifest's article-view definition. The definition becomes
  explicit and traceable rather than a filter string per surface.
- **`sessions` is not a sum.** In the live builder it is the per-reader-per-day
  **distinct** session count, renamed -- not the sum of the per-content session
  column. A rewrite that sums the obvious column produces a plausible, larger,
  wrong number, and the articles-per-session feature is where the error lands.
  The contract sidesteps this by carrying `session_id` per event so distinct
  sessions are counted, never summed.
- **`total_time_seconds` becomes `engagement_time_seconds` per event**,
  nullable, additive, with the three-delivery rate floor now stated in the
  contract rather than living in one function's default argument.

Dropped on the way across: the browser-scoped analytics id, the two-grain
`person_id` (see below), and the scroll column.

### `web_user_content_day` / `app_user_content_day` → `reader_event`

Not separate contract tables. Their only live consumer is the business-segment
overlay's activity anchor, which reads `views` per reader per day -- and that is
`SUM(views)` over exactly these rows, which `user_channel_day` already
aggregates. In the contract both are simply reader events with a `channel`.

**Verify before dropping them.** The equality is algebraic on the SQL, which is
a claim about code, not about live data. The porting work should prove it with an
equality check against a real partition. That check belongs to the porting
ticket, not to the contract.

### `content_dim` → `content`

Five of fourteen columns survive, and only three of those matter once the
author-level and content-type-level tables are dropped: the content id, the
cleaned section list, and the raw section as its fallback. The contract folds
those two into one `sections` list plus an explicit `section_resolution`, so
"the metadata did not resolve" is a declared outcome rather than something a
reader infers from a blank string.

Not mapped: the URL, the title, the publish date as used for anything other than
information, the location, paywall, tag and topic-tag columns, the first author
and the author category. No lane reads them, and two of them are forbidden model
columns already.

`published_ts` is in the contract as an optional, nullable instant. It is
information, not a bucketing key: reader activity is bucketed by the reader's
event instant, never by the content's publish date.

### `user_section_day` → derived from `reader_event` × `content`

The single most load-bearing live table after `user_channel_day`, and it does
**not** become a contract table. It is a per-reader-per-section-per-day
aggregate, and every one of its measures is derivable from reader events joined
to content sections -- section views, section time, distinct content ids. Making
it an input would re-import the pre-bucketed day *and* pre-commit the
fractional-attribution rule that the contract states explicitly (content in *n*
sections contributes 1/n to each).

The live table's `person_id` column is loaded by the fit path and never read; the
topic-cluster lane does select it. Whether the topic-cluster **output** needs to
carry a person identifier is a question for whoever consumes that output, and it
is an output question, not an input-contract question.

### `email_user_day_v2` → `email_click` + `email_open`

One live table splits into two contract tables, and the split is the point.

- `clicks` → `email_click`, **one row per click event**, carrying the list and,
  where the provider can attribute it, the campaign.
- `opens` → `email_open`, a separate table in a separate feature block,
  permitted use: reachability only.

The split makes "opens are never a model feature" structural instead of a
comment on a column, and it makes the click unit explicit. That second part
fixes a real defect: the click count **changed meaning between our two kernel
generations** -- distinct campaigns clicked in the first, click events in the
second -- and the surrounding comment kept describing the first while the lane
read the second. The observed gap was roughly 2× the distinct clickers and
nearly 5× the click events.

Not mapped: the sends column (banned as a model feature and read by nothing
else), the last-open and last-click timestamps (no reader), and the three
reconciliation-diagnostic counters, which are a property of that builder rather
than of the data.

`email_user_day` (the first-generation table) is **not mapped at all**. It is
built from a per-member activity feed capped at a fixed event count, so opens
crowd clicks out of the window and it under-captures clicks
non-deterministically; its engagement fields have read zero since mid-2026. It is
also the table the three-file seam reads, which is most of why that seam is the
wrong one.

### `user_comment_day` → `community_action`

Five per-day counted columns become one row per action with an `action_kind`.
Both directions of the translation matter:

- The live columns are **actions the reader performed** -- the actor id is the
  actor in every row, so likes and dislikes are given, not received. The
  contract makes that unambiguous by having no received-side value at all.
- The site column is a constant in our deployment, because the builder has
  already filtered to one property, and the lane sums across it and never groups
  on it. The contract **keeps** it, non-nullable: a single-property deployment
  supplies one constant value, and a multi-property deployment stays
  expressible. A dropped dimension is much harder to add back than a constant
  one is to ignore.

Community data has a hard floor: commenting launched in late 2025 and there is
no data before it. That is exactly what the manifest's `not_yet_launched` status
and `available_from` floor exist for -- a baseline reaching back past the launch
must drop the community block, not read zeros.

### `subscription_state_history` → `subscription_span`

The closest one-to-one mapping in the set, and the one the previous design
already had right: one row per reader per state interval, a start, and a nullable
end meaning an open span. The contract adds three things:

- **`payer_type`**, optional and nullable, because the serving path needs it and
  a state history alone cannot supply it. Null means unknown, never
  `individual`.
- **The scored population as a declaration.** `entitled_states` in the manifest,
  with its own definition id. Our fit path hardcodes two states; our serving path
  filters three differently-named labels from a separate, several-hundred-line
  publisher-specific derivation. Neither is portable, and the difference between
  them is invisible in the output.
- **Half-open intervals, stated.** `[start_ts, end_ts)`, at most one open span
  per reader, no overlaps -- all three checked by the validator, because "status
  as of a date" is only well defined if they hold.

Not mapped: the registration-state column, which the builder emits as a literal
constant for every row, and the provenance string, likewise.

The live serving path does not read this table at all: in seed mode the spine
universe *is* a membership snapshot, so subscribers are carried with null
entitlement spans. That is worth knowing because it means the two live paths
disagree about where subscription state comes from, and the contract picks the
interval table -- the one that can answer "as of a historical date".

### `reader` — new

No live table corresponds to it. Our pipeline emits two identity columns in two
namespaces: a resolved-person id from the subscription platform, and a second
column that is the resolved id where present and a browser id otherwise -- a
**union of two grains in one column**. The live lane never uses the second one
and treats the resolved id as its key, dropping unresolved rows implicitly.
Meanwhile email is keyed on the resolved id through a lowercased-address
crosswalk, and comments are keyed on the comment platform's third-party id,
which the source itself documents as *either* a resolved id or an analytics id --
so three id spaces are joined as though they were one.

`reader` exists to make that impossible: one registry, one declared grain, and
every other table's reader reference checked against it.

## Not mapped, and why

| live table | why not |
| --- | --- |
| `user_author_day` | No lane reads it. Author-level preference is in no published model. |
| `user_content_type_day` | No lane reads it. |
| `user_device_day` | No lane reads it, and it is the most expensive table in the build: it re-reads the raw web and app feeds from scratch instead of deriving from the per-content table. Dropping it removes a second full scan. |
| `person_day_activity_v1` | Built by the local pipeline, declared in no contract, read by no lane, 60+ output columns. |
| `email_user_day` (first generation) | Superseded; engagement fields zero since mid-2026. See above. |
| `web_user_content_day`, `app_user_content_day` | Their one consumer's anchor is equivalently available from the channel-day table. Re-point the overlay and prove the equality. |
| `user_content_day` | An intermediate, not an input: it feeds the channel-day and section-day tables and has no downstream reader. In the contract its role disappears entirely -- events *are* the intermediate. |

**Columns dropped from tables that do map:** the scroll column wherever it
appears (banned by pattern in two independent guards downstream, and hardcoded
to zero on app surfaces); the browser-scoped analytics id; the two-grain
`person_id`; the email sends column and its three reconciliation counters; the
two literal-constant columns on the state history; and nine unread columns on
the content dimension.

**"Not mapped" is not "safe to delete."** Three of these tables have consumers
inside our own estate that are not modelling lanes -- a warehouse mirror, a
daily row-count validation job, and a couple of inspection scripts. This note
resolves the *portability* question only. Retirement is a separate decision with
separate evidence.

## What the contract cannot express, by design

| concept | where it belongs |
| --- | --- |
| The section-to-topic-bucket map | Reference-engine configuration, versioned, with a catch-all bucket and a maximum permitted catch-all share. It is a curated editorial taxonomy, not input data, and it sizes the model matrix. |
| The publisher-specific membership taxonomy | Deployment configuration. The contract's subscription intervals plus the manifest's entitled-state list must be sufficient on their own; where they are not, the reference engine needs a documented degradation rather than a hidden dependency. |
| The business-segment overlay | An engine output, computed from contract inputs. |
| Scroll depth | Nowhere. Out of scope, and refused by name. |
| Population-exclusion predicates | Deployment configuration, as opaque reader ids. Our live definition carries four hardcoded predicates -- an employee-access term, a product-test term, and two personal-address fragments. They are operational policy, not logic, and two of them are personal identifiers, so **none of them is reproduced in this repository in any form.** The contract takes a list of ids and refuses an entry that looks like an address or a pattern instead of an id. |

## Traps to carry into the port

Collected in one place because each of them fails quietly.

1. **Three day boundaries in one live system.** Web and app convert explicitly
   to a named editorial zone; the email tables never convert and are effectively
   UTC; the comment table inherits an undocumented vendor partition zone. One
   configured timezone, applied once by the engine, is the fix -- and the reason
   the contract refuses a pre-bucketed date.
2. **Two week anchors in one live system.** One lane's weeks end on a Sunday,
   the other's start on one. Both call the result a week.
3. **`sessions` is a renamed distinct count, not a sum.** Summing the obvious
   column is plausible and wrong.
4. **The click unit changed meaning between kernel generations** and the comment
   did not follow. Whichever unit a port picks must move with the model version:
   a frozen fit calibrates the cadence axis against the unit it was fit on.
5. **The existing kernel reader numeric-coerces every column outside a small id
   allowlist**, which is why the live serving path cannot read the
   subscription-span table through it at all. Do not inherit that split. One
   typed reader over the whole contract, coercing nothing, is what this repo
   ships -- a wrong type is reported with the column named, not patched into
   something loadable.
6. **Unresolved metadata is not "no reading."** Keep the three-way distinction
   between read nothing, read something uncategorisable, and read too little to
   categorise.
7. **An absent optional input is not a column of zeros.** It selects a named
   alternate feature set, and the run reports which one it used.
