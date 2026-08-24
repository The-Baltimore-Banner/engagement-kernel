"""A synthetic cohort delivery, big enough to fit a model on.

``examples/demo-delivery`` is hand-written and small on purpose: every row in it
exists to demonstrate one property of the contract, and you can read the whole file
and see what each row is for. That makes it the right artifact for the validator and
the intermediate build, and the wrong one for this lane -- nine readers cannot
support a training panel, a percentile table, a stability screen or a cluster.

So this module generates a *cohort*: a few hundred invented readers over a few
months, written as a conforming delivery. It is generated rather than committed
because a committed one would be several megabytes of Parquet whose provenance
nobody could check by reading it, and because generating it exercises the whole path
-- generate, validate, build the intermediate tables, fit, score -- rather than
starting from a fixture of pre-baked features. A fixture cannot catch a defect
between the contract and the atomics; this can.

Every value here is invented. Nothing is sampled from, derived from, or anonymised
out of any real publisher's data. The reader ids are sequential, the section names
are generic, the archetypes below were written by hand.

The archetypes are the point
----------------------------

A cohort of readers drawn from one distribution has no cluster structure, so every
stability screen would fail -- correctly, and uselessly, because it would be
measuring the generator rather than the pipeline. So the readers are drawn from a
handful of behavioural archetypes with real differences between them. That makes the
cohort a *positive control*: the screens should pass on it, and a change that breaks
the lane shows up as a run that cannot find structure that is definitely there.

It also means the archetypes are not a claim about any real audience, and the
clusters this finds are not a finding. They are a demonstration that the machinery
works end to end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from engagement_kernel.contract import enums, spec
from engagement_kernel.contract.manifest import MANIFEST_FILENAME

#: The synthetic publisher's own declarations. Its choices, not contract defaults.
COHORT_TIMEZONE = "America/New_York"
COHORT_WEEK_ANCHOR = {"weekday": "Sunday", "position": "week_ends_on"}
COHORT_ARTICLE_VIEW_ID = "cohort-article-view-v1"
COHORT_POPULATION_ID = "cohort-scored-population-v1"

#: Sections, and the buckets the companion bucket map puts them in. Generic names:
#: a section list that looked like a specific newsroom's would invite an adopter to
#: map their own onto it rather than declaring their own.
COHORT_SECTIONS: tuple[str, ...] = (
    "news",
    "politics",
    "business",
    "sport",
    "culture",
    "food",
    "opinion",
    "education",
    "health",
    "weather",
)


@dataclass(frozen=True)
class Archetype:
    """One behavioural pattern, as the parameters the generator draws from."""

    name: str
    share: float
    #: Mean article views per day, per reader-event channel.
    views_per_day: dict[str, float]
    #: Mean email clicks per day.
    clicks_per_day: float
    #: Mean community actions per day.
    community_per_day: float
    #: How concentrated their reading is: 1.0 spreads over every section, 0.1 is
    #: nearly all in one.
    section_spread: float
    #: Probability their subscription covers the whole period.
    full_tenure: float


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        name="heavy_multichannel",
        share=0.10,
        views_per_day={"web": 2.4, "app": 1.6},
        clicks_per_day=0.5,
        community_per_day=0.15,
        section_spread=0.9,
        full_tenure=0.95,
    ),
    Archetype(
        name="web_regular",
        share=0.24,
        views_per_day={"web": 1.1, "app": 0.05},
        clicks_per_day=0.12,
        community_per_day=0.01,
        section_spread=0.6,
        full_tenure=0.9,
    ),
    Archetype(
        name="app_reader",
        share=0.16,
        views_per_day={"web": 0.08, "app": 1.3},
        clicks_per_day=0.05,
        community_per_day=0.0,
        section_spread=0.45,
        full_tenure=0.85,
    ),
    Archetype(
        name="email_habit",
        share=0.16,
        views_per_day={"web": 0.25, "app": 0.05},
        clicks_per_day=0.8,
        community_per_day=0.0,
        section_spread=0.3,
        full_tenure=0.9,
    ),
    Archetype(
        name="community_participant",
        share=0.08,
        views_per_day={"web": 0.6, "app": 0.2},
        clicks_per_day=0.1,
        community_per_day=1.1,
        section_spread=0.5,
        full_tenure=0.9,
    ),
    Archetype(
        name="narrow_specialist",
        share=0.12,
        views_per_day={"web": 0.7, "app": 0.1},
        clicks_per_day=0.05,
        community_per_day=0.0,
        section_spread=0.12,
        full_tenure=0.85,
    ),
    Archetype(
        name="light_lapsing",
        share=0.14,
        views_per_day={"web": 0.12, "app": 0.02},
        clicks_per_day=0.02,
        community_per_day=0.0,
        section_spread=0.4,
        full_tenure=0.55,
    ),
)

_share_total = sum(archetype.share for archetype in ARCHETYPES)
if abs(_share_total - 1.0) > 1e-9:  # pragma: no cover - a literal edit trips this
    raise ImportError(f"archetype shares sum to {_share_total}, not 1")


@dataclass(frozen=True)
class CohortSpec:
    """How big a cohort to generate, and over what period."""

    n_readers: int = 400
    #: Inclusive. Long enough that the trailing window of the first scored week sits
    #: entirely inside the period.
    start: date = date(2026, 1, 5)
    end: date = date(2026, 7, 5)
    n_content: int = 140
    #: Share of content whose section metadata does not resolve. Present because
    #: unresolved reading has its own code path and a cohort without any would leave
    #: it untested.
    unresolved_content_share: float = 0.08
    #: Share of content filed in two sections, so fractional attribution is exercised.
    multi_section_share: float = 0.15
    seed: int = 20260824
    #: Lists the synthetic publisher sends. Two, so a deployment restricting the
    #: cadence signal to one list has something to restrict.
    list_ids: tuple[str, ...] = ("lst-daily", "lst-weekend")

    def __post_init__(self) -> None:
        if self.n_readers < 50:
            raise ValueError(
                "a cohort below about 50 readers cannot support a training panel, a "
                "percentile table and a stability screen at once"
            )
        if self.start >= self.end:
            raise ValueError("the cohort period is empty")

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1


def _instant(day: date, hour: int, minute: int) -> datetime:
    """A UTC instant. The generator writes UTC; the engine converts once, as declared."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _table(table_spec: spec.TableSpec, columns: dict[str, list]) -> pa.Table:
    return pa.table(columns, schema=table_spec.arrow_schema())


def build_cohort(cohort: CohortSpec | None = None) -> dict[str, pa.Table]:
    """Generate the whole cohort delivery as Arrow tables."""
    cohort = cohort or CohortSpec()
    rng = np.random.default_rng(cohort.seed)
    days = [cohort.start + timedelta(days=offset) for offset in range(cohort.n_days)]

    # --- readers and their archetypes ---------------------------------------
    reader_ids = [f"rdr-{index:05d}" for index in range(cohort.n_readers)]
    weights = np.array([archetype.share for archetype in ARCHETYPES])
    assigned = rng.choice(len(ARCHETYPES), size=cohort.n_readers, p=weights / weights.sum())

    # --- content ------------------------------------------------------------
    content_ids = [f"cnt-{index:05d}" for index in range(cohort.n_content)]
    content_types: list[str] = []
    resolutions: list[str] = []
    sections: list[list[str] | None] = []
    published: list[datetime | None] = []
    for index in range(cohort.n_content):
        unresolved = rng.random() < cohort.unresolved_content_share
        # A handful of non-article types, so the article-view definition has
        # something to exclude and the exclusion is visible in the counts.
        content_types.append("article" if rng.random() > 0.12 else "video")
        if unresolved:
            resolutions.append(enums.SECTION_RESOLUTION_UNRESOLVED)
            sections.append(None)
        else:
            resolutions.append(enums.SECTION_RESOLUTION_RESOLVED)
            n_sections = 2 if rng.random() < cohort.multi_section_share else 1
            picked = rng.choice(len(COHORT_SECTIONS), size=n_sections, replace=False)
            sections.append([COHORT_SECTIONS[position] for position in picked])
        published.append(_instant(days[index % len(days)], 9, 0))

    content = _table(
        spec.CONTENT,
        {
            "content_id": content_ids,
            "content_type": content_types,
            "section_resolution": resolutions,
            "sections": sections,
            "published_ts": published,
        },
    )

    # Section preference per reader: a Dirichlet draw whose concentration is the
    # archetype's spread, so a specialist really is concentrated rather than
    # concentrated on average.
    resolved_positions = [
        index
        for index, resolution in enumerate(resolutions)
        if resolution == enums.SECTION_RESOLUTION_RESOLVED
    ]
    section_of_content = {
        index: sections[index][0]
        for index in resolved_positions  # type: ignore[index]
    }
    content_by_section: dict[str, list[int]] = {section: [] for section in COHORT_SECTIONS}
    for index in resolved_positions:
        content_by_section[section_of_content[index]].append(index)
    unresolved_positions = [
        index
        for index, resolution in enumerate(resolutions)
        if resolution == enums.SECTION_RESOLUTION_UNRESOLVED
    ]

    # --- reader events, email clicks, community actions ---------------------
    event_rows: list[tuple] = []
    click_rows: list[tuple] = []
    open_rows: list[tuple] = []
    community_rows: list[tuple] = []
    span_rows: list[tuple] = []

    for reader_index, reader_id in enumerate(reader_ids):
        archetype = ARCHETYPES[assigned[reader_index]]
        preference = rng.dirichlet(
            np.full(len(COHORT_SECTIONS), max(archetype.section_spread, 0.05) * 2.0)
        )

        # Tenure. A reader without full tenure starts partway through, which is what
        # produces the partial-window rows the projection flag exists for.
        if rng.random() < archetype.full_tenure:
            start_day = cohort.start
        else:
            start_day = cohort.start + timedelta(days=int(rng.integers(20, cohort.n_days - 40)))
        span_rows.append(
            (
                reader_id,
                "trial" if rng.random() < 0.15 else "active",
                "individual" if rng.random() < 0.9 else None,
                _instant(start_day, 0, 0),
                None,
            )
        )

        # A per-reader multiplier, so readers inside an archetype differ.
        intensity = float(rng.gamma(shape=4.0, scale=0.25))
        for day_index, day in enumerate(days):
            if day < start_day:
                continue
            # Weekends are quieter, which gives the weekly-bin metrics something
            # other than a flat rate to describe.
            weekday_factor = 0.75 if day.weekday() >= 5 else 1.0

            for channel, rate in archetype.views_per_day.items():
                n_views = int(rng.poisson(rate * intensity * weekday_factor))
                if n_views == 0:
                    continue
                session_id = f"ses-{reader_index:05d}-{day_index:04d}-{channel}"
                for view in range(n_views):
                    if rng.random() < 0.06 and unresolved_positions:
                        position = int(rng.choice(unresolved_positions))
                    else:
                        pick = int(rng.choice(len(COHORT_SECTIONS), p=preference))
                        section = COHORT_SECTIONS[pick]
                        candidates = content_by_section[section] or resolved_positions
                        position = int(rng.choice(candidates))
                    # Attention is missing on some events on purpose: null is not
                    # zero, and a cohort where everything is measured never exercises
                    # the measured-deliveries denominator.
                    seconds = None if rng.random() < 0.15 else float(rng.gamma(2.0, 45.0))
                    event_rows.append(
                        (
                            f"evt-{len(event_rows):08d}",
                            reader_id,
                            _instant(day, 7 + (view % 14), (view * 7) % 60),
                            channel,
                            enums.EVENT_KIND_CONTENT_DELIVERY,
                            content_ids[position],
                            session_id,
                            seconds,
                        )
                    )
                    if rng.random() < 0.2:
                        event_rows.append(
                            (
                                f"evt-{len(event_rows):08d}",
                                reader_id,
                                _instant(day, 7 + (view % 14), (view * 7 + 3) % 60),
                                channel,
                                enums.EVENT_KIND_CONTENT_INTERACTION,
                                content_ids[position],
                                session_id,
                                None,
                            )
                        )

            n_clicks = int(rng.poisson(archetype.clicks_per_day * intensity))
            for click in range(n_clicks):
                list_id = cohort.list_ids[click % len(cohort.list_ids)]
                click_rows.append(
                    (
                        f"clk-{len(click_rows):08d}",
                        reader_id,
                        # Late evening on purpose: in the publisher's declared zone
                        # this is the same day, and in UTC it is the next one. A
                        # cohort whose clicks all land at noon cannot show that the
                        # day boundary is being applied.
                        _instant(day, 23, 20 + click % 30),
                        list_id,
                        f"cmp-{day.isoformat()}-{list_id}",
                    )
                )
            # Opens are generated because the contract carries them and the lane must
            # be shown not to use them. They are never a feature.
            for extra in range(int(rng.poisson(archetype.clicks_per_day * intensity * 2.5))):
                open_rows.append(
                    (
                        f"opn-{len(open_rows):08d}",
                        reader_id,
                        _instant(day, 6, extra % 60),
                        cohort.list_ids[extra % len(cohort.list_ids)],
                        f"cmp-{day.isoformat()}-{cohort.list_ids[extra % len(cohort.list_ids)]}",
                    )
                )

            for action in range(int(rng.poisson(archetype.community_per_day * intensity))):
                kind = enums.COMMUNITY_ACTION_KINDS[
                    int(rng.choice(len(enums.COMMUNITY_ACTION_KINDS), p=[0.2, 0.2, 0.4, 0.1, 0.1]))
                ]
                community_rows.append(
                    (
                        f"act-{len(community_rows):08d}",
                        reader_id,
                        _instant(day, 20, (action * 11) % 60),
                        kind,
                        "site-main",
                        content_ids[int(rng.choice(len(content_ids)))],
                    )
                )

    reader = _table(
        spec.READER,
        {
            "reader_id": reader_ids,
            "id_grain": [enums.GRAIN_RESOLVED_PERSON] * len(reader_ids),
        },
    )
    reader_event = _table(
        spec.READER_EVENT,
        {
            name: [row[index] for row in event_rows]
            for index, name in enumerate(spec.READER_EVENT.field_names)
        },
    )
    subscription_span = _table(
        spec.SUBSCRIPTION_SPAN,
        {
            name: [row[index] for row in span_rows]
            for index, name in enumerate(spec.SUBSCRIPTION_SPAN.field_names)
        },
    )
    email_click = _table(
        spec.EMAIL_CLICK,
        {
            name: [row[index] for row in click_rows]
            for index, name in enumerate(spec.EMAIL_CLICK.field_names)
        },
    )
    email_open = _table(
        spec.EMAIL_OPEN,
        {
            name: [row[index] for row in open_rows]
            for index, name in enumerate(spec.EMAIL_OPEN.field_names)
        },
    )
    community_action = _table(
        spec.COMMUNITY_ACTION,
        {
            name: [row[index] for row in community_rows]
            for index, name in enumerate(spec.COMMUNITY_ACTION.field_names)
        },
    )
    return {
        "reader": reader,
        "reader_event": reader_event,
        "content": content,
        "subscription_span": subscription_span,
        "email_click": email_click,
        "email_open": email_open,
        "community_action": community_action,
    }


def build_manifest(cohort: CohortSpec | None = None) -> dict:
    """The cohort's manifest. Its own declarations, stated in full."""
    cohort = cohort or CohortSpec()
    available = {
        "status": enums.AVAILABILITY_AVAILABLE,
        "available_from": cohort.start.isoformat(),
    }
    return {
        "contract_name": spec.CONTRACT_NAME,
        "contract_version": spec.CONTRACT_VERSION,
        "day_boundary_timezone": COHORT_TIMEZONE,
        "week_anchor": dict(COHORT_WEEK_ANCHOR),
        "article_view": {
            "definition_id": COHORT_ARTICLE_VIEW_ID,
            # Articles only: the cohort carries video too, so the definition has
            # something to exclude and a reader who only watches video reads as
            # inactive -- which is a property of the definition, not of the engine.
            "content_types": ["article"],
            "event_kinds": [enums.EVENT_KIND_CONTENT_DELIVERY],
        },
        "scored_population": {
            "definition_id": COHORT_POPULATION_ID,
            "entitled_states": ["trial", "active", "grace"],
        },
        "optional_inputs": {
            "email_click": dict(available),
            "email_open": dict(available),
            "community_action": dict(available),
        },
        "population_exclusions": [],
    }


#: The bucket map that goes with :data:`COHORT_SECTIONS`.
#:
#: Five buckets plus the catch-all -- deliberately below the 8-to-12 range the source
#: system hardcoded, because a newsroom with a small taxonomy is a supported case and
#: refusing it was a portability defect rather than a validation rule.
COHORT_BUCKET_MAP: dict = {
    "version": "cohort-1",
    "buckets": {
        "news": ["news", "politics", "weather"],
        "money": ["business"],
        "sport": ["sport"],
        "living": ["culture", "food", "health"],
        "civic": ["opinion", "education"],
    },
    "catch_all_bucket": "other",
    "catch_all_share_max": 0.15,
    "completeness_min_view_share": 0.005,
    "min_buckets": 2,
    "max_buckets": 16,
}

BUCKET_MAP_FILENAME = "section_buckets.json"


def write_cohort(directory: str | Path, cohort: CohortSpec | None = None) -> list[Path]:
    """Write the cohort delivery, its manifest and its bucket map to a directory."""
    cohort = cohort or CohortSpec()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in build_cohort(cohort).items():
        path = target / f"{name}.parquet"
        pq.write_table(table, path)
        written.append(path)
    manifest_path = target / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(build_manifest(cohort), indent=2) + "\n")
    written.append(manifest_path)
    bucket_path = target / BUCKET_MAP_FILENAME
    bucket_path.write_text(json.dumps(COHORT_BUCKET_MAP, indent=2) + "\n")
    written.append(bucket_path)
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Generate a synthetic cohort delivery large enough to fit the engagement "
            "lane on. Every value is invented."
        )
    )
    parser.add_argument("directory", help="directory to write the delivery into")
    parser.add_argument("--readers", type=int, default=CohortSpec.n_readers)
    parser.add_argument("--seed", type=int, default=CohortSpec.seed)
    args = parser.parse_args(argv)
    cohort = CohortSpec(n_readers=args.readers, seed=args.seed)
    for path in write_cohort(args.directory, cohort):
        print(path)
    return 0
