# The four questions, and who answers them

> **This is the adopter path.** It is the first thing to do, before you write any
> export. For working *on* this repository instead, see
> [Getting started for contributors](../README.md#getting-started-for-contributors).

Four things about your deployment cannot be read off your data, and the contract
will not guess them. Nothing runs until they are answered, which is deliberate:
every plausible default is right for some publisher and wrong for another, and
wrong without anything visibly breaking. A run under the wrong week anchor does
not error. It produces numbers.

The engineer doing the port cannot answer all four. Two are editorial and one is
half commercial, so this page exists to be taken to other people rather than
worked through alone. Copy the questions, get the answers, then fill in
[`examples/manifest-template.json`](../examples/manifest-template.json).

You are not being asked to get these right in some absolute sense. You are being
asked to make them explicit, and to record who chose, so that a number produced
six months from now can be traced to the decision behind it.

<!-- questionnaire:begin -->

## 1. Which timezone defines a day?

**Manifest key:** `day_boundary_timezone` — one IANA name, e.g. `America/Chicago`.

**Ask:** *When we say "Tuesday's traffic", which Tuesday do we mean — the
newsroom's, or UTC's?*

**Owner: whoever owns editorial reporting.** The person whose weekly numbers the
newsroom already argues about. If your organisation has a standing definition of
a reporting day, the answer is that one, and using a different one here means the
kernel's numbers will disagree with numbers people already trust.

**What it changes:** everything with a day or week in it, on every channel at
once. The two plausible answers — your editorial timezone and UTC — differ by
hours, so an evening click lands on a different day under each, and a
Saturday-evening click lands in a different *week*. There is no partial version
of this decision: it is applied once, by the engine, to every input.

**Do not** apply it yourself in your export. Emit instants in whatever zone your
warehouse actually stores (usually UTC) with the zone attached, and let the engine
convert. Converting in both places shifts every instant twice, and the second
shift is invisible because the column still looks correct.

## 2. Which weekday anchors a week, and at which end?

**Manifest key:** `week_anchor` — `{"weekday": ..., "position": "week_starts_on"
| "week_ends_on"}`.

**Ask:** *Does our week start on Sunday, or end on Sunday?*

**Owner: whoever owns editorial reporting** — the same person as question 1, and
worth asking in the same conversation.

**What it changes:** which days fall in which week, and therefore every weekly
score and every four-week window. Both conventions are in live use. They are not
a stylistic difference: the same day can land in either of two weeks depending on
which is chosen, and the two labellings can differ by up to six days.

This question catches people out because "our week starts on Monday" and "our
weeks are labelled by the Sunday they end on" can both be true of one
organisation, describing the same calendar from two ends. Ask for the *label*: if
someone hands you a weekly report, which date is in the header?

## 3. What counts as reading an article?

**Manifest key:** `article_view` — `definition_id`, plus `content_types` and
`event_kinds` selected from the contract's vocabulary.

**Ask:** *Does a liveblog count? A photo gallery? A newsletter viewed on the web?
When we report "articles read", which of those are in the number?*

**Owner: the newsroom.** This is an editorial definition, not an engineering one.
Audience or the managing editor, depending on how your organisation is arranged —
not the person running the port, who has no basis for choosing and every
temptation to pick whatever makes the pipeline simplest.

**What it changes:** every view-based feature, and therefore reading volume,
breadth and the section mix. Including galleries in a newsroom that publishes a
lot of them moves the distribution materially.

**Why `definition_id` exists:** the answer will change. When it does, every number
produced under the old selection is no longer comparable to the new ones, and
nothing in the output records which was in force. So the id travels with the
selection — change the selection, change the id, and a published figure stays
traceable to the definition that produced it.

## 4. Which readers are scored at all?

**Manifest key:** `scored_population` — `definition_id` plus `entitled_states`,
selected from the contract's subscription-state vocabulary.

**Ask:** two questions, and they have different owners.

**4a. Which of our billing states map onto the contract's states?**
**Owner: whoever knows the billing system.** Mechanical, and genuinely an
engineering job: your statuses are your own, and mapping `past_due` onto either
`grace` or `cancelled` depends on whether a reader in that state still has
access. Read your own dunning rules, not the state names.

**4b. Of those, which count as entitled — and therefore get scored?**
**Owner: whoever owns subscription policy.** A commercial decision, and the one
place on this page where the engineer running the port should stop and go and ask
somebody. *Are trials in? Are comped and staff accounts in? Is a reader in their
grace period still one of ours?*

**What it changes:** who is in the population at all. Subscription state is never
a model feature — it decides who gets fit and scored in the first place. A
deployment that scores paying readers only and one that also scores trials are
answering different questions from the same data, and the scores themselves do
not say which happened. That is why this one is declared rather than defaulted,
and why it carries its own id.

This is the question adopters are least equipped to answer, and the only one with
no worked example anywhere in this repository —
[`examples/publisher-declarations/baltimore-banner.json`](../examples/publisher-declarations/baltimore-banner.json)
explicitly declines it, because that publisher's answer belongs to a different
owner than the editorial three. That is not an omission to be fixed by supplying
a plausible value. It is what the shape of this decision looks like.

<!-- questionnaire:end -->

## And per optional input: do you have it, and from when?

Not one of the four, because it is a property of the delivery rather than a
policy: for each of `email_click`, `email_open` and `community_action`, say
`available` with a coverage floor, `not_deployed`, or `not_yet_launched`.

**Owner: whoever builds the export.** No conversation needed — you either have
the feed or you do not.

Two things worth knowing before you assume this is a blocker:

* **All three can be absent.** Four inputs are required; these three are not. See
  [what you lose per omitted input](../README.md#four-required-inputs-three-optional)
  — one of the three costs the model nothing at all.
* **`not_deployed` and `not_yet_launched` are different facts.** The first is
  permanent and drops the block for every window. The second drops it only for
  windows reaching back past the floor date, and keeps it for windows that do
  not. Declaring a recently-launched product `not_deployed` throws away the
  windows it *does* cover.

## When you have the answers

1. Copy [`examples/manifest-template.json`](../examples/manifest-template.json)
   into your delivery directory as `manifest.json`.
2. Replace each `ANSWER-REQUIRED`.
3. Run `engagement-kernel-validate <your-delivery>`. Before you fill anything in,
   it lists every outstanding decision at once — which is the shortest available
   description of what this page is asking.

Record the owner and the decision reference for each answer somewhere durable. If
you are following
[agent spec 1](agent-spec-1-map-your-warehouse.md), the mapping manifest has a
field for exactly that, and the lint refuses `TBD`.
