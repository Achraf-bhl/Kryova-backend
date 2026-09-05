"""The pre-solve restraint check.

Offline: no database, no gmsh, no solver. Every case is a structured box mesh and
a set of fixtures, and every expected answer is derived from the mechanics rather
than recorded from a run — a block resting on a frictionless table can slide in
two directions and spin about the normal, and that is three surviving motions
whatever this code says.

Two of these tests pin a *reported number* that contradicts the brief this module
was written from, which asked for five surviving motions from a single roller
plane. Five is what a single held *point* leaves; a whole face held out of plane
leaves three, because holding a face's z also removes rocking about x and y. Both
cases are pinned below, under their own names, so the distinction cannot be lost
again.
"""

import json

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import TetMesh
from app.solve.constraints import (
    MODE_NAMES,
    RIGID_BODY_MODES,
    ConstraintReport,
    check_restraints,
    held_dofs,
    require_restrained,
    rigid_body_modes,
)
from app.solve.types import (
    BoxSelector,
    FaceSelector,
    Fixture,
    SolverError,
    SphereSelector,
)

SIZE = (60.0, 40.0, 20.0)


def block() -> TetMesh:
    return box_mesh(SIZE, divisions=(3, 2, 2))


def clamp_bottom() -> Fixture:
    return Fixture(where=FaceSelector(axis="z", side="min"), kind="clamp")


def roller_bottom() -> Fixture:
    return Fixture(where=FaceSelector(axis="z", side="min"), kind="roller", normal="z")


def free_names(report: ConstraintReport) -> set[str]:
    return {motion.name for motion in report.free_outright}


# -- the mathematics itself --------------------------------------------------


class TestRigidBodyModes:
    def test_there_are_six_and_they_are_independent(self) -> None:
        modes = rigid_body_modes(block().nodes)
        assert modes.shape == (3 * block().node_count, RIGID_BODY_MODES)
        assert np.linalg.matrix_rank(modes) == RIGID_BODY_MODES

    def test_rotations_are_taken_about_the_centroid(self) -> None:
        """Which is what makes translations orthogonal to rotations.

        sum_i e_a . (e_b x r_i) = e_a . (e_b x sum_i r_i), and sum_i r_i is zero
        by the definition of the centroid. Break the centring and this is the
        first thing that stops being true.
        """
        modes = rigid_body_modes(block().nodes)
        gram = modes.T @ modes
        translations_by_rotations = gram[:3, 3:]
        assert np.abs(translations_by_rotations).max() < 1e-8 * np.abs(gram).max()

    def test_a_rigid_motion_does_not_strain_the_body(self) -> None:
        """The physical definition, checked directly: applying any combination of
        the six leaves every inter-node distance unchanged to first order."""
        mesh = block()
        modes = rigid_body_modes(mesh.nodes)
        rng = np.random.default_rng(0)
        coefficients = rng.normal(size=RIGID_BODY_MODES)
        # Scaled small so the second-order term of a finite rotation stays below
        # the tolerance; a rigid-body mode is the linearised motion.
        displacement = (modes @ coefficients).reshape(-1, 3) * 1e-6

        first, second = mesh.tets[:, 0], mesh.tets[:, 1]
        before = np.linalg.norm(mesh.nodes[first] - mesh.nodes[second], axis=1)
        moved = mesh.nodes + displacement
        after = np.linalg.norm(moved[first] - moved[second], axis=1)
        assert np.abs(after - before).max() < 1e-9 * before.min()


class TestHeldDofs:
    def test_numbering_is_three_per_node(self) -> None:
        mesh = block()
        fixture = Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"])
        dofs = held_dofs(mesh, [fixture])
        assert np.all(dofs % 3 == 2)
        assert len(dofs) == len(np.unique(dofs))
        assert dofs.max() < 3 * mesh.node_count

    def test_overlapping_fixtures_are_counted_once(self) -> None:
        mesh = block()
        one = held_dofs(mesh, [clamp_bottom()])
        twice = held_dofs(mesh, [clamp_bottom(), clamp_bottom()])
        assert np.array_equal(one, twice)

    def test_no_fixtures_holds_nothing(self) -> None:
        assert len(held_dofs(block(), [])) == 0


# -- the verdict -------------------------------------------------------------


class TestProperlyRestrained:
    def test_a_single_clamped_face_passes(self) -> None:
        report = check_restraints(block(), [clamp_bottom()])
        assert report.restrained
        assert report.rank == RIGID_BODY_MODES
        assert report.free_count == 0
        assert report.free_outright == ()
        assert report.combination_count == 0
        assert report.message() == "The fixtures remove all six rigid-body motions."

    def test_three_orthogonal_roller_planes_pass(self) -> None:
        """The arrangement `tests/test_solver.py::uniaxial_case` uses, which
        solves cleanly today — so the check must not refuse it."""
        rollers = [
            Fixture(where=FaceSelector(axis=axis, side="min"), kind="roller", normal=axis)
            for axis in ("x", "y", "z")
        ]
        assert check_restraints(block(), rollers).restrained

    def test_require_restrained_returns_the_report(self) -> None:
        report = require_restrained(block(), [clamp_bottom()])
        assert report.restrained


class TestCompletelyFree:
    """The measured defect: a part restrained nowhere at all."""

    def test_all_six_survive_and_all_six_are_named(self) -> None:
        report = check_restraints(block(), [])
        assert not report.restrained
        assert report.rank == 0
        assert report.free_count == RIGID_BODY_MODES
        assert free_names(report) == set(MODE_NAMES)
        assert report.combination_count == 0
        assert report.held_dof_count == 0

    def test_the_message_says_it_is_held_nowhere(self) -> None:
        message = check_restraints(block(), []).message()
        assert "nothing in it is held at all" in message
        assert "Check that the fixtures remove all six rigid-body motions" in message

    def test_require_restrained_raises(self) -> None:
        with pytest.raises(SolverError) as caught:
            require_restrained(block(), [])
        assert "under-constrained" in str(caught.value)

    def test_the_instruction_does_not_recite_all_six(self) -> None:
        """Listing every motion back at someone who restrained nothing is a
        restatement of the problem, not an instruction."""
        instruction = check_restraints(block(), []).what_to_do()
        assert instruction.startswith("Add a fixture: nothing holds this part")
        assert "rotation about y" not in instruction


class TestRollerPlane:
    """A block on one frictionless plane: slides in x and y, spins about z.

    Three, not five. Holding z over a whole face also removes rocking about x and
    about y, because those motions have a non-zero z at every node of the face
    except one line. Five is `TestSinglePointRoller` below.
    """

    def test_three_named_motions_survive(self) -> None:
        report = check_restraints(block(), [roller_bottom()])
        assert not report.restrained
        assert report.rank == 3
        assert report.free_count == 3
        assert free_names(report) == {
            "translation along x",
            "translation along y",
            "rotation about z",
        }
        assert report.combination_count == 0
        assert report.partly_free == {}

    def test_the_message_names_all_three(self) -> None:
        message = check_restraints(block(), [roller_bottom()]).message()
        for name in ("translation along x", "translation along y", "rotation about z"):
            assert name in message
        assert "3 of the six" in message
        assert "Check that the fixtures remove all six rigid-body motions" in message

    def test_many_redundant_fixtures_on_the_same_plane_still_fail(self) -> None:
        """Numerous is not the same as sufficient.

        Six separate patches tiling the bottom face, each held out of plane, on a
        mesh with thirty-five nodes on that face. Six fixtures and thirty-five
        held degrees of freedom, and exactly the same three surviving motions as
        the one roller above — a rank test cannot be fooled by repetition, and a
        count of fixtures or of held nodes could be.
        """
        mesh = box_mesh(SIZE, divisions=(6, 4, 2))
        patches = [
            Fixture(
                where=BoxSelector(
                    min=(i * 20.0, j * 20.0, -0.1),
                    max=(i * 20.0 + 20.0, j * 20.0 + 20.0, 0.1),
                ),
                dofs=["z"],
            )
            for i in range(3)
            for j in range(2)
        ]
        assert len(patches) == 6
        report = check_restraints(mesh, patches)
        assert report.held_dof_count == 35
        assert report.held_dof_count == check_restraints(mesh, [roller_bottom()]).held_dof_count
        assert report.free_count == 3
        assert free_names(report) == {
            "translation along x",
            "translation along y",
            "rotation about z",
        }


class TestSinglePointRoller:
    """One node held in z alone: five motions survive, only three of them named.

    This is the case the brief meant. It is also the case that decides how the
    unnamed remainder is presented: the other two live in the span of translation
    along z, rotation about x and rotation about y, and no basis of that plane is
    more real than another.
    """

    def corner(self) -> Fixture:
        return Fixture(where=SphereSelector(centre=(0.0, 0.0, 0.0), radius=1e-6), dofs=["z"])

    def test_five_survive_of_which_three_are_named(self) -> None:
        report = check_restraints(block(), [self.corner()])
        assert report.held_dof_count == 1
        assert report.rank == 1
        assert report.free_count == 5
        assert free_names(report) == {
            "translation along x",
            "translation along y",
            "rotation about z",
        }
        assert report.combination_count == 2

    def test_the_unnamed_remainder_is_reported_as_a_spread(self) -> None:
        report = check_restraints(block(), [self.corner()])
        assert set(report.partly_free) == {
            "translation along z",
            "rotation about x",
            "rotation about y",
        }
        assert all(0.0 < value < 1.0 for value in report.partly_free.values())
        message = report.message()
        assert "are combinations of" in message
        assert "not any one of them on its own" in message
        # Canonical order, not alphabetical: translations then rotations.
        assert "translation along z, rotation about x and rotation about y" in message

    def test_the_instruction_admits_it_is_only_a_start(self) -> None:
        """Naming the three free-outright motions and stopping would send the
        engineer round the loop a second time for the two that remain."""
        instruction = check_restraints(block(), [self.corner()]).what_to_do()
        assert instruction.startswith("Start with a restraint that resists")
        assert "2 more motions would still survive that" in instruction


class TestClampedAlongOneLine:
    """Every degree of freedom held, at many nodes, and still under-constrained.

    The nodes all lie on one edge, so the part can still turn about that edge.
    That motion is *not* one of the six named ones — it is a rotation about a line
    that does not pass through the centroid — so nothing is reported as free
    outright, and saying "rotation about x is free" would be a lie that sends the
    engineer to restrain something already held.
    """

    def edge(self) -> Fixture:
        return Fixture(where=BoxSelector(min=(-0.1, -0.1, -0.1), max=(60.1, 0.1, 0.1)))

    def test_one_motion_survives_and_it_is_not_a_named_one(self) -> None:
        report = check_restraints(block(), [self.edge()])
        assert report.held_dof_count >= 12  # four nodes along the edge, three each
        assert report.rank == 5
        assert report.free_count == 1
        assert report.free_outright == ()
        assert report.combination_count == 1

    def test_the_message_names_the_motions_it_is_spread_over(self) -> None:
        report = check_restraints(block(), [self.edge()])
        assert set(report.partly_free) == {
            "rotation about x",
            "translation along y",
            "translation along z",
        }
        message = report.message()
        assert "The surviving motion is a combination of" in message
        assert "rotation about x" in message
        assert "free outright" not in message


class TestSingleClampedPoint:
    """One node held in all three directions: the three rotations about it survive.

    A translation is *not* free here even though only one node in a hundred holds
    it — which is the case the rank tolerance has to get right, and the reason it
    cannot be set anywhere near the share one node carries.
    """

    def test_three_survive_and_no_translation_is_called_free(self) -> None:
        fixture = Fixture(where=SphereSelector(centre=(0.0, 0.0, 0.0), radius=1e-6))
        report = check_restraints(block(), [fixture])
        assert report.held_dof_count == 3
        assert report.rank == 3
        assert report.free_count == 3
        assert report.free_outright == ()
        assert report.combination_count == 3


class TestQuadraticElements:
    """tet10. The midside nodes are degrees of freedom too, and are restrained by
    the same selectors — a check that looked only at corners would report a
    different rank for the same physical model."""

    def test_a_clamped_face_passes(self) -> None:
        mesh = promote_to_tet10(block())
        assert mesh.midside is not None
        assert check_restraints(mesh, [clamp_bottom()]).restrained

    def test_the_verdict_matches_the_linear_mesh(self) -> None:
        linear = block()
        quadratic = promote_to_tet10(block())
        for fixtures in ([], [roller_bottom()], [clamp_bottom()]):
            first = check_restraints(linear, fixtures)
            second = check_restraints(quadratic, fixtures)
            assert second.node_count > first.node_count
            assert (second.restrained, second.free_count) == (first.restrained, first.free_count)
            assert free_names(second) == free_names(first)


# -- the scaling decisions ---------------------------------------------------


class TestScaleAndPlacement:
    """The verdict is a property of the model, not of the units it is drawn in or
    of where it happens to sit relative to the global origin. This is what the
    per-mode normalisation and the centroid-centred rotations are for."""

    @pytest.mark.parametrize("scale", [1e-2, 1.0, 1e3])
    def test_the_same_part_at_three_sizes_reads_the_same(self, scale: float) -> None:
        mesh = box_mesh((60.0 * scale, 40.0 * scale, 20.0 * scale), divisions=(3, 2, 2))
        assert check_restraints(mesh, [clamp_bottom()]).restrained
        rolling = check_restraints(mesh, [roller_bottom()])
        assert rolling.free_count == 3
        assert free_names(rolling) == {
            "translation along x",
            "translation along y",
            "rotation about z",
        }

    def test_a_part_far_from_the_origin_reads_the_same(self) -> None:
        near = block()
        far = TetMesh(nodes=near.nodes + np.array([1.0e5, 2.0e5, 3.0e5]), tets=near.tets)
        assert check_restraints(far, [clamp_bottom()]).restrained
        rolling = check_restraints(far, [roller_bottom()])
        assert rolling.free_count == 3
        assert free_names(rolling) == {
            "translation along x",
            "translation along y",
            "rotation about z",
        }

    def test_a_fine_mesh_and_a_coarse_one_agree(self) -> None:
        coarse = check_restraints(box_mesh(SIZE, divisions=(1, 1, 1)), [roller_bottom()])
        fine = check_restraints(box_mesh(SIZE, divisions=(8, 6, 5)), [roller_bottom()])
        assert fine.node_count > 10 * coarse.node_count
        assert (coarse.free_count, fine.free_count) == (3, 3)
        assert free_names(coarse) == free_names(fine)


def surviving_fractions(mesh: TetMesh, fixtures: list[Fixture]) -> dict[str, float]:
    """An independent, deliberately slow answer to "how much of each named motion
    survives", computed in the full 3N-dimensional displacement space.

    Definitional rather than clever: build the six modes, find the null space of
    their restriction to the held degrees of freedom, map that null space back
    into displacement space, orthonormalise it *there*, and project each mode onto
    it. No whitening, no reuse of the module's own arithmetic — which is what
    makes it an oracle rather than a restatement.
    """
    modes = rigid_body_modes(mesh.nodes)
    modes = modes / np.linalg.norm(modes, axis=0)
    restricted = modes[held_dofs(mesh, fixtures), :]
    _, singular, right = np.linalg.svd(restricted, full_matrices=True)
    rank = int((singular > 1e-9 * singular.max()).sum())
    basis, _ = np.linalg.qr(modes @ right[rank:].T)
    return {
        name: float(np.linalg.norm(basis.T @ modes[:, index]))
        for index, name in enumerate(MODE_NAMES)
    }


class TestSurvivingFractionsAreEnergyFractions:
    """`partly_free` is a fraction of the motion's energy, not of a coefficient.

    Checked on a mesh deliberately turned off the global axes, because that is
    the only case where the distinction shows: for an axis-aligned box the six
    modes are already orthogonal and every reasonable way of computing this
    agrees. Rotate the part and the rotation modes stop being orthogonal to each
    other, and a projection taken in coefficient space starts answering a
    different question from a projection taken in displacement space.
    """

    def tilted(self) -> TetMesh:
        mesh = box_mesh(SIZE, divisions=(3, 2, 2))
        about_z = np.array(
            [
                [np.cos(0.5), -np.sin(0.5), 0.0],
                [np.sin(0.5), np.cos(0.5), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        about_x = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, np.cos(0.35), -np.sin(0.35)],
                [0.0, np.sin(0.35), np.cos(0.35)],
            ]
        )
        return TetMesh(nodes=mesh.nodes @ about_z.T @ about_x.T, tets=mesh.tets)

    def held_node(self, mesh: TetMesh) -> list[Fixture]:
        centre = tuple(float(value) for value in mesh.nodes[0])
        assert len(centre) == 3
        return [Fixture(where=SphereSelector(centre=centre, radius=1e-6), dofs=["z"])]

    def test_the_modes_really_are_off_axis(self) -> None:
        """Guard on the fixture itself: if this mesh were axis-aligned the test
        below would pass without exercising anything."""
        modes = rigid_body_modes(self.tilted().nodes)
        modes = modes / np.linalg.norm(modes, axis=0)
        gram = modes.T @ modes
        rotations = gram[3:, 3:]
        assert np.abs(rotations - np.eye(3)).max() > 0.05

    def test_every_reported_fraction_matches_the_displacement_space_answer(self) -> None:
        mesh = self.tilted()
        fixtures = self.held_node(mesh)
        report = check_restraints(mesh, fixtures)
        oracle = surviving_fractions(mesh, fixtures)

        assert report.free_count == 5
        assert report.partly_free  # otherwise there is nothing being compared
        for name, value in report.partly_free.items():
            assert value == pytest.approx(oracle[name], abs=1e-9)
        for motion in report.free_outright:
            assert oracle[motion.name] == pytest.approx(1.0, abs=1e-9)


class TestDegenerateMesh:
    def test_a_collinear_mesh_is_refused_by_name(self) -> None:
        """Every node on one line: one rotation is not a motion of this body at
        all, so the six-mode basis does not exist and the question is malformed."""
        nodes = np.array([[float(i), 0.0, 0.0] for i in range(4)])
        # `TetMesh` accepts this: it checks shapes and index ranges, not volumes.
        mesh = TetMesh(nodes=nodes, tets=np.array([[0, 1, 2, 3]], dtype=np.int64))
        with pytest.raises(SolverError) as caught:
            check_restraints(mesh, [clamp_bottom()])
        assert "every node lies on one line" in str(caught.value)
        assert "Re-mesh" in str(caught.value)


# -- what the API and the agent get -----------------------------------------


class TestReportSurface:
    def test_as_dict_is_json_safe(self) -> None:
        report = check_restraints(block(), [roller_bottom()])
        payload = json.loads(json.dumps(report.as_dict()))
        assert payload["restrained"] is False
        assert payload["free_count"] == 3
        assert {motion["axis"] for motion in payload["free_outright"]} == {"x", "y", "z"}
        assert {motion["kind"] for motion in payload["free_outright"]} == {
            "translation",
            "rotation",
        }
        assert payload["message"].startswith("The model is under-constrained")

    def test_the_agent_gets_the_axes_without_parsing_the_sentence(self) -> None:
        report = check_restraints(block(), [roller_bottom()])
        pairs = {(motion.kind, motion.axis) for motion in report.free_outright}
        assert pairs == {("translation", "x"), ("translation", "y"), ("rotation", "z")}

    def test_the_instruction_is_separable_from_the_account(self) -> None:
        report = check_restraints(block(), [roller_bottom()])
        assert report.what_to_do().startswith("Add a restraint that resists")
        assert "under-constrained" not in report.what_to_do()
        assert report.what_to_do() in report.message()

    def test_a_restrained_report_has_no_instruction(self) -> None:
        report = check_restraints(block(), [clamp_bottom()])
        assert report.what_to_do() == ""
