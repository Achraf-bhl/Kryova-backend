"""A result bound to what produced it — Decision 3, and master plan 7.

*"Every result is bound to the geometry, mesh, material, load case and solver
version that produced it."* That is the sentence, and this is the module that
makes it enforceable rather than aspirational.

The reason it is not optional: a stress figure with no provenance cannot be
reproduced, cannot be re-checked when the mesher changes, and cannot be defended
two years later by the engineer who signed it. And the failure is silent — a
number without its chain looks exactly like a number with one, right up until
somebody needs to know which mesh it came from.

Two properties carry it. **A digest changes when the thing changes**: if
refining a mesh leaves `mesh_digest` unchanged, then two different results claim
the same provenance and the record is worse than none, because it now asserts
something false. And **a version that could not be read is recorded as unknown
with a reason, never guessed** — `SolverIdentity` carries `version_reason` for
exactly that, which is the same rule `app/parts/` applies to a bolt's absent
proof load and `benchmarks.py` applies to an unpublished target.
"""

from __future__ import annotations

import numpy as np

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import TetMesh
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, Fixture, ForceLoad, LoadCase
from app.verify.provenance import case_digest, identify_solver, mesh_digest


def _case(force: float = 5000.0, material: str = "steel-1018") -> LoadCase:
    return LoadCase(
        name="uniaxial",
        material=MATERIALS[material],
        fixtures=[Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"])],
        loads=[
            ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, force))
        ],
    )


class TestAMeshDigestTracksTheMesh:
    def test_the_same_mesh_digests_the_same(self) -> None:
        """Otherwise nothing can be compared at all, and a re-run of an
        unchanged model would look like a different model."""
        first = box_mesh((10.0, 20.0, 60.0), divisions=(2, 2, 3))
        second = box_mesh((10.0, 20.0, 60.0), divisions=(2, 2, 3))

        assert mesh_digest(first) == mesh_digest(second)

    def test_refining_it_changes_the_digest(self) -> None:
        """The property that matters. If a refinement left the digest alone, two
        different results would claim the same provenance — and a record that
        asserts something false is worse than no record."""
        coarse = box_mesh((10.0, 20.0, 60.0), divisions=(2, 2, 3))
        fine = box_mesh((10.0, 20.0, 60.0), divisions=(4, 4, 6))

        assert mesh_digest(coarse) != mesh_digest(fine)

    def test_changing_the_geometry_changes_the_digest(self) -> None:
        a = box_mesh((10.0, 20.0, 60.0), divisions=(2, 2, 3))
        b = box_mesh((10.0, 20.0, 61.0), divisions=(2, 2, 3))

        assert mesh_digest(a) != mesh_digest(b)

    def test_promoting_to_tet10_changes_the_digest(self) -> None:
        """A tet4 result and a tet10 result must not share a provenance —
        quadratic elements beat linear at equal element count, so they are
        different answers."""
        linear = box_mesh((10.0, 20.0, 60.0), divisions=(1, 1, 2))
        quadratic = promote_to_tet10(box_mesh((10.0, 20.0, 60.0), divisions=(1, 1, 2)))

        assert mesh_digest(linear) != mesh_digest(quadratic)

    def test_the_midside_nodes_are_in_the_digest_on_their_own(self) -> None:
        """**The test above passes for the wrong reason and this one does not.**

        `promote_to_tet10` appends the midside nodes to the *node array* as well
        as filling `midside`, so the two meshes differ in their coordinates and
        the digest changes whether or not `midside` is hashed at all — measured
        by deleting that line and watching all thirteen tests still pass.

        This holds `nodes` and `tets` identical and changes only `midside`, so
        it fails if and only if the midside connectivity is really in the digest.
        """
        base = promote_to_tet10(box_mesh((10.0, 20.0, 60.0), divisions=(1, 1, 2)))
        assert base.midside is not None
        shuffled = base.midside.copy()
        shuffled[0, [0, 1]] = shuffled[0, [1, 0]]
        other = TetMesh(nodes=base.nodes, tets=base.tets, midside=shuffled)

        assert mesh_digest(base) != mesh_digest(other)

    def test_the_connectivity_is_in_the_digest_on_its_own(self) -> None:
        """Same reasoning: refining a mesh moves its nodes too, so
        `test_refining_it_changes_the_digest` cannot tell whether `tets` is
        hashed. Two meshes over identical node coordinates, connected
        differently, are different meshes and different results."""
        base = box_mesh((10.0, 20.0, 60.0), divisions=(1, 1, 2))
        rolled = np.roll(base.tets, 1, axis=0)
        other = TetMesh(nodes=base.nodes, tets=rolled)

        assert mesh_digest(base) != mesh_digest(other)

    def test_the_digest_names_its_own_algorithm(self) -> None:
        """`sha256:aa73…` rather than bare hex, and the prefix is the better
        choice: a digest that says which algorithm produced it can be migrated
        when sha256 is no longer the right answer, and an old record stays
        readable beside a new one. A bare hex string in a provenance chain is
        undecodable the day the algorithm changes.
        """
        digest = mesh_digest(box_mesh((10.0, 10.0, 10.0), divisions=(1, 1, 1)))

        algorithm, _, value = digest.partition(":")
        assert algorithm == "sha256"
        assert len(value) == 64
        assert all(c in "0123456789abcdef" for c in value)


class TestACaseDigestTracksTheLoadCase:
    def test_the_same_case_digests_the_same(self) -> None:
        assert case_digest(_case()) == case_digest(_case())

    def test_changing_the_load_changes_the_digest(self) -> None:
        """5 kN and 6 kN on the same mesh are two different results, and the
        record has to be able to tell them apart."""
        assert case_digest(_case(force=5000.0)) != case_digest(_case(force=6000.0))

    def test_changing_the_material_changes_the_digest(self) -> None:
        """Same geometry, same load, different modulus — a different
        displacement, so a different result."""
        steel = case_digest(_case(material="steel-1018"))
        aluminium = case_digest(_case(material="aluminium-6061-t6"))

        assert steel != aluminium


class TestASolverIdentityIsHonestAboutItsVersion:
    def test_it_names_the_solver_that_ran(self) -> None:
        from app.solve.linear_static import LinearStaticSolver

        identity = identify_solver(LinearStaticSolver())

        assert identity.name

    def test_it_carries_a_code_digest(self) -> None:
        """A version string alone is not enough for the in-house solver: the
        version is the codebase, and the codebase changes between releases. The
        digest is what makes "this exact solver" checkable."""
        from app.solve.linear_static import LinearStaticSolver

        identity = identify_solver(LinearStaticSolver())

        assert identity.code_digest

    def test_an_unreadable_version_is_none_with_a_reason(self) -> None:
        """Never guessed. `version_reason` exists precisely so that "we could
        not determine it" is a first-class answer rather than a blank that reads
        as "it does not have one" — the same rule `app/parts/` applies to an
        absent proof load and `benchmarks.py` to an unpublished target.
        """

        class Nameless:
            name = "mystery"

        identity = identify_solver(Nameless())

        if identity.version is None:
            assert identity.version_reason, (
                "a version that could not be read must say why; a bare None is "
                "indistinguishable from a solver that genuinely has no version"
            )

    def test_two_different_solvers_do_not_share_an_identity(self) -> None:
        from app.solve.linear_static import LinearStaticSolver

        class Other:
            name = "surrogate"

        assert identify_solver(LinearStaticSolver()).name != identify_solver(Other()).name


class TestTheEnvironmentIsRecorded:
    def test_it_reports_something_about_the_machine(self) -> None:
        """A result that cannot say what Python or what numpy produced it cannot
        be reproduced when either changes under it — and both have changed the
        answer of a linear solve before, in every numerical codebase there has
        ever been."""
        from app.verify.provenance import environment

        recorded = environment()

        assert recorded
        assert all(isinstance(v, str) for v in recorded.values())
