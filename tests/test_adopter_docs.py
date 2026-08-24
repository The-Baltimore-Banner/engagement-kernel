"""The adopter path is a claim about documents, so the checkable parts are checked.

Most of what makes a setup path good is not mechanically checkable -- whether a
stranger can follow it is settled by watching one try, not by a test. What *is*
checkable is the part most likely to rot: the required-plus-optional shape stated
on the front page, which is a summary of code and will drift from it silently the
first time an input is added, removed or repurposed.

So that table is rendered from the contract's own definitions and asserted equal
to what the documents carry. Everything else here is coarse: links resolve, the
template covers what the manifest actually requires, both paths are labelled, and
the adopter path does not open with the contributor's install line. Coarse checks
on documents are worth having as long as nobody mistakes them for evidence that
the path works.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engagement_kernel.contract import degradation, enums, manifest, spec

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
README = REPO_ROOT / "README.md"
ADOPTER_PATH = DOCS / "adopter-path.md"
QUESTIONNAIRE = DOCS / "declarations-questionnaire.md"
AGENT_SPEC = DOCS / "agent-spec-1-map-your-warehouse.md"
MESSAGES = DOCS / "adopter-first-contact-messages.md"
CONTRACT_DOC = DOCS / "canonical-input-contract.md"
TEMPLATE = REPO_ROOT / "examples" / "manifest-template.json"

SHAPE_BEGIN = "<!-- input-shape:begin -->"
SHAPE_END = "<!-- input-shape:end -->"

ADOPTER_DOCS = [ADOPTER_PATH, QUESTIONNAIRE, AGENT_SPEC, MESSAGES]


def _between(text: str, begin: str, end: str) -> str:
    start = text.index(begin) + len(begin)
    return text[start : text.index(end)].strip()


# --- the input shape is rendered, not restated -----------------------------


@pytest.mark.parametrize("path", [README, CONTRACT_DOC], ids=lambda p: p.name)
def test_the_input_shape_matches_the_contracts_own_definitions(path: Path) -> None:
    """A summary of code, kept equal to the code that it summarises.

    This is the one table in the adopter documents that would go wrong silently:
    an input added to or removed from the contract changes what an adopter must
    produce, and nothing else would notice the front page still said seven.
    """
    block = _between(path.read_text(encoding="utf-8"), SHAPE_BEGIN, SHAPE_END)
    assert degradation.render_optional_input_table() in block
    assert degradation.render_required_input_list() in block


def test_the_shape_the_documents_claim_is_the_shape_the_contract_has() -> None:
    assert len(spec.REQUIRED_TABLES) == 4
    assert len(spec.OPTIONAL_TABLES) == 3
    for path in (README, CONTRACT_DOC):
        # Whitespace-normalised: the claim is the property under test, not where
        # the line happens to wrap.
        prose = re.sub(r"\s+", " ", path.read_text(encoding="utf-8").replace("**", ""))
        assert "four required, three optional" in prose


def test_every_optional_block_says_what_its_absence_costs() -> None:
    """No optional input may reach an adopter with its loss undocumented."""
    declared = {table.feature_block for table in spec.OPTIONAL_TABLES}
    assert declared <= set(degradation.LOSS_BY_BLOCK)
    assert "UNDOCUMENTED" not in degradation.render_optional_input_table()


def test_the_front_page_says_a_newsroom_can_start_without_the_optional_inputs() -> None:
    # The single largest adoption barrier is a reader concluding seven tables
    # means seven tables. Asserted as a property of the front page rather than
    # left to survive an edit.
    text = README.read_text(encoding="utf-8")
    assert "You can start with all three absent." in text


# --- the two paths are distinguishable -------------------------------------


@pytest.mark.parametrize("path", ADOPTER_DOCS, ids=lambda p: p.name)
def test_every_adopter_document_labels_itself_at_the_top(path: Path) -> None:
    head = path.read_text(encoding="utf-8")[:900]
    assert "This is the adopter path" in head
    # And points at the other one, so a reader who is on the wrong path can get off.
    assert "contributor" in head.lower()


def test_the_contributor_section_labels_itself_and_points_the_other_way() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## Getting started for contributors" in text
    section = text[text.index("## Getting started for contributors") :]
    section = section[: section.index("\n## ")]
    assert "This is the contributor path" in section
    assert "docs/adopter-path.md" in section


def test_the_adopter_path_does_not_open_with_the_contributor_install() -> None:
    """The acceptance criterion, asserted directly.

    Not a style rule. `pip install -e ".[dev]"` installs the linter and the test
    framework, which is the right first move for changing the engine and the
    wrong one for running it -- and starting there is what makes a reader
    conclude the repository is not for them.
    """
    text = ADOPTER_PATH.read_text(encoding="utf-8")
    first_command = text.index("pip install")
    opening = text[: first_command + 60]
    assert 'pip install -e ".[dev]"' not in opening
    # The first install line is the plain one.
    assert re.search(r"^pip install \.$", text, re.M)


def test_the_adopter_path_starts_from_a_clone_rather_than_a_package() -> None:
    # There is no release and nothing on any index. Implying a pip install that
    # does not exist is the fastest way to lose a reader in step 1.
    text = ADOPTER_PATH.read_text(encoding="utf-8")
    assert "git clone" in text
    assert "no release and no package on any index" in text


# --- the manifest template --------------------------------------------------


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def test_the_template_carries_every_key_the_manifest_requires(template: dict) -> None:
    """Four required declarations, not three.

    Tracked against ``_REQUIRED_KEYS`` rather than a list written out here,
    because a template built to the wrong count omits exactly the declaration an
    adopter is least equipped to answer.
    """
    for key in manifest._REQUIRED_KEYS:
        assert key in template, f"the template omits {key!r}"


def test_the_template_declares_every_optional_input(template: dict) -> None:
    assert set(template["optional_inputs"]) == {t.name for t in spec.OPTIONAL_TABLES}
    for entry in template["optional_inputs"].values():
        assert entry["status"] == manifest.ANSWER_REQUIRED


def test_every_undefaulted_declaration_is_present_and_unanswered(template: dict) -> None:
    substantive = set(manifest._REQUIRED_KEYS) - {
        "contract_name",
        "contract_version",
        "optional_inputs",
    }
    assert len(substantive) == 4
    unanswered = set(manifest._unanswered_paths(template))
    for key in substantive:
        assert any(path.split(".")[0] == key for path in unanswered), (
            f"{key} is present but already answered; the template must leave it open"
        )


def test_every_declaration_carries_a_comment_saying_what_it_changes(
    template: dict,
) -> None:
    substantive = set(manifest._REQUIRED_KEYS) - {"contract_name", "contract_version"}
    for key in substantive:
        comment = template.get(f"_{key}")
        assert comment, f"{key} ships with no explanation of what it decides"
        assert "DECIDES" in comment or "OPTIONAL" in comment


def test_the_unanswered_template_is_refused_as_a_question(template: dict) -> None:
    with pytest.raises(manifest.ManifestError) as excinfo:
        manifest.parse_manifest(template)
    message = str(excinfo.value)
    # The refusal is the template's first useful output, so it has to be the
    # question rather than a type error about a sentinel.
    assert manifest.ANSWER_REQUIRED in message
    assert "declarations-questionnaire" in message
    assert "scored_population" in message
    assert "not a known IANA timezone" not in message


def test_a_filled_in_template_is_a_working_manifest(template: dict) -> None:
    """The positive control, and the one that matters.

    A template that only ever fails proves nothing about whether it is a usable
    starting point. This fills it in the way an adopter would and requires the
    result to parse.
    """
    filled = dict(template)
    filled["day_boundary_timezone"] = "America/Chicago"
    filled["week_anchor"] = {"weekday": "Monday", "position": "week_starts_on"}
    filled["article_view"] = {
        "definition_id": "example-v1",
        "content_types": ["article"],
        "event_kinds": [enums.READER_EVENT_KINDS[0]],
    }
    filled["scored_population"] = {
        "definition_id": "example-population-v1",
        "entitled_states": ["active"],
    }
    filled["optional_inputs"] = {
        name: {"status": enums.AVAILABILITY_NOT_DEPLOYED, "available_from": None}
        for name in filled["optional_inputs"]
    }
    parsed = manifest.parse_manifest(filled)
    assert parsed.scored_population.entitled_states == ("active",)
    # And all three absent is a legal answer, which is the claim the front page makes.
    assert not any(entry.is_available for entry in parsed.optional_inputs.values())


# --- the questionnaire ------------------------------------------------------


def test_the_questionnaire_asks_about_every_undefaulted_declaration() -> None:
    body = _between(
        QUESTIONNAIRE.read_text(encoding="utf-8"),
        "<!-- questionnaire:begin -->",
        "<!-- questionnaire:end -->",
    )
    substantive = set(manifest._REQUIRED_KEYS) - {
        "contract_name",
        "contract_version",
        "optional_inputs",
    }
    for key in substantive:
        assert f"`{key}`" in body, f"{key} has no question"


def test_every_question_names_an_owner() -> None:
    body = _between(
        QUESTIONNAIRE.read_text(encoding="utf-8"),
        "<!-- questionnaire:begin -->",
        "<!-- questionnaire:end -->",
    )
    questions = re.split(r"^## ", body, flags=re.M)[1:]
    assert len(questions) == 4
    for question in questions:
        assert "**Owner" in question, f"no owner named in: {question.splitlines()[0]}"


def test_the_commercial_half_of_scored_population_is_attributed_elsewhere() -> None:
    """The point of the questionnaire, asserted.

    scored_population is two decisions with two owners, and the second is not the
    engineer's. A questionnaire that let the port engineer answer it would have
    made the decision easy to skip, which is the failure the undefaulted design
    exists to prevent.
    """
    text = QUESTIONNAIRE.read_text(encoding="utf-8")
    section = text[text.index("## 4. Which readers are scored at all?") :]
    section = section[: section.index("\n## ")]
    assert "4a." in section and "4b." in section
    assert "commercial" in section
    assert "subscription policy" in section


# --- links ------------------------------------------------------------------


@pytest.mark.parametrize("path", ADOPTER_DOCS + [README, CONTRACT_DOC], ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?://)([^)#]+)(?:#[^)]*)?\)", text):
        resolved = (path.parent / target).resolve()
        assert resolved.exists(), f"{path.name} links to missing {target}"


@pytest.mark.parametrize("path", ADOPTER_DOCS, ids=lambda p: p.name)
def test_anchors_into_the_readme_resolve(path: Path) -> None:
    slugs = {
        re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
        for heading in re.findall(r"^#+ (.+)$", README.read_text(encoding="utf-8"), re.M)
    }
    for anchor in re.findall(r"\]\((?:\.\./)?README\.md#([^)]+)\)", path.read_text("utf-8")):
        assert anchor in slugs, f"{path.name} links to README#{anchor}, which does not exist"


@pytest.mark.parametrize("name", ["lint-mapping", "check-oracle", "demo-oracle"])
def test_the_documents_only_name_commands_that_exist(name: str) -> None:
    scripts = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f"engagement-kernel-{name} =" in scripts
