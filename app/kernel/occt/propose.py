"""What would you *call* this face? — master plan P6.6, the 3D → name direction.

`resolve.py` turns a predicate into faces. This turns a face back into a predicate, which
is the half the viewer needs: a user clicks a triangle, the pick becomes a face ordinal
(`tessellate.TriangleMesh.face_of`), and the question is what to put in the `faces:`
argument of the next operation, or in the `element` of a measure route.

**Nothing here decides anything — it offers.** The plan's own word for P6.6 is *offered*,
and that is the honest verb: naming a face is a judgement about what a person meant, and
several predicates can be right at once. So this returns a ranked list and the caller (or
the user) picks.

**Every candidate is verified by resolving it, and that is the whole design.** Candidates
are generated liberally — "maybe it's the top face", "maybe it's the Ø12 bore" — and then
each one is run through `resolve.resolve` against the very same shape and kept only if it
actually selects the picked face. So this module contains no geometric reasoning about
what "at the top" means, and cannot drift from `resolve.py`'s answer the way a second
implementation would. A candidate that does not name the pick is dropped, not corrected.

**A face that nothing names uniquely says so.** `FaceProposal.best` is `None` when no
candidate selects the pick *alone*, and `offered` still carries the near misses with the
count each one matched. That is the `UNMEASURED`-rather-than-a-pass rule applied here: an
ambiguous name that looks definite is worse than no name, because the operation built on
it fillets three faces and reports success. Two faces of a symmetric part genuinely have
no distinguishing description in this vocabulary, and the right answer is to say so.

**Ordering is from most human to least**, because the list is read by a person. A feature
reference (`boss#top`) comes before a bare direction, a direction before an area bracket,
and an `inside` box last — a box always works and tells the reader nothing, so it is the
answer of last resort rather than the first one offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt import classify
from app.kernel.occt.binding import require
from app.kernel.occt.resolve import resolve
from app.kernel.occt.topology import FACE, faces, index_map
from app.kernel.selection import DIRECTIONS, Predicate

#: How close a face normal must sit to a named axis direction before this offers that
#: direction as a name. Deliberately tighter than
#: `selection.DEFAULT_NORMAL_TOLERANCE_DEG`: that one decides whether a *given* name
#: matches, and is loose so a drafted wall still counts as facing outward. This one
#: decides whether to *suggest* a name, and suggesting "+z" for a face 4° off vertical
#: puts a number in front of a user that their part does not have.
SUGGEST_NORMAL_TOLERANCE_DEG: float = 1.0

#: Fractional half-width of an area bracket. `larger_than_mm2`/`smaller_than_mm2` are
#: strict, so a face of area A is bracketed by A·(1∓this). Wide enough to survive the
#: kernel's own noise on an integrated area, far tighter than the difference between two
#: faces a person would call different sizes.
AREA_BRACKET = 1e-4

#: How far a proposed `inside` box is grown past the face's own bounding box, in mm. It
#: has to clear the box comparison's own 1e-9 slack without swallowing a neighbour.
BOX_PADDING_MM = 1e-3


@dataclass(frozen=True)
class Proposal:
    """One way of naming a face, with what it actually selected.

    `matches` and `names_the_pick` are measurements against the real shape, not
    predictions — see the module docstring. A `Proposal` is never constructed without
    having been resolved.
    """

    predicate: Predicate
    matches: int
    names_the_pick: bool

    @property
    def unique(self) -> bool:
        """True only when this predicate selects the picked face and nothing else."""
        return self.matches == 1 and self.names_the_pick

    @property
    def stable(self) -> bool:
        """Whether this name survives an upstream parameter change.

        A predicate whose only discriminator is `inside` is **positional**: it names a
        region of space, so it is right until the part changes size and then it is
        quietly wrong — which is the topological-naming failure `app/design/` exists to
        remove, arrived at from the other end. A box is still offered, because a name
        that works today beats no name, but it is ranked last and marked, and a caller
        writing it into a design should know what it costs.

        An area bracket is *not* counted as positional: it is a property of the face
        rather than of where the face sits, so it moves with the part. It is narrower
        than a direction and is ranked accordingly, but it does not lie.
        """
        return self.predicate.inside is None

    @property
    def words(self) -> str:
        """The predicate in English, for a menu item a person reads."""
        return self.predicate.describe()

    def as_argument(self) -> dict[str, Any]:
        """The spelling an operation's `faces:` argument takes.

        Only the fields that were actually set, so the offered argument reads the way a
        person would have typed it rather than carrying every default.
        """
        out: dict[str, Any] = {"type": "face"}
        for field, value in (
            ("of", self.predicate.of),
            ("axis", self.predicate.axis),
            ("side", self.predicate.side),
            ("normal", self.predicate.normal),
            ("cylindrical", self.predicate.cylindrical),
            ("planar", self.predicate.planar),
            ("diameter_mm", self.predicate.diameter_mm),
            ("larger_than_mm2", self.predicate.larger_than_mm2),
            ("smaller_than_mm2", self.predicate.smaller_than_mm2),
        ):
            if value is not None:
                out[field] = value
        if self.predicate.inside is not None:
            out["inside"] = {
                "min": list(self.predicate.inside.minimum),
                "max": list(self.predicate.inside.maximum),
            }
        return out


@dataclass(frozen=True)
class FaceProposal:
    """Every verified way of naming one picked face, best first."""

    face_index: int
    offered: tuple[Proposal, ...]

    @property
    def best(self) -> Proposal | None:
        """The first candidate that names the pick, alone and durably, or `None`.

        A positional name never wins this, however unique it is — see `Proposal.stable`.
        `None` therefore means "nothing describes this face by what it *is*", which is a
        real and common answer on a symmetric part, and the caller is expected to fall
        back to `positional` **visibly** rather than silently.

        Callers must not fall back to `offered[0]`, which by construction may name other
        faces too.
        """
        for proposal in self.offered:
            if proposal.unique and proposal.stable:
                return proposal
        return None

    @property
    def positional(self) -> Proposal | None:
        """A unique name that works today and will not survive a resize, or `None`."""
        for proposal in self.offered:
            if proposal.unique and not proposal.stable:
                return proposal
        return None

    @property
    def ambiguous(self) -> tuple[Proposal, ...]:
        """Candidates that include the pick but catch other faces with it."""
        return tuple(p for p in self.offered if p.names_the_pick and not p.unique)

    def describe(self) -> str:
        """One sentence for a user who asked what this face is called."""
        best = self.best
        if best is not None:
            return f"This face is {best.words}."

        closest = min(self.ambiguous, key=lambda p: p.matches, default=None)
        shared = (
            f" The closest description by shape is {closest.words}, which selects "
            f"{closest.matches} faces including this one."
            if closest is not None
            else ""
        )
        if self.positional is not None:
            return (
                "Nothing describes this face by what it is — only by where it sits, "
                "which stops being true if the part is resized." + shared
            )
        return (
            "Nothing in the selection vocabulary names this face, on its own or "
            "otherwise." + shared
        )


def _named_direction(normal: tuple[float, float, float]) -> str | None:
    """The axis word this normal points along, within the suggest tolerance."""
    import math

    limit = math.cos(math.radians(SUGGEST_NORMAL_TOLERANCE_DEG))
    for word, direction in DIRECTIONS.items():
        if sum(normal[i] * direction[i] for i in range(3)) >= limit:
            return word
    return None


def _feature_owning(document: Any, face: Any) -> str | None:
    """The name of the feature that contributed this face, if one recorded it.

    Read from the document's own record rather than inferred: a feature that does not
    record its faces is not guessed at, for `restrict_to_feature`'s reason. A
    `contributed_faces` of `None` means *not recorded* and is skipped; an empty list
    means the feature has no surviving face, which is also not a match.

    **A `PartDocument` is iterated, not asked for `.features()`** -- it has no such
    method, and `getattr(document, "features", lambda: [])()` returns `[]` for every
    document, so a feature name was never offered and nothing said so. Found 2026-09-16
    by probing the real runner rather than by any test going red, which is the whole
    argument for probing.

    The *last* owner wins. A face is carried forward through later operations, so a pad
    and the pocket that cut it can both list it; the later feature is the one a person
    points at.
    """
    if document is None:
        return None
    try:
        features = list(document)
    except TypeError:
        return None

    found: str | None = None
    for feature in features:
        owned = getattr(feature, "contributed_faces", None)
        if not owned:
            continue
        if any(face.IsSame(other) for other in owned):
            # **The authored name, never `catia_style_name`.** `document.feature()`
            # resolves either, so both would verify -- but `slab` is what the engineer
            # called it and `Pad.1` is what the kernel invented, and putting the invented
            # one in front of a user is the positional fragility `app/design/` exists to
            # remove, arriving through the UI instead of through a plan. Measured
            # 2026-09-16: this offered `of: "Pad.1"` for a pad the user named `slab`.
            name = getattr(feature, "name", None) or getattr(
                feature, "catia_style_name", None
            )
            if name:
                found = str(name)
    return found


def _candidates(shape: Any, face: Any, document: Any) -> list[Predicate]:
    """Plausible names for this face, most human first. None of these is checked here."""
    kind = classify.face_surface_type(face)
    planar = kind == "Plane"
    # A direction is only offered for a *planar* face. `face_normal` reads the normal at
    # the parametric centre, which on a cylinder is wherever the seam happens to put it:
    # the bore of a 60x40x20 plate reports (1, 0, 0) and is caught by `normal: "+x"`
    # alongside the +x wall (measured 2026-09-16). On a part with no +x wall that
    # candidate would verify as *unique* and be offered as the best name for a bore —
    # a name that is about the seam rather than the geometry, and that moves the day an
    # upstream edit rotates it. Curved faces are named by what they are (cylindrical, a
    # diameter) and where they are, never by which way they happen to look.
    normal = classify.face_normal(face) if planar else None
    word = _named_direction(normal) if normal is not None else None
    axis = word[1] if word else None
    side = {"+": "max", "-": "min"}[word[0]] if word else None
    diameter = classify.cylinder_diameter_mm(face)
    area = classify.face_area_mm2(face)
    low = area * (1.0 - AREA_BRACKET)
    high = area * (1.0 + AREA_BRACKET)
    feature = _feature_owning(document, face)

    out: list[Predicate] = []

    def add(**fields: Any) -> None:
        out.append(Predicate(kind="face", **fields))

    # 1. The feature's own extreme -- `boss#top`, the most human name there is.
    if feature and axis and side:
        add(of=feature, axis=axis, side=side)
    if feature and word:
        add(of=feature, normal=word)

    # 2. The part's extreme in that direction -- "the top face".
    if word and axis and side:
        add(normal=word, axis=axis, side=side)
    # 3. The direction alone.
    if word:
        add(normal=word)

    # 4. A bore or boss by its size.
    if diameter is not None:
        add(cylindrical=True, diameter_mm=diameter)
        if axis and side:
            add(cylindrical=True, diameter_mm=diameter, axis=axis, side=side)
        if feature:
            add(of=feature, cylindrical=True, diameter_mm=diameter)

    # 5. The direction, narrowed by size, for one of several parallel faces.
    if word:
        add(normal=word, larger_than_mm2=low, smaller_than_mm2=high)
    if planar:
        add(planar=True, larger_than_mm2=low, smaller_than_mm2=high)
    if diameter is None and not planar:
        add(planar=False, cylindrical=False, larger_than_mm2=low, smaller_than_mm2=high)

    # 6. The feature alone, for a feature that made exactly one face.
    if feature:
        add(of=feature)

    # 7. Last resort: where it is. Always available, says nothing, marked unstable.
    #
    # Built from the face's **vertices**, because `resolve._filter_by_box` tests vertices
    # and a box built any other way would be testing something the filter does not. On a
    # full cylinder that is only the two seam vertices, so the box comes out as a sliver
    # -- 20.999..21.001 in x for a bore centred at 21 (measured 2026-09-16). It reads
    # oddly and it is correct: every vertex of that face is inside it and no other face's
    # are. Do not "fix" it into a `Bnd_Box` of the surface, which is larger and would
    # start catching neighbours.
    from app.kernel.occt.topology import point_of, vertices

    points = [point_of(v) for v in vertices(face)]
    if points:
        low_corner = tuple(
            min(p[i] for p in points) - BOX_PADDING_MM for i in range(3)
        )
        high_corner = tuple(
            max(p[i] for p in points) + BOX_PADDING_MM for i in range(3)
        )
        from app.kernel.selection import Box

        add(inside=Box(minimum=low_corner, maximum=high_corner))  # type: ignore[arg-type]

    return out


def propose_face(shape: Any, face_index: int, *, document: Any = None) -> FaceProposal:
    """Verified ways of naming the face at `face_index`, best first.

    `face_index` is the ordinal in `topology.faces()` order, which is what
    `TriangleMesh.face_of` returns for a picked triangle — one numbering, so a pick in
    the viewer and a predicate in an operation are talking about the same face.

    `document` is optional and only widens the answer: with it, a face a feature recorded
    can be offered as `feature#selector`, which is the name a person would use.
    """
    require()
    all_faces = faces(shape)
    if not 0 <= face_index < len(all_faces):
        raise GeometryError(
            f"Face {face_index} is not on this shape, which has {len(all_faces)} faces "
            f"(0..{len(all_faces) - 1}). A face ordinal comes from the display mesh's "
            "own partition and is only valid for the shape that was tessellated."
        )
    target = all_faces[face_index]

    verified: list[Proposal] = []
    seen: set[tuple[Any, ...]] = set()
    for predicate in _candidates(shape, target, document):
        key = tuple(sorted((k, repr(v)) for k, v in predicate.__dict__.items()))
        if key in seen:
            continue
        seen.add(key)
        try:
            matched = resolve(shape, predicate, document)
        except Exception:
            # A candidate that cannot even be resolved is not a name. This is the one
            # place a broad catch is right: every candidate here is a guess, and one
            # guess failing must not deny the user the others.
            continue
        if not any(face.IsSame(target) for face in matched):
            continue
        verified.append(
            Proposal(predicate=predicate, matches=len(matched), names_the_pick=True)
        )

    return FaceProposal(face_index=face_index, offered=tuple(verified))


def propose_for_triangle(shape: Any, mesh: Any, triangle_index: int, *, document: Any = None) -> FaceProposal:
    """The whole pick, end to end: a triangle the ray hit → names for its face."""
    return propose_face(shape, mesh.face_of(triangle_index), document=document)


def face_ordinal_of(shape: Any, face: Any) -> int:
    """Where `face` sits in `faces()` order, or -1 when it is not on this shape."""
    require()
    index = index_map(shape, FACE).FindIndex(face)
    return int(index) - 1 if index else -1


__all__ = [
    "AREA_BRACKET",
    "BOX_PADDING_MM",
    "SUGGEST_NORMAL_TOLERANCE_DEG",
    "FaceProposal",
    "Proposal",
    "face_ordinal_of",
    "propose_face",
    "propose_for_triangle",
]
