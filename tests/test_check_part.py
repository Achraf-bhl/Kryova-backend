"""`check_part` — Decision 3 reaching the conversation.

`assertions.py` has existed since 2026-09-04 and could not be called from a chat.
So the only thing between "every tool returned ok" and "the part is right" was
the model's opinion, and on 2026-09-05 that gap produced a flange whose every
call succeeded, whose bolt circle was on a 99 mm diameter instead of 70, whose
every edge was rounded instead of four, and which read in the transcript as a
complete success.

Offline: the measurement is stubbed, because what is under test is the seam and
the honesty of the verdict, not the kernel's arithmetic — that has its own suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.tools import ToolBox, ToolError


def _box(measurement: Any) -> ToolBox:
    """A toolbox whose only wired collaborator is the measurement."""
    box = ToolBox.__new__(ToolBox)
    object.__setattr__(box, "_tools", {})

    def measure(name: str, arguments: dict[str, Any]) -> Any:
        assert name == "catia_measure", f"check_part measured with {name}"
        if isinstance(measurement, Exception):
            raise measurement
        return measurement

    object.__setattr__(box, "_call_catia", measure)
    return box


FLAT = {
    "mass_kg": 0.2,
    "volume_mm3": 101701.91,
    "solid_count": 1,
    "face_count": 11,
    "bounding_box_mm": {"size": [100.0, 100.0, 12.0]},
    "centre_of_mass_mm": [0.0, 0.0, 6.0],
}

#: The kernel route wraps its payload; the bridge returns it flat. A caller must
#: not have to know which backend answered.
WRAPPED = {"backend": "occt", "detail": "full", "measurements": FLAT}


class TestItReadsWhicheverShapeTheBackendReturns:
    def test_a_flat_payload(self) -> None:
        out = _box(FLAT)._check_part(
            [{"name": "one solid", "measure": "solid_count", "comparison": "==",
              "bound": 1, "tolerance": 0.5}]
        )

        assert out["ok"] is True

    def test_a_wrapped_payload(self) -> None:
        out = _box(WRAPPED)._check_part(
            [{"name": "one solid", "measure": "solid_count", "comparison": "==",
              "bound": 1, "tolerance": 0.5}]
        )

        assert out["ok"] is True

    def test_a_nested_path_resolves(self) -> None:
        out = _box(WRAPPED)._check_part(
            [{"name": "12 mm thick", "measure": "bounding_box_mm.size[2]",
              "comparison": "==", "bound": 12.0, "tolerance": 1e-4}]
        )

        assert out["ok"] is True


class TestUnmeasuredIsNeverAPass:
    """The rule the whole module exists to carry into the chat."""

    def test_a_claim_about_a_number_that_is_not_there_is_not_a_pass(self) -> None:
        out = _box(FLAT)._check_part(
            [{"name": "wall thickness", "measure": "min_wall_mm", "comparison": ">=",
              "bound": 3.0}]
        )

        assert out["ok"] is False
        assert out["unmeasured"] == 1
        assert out["failed"] == 0, "unmeasured is its own outcome, not a failure"

    def test_it_says_so_in_words_as_well_as_in_the_structure(self) -> None:
        """A model reading this must not be able to mistake it for a pass."""
        out = _box(FLAT)._check_part(
            [{"name": "wall", "measure": "min_wall_mm", "comparison": ">=", "bound": 3.0}]
        )

        assert "not a pass" in out["warning"]

    def test_a_fully_measured_pass_carries_no_warning(self) -> None:
        out = _box(FLAT)._check_part(
            [{"name": "mass", "measure": "mass_kg", "comparison": "<=", "bound": 1.0}]
        )

        assert "warning" not in out
        assert out["ok"] is True


class TestItCatchesTheThingThatWasActuallyWrong:
    """The flange, as it was really built, against the claims it should have made."""

    def test_the_volume_shortfall_is_caught(self) -> None:
        """Rounding every edge instead of four took 4,494 mm3 out of the part and
        reported success. A volume claim sees it immediately."""
        as_built = dict(FLAT, volume_mm3=97208.33)

        out = _box(as_built)._check_part(
            [{"name": "volume matches the design", "measure": "volume_mm3",
              "comparison": "==", "bound": 101701.91, "tolerance": 1.0}]
        )

        assert out["ok"] is False
        assert out["failed"] == 1

    def test_the_failure_names_the_claim_in_the_words_it_was_given(self) -> None:
        as_built = dict(FLAT, volume_mm3=97208.33)

        out = _box(as_built)._check_part(
            [{"name": "volume matches the design", "measure": "volume_mm3",
              "comparison": "==", "bound": 101701.91, "tolerance": 1.0}]
        )

        assert "volume matches the design" in out["summary"]

    def test_a_part_that_is_right_passes(self) -> None:
        out = _box(FLAT)._check_part(
            [
                {"name": "100 mm wide", "measure": "bounding_box_mm.size[0]",
                 "comparison": "==", "bound": 100.0, "tolerance": 1e-4},
                {"name": "12 mm thick", "measure": "bounding_box_mm.size[2]",
                 "comparison": "==", "bound": 12.0, "tolerance": 1e-4},
                {"name": "one solid", "measure": "solid_count", "comparison": "==",
                 "bound": 1, "tolerance": 0.5},
                {"name": "under 2 kg", "measure": "mass_kg", "comparison": "<=",
                 "bound": 2.0},
            ]
        )

        assert out["ok"] is True
        assert out["passed"] == 4


class TestItRefusesWhatItCannotCheck:
    def test_no_claims_is_refused_rather_than_reported_green(self) -> None:
        """An empty suite passing vacuously is the worst possible answer."""
        with pytest.raises(ToolError, match="at least one claim"):
            _box(FLAT)._check_part([])

    def test_an_exact_equality_with_no_tolerance_is_refused_by_name(self) -> None:
        """`assertions.py`'s own rule, surfaced rather than restated: a kernel
        does not return round decimals."""
        with pytest.raises(ToolError, match="Claim 1"):
            _box(FLAT)._check_part(
                [{"name": "mass", "measure": "mass_kg", "comparison": "==", "bound": 0.2}]
            )

    def test_the_refusal_says_which_claim_is_wrong(self) -> None:
        with pytest.raises(ToolError, match="Claim 2"):
            _box(FLAT)._check_part(
                [
                    {"name": "ok", "measure": "mass_kg", "comparison": "<=", "bound": 1.0},
                    {"name": "bad", "measure": "mass_kg", "comparison": "~=", "bound": 1.0},
                ]
            )

    def test_a_measurement_that_fails_is_not_swallowed(self) -> None:
        """No part, no check. Reporting green because nothing could be measured
        is the failure this tool exists to prevent, one level up."""
        with pytest.raises(ToolError):
            _box(ToolError("No document is open"))._check_part(
                [{"name": "mass", "measure": "mass_kg", "comparison": "<=", "bound": 1.0}]
            )

    def test_a_measurement_of_the_wrong_shape_says_what_to_do(self) -> None:
        with pytest.raises(ToolError, match="catia_measure"):
            _box("not a payload")._check_part(
                [{"name": "mass", "measure": "mass_kg", "comparison": "<=", "bound": 1.0}]
            )


class TestItIsWiredIntoTheAgent:
    def test_the_tool_is_registered_and_labelled(self) -> None:
        from app.ai.tools import BUILTIN_TOOL_LABELS, tool_label

        assert "check_part" in BUILTIN_TOOL_LABELS
        assert tool_label("check_part") == "Checking the part against the request"

    def test_it_is_offered_whatever_the_query_says(self) -> None:
        """Retrieval must never withhold the check — a part nobody verified is
        exactly what 16.1's narrowing could otherwise cause."""
        from app.ai.tool_retrieval import CORE_TOOLS

        assert "check_part" in CORE_TOOLS

    def test_the_prompt_tells_the_model_to_finish_by_checking(self) -> None:
        from app.ai.prompts import AGENT_SYSTEM_CATIA

        assert "check_part" in AGENT_SYSTEM_CATIA
        assert "UNMEASURED" in AGENT_SYSTEM_CATIA
