"""The section bucket map: its rules, and each rule shown failing.

The completeness rule is the one the acceptance criteria single out, so
:func:`test_the_completeness_rule_fails_on_an_incomplete_map` is the control that
matters: it takes a map that is missing a substantial section and requires the check
to say so. A completeness rule that only ever passes is documentation.

The bucket-count bounds get their own tests for a different reason. The system this
ports from hardcoded 8 to 12 buckets and refused anything outside it, which would
turn away a newsroom with five sections -- a portability defect, not a validation
rule. So the bounds are now declared per map, and both the "a small taxonomy is
accepted" and "a declared bound is still enforced" cases are asserted.
"""

from __future__ import annotations

import json

import pytest

from engagement_kernel.engagement.buckets import (
    BucketMapError,
    check_completeness,
    load_bucket_map,
    parse_bucket_map,
)
from engagement_kernel.engagement.cohort import COHORT_BUCKET_MAP
from engagement_kernel.intermediate.config import DEFAULT_UNRESOLVED_SECTION


def a_map(**overrides) -> dict:
    document = json.loads(json.dumps(COHORT_BUCKET_MAP))
    document.update(overrides)
    return document


def test_the_cohort_map_is_valid_and_small(bucket_map) -> None:
    """Six buckets including the catch-all -- below the range the source hardcoded."""
    assert bucket_map.n_buckets == 6
    assert bucket_map.catch_all_bucket == "other"
    assert bucket_map.bucket_names[-1] == "other", "the catch-all sorts last, always"


def test_a_five_section_newsroom_is_accepted() -> None:
    """The portability case the hardcoded range would have refused outright."""
    small = parse_bucket_map(
        {
            "version": "small-1",
            "buckets": {"news": ["news"], "sport": ["sport"]},
        }
    )
    assert small.n_buckets == 3


def test_a_declared_bound_is_still_enforced() -> None:
    """Configurable is not the same as absent: a publisher may still fix the count."""
    with pytest.raises(BucketMapError) as exc:
        parse_bucket_map(a_map(min_buckets=10, max_buckets=12))
    assert "outside the declared range" in str(exc.value)
    assert "this file's own declaration" in str(exc.value)


def test_a_single_bucket_is_refused() -> None:
    """One bucket holds every reader's whole attention, so it carries no information."""
    with pytest.raises(BucketMapError) as exc:
        parse_bucket_map({"version": "v", "buckets": {"news": ["news"]}, "min_buckets": 1})
    assert "at least 2" in str(exc.value)


def test_a_section_in_two_buckets_is_refused() -> None:
    """Its views would land in whichever bucket the loader saw last, so shares would not close."""
    document = a_map()
    document["buckets"]["money"] = ["business", "news"]
    with pytest.raises(BucketMapError) as exc:
        parse_bucket_map(document)
    assert "mapped to both" in str(exc.value)


def test_the_catch_all_may_not_list_sections() -> None:
    document = a_map()
    document["buckets"]["other"] = ["news"]
    with pytest.raises(BucketMapError) as exc:
        parse_bucket_map(document)
    assert "remainder" in str(exc.value)


def test_the_unresolved_sentinel_is_never_a_bucket_or_a_member() -> None:
    """Unresolved metadata is a coverage outcome. Mapping it publishes it as a taste."""
    as_bucket = a_map()
    as_bucket["buckets"][DEFAULT_UNRESOLVED_SECTION] = ["news"]
    with pytest.raises(BucketMapError):
        parse_bucket_map(as_bucket)

    as_member = a_map()
    as_member["buckets"]["news"] = ["news", DEFAULT_UNRESOLVED_SECTION]
    with pytest.raises(BucketMapError):
        parse_bucket_map(as_member)


def test_bucketing_the_unresolved_sentinel_raises(bucket_map) -> None:
    with pytest.raises(BucketMapError) as exc:
        bucket_map.bucket_for(DEFAULT_UNRESOLVED_SECTION)
    assert "coverage outcome" in str(exc.value)


def test_a_bucket_name_the_model_guard_would_refuse_is_caught_here() -> None:
    """Bucket names become column names, so the guard's verdict belongs at load time.

    Without this the failure surfaces at matrix assembly, several layers away, with
    no mention of the file that caused it.
    """
    document = a_map()
    document["buckets"]["opens"] = ["a-section-nothing-else-maps"]
    with pytest.raises(BucketMapError) as exc:
        parse_bucket_map(document)
    assert "feature guard refuses" in str(exc.value)
    assert "topic_share_opens" in str(exc.value)


def test_the_completeness_rule_fails_on_an_incomplete_map(bucket_map) -> None:
    """The control the acceptance criteria ask for: an incomplete map is caught.

    ``sport`` is dropped from the map while a fifth of observed reading is in it, so
    it falls to the catch-all and takes it over the declared ceiling. Both halves of
    the rule fire, and the report names the section.
    """
    document = a_map()
    del document["buckets"]["sport"]
    incomplete = parse_bucket_map(document)

    shares = {"news": 0.4, "business": 0.2, "sport": 0.2, "culture": 0.1, "food": 0.1}
    report = check_completeness(incomplete, shares)

    assert not report.passed
    assert "sport" in report.unmapped_above_threshold
    assert report.catch_all_view_share == pytest.approx(0.2)
    assert report.catch_all_view_share > report.catch_all_share_max
    assert "sport" in report.describe()

    # And the complete map passes on the same shares, so the failure above is about
    # the map rather than about the shares.
    assert check_completeness(bucket_map, shares).passed


def test_a_long_tail_section_is_not_a_completeness_failure(bucket_map) -> None:
    """The rule is about substantial reading, not about every section ever published."""
    shares = {"news": 0.6, "business": 0.2, "sport": 0.199, "a-one-off-tag": 0.001}
    report = check_completeness(bucket_map, shares)
    assert report.passed
    assert "a-one-off-tag" not in report.unmapped_above_threshold


def test_completeness_refuses_shares_that_include_unresolved(bucket_map) -> None:
    """Including it would make a metadata outage look like improved coverage."""
    with pytest.raises(BucketMapError) as exc:
        check_completeness(bucket_map, {"news": 0.5, DEFAULT_UNRESOLVED_SECTION: 0.5})
    assert "resolved reading only" in str(exc.value)


def test_a_missing_file_says_there_is_no_default(tmp_path) -> None:
    with pytest.raises(BucketMapError) as exc:
        load_bucket_map(tmp_path / "absent.json")
    assert "no default" in str(exc.value)


def test_the_snapshot_carries_the_mapping_not_just_its_version(bucket_map) -> None:
    """A frozen model needs to say which sections its buckets held, not only that they differed."""
    snapshot = bucket_map.snapshot()
    assert snapshot["news"] == list(bucket_map.buckets["news"])
    assert bucket_map.catch_all_bucket not in snapshot
