# Contract reference

**Generated file.** Produced by `python3 tools/render_contract_reference.py --write`
from `src/engagement_kernel/contract/spec.py`, and compared against a fresh render by
`tests/test_contract_docs.py`. Do not edit it by hand -- edit the spec.

For what the derived concepts *mean* -- an article view, an active day, the scored
population, the day boundary, the reader-id grain -- see
[canonical-input-contract.md](canonical-input-contract.md). This file is the field-level
reference only.

- contract name: `engagement-kernel-input`
- contract version: `1.0.0`
- tables: 7 (4 required, 3 optional)
- fields: 36

## A delivery

One directory. One Parquet file per table, named for the table, plus
`manifest.json`. Validate it with:

```bash
engagement-kernel-validate path/to/delivery
```

Exit status `0` conforms, `1` does not conform, `2` the verdict could not be trusted
(no manifest, or an invalid one).

## Tables

### `reader` (**required**)

`reader.parquet` -- The reader registry. Declares every reader the delivery covers, once, with its identity grain. Every other table references it.

- **grain**: One row per reader.
- **deduplication key**: `reader_id`
- **null behaviour**: No nullable fields. A reader that cannot be named at the declared grain does not belong in the delivery at all.
- **feature block**: `spine`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `reader_id` | `string` | **no** | -- | Opaque pseudonymous identifier for one reader. Must not be, or encode, an email address, a login name, or any other personal datum, and must not carry a namespace prefix -- a prefixed id is the signature of two grains sharing one column. |
| `id_grain` | `string` | **no** | `resolved_person` | What the id identifies. Exactly one value is permitted at this contract version: a resolved person. A device, browser, app install or session is not a reader. |

**Notes.**

- More than one distinct id_grain in this table is rejected as a mixed-grain id column, separately from the enum check, so the failure message names the actual problem.

### `reader_event` (**required**)

`reader_event.parquet` -- Web and app reading activity, one row per event, with the instant it happened. Views, distinct sessions, engagement time and raw event counts are all derived from this table by the reference engine, in the single timezone the manifest declares.

- **grain**: One row per reader event.
- **deduplication key**: `event_id`
- **null behaviour**: content_id is null only on an interaction event. session_id is always present. engagement_time_seconds is null when attention was not measured, which is not the same as measured-and-zero: a null must never be read as 0.0.
- **feature block**: `consumption`
- **event-time column**: `event_ts`
- **reader registry reference**: `reader_id` -- every value must appear in `reader.reader_id`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `event_id` | `string` | **no** | -- | Stable opaque identifier for this event, unique across the delivery. Re-delivering the same event must reuse its id so the deduplication key can do its job. |
| `reader_id` | `string` | **no** | -- | The reader who produced the event. Must exist in `reader`. |
| `event_ts` | `timestamp[us, tz=UTC]` | **no** | -- | The instant the event happened, timezone-aware. The calendar day it belongs to is computed by the engine in the manifest's timezone; the producer must not pre-bucket it. |
| `channel` | `string` | **no** | `web`, `app` | Surface the event happened on. |
| `event_kind` | `string` | **no** | `content_delivery`, `content_interaction` | Whether content was delivered to the reader (a page or screen shown) or the reader interacted with content already shown. Only deliveries are candidates for a view; which deliveries count as an article view is set in the manifest, not here. |
| `content_id` | `string` | yes | -- | The content the event concerns. Required on a delivery. A content_id with no matching row in `content` is permitted and means the metadata did not resolve -- it is not an error. |
| `session_id` | `string` | **no** | -- | Opaque identifier for the visit this event belongs to. Present so the engine can count distinct sessions per reader-day directly; a session count supplied as a pre-aggregated number cannot be checked and is not accepted. |
| `engagement_time_seconds` | `double` | yes | -- | Attention attributable to this event, in seconds. Null means not measured. Additive across events. Any rate derived from it is undefined below the minimum-deliveries threshold the contract declares (see ENGAGEMENT_TIME_MIN_DELIVERIES). Non-negative. |

**Conditional rules.**

- `delivery_requires_content_id`: when `event_kind` is in `content_delivery`, `content_id` must be `non_null`. A delivery with no content id cannot be attributed to a piece of content, so it cannot be a view of one.

**Notes.**

- Scroll depth is deliberately not in this table. See SCROLL_DEPTH_SCOPE_NOTE.

### `content` (**required**)

`content.parquet` -- The content dimension: what each piece of content is and which sections it belongs to. Its section list is what makes per-section reading measurable, with a view of content in n sections contributing 1/n to each.

- **grain**: One row per piece of content.
- **deduplication key**: `content_id`
- **null behaviour**: sections is null or empty exactly when section_resolution is 'unresolved'. Unresolved is a declared outcome, not a gap: a reader whose reading all landed on unresolved content read something, and must not be reported as having read nothing.
- **feature block**: `topic`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `content_id` | `string` | **no** | -- | Opaque identifier for the content, matching reader_event.content_id. |
| `content_type` | `string` | **no** | `article`, `liveblog`, `gallery`, `video`, `podcast`, `newsletter`, `other` | What kind of thing this is, in the publisher's own editorial taxonomy mapped onto the contract's vocabulary. The manifest says which of these types an article view may count. |
| `section_resolution` | `string` | **no** | `resolved`, `unresolved` | Whether this content's section metadata resolved to at least one usable section. Declared by the producer rather than inferred from an empty list, so 'we have no metadata' cannot be confused with 'we forgot the column'. |
| `sections` | `list<item: string>` | yes | -- | The sections this content belongs to, as opaque section keys. Non-empty when resolved; null or empty when unresolved. Order carries no meaning and duplicates are not permitted. |
| `published_ts` | `timestamp[us, tz=UTC]` | yes | -- | When the content was first published, timezone-aware. Null when unknown. Not used for bucketing reader activity. |

**Conditional rules.**

- `resolved_requires_sections`: when `section_resolution` is in `resolved`, `sections` must be `non_empty_list`. Content declared resolved must name at least one section.
- `unresolved_forbids_sections`: when `section_resolution` is in `unresolved`, `sections` must be `null_or_empty_list`. Content declared unresolved must not also carry sections; one of the two statements would be false.

### `subscription_span` (**required**)

`subscription_span.parquet` -- Subscription state as a history of intervals, so a reader's status can be resolved as of any historical date. Status defines the spine -- which readers are fit and scored -- and is never a model feature.

- **grain**: One row per reader per state interval.
- **deduplication key**: `reader_id`, `start_ts`
- **null behaviour**: end_ts is null for an open span, and at most one span per reader may be open. payer_type is null when the billing system cannot distinguish payer types; null means unknown, never 'individual'.
- **feature block**: `spine`
- **reader registry reference**: `reader_id` -- every value must appear in `reader.reader_id`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `reader_id` | `string` | **no** | -- | The reader whose state this is. Must exist in `reader`. |
| `state` | `string` | **no** | `registered_unpaid`, `trial`, `active`, `grace`, `payment_failed`, `cancelled`, `expired` | The reader's commercial state for the whole interval. Mapping a publisher's own billing states onto these seven is a business decision the publisher records; the contract fixes the vocabulary, not the mapping. |
| `payer_type` | `string` | yes | `individual`, `institutional`, `guest` | Who pays for the subscription. Optional, because a state history alone cannot supply it. Never a model feature. |
| `start_ts` | `timestamp[us, tz=UTC]` | **no** | -- | Instant the interval begins, inclusive, timezone-aware. |
| `end_ts` | `timestamp[us, tz=UTC]` | yes | -- | Instant the interval ends, EXCLUSIVE, timezone-aware. Null means the span is still open. Intervals are half-open [start_ts, end_ts) so consecutive spans meet without overlapping and without a one-unit gap. |

**Notes.**

- Population exclusions are manifest configuration, as a list of opaque reader ids. This table carries no personal field, so an exclusion predicate over a personal attribute is not expressible against it.

### `email_click` (optional)

`email_click.parquet` -- Email clicks: the only email signal the models may use. A click is an intentional act by the reader.

- **grain**: One row per click event.
- **deduplication key**: `event_id`
- **null behaviour**: campaign_id is null when the provider cannot attribute the click to a campaign. list_id is always present.
- **feature block**: `email_cadence`
- **event-time column**: `event_ts`
- **reader registry reference**: `reader_id` -- every value must appear in `reader.reader_id`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `event_id` | `string` | **no** | -- | Stable opaque identifier for this click event. |
| `reader_id` | `string` | **no** | -- | The reader who clicked. Must exist in `reader`. |
| `event_ts` | `timestamp[us, tz=UTC]` | **no** | -- | The instant of the click, timezone-aware. Not pre-bucketed: this is the input whose day boundary is most often inherited from a vendor's own zone. |
| `list_id` | `string` | **no** | -- | Opaque identifier for the list or newsletter the clicked message belonged to. Present so a deployment can restrict the email cadence signal to specific lists. |
| `campaign_id` | `string` | yes | -- | Opaque identifier for the individual send. Present so distinct campaigns clicked can be counted as well as click events; which of the two a model uses is a modelling decision recorded as an open question, not a property of this table. |

**Notes.**

- THE CLICK UNIT IS ONE ROW PER CLICK EVENT. Not one row per campaign clicked, not one row per recipient, not one row per day. A provider that supplies opens here is non-conformant, and no validator can detect it from the values -- which is exactly why opens have their own table with its own declared permitted use rather than a flag on this one.

### `email_open` (optional)

`email_open.parquet` -- Email opens, for reachability only. Machine opens inflate this signal and cannot be cleaned out of it, so an open says the message reached a reachable inbox and nothing about the reader's interest.

- **grain**: One row per open event.
- **deduplication key**: `event_id`
- **null behaviour**: As email_click.
- **feature block**: `deliverability`
- **event-time column**: `event_ts`
- **reader registry reference**: `reader_id` -- every value must appear in `reader.reader_id`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `event_id` | `string` | **no** | -- | Stable opaque identifier for this open event. |
| `reader_id` | `string` | **no** | -- | The reader whose message was opened. Must exist in `reader`. |
| `event_ts` | `timestamp[us, tz=UTC]` | **no** | -- | The instant of the open, timezone-aware. |
| `list_id` | `string` | **no** | -- | Opaque identifier for the list or newsletter. |
| `campaign_id` | `string` | yes | -- | Opaque identifier for the individual send. Null when unattributed. |

**Notes.**

- PERMITTED USE: reachability and deliverability reporting only. This table is a separate table, in a separate feature block, precisely so that 'opens are never a model feature' is structural rather than a comment on a column.

### `community_action` (optional)

`community_action.parquet` -- Community participation: actions the reader performed on a comment surface. Splits into contribution (authoring) and reaction (responding to others) downstream.

- **grain**: One row per action.
- **deduplication key**: `event_id`
- **null behaviour**: site_id is always present: a single-property deployment supplies one constant value rather than a null, so the column never has to be read as 'unknown'. target_content_id is null when the action was not attached to a piece of content.
- **feature block**: `community`
- **event-time column**: `event_ts`
- **reader registry reference**: `reader_id` -- every value must appear in `reader.reader_id`

| field | type | nullable | enum | definition |
| --- | --- | --- | --- | --- |
| `event_id` | `string` | **no** | -- | Stable opaque identifier for this action. |
| `reader_id` | `string` | **no** | -- | The reader who PERFORMED the action. Never the reader who received it. Must exist in `reader`. |
| `event_ts` | `timestamp[us, tz=UTC]` | **no** | -- | The instant of the action, timezone-aware. |
| `action_kind` | `string` | **no** | `post_created`, `reply_created`, `like_given`, `dislike_given`, `flag_given` | What the reader did. Every value is an action given or authored by this reader: 'like_given' is a like this reader handed out, not one they received. There is no received-side value, because a received reaction measures somebody else. |
| `site_id` | `string` | **no** | -- | Opaque identifier for the community property the action happened on. Required so a multi-property deployment stays expressible; the engine sums across it by default. |
| `target_content_id` | `string` | yes | -- | The content the action was attached to, when there is one. Null for actions not tied to a piece of content. |

## Manifest

`manifest.json` states what cannot be read off the files. Nothing here has a
default; a missing value is a hard failure rather than a guess.

| key | meaning |
| --- | --- |
| `contract_name` | must be `engagement-kernel-input`, so a directory cannot be mistaken for a different contract that happens to share table names |
| `contract_version` | the contract version the delivery was built against |
| `day_boundary_timezone` | the single IANA timezone that decides which calendar day an instant belongs to, for every channel |
| `week_anchor.weekday` | the weekday that anchors a week |
| `week_anchor.position` | which end of the week that weekday sits on: `week_starts_on` or `week_ends_on` |
| `article_view.definition_id` | names the editorial decision, so a published number is traceable to the definition it was produced under |
| `article_view.content_types` | which content types an article view may count |
| `article_view.event_kinds` | which event kinds an article view may count |
| `scored_population.definition_id` | names the population decision, for the same reason |
| `scored_population.entitled_states` | which subscription states are in the scored population |
| `optional_inputs.<table>.status` | `available`, `not_deployed`, `not_yet_launched` |
| `optional_inputs.<table>.available_from` | the coverage floor date, required when the status is `available` and forbidden otherwise |
| `population_exclusions` | opaque reader ids excluded from the scored population; deployment configuration, never a predicate in code |

## Closed vocabularies

| vocabulary | values |
| --- | --- |
| reader id grains | `resolved_person` |
| reader event channels | `web`, `app` |
| reader event kinds | `content_delivery`, `content_interaction` |
| content types | `article`, `liveblog`, `gallery`, `video`, `podcast`, `newsletter`, `other` |
| section resolutions | `resolved`, `unresolved` |
| subscription states | `registered_unpaid`, `trial`, `active`, `grace`, `payment_failed`, `cancelled`, `expired` |
| payer types | `individual`, `institutional`, `guest` |
| community action kinds | `post_created`, `reply_created`, `like_given`, `dislike_given`, `flag_given` |
| availability statuses | `available`, `not_deployed`, `not_yet_launched` |
| week anchor positions | `week_starts_on`, `week_ends_on` |

## Declared exclusions and thresholds

- **Engagement-time rate floor**: `3` deliveries in a window. Below it, any per-view rate derived from engagement time is undefined rather than zero.
- **Scroll depth**: Scroll depth is deliberately OUT OF SCOPE. It is not a field in any table and a column whose name contains 'scroll' is rejected. It is excluded on evidence, not on principle: where it has been measured it was carried on several aggregates and read by nothing, and on app surfaces it is commonly not measurable at all, so a mixed-surface deployment would be comparing a real number against a hardcoded zero.
- **Never a model feature**: `state`, `payer_type`, `email_open`, `opens`, `sends`. Subscription state and payer type define the population, not the features; email opens and sends are reachability signals that machine opens inflate and nothing can clean.

**Refused column names.** A column whose name contains one of these is rejected with its own reason rather than a generic 'unexpected column':

| substring | why it is refused |
| --- | --- |
| `scroll` | scroll depth is declared out of scope by this contract |
| `event_date` | a pre-bucketed calendar date re-imports the day-boundary defect this contract exists to prevent; supply the event instant instead |
| `local_date` | a pre-bucketed calendar date re-imports the day-boundary defect this contract exists to prevent; supply the event instant instead |
| `email` | the contract requires no personal data and must not carry an email address |
| `ip_address` | the contract requires no personal data and must not carry an IP address |
| `ip_addr` | the contract requires no personal data and must not carry an IP address |
| `phone` | the contract requires no personal data and must not carry a phone number |
| `first_name` | the contract requires no personal data and must not carry a person's name |
| `last_name` | the contract requires no personal data and must not carry a person's name |
| `full_name` | the contract requires no personal data and must not carry a person's name |
| `postal` | the contract requires no personal data and must not carry a postal address |
| `birth` | the contract requires no personal data and must not carry a date of birth |

## Feature blocks and honest degradation

Each table feeds one named feature block. An optional input that is absent drops its block and changes the feature-set id; it never becomes a column of zeros.

| block | source table | optional |
| --- | --- | --- |
| `spine` | `reader` | no |
| `consumption` | `reader_event` | no |
| `topic` | `content` | no |
| `spine` | `subscription_span` | no |
| `email_cadence` | `email_click` | yes |
| `deliverability` | `email_open` | yes |
| `community` | `community_action` | yes |

Feature-set ids: `full` when every block is
supported, otherwise the dropped blocks named in a fixed order --
`no-email-cadence`, `no-deliverability`, `no-community` -- joined with `+`.
