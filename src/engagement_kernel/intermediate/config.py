"""Everything the intermediate build has to be told, and nothing it may assume.

Three values decide what the daily tables mean, and none of them has a default
here:

**The timezone that defines a day.** Applied once, by this build, to every
channel. The upstream system this replaces converted web and app to one zone
and applied no conversion at all to email and comments, so one channel's whole
history sat hours away from the others and nothing visibly broke. There is no
default because the two plausible guesses -- the publisher's editorial zone and
UTC -- differ by hours.

**What an article view means.** The manifest names which content types count and
which event kinds are candidates. It is resolved once, into one predicate, and
that predicate is the only place the question is answered. A placeholder here
would produce a build that runs and is wrong, which is the failure this whole
repository exists to prevent -- so an unset or empty selection raises instead.

**Which section a view lands on when the content's metadata did not resolve.**
A sentinel, never zero and never a real section. See
:data:`DEFAULT_UNRESOLVED_SECTION`.

The configuration is built from the delivery's own manifest
(:meth:`BuildConfig.from_manifest`). Constructing one by hand is supported for
tests, and it is checked just as hard: the guard is in ``__post_init__``, not in
the loader, so there is no path into the build that skips it.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engagement_kernel.contract import spec
from engagement_kernel.contract.manifest import ArticleViewDefinition, Manifest

#: The section key a view of unresolved content is attributed to.
#:
#: Deliberately not a real section name and deliberately not null: the
#: downstream reason codes have to tell "we do not know what this reader read"
#: apart from "this reader read nothing", and those are the same number the
#: moment unresolved reading is dropped or folded into a real bucket. The
#: delimiters make an accidental collision with a publisher's own section key
#: implausible rather than merely unlikely.
DEFAULT_UNRESOLVED_SECTION = "__unresolved__"


class BuildConfigError(ValueError):
    """The build was asked to run without something it cannot guess.

    Raised rather than warned about. Every member of this class is a value whose
    wrong setting produces plausible numbers, so a build that continued would
    hand back an answer nobody could audit.
    """


@dataclass(frozen=True)
class BuildConfig:
    """The resolved parameters of one intermediate build."""

    #: IANA name of the zone that defines a calendar day, for every channel.
    day_boundary_timezone: str
    #: The editorial selection that turns a delivery event into an article view.
    article_view: ArticleViewDefinition
    #: Section key for views of content whose section metadata did not resolve.
    unresolved_section: str = DEFAULT_UNRESOLVED_SECTION
    #: Carried from the contract so a consumer of these tables can tell whether
    #: a per-view rate is defined at all. Not applied here: this build emits the
    #: numerator and the denominator, and the window layer decides.
    engagement_time_min_deliveries: int = spec.ENGAGEMENT_TIME_MIN_DELIVERIES

    def __post_init__(self) -> None:
        if not isinstance(self.day_boundary_timezone, str) or not (
            self.day_boundary_timezone.strip()
        ):
            raise BuildConfigError(
                "day_boundary_timezone is required and has no default. One zone is applied to "
                "every channel; guessing it mis-buckets every window without anything visibly "
                "breaking"
            )
        try:
            ZoneInfo(self.day_boundary_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BuildConfigError(
                f"day_boundary_timezone {self.day_boundary_timezone!r} is not a known IANA timezone"
            ) from exc
        if self.article_view is None:
            raise BuildConfigError(
                "article_view is required and has no default. Which deliveries count as an "
                "article view is an editorial decision; a placeholder produces a build that "
                "runs and is wrong"
            )
        if not self.article_view.content_types or not self.article_view.event_kinds:
            raise BuildConfigError(
                "article_view names no content types or no event kinds, so it selects nothing. "
                "An article-view definition that counts nothing reports every reader as "
                "inactive, which is indistinguishable from a quiet publisher"
            )
        if not self.article_view.definition_id.strip():
            raise BuildConfigError(
                "article_view.definition_id is required: a published number has to be "
                "traceable to the definition it was produced under"
            )
        if not self.unresolved_section.strip():
            raise BuildConfigError(
                "unresolved_section must be a non-empty sentinel. Unresolved metadata is its "
                "own outcome; an empty key would make it indistinguishable from no reading"
            )
        if self.unresolved_section in self.article_view.content_types:
            raise BuildConfigError(
                f"unresolved_section {self.unresolved_section!r} collides with a declared "
                "content type"
            )

    @classmethod
    def from_manifest(cls, manifest: Manifest, **overrides: object) -> BuildConfig:
        """Resolve the build configuration from a delivery's own manifest.

        The manifest is the only supported source, so the timezone and the
        article-view definition travel with the data rather than living in a
        deployment's config file where the two can drift apart.
        """
        return cls(
            day_boundary_timezone=manifest.day_boundary_timezone,
            article_view=manifest.article_view,
            **overrides,  # type: ignore[arg-type]
        )

    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.day_boundary_timezone)

    def describe(self) -> str:
        return "\n".join(
            (
                f"day boundary timezone : {self.day_boundary_timezone}",
                f"article view          : {self.article_view.definition_id}",
                f"  content types       : {', '.join(self.article_view.content_types)}",
                f"  event kinds         : {', '.join(self.article_view.event_kinds)}",
                f"unresolved section    : {self.unresolved_section}",
                f"time rate floor       : {self.engagement_time_min_deliveries} deliveries",
            )
        )
