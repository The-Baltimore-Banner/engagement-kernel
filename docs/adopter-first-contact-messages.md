# The four failures an adopter hits first

> **This is the adopter path.** For working *on* this repository instead, see
> [Getting started for contributors](../README.md#getting-started-for-contributors).

These four account for most first-run failures, and they were all assessed against
one question: **does the message name the fix, or only the violation?**

That distinction is easy to lose. A message naming the violation precisely —
"column 'x' is string, the contract declares double" — reads as rigorous and is
complete information for whoever wrote the schema. It is not enough for the person
meeting it for the first time, who does not yet know whether the fix is theirs,
whether it is a cast or an upstream defect, or whether the validator was supposed
to handle it.

Three of the four were rewritten. The before and after are recorded verbatim
because a claim that messages "were improved" is unfalsifiable, and because the
direction of drift is predictable: a bare violation report is shorter and reads as
more precise, so any future edit will be tempted back toward the left column.

**Each rewrite has a standing check behind it.** The oracle case set asserts a
phrase from the *fix* half of each message, so a message that reverts fails the
suite rather than a review nobody scheduled. That was verified by reverting one on
purpose and watching the oracle refuse it — see
[the negative control](#the-control-that-makes-this-more-than-a-claim).

---

## 1. A required declaration is missing

An adopter's very first run, because the manifest is the first thing they write.

**Before**

```
manifest.json is missing required key 'scored_population'
```

**Diagnosis:** names the violation and stops. Worse than it looks — this is the
one declaration with no worked example anywhere in the repository, and it is two
decisions in one, the second of which belongs to somebody else in the reader's
organisation. A key name conveys none of that. The predictable response is to pick
entitled states expediently to make the error go away, which is precisely the
"decision easy to skip" failure that making them required exists to prevent.

**After**

```
manifest.json is missing required key 'scored_population'. It declares which
subscription states are entitled, and therefore who is fit and scored at all. Two
decisions in one: mapping your billing states onto the contract's vocabulary is an
engineering job, but deciding which of them are scored is a commercial one, and
the scores do not record which was chosen. There is no default: start from
examples/manifest-template.json, which ships every required key with its answer
left open, and see docs/declarations-questionnaire.md for the question and who
owns it
```

Every one of the seven required keys has its own such sentence, so this is not a
special case for the hardest one.

**Related change:** the manifest template ships with an `ANSWER-REQUIRED`
sentinel, and an unanswered template is now refused *as the question it is* rather
than as whatever type error the placeholder happens to trigger first. Before, a
template's first run reported that `'ANSWER-REQUIRED'` is not a known IANA
timezone — technically true, and it teaches the reader nothing. Now it lists every
outstanding decision at once, which is the shortest available description of what
they are being asked.

## 2. A timezone-naive timestamp

The defect the contract exists to refuse — and the one whose obvious fix is wrong
in a way that looks right.

**Before**

```
column 'event_ts' is a timezone-naive timestamp (timestamp[us]); the contract
declares timestamp[us, tz=UTC]. A naive instant silently inherits whichever zone
the producing system used, which is exactly the day-boundary defect this contract
refuses
```

**Diagnosis:** the reasoning is good and the message was still the most dangerous
of the four, because a reader who understands it completely can still apply the
wrong fix. Told the contract wants a zone and that day boundaries matter, the
natural move is to localise the column to the day-boundary timezone they just
declared. The engine applies that conversion itself, so doing it in the export
shifts every instant twice — and the second shift is invisible, because the column
now has a zone and looks correct.

**After**

```
column 'event_ts' is a timezone-naive timestamp (timestamp[us]); the contract
declares timestamp[us, tz=UTC]. Attach the zone the producing system actually
stored these instants in -- for most warehouses that is UTC -- at the point you
write the file. Do NOT localise them to the timezone you declared as the day
boundary: the engine applies that itself, so doing it here shifts every instant
twice, and the second shift is invisible because the column then looks correct. If
you do not know which zone the source stored, that is the defect, and guessing it
here is how it stops being findable. A naive instant inherits whichever zone the
producing system happened to use, which is exactly the day-boundary error this
contract exists to refuse
```

The last-but-one sentence is the one worth having. "I don't know what zone this is
in" is a real state for an adopter to be in, and it is a finding about their
warehouse rather than a question about this contract.

## 3. A column with the wrong type

**Before**

```
column 'engagement_time_seconds' is string, the contract declares double. The
value is not coerced: coercing is how a label becomes a number and a date becomes
nothing
```

**Diagnosis:** explains why the validator refuses to fix it, and never says who
should. It leaves open the reading that this is a validator limitation to be
worked around rather than an export defect to be repaired, and it does not warn
that a blind cast can be the wrong repair.

**After**

```
column 'engagement_time_seconds' is string, the contract declares double. Cast it
to double in the query or job that writes this file, and look at what the cast
does to the values before you trust it -- a string column that will not cast
cleanly is carrying something the contract has no field for, and the fix is
upstream rather than a cast. The validator will not coerce it for you: coercing is
how a label becomes a number and a date becomes nothing, silently, in the
direction that keeps the pipeline running
```

## 4. A required table is not in the delivery

**Before**

```
content.parquet is required by the contract and is not in the delivery
```

**Diagnosis:** accurate and it sends the reader down a blind alley. Having just
read that three optional inputs can be declared absent with a reason, an adopter
missing a required input will go looking for the same mechanism — and that
mechanism does not extend to required inputs. There is nothing to find, and
nothing tells them so.

**After**

```
content.parquet is required by the contract and is not in the delivery. Its grain:
One row per piece of content. Unique on (content_id). Note what the fix is *not*:
the manifest's availability mechanism covers the three optional inputs only, so
there is no way to declare a required input absent. Either the file is produced,
or the delivery cannot conform yet -- and the second is a real answer, reached by
narrowing the window to a period the input covers or by concluding this deployment
is not ready. docs/contract-reference.md has the field list
```

Naming the two legitimate outcomes matters as much as closing off the wrong one.
"This deployment cannot satisfy the contract yet" is a real, respectable answer,
and an adopter who does not know it is available will manufacture a file instead —
which passes validation and answers a different question.

---

## The control that makes this more than a claim

Rewritten messages are worth exactly as much as the check that keeps them
rewritten. So the phrase carrying the *fix* in each of the four is asserted by the
oracle case set that `engagement-kernel-demo-oracle` writes, and again in
`tests/test_oracle.py`:

| class | asserted phrase |
| --- | --- |
| missing declaration | `commercial`, `declarations-questionnaire` |
| naive timestamp | `Do NOT localise` |
| wrong dtype | `Cast it to` |
| missing required table | `no way to declare a required input absent` |

Verified by reverting the naive-timestamp message to its "before" form. The oracle
refused it, and named the reason:

```
FAIL  naive-timestamp  exit=1
        the validator's message does not contain 'Do NOT localise'. This is the
        assertion that the refusal names the fix rather than only the violation,
        so it is checked against the text a producer actually reads
```

The other four cases still passed, so the control is specific to the thing it
protects rather than a suite-wide alarm. The message was then restored and the
oracle went green again.

## One class deliberately left alone

`FORBIDDEN_COLUMN` — a column the contract refuses, such as scroll depth — already
names the fix, because its message is the reason for the refusal and the reason is
the fix: the measure is out of scope, so the answer is to stop sending it. It is
carried as a fifth oracle case anyway. Not because the message needed work, but
because it is the defect an adopter's *coding agent* produces rather than one
their warehouse does: the source has the measure, so the agent helpfully includes
it. That refusal is much better met during mapping than after a model has been fit.
