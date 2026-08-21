# Validator negative controls

**Generated file.** Produced by `python3 tools/capture_negative_controls.py --write`
and checked against a fresh render by `tests/test_negative_controls.py`, so it cannot
drift from the validator's actual output. Do not edit it by hand.

Each case below starts from the conformant synthetic delivery in
`engagement_kernel.contract.demo`, applies exactly one mutation, and runs the real
validator over the result. The conformant base passes; that is asserted in the same
test file, because a suite of failing fixtures proves nothing if the passing case
would fail too.

The test asserts the **exact** set of finding codes each case produces, not merely
that something failed. Where a case legitimately produces two codes, the case says
why. Exit status is part of the assertion: `1` means the delivery was read and does
not conform, `2` means the verdict could not be trusted at all.

Cases: 50.

## reader

### `reader.missing_required_column`

- **defect class**: missing required column
- **table**: `reader`
- **mutation**: drop the `id_grain` column
- **expected code**: `MISSING_REQUIRED_COLUMN`
- **expected column**: `id_grain`
- **expected rows**: 9
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               9 rows
        MISSING_REQUIRED_COLUMN reader column=id_grain rows=9: the contract requires column 'id_grain' and the file does not have it; columns present: ['reader_id']
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader.wrong_dtype`

- **defect class**: wrong dtype
- **table**: `reader`
- **mutation**: supply `reader_id` as int64 instead of string
- **expected code**: `COLUMN_TYPE_MISMATCH`
- **expected column**: `reader_id`
- **expected rows**: 9
- **expected exit status**: 1
- **why it matters**: The registry is unreadable when its key column has the wrong type, so no other table is checked for membership of it. That is why the type check runs before the value checks: the alternative is nine tables reporting orphaned ids.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               9 rows
        COLUMN_TYPE_MISMATCH reader column=reader_id rows=9: column 'reader_id' is int64, the contract declares string. The value is not coerced: coercing is how a label becomes a number and a date becomes nothing
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader.null_in_non_nullable`

- **defect class**: null in a non-nullable field
- **table**: `reader`
- **mutation**: null out one `id_grain`
- **expected code**: `NULL_IN_NON_NULLABLE`
- **expected column**: `id_grain`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               9 rows
        NULL_IN_NON_NULLABLE reader column=id_grain rows=1: column 'id_grain' is declared non-nullable and holds 1 null value(s)
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader.duplicate_dedup_key`

- **defect class**: duplicate on the stated dedup key
- **table**: `reader`
- **mutation**: append a second row for the first reader
- **expected code**: `DUPLICATE_DEDUP_KEY`
- **expected column**: `reader_id`
- **expected rows**: 2
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               10 rows
        DUPLICATE_DEDUP_KEY reader column=reader_id rows=2: the deduplication key (reader_id) is not unique: 1 key value(s) cover 2 row(s). A duplicate on this key double-counts every measure derived from the table
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader.enum_out_of_range`

- **defect class**: out-of-range enum value
- **table**: `reader`
- **mutation**: set one `id_grain` to `session`
- **expected code**: `ENUM_VALUE_OUT_OF_RANGE`
- **also reported**: `MIXED_READER_ID_GRAIN`
- **expected column**: `id_grain`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: Two codes, and both are correct. The contract permits exactly one grain, so any out-of-vocabulary grain value in a registry that also holds the permitted one is simultaneously an unknown value and a mixed-grain column.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               9 rows
        ENUM_VALUE_OUT_OF_RANGE reader column=id_grain rows=1: column 'id_grain' holds 1 row(s) whose value is outside the contract vocabulary. Permitted: ['resolved_person']. Found: ['session']
        MIXED_READER_ID_GRAIN reader column=id_grain rows=1: the reader registry mixes identity grains: ['resolved_person', 'session']. One reader id column may hold exactly one grain. Two grains in one column make every distinct-reader count and every cross-channel join meaningless, and no downstream check can see it
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 2 finding(s)
$ echo $?  ->  1
```

### `reader.mixed_id_grain`

- **defect class**: mixed reader-id grain (one id column, two grains)
- **table**: `reader`
- **mutation**: declare two readers at `device_browser` grain alongside resolved people
- **expected code**: `MIXED_READER_ID_GRAIN`
- **also reported**: `ENUM_VALUE_OUT_OF_RANGE`
- **expected column**: `id_grain`
- **expected rows**: 2
- **expected exit status**: 1
- **why it matters**: The rejection this contract exists for. A device id and a person id in one column make every distinct-reader count and every cross-channel join meaningless, and nothing downstream can see it -- so it is refused here, by name, rather than discouraged in prose.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

FAIL  reader               9 rows
        ENUM_VALUE_OUT_OF_RANGE reader column=id_grain rows=2: column 'id_grain' holds 2 row(s) whose value is outside the contract vocabulary. Permitted: ['resolved_person']. Found: ['device_browser']
        MIXED_READER_ID_GRAIN reader column=id_grain rows=2: the reader registry mixes identity grains: ['device_browser', 'resolved_person']. One reader id column may hold exactly one grain. Two grains in one column make every distinct-reader count and every cross-channel join meaningless, and no downstream check can see it
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 2 finding(s)
$ echo $?  ->  1
```

### `reader_event.namespaced_reader_id`

- **defect class**: namespaced reader id (the visible half of a mixed id space)
- **table**: `reader_event`
- **mutation**: prefix one event's `reader_id` with `login:`
- **expected code**: `NAMESPACED_READER_ID`
- **also reported**: `UNKNOWN_READER_ID`
- **expected column**: `reader_id`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: The second code is the referential check doing its job: a prefixed id is not the id that is in the registry. This is the mechanically visible signature of the mixed-grain defect above -- a prefix announces its own grain.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        NAMESPACED_READER_ID reader_event column=reader_id rows=1: 1 reader id(s) carry a namespace prefix (['login']). A prefixed id announces its own grain, which is the signature of two id spaces sharing one column. Reader ids in this contract are opaque and single-grain
        UNKNOWN_READER_ID reader_event column=reader_id rows=1: 1 reader id(s) are not in the reader registry. Every table must reference the same declared id space
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 2 finding(s)
$ echo $?  ->  1
```

### `email_click.disjoint_id_space`

- **defect class**: a whole table keyed on a different id space
- **table**: `email_click`
- **mutation**: re-key every email click onto ids from another id space
- **expected code**: `DISJOINT_READER_ID_SPACE`
- **expected column**: `reader_id`
- **expected rows**: 7
- **expected exit status**: 1
- **why it matters**: Reported separately from a handful of unknown ids because the consequence differs: every reader looks email-inactive, which is indistinguishable from real disengagement unless the join failure is named.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
FAIL  email_click          7 rows
        DISJOINT_READER_ID_SPACE email_click column=reader_id rows=7: not one of the 7 reader id(s) in this table appears in the reader registry. This input is keyed on a different id space, so joining it to reading activity would produce readers who look single-channel because the join missed
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

## reader_event

### `reader_event.missing_required_column`

- **defect class**: missing required column
- **table**: `reader_event`
- **mutation**: drop the `session_id` column
- **expected code**: `MISSING_REQUIRED_COLUMN`
- **expected column**: `session_id`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: Sessions are required as rows, not as a pre-aggregated count, so a delivery cannot satisfy this by supplying a number the validator has no way to check.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        MISSING_REQUIRED_COLUMN reader_event column=session_id rows=26: the contract requires column 'session_id' and the file does not have it; columns present: ['channel', 'content_id', 'engagement_time_seconds', 'event_id', 'event_kind', 'event_ts', 'reader_id']
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.wrong_dtype`

- **defect class**: wrong dtype
- **table**: `reader_event`
- **mutation**: supply `engagement_time_seconds` as int64 instead of float64
- **expected code**: `COLUMN_TYPE_MISMATCH`
- **expected column**: `engagement_time_seconds`
- **expected rows**: 26
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        COLUMN_TYPE_MISMATCH reader_event column=engagement_time_seconds rows=26: column 'engagement_time_seconds' is int64, the contract declares double. The value is not coerced: coercing is how a label becomes a number and a date becomes nothing
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.timezone_naive_timestamp`

- **defect class**: timezone-naive timestamp (the day-boundary defect)
- **table**: `reader_event`
- **mutation**: strip the timezone from `event_ts`
- **expected code**: `TIMESTAMP_NOT_TIMEZONE_AWARE`
- **expected column**: `event_ts`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: This is the defect the contract's shape exists to prevent, and it gets its own code rather than being reported as a generic type mismatch. A naive instant silently inherits whichever zone the producing system used; nothing downstream can recover the boundary, and every window is mis-bucketed by hours while looking plausible.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        TIMESTAMP_NOT_TIMEZONE_AWARE reader_event column=event_ts rows=26: column 'event_ts' is a timezone-naive timestamp (timestamp[us]); the contract declares timestamp[us, tz=UTC]. A naive instant silently inherits whichever zone the producing system used, which is exactly the day-boundary defect this contract refuses
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.null_in_non_nullable`

- **defect class**: null in a non-nullable field
- **table**: `reader_event`
- **mutation**: null out one `session_id`
- **expected code**: `NULL_IN_NON_NULLABLE`
- **expected column**: `session_id`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        NULL_IN_NON_NULLABLE reader_event column=session_id rows=1: column 'session_id' is declared non-nullable and holds 1 null value(s)
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.duplicate_dedup_key`

- **defect class**: duplicate on the stated dedup key
- **table**: `reader_event`
- **mutation**: re-deliver one event under the same `event_id`
- **expected code**: `DUPLICATE_DEDUP_KEY`
- **expected column**: `event_id`
- **expected rows**: 2
- **expected exit status**: 1
- **why it matters**: The reason the contract requires a stable event id at all: a re-delivery that reuses its id is caught here, and one that invents a new id is not distinguishable from a real second event by anything.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         27 rows
        DUPLICATE_DEDUP_KEY reader_event column=event_id rows=2: the deduplication key (event_id) is not unique: 1 key value(s) cover 2 row(s). A duplicate on this key double-counts every measure derived from the table
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.enum_out_of_range`

- **defect class**: out-of-range enum value
- **table**: `reader_event`
- **mutation**: set one `channel` to `newsletter`
- **expected code**: `ENUM_VALUE_OUT_OF_RANGE`
- **expected column**: `channel`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        ENUM_VALUE_OUT_OF_RANGE reader_event column=channel rows=1: column 'channel' holds 1 row(s) whose value is outside the contract vocabulary. Permitted: ['web', 'app']. Found: ['newsletter']
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.delivery_without_content_id`

- **defect class**: conditional requirement violated
- **table**: `reader_event`
- **mutation**: null the `content_id` of a delivery event
- **expected code**: `CONDITIONAL_FIELD_REQUIRED`
- **expected column**: `content_id`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: A delivery with no content id cannot be attributed to a piece of content, so it cannot be a view of one. Nulling it would otherwise silently shrink every view-based measure.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        CONDITIONAL_FIELD_REQUIRED reader_event column=content_id rows=1: rule 'delivery_requires_content_id': 1 row(s) with event_kind in ['content_delivery'] have no content_id. A delivery with no content id cannot be attributed to a piece of content, so it cannot be a view of one.
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.negative_measure`

- **defect class**: negative value in a non-negative measure
- **table**: `reader_event`
- **mutation**: set one `engagement_time_seconds` to -5.0
- **expected code**: `NEGATIVE_MEASURE`
- **expected column**: `engagement_time_seconds`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        NEGATIVE_MEASURE reader_event column=engagement_time_seconds rows=1: column 'engagement_time_seconds' holds 1 negative value(s); the measure is a non-negative quantity
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.prebucketed_date_column`

- **defect class**: forbidden column: a pre-bucketed calendar date
- **table**: `reader_event`
- **mutation**: add an `event_date` column alongside the instant
- **expected code**: `FORBIDDEN_COLUMN`
- **expected column**: `event_date`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: Refused by name, with its own reason, because this is how the day-boundary defect comes back: a producer keeps its convenient per-source date, the engine reads it, and the timezone the manifest declares stops being the one that decides a day.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        FORBIDDEN_COLUMN reader_event column=event_date rows=26: column 'event_date' is refused: a pre-bucketed calendar date re-imports the day-boundary defect this contract exists to prevent; supply the event instant instead
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.scroll_column`

- **defect class**: forbidden column: an out-of-scope measure
- **table**: `reader_event`
- **mutation**: add a `total_scroll_pct` column
- **expected code**: `FORBIDDEN_COLUMN`
- **expected column**: `total_scroll_pct`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: Scroll depth is declared out of scope, so it is refused rather than ignored. On surfaces where it cannot be measured it arrives as a hardcoded zero, and a mixed-surface deployment then compares a real number against that zero.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        FORBIDDEN_COLUMN reader_event column=total_scroll_pct rows=26: column 'total_scroll_pct' is refused: scroll depth is declared out of scope by this contract
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.personal_data_column`

- **defect class**: forbidden column: personal data
- **table**: `reader_event`
- **mutation**: add a column whose name announces personal data
- **expected code**: `FORBIDDEN_COLUMN`
- **expected column**: `reader_email_hint`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: The contract requires no personal data, so a column that announces it is refused on arrival. The check is on the column name, which catches the accident, not the adversary -- but the accident is the realistic case.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        FORBIDDEN_COLUMN reader_event column=reader_email_hint rows=26: column 'reader_email_hint' is refused: the contract requires no personal data and must not carry an email address
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.unexpected_column`

- **defect class**: unexpected column
- **table**: `reader_event`
- **mutation**: add a vendor column the contract does not declare
- **expected code**: `UNEXPECTED_COLUMN`
- **expected column**: `referrer_medium`
- **expected rows**: 26
- **expected exit status**: 1
- **why it matters**: Extra columns fail closed. A vendor-shaped table arrives one convenient field at a time, and the point of a contract is that the shape is agreed rather than inferred.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         26 rows
        UNEXPECTED_COLUMN reader_event column=referrer_medium rows=26: column 'referrer_medium' is not in the contract. Extra columns are refused so a vendor-shaped table cannot arrive one field at a time; put provenance in the manifest instead
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

## content

### `content.missing_required_column`

- **defect class**: missing required column
- **table**: `content`
- **mutation**: drop the `sections` column
- **expected code**: `MISSING_REQUIRED_COLUMN`
- **expected column**: `sections`
- **expected rows**: 10
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        MISSING_REQUIRED_COLUMN content column=sections rows=10: the contract requires column 'sections' and the file does not have it; columns present: ['content_id', 'content_type', 'published_ts', 'section_resolution']
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.wrong_dtype`

- **defect class**: wrong dtype
- **table**: `content`
- **mutation**: supply `sections` as a comma-joined string instead of a list
- **expected code**: `COLUMN_TYPE_MISMATCH`
- **expected column**: `sections`
- **expected rows**: 10
- **expected exit status**: 1
- **why it matters**: The shape that loses the 1/n attribution rule: a joined string has to be split by a convention nobody wrote down, and a section containing the separator disappears.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        COLUMN_TYPE_MISMATCH content column=sections rows=10: column 'sections' is string, the contract declares list<item: string>. The value is not coerced: coercing is how a label becomes a number and a date becomes nothing
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.null_in_non_nullable`

- **defect class**: null in a non-nullable field
- **table**: `content`
- **mutation**: null out one `content_type`
- **expected code**: `NULL_IN_NON_NULLABLE`
- **expected column**: `content_type`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        NULL_IN_NON_NULLABLE content column=content_type rows=1: column 'content_type' is declared non-nullable and holds 1 null value(s)
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.duplicate_dedup_key`

- **defect class**: duplicate on the stated dedup key
- **table**: `content`
- **mutation**: append a second row for the same `content_id`
- **expected code**: `DUPLICATE_DEDUP_KEY`
- **expected column**: `content_id`
- **expected rows**: 2
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              11 rows
        DUPLICATE_DEDUP_KEY content column=content_id rows=2: the deduplication key (content_id) is not unique: 1 key value(s) cover 2 row(s). A duplicate on this key double-counts every measure derived from the table
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.enum_out_of_range`

- **defect class**: out-of-range enum value
- **table**: `content`
- **mutation**: set one `section_resolution` to `partial`
- **expected code**: `ENUM_VALUE_OUT_OF_RANGE`
- **expected column**: `section_resolution`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: `resolved` and `unresolved` are the whole vocabulary. A third value would make 'we have no metadata' and 'we forgot the column' indistinguishable again.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        ENUM_VALUE_OUT_OF_RANGE content column=section_resolution rows=1: column 'section_resolution' holds 1 row(s) whose value is outside the contract vocabulary. Permitted: ['resolved', 'unresolved']. Found: ['partial']
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.duplicate_section_in_list`

- **defect class**: repeated section inside one content's list
- **table**: `content`
- **mutation**: repeat a section in one `sections` list
- **expected code**: `DUPLICATE_SECTION`
- **expected column**: `sections`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: A view of content in n sections contributes 1/n to each. A repeat inflates that content's own share and breaks the reconciliation back to total views.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        DUPLICATE_SECTION content column=sections rows=1: 1 row(s) repeat a section. A view of content in n sections contributes 1/n to each, so a repeated section inflates that content's own share and breaks the reconciliation to total views
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.resolved_without_sections`

- **defect class**: conditional requirement violated
- **table**: `content`
- **mutation**: declare content `resolved` and give it no sections
- **expected code**: `CONDITIONAL_FIELD_REQUIRED`
- **expected column**: `sections`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        CONDITIONAL_FIELD_REQUIRED content column=sections rows=1: rule 'resolved_requires_sections': 1 row(s) with section_resolution in ['resolved'] have no sections. Content declared resolved must name at least one section.
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `content.unresolved_with_sections`

- **defect class**: conditional prohibition violated
- **table**: `content`
- **mutation**: declare content `unresolved` and give it a section anyway
- **expected code**: `CONDITIONAL_FIELD_FORBIDDEN`
- **expected column**: `sections`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: One of the two statements would have to be false, so neither can be trusted.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
FAIL  content              10 rows
        CONDITIONAL_FIELD_FORBIDDEN content column=sections rows=1: rule 'unresolved_forbids_sections': 1 row(s) with section_resolution in ['unresolved'] carry a sections they must not. Content declared unresolved must not also carry sections; one of the two statements would be false.
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

## subscription_span

### `subscription_span.missing_required_column`

- **defect class**: missing required column
- **table**: `subscription_span`
- **mutation**: drop the `payer_type` column
- **expected code**: `MISSING_REQUIRED_COLUMN`
- **expected column**: `payer_type`
- **expected rows**: 16
- **expected exit status**: 1
- **why it matters**: Nullable is not optional. The column has to be there, carrying nulls where the billing system cannot say -- otherwise 'unknown payer' and 'no such concept here' are the same absence.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        MISSING_REQUIRED_COLUMN subscription_span column=payer_type rows=16: the contract requires column 'payer_type' and the file does not have it; columns present: ['end_ts', 'reader_id', 'start_ts', 'state']
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `subscription_span.wrong_dtype`

- **defect class**: wrong dtype
- **table**: `subscription_span`
- **mutation**: supply `start_ts` as an ISO date string
- **expected code**: `COLUMN_TYPE_MISMATCH`
- **expected column**: `start_ts`
- **expected rows**: 16
- **expected exit status**: 1
- **why it matters**: Refused rather than parsed. A reader that coerces this is the reader that turns a state label into a number and a date into nothing -- which is why one typed reader serves the whole contract and coerces nothing.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        COLUMN_TYPE_MISMATCH subscription_span column=start_ts rows=16: column 'start_ts' is string, the contract declares timestamp[us, tz=UTC]. The value is not coerced: coercing is how a label becomes a number and a date becomes nothing
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `subscription_span.null_in_non_nullable`

- **defect class**: null in a non-nullable field
- **table**: `subscription_span`
- **mutation**: null out one `state`
- **expected code**: `NULL_IN_NON_NULLABLE`
- **expected column**: `state`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        NULL_IN_NON_NULLABLE subscription_span column=state rows=1: column 'state' is declared non-nullable and holds 1 null value(s)
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `subscription_span.duplicate_dedup_key`

- **defect class**: duplicate on the stated dedup key
- **table**: `subscription_span`
- **mutation**: append a second span with the same `(reader_id, start_ts)`
- **expected code**: `DUPLICATE_DEDUP_KEY`
- **also reported**: `OVERLAPPING_SPANS`
- **expected column**: `reader_id, start_ts`
- **expected rows**: 2
- **expected exit status**: 1
- **why it matters**: Two codes, and both are true of the data: a duplicated interval is also an overlapping one. There is no mutation that duplicates the key without overlapping, which is itself worth knowing about this table.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    17 rows
        DUPLICATE_DEDUP_KEY subscription_span column=reader_id, start_ts rows=2: the deduplication key (reader_id, start_ts) is not unique: 1 key value(s) cover 2 row(s). A duplicate on this key double-counts every measure derived from the table
        OVERLAPPING_SPANS subscription_span column=start_ts, end_ts rows=1: 1 span pair(s) overlap for the same reader. A reader has one state at a time, and overlapping spans make status as of a date ambiguous
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 2 finding(s)
$ echo $?  ->  1
```

### `subscription_span.enum_out_of_range`

- **defect class**: out-of-range enum value
- **table**: `subscription_span`
- **mutation**: set one `state` to `churned`
- **expected code**: `ENUM_VALUE_OUT_OF_RANGE`
- **expected column**: `state`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: The publisher maps its own billing states onto the contract's seven. A state outside them is refused here rather than silently excluded from the population later, which is what an unrecognised label does to a spine filter.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        ENUM_VALUE_OUT_OF_RANGE subscription_span column=state rows=1: column 'state' holds 1 row(s) whose value is outside the contract vocabulary. Permitted: ['registered_unpaid', 'trial', 'active', 'grace', 'payment_failed', 'cancelled', 'expired']. Found: ['churned']
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `subscription_span.end_before_start`

- **defect class**: interval that ends before it starts
- **table**: `subscription_span`
- **mutation**: move one `end_ts` earlier than its `start_ts`
- **expected code**: `SPAN_END_NOT_AFTER_START`
- **expected column**: `end_ts`
- **expected rows**: 1
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        SPAN_END_NOT_AFTER_START subscription_span column=end_ts rows=1: 1 span(s) end at or before they start. Intervals are half-open [start_ts, end_ts), so end_ts must be strictly later
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `subscription_span.multiple_open_spans`

- **defect class**: more than one open interval for one reader
- **table**: `subscription_span`
- **mutation**: null the `end_ts` of an already-closed span
- **expected code**: `MULTIPLE_OPEN_SPANS`
- **also reported**: `OVERLAPPING_SPANS`
- **expected column**: `end_ts`
- **expected rows**: 2
- **expected exit status**: 1
- **why it matters**: Two open spans make status as of a date ambiguous, and the ambiguity resolves differently depending on join order -- so the same delivery scores differently on different runs. The overlap code fires for the same reason.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
FAIL  subscription_span    16 rows
        OVERLAPPING_SPANS subscription_span column=start_ts, end_ts rows=1: 1 span pair(s) overlap for the same reader. A reader has one state at a time, and overlapping spans make status as of a date ambiguous
        MULTIPLE_OPEN_SPANS subscription_span column=end_ts rows=2: 2 open span(s) share a reader. At most one span per reader may have a null end_ts
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 2 finding(s)
$ echo $?  ->  1
```

## optional inputs

### `email_click.event_before_availability_floor`

- **defect class**: event before the input's declared availability floor
- **table**: `email_click`
- **mutation**: move one click to before the declared floor date
- **expected code**: `EVENT_BEFORE_AVAILABILITY_FLOOR`
- **expected column**: `event_ts`
- **expected rows**: 1
- **expected exit status**: 1
- **why it matters**: Either the floor is wrong or the row is, and the difference decides whether a pre-launch period is a gap to be excluded or a real zero to be modelled.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
FAIL  email_click          7 rows
        EVENT_BEFORE_AVAILABILITY_FLOOR email_click column=event_ts rows=1: 1 row(s) fall before the declared availability floor 2025-11-01 (America/New_York). Either the floor is wrong or the rows are, and the difference decides whether a pre-launch period is a gap or a real zero
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `community_action.file_present_but_declared_absent`

- **defect class**: manifest and delivery contradict each other
- **table**: `community_action`
- **mutation**: declare the community input `not_deployed` while still shipping the file
- **expected code**: `FILE_PRESENT_BUT_DECLARED_ABSENT`
- **expected exit status**: 1
- **why it matters**: The manifest is what the engine plans its feature set against, so the two statements cannot both stand. Trusting the file would silently re-add a feature block the run reported as dropped.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
FAIL  community_action     None rows
        FILE_PRESENT_BUT_DECLARED_ABSENT community_action: the file is in the delivery but the manifest declares this input 'not_deployed'. The two statements cannot both be true, and the manifest is what the engine plans against

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `email_click.file_absent_but_declared_available`

- **defect class**: declared available, then not delivered
- **table**: `email_click`
- **mutation**: declare email clicks available and omit the file
- **expected code**: `FILE_ABSENT_BUT_DECLARED_AVAILABLE`
- **expected exit status**: 1
- **why it matters**: The absence that must not be read as zero activity. Declared-and-missing is a delivery failure; not-deployed is a property of the deployment. They degrade differently, so they are reported differently.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
PASS  reader_event         26 rows
PASS  content              10 rows
PASS  subscription_span    16 rows
FAIL  email_click          absent
        FILE_ABSENT_BUT_DECLARED_AVAILABLE email_click: the manifest declares this optional input 'available' but the file is not in the delivery. An input declared available and then missing would be read as zero activity rather than as an absent input
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

### `reader_event.missing_required_table`

- **defect class**: missing required table
- **table**: `reader_event`
- **mutation**: omit `reader_event.parquet` entirely
- **expected code**: `MISSING_REQUIRED_TABLE`
- **expected exit status**: 1

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

PASS  reader               9 rows
FAIL  reader_event         absent
        MISSING_REQUIRED_TABLE reader_event: reader_event.parquet is required by the contract and is not in the delivery
PASS  content              10 rows
PASS  subscription_span    16 rows
PASS  email_click          7 rows
PASS  email_open           7 rows
PASS  community_action     7 rows

FAIL: 1 finding(s)
$ echo $?  ->  1
```

## manifest

### `manifest.absent`

- **defect class**: no manifest at all
- **table**: `-`
- **mutation**: omit `manifest.json`
- **expected exit status**: 2
- **why it matters**: Exit 2, not 1, and no table is checked. Without the manifest there is no timezone, week anchor, article-view definition or availability floor to check against, so a pass would be a verdict about a question nobody asked.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST no manifest.json in <delivery>. A directory of Parquet files is not a conforming delivery on its own: the timezone that defines a day, the week anchor, the article-view definition and the per-input availability floors cannot be read off the files

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.missing_timezone`

- **defect class**: undeclared day boundary
- **table**: `-`
- **mutation**: remove `day_boundary_timezone`
- **expected exit status**: 2
- **why it matters**: One of the two definitions this contract deliberately does not decide. There is no default, because the plausible answers differ by hours and the wrong one mis-buckets every window without anything visibly breaking.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST manifest.json is missing required key 'day_boundary_timezone'

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.unknown_timezone`

- **defect class**: day boundary declared as something that is not a zone
- **table**: `-`
- **mutation**: set `day_boundary_timezone` to `EST-ish`
- **expected exit status**: 2

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST day_boundary_timezone 'EST-ish' is not a known IANA timezone

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.missing_week_anchor`

- **defect class**: undeclared week anchor
- **table**: `-`
- **mutation**: remove `week_anchor`
- **expected exit status**: 2
- **why it matters**: Both conventions -- the week starts on a weekday, the week ends on one -- are in live use, and they differ by up to six days.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST manifest.json is missing required key 'week_anchor'

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.missing_article_view`

- **defect class**: undeclared article-view definition
- **table**: `-`
- **mutation**: remove `article_view`
- **expected exit status**: 2
- **why it matters**: The other definition this contract does not decide. The contract supplies the mechanism -- a delivery event, a resolvable content id, a content type -- and the publisher supplies the editorial selection, with an id so a published number can be traced to the definition it was produced under.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST manifest.json is missing required key 'article_view'

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.unknown_article_view_content_type`

- **defect class**: article view defined over a content type the contract has no vocabulary for
- **table**: `-`
- **mutation**: add `explainer` to `article_view.content_types`
- **expected exit status**: 2

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST article_view.content_types names unknown content types: ['explainer']

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.missing_scored_population`

- **defect class**: undeclared scored population
- **table**: `-`
- **mutation**: remove `scored_population`
- **expected exit status**: 2
- **why it matters**: Subscription state is never a model feature; it decides who is scored at all. Two deployments with different entitled-state sets produce different distributions from identical data, and the scores do not say which happened.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST manifest.json is missing required key 'scored_population'

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.empty_entitled_states`

- **defect class**: scored population that names no state
- **table**: `-`
- **mutation**: set `scored_population.entitled_states` to an empty list
- **expected exit status**: 2

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST scored_population.entitled_states must name at least one subscription state. An empty set scores nobody, and a missing set would have to be guessed

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.optional_input_available_without_floor`

- **defect class**: availability floor missing on an input declared available
- **table**: `-`
- **mutation**: declare email clicks available with no `available_from`
- **expected exit status**: 2

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST an input declared 'available' must also declare available_from: without a floor date, a window that reaches back before the input existed silently reads zeros

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.exclusion_is_a_personal_identifier`

- **defect class**: population exclusion that is not an opaque id
- **table**: `-`
- **mutation**: put an address-shaped exclusion in `population_exclusions`
- **expected exit status**: 2
- **why it matters**: Exclusion lists are the one place a personal identifier has historically leaked into a population definition, so the entries are checked rather than trusted. A deployment resolves its policy to reader ids before the manifest sees it.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST population_exclusions must hold opaque reader ids only. An entry contains ['@'], which means it is a personal identifier or a pattern rather than an id. Resolve it to reader ids before it reaches the manifest

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```

### `manifest.declares_a_different_contract`

- **defect class**: manifest for another contract
- **table**: `-`
- **mutation**: change `contract_name`
- **expected exit status**: 2
- **why it matters**: Table names are generic enough to collide. Without this check a directory produced for something else could validate on shape alone.

```text
contract: engagement-kernel-input 1.0.0
directory: <delivery>

MANIFEST contract_name is 'some-other-input-contract', expected 'engagement-kernel-input' -- this directory declares a different contract

no table was checked: the manifest declares what to check against
$ echo $?  ->  2
```
