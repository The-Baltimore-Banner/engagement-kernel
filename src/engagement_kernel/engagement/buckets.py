"""The section bucket map: versioned deployment configuration, not code.

A publisher's section taxonomy is theirs. It has however many sections it has,
they are named whatever the CMS calls them, and they change. So the map from
sections to topic buckets is a file the deployment owns and versions, and this
module is only the loader and the rules the file has to satisfy.

Three rules, and they are the invariants -- everything else here is bookkeeping:

**Completeness.** Every section carrying a non-trivial share of reading must be
mapped. Unmapped sections fall into the catch-all, and a catch-all that has
quietly become the largest bucket is a taxonomy that no longer describes the
publication. :func:`check_completeness` measures this against observed reading
rather than against the file, because a map can be complete on paper and stale in
fact.

**A declared catch-all ceiling.** The catch-all exists so a new section does not
crash the build; it is not a bucket. The map declares the share above which it
stops being a remainder, and the gate reads that number.

**Bucket names must survive the model guard.** Bucket names become column names
(``topic_share_<bucket>``), so a bucket the model guard would refuse produces a
matrix that cannot be built -- at matrix-assembly time, several layers from the
file that caused it. Checked here instead, where the message can name the file.

**The bucket count is declared, not fixed.** The system this ports from hardcoded
a range of 8 to 12 buckets and refused anything outside it. That is a portability
defect rather than a validation rule: a newsroom with five sections would be
refused outright, and nothing about five sections is unsound. The count bounds are
therefore fields on the map with permissive defaults, and the real invariants --
completeness and the catch-all ceiling -- hold at any count. A bound is still
supported, because a publisher who has decided their taxonomy has ten buckets
should be able to say so and have a refit that produced eleven fail.

The file is JSON. The delivery manifest is JSON for the same reason: reading the
configuration of a run should need nothing but the standard library.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from engagement_kernel.engagement.guards import inspect_model_columns

#: Section key the intermediate build attributes unresolved content to.
#:
#: Imported rather than restated so the two cannot drift: if the build's sentinel
#: changed and this did not, unresolved reading would silently become a mapped
#: section and appear as a topic preference.
from engagement_kernel.intermediate.config import (  # noqa: E402 - grouped with its explanation
    DEFAULT_UNRESOLVED_SECTION,
)

#: Default catch-all bucket name.
DEFAULT_CATCH_ALL = "other"

#: Default ceiling on the catch-all's share of resolved reading.
#:
#: A default rather than a required field, because a deployment standing the
#: engine up for the first time should not have to pick this number before it has
#: seen the distribution. It is reported on every run so the first look at the
#: real data is also the prompt to set it deliberately.
DEFAULT_CATCH_ALL_SHARE_MAX = 0.15

#: Share of resolved reading at or above which an unmapped section is a
#: completeness failure rather than a long-tail remainder.
DEFAULT_COMPLETENESS_MIN_VIEW_SHARE = 0.005

#: Bucket-count bounds when the map declares none. Wide on purpose: the point of
#: making these configurable is that the engine has no opinion about how many
#: sections a newsroom has.
DEFAULT_MIN_BUCKETS = 2
DEFAULT_MAX_BUCKETS = 64


class BucketMapError(ValueError):
    """The bucket map file does not satisfy a rule the engine depends on."""


@dataclass(frozen=True)
class SectionBucketMap:
    """A validated section-to-bucket mapping, and the rules it declares."""

    version: str
    #: Bucket name -> the sections it contains. Excludes the catch-all, which
    #: lists nothing by construction.
    buckets: dict[str, tuple[str, ...]]
    catch_all_bucket: str = DEFAULT_CATCH_ALL
    catch_all_share_max: float = DEFAULT_CATCH_ALL_SHARE_MAX
    completeness_min_view_share: float = DEFAULT_COMPLETENESS_MIN_VIEW_SHARE
    min_buckets: int = DEFAULT_MIN_BUCKETS
    max_buckets: int = DEFAULT_MAX_BUCKETS
    section_to_bucket: dict[str, str] = field(default_factory=dict)

    @property
    def bucket_names(self) -> tuple[str, ...]:
        """Every bucket including the catch-all, in file order then the catch-all.

        Ordering is fixed rather than sorted because it becomes the column order
        of the topic block, and a frozen model's centroid is a vector in that
        order.
        """
        return (*self.buckets.keys(), self.catch_all_bucket)

    @property
    def n_buckets(self) -> int:
        return len(self.bucket_names)

    def bucket_for(self, section: str) -> str:
        """Map a resolved section to its bucket; unmapped sections fall to the catch-all.

        The unresolved sentinel is refused rather than mapped. Unresolved
        metadata means "we do not know what this reader read", which is a
        coverage fact; folding it into a bucket -- even the catch-all -- turns it
        into a stated preference for that bucket.
        """
        if section == DEFAULT_UNRESOLVED_SECTION:
            raise BucketMapError(
                f"{DEFAULT_UNRESOLVED_SECTION!r} is a coverage outcome, not a section. "
                "Exclude unresolved rows before bucketing; mapping them would publish "
                "missing metadata as a topic preference"
            )
        return self.section_to_bucket.get(section, self.catch_all_bucket)

    def topic_share_columns(self) -> list[str]:
        """The model-matrix topic-share column per bucket, in bucket order."""
        return [f"topic_share_{bucket}" for bucket in self.bucket_names]

    def snapshot(self) -> dict[str, list[str]]:
        """The mapping as plain data, for the frozen model bundle.

        A frozen model's topic block is a vector over these buckets, so the
        bundle has to carry the map it was fit against -- not its version number
        alone. A version string proves two runs disagree; the snapshot says how.
        """
        return {bucket: list(sections) for bucket, sections in self.buckets.items()}

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "buckets": self.snapshot(),
            "catch_all_bucket": self.catch_all_bucket,
            "catch_all_share_max": self.catch_all_share_max,
            "completeness_min_view_share": self.completeness_min_view_share,
            "min_buckets": self.min_buckets,
            "max_buckets": self.max_buckets,
        }


def parse_bucket_map(raw: object) -> SectionBucketMap:
    """Validate a decoded bucket-map document and build the map."""
    if not isinstance(raw, dict):
        raise BucketMapError("a bucket map must be a JSON object")
    for key in ("version", "buckets"):
        if key not in raw:
            raise BucketMapError(f"bucket map is missing required key {key!r}")

    raw_buckets = raw["buckets"]
    if not isinstance(raw_buckets, dict) or not raw_buckets:
        raise BucketMapError("'buckets' must be a non-empty object of bucket -> sections")

    catch_all = str(raw.get("catch_all_bucket", DEFAULT_CATCH_ALL))
    if not catch_all.strip():
        raise BucketMapError("catch_all_bucket must be a non-empty name")
    if catch_all in raw_buckets:
        raise BucketMapError(
            f"the catch-all bucket {catch_all!r} must not list sections explicitly. It is "
            "the remainder: giving it members means a section is both mapped and unmapped"
        )
    if DEFAULT_UNRESOLVED_SECTION in raw_buckets:
        raise BucketMapError(f"{DEFAULT_UNRESOLVED_SECTION!r} is never a bucket")

    min_buckets = int(raw.get("min_buckets", DEFAULT_MIN_BUCKETS))
    max_buckets = int(raw.get("max_buckets", DEFAULT_MAX_BUCKETS))
    if min_buckets < 2:
        raise BucketMapError(
            "min_buckets must be at least 2: a single bucket holds every reader's whole "
            "attention by construction, so the topic block would carry no information"
        )
    if max_buckets < min_buckets:
        raise BucketMapError(f"max_buckets {max_buckets} is below min_buckets {min_buckets}")

    buckets: dict[str, tuple[str, ...]] = {}
    section_to_bucket: dict[str, str] = {}
    for bucket, sections in raw_buckets.items():
        name = str(bucket)
        if not isinstance(sections, list) or not sections:
            raise BucketMapError(f"bucket {name!r} must list at least one section")
        cleaned = tuple(str(section).strip() for section in sections)
        for section in cleaned:
            if not section:
                raise BucketMapError(f"bucket {name!r} contains an empty section name")
            if section == DEFAULT_UNRESOLVED_SECTION:
                raise BucketMapError(
                    f"{DEFAULT_UNRESOLVED_SECTION!r} must never be mapped to a bucket"
                )
            if section in section_to_bucket:
                raise BucketMapError(
                    f"section {section!r} is mapped to both {section_to_bucket[section]!r} "
                    f"and {name!r}. A view of it would be counted under whichever bucket "
                    "the loader saw last, so the shares would not close"
                )
            section_to_bucket[section] = name
        buckets[name] = cleaned

    bucket_map = SectionBucketMap(
        version=str(raw["version"]),
        buckets=buckets,
        catch_all_bucket=catch_all,
        catch_all_share_max=float(raw.get("catch_all_share_max", DEFAULT_CATCH_ALL_SHARE_MAX)),
        completeness_min_view_share=float(
            raw.get("completeness_min_view_share", DEFAULT_COMPLETENESS_MIN_VIEW_SHARE)
        ),
        min_buckets=min_buckets,
        max_buckets=max_buckets,
        section_to_bucket=section_to_bucket,
    )

    if not (min_buckets <= bucket_map.n_buckets <= max_buckets):
        raise BucketMapError(
            f"the map has {bucket_map.n_buckets} buckets including the catch-all, outside "
            f"the declared range [{min_buckets}, {max_buckets}]. These bounds are this "
            "file's own declaration, not an engine rule: widen them here if the taxonomy "
            "really has this many buckets"
        )
    if not (0.0 < bucket_map.catch_all_share_max <= 1.0):
        raise BucketMapError(
            f"catch_all_share_max {bucket_map.catch_all_share_max} must be in (0, 1]. Zero "
            "would refuse any unmapped reading at all, which no live taxonomy survives"
        )
    if not (0.0 <= bucket_map.completeness_min_view_share <= 1.0):
        raise BucketMapError(
            f"completeness_min_view_share {bucket_map.completeness_min_view_share} must be "
            "in [0, 1]"
        )

    refused = inspect_model_columns(bucket_map.topic_share_columns())
    if refused:
        detail = "; ".join(str(finding) for finding in refused)
        raise BucketMapError(
            f"bucket names produce model columns the feature guard refuses: {detail}. "
            "Bucket names become column names, so this would fail at matrix assembly "
            "with no mention of this file. Rename the bucket"
        )
    return bucket_map


def load_bucket_map(path: str | Path) -> SectionBucketMap:
    """Load and validate a bucket map from a JSON file."""
    location = Path(path)
    try:
        raw = json.loads(location.read_text())
    except FileNotFoundError as exc:
        raise BucketMapError(
            f"no bucket map at {location}. The topic block needs a section taxonomy and "
            "there is no default: a map invented by the engine would name buckets no "
            "editor recognises"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BucketMapError(f"bucket map at {location} is not valid JSON: {exc}") from exc
    return parse_bucket_map(raw)


@dataclass(frozen=True)
class CompletenessReport:
    """What the map looks like against reading actually observed."""

    unmapped_above_threshold: tuple[str, ...]
    catch_all_view_share: float
    catch_all_share_max: float
    completeness_min_view_share: float

    @property
    def passed(self) -> bool:
        return (
            not self.unmapped_above_threshold
            and self.catch_all_view_share <= self.catch_all_share_max
        )

    def describe(self) -> str:
        parts = [
            f"catch-all share {self.catch_all_view_share:.4f} (ceiling {self.catch_all_share_max})"
        ]
        if self.unmapped_above_threshold:
            parts.append(
                "unmapped sections above "
                f"{self.completeness_min_view_share}: "
                f"{', '.join(self.unmapped_above_threshold)}"
            )
        return "; ".join(parts)


def check_completeness(
    bucket_map: SectionBucketMap,
    section_view_shares: Mapping[str, float],
) -> CompletenessReport:
    """Check the map against observed shares of *resolved* reading.

    ``section_view_shares`` must already exclude the unresolved sentinel. Passing
    it in would put unresolved reading in the denominator, so a metadata outage
    would read as a taxonomy that had suddenly become complete.
    """
    if DEFAULT_UNRESOLVED_SECTION in section_view_shares:
        raise BucketMapError(
            "section_view_shares must cover resolved reading only; including "
            f"{DEFAULT_UNRESOLVED_SECTION!r} makes a metadata outage look like better coverage"
        )
    unmapped = tuple(
        sorted(
            section
            for section, share in section_view_shares.items()
            if share >= bucket_map.completeness_min_view_share
            and section not in bucket_map.section_to_bucket
        )
    )
    catch_all_share = sum(
        share
        for section, share in section_view_shares.items()
        if section not in bucket_map.section_to_bucket
    )
    return CompletenessReport(
        unmapped_above_threshold=unmapped,
        catch_all_view_share=float(catch_all_share),
        catch_all_share_max=bucket_map.catch_all_share_max,
        completeness_min_view_share=bucket_map.completeness_min_view_share,
    )
