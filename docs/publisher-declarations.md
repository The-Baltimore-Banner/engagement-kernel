# The four declarations a publisher has to make

The contract deliberately has no default for any of these. A missing value is a
hard failure, not an assumed UTC and not an assumed content-type set, because
every plausible default here is wrong for somebody and wrong silently. This
document says what each declaration means, what it changes, and — as a worked
example — one publisher's answers and what they cost that publisher.

If you are bringing your own data, read this as the list of questions to take to
whoever owns the answers. The engine supplies the mechanism; you supply the
selection.

> **The worked example is an example.** The values below are The Baltimore
> Banner's, recorded here because a real answer is more useful than a
> placeholder. They are not defaults, and copying them is not a decision. The
> synthetic demo delivery in [`examples/demo-delivery/`](../examples/demo-delivery)
> deliberately declares a *different* article-view selection, so that anything
> reading the selection from the wrong place is visibly wrong rather than
> accidentally right.

The machine-readable form of the example is
[`examples/publisher-declarations/baltimore-banner.json`](../examples/publisher-declarations/baltimore-banner.json).
It is contract-shaped: the keys splice into a `manifest.json` unchanged.
`tests/test_publisher_declarations.py` parses it against the contract and fails
if the table below and the file disagree, so this document cannot drift away from
the values it describes.

<!-- declarations-table:start -->

| declaration | value |
| --- | --- |
| `day_boundary_timezone` | `America/New_York` |
| `week_anchor.weekday` | `Sunday` |
| `week_anchor.position` | `week_ends_on` |
| `article_view.definition_id` | `baltimore-banner-article-only-v1` |
| `article_view.content_types` | `article` |
| `article_view.event_kinds` | `content_delivery` |

<!-- declarations-table:end -->

---

## 1. What counts as an article view

**Declaration:** `article_view` — a `definition_id`, the content types that
count, and the event kinds that count.

The mechanism is three conditions, all expressible in the contract: the event
represents content being *delivered* (a page or screen shown) rather than an
interaction with content already shown; it names a resolvable content id; and
that content's type is one the publisher counts.

**The example publisher counts `article`, and nothing else.**

| content type | in | why |
| --- | --- | --- |
| article | ✅ | |
| newsletter | ❌ | Newsletter reading is recorded as email clicks, and the email channel has its own anchor. Counting newsletters here would count the same reading twice. |
| liveblog | ❌ | Not measured today. A gap, not a judgement — see below. |
| gallery | ❌ | Not measured today. |
| podcast | ❌ | Not measured today. |
| video | ❌ | Not measured today. |

**Four of those five exclusions are gaps, not decisions**, and that distinction
matters more than it looks. The publisher intends to measure liveblogs,
galleries, podcasts, audio listens and video watches, and to count them, in a
later revision. So the recorded answer is not "these do not count" but "these
are not yet measured, and the definition id will change when they are."

Two consequences follow, and both are easy to get wrong.

**The definition id has to move when the selection widens.** Every view-based
feature changes when the content-type set changes, so a widened set published
under an unchanged id silently restates every historical view count. The id
carries the selection precisely so that two numbers produced under two
definitions are distinguishable rather than comparable-looking.

**Audio has no home in the vocabulary yet.** `CONTENT_TYPES` has `podcast`, but
read-aloud audio on an article is a different act from a podcast episode. If a
publisher needs to distinguish them, that is a contract *version* change — a new
enum member — not a configuration edit. Nothing is lost by waiting: until the
signal is measured there is nothing to classify.

## 2. What to do when the signal is only partly present

**Declaration:** none. This one is a property of the engine, and it is worth
knowing before you read a view count.

A publisher's content-type metadata is rarely complete, and it is often much
less complete on one surface than another. The example publisher's app
instrumentation carries a content-type parameter on a minority of screen views —
roughly two in five — while its web instrumentation carries one on nearly all
page views.

The engine's rule: **a delivery whose content id has no matching content row
still counts as a view, and is attributed to the unresolved section sentinel.**
The alternative — dropping it — collapses "we do not know what they read" into
"they did not read", which is the exact distinction
[the unresolved-metadata rule](canonical-input-contract.md) exists to preserve.
The volume is not hidden: it is all on one sentinel section where it can be
counted, and a deployment that wants to know how bad its coverage is can measure
it there.

**State the consequence rather than discovering it.** Where one surface's
content-type metadata is sparse, that surface's view count is *not* comparable to
a surface whose metadata is complete. For the example publisher, web views are
confirmed articles and app views are closer to "every screen view naming a piece
of content". Both are called views, and the contract cannot detect the difference
for you. Do not present a single uniform article-view definition as though every
channel measured it equally well.

There is a cheaper way to hold this than accepting the asymmetry forever, and it
is worth doing at the adapter: **emit every delivery, and populate the content
row with its real type**, even for types the current definition does not count.
Then unmeasured content is excluded from views because its type says so rather
than because its metadata is missing, it stays visible in the raw event total,
and widening the definition later is a one-line manifest edit instead of a new
data feed.

## 3. Which timezone defines a day

**Declaration:** `day_boundary_timezone` — one IANA timezone, applied by the
reference engine to every channel, once.

**The example publisher declares `America/New_York`.**

The engine's shape matters here as much as the value. Every timestamp in the
contract is a timezone-aware instant and a timezone-naive column is refused
outright; the engine's own session zone is pinned to UTC so that a bucketing
expression which forgot to convert is *visibly* wrong rather than accidentally
right on a machine that happens to be set to the publisher's zone; and a
pre-bucketed calendar-date column is refused by name on input, because a producer
supplying one has already applied some timezone and no validator can recover
which. So the declared zone is applied in exactly one place, and applying it
twice is impossible.

**Does the editorial day match the data day?** For the example publisher, yes:
the day is midnight to midnight in the declared zone. The question was put
specifically — a newsroom whose sense of "today" runs past midnight into the late
shift would need a different answer, and the contract could not express it
without a boundary offset — and no such carve-out was wanted. Recording that the
question was asked and declined is part of the answer.

**What this changes about the example publisher's existing numbers.** This is the
declaration with the largest silent-wrongness risk, and it is not hypothetical:
that publisher's live pipeline currently has *four* different day boundaries. Web
and app convert explicitly to the editorial zone. Both email tables never convert
at all and are therefore effectively UTC. The community table inherits a vendor
partition string whose zone is undocumented.

Declaring one zone asserts that three of those four are wrong. Concretely, for
UTC-stamped email:

- an evening click in the declared zone moves back one calendar day;
- a weekend-evening click moves back one *week*, because it crosses the week
  anchor as well as the day boundary;
- `email_active_days` over any 7- or 28-day window changes, recency changes, and
  weekly-bin consistency changes;
- those are model inputs, so cluster membership moves.

None of that is a reason not to declare a zone — it is the reason to. But it does
mean a port cannot claim numeric email parity against the pre-existing pipeline,
and a parity check that expects it will fail for the right reason and be read as
the wrong one. Say it up front.

## 4. Which weekday anchors a week, and at which end

**Declaration:** `week_anchor` — a weekday, and which end of the week it sits on
(`week_starts_on` or `week_ends_on`).

**The example publisher declares `Sunday` / `week_ends_on`.**

Both conventions are in live use, and the reason this is contract data rather
than a constant is that the example publisher's own two lanes disagree: one ends
weeks on a Sunday, the other starts weeks on a Sunday. Both call the result a
week. They differ by up to six days.

**What this changes.** Declaring one of them makes the other lane's weekly
history non-comparable with the engine's output. The declaration picks the
convention used by the lane that actually publishes; reconciling the two lanes to
each other is separate work in the publisher's own pipeline, and it is worth
doing, because two things called a week that differ by six days will eventually
be compared by somebody.

---

## Still open, under its own owner

**The email cadence unit: click events, or distinct campaigns clicked?** The
contract fixes the *delivered* unit — one row per click event — so both are
derivable from a conforming delivery, and nothing here forecloses the choice. But
the modelling choice is not fixed, the two are not interchangeable in a cadence
feature, and whichever is chosen has to move together with the model version,
because recalibrating a cadence axis on a different unit republishes plausible
and different clusters. Owner: the modelling owner.

That question is deliberately *not* answered by a column in this repo. A column
carrying an unmade decision gets read as though the decision were made.
