"""A cut reports how deep it went, so a later claim can be checked against it.

Measured on the seat, 2026-09-06, ladder prompt S5 turn 2 -- "add a 60 x 60
boss 25 mm tall in the centre, with a 20 mm diameter bore through the whole
thing". The agent cut a 25 mm pocket, which is the boss and not the 20 mm plate
under it, and then wrote:

    Central bore: 20 mm diameter hole through the entire boss and base plate
    All requirements met.

The measured volume said otherwise. 377,057 mm3 is exactly the plate, less its
four Ø9 holes, plus the boss, less a Ø20 bore **25 mm deep**; a real through
bore would have left 370,773. `through_all` exists on `catia_pocket` and would
have done it -- the agent passed a depth instead.

The tool cannot stop a model choosing a depth. What it can stop is that choice
being invisible: the result said `Done`, so nothing in the transcript the model
re-reads contradicted the sentence it then wrote, and nothing in the flow list
the user reads did either. The result now says whether the cut was blind or
through, how deep, and how much material went.

Offline: no CATIA, no model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.catia_com import CatiaCom  # noqa: E402

report = CatiaCom._cut_report


class TestWhatACutSaysAboutItself:
    def test_a_blind_cut_says_blind_and_how_deep(self) -> None:
        payload = report(False, 25.0, 400_000.0, 392_146.0)
        assert payload["through"] is False
        assert payload["depth_mm"] == 25.0
        assert "blind to 25 mm" in payload["cut"]

    def test_a_blind_cut_says_it_does_not_break_out(self) -> None:
        """The exact thing the agent claimed and the geometry denied."""
        assert "does not break out" in report(False, 25.0, 1.0, 0.5)["cut"]

    def test_a_through_cut_says_through(self) -> None:
        payload = report(True, None, 400_000.0, 385_863.0)
        assert payload["through"] is True
        assert payload["cut"] == "through the material"

    def test_a_through_cut_carries_no_depth(self) -> None:
        """A depth alongside `through` reads as a limit that was applied, and
        it was not -- CATIA's up-to-last ignores it."""
        assert "depth_mm" not in report(True, 25.0, 1.0, 0.5)

    def test_it_reports_the_material_removed(self) -> None:
        assert report(False, 25.0, 400_000.0, 392_146.0)["removed_mm3"] == pytest.approx(7854.0)

    def test_an_unmeasurable_part_omits_the_volume_rather_than_guessing(self) -> None:
        payload = report(False, 25.0, None, None)
        assert "removed_mm3" not in payload
        assert payload["through"] is False


class TestTheS5NumbersItWouldHaveShown:
    """The arithmetic that caught the defect, kept as the regression."""

    PLATE = 150 * 100 * 20
    BOSS = 60 * 60 * 25

    def test_a_25_mm_bore_removes_what_was_measured(self) -> None:
        import math

        removed = math.pi * 10**2 * 25
        assert removed == pytest.approx(7853.98, abs=0.01)
        holes = 4 * math.pi * 4.5**2 * 20
        assert self.PLATE - holes + self.BOSS - removed == pytest.approx(377_057, abs=1)

    def test_a_through_bore_would_have_removed_more(self) -> None:
        import math

        holes = 4 * math.pi * 4.5**2 * 20
        through = math.pi * 10**2 * 45
        assert self.PLATE - holes + self.BOSS - through == pytest.approx(370_773, abs=1)


class TestItReachesBothReaders:
    def test_the_pocket_result_carries_it(self) -> None:
        """A report the tool does not return is a report nobody reads."""
        import inspect

        source = inspect.getsource(CatiaCom.pocket)
        assert "_cut_report(" in source

    def test_the_flow_list_shows_it(self) -> None:
        from app.ai.agent import _catia_summary

        line = _catia_summary({"feature": "Poche.1", "cut": "blind to 25 mm -- it does not break out"})
        assert "Poche.1" in line
        assert "blind to 25 mm" in line

    def test_a_result_without_a_cut_is_unchanged(self) -> None:
        from app.ai.agent import _catia_summary

        assert _catia_summary({"feature": "Extrusion.1", "mass_kg": 2.4}) == "Extrusion.1, 2.4 kg"
