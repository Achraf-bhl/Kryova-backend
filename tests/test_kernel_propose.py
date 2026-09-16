"""Naming a picked face — master plan P6.6, the 3D → name direction.

`resolve.py` turns a predicate into faces; `propose.py` turns a face back into a
predicate, so a click on a triangle in the viewer can become the `faces:` argument of the
next operation. These tests hold it to three claims:

* the triangle → face partition is **right**, checked by geometry rather than by trusting
  the loop that built it;
* a proposed name is **verified**, so it selects the picked face and nothing else;
* a face nothing describes says so, rather than offering a name that means two faces.

Every number here was measured against the real kernel on 2026-09-16 with a one-off
script before it was written down, and the partition was mutation-tested: shifting the
ordinal by one makes `test_each_faces_triangles_reconstruct_that_faces_area` fail with
face 0 reporting the bore's 752.1 mm² instead of its own 800.0. Note what that means —
the *geometric* check catches a permutation and the bookkeeping one does not, because a
permuted partition still covers exactly {0..6}.

**One thing here is unpinned and is labelled rather than claimed.** `tessellate` looks a
face's ordinal up in the de-duplicated map instead of counting it off its own walk, which
matters only where one face has two parents. No shape tried on 2026-09-16 produces that
(see the comment in `tessellate.py` for the five that were tried), so nothing below fails
when the lookup is replaced by a counter. It is insurance, not a verified guard.

Offline: no seat, no database.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.kernel import available

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


def _plate_with_one_bore():
    """A 60×40×20 plate with one Ø12 bore through it: 7 faces, the shape CLAUDE.md quotes."""
    from app.kernel.occt.binding import symbol

    box = symbol("BRepPrimAPI_MakeBox")(60.0, 40.0, 20.0).Shape()
    axis = symbol("gp_Ax2")(
        symbol("gp_Pnt")(30.0, 20.0, -1.0), symbol("gp_Dir")(0.0, 0.0, 1.0)
    )
    bore = symbol("BRepPrimAPI_MakeCylinder")(axis, 6.0, 30.0).Shape()
    return symbol("BRepAlgoAPI_Cut")(box, bore).Shape()


def _plate_with_two_identical_bores():
    """The same plate with two Ø12 bores — the genuinely ambiguous case."""
    from app.kernel.occt.binding import symbol

    shape = symbol("BRepPrimAPI_MakeBox")(60.0, 40.0, 20.0).Shape()
    for x in (15.0, 45.0):
        axis = symbol("gp_Ax2")(
            symbol("gp_Pnt")(x, 20.0, -1.0), symbol("gp_Dir")(0.0, 0.0, 1.0)
        )
        cylinder = symbol("BRepPrimAPI_MakeCylinder")(axis, 6.0, 30.0).Shape()
        shape = symbol("BRepAlgoAPI_Cut")(shape, cylinder).Shape()
    return shape


class TestTheDisplayMeshKnowsWhichFaceEachTriangleCameFrom:
    def test_the_partition_covers_every_triangle_and_no_more(self) -> None:
        from app.kernel.occt.tessellate import tessellate

        mesh = tessellate(_plate_with_one_bore(), linear_deflection_mm=0.1)

        assert mesh.face_of_triangle is not None
        assert mesh.face_of_triangle.shape == (mesh.triangle_count,)
        assert mesh.face_count == 7

    def test_every_face_ordinal_is_the_one_resolve_uses(self) -> None:
        """The partition indexes `topology.faces()`, not the oriented walk.

        The two agree on a solid and come apart where a face has two parents. Keying on
        the map is what makes a pick and a predicate talk about the same face.
        """
        from app.kernel.occt.tessellate import tessellate
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        mesh = tessellate(shape, linear_deflection_mm=0.1)

        assert mesh.face_count == len(faces(shape))
        assert set(np.unique(mesh.face_of_triangle)) == set(range(len(faces(shape))))

    def test_each_faces_triangles_reconstruct_that_faces_area(self) -> None:
        """Geometry, not bookkeeping: a mislabelled triangle changes two areas.

        The planar faces come back exact; the bore is short by the chord error of a
        0.1 mm deflection, which is why the tolerance is relative and 2%.
        """
        from app.kernel.occt import classify
        from app.kernel.occt.tessellate import tessellate
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        mesh = tessellate(shape, linear_deflection_mm=0.1)

        for ordinal, face in enumerate(faces(shape)):
            chosen = mesh.indices[mesh.face_of_triangle == ordinal]
            a = mesh.positions[chosen[:, 0]]
            b = mesh.positions[chosen[:, 1]]
            c = mesh.positions[chosen[:, 2]]
            area = float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0)
            expected = classify.face_area_mm2(face)
            assert area == pytest.approx(expected, rel=0.02), f"face {ordinal}"

    def test_a_mesh_nobody_tessellated_refuses_a_pick_rather_than_guessing(self) -> None:
        from app.kernel.errors import KernelError
        from app.kernel.occt.tessellate import TriangleMesh

        hand_built = TriangleMesh(np.zeros((3, 3)), np.array([[0, 1, 2]]), 0.1, 0.5)

        assert hand_built.face_count == 0
        with pytest.raises(KernelError, match="no face partition"):
            hand_built.face_of(0)

    def test_a_triangle_that_is_not_in_the_mesh_is_refused_by_number(self) -> None:
        from app.kernel.errors import KernelError
        from app.kernel.occt.tessellate import tessellate

        mesh = tessellate(_plate_with_one_bore(), linear_deflection_mm=0.1)

        with pytest.raises(KernelError, match="not in this mesh"):
            mesh.face_of(mesh.triangle_count)


class TestAProposedNameSelectsTheFaceItNames:
    def test_every_face_of_a_bored_plate_gets_a_name_that_is_its_own(self) -> None:
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.resolve import resolve
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()

        for ordinal, face in enumerate(faces(shape)):
            proposal = propose_face(shape, ordinal)
            best = proposal.best
            assert best is not None, f"face {ordinal} got no durable name"
            matched = resolve(shape, best.predicate)
            assert len(matched) == 1
            assert matched[0].IsSame(face)

    def test_the_top_face_is_called_the_top_face(self) -> None:
        """The name offered is the one a person would have written."""
        from app.kernel.occt import classify
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        top = next(
            i
            for i, f in enumerate(faces(shape))
            if classify.face_normal(f) is not None
            and classify.face_normal(f)[2] > 0.99
        )

        best = propose_face(shape, top).best

        assert best is not None
        assert best.as_argument() == {
            "type": "face",
            "axis": "z",
            "side": "max",
            "normal": "+z",
        }

    def test_a_bore_is_named_by_its_diameter_and_never_by_a_direction(self) -> None:
        """A cylinder's centre-parameter normal is where the seam is, not where it faces.

        The bore of this plate reports (1, 0, 0), so `normal: "+x"` catches it alongside
        the +x wall. Offering that as a *name* would be a name about the seam, and on a
        part with no +x wall it would verify as unique and be believed.
        """
        from app.kernel.occt import classify
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        bore = next(
            i
            for i, f in enumerate(faces(shape))
            if classify.face_surface_type(f) == "Cylinder"
        )

        proposal = propose_face(shape, bore)

        assert proposal.best is not None
        assert proposal.best.as_argument() == {
            "type": "face",
            "cylindrical": True,
            "diameter_mm": 12.0,
        }
        assert all("normal" not in p.as_argument() for p in proposal.offered)

    def test_the_pick_goes_from_a_triangle_all_the_way_to_a_name(self) -> None:
        from app.kernel.occt.propose import propose_for_triangle
        from app.kernel.occt.tessellate import tessellate

        shape = _plate_with_one_bore()
        mesh = tessellate(shape, linear_deflection_mm=0.1)

        for triangle in range(0, mesh.triangle_count, 7):
            proposal = propose_for_triangle(shape, mesh, triangle)
            assert proposal.face_index == mesh.face_of(triangle)
            assert proposal.best is not None

    def test_a_face_that_is_not_on_the_shape_is_refused_with_the_count(self) -> None:
        from app.kernel.errors import GeometryError
        from app.kernel.occt.propose import propose_face

        with pytest.raises(GeometryError, match="which has 7 faces"):
            propose_face(_plate_with_one_bore(), 7)


class TestAFaceNothingDescribesSaysSo:
    def test_one_of_two_identical_bores_gets_no_durable_name(self) -> None:
        """Two Ø12 bores are the same shape. No predicate in this vocabulary separates
        them, and the honest answer is to say so rather than offer a name meaning both.
        """
        from app.kernel.occt import classify
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_two_identical_bores()
        bores = [
            i
            for i, f in enumerate(faces(shape))
            if classify.face_surface_type(f) == "Cylinder"
        ]
        assert len(bores) == 2

        for ordinal in bores:
            proposal = propose_face(shape, ordinal)
            assert proposal.best is None
            assert proposal.positional is not None
            assert not proposal.positional.stable
            assert any(p.matches == 2 for p in proposal.ambiguous)

    def test_the_sentence_says_which_description_was_shared_and_with_how_many(
        self,
    ) -> None:
        from app.kernel.occt import classify
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_two_identical_bores()
        bore = next(
            i
            for i, f in enumerate(faces(shape))
            if classify.face_surface_type(f) == "Cylinder"
        )

        sentence = propose_face(shape, bore).describe()

        assert "only by where it sits" in sentence
        assert "selects 2 faces" in sentence
        assert "diameter 12.0 mm" in sentence

    def test_a_positional_name_is_never_returned_as_best(self) -> None:
        """The whole reason `best` and `positional` are separate properties.

        A box always resolves uniquely, so a `best` that accepted one would never be
        `None` and the ambiguity would never surface.
        """
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        for shape in (_plate_with_one_bore(), _plate_with_two_identical_bores()):
            for ordinal in range(len(faces(shape))):
                proposal = propose_face(shape, ordinal)
                if proposal.best is not None:
                    assert proposal.best.stable
                    assert proposal.best.predicate.inside is None


class TestTheOrderingPutsTheHumanNameFirst:
    def test_a_durable_name_outranks_a_narrower_one(self) -> None:
        """`facing +z` should be offered before an area bracket that also works."""
        from app.kernel.occt import classify
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        top = next(
            i
            for i, f in enumerate(faces(shape))
            if classify.face_normal(f) is not None
            and classify.face_normal(f)[2] > 0.99
        )

        offered = propose_face(shape, top).offered
        with_area = next(
            i for i, p in enumerate(offered) if p.predicate.larger_than_mm2 is not None
        )
        plain = next(
            i
            for i, p in enumerate(offered)
            if p.predicate.normal == "+z" and p.predicate.larger_than_mm2 is None
        )

        assert plain < with_area

    def test_the_box_is_offered_last(self) -> None:
        from app.kernel.occt.propose import propose_face
        from app.kernel.occt.topology import faces

        shape = _plate_with_one_bore()
        for ordinal in range(len(faces(shape))):
            offered = propose_face(shape, ordinal).offered
            boxed = [i for i, p in enumerate(offered) if p.predicate.inside is not None]
            if boxed:
                assert boxed == [len(offered) - 1]
