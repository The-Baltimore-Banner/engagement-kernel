#!/usr/bin/env python3
"""Render the field-level contract reference from the spec itself.

The reference is generated rather than written because a hand-maintained copy of
35 field definitions drifts from the code, and a documented contract that no
longer matches the validator is worse than no document: a producer satisfies the
prose and fails the gate, or satisfies the gate and gets a different answer than
the prose promised.

``tests/test_contract_docs.py`` compares the committed file against a fresh
render, so the drift is a test failure rather than a surprise.

Usage::

    python3 tools/render_contract_reference.py           # print
    python3 tools/render_contract_reference.py --write   # update the doc
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:  # running as a script, not an installed package
    sys.path.insert(0, str(REPO_ROOT / "src"))

from engagement_kernel.contract import degradation, enums, spec  # noqa: E402
from engagement_kernel.contract.manifest import MANIFEST_FILENAME  # noqa: E402

DOC_RELPATH = "docs/contract-reference.md"


def _cell(text: str) -> str:
    """Markdown-table-safe single line."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _enum_cell(field: spec.FieldSpec) -> str:
    if field.enum is None:
        return "--"
    return ", ".join(f"`{value}`" for value in field.enum)


def _table_section(table: spec.TableSpec) -> list[str]:
    requirement = "**required**" if table.required else "optional"
    lines = [
        f"### `{table.name}` ({requirement})",
        "",
        f"`{table.filename}` -- {_cell(table.purpose)}",
        "",
        f"- **grain**: {_cell(table.grain)}",
        f"- **deduplication key**: {', '.join(f'`{k}`' for k in table.dedup_key)}",
        f"- **null behaviour**: {_cell(table.null_behaviour)}",
        f"- **feature block**: `{table.feature_block}`",
    ]
    if table.event_time_column:
        lines.append(f"- **event-time column**: `{table.event_time_column}`")
    if table.reader_reference_column:
        lines.append(
            f"- **reader registry reference**: `{table.reader_reference_column}` -- every value "
            "must appear in `reader.reader_id`"
        )
    lines.append("")
    lines.append("| field | type | nullable | enum | definition |")
    lines.append("| --- | --- | --- | --- | --- |")
    for field in table.fields:
        nullable = "yes" if field.nullable else "**no**"
        note = " Non-negative." if field.non_negative else ""
        lines.append(
            f"| `{field.name}` | `{field.arrow_type}` | {nullable} | {_enum_cell(field)} "
            f"| {_cell(field.definition)}{note} |"
        )
    lines.append("")
    if table.conditional_rules:
        lines.append("**Conditional rules.**")
        lines.append("")
        for rule in table.conditional_rules:
            lines.append(
                f"- `{rule.rule_id}`: when `{rule.when_column}` is in "
                f"{', '.join(f'`{v}`' for v in rule.when_values)}, `{rule.then_column}` must be "
                f"`{rule.requirement}`. {_cell(rule.definition)}"
            )
        lines.append("")
    if table.notes:
        lines.append("**Notes.**")
        lines.append("")
        for note in table.notes:
            lines.append(f"- {_cell(note)}")
        lines.append("")
    return lines


def render_document() -> str:
    lines: list[str] = [
        "# Contract reference",
        "",
        "**Generated file.** Produced by `python3 tools/render_contract_reference.py --write`",
        "from `src/engagement_kernel/contract/spec.py`, and compared against a fresh render by",
        "`tests/test_contract_docs.py`. Do not edit it by hand -- edit the spec.",
        "",
        "For what the derived concepts *mean* -- an article view, an active day, the scored",
        "population, the day boundary, the reader-id grain -- see",
        "[canonical-input-contract.md](canonical-input-contract.md). This file is the field-level",
        "reference only.",
        "",
        f"- contract name: `{spec.CONTRACT_NAME}`",
        f"- contract version: `{spec.CONTRACT_VERSION}`",
        f"- tables: {len(spec.TABLES)} "
        f"({len(spec.REQUIRED_TABLES)} required, {len(spec.OPTIONAL_TABLES)} optional)",
        f"- fields: {sum(len(table.fields) for table in spec.TABLES)}",
        "",
        "## A delivery",
        "",
        "One directory. One Parquet file per table, named for the table, plus",
        f"`{MANIFEST_FILENAME}`. Validate it with:",
        "",
        "```bash",
        "engagement-kernel-validate path/to/delivery",
        "```",
        "",
        "Exit status `0` conforms, `1` does not conform, `2` the verdict could not be trusted",
        "(no manifest, or an invalid one).",
        "",
        "## Tables",
        "",
    ]
    for table in spec.TABLES:
        lines.extend(_table_section(table))

    lines.extend(
        [
            "## Manifest",
            "",
            f"`{MANIFEST_FILENAME}` states what cannot be read off the files. Nothing here has a",
            "default; a missing value is a hard failure rather than a guess.",
            "",
            "| key | meaning |",
            "| --- | --- |",
            "| `contract_name` | must be "
            f"`{spec.CONTRACT_NAME}`, so a directory cannot be mistaken for a different "
            "contract that happens to share table names |",
            "| `contract_version` | the contract version the delivery was built against |",
            "| `day_boundary_timezone` | the single IANA timezone that decides which calendar "
            "day an instant belongs to, for every channel |",
            "| `week_anchor.weekday` | the weekday that anchors a week |",
            "| `week_anchor.position` | which end of the week that weekday sits on: "
            + " or ".join(f"`{value}`" for value in enums.WEEK_ANCHOR_POSITIONS)
            + " |",
            "| `article_view.definition_id` | names the editorial decision, so a published "
            "number is traceable to the definition it was produced under |",
            "| `article_view.content_types` | which content types an article view may count |",
            "| `article_view.event_kinds` | which event kinds an article view may count |",
            "| `scored_population.definition_id` | names the population decision, for the same "
            "reason |",
            "| `scored_population.entitled_states` | which subscription states are in the "
            "scored population |",
            "| `optional_inputs.<table>.status` | "
            + ", ".join(f"`{value}`" for value in enums.AVAILABILITY_STATUSES)
            + " |",
            "| `optional_inputs.<table>.available_from` | the coverage floor date, required "
            "when the status is `available` and forbidden otherwise |",
            "| `population_exclusions` | opaque reader ids excluded from the scored "
            "population; deployment configuration, never a predicate in code |",
            "",
            "## Closed vocabularies",
            "",
        ]
    )
    vocabularies = (
        ("reader id grains", enums.READER_ID_GRAINS),
        ("reader event channels", enums.READER_EVENT_CHANNELS),
        ("reader event kinds", enums.READER_EVENT_KINDS),
        ("content types", enums.CONTENT_TYPES),
        ("section resolutions", enums.SECTION_RESOLUTIONS),
        ("subscription states", enums.SUBSCRIPTION_STATES),
        ("payer types", enums.PAYER_TYPES),
        ("community action kinds", enums.COMMUNITY_ACTION_KINDS),
        ("availability statuses", enums.AVAILABILITY_STATUSES),
        ("week anchor positions", enums.WEEK_ANCHOR_POSITIONS),
    )
    lines.append("| vocabulary | values |")
    lines.append("| --- | --- |")
    for name, values in vocabularies:
        lines.append(f"| {name} | {', '.join(f'`{value}`' for value in values)} |")
    lines.append("")

    lines.extend(
        [
            "## Declared exclusions and thresholds",
            "",
            f"- **Engagement-time rate floor**: `{spec.ENGAGEMENT_TIME_MIN_DELIVERIES}` "
            "deliveries in a window. Below it, any per-view rate derived from engagement "
            "time is undefined rather than zero.",
            f"- **Scroll depth**: {_cell(spec.SCROLL_DEPTH_SCOPE_NOTE)}",
            "- **Never a model feature**: "
            + ", ".join(f"`{name}`" for name in spec.FORBIDDEN_MODEL_FEATURE_SOURCES)
            + ". Subscription state and payer type define the population, not the features; "
            "email opens and sends are reachability signals that machine opens inflate and "
            "nothing can clean.",
            "",
            "**Refused column names.** A column whose name contains one of these is rejected "
            "with its own reason rather than a generic 'unexpected column':",
            "",
            "| substring | why it is refused |",
            "| --- | --- |",
        ]
    )
    for token, reason in spec.FORBIDDEN_COLUMN_REASONS:
        lines.append(f"| `{token}` | {_cell(reason)} |")
    lines.append("")

    lines.extend(
        [
            "## Feature blocks and honest degradation",
            "",
            "Each table feeds one named feature block. An optional input that is absent drops "
            "its block and changes the feature-set id; it never becomes a column of zeros.",
            "",
            "| block | source table | optional |",
            "| --- | --- | --- |",
        ]
    )
    for table in spec.TABLES:
        optional = "yes" if not table.required else "no"
        lines.append(f"| `{table.feature_block}` | `{table.name}` | {optional} |")
    lines.append("")
    lines.append("Feature-set ids: `" + degradation.FEATURE_SET_FULL + "` when every block is")
    lines.append("supported, otherwise the dropped blocks named in a fixed order --")
    lines.append(
        ", ".join(f"`{suffix}`" for suffix in degradation.OPTIONAL_BLOCK_SUFFIXES.values())
        + " -- joined with `+`."
    )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the field-level contract reference.")
    parser.add_argument(
        "--write", action="store_true", help=f"write {DOC_RELPATH} instead of printing"
    )
    args = parser.parse_args(argv)
    text = render_document()
    if args.write:
        (REPO_ROOT / DOC_RELPATH).write_text(text, encoding="utf-8")
        print(f"wrote {DOC_RELPATH}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
