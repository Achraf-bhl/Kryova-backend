"""A part with no material is weighed at 1000 kg/m3, and nobody is told.

Nothing in the bridge applied a material, so CATIA weighed every part it built
at its default density. Observed live: asked "how heavy is it?" about a
120x80x10 steel bracket, the assistant answered "0.095 kg". The real answer is
0.755 kg -- wrong by the density ratio of the material the user had named in
their first message. Aluminium came out 2.7x light the same way.

The same gap had a second symptom. Eight turns after "steel gearbox mounting
plate", asked "what material did we pick?", the assistant answered "none has
been assigned -- which grade would you like?", because it read CATIA rather
than the conversation and CATIA genuinely had none.

Two rules follow, and both are tested here.

**The density never comes from the model.** It decides every mass the part will
report, and "never state a physics number you did not read from a tool result"
has to bind the tools too. The model names a material from the library; the
server looks up what it weighs.

**Applying it in CATIA is best effort, and reported honestly.** `Documents.Open`
on a `.CATMaterial` fails outright without the Material Library product --
measured on a live V5-R33, all three shipped catalogues refused in about ten
seconds each. The mass has to be right on that workstation too.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.ai.tools import CATIA_STATE_KEYS
from app.catia.tool_specs import TOOL_SPECS_BY_NAME
from app.catia.validation import SchemaError, validate
from app.solve.materials import MATERIALS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.tool_table import check_call  # noqa: E402

SPEC = TOOL_SPECS_BY_NAME["catia_set_material"]


class TestTheModelChoosesAMaterialNotADensity:
    def test_the_schema_offers_the_real_library(self) -> None:
        assert set(SPEC.parameters["properties"]["material"]["enum"]) == set(MATERIALS)

    def test_density_is_not_something_the_model_may_send(self) -> None:
        # additionalProperties is false, so a model that tries to set the number
        # that decides every mass is refused rather than believed.
        with pytest.raises(SchemaError):
            validate({"material": "steel-1018", "density_kg_m3": 1.0}, SPEC.parameters)

    def test_an_invented_material_is_refused(self) -> None:
        with pytest.raises(SchemaError):
            validate({"material": "unobtainium"}, SPEC.parameters)

    def test_the_daemon_requires_the_server_supplied_density(self) -> None:
        # The server injects it. A call that reached the daemon without one is
        # refused rather than silently defaulted to something plausible.
        with pytest.raises(Exception):
            check_call("catia_set_material", {"material": "steel-1018"}, approval_token=None)

    def test_the_daemon_accepts_the_injected_call(self) -> None:
        check_call(
            "catia_set_material",
            {"material": "steel-1018", "density_kg_m3": 7870.0},
            approval_token=None,
        )

    def test_it_is_a_mutating_tool(self) -> None:
        # It changes the part, so it needs the same consent every other write does.
        assert SPEC.mutating


class TestTheMaterialSurvivesTheTurn:
    def test_it_is_recorded_in_conversation_state(self) -> None:
        # Without this the state block cannot answer "what material did we pick?"
        # and the assistant re-asks a question already answered.
        assert "material" in CATIA_STATE_KEYS
        assert "density_kg_m3" in CATIA_STATE_KEYS


class TestMassFollowsTheChosenMaterial:
    @pytest.fixture
    def catia(self, tmp_path: Path) -> Any:
        backend = MockCatia(tmp_path)
        backend.new_part(name="Plate")
        backend.sketch_rectangle(plane="XY", width_mm=120.0, height_mm=80.0)
        backend.pad(sketch="Sketch.1", length_mm=10.0)
        return backend

    def test_steel_gives_the_hand_calculated_mass(self, catia: Any) -> None:
        # 120 x 80 x 10 mm = 96000 mm3; at 7870 kg/m3 that is 0.75552 kg.
        result = catia.set_material(material="steel-1018", density_kg_m3=7870.0)
        assert result["mass_kg"] == pytest.approx(0.75552, rel=1e-6)

    def test_aluminium_gives_a_different_one(self, catia: Any) -> None:
        result = catia.set_material(material="aluminium-6061-t6", density_kg_m3=2700.0)
        assert result["mass_kg"] == pytest.approx(0.2592, rel=1e-6)

    def test_the_default_would_have_been_wrong_by_the_density_ratio(self, catia: Any) -> None:
        # The bug, stated as arithmetic: CATIA's 1000 kg/m3 against steel's 7870.
        steel = catia.set_material(material="steel-1018", density_kg_m3=7870.0)["mass_kg"]
        unspecified = 96_000 * 1e-9 * 1000.0
        assert steel / unspecified == pytest.approx(7.87, rel=1e-3)

    def test_a_later_measure_agrees(self, catia: Any) -> None:
        # The material has to stick, not just colour the one reply.
        catia.set_material(material="titanium-ti6al4v", density_kg_m3=4430.0)
        assert catia.measure()["mass_kg"] == pytest.approx(0.42528, rel=1e-6)

    def test_the_material_is_reported_back(self, catia: Any) -> None:
        assert catia.set_material(material="stainless-304", density_kg_m3=8000.0)["material"] == (
            "stainless-304"
        )

    def test_it_says_whether_catia_actually_took_it(self, catia: Any) -> None:
        # An install without the Material Library cannot attach anything, and
        # claiming otherwise would be the same class of lie as the wrong mass.
        result = catia.set_material(material="steel-1018", density_kg_m3=7870.0)
        assert result["applied_in_catia"] is False
        assert result["detail"]


class TestEveryLibraryMaterialIsUsable:
    @pytest.mark.parametrize("key", sorted(MATERIALS))
    def test_it_round_trips(self, key: str, tmp_path: Path) -> None:
        validate({"material": key}, SPEC.parameters)
        material = MATERIALS[key]
        check_call(
            "catia_set_material",
            {"material": key, "density_kg_m3": material.density_kg_m3},
            approval_token=None,
        )


class TestTheDensityActuallyReachesTheDaemon:
    """The ordering bug this class exists for.

    `catia_set_material` was injecting the density into the arguments *before*
    they were validated against the model-facing schema -- the schema that lists
    `material` alone and sets `additionalProperties: false` precisely so the
    model cannot choose the number every mass is computed from. So the check
    rejected the server's own field, and every live call came back
    `catia_set_material: arguments has unknown field(s): density_kg_m3`.

    Nothing above caught it. Those tests drive the daemon's schema, which
    requires the density, and the mock backend, which is handed one directly.
    The whole feature was green and had never once worked: run live, a steel
    shaft came back at CATIA's default 1000 kg/m3, which is the exact bug the
    feature was written to fix. So this drives `call_catia` itself, and asserts
    on what leaves for the daemon.
    """

    @pytest.fixture
    def sent(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        from unittest.mock import MagicMock

        from app.catia import dispatch

        captured: list[dict[str, Any]] = []

        def fake_execute(db: Any, **kwargs: Any) -> dict[str, Any]:
            captured.append(kwargs["arguments"])
            return {"material": kwargs["arguments"].get("material"), "mass_kg": 0.75552}

        monkeypatch.setattr(
            dispatch, "_resolve_connection", lambda db, user_id: (MagicMock(id="d"), MagicMock())
        )
        monkeypatch.setattr(dispatch, "_enforce_rate_limit", lambda device_id: None)
        monkeypatch.setattr(dispatch, "_execute", fake_execute)
        monkeypatch.setattr(dispatch, "_log", lambda db, **kwargs: None)
        return captured

    def test_the_server_supplied_density_survives_validation(
        self, db_session: Any, sent: list[dict[str, Any]]
    ) -> None:
        from app.catia.dispatch import call_catia

        call_catia(
            db_session,
            user_id="u",
            conversation_id=None,
            tool="catia_set_material",
            arguments={"material": "steel-1018"},
        )
        assert sent[0]["density_kg_m3"] == pytest.approx(MATERIALS["steel-1018"].density_kg_m3)

    def test_the_model_still_cannot_send_one(
        self, db_session: Any, sent: list[dict[str, Any]]
    ) -> None:
        # The strictness the ordering bug was hiding behind must still be there.
        from app.catia.dispatch import CatiaError, call_catia

        with pytest.raises(CatiaError):
            call_catia(
                db_session,
                user_id="u",
                conversation_id=None,
                tool="catia_set_material",
                arguments={"material": "steel-1018", "density_kg_m3": 1.0},
            )
        assert not sent

    def test_every_library_material_gets_through(
        self, db_session: Any, sent: list[dict[str, Any]]
    ) -> None:
        from app.catia.dispatch import call_catia

        for key, material in sorted(MATERIALS.items()):
            call_catia(
                db_session,
                user_id="u",
                conversation_id=None,
                tool="catia_set_material",
                arguments={"material": key},
            )
            assert sent[-1]["density_kg_m3"] == pytest.approx(material.density_kg_m3)

    def test_other_tools_are_left_alone(self, db_session: Any, sent: list[dict[str, Any]]) -> None:
        from app.catia.dispatch import call_catia

        call_catia(
            db_session,
            user_id="u",
            conversation_id=None,
            tool="catia_pad",
            arguments={"sketch": "Sketch.1", "length_mm": 10.0},
        )
        assert "density_kg_m3" not in sent[0]


class TestAThroughHoleDoesNotArgueAboutDepth:
    """`depth_mm: 0` with `through_all: true` used to cost most of a turn.

    The schema refuses zero because zero is not a depth, and the tool
    description says to omit the field entirely for a through hole. Observed
    live, the model sent it anyway and got `depth_mm must be greater than 0`,
    then guessed `position: 'corner'` and got a second rejection, before
    finally producing a call that ran. The value is unused for a through hole,
    so there is nothing to argue about.
    """

    @pytest.fixture
    def hole(self) -> Any:
        from app.catia.dispatch import _normalise

        return _normalise

    def test_a_zero_depth_is_dropped_for_a_through_hole(self, hole: Any) -> None:
        cleaned = hole(
            "catia_hole",
            {
                "face": "top",
                "position": "center",
                "diameter_mm": 8.0,
                "depth_mm": 0,
                "through_all": True,
            },
        )
        assert "depth_mm" not in cleaned
        validate(cleaned, TOOL_SPECS_BY_NAME["catia_hole"].parameters)

    def test_through_all_defaults_to_true(self, hole: Any) -> None:
        # The schema's own default, so an omitted `through_all` behaves the same.
        cleaned = hole(
            "catia_hole",
            {"face": "top", "position": "center", "diameter_mm": 8.0, "depth_mm": 0},
        )
        assert "depth_mm" not in cleaned

    def test_a_blind_hole_keeps_its_depth(self, hole: Any) -> None:
        arguments = {
            "face": "top",
            "position": "center",
            "diameter_mm": 8.0,
            "depth_mm": 5.0,
            "through_all": False,
        }
        assert hole("catia_hole", arguments) == arguments

    def test_a_zero_depth_blind_hole_is_still_refused(self, hole: Any) -> None:
        # Here the zero really is wrong, and saying so is the right answer.
        cleaned = hole(
            "catia_hole",
            {
                "face": "top",
                "position": "center",
                "diameter_mm": 8.0,
                "depth_mm": 0,
                "through_all": False,
            },
        )
        with pytest.raises(SchemaError):
            validate(cleaned, TOOL_SPECS_BY_NAME["catia_hole"].parameters)

    def test_nothing_else_is_touched(self, hole: Any) -> None:
        arguments = {"sketch": "Sketch.1", "length_mm": 0}
        assert hole("catia_pad", arguments) == arguments
