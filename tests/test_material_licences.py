"""Every material number names the right it is shown under (master plan E21.4).

Offline; no database. **Written on Linux on 2026-09-15 and not run there as pytest** (the user's
rule). The shipped-set findings were checked by a one-off script the same day.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.solve.material_licences import (
    READINGS,
    CustomerDataError,
    customer_records,
    load_customer_records,
)
from app.solve.materials import (
    MATWEB_NO_RELIANCE,
    RECORDS,
    Right,
    Source,
    SourceKind,
    Status,
)


def _licensed(**overrides):  # type: ignore[no-untyped-def]
    document = {
        "licensee": "Acme Aero",
        "licence": "MMPDS-2026 Volume I, single-user PDF",
        "materials": [
            {
                "slug": "al-2024-t3-sheet-acme",
                "display_name": "Aluminium 2024-T3 sheet",
                "category": "aluminium alloy",
                "condition": {"temper": "T3", "form": "sheet"},
                "properties": [
                    {
                        "name": "yield_strength_mpa",
                        "value": 40.0,
                        "unit": "ksi",
                        "status": "specified",
                        "citation": "Table (as the customer reads it)",
                    }
                ],
            }
        ],
    }
    document.update(overrides)
    return document


class TestTheShippedSetNamesItsRights:
    def test_every_shipped_property_says_what_right_it_is_shown_under(self) -> None:
        for record in RECORDS.values():
            for prop in record.properties.values():
                assert prop.to_dict()["source"]["right"] in {str(r) for r in Right}

    def test_nothing_shipped_is_held_under_a_customer_licence(self) -> None:
        for record in RECORDS.values():
            for prop in record.properties.values():
                assert prop.source.right is not Right.CUSTOMER_LICENCE, (record.slug, prop.name)

    def test_nothing_shipped_claims_to_be_redistributable_yet(self) -> None:
        """No source's terms granting redistribution have been read. Fails on purpose when
        one is, so the claim is widened deliberately."""
        rights = {prop.source.right for record in RECORDS.values() for prop in record.properties.values()}
        assert rights == {Right.NOT_ESTABLISHED}

    def test_the_matweb_materials_are_flagged_and_never_a_design_basis(self) -> None:
        citing = sorted(
            record.slug
            for record in RECORDS.values()
            if any(p.source.citation.startswith("MatWeb") for p in record.properties.values())
        )
        assert citing == ["stainless-304", "steel-1018"]
        for slug in citing:
            for prop in RECORDS[slug].properties.values():
                if prop.source.citation.startswith("MatWeb"):
                    assert prop.source.reliance_disclaimed
                    assert MATWEB_NO_RELIANCE in prop.source.terms
                    assert not prop.is_design_basis


class TestASourceStatesItsRightHonestly:
    def test_redistributable_needs_the_terms_that_grant_it(self) -> None:
        with pytest.raises(ValueError, match="terms somebody"):
            Source("A datasheet", SourceKind.DATASHEET, right=Right.REDISTRIBUTABLE)

    def test_a_customer_licence_names_the_licensee(self) -> None:
        with pytest.raises(ValueError, match="licensee"):
            Source("MMPDS", SourceKind.STANDARD, right=Right.CUSTOMER_LICENCE, terms="x")

    def test_a_reliance_disclaimer_quotes_the_source(self) -> None:
        with pytest.raises(ValueError, match="own words"):
            Source("A site", SourceKind.DATASHEET, reliance_disclaimed=True)

    def test_a_disclaimed_source_is_not_a_design_basis_even_when_specified(self) -> None:
        from app.solve.materials import Property

        prop = Property(
            "yield_strength_mpa",
            250.0,
            Status.SPECIFIED,
            Source("A site", SourceKind.DATASHEET, terms="do not rely", reliance_disclaimed=True),
        )
        assert not prop.is_design_basis


class TestCustomerAllowablesStayTheCustomers:
    def test_every_loaded_property_is_under_the_customer_licence(self) -> None:
        records = customer_records(_licensed())
        prop = records["al-2024-t3-sheet-acme"].properties["yield_strength_mpa"]

        assert prop.source.right is Right.CUSTOMER_LICENCE
        assert prop.source.licensee == "Acme Aero"
        assert prop.to_dict()["source"]["licensee"] == "Acme Aero"
        assert prop.is_design_basis

    def test_the_value_is_converted_at_the_one_boundary(self) -> None:
        prop = customer_records(_licensed())["al-2024-t3-sheet-acme"].properties["yield_strength_mpa"]
        assert prop.value == pytest.approx(40.0 * 6.894757293168361)

    def test_a_file_cannot_choose_its_own_right(self) -> None:
        document = _licensed()
        document["materials"][0]["properties"][0]["right"] = "redistributable"
        prop = customer_records(document)["al-2024-t3-sheet-acme"].properties["yield_strength_mpa"]
        assert prop.source.right is Right.CUSTOMER_LICENCE

    def test_a_shipped_slug_is_refused(self) -> None:
        document = _licensed()
        document["materials"][0]["slug"] = "steel-1018"
        with pytest.raises(CustomerDataError, match="Kryova ships"):
            customer_records(document)

    @pytest.mark.parametrize("field", ["licensee", "licence"])
    def test_a_file_without_its_licence_is_refused(self, field: str) -> None:
        with pytest.raises(CustomerDataError, match="licensee"):
            customer_records(_licensed(**{field: ""}))

    def test_a_property_without_a_citation_is_refused(self) -> None:
        document = _licensed()
        document["materials"][0]["properties"][0]["citation"] = ""
        with pytest.raises(CustomerDataError, match="cites"):
            customer_records(document)

    def test_the_file_is_read_from_the_deployment(self, tmp_path: Path) -> None:
        target = tmp_path / "licensed-materials.json"
        target.write_text(json.dumps(_licensed()), encoding="utf-8")
        assert list(load_customer_records(target)) == ["al-2024-t3-sheet-acme"]

    def test_no_licensed_materials_file_is_in_the_repository(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tracked = subprocess.run(
            ["git", "ls-files", "*.json"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        for name in tracked:
            path = root / name
            text = path.read_text(encoding="utf-8", errors="replace")
            assert '"licensee"' not in text, f"{path} looks like a customer's licensed materials file"


def test_every_reading_names_a_source_and_a_date() -> None:
    for reading in READINGS:
        assert reading.source.startswith("https://")
        assert reading.read_on == "2026-09-15"
