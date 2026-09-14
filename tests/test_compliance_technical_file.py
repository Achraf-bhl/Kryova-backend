"""The technical-file contribution is organised by the clause, says what is
missing, and can be checked for tampering — master plan E19 task 3, the half
that touches no database. The export route is in
`tests/test_designs.py::TestTheTechnicalFile`."""

from __future__ import annotations

import copy
import datetime
import json
import string

import pytest

from app.compliance import boundary
from app.compliance import technical_file as tf
from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.technical_file import Artefact, Contribution, Element

NOW = datetime.datetime(2026, 9, 14, 12, 0, tzinfo=datetime.UTC)


def _artefacts() -> list[Artefact]:
    return [
        Artefact(tf.SPECIFICATION, "spec", {"name": "Bracket", "parameters": []}),
        Artefact(tf.BUILD_PLAN, "plan", {"compiled": True, "calls": []}),
        Artefact(tf.REVISIONS, "revisions", [{"revision": 1}]),
        Artefact(tf.OPERATIONS, "operations", []),
        Artefact(tf.ANALYSES, "analyses", {"runs": []}),
        Artefact(tf.APPROVALS, "approvals", []),
    ]


def _without_quotations(value: object) -> object:
    if isinstance(value, dict):
        return {k: _without_quotations(v) for k, v in value.items() if k not in {"quote", "annex"}}
    if isinstance(value, list):
        return [_without_quotations(v) for v in value]
    return value


def _file() -> dict[str, object]:
    return tf.assemble(
        design={"name": "Bracket"},
        artefacts=_artefacts(),
        statements={"validation": "Not validated."},
        generated_at=NOW,
    )


class TestAnnexIvPartAIsReadWhole:
    def test_all_fifteen_points_a_to_o_are_quoted_from_the_official_journal(self) -> None:
        assert list(tf.POINTS) == list(string.ascii_lowercase[:15])
        for clause in tf.POINTS.values():
            assert OJ_L_165 in clause.sources

    def test_every_point_has_exactly_one_element(self) -> None:
        assert [element.letter for element in tf.ELEMENTS] == list(tf.POINTS)


class TestNothingIsClaimedInFull:
    def test_kryova_contributes_to_four_points_and_supplies_none_alone(self) -> None:
        assert {e.letter for e in tf.ELEMENTS if e.contribution is Contribution.PART} == {"a", "c", "d", "g"}
        assert set(Contribution) == {Contribution.PART, Contribution.NONE}

    def test_every_element_says_what_only_the_manufacturer_can_supply(self) -> None:
        for element in tf.ELEMENTS:
            assert element.manufacturer.strip(), element.letter

    def test_a_contribution_with_nothing_carrying_it_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names the artefacts"):
            Element("a", Contribution.PART, kryova="Everything.", manufacturer="Nothing.")

    def test_point_m_is_answered_rather_than_skipped(self) -> None:
        (m,) = [e for e in tf.ELEMENTS if e.letter == "m"]

        assert "produces no safety related software" in m.kryova

    def test_retention_is_named_as_not_provided(self) -> None:
        assert any("Ten-year retention" in item for item in tf.NOT_INCLUDED)

    def test_the_file_makes_no_conformity_claim_in_its_own_words(self) -> None:
        """`app/compliance/` is exempt from the product-wide scan because it
        quotes the law; the file's own words are not. The quotations are the
        regulation's ("to ensure the conformity of the machinery"), and are
        removed before the scan rather than excused inside it."""
        own_words = json.dumps(_without_quotations(_file()), ensure_ascii=False)

        assert "Annex IV" in own_words
        assert boundary.claims_in(own_words) == []

    def test_the_scan_would_see_a_claim_in_the_file_s_own_words(self) -> None:
        body = _file()
        body["what_this_is"] = "A technical file that guarantees full compliance."

        assert boundary.claims_in(json.dumps(_without_quotations(body))) == ["guarantees full compliance"]


class TestTheFileCanBeChecked:
    def test_a_file_as_assembled_verifies(self) -> None:
        assert tf.verify(_file())

    def test_every_artefact_carries_the_hash_of_its_content(self) -> None:
        for item in _file()["artefacts"]:  # type: ignore[attr-defined]
            assert item["sha256"] == tf.sha256_of(item["content"])

    @pytest.mark.parametrize(
        "tamper",
        [
            lambda f: f["artefacts"][0]["content"].update(name="Bigger bracket"),
            lambda f: f["elements"][1].update(contribution="part"),
            lambda f: f["design"].update(name="Another machine"),
        ],
    )
    def test_a_changed_file_no_longer_verifies(self, tamper) -> None:  # type: ignore[no-untyped-def]
        changed = copy.deepcopy(_file())

        tamper(changed)

        assert not tf.verify(changed)

    def test_an_artefact_changed_and_the_file_re_digested_is_still_caught(self) -> None:
        """Re-digesting the whole file is easy; each artefact's own hash is what
        says which part was touched, and it must be checked on its own."""
        changed = copy.deepcopy(_file())
        changed["artefacts"][0]["content"]["name"] = "Bigger bracket"  # type: ignore[index]
        body = {k: v for k, v in changed.items() if k != "digest"}
        changed["digest"] = {**changed["digest"], "value": tf.sha256_of(body)}  # type: ignore[dict-item]

        assert not tf.verify(changed)

    def test_the_digest_does_not_depend_on_key_order(self) -> None:
        assert tf.sha256_of({"a": 1, "b": [1, 2]}) == tf.sha256_of({"b": [1, 2], "a": 1})

    def test_an_element_citing_an_artefact_the_file_does_not_carry_is_refused(self) -> None:
        without_plan = [a for a in _artefacts() if a.id != tf.BUILD_PLAN]

        with pytest.raises(ValueError, match=r"Point \(c\) cites \['build-plan'\]"):
            tf.assemble(design={}, artefacts=without_plan, statements={}, generated_at=NOW)

    def test_it_names_its_format_and_version(self) -> None:
        body = _file()

        assert body["format"] == tf.FORMAT
        assert body["format_version"] == 1
