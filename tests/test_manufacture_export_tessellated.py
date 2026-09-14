"""Tessellated STEP, master plan 21.3 — measured, not assumed.

Kept out of `test_manufacture_export.py` on purpose: that file's own first line
gates the whole module on `ezdxf`, which is not installed here (CLAUDE.md's own
landmine list says so), so every STEP assertion in it is silently skipped along
with the DXF ones. STEP does not depend on `ezdxf` at all, and a tessellation
capability with no test that actually runs would be exactly the "green on a
suite nobody ran" failure this codebase warns about elsewhere.

See `app/manufacture/export.py`'s module docstring for what was measured about
`write.step.tessellated` and `read.step.tessellated` on this OCCT build, and
what was deliberately left unmeasured (`ON_NO_BREP` on a shape with no BRep).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.kernel.occt.metrology import volume_mm3
from app.kernel.occt.topology import FACE, explore
from app.manufacture.errors import ExportError
from app.manufacture.export import (
    ROUND_TRIP_TOLERANCE,
    TessellatedWrite,
    read_step,
    write_step,
)
from tests.test_manufacture_dimensions import built


class TestTheTessellatedRepresentationIsAdditional:
    """Two findings, pinned separately because they disagree — see
    `app/manufacture/export.py`'s module docstring. `On` measurably attaches a
    tessellation on an OCCT primitive; the identical setting, on this
    codebase's own multi-feature `bracket()` fixture, does not. Both are
    measured, neither is assumed, and the gap between them is the residual."""

    def test_on_adds_a_tessellated_solid_to_a_primitive(
        self, tmp_path: Path
    ) -> None:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

        box = BRepPrimAPI_MakeBox(50.0, 30.0, 20.0).Shape()
        record = write_step(
            box, tmp_path / "box.step", tessellated=TessellatedWrite.ON
        )
        text = record.path.read_text(encoding="utf-8", errors="replace")

        assert "TESSELLATED_SOLID" in text
        assert "ADVANCED_FACE" in text

    def test_on_writes_no_tessellated_solid_for_the_bracket_fixture(
        self, tmp_path: Path
    ) -> None:
        """Not the desired behaviour — the desired behaviour is the primitive
        case above, on every shape. Pinned so a future OCCT that starts
        attaching a tessellation to this shape is a change this test notices
        rather than one nobody checked for."""
        record = write_step(
            built().shape,
            tmp_path / "bracket.step",
            tessellated=TessellatedWrite.ON,
        )
        text = record.path.read_text(encoding="utf-8", errors="replace")

        assert "TESSELLATED_SOLID" not in text
        assert "ADVANCED_FACE" in text

    def test_off_is_the_default_and_writes_no_tessellation(
        self, tmp_path: Path
    ) -> None:
        record = write_step(built().shape, tmp_path / "bracket.step")
        text = record.path.read_text(encoding="utf-8", errors="replace")

        assert "TESSELLATED_SOLID" not in text
        assert "ADVANCED_FACE" in text

    def test_on_no_brep_measures_as_a_no_op_on_a_kryova_solid(
        self, tmp_path: Path
    ) -> None:
        """Pinned so a future OCCT that changes this behaviour is a failing
        test here rather than a silently different file. See the module
        docstring: `ON_NO_BREP` only suppresses BRep on a shape that has none,
        and every shape this codebase's own kernel produces has one."""
        off = write_step(
            built().shape,
            tmp_path / "off.step",
            tessellated=TessellatedWrite.OFF,
        )
        no_brep = write_step(
            built().shape,
            tmp_path / "no_brep.step",
            tessellated=TessellatedWrite.ON_NO_BREP,
        )

        off_text = off.path.read_text(encoding="utf-8", errors="replace")
        no_brep_text = no_brep.path.read_text(encoding="utf-8", errors="replace")

        assert "TESSELLATED_SOLID" not in off_text
        assert "TESSELLATED_SOLID" not in no_brep_text
        assert "ADVANCED_FACE" in no_brep_text


class TestATessellatedFileStillRoundTripsAsBrep:
    def test_the_geometry_reads_back_unchanged(self, tmp_path: Path) -> None:
        """The BRep is the thing this codebase's own solver and drawing paths
        consume; adding a tessellation payload alongside it must not perturb
        the geometry a plain reader recovers."""
        shape = built().shape
        record = write_step(
            shape, tmp_path / "bracket.step", tessellated=TessellatedWrite.ON
        )

        back = read_step(record.path)

        assert volume_mm3(back) == pytest.approx(
            volume_mm3(shape), rel=ROUND_TRIP_TOLERANCE
        )
        assert len(list(explore(back, FACE))) == len(list(explore(shape, FACE)))

    def test_reading_a_tessellated_file_with_tessellated_off_still_recovers_the_brep(
        self, tmp_path: Path
    ) -> None:
        """`read_step`'s own default is permissive (`ON`); a caller that asks
        for `OFF` on read is asking OCCT to ignore the tessellation entities,
        which must not stop it recovering the BRep that is still in the file."""
        shape = built().shape
        record = write_step(
            shape, tmp_path / "bracket.step", tessellated=TessellatedWrite.ON
        )

        back = read_step(record.path, tessellated=TessellatedWrite.OFF)

        assert volume_mm3(back) == pytest.approx(
            volume_mm3(shape), rel=ROUND_TRIP_TOLERANCE
        )


class TestExportingLeavesThePartAlone:
    """Found 2026-09-14 by the full suite, not by this file: `write_step` with
    `ON` meshed the shape it was handed, the shape was `built()`'s cached
    bracket, and `tests/test_manufacture_sheet.py` then drew a 120 mm edge as
    `120.207` — the bounding box read the triangulation instead of the
    surfaces. It passed alone and failed only after this file had run."""

    def test_the_callers_shape_carries_no_triangulation_afterwards(
        self, tmp_path: Path
    ) -> None:
        from OCP.BRep import BRep_Tool
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS

        shape = BRepPrimAPI_MakeBox(20.0, 10.0, 5.0).Shape()

        record = write_step(shape, tmp_path / "box.step", tessellated=TessellatedWrite.ON)

        assert "TESSELLATED_SOLID" in record.path.read_text(encoding="utf-8")
        for face in explore(shape, FACE):
            triangulation = BRep_Tool.Triangulation_s(TopoDS.Face_s(face), TopLoc_Location())
            assert triangulation is None, "the export meshed the caller's shape"

    def test_the_bounding_box_is_the_same_before_and_after(self, tmp_path: Path) -> None:
        """The symptom itself, on the bracket that showed it. A fresh build, not
        `built()`: the cached one may already have been meshed by a test before
        this one, and then "before" would be the contaminated extent too."""
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        from tests.test_manufacture_dimensions import Built, bracket

        def extent(shape: object) -> tuple[float, ...]:
            box = Bnd_Box()
            BRepBndLib.Add_s(shape, box)
            return box.Get()

        shape = Built(bracket()).shape
        before = extent(shape)

        write_step(shape, tmp_path / "bracket.step", tessellated=TessellatedWrite.ON)

        assert extent(shape) == before


class TestTheParameterNameIsReal:
    def test_an_unknown_static_parameter_would_be_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pins the refusal path `configure_writer`'s docstring describes for
        `write.step.schema`, exercised here through the tessellation
        parameter instead: a `SetCVal_s` that returns `False` must stop the
        export rather than write a file the manifest misdescribes."""
        import app.manufacture.export as export_module

        real_step = export_module._step

        def _lying_step() -> dict:
            resources = real_step()
            static = resources["static"]

            class _RefusingStatic:
                def SetCVal_s(self, name: str, value: str) -> bool:
                    if name == "write.step.tessellated":
                        return False
                    return bool(static.SetCVal_s(name, value))

            resources = dict(resources)
            resources["static"] = _RefusingStatic()
            return resources

        monkeypatch.setattr(export_module, "_step", _lying_step)

        with pytest.raises(ExportError, match="write.step.tessellated"):
            write_step(
                built().shape,
                tmp_path / "bracket.step",
                tessellated=TessellatedWrite.ON,
            )
