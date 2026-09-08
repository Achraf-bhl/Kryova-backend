"""Is this model held still enough to solve? Answered before anything is solved.

**Why this exists.** `linear_static` catches an under-constrained model *after*
the fact, with the equilibrium residual (`_residual_is_small`): SuperLU returns a
finite, meaningless vector for a singular system, so a finiteness check would
pass it. That works, and it must stay — but it needs the assembled stiffness
matrix, which is exactly what federating to CalculiX is meant to avoid owning.
And CalculiX does not make up the difference: measured on this machine on
2026-09-05, a deck with its `*BOUNDARY` block removed — a part restrained
nowhere at all — came back from `ccx` 2.23/PaStiX with exit code 0, a complete
`.frd`, no `*ERROR`, no `*WARNING`, and a maximum displacement of 5.4e+11 mm.
`diagnose()` correctly returns `None` for that run, because there is nothing in
the solver's output to diagnose. 541 million metres would have been reported as
a successful analysis.

So the check has to happen before the solve, and it can: whether the fixtures
remove all six rigid-body motions is a question about the restraints and the
node positions alone. No material, no loads, no stiffness matrix.

**The mathematics.** A free 3D body has exactly six rigid-body motions — three
translations and three rotations. Over a mesh of N nodes each is a vector in the
3N-dimensional displacement space:

* translation along an axis: every node moves by the same unit vector;
* rotation about an axis through the centroid: node *i* moves by ``omega x r_i``,
  with ``r_i`` measured from the centroid.

Collect them as the columns of ``R``, shape ``(3N, 6)``. Let ``C`` be the set of
degrees of freedom the fixtures hold. A rigid motion ``R v`` is still available
to the restrained model exactly when it moves no held degree of freedom, i.e.
when ``R[C, :] v == 0``. The surviving motions are therefore the null space of
``R[C, :]``, and the model is properly restrained iff::

    rank(R[C, :]) == 6

which is decided by SVD. Nothing here is a heuristic: it is an exact statement
about the load case, and it is the same statement whatever solver runs next.

The centroid is not cosmetic. Taking rotations about the centroid makes every
translation column orthogonal to every rotation column — ``sum_i e_a . (e_b x r_i)
= e_a . (e_b x sum_i r_i)``, and ``sum_i r_i`` is zero by the definition of the
centroid — so the basis starts out well conditioned instead of being fixed up
afterwards.

**Why the modes are normalised, and by what.** A rotation mode's magnitude grows
with the size of the part (its entries are distances) while a translation's does
not (its entries are 1). Un-normalised, a rank tolerance would mean something
different for a 10 mm bracket than for a 10 m gantry, and something different
again for a fine mesh than a coarse one. Each column is therefore divided by its
own L2 norm **over the whole mesh** — never over the restrained rows only.
Normalising the submatrix's columns would rescale a column that is nearly zero on
``C`` up to unit length, which destroys precisely the information being measured.

After that normalisation the six modes are unit vectors but not quite mutually
orthogonal: rotations about the global axes are orthogonal to each other only
when those axes are principal for the point set. So the columns are whitened by
their own Gram matrix ``G = R^T R`` (a correlation matrix, unit diagonal) before
the SVD: with ``G = L L^T`` and ``A = R[C, :] L^-T``, a unit vector in the whitened
coordinates is a rigid motion of unit total L2 energy over the mesh. That makes
every singular value of ``A`` lie in ``[0, 1]`` with a physical reading — the
square root of the fraction of that motion's energy that lands on held degrees of
freedom — and it leaves the rank untouched, because ``L`` is invertible.

**The rank tolerance is absolute, and can be, because of that scaling.**
``RANK_TOLERANCE`` is 1e-9. The two bounds it sits between:

* A genuinely restrained motion is restrained by at least one degree of freedom
  of one node, which carries a share ``~1/sqrt(3N)`` of the mode's energy — 6e-4
  for a million-node mesh. Four orders of magnitude of margin, and it *grows*
  relative to the tolerance as meshes get coarser, never shrinks past it.
* A genuinely free motion reads exactly zero except for round-off in ``r = x - c``,
  which is ``eps * |x| / |r|`` — a part 10 km from the origin with 1 mm features
  still lands near 1e-9's predecessor. Parts do not sit that far off.

**What it reports, and the one place it refuses to guess.** Naming which of the
six is free is the half that saves an engineer time: "rotation about z is free"
says which face to hold; "under-constrained" says go and find out. A named motion
is reported free only on the exact criterion above — its entries on ``C`` are all
zero — so that claim is never approximate.

But the null space is usually not spanned by named motions. Hold one node in z
alone and five motions survive, of which only three (translation along x,
translation along y, rotation about z) are named; the other two are *some*
two-dimensional space inside the span of translation along z, rotation about x and
rotation about y, and no particular basis of it is more real than any other. Two
choices were rejected: naming the nearest named axis, which invents precision the
null space does not contain, and printing an SVD basis vector per surviving
dimension, which does the same thing in worse handwriting — those vectors are an
artefact of the algorithm and would change with the node ordering. What is
reported instead is basis-independent: the *dimension* of the residual space, and
for each named motion the fraction of its energy that survives (``partly_free``).
An engineer reading "2 more survive, spread across translation along z, rotation
about x and rotation about y" knows both that three candidates are implicated and
that restraining one of them is not automatically enough — which is true, and is
what the arithmetic actually supports.

**On connectivity.** The same 2026-09-05 session found that an element referring
to a node number outside the mesh is accepted silently by CalculiX — max stress
jumped from 25 to 6512 MPa, exit code 0, no complaint. That guard is deliberately
not here. `TetMesh.__post_init__` already refuses it at construction ("tet
references a node index outside the node array"), so a `TetMesh` handed to a deck
writer cannot carry one; the only way an out-of-range node number reaches a `.inp`
is if the deck writer's own 1-based renumbering goes wrong, and a check on the
`TetMesh` cannot see that. It belongs over the emitted deck, in
`app/solve/calculix/deck.py`. Repeating the mesh's own invariant here would be a
second copy of a guard that already holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from app.solve.selection import PointCloud, select_nodes
from app.solve.types import Fixture, SolverError

#: A free rigid body in three dimensions has exactly this many motions.
RIGID_BODY_MODES: Final = 6

#: Degrees of freedom a node of a solid element carries: three translations, and
#: no rotation at all. A `*BOUNDARY` naming DOF 4 on a solid is meaningless.
SOLID_DOFS: Final = 3

#: Degrees of freedom a node of a shell or beam element carries: three
#: translations and three rotations. Master plan 6.3 — CalculiX expands these
#: elements into solids internally and ties the expansion back with multiple-
#: point constraints, so the node the caller wrote does have a rotation to hold.
STRUCTURAL_DOFS: Final = 6

#: Below this, a normalised, whitened singular value is zero. See the module
#: docstring for the two bounds it sits between; it is absolute rather than
#: relative because the whitening puts every singular value in [0, 1].
RANK_TOLERANCE: Final = 1e-9

_AXES: Final = ("x", "y", "z")

#: The six motions, in column order: three translations then three rotations.
_MOTIONS: Final[tuple[tuple[str, str], ...]] = tuple(
    [("translation", axis) for axis in _AXES] + [("rotation", axis) for axis in _AXES]
)


def motion_name(kind: str, axis: str) -> str:
    """"translation along x", "rotation about z" — how these are said out loud."""
    return f"{kind} along {axis}" if kind == "translation" else f"{kind} about {axis}"


MODE_NAMES: Final[tuple[str, ...]] = tuple(motion_name(kind, axis) for kind, axis in _MOTIONS)


@dataclass(frozen=True)
class FreeMotion:
    """One of the six named motions that the fixtures do not touch at all.

    `kind` and `axis` are here as well as `name` because the agent acts on the
    pair and the user reads the sentence, and making the agent parse the sentence
    back apart is how it comes to depend on the wording.
    """

    kind: str  # "translation" | "rotation"
    axis: str  # "x" | "y" | "z"

    @property
    def name(self) -> str:
        return motion_name(self.kind, self.axis)

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "axis": self.axis, "name": self.name}


@dataclass(frozen=True)
class ConstraintReport:
    """What the fixtures do and do not hold, decided before any solve.

    `restrained` is the whole verdict; everything else exists so that a failure
    can be acted on rather than only announced.
    """

    #: True iff rank(R[C, :]) == 6 — the model has a unique static solution.
    restrained: bool
    #: rank(R[C, :]), between 0 and 6.
    rank: int
    #: 6 - rank: the dimension of the space of motions that survive.
    free_count: int
    #: The named motions with no component at all on a held degree of freedom.
    #: Exact, never approximate.
    free_outright: tuple[FreeMotion, ...]
    #: free_count - len(free_outright): surviving motions that are combinations
    #: of named ones. Reported as a dimension, not as invented named axes.
    combination_count: int
    #: For each named motion not free outright, the fraction of its energy that
    #: survives the fixtures, in (0, 1). Indicative — the exact claims are
    #: `free_outright` and `free_count`.
    partly_free: dict[str, float]
    node_count: int
    held_dof_count: int

    def message(self) -> str:
        """One sentence for a `SolverError`, in the codebase's own register.

        Ends on the phrase `linear_static` has always used, so the two paths say
        the same thing to a user who has met one of them before.
        """
        if self.restrained:
            return "The fixtures remove all six rigid-body motions."

        parts = [self._what_survives(), self.what_to_do()]
        return " ".join(part for part in parts if part)

    def what_to_do(self) -> str:
        """The instruction, kept apart from the account, as `Diagnosis` does."""
        if self.restrained:
            return ""
        tail = (
            "Check that the fixtures remove all six rigid-body motions — "
            "three translations and three rotations."
        )
        if self.free_count == RIGID_BODY_MODES:
            # Listing all six here would be a restatement, not an instruction.
            return f"Add a fixture: nothing holds this part at present. {tail}"
        if not self.free_outright:
            return tail
        named = _and([motion.name for motion in self.free_outright])
        if self.combination_count:
            # Saying "add these and you are done" when the rest of the null space
            # is still there would send the engineer round the loop twice.
            more = self.combination_count
            return (
                f"Start with a restraint that resists {named}; {more} more "
                f"{'motion' if more == 1 else 'motions'} would still survive that. {tail}"
            )
        return f"Add a restraint that resists {named}. {tail}"

    def _what_survives(self) -> str:
        if self.free_count == RIGID_BODY_MODES:
            return (
                "The model is under-constrained: nothing in it is held at all, so "
                "all six rigid-body motions survive and the answer is not unique."
            )

        survive = "survives" if self.free_count == 1 else "survive"
        head = (
            f"The model is under-constrained: {self.free_count} of the six "
            f"rigid-body motions {survive} the fixtures"
        )
        if self.free_outright:
            named = _and([motion.name for motion in self.free_outright])
            verb = "is" if len(self.free_outright) == 1 else "are"
            head += f" — {named} {verb} free outright"
        head += "."

        count = self.combination_count
        if count:
            spread = _and(self._spread_names()) or "the remaining motions"
            if self.free_outright:
                subject = "The other one" if count == 1 else f"The other {count}"
            else:
                subject = "The surviving motion" if count == 1 else f"The surviving {count} motions"
            verb = "is a combination" if count == 1 else "are combinations"
            head += (
                f" {subject} {verb} of {spread}, not any one of them on its own, "
                "so restraining any single one of them need not be enough."
            )
        return head

    def _spread_names(self) -> list[str]:
        """`partly_free`'s keys in the canonical mode order, not alphabetically —
        "translation along z, rotation about x and rotation about y" is the order
        an engineer reads the six in everywhere else."""
        return [name for name in MODE_NAMES if name in self.partly_free]

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe, for the API and for the agent's tool result."""
        return {
            "restrained": self.restrained,
            "rank": self.rank,
            "free_count": self.free_count,
            "free_outright": [motion.as_dict() for motion in self.free_outright],
            "combination_count": self.combination_count,
            "partly_free": {name: self.partly_free[name] for name in self._spread_names()},
            "node_count": self.node_count,
            "held_dof_count": self.held_dof_count,
            "message": self.message(),
            "what_to_do": self.what_to_do(),
        }


def _and(names: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c" — a list a person can read."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def local_dofs(fixture: Fixture, *, rotations: bool) -> tuple[int, ...]:
    """Which of a node's degrees of freedom this fixture holds, 0-based and local.

    0, 1, 2 are the translations along x, y, z; 3, 4, 5 the rotations about them.
    CalculiX numbers the same six 1 to 6, and `deck.py` adds the one.

    **Master plan 6.3.** On a solid there is nothing to decide: a solid element
    has no rotational degree of freedom, so a fixture holds exactly the
    translations it names. On a shell or a beam the same word means more, and
    getting it wrong is not a small error:

    * **`clamp` holds all six.** A clamp is *built in*. Holding only the three
      translations leaves a **pin**, and a cantilever on a pin is a mechanism —
      a beam clamped at one node and loaded at the other is either four times
      stiffer than the pinned version or, with a single element, not solvable at
      all. This is the one place the word has to be read rather than the letters.
    * **`symmetry` holds the out-of-plane translation and the two rotations about
      the in-plane axes.** That is what a plane of symmetry is: material crossing
      it must arrive perpendicular. Holding only the translation gives a roller,
      and a roller lets the section rotate through the plane, which is precisely
      the thing symmetry forbids.
    * **`roller` holds one translation and no rotation**, and `slider` holds the
      two translations across its normal and no rotation. Both are named for
      supports that genuinely do not resist a moment, so nothing is added.
    * **`custom` holds the translations it names and nothing else** — even
      `["x", "y", "z"]`, which is a pin and not a clamp. `dofs` is a list of
      *letters*, and letters are translations; a caller who wants the rotations
      held has the word `clamp` to say so. Upgrading it silently would mean two
      spellings of the same fixture solving differently with nothing to read.
    """
    translations = tuple(_AXES.index(axis) for axis in fixture.held)
    if not rotations:
        return translations

    if fixture.kind == "clamp":
        return (0, 1, 2, 3, 4, 5)
    if fixture.kind == "symmetry":
        # `Fixture._implied_dofs` has already refused a symmetry fixture with no
        # normal, so this cannot be None by the time a solver sees one.
        normal = fixture.normal or "x"
        in_plane = tuple(3 + _AXES.index(axis) for axis in _AXES if axis != normal)
        return translations + in_plane
    return translations


def held_dofs(
    mesh: PointCloud,
    fixtures: Sequence[Fixture],
    *,
    dofs_per_node: int = SOLID_DOFS,
) -> NDArray[np.int64]:
    """Global degree-of-freedom indices the fixtures hold: sorted, de-duplicated.

    Degrees of freedom are numbered ``dofs_per_node * node + local``, with the
    local numbering `local_dofs` documents. At the default of three that is
    ``3 * node + {0, 1, 2}`` for x, y, z — the numbering the whole `solve`
    package uses, unchanged.

    `linear_static._dof_indices` computes the same thing and should call this
    instead, so the mapping is written down once. It could not be changed to in
    this session: `linear_static.py` was being edited by another agent, and the
    dependency has to run that way round anyway — this module deliberately
    imports no solver and no scipy, so that the check stays callable from the
    deck writer, from a route, and from anywhere else that has a mesh and a load
    case but no intention of assembling a stiffness matrix.
    """
    if not fixtures:
        return np.zeros(0, dtype=np.int64)
    rotations = dofs_per_node == STRUCTURAL_DOFS
    blocks = []
    for fixture in fixtures:
        nodes = select_nodes(mesh, fixture.where)
        offsets = np.array(local_dofs(fixture, rotations=rotations), dtype=np.int64)
        blocks.append((nodes[:, None] * dofs_per_node + offsets[None, :]).ravel())
    return np.unique(np.concatenate(blocks))


def rigid_body_modes(
    nodes: NDArray[np.float64], *, dofs_per_node: int = SOLID_DOFS
) -> NDArray[np.float64]:
    """The six rigid-body motions over these nodes, as columns of a (D*N, 6) array.

    Columns 0-2 are the translations along x, y, z; columns 3-5 the rotations
    about x, y, z **through the centroid**, which is what makes the two groups
    mutually orthogonal (see the module docstring). Not normalised — `check` does
    that, and a caller wanting the raw motions should get the raw motions.

    At `dofs_per_node=6` each node also carries the *rotation* part of each mode:
    a rigid rotation about x turns every node by the same amount about x, so the
    rotational block of that column is a constant unit vector while the
    translational block is still ``omega x r``. Two things follow, and the second
    is why the six-degree-of-freedom form has to exist at all:

    * the two groups stay mutually orthogonal — the extra term is
      ``e_a . 0 = 0`` against a translation — so the whitening argument in the
      module docstring is untouched;
    * **a rotation mode of a collinear mesh is no longer zero.** A straight beam
      has every node on one line, so ``omega x r`` about that line vanishes and
      the three-degree-of-freedom form calls the mesh degenerate. With the
      rotational block present the column has norm ``sqrt(N)`` whatever the
      geometry, and a straight beam is checked like anything else — which it has
      to be, because a straight beam is the commonest member in a frame.
    """
    coords = np.ascontiguousarray(nodes, dtype=np.float64)
    count = len(coords)
    offsets = coords - coords.mean(axis=0)

    modes = np.zeros((dofs_per_node * count, RIGID_BODY_MODES), dtype=np.float64)
    for index, axis in enumerate(np.eye(3)):
        modes[index :: dofs_per_node, index] = 1.0
        rotation = np.cross(axis, offsets)
        for component in range(3):
            modes[component :: dofs_per_node, 3 + index] = rotation[:, component]
        if dofs_per_node == STRUCTURAL_DOFS:
            modes[3 + index :: dofs_per_node, 3 + index] = 1.0
    return modes


def _normalised_modes(
    nodes: NDArray[np.float64], dofs_per_node: int
) -> NDArray[np.float64]:
    """`rigid_body_modes` with each column scaled to unit L2 norm over the mesh."""
    modes = rigid_body_modes(nodes, dofs_per_node=dofs_per_node)
    norms = np.linalg.norm(modes, axis=0)
    if float(norms.min()) <= 0.0:
        raise SolverError(
            "This mesh has no three-dimensional extent — every node lies on one "
            "line, so one of the rigid-body rotations does not move it at all. "
            "Re-mesh the part; a degenerate mesh cannot be checked or solved."
        )
    return modes / norms[None, :]


def check_restraints(
    mesh: PointCloud,
    fixtures: Sequence[Fixture],
    *,
    dofs_per_node: int = SOLID_DOFS,
) -> ConstraintReport:
    """Do these fixtures remove all six rigid-body motions? Exact, and cheap.

    Cheap: one pass to build a ``(3N, 6)`` array and an SVD of a ``(|C|, 6)``
    matrix. No material, no loads, no stiffness. Safe to call on every solve, in
    either solver, before writing a deck or assembling anything.

    Never raises for an under-constrained model — that is a report, not an
    exception, because the API and the agent both want to *render* it. Use
    `require_restrained` where a `SolverError` is what the caller wants.
    """
    modes = _normalised_modes(mesh.nodes, dofs_per_node)
    constrained = held_dofs(mesh, fixtures, dofs_per_node=dofs_per_node)

    # Whitening: G = R^T R is a correlation matrix (unit diagonal, since the
    # columns are normalised). In the whitened coordinates a unit vector is a
    # rigid motion carrying unit total energy over the mesh, so every singular
    # value below lies in [0, 1] and the rank tolerance can be absolute.
    gram = modes.T @ modes
    try:
        cholesky = np.linalg.cholesky(gram)
    except np.linalg.LinAlgError as exc:  # pragma: no cover - degenerate mesh
        raise SolverError(
            "The six rigid-body motions of this mesh are not independent, which "
            "means the mesh is degenerate. Re-mesh the part."
        ) from exc

    restricted = modes[constrained, :]
    if len(constrained) == 0:
        singular = np.zeros(0, dtype=np.float64)
        directions = np.eye(RIGID_BODY_MODES, dtype=np.float64)
    else:
        # A = R[C, :] @ inv(L.T), i.e. A.T = inv(L) @ R[C, :].T.
        whitened = np.linalg.solve(cholesky, restricted.T).T
        _, singular, directions = np.linalg.svd(whitened, full_matrices=True)

    rank = int((singular > RANK_TOLERANCE).sum())
    free_count = RIGID_BODY_MODES - rank

    # Each named motion in whitened coordinates: y_k = L.T @ e_k, which is the
    # k-th column of L.T and has unit norm because diag(G) == 1.
    whitened_names = cholesky.T

    free_outright: list[FreeMotion] = []
    partly_free: dict[str, float] = {}
    for index, (kind, axis) in enumerate(_MOTIONS):
        # Exact test, and the one the claim is made on: this motion's entries on
        # the held degrees of freedom are all zero.
        on_held = float(np.linalg.norm(restricted[:, index])) if len(constrained) else 0.0
        if on_held <= RANK_TOLERANCE:
            free_outright.append(FreeMotion(kind=kind, axis=axis))
            continue
        # Otherwise, how much of it survives: the norm of its projection onto the
        # null space, taken *directly* against the null-space basis rather than as
        # sqrt(1 - held^2). The two are equal in exact arithmetic and are not in
        # floating point: for a fully restrained motion the second form is
        # sqrt(1 - (1 - 2e-16)^2) ~ 2e-8, which is a thousandfold above the
        # tolerance and would list a mode as partly free when it is entirely held.
        # Measured, not predicted — it was the first failure this test file found.
        surviving = float(np.linalg.norm(directions[rank:] @ whitened_names[:, index]))
        if surviving > RANK_TOLERANCE:
            partly_free[motion_name(kind, axis)] = surviving

    return ConstraintReport(
        restrained=rank == RIGID_BODY_MODES,
        rank=rank,
        free_count=free_count,
        free_outright=tuple(free_outright),
        combination_count=max(0, free_count - len(free_outright)),
        partly_free=partly_free,
        node_count=mesh.node_count,
        held_dof_count=int(len(constrained)),
    )


def require_restrained(
    mesh: PointCloud,
    fixtures: Sequence[Fixture],
    *,
    dofs_per_node: int = SOLID_DOFS,
) -> ConstraintReport:
    """`check_restraints`, but raise `SolverError` when the model can still move.

    This is the one line a solver adds. Call it *before* writing a deck or
    assembling a stiffness matrix: it costs an SVD of a six-column matrix, and it
    is the only thing standing between a part held nowhere and a plausible-looking
    displacement of 5.4e+11 mm.
    """
    report = check_restraints(mesh, fixtures, dofs_per_node=dofs_per_node)
    if not report.restrained:
        raise SolverError(report.message())
    return report


__all__ = [
    "MODE_NAMES",
    "RANK_TOLERANCE",
    "RIGID_BODY_MODES",
    "SOLID_DOFS",
    "STRUCTURAL_DOFS",
    "ConstraintReport",
    "FreeMotion",
    "check_restraints",
    "held_dofs",
    "local_dofs",
    "motion_name",
    "require_restrained",
    "rigid_body_modes",
]
