"""Knowledge in the mock: parameters that drive, formulas that recompute.

The point of these is that the mock does the *work* rather than recording that
it was asked to. A formula that is stored and never evaluated looks identical in
the tree, reads back correctly from `knowledge_report`, and silently stops
propagating — which is exactly the failure a design table exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.catia_bridge.backend import CatiaOperationError
from scripts.catia_bridge.mock_catia import MockCatia


@pytest.fixture
def catia(tmp_path: Path) -> MockCatia:
    backend = MockCatia(tmp_path / "catia")
    backend.new_part(name="Bracket")
    return backend


def _length(catia: MockCatia, name: str, value: float) -> None:
    catia.parameter_create(name=name, kind="length", value=value)


class TestParameters:
    def test_a_created_parameter_is_typed_and_listed(self, catia: MockCatia) -> None:
        catia.parameter_create(name="Width", kind="length", value=40)
        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Width"]["value"] == 40.0
        assert listed["Width"]["unit"] == "mm"

    def test_an_angle_carries_degrees_not_millimetres(self, catia: MockCatia) -> None:
        catia.parameter_create(name="Draft", kind="angle", value=3)
        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Draft"]["unit"] == "deg"

    def test_a_duplicate_name_is_refused(self, catia: MockCatia) -> None:
        _length(catia, "Width", 40)
        with pytest.raises(CatiaOperationError, match="already has a parameter"):
            _length(catia, "Width", 50)

    def test_a_name_no_formula_could_reference_is_refused(self, catia: MockCatia) -> None:
        # Formulas are parsed, so a name that is not an identifier could never
        # appear in one -- the parameter would be unreachable the moment it
        # mattered.
        with pytest.raises(CatiaOperationError, match="could never"):
            catia.parameter_create(name="Wall Thickness", kind="length", value=2)

    def test_a_value_outside_its_own_bounds_is_refused(self, catia: MockCatia) -> None:
        with pytest.raises(CatiaOperationError, match="below the minimum"):
            catia.parameter_create(name="Bore", kind="length", value=1, minimum=5)


class TestFormulas:
    def test_a_formula_computes_its_parameter_immediately(self, catia: MockCatia) -> None:
        _length(catia, "Width", 40)
        _length(catia, "Half", 0)
        result = catia.formula_create(parameter="Half", expression="Width / 2")
        assert result["value"] == pytest.approx(20.0)

    def test_changing_an_input_recomputes_the_formula(self, catia: MockCatia) -> None:
        # The property that makes a formula worth having. Storing one that never
        # recomputes is worse than storing none: it looks right in the tree.
        _length(catia, "Width", 40)
        _length(catia, "Half", 0)
        catia.formula_create(parameter="Half", expression="Width / 2")

        catia.set_parameter(name="Width", value=100, unit="mm")

        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Half"]["value"] == pytest.approx(50.0)

    def test_a_chain_of_formulas_settles(self, catia: MockCatia) -> None:
        _length(catia, "Width", 10)
        _length(catia, "Double", 0)
        _length(catia, "Quadruple", 0)
        catia.formula_create(parameter="Double", expression="Width * 2")
        catia.formula_create(parameter="Quadruple", expression="Double * 2")

        catia.set_parameter(name="Width", value=5, unit="mm")

        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Double"]["value"] == pytest.approx(10.0)
        assert listed["Quadruple"]["value"] == pytest.approx(20.0)

    def test_a_formula_that_reads_itself_is_refused(self, catia: MockCatia) -> None:
        _length(catia, "Width", 40)
        with pytest.raises(CatiaOperationError, match="defines itself"):
            catia.formula_create(parameter="Width", expression="Width + 1")

    def test_a_circular_pair_is_refused_and_not_kept(self, catia: MockCatia) -> None:
        # And crucially not kept: a stored formula that cannot be solved would
        # make every later parameter change fail behind the same error.
        _length(catia, "A", 1)
        _length(catia, "B", 2)
        catia.formula_create(parameter="A", expression="B + 1")
        with pytest.raises(CatiaOperationError, match="never settle"):
            catia.formula_create(parameter="B", expression="A + 1")

        assert catia.knowledge_report(kind="formulas")["formulas"][0]["parameter"] == "A"
        catia.set_parameter(name="B", value=7, unit="mm")

    def test_a_formula_naming_a_parameter_that_does_not_exist_is_refused(
        self, catia: MockCatia
    ) -> None:
        _length(catia, "Half", 0)
        with pytest.raises(CatiaOperationError, match="Width"):
            catia.formula_create(parameter="Half", expression="Width / 2")


class TestDesignTables:
    def test_activating_a_row_writes_its_configuration(self, catia: MockCatia) -> None:
        _length(catia, "Width", 10)
        _length(catia, "Height", 10)
        catia.design_table_create(
            name="Sizes",
            columns=["Width", "Height"],
            rows=[[10, 20], [30, 40], [50, 60]],
            active_row=1,
        )

        catia.design_table_activate(table="Sizes", row=3)

        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Width"]["value"] == 50
        assert listed["Height"]["value"] == 60

    def test_a_configuration_drives_the_formulas_too(self, catia: MockCatia) -> None:
        _length(catia, "Width", 10)
        _length(catia, "Half", 0)
        catia.formula_create(parameter="Half", expression="Width / 2")
        catia.design_table_create(name="Sizes", columns=["Width"], rows=[[10], [80]])

        catia.design_table_activate(table="Sizes", row=2)

        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Half"]["value"] == pytest.approx(40.0)

    def test_a_ragged_row_is_refused_with_its_number(self, catia: MockCatia) -> None:
        _length(catia, "Width", 10)
        _length(catia, "Height", 10)
        with pytest.raises(CatiaOperationError, match="Row 2"):
            catia.design_table_create(
                name="Sizes", columns=["Width", "Height"], rows=[[1, 2], [3]]
            )

    def test_a_row_outside_the_table_is_refused(self, catia: MockCatia) -> None:
        _length(catia, "Width", 10)
        catia.design_table_create(name="Sizes", columns=["Width"], rows=[[1], [2]])
        with pytest.raises(CatiaOperationError, match="outside this table"):
            catia.design_table_activate(table="Sizes", row=5)


class TestChecks:
    def test_a_satisfied_check_reports_satisfied(self, catia: MockCatia) -> None:
        _length(catia, "Wall", 3)
        result = catia.check_create(name="MinWall", condition="Wall >= 2")
        assert result["satisfied"] is True

    def test_a_check_follows_the_parameter_it_watches(self, catia: MockCatia) -> None:
        _length(catia, "Wall", 3)
        catia.check_create(
            name="MinWall", condition="Wall >= 2", message="Too thin to mould"
        )
        catia.set_parameter(name="Wall", value=1, unit="mm")

        failing = catia.knowledge_report(kind="checks", failing_only=True)
        assert [check["name"] for check in failing["checks"]] == ["MinWall"]
        assert failing["checks"][0]["message"] == "Too thin to mould"

    def test_a_rule_is_recorded_and_says_it_did_not_run(self, catia: MockCatia) -> None:
        # A CATIA rule is imperative CATVBS. Running one here would mean a
        # second scripting language whose behaviour could not match CATIA's.
        result = catia.rule_create(name="Naming", body="if Width > 10 then ...")
        assert result["evaluated"] is False


class TestPersistence:
    def test_formulas_survive_a_checkpoint_and_restore(self, catia: MockCatia) -> None:
        # Formulas are part of the document in CATIA. A checkpoint that dropped
        # them would restore a part whose parameters had quietly stopped moving.
        _length(catia, "Width", 40)
        _length(catia, "Half", 0)
        catia.formula_create(parameter="Half", expression="Width / 2")
        snapshot = catia.checkpoint(label="with formulas")

        catia.restore(checkpoint=snapshot)
        catia.set_parameter(name="Width", value=10, unit="mm")

        listed = {p["name"]: p for p in catia.list_parameters()["parameters"]}
        assert listed["Half"]["value"] == pytest.approx(5.0)
