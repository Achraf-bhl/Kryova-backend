"""The daemon's COM surface, checked against CATIA's published IDL.

`tests/test_catia.py` covers the *server's* COM path (`app/catia/bridge.py`) and
`tests/test_catia_e2e.py` drives the daemon through `MockCatia`. Neither one
touches `scripts/catia_bridge/catia_com.py`, the file that actually talks to
CATIA on a workstation -- every method in it is marked
`# pragma: no cover - Windows only`, and the mock is a separate implementation
that agrees with whatever the real one was *believed* to do.

That gap hid four bugs that a live V5-6R2023 found in minutes, all of the same
shape: a call written against an API CATIA does not have, or a unit it does not
use. The IDL reference is in `docs/catiadoc_interfaces_CONTENT.txt`.

These tests drive the real methods against a stub COM object, so they run on any
OS with no CATIA. A stub cannot prove CATIA accepts a call -- only a live
session does that -- but it does pin the things that were wrong: the format
token, the units, the version string, and the file collision.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402


class _Analyze:
    """`Product.Analyze`, which reports millimetre units (and kg for Mass).

    Values are a 100 mm cube of the default CATIA material, copied from a live
    V5-6R2023 session: volume 1e6 mm3, wet area 6e4 mm2, mass 1.0 kg.
    """

    Volume = 1_000_000.0
    WetArea = 60_000.0
    Mass = 1.0


class _Product:
    Analyze = _Analyze()


#: The solid the stub pretends to hold: 120 x 80 x 10, sitting on z = 0.
STUB_BOX = (-60.0, -40.0, 0.0, 60.0, 40.0, 10.0)


class _OffsetPlane:
    """A measuring plane, remembering which side of which axis it was put on."""

    def __init__(self, axis: int, sign: int) -> None:
        self.axis = axis
        self.sign = sign


class _OriginElements:
    # `AddNewPlaneOffset` is given one of these; the axis index is what the
    # stub needs to answer a distance, so the planes are just their axis.
    PlaneYZ = 0
    PlaneZX = 1
    PlaneXY = 2


class _HybridShapeFactory:
    def AddNewPlaneOffset(self, plane: int, offset: float, _orientation: bool) -> _OffsetPlane:
        return _OffsetPlane(plane, 1 if offset > 0 else -1)


class _HybridBody:
    def __init__(self) -> None:
        self.appended: list[object] = []
        self.deleted = False

    def AppendHybridShape(self, shape: object) -> None:
        self.appended.append(shape)


class _Measurable:
    """Answers `GetMinimumDistance` as a real CATIA would for `STUB_BOX`."""

    def GetMinimumDistance(self, plane: _OffsetPlane) -> float:
        from catia_bridge.catia_com import _BBOX_REACH

        low = STUB_BOX[plane.axis]
        high = STUB_BOX[plane.axis + 3]
        reach = high if plane.sign > 0 else low
        # The plane sits `_BBOX_REACH` out on that side; the gap is whatever is
        # left once the solid has reached as far as it does.
        return _BBOX_REACH - plane.sign * reach


class _Workbench:
    def GetMeasurable(self, _reference: object) -> _Measurable:
        return _Measurable()


class _PartDocument:
    def GetWorkbench(self, _name: str) -> _Workbench:
        return _Workbench()


class _Part:
    def __init__(self) -> None:
        self.HybridShapeFactory = _HybridShapeFactory()
        self.OriginElements = _OriginElements()
        self.Parent = _PartDocument()
        self.MainBody = object()
        self.bodies: list[_HybridBody] = []
        self.HybridBodies = self

    def Add(self) -> _HybridBody:
        body = _HybridBody()
        self.bodies.append(body)
        return body

    def CreateReferenceFromObject(self, obj: object) -> object:
        return obj

    def Update(self) -> None:
        return None


class _Selection:
    def __init__(self, document: "_Document") -> None:
        self._document = document
        self._staged: list[object] = []

    def Clear(self) -> None:
        self._staged.clear()

    def Add(self, item: object) -> None:
        self._staged.append(item)

    def Delete(self) -> None:
        for item in self._staged:
            if isinstance(item, _HybridBody):
                item.deleted = True
            self._document.deleted.append(item)
        self._staged.clear()


class _Document:
    def __init__(self) -> None:
        self.Product = _Product()
        self.Part = _Part()
        self.Name = "Bracket.CATPart"
        self.exported: list[tuple[str, str]] = []
        self.saved_as: list[str] = []
        self.deleted: list[object] = []
        self.Selection = _Selection(self)

    def ExportData(self, filename: str, fmt: str) -> None:
        # The real CATIA raises when the token is not a translator name. It does
        # not fall back to STEP, and it does not infer from the extension.
        if fmt not in {"stp", "stl", "igs", "model", "CATPart"}:
            raise RuntimeError("La methode ExportData a echoue")
        self.exported.append((filename, fmt))
        Path(filename).write_bytes(b"ISO-10303-21;\n")

    def SaveAs(self, path: str) -> None:
        if Path(path).exists():
            # What CATIA really does here is put up a modal "Save As" dialog and
            # wait. Raising is the closest a stub can get; the point is that the
            # call must never be reached with a path that exists.
            raise AssertionError(
                "SaveAs was called onto an existing file -- on real CATIA this "
                "opens a modal dialog and wedges the bridge."
            )
        self.saved_as.append(path)
        Path(path).write_bytes(b"CATPart")


class _SystemConfiguration:
    Version = "5"
    Release = "33"


class _SystemService:
    """`Evaluate`, answering only the snippets the daemon is allowed to run.

    Mirrors the real thing closely enough to matter: it returns the semicolon
    string a VBScript helper returns, and it refuses anything it does not
    recognise, so a test cannot pass by accident against a snippet CATIA would
    have rejected.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def Evaluate(self, script: str, language: int, function: str, parameters: list) -> str:
        from catia_bridge import vba

        assert language == vba.VBSCRIPT
        self.calls.append(function)
        if script is vba.CENTRE_OF_GRAVITY:
            # Centre of the stub box: (0, 0, 5) for a 120 x 80 x 10 on z = 0.
            return "0;0;5"
        raise RuntimeError(f"CATIA would refuse the unknown helper {function}")


class _App:
    def __init__(self) -> None:
        self.SystemConfiguration = _SystemConfiguration()
        self.SystemService = _SystemService()
        self.document = _Document()

    @property
    def ActiveDocument(self) -> _Document:
        return self.document


def _com(tmp_path: Path) -> CatiaCom:
    """A `CatiaCom` wired to the stub, without running `_connect`."""
    com = object.__new__(CatiaCom)
    com.workdir = tmp_path
    com.documents = tmp_path / "documents"
    com.snapshots = tmp_path / "snapshots"
    for directory in (com.documents, com.snapshots):
        directory.mkdir(parents=True, exist_ok=True)
    com._app = _App()
    com.catia_version = com._read_version()
    return com


class TestVersionString:
    """`SystemConfiguration.Version` alone is the number 5, and says nothing."""

    def test_version_and_release_are_joined(self, tmp_path: Path) -> None:
        assert _com(tmp_path).catia_version == "V5-R33"

    def test_it_does_not_report_the_bare_version_number(self, tmp_path: Path) -> None:
        # The regression: this reached `GET /catia/status` as
        # `catia_version: "5"`, which the UI then showed to the engineer.
        assert _com(tmp_path).catia_version != "5"


class TestExportFormatToken:
    """`Document.ExportData` takes the translator token, not the format name.

    The IDL reference's own example is `Doc.ExportData("IGESDoc", "igs")`. A
    live V5-6R2023 answers `"step"` with `La methode ExportData a echoue`.
    """

    def test_step_export_uses_the_stp_token(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com.export_step(max_inline_bytes=1024 * 1024)
        assert com._app.document.exported, "nothing was exported"
        _filename, fmt = com._app.document.exported[-1]
        assert fmt == "stp", (
            f"ExportData was called with {fmt!r}; CATIA wants 'stp' and rejects 'step'."
        )

    def test_the_written_file_carries_the_stp_suffix(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com.export_step(max_inline_bytes=1024 * 1024)
        filename, _fmt = com._app.document.exported[-1]
        assert filename.endswith(".stp")

    def test_the_server_side_path_agrees_on_the_token(self) -> None:
        # Two COM implementations ship in this repo. They must not disagree
        # about the one string that decides whether an export happens at all.
        from app.catia.bridge import ExportFormat

        assert ExportFormat.STEP.value == "stp"


class TestMeasurementUnits:
    """Kryova is mm-N-MPa, and `Analyze` is the interface that already is.

    `Measurable.Volume` on the same 100 mm cube returns `0.001` -- cubic metres.
    Reading that and labelling it `volume_mm3` put every mass out by 1e9, which
    rounded to `0.0 kg` and was reported to the user as a measurement.
    """

    def test_volume_is_reported_in_cubic_millimetres(self, tmp_path: Path) -> None:
        assert _com(tmp_path).measure()["volume_mm3"] == pytest.approx(1_000_000.0)

    def test_surface_area_is_reported_in_square_millimetres(self, tmp_path: Path) -> None:
        assert _com(tmp_path).measure()["surface_area_mm2"] == pytest.approx(60_000.0)

    def test_mass_is_kilograms_and_is_not_zero(self, tmp_path: Path) -> None:
        mass = _com(tmp_path).measure()["mass_kg"]
        assert mass == pytest.approx(1.0)
        assert mass > 0.0, "the 1e9 unit error rounded every mass to 0.0 kg"

    def test_mass_comes_from_catia_not_from_an_assumed_density(self, tmp_path: Path) -> None:
        # The replaced code multiplied volume by a density read from
        # `Part.AnalyzeMaterial` -- an interface that does not exist in the IDL
        # -- and so silently used steel for every part. Steel would make this
        # cube 7.85 kg; CATIA says 1.0.
        assert _com(tmp_path).measure()["mass_kg"] != pytest.approx(7.85, rel=1e-3)


class TestConstructedBoundingBox:
    """CATIA V5 has no bounding-box call, so the box is measured, not queried.

    Six planes `_BBOX_REACH` outside the part, and the minimum distance from the
    solid to each. The alternative -- six `AddNewExtremum` points, which is what
    `pycatia`'s bounding-box script does -- is exact on a prismatic solid and
    fails outright on a curved one, because reading an extremum's position needs
    `GetCOG` and CATIA answers `La methode GetCOG a echoue` on the silhouette of
    a cylinder. Bolts and gears are not an edge case.
    """

    def test_the_box_matches_the_solid(self, tmp_path: Path) -> None:
        box = _com(tmp_path).measure()["bounding_box_mm"]
        assert box["min"] == [-60.0, -40.0, 0.0]
        assert box["max"] == [60.0, 40.0, 10.0]
        assert box["size"] == [120.0, 80.0, 10.0]

    def test_a_face_on_an_origin_plane_is_zero_not_negative_zero(self, tmp_path: Path) -> None:
        # The stub box sits on z = 0. `sign * (reach - gap)` produces -0.0 there,
        # which renders as "-0.0" and reads as a bug to anyone checking numbers.
        assert str(_com(tmp_path).measure()["bounding_box_mm"]["min"][2]) == "0.0"

    def test_the_measuring_planes_are_deleted_again(self, tmp_path: Path) -> None:
        # They are construction features in the engineer's own part, and
        # `_measure_solid` runs after every single mutation.
        com = _com(tmp_path)
        com.measure()
        bodies = com._app.document.Part.bodies
        assert bodies, "no temporary geometry set was created"
        assert all(body.deleted for body in bodies), "construction geometry was left behind"

    def test_a_measurement_still_succeeds_when_the_box_cannot_be_built(
        self, tmp_path: Path
    ) -> None:
        # The regression that mattered: `_measure_solid` runs after every
        # mutation, so a raising bounding box turned a pad CATIA had already
        # created into a reported failure -- and the agent's next move is to
        # pad again.
        com = _com(tmp_path)
        com._app.document.Part.HybridShapeFactory = None
        result = com.measure()
        assert result["has_solid"] is True
        assert result["bounding_box_mm"] is None


class TestCentreOfGravity:
    """Read through a frozen VBScript helper, because COM alone cannot."""

    def test_it_is_measured_not_invented(self, tmp_path: Path) -> None:
        # `GetCOG` returns through a SAFEARRAY out-parameter that late binding
        # cannot marshal: pywin32 leaves the list untouched, so the old code
        # reported the [0, 0, 0] it had initialised as a measured value.
        assert _com(tmp_path).measure()["center_of_gravity_mm"] == [0.0, 0.0, 5.0]

    def test_it_goes_through_the_frozen_snippet(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com.measure()
        assert "KryovaCentreOfGravity" in com._app.SystemService.calls

    def test_a_refused_helper_does_not_fail_the_measurement(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com._app.SystemService = None
        result = com.measure()
        assert result["has_solid"] is True
        assert result["center_of_gravity_mm"] is None


class TestFrozenScriptLibrary:
    """`Evaluate` is a code-execution hatch; this is what keeps it shut."""

    def test_a_script_that_is_not_in_the_library_is_refused(self, tmp_path: Path) -> None:
        from catia_bridge import vba

        com = _com(tmp_path)
        with pytest.raises(vba.VbaUnavailable):
            vba.run(com._app, "Function Whatever()\nEnd Function\n", [])

    def test_the_library_is_small_and_named(self) -> None:
        from catia_bridge import vba

        # Every entry is a hand-written constant in that module. If this set
        # grows, someone added a script -- which is exactly when a human should
        # be looking at it. KryovaPointsOnCurve was reviewed in: it measures an
        # edge's start/middle/end so fillet and chamfer can pick edges by
        # meaning, and takes only (part, reference).
        assert set(vba._ALLOWED.values()) == {
            "KryovaCentreOfGravity",
            "KryovaPointsOnCurve",
            "KryovaEdgeMap",
        }

    def test_no_script_interpolates_anything(self) -> None:
        from catia_bridge import vba

        for script in vba._ALLOWED:
            assert "%s" not in script and "{" not in script and "+ " not in script, (
                "a script is being built from parts; arguments must be passed "
                "through Evaluate's parameter array, never into the text"
            )


class TestHolesAreCutNotDrilled:
    """`AddNewHoleFromPoint` needs a face reference this bridge cannot make."""

    def test_the_drilling_call_is_gone(self) -> None:
        # It needs a BRep face reference; the code passed
        # `CreateReferenceFromName("")` and CATIA answered
        # `La methode AddNewHoleFromPoint a echoue` for every hole ever asked for.
        assert "AddNewHoleFromPoint" not in TestNoFabricatedInterfaces._attributes_accessed()

    def test_every_named_face_has_a_sketch_plane(self) -> None:
        from catia_bridge.catia_com import _SKETCH_FRAME
        from catia_bridge.tool_table import NAMED_FACES

        assert set(NAMED_FACES) == set(_SKETCH_FRAME), (
            "a face the schema accepts but that has no sketch plane is a hole "
            "request the daemon will crash on"
        )

    def test_a_hole_needs_a_bounding_box(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com._app.document.Part.HybridShapeFactory = None
        with pytest.raises(CatiaOperationError) as caught:
            com.hole(face="top", position="center", diameter_mm=6.0)
        assert "bounding box" in str(caught.value).lower()


class TestDocumentPathCollision:
    """`SaveAs` onto an existing file opens a modal dialog and wedges CATIA."""

    def test_a_reused_part_name_gets_its_own_file(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        (com.documents / "Bracket.CATPart").write_bytes(b"already here")
        assert com._free_document_path("Bracket").name == "Bracket-2.CATPart"

    def test_it_keeps_counting_past_the_second(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        for name in ("Bracket.CATPart", "Bracket-2.CATPart", "Bracket-3.CATPart"):
            (com.documents / name).write_bytes(b"x")
        assert com._free_document_path("Bracket").name == "Bracket-4.CATPart"

    def test_a_fresh_name_is_left_alone(self, tmp_path: Path) -> None:
        assert _com(tmp_path)._free_document_path("Bracket").name == "Bracket.CATPart"

    def test_new_part_never_saves_over_an_existing_document(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        (com.documents / "Bracket.CATPart").write_bytes(b"the engineer's earlier part")

        class _Documents:
            def Add(self, _kind: str) -> _Document:
                return com._app.document

        com._app.Documents = _Documents()
        com._app.document.Part = type("P", (), {"Update": lambda self: None})()

        # The stub's SaveAs asserts if it is handed a path that exists.
        result = com.new_part(name="Bracket")
        assert result["remote_path"].endswith("Bracket-2.CATPart")
        assert (com.documents / "Bracket.CATPart").read_bytes() == (
            b"the engineer's earlier part"
        ), "the earlier part was overwritten"


class TestNoFabricatedInterfaces:
    """Names that are not in CATIA's IDL must not be *called* by the daemon.

    Checked over the parsed attribute accesses rather than the raw text, so the
    comments explaining why each name is wrong -- which have to name it -- do
    not trip the test they are documenting.
    """

    @staticmethod
    def _attributes_accessed() -> set[str]:
        import ast

        source = (
            Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge" / "catia_com.py"
        ).read_text(encoding="utf-8")
        return {
            node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
        }

    @pytest.mark.parametrize(
        ("name", "why"),
        [
            (
                "AnalyzeMaterial",
                "not a CATIA interface; use Product.Analyze.Mass for mass in kg",
            ),
            (
                "GetBoundingBox",
                "Measurable has no such method; CATIA V5 exposes no bounding box",
            ),
            (
                "GetCOG",
                "a SAFEARRAY out-parameter that late binding cannot marshal; it "
                "silently returns whatever list it was handed",
            ),
        ],
    )
    def test_the_name_is_never_called(self, name: str, why: str) -> None:
        assert name not in self._attributes_accessed(), f"{name} is {why}"


class TestMassIsLabelledWithItsMaterial:
    """A mass computed from no material is not the part's weight.

    Nothing in this bridge applies a material, so CATIA weighs every part it
    builds at its default density of 1000 kg/m3. The stub above is that case:
    1e6 mm3 massing 1.0 kg. For the steel bracket the agent was actually asked
    about, the real mass is 7.9x what CATIA reported -- and observed live, the
    agent answered "how heavy is it?" with "0.095 kg" for a part that weighs
    0.755 kg.

    The number is still reported, because a tool must not invent a different
    one. It is labelled, in terms that cannot be restated as a plain weight.
    """

    def test_the_density_is_reported(self, tmp_path: Path) -> None:
        assert _com(tmp_path).measure()["density_kg_m3"] == 1000.0

    def test_a_part_with_no_material_is_flagged(self, tmp_path: Path) -> None:
        measured = _com(tmp_path).measure()
        assert measured["material_applied"] is False
        assert measured["mass_is_provisional"] is True

    def test_the_warning_says_not_to_quote_it(self, tmp_path: Path) -> None:
        warning = _com(tmp_path).measure()["mass_warning"]
        assert "NOT the real mass" in warning
        assert "Do not quote it" in warning

    def test_the_mass_itself_is_still_reported(self, tmp_path: Path) -> None:
        # Hiding it would make the agent invent one, which is worse.
        assert _com(tmp_path).measure()["mass_kg"] == 1.0

    def test_a_real_material_is_not_flagged(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        # Steel: 1e6 mm3 at 7850 kg/m3 is 7.85 kg.
        original = _Analyze.Mass
        _Analyze.Mass = 7.85
        try:
            measured = com.measure()
            assert measured["material_applied"] is True
            assert measured["mass_is_provisional"] is False
            assert "mass_warning" not in measured
            assert measured["density_kg_m3"] == 7850.0
        finally:
            _Analyze.Mass = original


class TestABoltPatternCanStateItsInset:
    """ "15 mm in from each corner" is how bolt patterns are actually specified.

    Before `inset_mm` there was nowhere to put that number. Observed live, asked
    for "four M8 bolt holes, 15 mm in from each corner", the model had no field
    for the 15 and spent the turn guessing invalid `face` and `position` values
    until it ran out of steps and answered nothing at all. Three of the four
    blank answers in a 66-turn run started exactly that way.
    """

    def test_the_default_is_still_derived_from_the_diameter(self) -> None:
        from catia_bridge.catia_com import _face_point

        # Radius plus half again: an 8 mm hole sits 6 mm in.
        x, y, _ = _face_point(STUB_BOX, "top", "front_left", 8.0)
        assert (x, y) == (STUB_BOX[0] + 6.0, STUB_BOX[1] + 6.0)

    def test_an_explicit_inset_is_honoured(self) -> None:
        from catia_bridge.catia_com import _face_point

        x, y, _ = _face_point(STUB_BOX, "top", "front_left", 8.0, 15.0)
        assert (x, y) == (STUB_BOX[0] + 15.0, STUB_BOX[1] + 15.0)

    def test_it_applies_to_the_far_corner_too(self) -> None:
        from catia_bridge.catia_com import _face_point

        x, y, _ = _face_point(STUB_BOX, "top", "back_right", 8.0, 15.0)
        assert (x, y) == (STUB_BOX[3] - 15.0, STUB_BOX[4] - 15.0)

    def test_the_centre_is_unaffected(self) -> None:
        from catia_bridge.catia_com import _face_point

        assert _face_point(STUB_BOX, "top", "center", 8.0, 15.0) == _face_point(
            STUB_BOX, "top", "center", 8.0
        )

    def test_both_schemas_accept_it(self) -> None:
        # The daemon re-validates against its own copy; if the two disagree the
        # server accepts a call the workstation then refuses.
        from catia_bridge.tool_table import check_call

        from app.catia.tool_specs import TOOL_SPECS_BY_NAME
        from app.catia.validation import validate

        arguments = {
            "face": "top",
            "position": "front_left",
            "diameter_mm": 8.0,
            "inset_mm": 15.0,
        }
        validate(arguments, TOOL_SPECS_BY_NAME["catia_hole"].parameters)
        check_call("catia_hole", arguments, approval_token=None)


class TestAChosenDensityStaysWithItsDocument:
    """A material set on one part must not weigh the next one.

    `CatiaCom` lives as long as the daemon does, so state kept on it outlives
    any single document. The chosen density was kept that way, and followed the
    engineer into every part built afterwards: a brand new 10 mm cube with no
    material reported 2700 kg/m3 inherited from an aluminium plate built in an
    earlier conversation. Worse than the wrong number, the override moved the
    density away from CATIA's 1000 kg/m3 default, which is the very test
    `_measure_solid` uses to decide whether to warn -- so the wrong mass came
    back as authoritative with `mass_is_provisional: false`.
    """

    def test_the_density_applies_to_the_document_it_was_set_on(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com.set_material(material="aluminium-6061-t6", density_kg_m3=2700.0)

        measured = com._measure_solid()
        assert measured["density_kg_m3"] == pytest.approx(2700.0)
        assert measured["material_applied"] is True
        assert measured["mass_is_provisional"] is False

    def test_a_different_document_is_not_weighed_with_it(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com.set_material(material="aluminium-6061-t6", density_kg_m3=2700.0)

        # The engineer moves on to a new part. The stub's solid is unchanged, so
        # any difference here is the leak and nothing else.
        com._app.document = _Document()
        com._app.document.Name = "Something-else.CATPart"

        measured = com._measure_solid()
        assert measured["density_kg_m3"] == pytest.approx(1000.0)
        assert measured["material_applied"] is False
        assert measured["mass_is_provisional"] is True
        assert "NOT the real mass" in measured["mass_warning"]


class TestAPocketThatCutsNothingIsRefused:
    """A pocket aimed away from the solid builds happily and removes nothing.

    `hole` has guarded against this since it was written -- it cuts, looks at
    the volume, flips the direction and looks again. `pocket` did neither, so a
    profile sketched on an origin plane the solid does not straddle produced a
    feature, a success frame and an unchanged part. Measured live: the agent
    pocketed a 4x3 grid of 6.5 mm holes into a 140x100x8 plate and it came back
    at 112000 mm3 -- its full solid volume -- while the reply described the
    holes as cut.
    """

    @staticmethod
    def _com_with_pocket(tmp_path: Path, volumes: list[float]) -> tuple[CatiaCom, dict]:
        com = _com(tmp_path)
        state: dict = {"flipped": False, "updates": 0}

        class _Limit:
            LimitMode = 0

        class _Pocket:
            Name = "Poche.1"
            FirstLimit = _Limit()

            def __setattr__(self, key, value):
                if key == "DirectionOrientation":
                    state["flipped"] = True
                object.__setattr__(self, key, value)

        class _Factory:
            @staticmethod
            def AddNewPocket(_sketch, _depth):
                return _Pocket()

        class _PartWithFactory:
            ShapeFactory = _Factory()

            @staticmethod
            def Update():
                state["updates"] += 1

        com._part = lambda: _PartWithFactory()  # type: ignore[method-assign]
        com._find_sketch = lambda _name: object()  # type: ignore[method-assign]
        com._feature_result = lambda name: {"feature": name}  # type: ignore[method-assign]
        readings = iter(volumes)
        com._solid_volume = lambda: next(readings)  # type: ignore[method-assign]
        return com, state

    def test_it_flips_the_direction_before_giving_up(self, tmp_path: Path) -> None:
        # before=1000, still 1000 after the first cut, 900 once reversed.
        com, state = self._com_with_pocket(tmp_path, [1000.0, 1000.0, 900.0])
        result = com.pocket(sketch="Sketch.1", through_all=True)
        assert result["feature"] == "Poche.1"
        assert state["flipped"], "a pocket that removed nothing must try the other direction"

    def test_it_refuses_when_neither_direction_cuts(self, tmp_path: Path) -> None:
        com, _ = self._com_with_pocket(tmp_path, [1000.0, 1000.0, 1000.0])
        with pytest.raises(CatiaOperationError, match="removed no material"):
            com.pocket(sketch="Sketch.1", through_all=True)

    def test_a_pocket_that_cuts_straight_away_is_left_alone(self, tmp_path: Path) -> None:
        com, state = self._com_with_pocket(tmp_path, [1000.0, 800.0, 800.0])
        com.pocket(sketch="Sketch.1", through_all=True)
        assert not state["flipped"], "a working pocket must not be reversed"


# ---------------------------------------------------------------------------
# Driving the interface
# ---------------------------------------------------------------------------


class TestInterfaceDriving:
    """The COM half of the interactive tools, against the stub.

    The Win32 half needs Windows and a live CATIA and is left to a real session
    -- mocking `user32` would only prove the mock agrees with itself. What is
    checkable here is what these methods do *through COM*: which workbench
    identifier they pass, what they put in the selection, and how they answer
    when the name is not in the part.
    """

    def test_a_known_workbench_goes_through_start_workbench(self, tmp_path: Path) -> None:
        """The one-call path. The Start-menu walk is the fallback, not the norm."""
        com = _com(tmp_path)
        started: list[str] = []
        com._app.StartWorkbench = started.append  # type: ignore[attr-defined]

        result = com.switch_workbench(
            workbench="Part Design",
            workbench_id="PrtCfg",
            workbench_name="Part Design",
            licence="P1 -- Part Design 1 (PD1)",
        )
        assert started == ["PrtCfg"]
        assert result["reached_by"] == "identifier"
        # The licence rides along because a seat without it cannot open the
        # workbench however the menu looks, and that is worth saying once
        # rather than retrying.
        assert "PD1" in result["licence"]

    def test_selecting_by_name_adds_the_element_catia_found(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        sketch = object()
        com._part = lambda: object()  # type: ignore[method-assign]
        com._document = lambda: com._app.ActiveDocument  # type: ignore[method-assign]
        import catia_bridge.catia_com as module

        original = module._find_named
        module._find_named = lambda _part, name: sketch if name == "Sketch.1" else None
        try:
            result = com.select(features=["Sketch.1"])
        finally:
            module._find_named = original
        assert result["selected"] == ["Sketch.1"]

    def test_a_name_that_is_not_in_the_part_names_the_tool_that_lists_them(
        self, tmp_path: Path
    ) -> None:
        com = _com(tmp_path)
        com._part = lambda: object()  # type: ignore[method-assign]
        com._document = lambda: com._app.ActiveDocument  # type: ignore[method-assign]
        import catia_bridge.catia_com as module

        original = module._find_named
        module._find_named = lambda _part, _name: None
        try:
            with pytest.raises(CatiaOperationError, match="catia_list_features"):
                com.select(features=["Sketch.9"])
        finally:
            module._find_named = original

    def test_an_empty_selection_clears_rather_than_failing(self, tmp_path: Path) -> None:
        com = _com(tmp_path)
        com._document = lambda: com._app.ActiveDocument  # type: ignore[method-assign]
        assert com.select(features=[])["count"] == 0


class TestDialogFieldMatching:
    """`_match_control` is how a label the agent types finds the real box."""

    @staticmethod
    def _dialog():
        from catia_bridge.ui_automation import Control, Dialog

        return Dialog(
            title="Pad Definition",
            handle=1,
            controls=(
                Control(kind="text", label="Length", value="10mm", handle=2),
                Control(kind="choice", label="Type", options=("Dimension",), handle=3),
                Control(kind="button", label="OK", control_id=1, handle=4),
            ),
        )

    def test_an_exact_label_matches(self) -> None:
        from catia_bridge.catia_com import _match_control

        assert _match_control(self._dialog(), "Length").handle == 2

    def test_case_and_accents_do_not_matter(self) -> None:
        """The agent echoes a label back; it should not have to echo it byte for byte."""
        from catia_bridge.catia_com import _match_control

        assert _match_control(self._dialog(), "length").handle == 2

    def test_a_trailing_colon_in_the_dialog_is_tolerated(self) -> None:
        from catia_bridge.catia_com import _match_control

        assert _match_control(self._dialog(), "Len").handle == 2

    def test_an_unknown_field_lists_the_real_ones(self) -> None:
        from catia_bridge.catia_com import _match_control

        with pytest.raises(CatiaOperationError, match="Length"):
            _match_control(self._dialog(), "Thickness")

    def test_a_button_is_not_offered_as_a_field(self) -> None:
        """Filling `OK` with a number is a mistake worth refusing outright."""
        from catia_bridge.catia_com import _match_control

        with pytest.raises(CatiaOperationError):
            _match_control(self._dialog(), "OK")


class _ProductAnalyze:
    Volume = 1_000_000.0
    Mass = 0.0


class _EmptyProducts:
    Count = 0

    def Item(self, index: int):  # pragma: no cover - nothing to index, by design
        raise IndexError(index)


class _ProductRoot:
    PartNumber = "Stage"

    def __init__(self) -> None:
        self.Analyze = _ProductAnalyze()
        self.Products = _EmptyProducts()

    def Update(self) -> None:
        return None


class _ProductDocument:
    """A CATProduct, distinguished from a part by raising on `.Part`."""

    Name = "Stage.CATProduct"

    def __init__(self) -> None:
        self.Product = _ProductRoot()

    def __getattr__(self, item: str):
        if item == "Part":
            raise AttributeError("a product has no Part")
        raise AttributeError(item)


def _com_for_document(tmp_path: Path, document) -> CatiaCom:
    com = object.__new__(CatiaCom)
    com.workdir = tmp_path
    com._document = lambda: document  # type: ignore[method-assign]
    return com


class TestUpdateOnAProduct:
    """`catia_update` dispatches to a product's `Analyze` rather than a part's
    solid, the same distinction `_is_part_document` draws everywhere else."""

    def test_it_resolves_and_reports_mass_in_mm_units(self, tmp_path: Path) -> None:
        """`Analyze` reports mm3 and kg; scaling it would repeat the 1e9 bug."""
        document = _ProductDocument()
        document.Product.Analyze.Mass = 7.86
        com = _com_for_document(tmp_path, document)

        result = com.update()

        assert result["updated"] is True
        assert result["volume_mm3"] == 1_000_000.0
        assert result["mass_kg"] == 7.86
        assert result["mass_is_provisional"] is False

    def test_an_assembly_with_no_materials_reports_no_mass_rather_than_zero(
        self, tmp_path: Path
    ) -> None:
        document = _ProductDocument()
        com = _com_for_document(tmp_path, document)

        result = com.update()

        assert result["mass_kg"] is None
        assert result["mass_is_provisional"] is True
        assert "material" in result["mass_warning"]
