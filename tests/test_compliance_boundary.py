"""The written boundary holds, in the copy and in the agent's vocabulary —
master plan E19 task 2.

The boundary is a decision, and no test can make a decision right. What these
can do is hold the product to it: nothing in `app/` makes a conformity claim
about Kryova or its output, no tool the agent is offered produces what the
boundary says Kryova never produces, and every placement on the boundary rests
on a clause read from the Official Journal.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

import pytest

from app.ai.tools import ToolBox
from app.compliance import boundary
from app.compliance.boundary import Output, Stance
from app.compliance.eu_machinery_regulation import OJ_L_165
from app.core.config import BASE_DIR
from app.verify.commitments import BY_ID

APP = BASE_DIR / "app"

#: The one package allowed to spell the claims out: it quotes the law, and it
#: holds the list of claims this test forbids everywhere else.
_EXEMPT = APP / "compliance"


def _strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


class TestThePlacementsRestOnTheText:
    def test_every_output_cites_a_clause_read_from_the_official_journal(self) -> None:
        for output in boundary.OUTPUTS:
            for clause in output.rests_on:
                assert OJ_L_165 in clause.sources, (output.what, clause.citation)

    def test_an_output_with_nothing_behind_it_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no clause behind it"):
            Output(what="A brochure", stance=Stance.MAY_GENERATE, why="because", rests_on=())

    def test_the_boundary_has_all_three_sides(self) -> None:
        for stance in Stance:
            assert boundary.outputs(stance), stance

    @pytest.mark.parametrize(
        "what",
        [
            "An EU declaration of conformity",
            "A CE marking, or an image or label of one",
            "Software or logic that performs a safety function on the machine",
        ],
    )
    def test_the_manufacturer_s_acts_and_safety_logic_are_never_produced(self, what: str) -> None:
        (output,) = [o for o in boundary.OUTPUTS if o.what == what]

        assert output.stance is Stance.NEVER

    def test_a_risk_assessment_is_only_ever_a_draft(self) -> None:
        (output,) = [o for o in boundary.OUTPUTS if o.what == "Risk assessment documentation"]

        assert output.stance is Stance.DRAFT_FOR_A_NAMED_PERSON


class TestThePlanWasCorrected:
    """The plan put 'software ensuring safety functions' in Annex I. It is not."""

    def test_software_ensuring_safety_functions_is_an_annex_ii_safety_component(self) -> None:
        assert boundary.ANNEX_II_18.citation == "Annex II, point 18"
        assert boundary.ANNEX_II_18.quote == "Software ensuring safety functions."

    def test_third_party_assessment_is_for_learning_systems_only(self) -> None:
        assert "should not apply to software incapable of learning" in boundary.RECITAL_55.quote
        assert "self-evolving" in boundary.ANNEX_I_PART_A_5.quote


class TestTheClaimPatterns:
    @pytest.mark.parametrize(
        "claim",
        [
            "Kryova is CE certified.",
            "Every design is CE-compliant.",
            "The frame conforms to the Machinery Regulation.",
            "Parts are compliant with Regulation (EU) 2023/1230.",
            "Certified to 2006/42/EC.",
            "Kryova generates the EU declaration of conformity.",
            "The agent can affix the CE marking.",
            "We carry out the conformity assessment for you.",
            "The assistant completes the risk assessment.",
            "Our checks guarantee full compliance.",
        ],
    )
    def test_a_conformity_claim_is_caught(self, claim: str) -> None:
        assert boundary.claims_in(claim), claim

    @pytest.mark.parametrize(
        "mention",
        [
            boundary.SALES_SENTENCE,
            "The manufacturer affixes the CE marking.",
            "The agent drafts a risk assessment for the engineer to complete.",
            "A licensed engineer signs.",
        ],
    )
    def test_the_boundary_stated_is_not_the_boundary_crossed(self, mention: str) -> None:
        assert boundary.claims_in(mention) == []


class TestNothingInTheProductCrossesIt:
    def test_no_string_in_the_application_makes_a_conformity_claim(self) -> None:
        """Every string constant in `app/` — tool descriptions, system prompts,
        the trust pages, the handbook, error messages, docstrings."""
        offending = [
            f"{path.relative_to(BASE_DIR)}:{line}: {claim!r}"
            for path in sorted(APP.rglob("*.py"))
            if _EXEMPT not in path.parents
            for line, text in _strings(path)
            for claim in boundary.claims_in(text)
        ]

        assert offending == []

    def test_the_scan_sees_a_claim_planted_where_the_product_keeps_its_copy(
        self, tmp_path: Path
    ) -> None:
        planted = tmp_path / "copy.py"
        planted.write_text('TAGLINE = "Designs that are CE-certified out of the box."\n', encoding="utf-8")

        assert [boundary.claims_in(text) for _, text in _strings(planted)] == [["CE-certified"]]

    def test_no_tool_the_agent_is_offered_produces_what_is_never_produced(self) -> None:
        box = ToolBox(db=cast(Any, None), user=cast(Any, None))
        forbidden = ("declaration", "conformity", "ce_mark", "certif", "plc", "safety_function")

        names = [tool.name for tool in box.every_tool()]

        assert names
        assert [n for n in names if any(word in n.lower() for word in forbidden)] == []


class TestThePublicCommitment:
    def test_the_trust_page_carries_it_and_names_this_test(self) -> None:
        commitment = BY_ID["the-manufacturer-makes-the-conformity-claims"]

        assert "tests/test_compliance_boundary.py" in commitment.enforced_by
        assert "Article 10(2)" in commitment.why
        assert boundary.claims_in(commitment.we_will_not) == []
