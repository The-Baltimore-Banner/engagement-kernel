"""The delivery manifest: the parameters a dataset must declare about itself.

A directory of conforming Parquet files is not yet a conforming delivery. Three
things cannot be read off the files and must be stated:

1. **Which timezone defines a day.** Applied once, by the reference engine, to
   every channel. There is deliberately **no default**: the two plausible
   answers -- the publisher's editorial timezone and UTC -- differ by hours,
   and an accidental UTC boundary puts evening clicks on the next day and
   Saturday-evening clicks in the next week's bin without anything visibly
   breaking. A missing value is a hard failure.
2. **Which weekday anchors a week, and which end of the week it anchors.** Both
   conventions are in live use and they differ by up to six days. There is no
   default here either.
3. **What an article view means editorially.** The contract supplies the
   mechanism -- a delivery event, a resolvable content id, and a content type --
   and the publisher supplies the selection: which content types count. There is
   no default, and the definition carries an id so a published number can be
   traced to the definition it was produced under.
4. **Which population is scored.** Subscription state is never a model feature;
   it decides which readers are fit and scored at all. Which of the contract's
   subscription states count as in-population is a commercial decision, so it
   is declared -- with its own id -- rather than defaulted. A deployment that
   scored "everyone with a paid state" and one that included trials produce
   different distributions from the same data, and nothing in the output says
   which happened.

Plus, per optional input, whether it is available and from when. "Absent" and
"not yet launched" are different facts with different consequences, so the
manifest makes the producer distinguish them.

The manifest is JSON so that reading it needs nothing but the standard library,
and it lives beside the data so a delivery travels with its own semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engagement_kernel.contract import enums
from engagement_kernel.contract.spec import CONTRACT_NAME, OPTIONAL_TABLES

MANIFEST_FILENAME = "manifest.json"


class ManifestError(ValueError):
    """The manifest is absent, unreadable, or does not declare what it must.

    Raised rather than reported as a finding: without the manifest the
    validator does not know which timezone, week anchor or availability floors
    to check against, so continuing would produce a verdict about a question
    nobody asked.
    """


@dataclass(frozen=True)
class WeekAnchor:
    """Which weekday anchors a week, and at which end."""

    weekday: str
    position: str

    def __post_init__(self) -> None:
        if self.weekday not in enums.WEEKDAYS:
            raise ManifestError(
                f"week_anchor.weekday {self.weekday!r} is not one of {list(enums.WEEKDAYS)}"
            )
        if self.position not in enums.WEEK_ANCHOR_POSITIONS:
            raise ManifestError(
                f"week_anchor.position {self.position!r} is not one of "
                f"{list(enums.WEEK_ANCHOR_POSITIONS)}"
            )


@dataclass(frozen=True)
class ArticleViewDefinition:
    """The editorial selection that turns a delivery event into an article view.

    ``definition_id`` is required and free-form: it names the editorial
    decision, so a published number can be traced back to the definition it was
    produced under. Changing which content types count changes every view-based
    feature, so the id has to move with it.
    """

    definition_id: str
    content_types: tuple[str, ...]
    event_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.definition_id.strip():
            raise ManifestError("article_view.definition_id must not be empty")
        if not self.content_types:
            raise ManifestError("article_view.content_types must name at least one content type")
        unknown = [value for value in self.content_types if value not in enums.CONTENT_TYPES]
        if unknown:
            raise ManifestError(
                f"article_view.content_types names unknown content types: {unknown}"
            )
        if not self.event_kinds:
            raise ManifestError("article_view.event_kinds must name at least one event kind")
        unknown_kinds = [
            value for value in self.event_kinds if value not in enums.READER_EVENT_KINDS
        ]
        if unknown_kinds:
            raise ManifestError(
                f"article_view.event_kinds names unknown event kinds: {unknown_kinds}"
            )


@dataclass(frozen=True)
class ScoredPopulation:
    """Which readers a run is fit and scored on.

    Subscription state is never a model feature -- it defines the spine. That
    makes the entitled-state set a *population* decision with no default: a
    deployment that scores paying readers only and one that also scores trials
    are answering different questions, and the scores themselves do not say
    which.

    ``definition_id`` moves with the state set for the same reason the
    article-view definition carries one: a published cohort has to be traceable
    to the population it was drawn from.
    """

    definition_id: str
    entitled_states: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.definition_id.strip():
            raise ManifestError("scored_population.definition_id must not be empty")
        if not self.entitled_states:
            raise ManifestError(
                "scored_population.entitled_states must name at least one subscription state. "
                "An empty set scores nobody, and a missing set would have to be guessed"
            )
        unknown = [
            value for value in self.entitled_states if value not in enums.SUBSCRIPTION_STATES
        ]
        if unknown:
            raise ManifestError(
                f"scored_population.entitled_states names states that are not in the contract "
                f"vocabulary: {unknown}. Permitted: {list(enums.SUBSCRIPTION_STATES)}"
            )
        if len(set(self.entitled_states)) != len(self.entitled_states):
            raise ManifestError("scored_population.entitled_states repeats a state")


@dataclass(frozen=True)
class InputAvailability:
    """Whether an optional input is delivered, and from when."""

    status: str
    available_from: date | None

    def __post_init__(self) -> None:
        if self.status not in enums.AVAILABILITY_STATUSES:
            raise ManifestError(
                f"availability status {self.status!r} is not one of "
                f"{list(enums.AVAILABILITY_STATUSES)}"
            )
        if self.status == enums.AVAILABILITY_AVAILABLE and self.available_from is None:
            raise ManifestError(
                "an input declared 'available' must also declare available_from: without a "
                "floor date, a window that reaches back before the input existed silently "
                "reads zeros"
            )
        if self.status != enums.AVAILABILITY_AVAILABLE and self.available_from is not None:
            raise ManifestError(
                f"an input declared {self.status!r} must not declare available_from"
            )

    @property
    def is_available(self) -> bool:
        return self.status == enums.AVAILABILITY_AVAILABLE


@dataclass(frozen=True)
class Manifest:
    """Everything a delivery must declare about itself."""

    contract_version: str
    day_boundary_timezone: str
    week_anchor: WeekAnchor
    article_view: ArticleViewDefinition
    scored_population: ScoredPopulation
    optional_inputs: dict[str, InputAvailability]
    #: Opaque reader ids excluded from the scored population. Deployment
    #: configuration, never a predicate in code: the contract carries no
    #: personal field, so an exclusion rule over a personal attribute cannot be
    #: expressed against it, and an entry that looks like a personal identifier
    #: is rejected.
    population_exclusions: tuple[str, ...] = field(default_factory=tuple)
    contract_name: str = CONTRACT_NAME

    def availability(self, table_name: str) -> InputAvailability:
        return self.optional_inputs[table_name]

    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.day_boundary_timezone)


#: Characters and substrings that mean an exclusion entry is not an opaque id.
#: The exclusion list is the one place a personal identifier has historically
#: leaked into a population definition, so it is checked rather than trusted.
_NON_OPAQUE_EXCLUSION_MARKERS = ("@", " ", "%", "*", "like ", "'")

_REQUIRED_KEYS = (
    "contract_name",
    "contract_version",
    "day_boundary_timezone",
    "week_anchor",
    "article_view",
    "scored_population",
    "optional_inputs",
)


def _require(raw: dict, key: str) -> object:
    if key not in raw:
        raise ManifestError(f"{MANIFEST_FILENAME} is missing required key {key!r}")
    return raw[key]


def _parse_date(value: object, where: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError(f"{where} must be an ISO date string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError(f"{where} is not an ISO date: {value!r}") from exc


def parse_manifest(raw: object) -> Manifest:
    """Build a :class:`Manifest` from already-decoded JSON, or raise."""
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST_FILENAME} must contain a JSON object")
    for key in _REQUIRED_KEYS:
        _require(raw, key)

    name = raw["contract_name"]
    if name != CONTRACT_NAME:
        raise ManifestError(
            f"contract_name is {name!r}, expected {CONTRACT_NAME!r} -- this directory "
            "declares a different contract"
        )

    timezone_name = raw["day_boundary_timezone"]
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ManifestError(
            "day_boundary_timezone must be a non-empty IANA timezone name. There is no "
            "default: which timezone defines a day is a publisher decision, and guessing "
            "it mis-buckets every window without anything visibly breaking"
        )
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ManifestError(
            f"day_boundary_timezone {timezone_name!r} is not a known IANA timezone"
        ) from exc

    anchor_raw = raw["week_anchor"]
    if not isinstance(anchor_raw, dict):
        raise ManifestError("week_anchor must be an object with 'weekday' and 'position'")
    for key in ("weekday", "position"):
        if key not in anchor_raw:
            raise ManifestError(f"week_anchor is missing required key {key!r}")
    anchor = WeekAnchor(weekday=anchor_raw["weekday"], position=anchor_raw["position"])

    view_raw = raw["article_view"]
    if not isinstance(view_raw, dict):
        raise ManifestError("article_view must be an object")
    for key in ("definition_id", "content_types", "event_kinds"):
        if key not in view_raw:
            raise ManifestError(f"article_view is missing required key {key!r}")
    article_view = ArticleViewDefinition(
        definition_id=str(view_raw["definition_id"]),
        content_types=tuple(view_raw["content_types"]),
        event_kinds=tuple(view_raw["event_kinds"]),
    )

    population_raw = raw["scored_population"]
    if not isinstance(population_raw, dict):
        raise ManifestError("scored_population must be an object")
    for key in ("definition_id", "entitled_states"):
        if key not in population_raw:
            raise ManifestError(f"scored_population is missing required key {key!r}")
    if not isinstance(population_raw["entitled_states"], list):
        raise ManifestError("scored_population.entitled_states must be a list of states")
    scored_population = ScoredPopulation(
        definition_id=str(population_raw["definition_id"]),
        entitled_states=tuple(population_raw["entitled_states"]),
    )

    inputs_raw = raw["optional_inputs"]
    if not isinstance(inputs_raw, dict):
        raise ManifestError("optional_inputs must be an object keyed by table name")
    expected = {table.name for table in OPTIONAL_TABLES}
    missing = sorted(expected - set(inputs_raw))
    if missing:
        raise ManifestError(
            f"optional_inputs must declare every optional input; missing: {missing}"
        )
    unknown = sorted(set(inputs_raw) - expected)
    if unknown:
        raise ManifestError(f"optional_inputs names inputs that are not in the contract: {unknown}")
    optional_inputs = {}
    for table_name, entry in inputs_raw.items():
        if not isinstance(entry, dict) or "status" not in entry:
            raise ManifestError(f"optional_inputs.{table_name} must be an object with a 'status'")
        optional_inputs[table_name] = InputAvailability(
            status=entry["status"],
            available_from=_parse_date(
                entry.get("available_from"), f"optional_inputs.{table_name}.available_from"
            ),
        )

    exclusions_raw = raw.get("population_exclusions", [])
    if not isinstance(exclusions_raw, list):
        raise ManifestError("population_exclusions must be a list of opaque reader ids")
    exclusions = tuple(str(item) for item in exclusions_raw)
    for entry in exclusions:
        lowered = entry.lower()
        offenders = [m for m in _NON_OPAQUE_EXCLUSION_MARKERS if m in lowered]
        if offenders:
            raise ManifestError(
                "population_exclusions must hold opaque reader ids only. An entry contains "
                f"{offenders!r}, which means it is a personal identifier or a pattern rather "
                "than an id. Resolve it to reader ids before it reaches the manifest"
            )

    return Manifest(
        contract_name=name,
        contract_version=str(raw["contract_version"]),
        day_boundary_timezone=timezone_name,
        week_anchor=anchor,
        article_view=article_view,
        scored_population=scored_population,
        optional_inputs=optional_inputs,
        population_exclusions=exclusions,
    )


def load_manifest(directory: str | Path) -> Manifest:
    """Read and validate ``manifest.json`` from a delivery directory."""
    path = Path(directory) / MANIFEST_FILENAME
    if not path.exists():
        raise ManifestError(
            f"no {MANIFEST_FILENAME} in {directory}. A directory of Parquet files is not a "
            "conforming delivery on its own: the timezone that defines a day, the week "
            "anchor, the article-view definition and the per-input availability floors "
            "cannot be read off the files"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc
    return parse_manifest(raw)


def manifest_to_dict(manifest: Manifest) -> dict:
    """Render a manifest back to the JSON shape, for generators and tests."""
    return {
        "contract_name": manifest.contract_name,
        "contract_version": manifest.contract_version,
        "day_boundary_timezone": manifest.day_boundary_timezone,
        "week_anchor": {
            "weekday": manifest.week_anchor.weekday,
            "position": manifest.week_anchor.position,
        },
        "article_view": {
            "definition_id": manifest.article_view.definition_id,
            "content_types": list(manifest.article_view.content_types),
            "event_kinds": list(manifest.article_view.event_kinds),
        },
        "scored_population": {
            "definition_id": manifest.scored_population.definition_id,
            "entitled_states": list(manifest.scored_population.entitled_states),
        },
        "optional_inputs": {
            name: {
                "status": entry.status,
                "available_from": (
                    entry.available_from.isoformat() if entry.available_from else None
                ),
            }
            for name, entry in manifest.optional_inputs.items()
        },
        "population_exclusions": list(manifest.population_exclusions),
    }
